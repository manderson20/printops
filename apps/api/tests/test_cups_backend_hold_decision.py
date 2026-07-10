"""infra/cups/backends/printops is a standalone stdlib-only script (no
package structure, runs under CUPS as root, outside the app's own venv) —
it has never had test coverage before. This is a narrow smoke test of just
the one line this session's quota feature changed: the hold-vs-forward
branch now keys off the created job's `hold_reason` field (server-decided)
instead of a separate `printer.release_required` lookup. Every external
call (HTTP, subprocess, spooling) is mocked — this never touches a real
printer, CUPS queue, or the network."""

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "infra" / "cups" / "backends" / "printops"


@pytest.fixture
def backend_module():
    # No .py extension (installed straight to /usr/lib/cups/backend/printops),
    # so importlib can't infer a loader from the filename — pass one explicitly.
    loader = SourceFileLoader("printops_cups_backend", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def argv_and_env(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["printops", "42", "matt", "Report.pdf", "1", "job-uuid=urn:uuid:x", "/tmp/doc.pdf"],
    )
    monkeypatch.setenv("DEVICE_URI", "printops://11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("PRINTER", "printops-test")


@pytest.fixture
def argv_and_env_no_filename(monkeypatch):
    # No trailing filename arg — CUPS is handing the document over via
    # stdin instead of a temp file, the path that used to leave
    # file_size_bytes permanently null (see infra/cups/backends/printops).
    monkeypatch.setattr(
        sys,
        "argv",
        ["printops", "42", "matt", "Report.pdf", "1", "job-uuid=urn:uuid:x"],
    )
    monkeypatch.setenv("DEVICE_URI", "printops://11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("PRINTER", "printops-test")


def test_hold_reason_set_spools_and_never_calls_real_backend(
    backend_module, argv_and_env, monkeypatch
):
    monkeypatch.setattr(backend_module, "load_backend_token", lambda: "test-token")
    monkeypatch.setattr(backend_module, "os", backend_module.os)
    monkeypatch.setattr(backend_module.os.path, "getsize", lambda _path: 123)

    responses = iter(
        [
            {"use_tls": False, "ip_address": "10.0.0.5", "port": 631},  # connection lookup
            {"id": "job-record-1", "hold_reason": "quota"},  # create_job
            {"status": "held"},  # PATCH held
        ]
    )
    api_request_mock = MagicMock(side_effect=lambda *a, **k: next(responses))
    spool_mock = MagicMock(return_value="/var/spool/printops-held/job-record-1")
    subprocess_mock = MagicMock()

    with (
        patch.object(backend_module, "api_request", api_request_mock),
        patch.object(backend_module, "spool_held_file", spool_mock),
        patch.object(backend_module.subprocess, "run", subprocess_mock),
    ):
        exit_code = backend_module.main()

    assert exit_code == 0
    spool_mock.assert_called_once()
    subprocess_mock.assert_not_called()
    # Third call is the PATCH marking the job held.
    patch_call = api_request_mock.call_args_list[2]
    assert patch_call.args[1] == "PATCH"
    assert patch_call.args[3]["status"] == "held"


def test_no_hold_reason_proceeds_to_real_backend(backend_module, argv_and_env, monkeypatch):
    monkeypatch.setattr(backend_module, "load_backend_token", lambda: "test-token")
    monkeypatch.setattr(backend_module.os.path, "getsize", lambda _path: 123)

    responses = iter(
        [
            {"use_tls": False, "ip_address": "10.0.0.5", "port": 631, "ipp_path": None},
            {"id": "job-record-2", "hold_reason": None},  # create_job — not held
            {"status": "forwarded"},  # final PATCH after real backend "succeeds"
        ]
    )
    api_request_mock = MagicMock(side_effect=lambda *a, **k: next(responses))
    spool_mock = MagicMock()
    completed = MagicMock(returncode=0)
    subprocess_mock = MagicMock(return_value=completed)

    with (
        patch.object(backend_module, "api_request", api_request_mock),
        patch.object(backend_module, "spool_held_file", spool_mock),
        patch.object(backend_module.subprocess, "run", subprocess_mock),
        patch.object(
            backend_module,
            "get_job_completion_attributes",
            return_value={
                "page_count": None,
                "color_mode": None,
                "duplex": None,
                "paper_size": None,
            },
        ),
    ):
        exit_code = backend_module.main()

    assert exit_code == 0
    spool_mock.assert_not_called()
    subprocess_mock.assert_called_once()
    real_argv = subprocess_mock.call_args.args[0]
    assert real_argv[0] == backend_module.REAL_IPP_BACKEND


def test_no_filename_forwarded_counts_bytes_via_stdin_proxy(
    backend_module, argv_and_env_no_filename, monkeypatch
):
    """No filename arg means CUPS piped the document over stdin — this used
    to leave file_size_bytes null forever (see infra/cups/backends/printops
    module docstring history). Now the script proxies stdin through to the
    real backend itself, counting bytes as they stream past."""
    monkeypatch.setattr(backend_module, "load_backend_token", lambda: "test-token")

    stdin_mock = MagicMock()
    stdin_mock.buffer.read.side_effect = [b"a" * 262144, b"bbbb", b""]
    monkeypatch.setattr(backend_module.sys, "stdin", stdin_mock)

    responses = iter(
        [
            {"use_tls": False, "ip_address": "10.0.0.5", "port": 631, "ipp_path": None},
            {"id": "job-record-3", "hold_reason": None},
            {"status": "forwarded"},
        ]
    )
    api_request_mock = MagicMock(side_effect=lambda *a, **k: next(responses))
    spool_mock = MagicMock()
    proc_mock = MagicMock()
    proc_mock.wait.return_value = 0
    popen_mock = MagicMock(return_value=proc_mock)

    with (
        patch.object(backend_module, "api_request", api_request_mock),
        patch.object(backend_module, "spool_held_file", spool_mock),
        patch.object(backend_module.subprocess, "Popen", popen_mock),
        patch.object(
            backend_module,
            "get_job_completion_attributes",
            return_value={
                "page_count": None,
                "color_mode": None,
                "duplex": None,
                "paper_size": None,
            },
        ),
    ):
        exit_code = backend_module.main()

    assert exit_code == 0
    spool_mock.assert_not_called()
    popen_mock.assert_called_once()
    real_argv = popen_mock.call_args.args[0]
    assert real_argv[0] == backend_module.REAL_IPP_BACKEND
    assert real_argv[-1] != "/tmp/doc.pdf"  # no filename appended in this path
    patch_call = api_request_mock.call_args_list[2]
    assert patch_call.args[1] == "PATCH"
    assert patch_call.args[3]["file_size_bytes"] == 262144 + 4


def test_no_filename_held_computes_size_from_spooled_file(
    backend_module, argv_and_env_no_filename, monkeypatch
):
    monkeypatch.setattr(backend_module, "load_backend_token", lambda: "test-token")
    monkeypatch.setattr(backend_module.os.path, "getsize", lambda _path: 4096)

    responses = iter(
        [
            {"use_tls": False, "ip_address": "10.0.0.5", "port": 631},
            {"id": "job-record-4", "hold_reason": "quota"},
            {"status": "held"},
        ]
    )
    api_request_mock = MagicMock(side_effect=lambda *a, **k: next(responses))
    spool_mock = MagicMock(return_value="/var/spool/printops-held/job-record-4")
    popen_mock = MagicMock()

    with (
        patch.object(backend_module, "api_request", api_request_mock),
        patch.object(backend_module, "spool_held_file", spool_mock),
        patch.object(backend_module.subprocess, "Popen", popen_mock),
    ):
        exit_code = backend_module.main()

    assert exit_code == 0
    spool_mock.assert_called_once()
    popen_mock.assert_not_called()
    patch_call = api_request_mock.call_args_list[2]
    assert patch_call.args[3]["file_size_bytes"] == 4096
