#!/usr/bin/env bash
# Ensures /var/spool/printops-held is root:lp, mode 2770, and that the
# current user is in the `lp` group — the CUPS backend (running as root)
# spools held documents there group-writable, not world-writable, so the
# API process needs `lp` membership to read/release/purge them.
#
# Idempotent, safe to re-run: usermod -aG is additive, and chown/chmod
# just re-assert the same state if it's already correct. Called from both
# scripts/setup.sh (fresh installs) and
# infra/update-watcher/apply-update.sh (already-deployed instances) —
# skipping it in the update path would leave existing installs unable to
# release held jobs once the backend starts enforcing 0o2770 itself.
set -euo pipefail

RUN_USER="$(id -un)"

sudo usermod -aG lp "$RUN_USER"
sudo mkdir -p /var/spool/printops-held
sudo chown root:lp /var/spool/printops-held
sudo chmod 2770 /var/spool/printops-held
