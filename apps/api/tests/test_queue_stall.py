"""app/printers/queue_stall.py is the backstop that would have caught the
LCACTC Kyocera outage (2026-08-20) in half an hour instead of six.

The device answered SNMP, its panel read "Ready.", and its IPP endpoint
answered anything that followed a redirect — so every direct health signal was
green while three jobs sat undelivered. The one honest signal was that the head
of the queue never changed.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.printers import queue_stall
from app.printers.job_control import QueueSnapshot
from app.printers.queue_stall import STALL_THRESHOLD, observe, stall_reason

T0 = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
PRINTER = "8142ccdb-195b-4acf-acfd-56bc52162b72"


@pytest.fixture(autouse=True)
def _clean_state():
    queue_stall.reset()
    yield
    queue_stall.reset()


def _snap(head, depth=1):
    return QueueSnapshot(depth=depth, head_job=head)


def test_a_queue_stuck_on_one_job_is_reported_after_the_threshold():
    """The production signature: same job at the head, hours passing."""
    assert observe(PRINTER, _snap("job-4193", depth=3), now=T0) is None

    just_under = T0 + STALL_THRESHOLD - timedelta(seconds=1)
    assert observe(PRINTER, _snap("job-4193", depth=3), now=just_under) is None

    stuck = observe(PRINTER, _snap("job-4193", depth=3), now=T0 + STALL_THRESHOLD)
    assert stuck == STALL_THRESHOLD


def test_a_moving_queue_never_reports_a_stall():
    """A busy printer can stay deep all day. Progress is the head *changing*,
    not the queue getting shorter — otherwise every genuinely busy graphic-arts
    printer would alarm constantly."""
    observe(PRINTER, _snap("job-1", depth=5), now=T0)
    later = T0 + timedelta(hours=4)
    assert observe(PRINTER, _snap("job-2", depth=5), now=later) is None
    # ...and the clock restarts from the change, not from the first sighting.
    assert observe(PRINTER, _snap("job-2", depth=5), now=later + timedelta(minutes=1)) is None


def test_a_long_single_job_is_not_a_stall_until_it_really_is():
    """Graphic-arts jobs are legitimately slow. A 26 MB Photoshop file at
    600dpi takes minutes; it must not be killed for that. The threshold is what
    separates 'slow' from 'never'."""
    observe(PRINTER, _snap("big-render"), now=T0)
    assert observe(PRINTER, _snap("big-render"), now=T0 + timedelta(minutes=10)) is None
    assert observe(PRINTER, _snap("big-render"), now=T0 + timedelta(minutes=45)) is not None


def test_an_empty_queue_clears_the_stall_clock():
    observe(PRINTER, _snap("job-4193"), now=T0)
    assert (
        observe(PRINTER, QueueSnapshot(depth=0, head_job=None), now=T0 + timedelta(hours=1)) is None
    )
    # The same job reappearing starts a fresh clock rather than inheriting the
    # old one.
    assert observe(PRINTER, _snap("job-4193"), now=T0 + timedelta(hours=1)) is None


def test_unreadable_queue_state_is_not_treated_as_a_stall():
    """lpstat failing during a cupsd restart must not look like a queue that
    never moved — that would fire an alert precisely when the system is already
    being disturbed."""
    observe(PRINTER, _snap("job-4193"), now=T0)
    assert observe(PRINTER, None, now=T0 + timedelta(minutes=10)) is None
    # And having forgotten, it needs the full threshold again from here.
    observe(PRINTER, _snap("job-4193"), now=T0 + timedelta(minutes=11))
    assert observe(PRINTER, _snap("job-4193"), now=T0 + timedelta(minutes=20)) is None
    assert observe(PRINTER, _snap("job-4193"), now=T0 + timedelta(minutes=45)) is not None


def test_printers_are_tracked_independently():
    other = "1df55b5a-33ea-4a18-aaaf-19b02a4d2cbe"
    observe(PRINTER, _snap("job-a"), now=T0)
    observe(other, _snap("job-b"), now=T0 + timedelta(minutes=25))

    late = T0 + STALL_THRESHOLD
    assert observe(PRINTER, _snap("job-a"), now=late) is not None
    assert observe(other, _snap("job-b"), now=late) is None


def test_forget_drops_only_the_named_printer():
    other = "1df55b5a-33ea-4a18-aaaf-19b02a4d2cbe"
    observe(PRINTER, _snap("job-a"), now=T0)
    observe(other, _snap("job-b"), now=T0)

    queue_stall.forget(PRINTER)

    late = T0 + STALL_THRESHOLD
    assert observe(PRINTER, _snap("job-a"), now=late) is None
    assert observe(other, _snap("job-b"), now=late) is not None


def test_stall_reason_states_the_observation_and_stays_actionable():
    reason = stall_reason(timedelta(minutes=47), _snap("job-4193", depth=3))
    assert "47 minutes" in reason
    assert "3 jobs" in reason
    # Names where to look without asserting a cause it cannot know.
    assert "port/TLS/IPP path" in reason


def test_stall_reason_singular_for_one_job():
    assert "1 job waiting" in stall_reason(timedelta(minutes=31), _snap("job-x", depth=1))


# --- Size-proportionate grace ------------------------------------------------
#
# The graphic-arts printer's ordinary workload is Photoshop output. Judging a
# 26 MB job by the same clock as a 60 KB memo means either alarming on a whole
# class's normal work or setting the bar so high nothing is ever caught.


def _sized(head, size_bytes, depth=1):
    return QueueSnapshot(depth=depth, head_job=head, head_size_bytes=size_bytes)


def test_a_small_job_gets_only_the_base_threshold():
    """No arithmetic below the floor — a 60 KB memo must not end up with a
    30m03s threshold nobody can explain."""
    assert queue_stall.threshold_for(60 * 1024) == STALL_THRESHOLD
    assert queue_stall.threshold_for(None) == STALL_THRESHOLD
    assert queue_stall.threshold_for(0) == STALL_THRESHOLD
    assert queue_stall.threshold_for(queue_stall.SIZE_GRACE_FLOOR_BYTES) == STALL_THRESHOLD


def test_a_large_job_earns_proportionate_extra_grace():
    """The actual job from the 2026-08-20 incident: 26,391,552 bytes."""
    assert queue_stall.threshold_for(26_391_552) > STALL_THRESHOLD
    assert queue_stall.threshold_for(26_391_552) == STALL_THRESHOLD + timedelta(
        minutes=26_391_552 / (1024 * 1024)
    )


def test_size_grace_is_capped_so_a_huge_job_cannot_hide_forever():
    """Without a cap, a big enough job would postpone detection indefinitely —
    which is the blind spot this whole module exists to close."""
    assert queue_stall.threshold_for(10_000 * 1024 * 1024) == (
        STALL_THRESHOLD + queue_stall.MAX_SIZE_GRACE
    )


def test_a_big_job_is_not_flagged_at_the_small_job_threshold():
    """The behavioural consequence: 26 MB still printing at 35 minutes is fine,
    where a 60 KB job would already have been called stalled."""
    big = _sized("photoshop", 26_391_552)
    observe(PRINTER, big, now=T0)
    assert observe(PRINTER, big, now=T0 + timedelta(minutes=35)) is None

    queue_stall.reset()
    small = _sized("memo", 60_416)
    observe(PRINTER, small, now=T0)
    assert observe(PRINTER, small, now=T0 + timedelta(minutes=35)) is not None


def test_a_big_job_is_still_caught_eventually():
    big = _sized("photoshop", 26_391_552)
    observe(PRINTER, big, now=T0)
    assert observe(PRINTER, big, now=T0 + timedelta(hours=2)) is not None


def test_stall_reason_names_the_head_job_size_when_known():
    reason = stall_reason(timedelta(minutes=60), _sized("photoshop", 26_391_552, depth=3))
    assert "25.2 MB" in reason


def test_stall_reason_omits_size_when_unknown():
    reason = stall_reason(timedelta(minutes=31), _snap("job-x", depth=1))
    assert "MB" not in reason
