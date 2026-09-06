"""sync_cups_queue.sh must take a queue's color default from the device, not
from the queue it just built.

This is #94. A printer too old to answer the driverless attribute request gets
the generic cupsfilters PPD, which hardcodes `*ColorDevice: True` because a
generic PPD has to claim everything. The script then asked
`ipp://localhost/printers/<queue>` whether color was supported — the queue, not
the printer — read the fallback's own claim back, believed it, and set color as
the default on monochrome hardware. Three printers on this estate were doing
that, and one color copier had the same bug with the sign flipped.

The decision is worth testing directly rather than by reading it, because it is
invisible when wrong: nobody notices a queue defaulting to color until a user
picks color, waits, and collects grey paper. So the script is run for real here
against stub `lpadmin`/`ipptool`/`curl`, and what it *did* is asserted.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "sync_cups_queue.sh"

PRINTER_ID = "11111111-2222-3333-4444-555555555555"

STUBS = {
    # Dispatches rather than exec'ing blindly: the script sudo's lpadmin (which
    # must be recorded), grep against a PPD path (which must stay real so the
    # had-a-real-PPD branch behaves), and the Avahi generator (which would want
    # a live API).
    "sudo": """#!/usr/bin/env bash
case "$1" in
  lpadmin) shift; exec lpadmin "$@" ;;
  grep) shift; exec grep "$@" ;;
  python3) exit 0 ;;
  *) exit 0 ;;
esac
""",
    "lpadmin": """#!/usr/bin/env bash
echo "$@" >> "$LPADMIN_LOG"
exit 0
""",
    "cupsenable": "#!/usr/bin/env bash\nexit 0\n",
    "cupsaccept": "#!/usr/bin/env bash\nexit 0\n",
    "curl": """#!/usr/bin/env bash
cat "$PRINTER_JSON_FILE"
""",
    # -X is the color query against the local queue; everything else is
    # everywhere_probe_ok's reachability probe, whose success or failure
    # decides whether the queue lands on the generic PPD.
    "ipptool": """#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "-X" ]; then
    if [ "$QUEUE_CLAIMS_COLOR" = "1" ]; then
      printf '<key>color-supported</key>\\n<true/>\\n'
    else
      printf '<key>color-supported</key>\\n<false/>\\n'
    fi
    exit 0
  fi
done
exit "${PROBE_RC:-0}"
""",
}


def _run(tmp_path, capabilities, *, is_virtual=False, queue_claims_color=True, probe_rc=0):
    bin_dir = tmp_path / "bin"
    # exist_ok: a test that asserts both answers runs the script twice.
    bin_dir.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)

    payload = {
        "name": "Test Printer",
        "ip_address": "10.0.0.9",
        "port": 631,
        "use_tls": False,
        "ipp_path": "/ipp/print",
        "airprint_enabled": False,
        "roll_autocut": False,
        "is_virtual": is_virtual,
        "release_required": False,
        "capabilities": capabilities,
    }
    printer_json = tmp_path / "printer.json"
    printer_json.write_text(json.dumps(payload))

    env_file = tmp_path / "api.env"
    env_file.write_text("PRINTOPS_BACKEND_TOKEN=stub-token\n")

    log = tmp_path / "lpadmin.log"
    log.write_text("")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PRINTOPS_ENV_FILE": str(env_file),
        "PRINTOPS_API_BASE": "http://stub.invalid",
        "PRINTER_JSON_FILE": str(printer_json),
        "LPADMIN_LOG": str(log),
        "QUEUE_CLAIMS_COLOR": "1" if queue_claims_color else "0",
        "PROBE_RC": str(probe_rc),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), PRINTER_ID],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return log.read_text(), result.stderr


def _color_default(lpadmin_log):
    """What the queue's color default was finally set to."""
    modes = [
        line.split("print-color-mode-default=", 1)[1].split()[0]
        for line in lpadmin_log.splitlines()
        if "print-color-mode-default=" in line
    ]
    assert modes, f"no color default was ever set:\n{lpadmin_log}"
    return modes[-1]


def _color_model(lpadmin_log):
    models = [
        line.split("ColorModel=", 1)[1].split()[0]
        for line in lpadmin_log.splitlines()
        if "ColorModel=" in line
    ]
    return models[-1] if models else None


def test_probed_mono_printer_defaults_to_monochrome(tmp_path):
    """#94 itself. The queue claims color — it is on the generic PPD, which
    always does — and the device says otherwise. The device wins."""
    log, _ = _run(
        tmp_path,
        {"color_supported": False},
        queue_claims_color=True,
    )
    assert _color_default(log) == "monochrome"
    assert _color_model(log) == "Gray"


def test_probed_color_printer_defaults_to_color(tmp_path):
    """The same bug with the sign flipped, which was also live: a color copier
    whose queue did not claim color was defaulting to monochrome, so Word and
    Adobe printed grey no matter what the user picked."""
    log, _ = _run(
        tmp_path,
        {"color_supported": True},
        queue_claims_color=False,
    )
    assert _color_default(log) == "color"
    assert _color_model(log) == "RGB"


@pytest.mark.parametrize("capabilities", [None, {}, {"color_supported": None}])
def test_unprobed_printer_falls_back_to_the_queue(tmp_path, capabilities):
    """A printer PrintOps has never successfully probed must not be guessed at.
    Assuming monochrome there would strip color from a color printer, which is
    the same error #94 describes in the opposite direction — so an unknown
    capability keeps the pre-existing behaviour of believing the queue."""
    log, stderr = _run(tmp_path, capabilities, queue_claims_color=True)
    assert _color_default(log) == "color"
    assert "no probed color capability" in stderr

    log, _ = _run(tmp_path, capabilities, queue_claims_color=False)
    assert _color_default(log) == "monochrome"


def test_virtual_queue_always_keeps_color(tmp_path):
    """A Follow-Me queue has no device to probe, so it reports unknown — but its
    answer is not in doubt. Delivery happens at whichever physical printer the
    job is released to, and the virtual queue must not strip color on the way
    through. It gets color even when the queue it built claims otherwise."""
    log, _ = _run(tmp_path, None, is_virtual=True, queue_claims_color=False)
    assert _color_default(log) == "color"
    assert _color_model(log) == "RGB"


def test_color_default_is_not_decided_by_asking_the_queue_first(tmp_path):
    """A mechanical guard on the shape of the fix, not just its result.

    The regression is easy to reintroduce by adding a convenience query for the
    local queue above the capability check, so this asserts the localhost query
    appears only inside the unknown-capability branch."""
    body = SCRIPT.read_text()
    lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    localhost_queries = [ln for ln in lines if "ipp://localhost/printers/" in ln]
    assert localhost_queries, "the unknown-capability fallback should still query the queue"

    capability_read = next(
        i for i, ln in enumerate(lines) if "color_supported" in ln
    )
    first_query = next(i for i, ln in enumerate(lines) if "ipp://localhost/printers/" in ln)
    assert capability_read < first_query, (
        "the device's probed capability must be consulted before the queue is"
    )
