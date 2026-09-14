"""Stops PrintOps feeding a printer the job that keeps taking it down.

On 2026-09-11 an office HP LaserJet 600 M601 crashed with firmware error
49.4A.04 part-way through one PDF. CUPS did what it does with a job it could not
deliver: it kept the job at the head of the queue and retried. Every time
someone power-cycled the printer, CUPS sent it the same file moments after it
booted, and it crashed again. It showed the error for three days, and CUPS's
own record of the job read 24 pages sent for a 2-page document.

Nothing in PrintOps could see it. The printer never answered a status check
between power-cycles — it was down again before the next poll — so to every
signal PrintOps had it was an offline printer with work waiting, which is
ordinary.

Two things break the loop, and both are deliberately small:

- **Pause on loss.** When the poll finds that a printer has stopped answering
  while cupsd is part-way through sending it a job, both of its queues are
  paused. A power-cycled printer then comes back idle, the next poll sees it
  answer, and queue recovery (app/printers/queue_recovery.py) starts the queue
  again. An ordinary outage loses nothing: the job goes back to waiting and
  prints when the printer returns, which is what CUPS would have done anyway.
- **Hold on the second loss.** If the printer goes away again while cupsd is
  sending the *same* job, that job is held and the queue restarts without it.
  An admin releases or cancels it from the printer's page.

Why not count how many times CUPS has sent a job? That was measured before this
was written, across every job cupsd still remembered. Of 467 countable jobs, the
job behind the loop had been sent twelve times its length — but innocent jobs
queued behind the crashed printer had been retried against it too, one four
times over and another three, and that one printed normally once the printer was
fixed. A resend count cannot tell the job that breaks a printer from a job
waiting behind a broken one. A second loss during delivery of the same job can.

State is in-process and resets on restart, like app/printers/queue_stall.py. A
restart can cost one extra crash before a job is held; it can never hold a job
on evidence this process did not see. A job already held stays held in cupsd
across restarts regardless.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Losses during delivery of one job before it is held. Two, not one: a printer
# switched off part-way through a job is ordinary, and the job it was printing
# is innocent. The same job in flight when the printer goes down a second time
# is not something a switch-off produces.
STRIKES_BEFORE_HOLD = 2

# Appended to Printer.status_reasons while a job PrintOps held is waiting for a
# decision. Namespaced like printops-queue-paused: our observation, not the
# printer's.
HELD_JOB_REASON = "printops-job-held"


@dataclass(frozen=True)
class HeldSuspect:
    cups_job_id: int
    document_name: str | None
    owner: str | None
    held_at: datetime


# printer id -> CUPS job id -> losses while that job was being delivered
_strikes: dict[str, dict[int, int]] = {}
# printers whose queues PrintOps paused, so recovery can say who stopped them
_paused: set[str] = set()
_held: dict[str, dict[int, HeldSuspect]] = {}


def reset() -> None:
    """Drops all remembered state. For tests."""
    _strikes.clear()
    _paused.clear()
    _held.clear()


def note_lost_during(printer_id: str, in_flight: tuple[int, ...]) -> list[int]:
    """Records that this printer stopped answering while cupsd was sending it
    these jobs, and returns the ones that have now been in flight for
    STRIKES_BEFORE_HOLD losses.

    Only jobs in flight *this* time keep their count. A job that finished
    between two losses was not being sent when the second one happened, and a
    count that outlived it would let two unrelated outages add up to a hold."""
    counts = _strikes.setdefault(printer_id, {})
    for finished in set(counts) - set(in_flight):
        del counts[finished]
    reached = []
    for cups_job_id in in_flight:
        counts[cups_job_id] = counts.get(cups_job_id, 0) + 1
        if counts[cups_job_id] >= STRIKES_BEFORE_HOLD:
            reached.append(cups_job_id)
    return reached


def strikes(printer_id: str, cups_job_id: int) -> int:
    return _strikes.get(printer_id, {}).get(cups_job_id, 0)


def note_paused(printer_id: str) -> None:
    _paused.add(printer_id)


def paused_by_printops(printer_id: str) -> bool:
    return printer_id in _paused


def note_resumed(printer_id: str) -> None:
    _paused.discard(printer_id)


def record_held(printer_id: str, suspect: HeldSuspect) -> None:
    _held.setdefault(printer_id, {})[suspect.cups_job_id] = suspect


def held(printer_id: str) -> list[HeldSuspect]:
    return sorted(_held.get(printer_id, {}).values(), key=lambda suspect: suspect.held_at)


def forget_held(printer_id: str, cups_job_id: int, *, released: bool) -> None:
    """An admin has dealt with a held job.

    A released job keeps all but one of its strikes: if the printer goes down
    while it is being sent again, that loss holds it again rather than starting
    the count over — releasing it was a judgement the printer can still
    overrule. A cancelled job is gone, and so is its count."""
    _held.get(printer_id, {}).pop(cups_job_id, None)
    counts = _strikes.setdefault(printer_id, {})
    if released:
        counts[cups_job_id] = STRIKES_BEFORE_HOLD - 1
    else:
        counts.pop(cups_job_id, None)


def forget_held_except(printer_id: str, still_held: set[int]) -> None:
    """Forgets held jobs that cupsd is no longer holding — released or
    cancelled somewhere other than PrintOps, or finished."""
    remembered = _held.get(printer_id, {})
    for cups_job_id in set(remembered) - still_held:
        remembered.pop(cups_job_id, None)


def held_reason(suspects: list[HeldSuspect]) -> str:
    """The operator-facing explanation, in the voice of
    app/printers/queue_recovery.py:paused_reason — what was observed and what
    was done, without claiming a cause it cannot know."""
    first = suspects[0]
    what = f'"{first.document_name}"' if first.document_name else f"job {first.cups_job_id}"
    who = f" from {first.owner}" if first.owner else ""
    others = len(suspects) - 1
    more = (
        f", and {others} other job{'s' if others != 1 else ''} held the same way" if others else ""
    )
    return (
        f"PrintOps is holding {what}{who}{more}. This printer stopped answering both times it "
        "was being sent that job, so it has been set aside and the rest of the queue is "
        "printing. A printer that keeps failing on one document is usually failing on the "
        "document itself. Release or cancel it from this printer's page."
    )
