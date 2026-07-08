/** Generates the MDM "Custom Command" script shown on
 * Settings → MDM Printer Resync. Pushed via Mosyle (or any MDM that can run
 * an arbitrary shell command as root), scheduled on whatever cadence the
 * admin picks in the MDM itself — this script does not schedule itself.
 *
 * Design constraints, all deliberate:
 * - Never deletes a queue, only re-runs `-m everywhere` against the queue
 *   name that's already there. That's a live re-probe + in-place PPD
 *   replacement (same mechanism scripts/sync_cups_queue.sh uses
 *   server-side) — the default-printer setting, any app's saved "last used
 *   printer", and pending jobs on OTHER queues are all unaffected, since the
 *   queue object itself never goes away.
 * - Skips a queue with a pending/active job instead of touching it.
 * - Exits immediately, untouched, if this PrintOps server isn't reachable —
 *   never partially modifies a queue it can't finish setting up.
 * - No credentials: `-m everywhere` is a plain IPP query against the
 *   printer's own already-shared queue, the same thing printing to it does.
 * - POSIX /bin/sh, not bash — macOS ships bash 3.2, and MDM script runners
 *   don't reliably honor a `#!/bin/bash` shebang, so this avoids bashisms
 *   (process substitution, arrays, [[ ]]) entirely.
 */
export function buildMdmResyncScript(host: string): string {
  const safeHost = host.trim() || "your-printops-server.example.org";

  return `#!/bin/sh
# PrintOps client-side printer queue resync.
# Generated for: ${safeHost}
#
# Regenerates each locally-configured PrintOps printer queue's PPD straight
# from the real printer (the same thing macOS's own driverless "Add
# Printer" discovery does), without ever deleting the queue — so the
# default-printer setting, any app's saved printer preference, and pending
# jobs on other queues are all left alone. Safe to run repeatedly / on a
# schedule from the MDM. Does nothing at all if this server isn't reachable
# right now, or if a matching queue currently has a pending/active job.
#
# Requires root (lpadmin needs it) — Mosyle Custom Commands run as root by
# default, so no user-context wrapper is needed here.

HOST="${safeHost}"
PORT=631

if ! command -v nc >/dev/null 2>&1 || ! nc -z -w 3 "$HOST" "$PORT" 2>/dev/null; then
  echo "PrintOps server ($HOST:$PORT) is not reachable right now -- skipping, nothing changed."
  exit 0
fi

lpstat -v 2>/dev/null | sed -n 's/^device for \\(.*\\): \\(.*\\)$/\\1|\\2/p' | while IFS='|' read -r queue uri; do
  case "$uri" in
    *"://$HOST:"*"/printers/printops-"*) ;;
    *) continue ;;
  esac

  if [ -n "$(lpstat -o "$queue" 2>/dev/null)" ]; then
    echo "Skipping $queue: has a pending/active job right now."
    continue
  fi

  echo "Resyncing $queue ..."
  if lpadmin -p "$queue" -v "$uri" -m everywhere >/dev/null 2>&1; then
    echo "Resynced $queue."
  else
    echo "Could not resync $queue (printer may be offline right now) -- left as-is."
  fi
done

echo "PrintOps queue resync complete."
`;
}
