"""Both sync scripts must apply Printer.render_pdf_on_server to the PPD that
`-m everywhere` just generated, in both directions, and never leave a queue
unable to print.

The fault this setting answers is invisible from the server. A LaserJet 600
M601 crashed with firmware error 49.4A.04 on one PDF, and CUPS retried that job
into the printer every time it came back up; an M607 accepted other PDFs and
printed nothing. Both queues were PDF passthrough, so the device's interpreter
was the one rendering. The fix is a PPD edit, and a PPD edit is easy to get
subtly wrong — against the wrong queue, after the color default it then wipes,
or on a printer with no raster format left to fall back to. So the scripts are
run for real against stub `lpadmin`/`ipptool`/`curl`, and the PPD they install
is read back.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts"
PRINTER_ID = "11111111-2222-3333-4444-555555555555"

# (script, the queue it builds)
SYNC_SCRIPTS = [
    ("sync_cups_queue.sh", f"printops-{PRINTER_ID}"),
    ("sync_release_queue.sh", f"printops-release-{PRINTER_ID}"),
]

PASSTHROUGH = '*cupsFilter2: "application/vnd.cups-pdf application/pdf 10 -"'
PASSTHROUGH_OLD_COST = '*cupsFilter2: "application/vnd.cups-pdf application/pdf 0 -"'
URF = '*cupsFilter2: "image/urf image/urf 100 -"'
PWG = '*cupsFilter2: "application/vnd.cups-raster image/pwg-raster 0 -"'
JPEG = '*cupsFilter2: "image/jpeg image/jpeg 0 -"'

STUBS = {
    "sudo": """#!/usr/bin/env bash
case "$1" in
  lpadmin) shift; exec lpadmin "$@" ;;
  grep) shift; exec grep "$@" ;;
  *) exit 0 ;;
esac
""",
    # Records every call, and keeps a copy of any PPD installed with -P: the
    # temp file is deleted as soon as lpadmin returns.
    "lpadmin": """#!/usr/bin/env bash
echo "$@" >> "$LPADMIN_LOG"
prev=""
for a in "$@"; do
  if [ "$prev" = "-P" ]; then cp "$a" "$PPD_INSTALLED"; fi
  prev="$a"
done
exit 0
""",
    "cupsenable": "#!/usr/bin/env bash\nexit 0\n",
    "cupsaccept": "#!/usr/bin/env bash\nexit 0\n",
    "curl": '#!/usr/bin/env bash\ncat "$PRINTER_JSON_FILE"\n',
    "ipptool": "#!/usr/bin/env bash\nexit 0\n",
}


def _ppd(*filters: str, nickname: str = "LaserJet 600 M601 - IPP Everywhere") -> str:
    return "\n".join(
        ['*PPD-Adobe: "4.3"', f'*NickName: "{nickname}"', *filters, "*DefaultColorModel: RGB", ""]
    )


def _run(
    tmp_path: Path,
    script: str,
    queue: str,
    ppd: str | None,
    *,
    render: bool,
    formats=("application/pdf", "image/urf"),
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)

    ppd_dir = tmp_path / "ppd"
    ppd_dir.mkdir(exist_ok=True)
    if ppd is not None:
        (ppd_dir / f"{queue}.ppd").write_text(ppd)

    payload = {
        "name": "Test Printer",
        "ip_address": "10.0.0.9",
        "port": 631,
        "use_tls": False,
        "ipp_path": "/ipp/print",
        "airprint_enabled": False,
        "roll_autocut": False,
        "render_pdf_on_server": render,
        "is_virtual": False,
        "release_required": False,
        "capabilities": {"color_supported": False, "document_formats": list(formats)},
    }
    printer_json = tmp_path / "printer.json"
    printer_json.write_text(json.dumps(payload))
    env_file = tmp_path / "api.env"
    env_file.write_text("PRINTOPS_BACKEND_TOKEN=stub-token\n")
    log = tmp_path / "lpadmin.log"
    log.write_text("")
    installed = tmp_path / "installed.ppd"
    installed.unlink(missing_ok=True)

    result = subprocess.run(
        ["bash", str(SCRIPTS / script), PRINTER_ID],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PRINTOPS_ENV_FILE": str(env_file),
            "PRINTOPS_API_BASE": "http://stub.invalid",
            "PRINTOPS_PPD_DIR": str(ppd_dir),
            "PRINTER_JSON_FILE": str(printer_json),
            "LPADMIN_LOG": str(log),
            "PPD_INSTALLED": str(installed),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    return calls, (installed.read_text() if installed.exists() else None), result.stderr


def _installs(calls: list[str], queue: str) -> list[str]:
    return [c for c in calls if c.startswith(f"-p {queue} -P ")]


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_on_removes_passthrough_and_keeps_the_raster_format(tmp_path, script, queue):
    calls, installed, _ = _run(tmp_path, script, queue, _ppd(JPEG, PASSTHROUGH, URF), render=True)

    assert len(_installs(calls, queue)) == 1
    assert "application/pdf" not in installed
    assert URF in installed
    assert JPEG in installed
    # Everything else in the PPD survives the edit.
    assert '*NickName: "LaserJet 600 M601 - IPP Everywhere"' in installed


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_on_is_installed_before_the_color_default_it_would_otherwise_wipe(tmp_path, script, queue):
    calls, _, _ = _run(tmp_path, script, queue, _ppd(PASSTHROUGH, URF), render=True)

    install = next(i for i, c in enumerate(calls) if " -P " in c)
    color_model = next(i for i, c in enumerate(calls) if "ColorModel=" in c)
    assert install < color_model


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_on_matches_passthrough_at_any_cost(tmp_path, script, queue):
    _, installed, _ = _run(tmp_path, script, queue, _ppd(PASSTHROUGH_OLD_COST, PWG), render=True)

    assert "application/pdf" not in installed
    assert PWG in installed


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_on_never_leaves_a_queue_with_nothing_to_print_in(tmp_path, script, queue):
    calls, installed, stderr = _run(
        tmp_path,
        script,
        queue,
        _ppd(JPEG, PASSTHROUGH),
        render=True,
        formats=("application/pdf", "image/jpeg"),
    )

    assert _installs(calls, queue) == []
    assert installed is None
    assert "no raster format" in stderr


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_on_a_queue_already_rendering_is_left_alone(tmp_path, script, queue):
    calls, _, _ = _run(tmp_path, script, queue, _ppd(URF), render=True)

    assert _installs(calls, queue) == []


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_off_leaves_an_ordinary_queue_byte_identical(tmp_path, script, queue):
    """The path every other printer on a server takes, every sync."""
    calls, installed, _ = _run(tmp_path, script, queue, _ppd(JPEG, PASSTHROUGH, URF), render=False)

    assert _installs(calls, queue) == []
    assert installed is None


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_off_restores_passthrough_an_earlier_run_removed(tmp_path, script, queue):
    """-m everywhere failed this sync, so the edited PPD from when the setting
    was on is still installed. Turning the setting off must still take effect."""
    calls, installed, _ = _run(tmp_path, script, queue, _ppd(JPEG, URF), render=False)

    assert len(_installs(calls, queue)) == 1
    assert PASSTHROUGH in installed
    assert URF in installed
    filters = re.findall(r"^\*cupsFilter2: .*$", installed, flags=re.MULTILINE)
    assert len(filters) == 3


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_off_does_not_give_pdf_to_a_device_that_never_listed_it(tmp_path, script, queue):
    calls, _, _ = _run(
        tmp_path, script, queue, _ppd(URF), render=False, formats=("image/urf", "image/jpeg")
    )

    assert _installs(calls, queue) == []


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_off_leaves_the_generic_fallback_ppd_alone(tmp_path, script, queue):
    generic = _ppd(PWG, nickname="Generic IPP Everywhere Printer")
    calls, _, _ = _run(tmp_path, script, queue, generic, render=False)

    assert _installs(calls, queue) == []


@pytest.mark.parametrize("script,queue", SYNC_SCRIPTS)
def test_a_queue_with_no_ppd_yet_is_not_an_error(tmp_path, script, queue):
    calls, _, stderr = _run(tmp_path, script, queue, None, render=True)

    assert _installs(calls, queue) == []
    assert "no document formats to change" in stderr


def _command_lines(body: str) -> list[str]:
    return [line for line in body.splitlines() if not line.lstrip().startswith("#")]


def _first_line_starting(lines: list[str], prefix: str) -> int:
    return next(i for i, line in enumerate(lines) if line.lstrip().startswith(prefix))


@pytest.mark.parametrize("script", [s for s, _ in SYNC_SCRIPTS])
def test_every_sync_script_applies_it_before_the_ppd_defaults(script):
    """Held and Follow-Me jobs are delivered through the release queue. A rule
    applied only to the client-facing queue sends exactly those jobs back
    through the printer's PDF interpreter — the same half-fix that
    lib/everywhere_probe.sh and lib/color_default.sh each shipped once."""
    lines = _command_lines((SCRIPTS / script).read_text())

    assert "lib/pdf_rendering.sh" in "\n".join(lines)
    call = _first_line_starting(lines, "apply_pdf_rendering ")
    assert call < _first_line_starting(lines, "apply_color_default ")
    assert call < _first_line_starting(lines, "TRIM_MAP=")
