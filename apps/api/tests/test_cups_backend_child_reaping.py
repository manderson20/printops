"""infra/cups/backends/printops spawns CUPS's real `ipp` backend as a child.
When cupsd cancels/holds/restarts a job it SIGTERMs *this* script, and Python's
default handling used to exit immediately without touching that child — leaving
it reparented to init with its TCP connection to the printer still open, still
retrying.

That leak took a printer out for an afternoon (LCACTC Kyocera, 2026-08-20:
three orphans, each retrying at several hundred connections/second, saturating
the device's IPP service while SNMP still reported "Ready."). These tests spawn
real child processes — the bug is entirely about process lifecycle, so mocking
subprocess would test nothing.

The handler is invoked directly rather than by signalling this process: an
actual SIGTERM to the pytest runner is caught by the runner's own machinery
before the assertions can run. Calling it is the same code path minus the
argument about who owns the process's signal disposition.
"""

import importlib.util
import os
import signal
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "infra" / "cups" / "backends" / "printops"


@pytest.fixture
def backend_module():
    loader = SourceFileLoader("printops_cups_backend_reaping", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def handler(backend_module):
    """Installs the forwarding handler, hands back the callable, and restores
    whatever disposition pytest had so a failing test can't leave the runner
    with our SIGTERM handler still armed."""
    previous = signal.getsignal(signal.SIGTERM)
    backend_module._install_child_signal_forwarding()
    installed = signal.getsignal(signal.SIGTERM)
    try:
        yield installed
    finally:
        signal.signal(signal.SIGTERM, previous)
        backend_module._CHILD_PROC = None


def _alive(pid: int) -> bool:
    """True while `pid` exists and is not a zombie. Deliberately not
    os.kill(pid, 0) alone: a child we spawned but have not reaped stays a
    zombie, which os.kill still reports as alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_reaps_a_child_that_exits_politely(backend_module, handler):
    """The ordinary case: cupsd SIGTERMs the backend mid-job and the real `ipp`
    backend honours its own SIGTERM. The child must not outlive us."""
    child = backend_module._run_real_backend(
        [sys.executable, "-c", "import time; time.sleep(120)"], dict(os.environ)
    )

    with pytest.raises(SystemExit) as exc:
        handler(signal.SIGTERM, None)

    assert exc.value.code == 128 + signal.SIGTERM
    assert _wait_gone(child.pid), "child survived SIGTERM — this is the orphan leak"


def test_kills_a_child_that_ignores_sigterm(backend_module, handler, monkeypatch):
    """The case that actually leaked. CUPS's `ipp` backend ignores SIGTERM while
    blocked in its connect/retry loop against a refusing device — exactly the
    situation where an orphan does the most damage. SIGTERM alone is therefore
    not enough; the grace period must expire into a SIGKILL."""
    monkeypatch.setattr(backend_module, "CHILD_TERM_GRACE_SECONDS", 1)

    child = backend_module._run_real_backend(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)",
        ],
        dict(os.environ),
    )

    with pytest.raises(SystemExit) as exc:
        handler(signal.SIGTERM, None)

    assert exc.value.code == 128 + signal.SIGTERM
    assert _wait_gone(child.pid), "SIGTERM-ignoring child survived — still leaking"


def test_no_child_yet_still_exits_cleanly(backend_module, handler):
    """A signal arriving before any child exists (the API-bookkeeping phase)
    must not blow up inside the handler."""
    backend_module._CHILD_PROC = None

    with pytest.raises(SystemExit) as exc:
        handler(signal.SIGTERM, None)

    assert exc.value.code == 128 + signal.SIGTERM


def test_already_finished_child_is_not_waited_on_again(backend_module, handler):
    """A child that exited on its own leaves _CHILD_PROC set. The handler must
    not block on it — CHILD_TERM_GRACE_SECONDS of dead time per signal would
    stall cupsd's job teardown for no reason."""
    child = backend_module._run_real_backend([sys.executable, "-c", "pass"], dict(os.environ))
    child.wait()

    started = time.monotonic()
    with pytest.raises(SystemExit):
        handler(signal.SIGTERM, None)
    assert time.monotonic() - started < 1.0


def test_child_is_not_orphaned_in_a_real_process_tree():
    """End-to-end guard on the actual production signature: run the backend's
    spawn+signal path in a *separate* interpreter, SIGTERM that interpreter, and
    assert the grandchild does not survive reparented to init. This is the shape
    the leak took in production and it is invisible to any in-process test."""
    driver = f"""
import importlib.util, os, sys, time
from importlib.machinery import SourceFileLoader
loader = SourceFileLoader("b", {str(SCRIPT_PATH)!r})
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
m.CHILD_TERM_GRACE_SECONDS = 1
child = m._run_real_backend(
    [sys.executable, "-c",
     "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"],
    dict(os.environ),
)
m._install_child_signal_forwarding()
print(child.pid, flush=True)
time.sleep(120)
"""
    proc = subprocess.Popen([sys.executable, "-c", driver], stdout=subprocess.PIPE, text=True)
    try:
        grandchild_pid = int(proc.stdout.readline().strip())
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
        assert _wait_gone(grandchild_pid), (
            f"grandchild {grandchild_pid} outlived its parent — "
            "this is precisely the production orphan"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
