"""The two scripts app/printers/crash_guard.py acts through.

Both exit codes are the API's whole verdict (queue_recovery.pause_queue,
job_control.hold_cups_job), and resume_cups_queue.sh once printed "Resumed" over
a refusal and exited 0, leaving a queue stopped for 31 hours. So the failure
paths are run for real here, against stub `sudo`, `lpstat` and friends.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
PRINTER = "9c2d5c5f-fddf-4a98-a0ea-b0bd0329efdc"

STUBS = {
    "sudo": '#!/usr/bin/env bash\nexec "$@"\n',
    # Knows the queues listed in $QUEUES; anything else "does not exist".
    "lpstat": """#!/usr/bin/env bash
for q in $QUEUES; do [ "$2" = "$q" ] && exit 0; done
exit 1
""",
    "cupsdisable": """#!/usr/bin/env bash
echo "cupsdisable $*" >> "$CALLS"
[ "$FAIL" = cupsdisable ] && { echo "cupsdisable: Forbidden" >&2; exit 1; }
exit 0
""",
    "lp": """#!/usr/bin/env bash
echo "lp $*" >> "$CALLS"
[ "$FAIL" = lp ] && { echo "lp: job not found" >&2; exit 1; }
exit 0
""",
}


def _run(tmp_path, script, *args, queues="", fail=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    result = subprocess.run(
        ["bash", str(SCRIPTS / script), *args],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "QUEUES": queues,
            "FAIL": fail,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, calls.read_text().splitlines()


BOTH = f"printops-{PRINTER} printops-release-{PRINTER}"


def test_pausing_disables_both_queues(tmp_path):
    result, calls = _run(tmp_path, "pause_cups_queue.sh", PRINTER, queues=BOTH)
    assert result.returncode == 0, result.stderr
    assert [c.split()[-1] for c in calls] == [f"printops-{PRINTER}", f"printops-release-{PRINTER}"]
    # With a reason, so anyone reading lpstat sees who did it.
    assert all(" -r " in c for c in calls)


def test_a_printer_without_a_release_queue_is_still_paused(tmp_path):
    result, calls = _run(tmp_path, "pause_cups_queue.sh", PRINTER, queues=f"printops-{PRINTER}")
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1


def test_a_refused_pause_is_reported_not_printed_over(tmp_path):
    result, _ = _run(tmp_path, "pause_cups_queue.sh", PRINTER, queues=BOTH, fail="cupsdisable")
    assert result.returncode == 1
    assert "Forbidden" in result.stderr


def test_pausing_a_printer_with_no_queues_is_a_failure(tmp_path):
    result, calls = _run(tmp_path, "pause_cups_queue.sh", PRINTER, queues="")
    assert result.returncode == 1
    assert calls == []


@pytest.mark.parametrize("action", ["hold", "resume"])
def test_hold_and_resume_go_to_lp(tmp_path, action):
    result, calls = _run(tmp_path, "hold_cups_job.sh", "12355", action)
    assert result.returncode == 0, result.stderr
    assert calls == [f"lp -i 12355 -H {action}"]


def test_a_refused_hold_is_a_failure(tmp_path):
    """A hold that did not happen leaves the printer being sent the job it
    keeps failing on."""
    result, _ = _run(tmp_path, "hold_cups_job.sh", "12355", "hold", fail="lp")
    assert result.returncode == 1
    assert "job not found" in result.stderr


@pytest.mark.parametrize("args", [("abc", "hold"), ("12355", "cancel"), ("12355; rm", "hold")])
def test_the_hold_script_refuses_what_it_was_not_built_for(tmp_path, args):
    result, calls = _run(tmp_path, "hold_cups_job.sh", *args)
    assert result.returncode == 2
    assert calls == []
