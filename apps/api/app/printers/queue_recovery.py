"""Starts a printer's CUPS queue again after cupsd stopped it.

cupsd stops a queue on its own whenever a backend exits with
CUPS_BACKEND_STOP (4), and it does that *regardless* of the queue's
ErrorPolicy — so the `abort-job` policy scripts/sync_cups_queue.sh sets is no
defence. Nothing in PrintOps ever started a stopped queue again, and cupsd
never does it by itself, so the queue stayed dead until a person ran
`cupsenable` by hand.

On 2026-08-21 the ES Veronica Copier (bizhub 950i) was taken away for a
Service Call 0206. The `ipp` backend hit a broken pipe part-way through
Send-Document, retried the same job 51 times, then exited 4; cupsd stopped
the queue at 16:26. The copier came back serviced and idle — and the queue
stayed stopped for the next 31 hours with 19 teachers' jobs behind it. The
queue was still *accepting* jobs the whole time, which is why the backlog
kept growing and why nothing looked obviously broken from a client.

Two things made this invisible rather than merely broken:

- PrintOps had no idea its own CUPS queues have a state. Every health
  signal it collects describes the *device* over IPP, and the device was
  reporting itself idle and accepting jobs, which it genuinely was.
- The one operation that would have fixed it — a queue resync, which ends
  in `cupsenable` — is deliberately skipped when the queue has jobs on it
  (app/printers/status.py:run_automatic_queue_sync, for good reasons of its
  own). A stopped queue always has jobs on it. The repair was gated off
  exactly when it was needed.

A stopped queue is always an accident here. PrintOps has no "pause this
printer" feature to trample: retiring a printer archives it, and that tears
the queue down entirely (app/routers/printers.py:archive_printer). So there
is no case where a present-but-stopped queue is something an admin asked
for.

Like app/printers/queue_stall.py, the backoff state is in-process and resets
on restart — see that module for why remembering across restarts would be a
claim this can't honestly make.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts"
RESUME_SCRIPT = SCRIPTS_DIR / "resume_cups_queue.sh"

LPSTAT_TIMEOUT_SECONDS = 5
RESUME_TIMEOUT_SECONDS = 15

# How long after resuming a queue before this printer may have another
# automatic attempt. Short, because the operation is local, cheap and
# idempotent — unlike a queue resync there is no `lpadmin -m everywhere`
# probing the device behind it, and no cupsd slot held for 30 seconds.
RESUME_COOLDOWN = timedelta(minutes=5)

# Each consecutive failed recovery doubles the wait. A queue that cupsd
# stops again within minutes of every resume is telling us the device is
# rejecting work for a reason IPP isn't reporting, and hammering it just
# feeds one more job into the shredder per cycle.
MAX_RESUME_BACKOFF = timedelta(hours=4)
_MAX_BACKOFF_DOUBLINGS = 16

# How long the device has to read online again, continuously, before the
# backoff above is dropped and this printer may be resumed immediately.
#
# The backoff is otherwise a clock and nothing else, and a clock cannot tell
# the two cases apart that it sits between. A device that claims to be online
# while rejecting every job deserves the full four hours. A copier that was
# genuinely away — the case this whole module was written for — comes back
# fixed, and making it wait out a backoff earned while it was unplugged is the
# original bug wearing a different hat: the queue stays off, the printer reads
# red, and the jobs behind it keep piling up for hours after the fault ended.
# Only the device coming *back* distinguishes them, so that is what resets it.
#
# Continuously, rather than on the first healthy reading, because a printer
# flapping between online and offline would otherwise reset the backoff on
# every flap and get a resume per cycle — exactly what the doubling exists to
# prevent. Five minutes bounds a flapping device to one resume per five
# minutes while costing a genuinely-serviced printer five minutes it spends
# holding jobs it was going to hold anyway.
HEALTHY_RESET_PERIOD = timedelta(minutes=5)

# Appended to Printer.status_reasons so the UI and anyone reading the row
# can tell "the device is unhappy" from "the device is fine but its queue on
# this server is switched off". Namespaced like printops-queue-stalled
# because it is our observation, not something the printer reported.
QUEUE_PAUSED_REASON = "printops-queue-paused"


class QueueResumeError(Exception):
    pass


@dataclass(frozen=True)
class LocalQueueState:
    """The state of *our* CUPS queue for a printer — not the device's own
    IPP state, which is what everything else in app/printers/ reports."""

    stopped: bool
    # cupsd's own explanation, e.g. "Unable to add document to print job."
    # None when the queue is running or gave no reason.
    message: str | None = None


@dataclass
class _Attempt:
    last_attempt: datetime
    failures: int
    # True between resuming a queue and next looking at it. Without it the
    # failure count would climb once per 60s poll for as long as a queue
    # stayed stopped, rather than once per resume that didn't hold — which
    # would run the backoff to its 4-hour cap inside twenty minutes and make
    # the doubling meaningless.
    awaiting_verdict: bool = False

    # True once this printer's *device* has been seen not-online while its
    # queue was stopped — i.e. there is a fault outside the queue that any
    # backoff accumulated so far may simply have been measuring.
    device_was_away: bool = False
    # When the device first read online again after that. None while it is
    # still away, or once the return has already been credited.
    online_since: datetime | None = None


_attempts: dict[str, _Attempt] = {}


def reset() -> None:
    """Drops all remembered backoff state. For tests, and for callers that
    know the world has changed underneath them."""
    _attempts.clear()


def forget(printer_id: str) -> None:
    _attempts.pop(printer_id, None)


def _lc_all_c_env() -> dict[str, str]:
    """lpstat's output is translated, and this module reads it. Pinning the
    locale keeps the parsing below valid on a server whose LANG is not
    English rather than relying on systemd handing the service an empty
    environment."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    return env


def _lpstat(queue_names: list[str]) -> str | None:
    """Raw `lpstat -p` output for these queues, or None if the command
    failed — which includes any one of the names not existing."""
    try:
        result = subprocess.run(
            ["lpstat", "-p", ",".join(queue_names)],
            capture_output=True,
            text=True,
            timeout=LPSTAT_TIMEOUT_SECONDS,
            env=_lc_all_c_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _parse(output: str) -> LocalQueueState | None:
    """Reads one or more `lpstat -p` blocks. Under LC_ALL=C cupsd writes
    exactly one of:

        printer <name> disabled since <date> -
        printer <name> is idle.  enabled since <date>
        printer <name> now printing <job>.  enabled since <date>

    each optionally followed by indented detail lines carrying the state
    message. "now printing" matters as much as "is idle": reading a busy
    printer as stopped would have PrintOps resuming queues all day.
    """
    stopped = False
    message: str | None = None
    in_stopped_block = False
    saw_a_queue = False

    for line in output.splitlines():
        if line.startswith("printer "):
            saw_a_queue = True
            in_stopped_block = " disabled since " in line
            stopped = stopped or in_stopped_block
            continue
        detail = line.strip()
        # First message from the first stopped queue wins. The client-facing
        # queue is asked for first, so that is the one an admin sees — it is
        # the queue their users' jobs are sitting on.
        if in_stopped_block and detail and message is None:
            message = detail

    if not saw_a_queue:
        return None
    return LocalQueueState(stopped=stopped, message=message)


def local_queue_state(printer_id: str) -> LocalQueueState | None:
    """Whether cupsd has either of this printer's queues stopped, or None if
    that couldn't be determined.

    Both queues are checked, not just the client-facing one. The internal
    release queue (app/printers/release.py) delivers to the same device over
    IPP and can be stopped by exactly the same backend failure — and when it
    is, PIN releases at the panel fail silently, which is harder to notice
    than a queue that visibly backs up.

    None (couldn't tell) is deliberately distinct from "running": a queue
    that lpstat didn't answer for is not evidence of a healthy one, and
    callers must not resume — or write status off — on the strength of a
    failed command.
    """
    client = f"printops-{printer_id}"
    release = f"printops-release-{printer_id}"

    output = _lpstat([client, release])
    if output is None:
        # lpstat fails the whole call if any name is unknown. A virtual
        # Follow-Me queue has no release queue by design (queue_sync.sync_queue
        # skips it), and a printer whose queue was never synced has neither —
        # so fall back to asking about the client queue on its own rather than
        # reporting "couldn't tell" for every virtual printer forever.
        output = _lpstat([client])
    if output is None:
        return None
    return _parse(output)


def _backoff(failures: int) -> timedelta:
    if failures <= 0:
        return RESUME_COOLDOWN
    # Clamped before the shift for the same reason as
    # app/printers/status.py:_auto_resync_backoff — a printer that has been
    # failing for weeks reaches four figures, and 2**that overflows rather
    # than merely being large.
    doublings = min(failures, _MAX_BACKOFF_DOUBLINGS)
    return min(RESUME_COOLDOWN * (2**doublings), MAX_RESUME_BACKOFF)


def resume_due(printer_id: str, now: datetime | None = None) -> bool:
    """Whether an automatic resume for this printer is off cooldown.

    Only automatic attempts are rate-limited. Someone who clicks "Check
    Status" is asking for this to be tried now and is answering for the
    consequences themselves — the same rule
    app/printers/status.py:auto_resync_due states for manual resyncs."""
    attempt = _attempts.get(printer_id)
    if attempt is None:
        return True
    now = now or datetime.now(UTC)
    return now - attempt.last_attempt >= _backoff(attempt.failures)


def note_still_stopped(printer_id: str) -> None:
    """Records the verdict on a resume we already made: the queue is stopped
    again, so it didn't hold and the next attempt waits longer.

    Counts at most once per resume — see _Attempt.awaiting_verdict. Doing
    nothing when there was no previous attempt is intentional: a queue that
    has been stopped since before this process started has not failed a
    recovery, it has simply never had one."""
    attempt = _attempts.get(printer_id)
    if attempt is None or not attempt.awaiting_verdict:
        return
    attempt.failures += 1
    attempt.awaiting_verdict = False


def note_device_away(printer_id: str) -> None:
    """Records that the device itself is not reporting online while its queue
    is stopped.

    Nothing is resumed in that state (app/printers/status.py:_apply_queue_recovery),
    so this changes no decision on its own — it only arms note_device_back
    below, so that the device's return counts as new information rather than
    as one more identical poll."""
    attempt = _attempts.get(printer_id)
    if attempt is None:
        return
    attempt.device_was_away = True
    # Any earlier partial return didn't last, so it doesn't count toward the
    # next one.
    attempt.online_since = None


def note_device_back(printer_id: str, now: datetime | None = None) -> None:
    """Records that the device is reporting online again, and drops the
    backoff once it has stayed that way for HEALTHY_RESET_PERIOD.

    Called on every poll where the queue is stopped and the device reads
    online — the sustained-ness is measured here rather than by the caller,
    which has no memory between polls.

    Does nothing for a printer that never went away: a queue whose resumes
    don't hold while the device insists it is fine is precisely the case the
    backoff is for, and there is nothing new to learn from another healthy
    reading of a device that was healthy all along."""
    attempt = _attempts.get(printer_id)
    if attempt is None or not attempt.device_was_away:
        return
    now = now or datetime.now(UTC)
    if attempt.online_since is None:
        attempt.online_since = now
        return
    if now - attempt.online_since < HEALTHY_RESET_PERIOD:
        return
    # The fault everything here was learned from is over, so drop the lot
    # rather than merely halving the wait: this printer is now in exactly the
    # state of one whose queue has just been found stopped for the first time,
    # which is a queue to start now. That includes the plain cooldown from the
    # last attempt — an attempt made before the device went away, about a
    # device that has since been taken apart and put back together.
    forget(printer_id)


def _failures(printer_id: str) -> int:
    attempt = _attempts.get(printer_id)
    return attempt.failures if attempt else 0


def resume_queue(printer_id: str, now: datetime | None = None) -> None:
    """Runs `cupsenable`/`cupsaccept` for this printer's queues. Raises
    QueueResumeError on failure — the caller turns that into a status
    message rather than letting it escape, since a status poll that raises
    would stop every other printer in the cycle from being checked."""
    _attempts[printer_id] = _Attempt(
        last_attempt=now or datetime.now(UTC),
        failures=_failures(printer_id),
        awaiting_verdict=True,
    )
    try:
        result = subprocess.run(
            [str(RESUME_SCRIPT), printer_id],
            capture_output=True,
            text=True,
            timeout=RESUME_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise QueueResumeError(f"{RESUME_SCRIPT.name} not found on the PrintOps server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise QueueResumeError(
            f"{RESUME_SCRIPT.name} timed out after {RESUME_TIMEOUT_SECONDS}s."
        ) from exc
    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        raise QueueResumeError(reason or f"{RESUME_SCRIPT.name} exited {result.returncode}.")


def paused_reason(state: LocalQueueState, resumed: bool) -> str:
    """The operator-facing explanation, in the same voice as
    app/printers/queue_stall.py:stall_reason — says what was observed and
    what was done about it, without asserting a cause it cannot know."""
    detail = f" cupsd's reason: {state.message}" if state.message else ""
    if resumed:
        return (
            "This printer's queue on the print server had been stopped by CUPS and "
            "was started again automatically. Jobs waiting behind it are printing "
            f"now.{detail}"
        )
    return (
        "This printer's queue on the print server is stopped, so jobs sent to it "
        "are piling up instead of printing. CUPS stops a queue by itself when a job "
        "fails badly enough — usually because the printer was switched off, "
        f"disconnected or taken away for service.{detail}"
    )
