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
 * - Fully self-discovering: identifies PrintOps-managed queues purely by
 *   their device-uri path (`/printers/printops-<uuid>`) — a signature no
 *   other queue would have — and reads each one's own host:port straight
 *   out of that same URI. No server hostname is hardcoded anywhere in this
 *   script; it's derived per-queue from what's already configured on the
 *   Mac. (An earlier version required a hostname typed in on the settings
 *   page to match exactly against each queue's device-uri — that broke
 *   silently whenever the MDM's printer profile pointed at the server by IP
 *   while the page had a DNS name, or vice versa, matching nothing at all
 *   without any error.)
 * - Skips a queue with a pending/active job instead of touching it.
 * - Skips a queue (doesn't touch it) if its own server isn't reachable
 *   right now — never partially modifies a queue it can't finish setting
 *   up. Checked per-queue, not once globally, since nothing stops a Mac
 *   from having queues from more than one PrintOps install.
 * - No credentials: `-m everywhere` is a plain IPP query against the
 *   printer's own already-shared queue, the same thing printing to it does.
 * - POSIX /bin/sh, not bash — macOS ships bash 3.2, and MDM script runners
 *   don't reliably honor a `#!/bin/bash` shebang, so this avoids bashisms
 *   (process substitution, arrays, [[ ]]) entirely.
 * - `-m everywhere` is bounded by a manual background-job watchdog, not
 *   `timeout(1)` — that's a GNU coreutils tool, not part of macOS/BSD, so
 *   it's simply not there to call. Same reason this exists server-side in
 *   sync_cups_queue.sh (confirmed live against a real device that hangs on
 *   this specific probe) — one unresponsive printer shouldn't be able to
 *   hang this whole script, which runs unattended fleet-wide via the MDM.
 */
export function buildMdmResyncScript(): string {
  return `#!/bin/sh
# PrintOps client-side printer queue resync.
#
# Regenerates each locally-configured PrintOps printer queue's PPD straight
# from the real printer (the same thing macOS's own driverless "Add
# Printer" discovery does), without ever deleting the queue — so the
# default-printer setting, any app's saved printer preference, and pending
# jobs on other queues are all left alone. Safe to run repeatedly / on a
# schedule from the MDM. Self-discovering: finds PrintOps-managed queues by
# their device-uri (no hostname needs configuring here, so this same script
# works unmodified on any PrintOps install). Skips a queue outright rather
# than touching it if its own server isn't reachable right now, or if it
# currently has a pending/active job.
#
# Requires root (lpadmin needs it) — Mosyle Custom Commands run as root by
# default, so no user-context wrapper is needed here.

# Portable bound on a slow/hung command — macOS doesn't ship GNU coreutils'
# timeout(1), so this uses a background watchdog instead.
run_with_timeout() {
  secs=$1; shift
  "$@" &
  cmd_pid=$!
  ( sleep "$secs"; kill -TERM "$cmd_pid" 2>/dev/null ) &
  watchdog_pid=$!
  wait "$cmd_pid" 2>/dev/null
  status=$?
  kill "$watchdog_pid" 2>/dev/null
  wait "$watchdog_pid" 2>/dev/null
  return $status
}

lpstat -v 2>/dev/null | sed -n 's/^device for \\(.*\\): \\(.*\\)$/\\1|\\2/p' | while IFS='|' read -r queue uri; do
  case "$uri" in
    *"/printers/printops-"*) ;;
    *) continue ;;
  esac

  hostport=$(echo "$uri" | sed -n 's#^[a-zA-Z][a-zA-Z0-9+.-]*://\\([^/]*\\)/.*#\\1#p')
  host=\${hostport%%:*}
  port=\${hostport#*:}
  case "$port" in
    ''|*[!0-9]*) port=631 ;;
  esac

  if [ -z "$host" ] || ! command -v nc >/dev/null 2>&1 || ! nc -z -w 3 "$host" "$port" 2>/dev/null; then
    echo "Skipping $queue: $host:$port not reachable right now."
    continue
  fi

  if [ -n "$(lpstat -o "$queue" 2>/dev/null)" ]; then
    echo "Skipping $queue: has a pending/active job right now."
    continue
  fi

  echo "Resyncing $queue ..."
  if run_with_timeout 30 lpadmin -p "$queue" -v "$uri" -m everywhere >/dev/null 2>&1; then
    echo "Resynced $queue."
  else
    echo "Could not resync $queue (printer may be offline, or timed out) -- left as-is."
  fi
done

echo "PrintOps queue resync complete."
`;
}
