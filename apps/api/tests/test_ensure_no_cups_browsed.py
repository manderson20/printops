"""scripts/ensure_no_cups_browsed.sh must turn cups-browsed off where it is on,
and touch nothing where it is absent, already off, or deliberately kept.

On a PrintOps server cups-browsed copies PrintOps's own advertised queues, and
after every CUPS restart it held nearly all of cupsd's client slots for 10–15
minutes while it rebuilt them (2026-09-14). The script runs on every update, so
the cases where it must do nothing matter as much as the one where it acts.
Run for real against stub `systemctl` and `sudo`.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ensure_no_cups_browsed.sh"

SYSTEMCTL = """#!/usr/bin/env bash
case "$1" in
  list-unit-files)
    [ "$UNIT_INSTALLED" = 1 ] && echo "cups-browsed.service enabled enabled"
    exit 0 ;;
  is-enabled) echo "$UNIT_ENABLED"; [ "$UNIT_ENABLED" = enabled ] ;;
  is-active) echo "$UNIT_ACTIVE"; [ "$UNIT_ACTIVE" = active ] ;;
  *) echo "systemctl $*" >> "$CALLS"; exit 0 ;;
esac
"""
SUDO = '#!/usr/bin/env bash\nexec "$@"\n'


def _run(tmp_path, *, installed=True, enabled="enabled", active="active", keep=None, env_file=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in {"systemctl": SYSTEMCTL, "sudo": SUDO}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "UNIT_INSTALLED": "1" if installed else "0",
        "UNIT_ENABLED": enabled,
        "UNIT_ACTIVE": active,
    }
    env.pop("PRINTOPS_KEEP_CUPS_BROWSED", None)
    # Always a file of the test's own: the default path is the repository's real
    # apps/api/.env, which must never decide a test's outcome.
    env_path = tmp_path / "api.env"
    env_path.write_text(env_file)
    env["PRINTOPS_ENV_FILE"] = str(env_path)
    if keep is not None:
        env["PRINTOPS_KEEP_CUPS_BROWSED"] = keep
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return calls.read_text().splitlines(), result.stdout


def test_an_enabled_running_cups_browsed_is_turned_off(tmp_path):
    calls, out = _run(tmp_path)
    assert calls == ["systemctl disable --now cups-browsed.service"]
    assert "Turned off" in out


@pytest.mark.parametrize(
    "enabled,active",
    [("enabled", "inactive"), ("disabled", "active")],
    ids=["enabled-but-stopped", "disabled-but-running"],
)
def test_half_on_is_still_turned_off(tmp_path, enabled, active):
    """Enabled-but-stopped starts again at the next boot; disabled-but-running
    is holding cupsd's slots right now. Either half is enough to act on."""
    calls, _ = _run(tmp_path, enabled=enabled, active=active)
    assert calls == ["systemctl disable --now cups-browsed.service"]


def test_already_off_is_left_alone(tmp_path):
    calls, out = _run(tmp_path, enabled="disabled", active="inactive")
    assert calls == []
    assert "already off" in out


def test_a_masked_unit_is_left_alone(tmp_path):
    calls, _ = _run(tmp_path, enabled="masked", active="inactive")
    assert calls == []


def test_a_server_without_cups_browsed_is_left_alone(tmp_path):
    calls, out = _run(tmp_path, installed=False)
    assert calls == []
    assert "not installed" in out


def test_the_opt_out_is_honoured(tmp_path):
    calls, out = _run(tmp_path, keep="1")
    assert calls == []
    assert "PRINTOPS_KEEP_CUPS_BROWSED=1" in out


def test_the_opt_out_is_read_from_the_api_env_file(tmp_path):
    """Scheduled updates run under a systemd unit whose environment is only
    PATH. The opt-out has to live somewhere that unit can read."""
    calls, out = _run(
        tmp_path, env_file="PRINTOPS_BACKEND_TOKEN=x\nPRINTOPS_KEEP_CUPS_BROWSED='1'\n"
    )
    assert calls == []
    assert "PRINTOPS_KEEP_CUPS_BROWSED=1" in out


def test_an_env_file_without_the_opt_out_still_turns_it_off(tmp_path):
    calls, _ = _run(tmp_path, env_file="PRINTOPS_BACKEND_TOKEN=x\n")
    assert calls == ["systemctl disable --now cups-browsed.service"]


def test_setup_and_the_updater_both_run_it():
    """setup.sh only runs on a fresh install. The servers that already have
    cups-browsed copying their queues are the ones that only ever run the
    updater, so a fix wired into setup alone would reach none of them."""
    repo = SCRIPT.parents[1]
    assert "scripts/ensure_no_cups_browsed.sh" in (repo / "scripts" / "setup.sh").read_text()
    updater = (repo / "infra" / "update-watcher" / "apply-update.sh").read_text()
    assert "scripts/ensure_no_cups_browsed.sh" in updater
