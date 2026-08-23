"""Notices a printer whose network path is dropping traffic while the printer
itself is answering perfectly well.

The case this was written for: on 2026-08-23 the LCACTC RM 502 was moved onto a
switch port that had not been used in a long time. Measured from the PrintOps
server, it then lost about 18% of the packets sent to it — blackouts of ~7
seconds arriving every ~37 seconds, under continuous once-a-second traffic, so
not the device sleeping between requests. Its own interface counters were
spotless: link up at 1 Gbps, no flap in three days, zero errors and zero
discards in either direction. The traffic was never reaching it.

What that did to PrintOps: the status poll's IPP probe overshot its five-second
timeout often enough to flip the printer offline and straight back, and every
return trip bought a capability rediscovery and a full queue resync. 0.59.6
stopped it reacting to that — two consecutive misses are now required before a
printer is called offline — which is right, and which also means the evidence
now goes nowhere at all. A printer that misses a probe every few minutes and
answers in between reads as perfectly healthy, so the one person who could fix
it, someone with access to the switch, never learns there is anything to fix.

Hence counting the misses. A probe that fails and is followed by one that
succeeds is not an offline printer: it is a working printer on a path that is
dropping traffic — a different fault, with a different owner, that no signal
PrintOps collects from the device will ever show. Printers get moved, ports get
reused, and the next instance of this will not announce itself either.

The history lives on the printer row (Printer.network_probe_log), not in this
module. app/printers/queue_stall.py keeps its state in memory on the reasoning
that a verdict this process did not observe is not one it can honestly report,
and that is right for a stall: it measures a duration that is still running, so
a restart genuinely breaks the observation. It is wrong for a loss count. These
are probes that were actually made and actually went unanswered; restarting the
API does not make them un-happen, and keeping them in memory meant every deploy
handed a lossy printer a fresh hour of looking healthy.

The functions here are pure — they take a log and return a new one — so the
router layer owns reading and writing the row, and nothing in this module needs
a session.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# How far back the miss rate is measured. An hour is long enough for an
# intermittent fault to show a pattern rather than a coincidence, and short
# enough that a path fixed this morning stops being reported this morning.
WINDOW = timedelta(hours=1)

# Misses inside the window before this is worth saying out loud. The status
# poll runs every 60s, so three is ~5% loss — well above the zero a healthy
# printer produces, and well below the ~7 an hour the RM 502 was producing.
# Deliberately not 1: a single missed probe is ordinary, which is the whole
# reason the offline debounce exists.
MISS_THRESHOLD = 3

# ...but only for a printer that is also *answering*. A printer that has simply
# gone away misses every probe, and "offline" already says that better than any
# loss rate could. What is being reported here is specifically the mixture:
# working, and unreliable to reach.
MIN_SUCCESSES = 1

# A probe that answered, but took this long or longer, counts as evidence of
# loss too — and it is the evidence that matters, because a failed probe is the
# rare case. The status probe opens a TCP connection with a five-second
# timeout, and TCP retransmits a lost SYN at roughly one second and again at
# three. RM 502's blackouts last about seven seconds, so a probe only *fails*
# if it starts in the first two or three of one; every other probe that runs
# into a blackout succeeds on a retransmit and looks perfectly healthy. That is
# how the first version of this alarm sat silent through a printer losing a
# fifth of its traffic: it was built on the pass/fail signal that the offline
# debounce had deliberately made tolerant.
#
# Two seconds, because a healthy IPP probe on this fleet measured 0.4-1.2s
# end to end and one lost SYN adds about a second on top of that.
SLOW_PROBE_SECONDS = 2.0

# ...but only for a printer that is otherwise quick. A device that always takes
# three seconds is a slow device, not a lossy path, and calling it lossy every
# hour of every day is how an alarm gets ignored. Slow probes count as evidence
# only when this same printer has also answered quickly inside the window, so
# each one is judged against itself rather than a fleet-wide guess.
FAST_PROBE_SECONDS = 1.0
# The keyword the UI keys off to render this as a warning about the network
# rather than a fault on the printer (apps/web/.../printers).
NETWORK_UNSTABLE_REASON = "printops-network-unstable"


@dataclass(frozen=True)
class NetworkFlap:
    """A window's worth of evidence that the path to a printer is lossy."""

    misses: int
    slow: int
    probes: int
    window: timedelta

    @property
    def affected(self) -> int:
        """Probes that ran into the fault, whether or not they survived it."""
        return self.misses + self.slow

    @property
    def loss_percent(self) -> int:
        return round(100 * self.affected / self.probes) if self.probes else 0


ProbeLog = list[list]
"""[[iso timestamp, answered, seconds], ...] — JSON-native, so it round-trips
through the column without a converter. Entries written before probe timing
existed have two elements and are read as "no duration recorded"."""


def _prune(log: ProbeLog | None, cutoff: datetime) -> ProbeLog:
    """Drops entries that have aged out of the window, and anything unparseable.

    Tolerant on purpose: this is a rolling diagnostic, and a row that somehow
    holds a malformed entry should lose that entry, not take the status poll
    down with it."""
    kept: ProbeLog = []
    for entry in log or []:
        try:
            stamp, answered = entry[0], entry[1]
            seconds = entry[2] if len(entry) > 2 else None
            when = datetime.fromisoformat(stamp)
        except (TypeError, ValueError, IndexError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when >= cutoff:
            kept.append([stamp, bool(answered), _as_seconds(seconds)])
    return kept


def _as_seconds(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def observe(
    log: ProbeLog | None,
    answered: bool,
    seconds: float | None = None,
    now: datetime | None = None,
) -> tuple[ProbeLog, NetworkFlap | None]:
    """Records one probe outcome against a printer's log, returning the log to
    store and the flap verdict if the recent history now warrants one.

    Returning the evidence rather than a bool keeps "is this worth reporting"
    with the caller, and lets the message say what was actually seen — the
    difference between an alert someone acts on and one they learn to dismiss.
    """
    now = now or datetime.now(UTC)
    history = _prune(log, now - WINDOW)
    history.append([now.isoformat(), answered, _as_seconds(seconds)])

    misses = sum(1 for entry in history if not entry[1])
    successes = len(history) - misses
    # Judged against this printer's own quick answers — see FAST_PROBE_SECONDS.
    normally_quick = any(
        entry[1] and entry[2] is not None and entry[2] < FAST_PROBE_SECONDS for entry in history
    )
    slow = (
        sum(
            1
            for entry in history
            if entry[1] and entry[2] is not None and entry[2] >= SLOW_PROBE_SECONDS
        )
        if normally_quick
        else 0
    )

    if misses + slow < MISS_THRESHOLD or successes < MIN_SUCCESSES:
        return history, None
    return history, NetworkFlap(misses=misses, slow=slow, probes=len(history), window=WINDOW)


def flap_reason(flap: NetworkFlap) -> str:
    """The operator-facing explanation. Says what was observed, says what it
    is not, and points at the part of the system that can actually be
    checked — without asserting a cause this cannot know."""
    hours = int(flap.window.total_seconds() // 3600)
    window_text = "the last hour" if hours == 1 else f"the last {hours} hours"
    if flap.misses and flap.slow:
        detail = f"{flap.misses} got no reply at all and {flap.slow} answered only after retrying"
    elif flap.misses:
        detail = f"{flap.misses} got no reply at all"
    else:
        detail = f"{flap.slow} answered only after retrying"
    return (
        f"{flap.affected} of {flap.probes} status checks in {window_text} ran into "
        f"trouble reaching this printer ({flap.loss_percent}%) — {detail}, while it "
        f"answered normally in "
        "between. That pattern is the network path dropping traffic rather than a fault "
        "on the printer, which will report itself healthy throughout. Worth checking the "
        "switch port, the cable and the wall port, particularly if this printer was "
        "moved recently."
    )
