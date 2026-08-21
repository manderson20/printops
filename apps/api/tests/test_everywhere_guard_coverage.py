"""Every script that runs `lpadmin -m everywhere` must guard it first.

`timeout 30 lpadmin -m everywhere` bounds the client, not the work: cupsd
builds the driverless PPD on its own thread, and against a device that never
satisfies the request that thread retries forever, with no backoff, until cupsd
is restarted.

This test exists because the first fix guarded only sync_cups_queue.sh.
queue_sync.py calls sync_release_queue.sh too, so the leak survived untouched
and the connection storm returned within two minutes of deploying the fix
(2026-08-20). The guard is easy to add to a new script and even easier to
forget, so it is checked mechanically rather than by memory.

Scope is scripts/ deliberately. apps/web/src/lib/mdmResyncScript.ts also emits
an `-m everywhere` call, and is deliberately not covered: it runs on client
Macs and aims at this server's own shared queue
(ipp://<printops-server>/printers/printops-<uuid>), not at a physical printer.
cupsd answers that request reliably, and that script already carries its own
reachability check, pending-job skip and watchdog. Different endpoint,
different failure mode — not an oversight.
"""

import re
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
SHARED_GUARD = SCRIPTS_DIR / "lib" / "everywhere_probe.sh"


def _command_lines(body: str) -> list[str]:
    """Executable lines only. The scripts discuss `-m everywhere` at length in
    comments — matching those would flag prose as an unguarded call."""
    return [line for line in body.splitlines() if not line.lstrip().startswith("#")]


def _everywhere_calls(body: str) -> list[str]:
    return [line for line in _command_lines(body) if re.search(r"lpadmin\b.*-m everywhere", line)]


def _scripts_invoking_everywhere() -> list[Path]:
    found = []
    for path in sorted(SCRIPTS_DIR.rglob("*.sh")):
        if path == SHARED_GUARD:
            continue
        if _everywhere_calls(path.read_text()):
            found.append(path)
    return found


def test_the_shared_guard_exists():
    assert SHARED_GUARD.is_file()
    assert "everywhere_probe_ok()" in SHARED_GUARD.read_text()


def test_at_least_one_script_is_covered():
    """Guards the guard: if the scan silently matched nothing, every assertion
    below would pass vacuously and this file would be worthless."""
    assert _scripts_invoking_everywhere(), (
        "No script matched `lpadmin -m everywhere` — the detection pattern has "
        "probably drifted, so this whole test file is no longer checking anything."
    )


@pytest.mark.parametrize("script", _scripts_invoking_everywhere(), ids=lambda p: p.name)
def test_every_everywhere_call_is_guarded(script: Path):
    body = script.read_text()

    assert "lib/everywhere_probe.sh" in body, (
        f"{script.name} runs `lpadmin -m everywhere` without sourcing "
        "scripts/lib/everywhere_probe.sh. An unguarded call leaks a cupsd "
        "thread that retries the device forever and can only be cleared by "
        "restarting cupsd."
    )
    assert "everywhere_probe_ok" in body, (
        f"{script.name} sources the guard but never calls everywhere_probe_ok."
    )

    # Every call must be gated on the probe, not merely preceded by it.
    for line in _everywhere_calls(body):
        assert "EVERYWHERE_SKIPPED" in line, (
            f"{script.name} calls `-m everywhere` on a line that does not consult "
            f"the probe result:\n    {line.strip()}"
        )
