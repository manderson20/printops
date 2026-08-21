#!/usr/bin/env bash
# Installs infra/cups/backends/printops to /usr/lib/cups/backend/printops,
# which is where cupsd actually looks for it.
#
# This exists because nothing used to do it. The backend was copied into place
# by hand at some point and then drifted: on 2026-08-20 the deployed copy was
# six weeks behind the repo and still carried the world-writable spool
# permissions (0o777) that a security scan had already forced a fix for. The
# repo had been correct for weeks; production had never been told.
#
# A backend that silently runs an old version is a bad thing to have in the
# print path — the code in the tree is what gets reviewed and tested, and it
# should be the code that runs. So this is idempotent and called from both
# scripts/setup.sh (fresh installs) and infra/update-watcher/apply-update.sh
# (already-deployed instances), the same arrangement as
# scripts/ensure_held_spool_group.sh.
#
# CUPS requires backends to be owned by root and *not* group/world writable —
# it silently refuses to run one that is (mode 0700 for a root-run backend).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_DIR/infra/cups/backends/printops"
DEST="/usr/lib/cups/backend/printops"

if [ ! -f "$SRC" ]; then
    echo "ERROR: $SRC not found — cannot install the PrintOps CUPS backend." >&2
    exit 1
fi

# Syntax-check before overwriting the live print path. A backend that fails to
# parse doesn't degrade gracefully: every job on every printops:// queue fails
# immediately, district-wide.
if ! python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$SRC"; then
    echo "ERROR: $SRC is not valid Python — refusing to install it." >&2
    exit 1
fi

if sudo cmp -s "$SRC" "$DEST" 2>/dev/null; then
    echo "PrintOps CUPS backend already up to date at $DEST"
    exit 0
fi

sudo install -o root -g root -m 0700 "$SRC" "$DEST"
echo "Installed PrintOps CUPS backend to $DEST"

# cupsd execs the backend fresh per job, so there is nothing to reload — jobs
# submitted from here on already use the new copy. Any backend process still
# running belongs to a job in flight and is left alone.
