"""What a printer is offered at submission time, and what reaches lp.

PrintOps has discovered finishing capabilities since capability detection was
built, and until now nothing used them at the point a job is submitted. The
rule everywhere here is the same one #94 established for colour: never offer
what the device cannot do, and never send what it did not report. An option
that silently does nothing is worse than no option, because the person believes
they used it.

The capability dicts below are real shapes taken from this estate — five
machines report the staple/punch set, one reports only trim (the plotter), and
forty-four report an empty list.
"""

import pytest

from app.self_service_print.options import (
    DEFAULT_SIDES,
    lp_options,
    offered_finishings,
    supports_duplex,
)

FINISHER = {
    "duplex_supported": True,
    "finishings": [
        "staple",
        "punch",
        "job-offset",
        "staple-top-left",
        "staple-dual-left",
        "punch-dual-left",
        "punch-triple-top",
    ],
}
STAPLE_ONLY = {"duplex_supported": True, "finishings": ["staple", "job-offset"]}
PLOTTER = {"duplex_supported": False, "finishings": ["trim"]}
PLAIN = {"duplex_supported": True, "finishings": []}
NEVER_PROBED = None


def test_positional_variants_are_not_offered():
    """These devices report up to fifteen finishings, most of them positions.
    A self-service upload page is not where somebody chooses which corner —
    asking for a staple and letting the printer apply its own default is what a
    person at a web form means."""
    assert offered_finishings(FINISHER) == ["staple", "punch"]


def test_only_what_the_printer_reports_is_offered():
    assert offered_finishings(STAPLE_ONLY) == ["staple"]
    assert offered_finishings(PLAIN) == []
    # The plotter's trim is a real finishing and deliberately not offerable.
    assert offered_finishings(PLOTTER) == []


def test_a_printer_that_was_never_probed_offers_nothing():
    """Two printers on this estate have never answered a capability probe. The
    honest answer is to offer nothing; guessing produces a job that quietly
    comes out wrong."""
    assert offered_finishings(NEVER_PROBED) == []
    assert supports_duplex(NEVER_PROBED) is False


def test_the_default_sides_value_adds_no_option():
    """One-sided is what a queue does anyway. Sending it explicitly would
    override a queue whose admin-set default is duplex, which is the opposite of
    what leaving the control alone should mean."""
    assert lp_options(sides=DEFAULT_SIDES, finishings=[], capabilities=FINISHER) == []


def test_duplex_reaches_lp_when_the_printer_supports_it():
    assert lp_options(sides="two-sided-long-edge", finishings=[], capabilities=FINISHER) == [
        "-o",
        "sides=two-sided-long-edge",
    ]


def test_duplex_is_dropped_on_a_printer_that_cannot_do_it():
    assert lp_options(sides="two-sided-long-edge", finishings=[], capabilities=PLOTTER) == []


@pytest.mark.parametrize(
    ("asked", "capabilities", "expected"),
    [
        (["staple"], FINISHER, ["-o", "finishings=4"]),
        (["punch"], FINISHER, ["-o", "finishings=5"]),
        (["staple", "punch"], FINISHER, ["-o", "finishings=4", "-o", "finishings=5"]),
        # Asked for on a machine that reports only a stapler.
        (["staple", "punch"], STAPLE_ONLY, ["-o", "finishings=4"]),
        # Asked for on a machine with no finisher at all.
        (["staple"], PLAIN, []),
        # Asked for on a machine nobody has probed.
        (["staple"], NEVER_PROBED, []),
        # Not an IPP finishing this code knows.
        (["laminate"], FINISHER, []),
    ],
)
def test_finishings_are_filtered_against_the_device(asked, capabilities, expected):
    assert lp_options(sides=DEFAULT_SIDES, finishings=asked, capabilities=capabilities) == expected


def test_options_are_shaped_as_separate_flags():
    """`lp` takes a 1setOf here as repeated -o flags, which is what CUPS's own
    tools emit. Comma-joining is accepted by some versions and not others."""
    options = lp_options(
        sides="two-sided-long-edge", finishings=["staple", "punch"], capabilities=FINISHER
    )
    assert options.count("-o") == 3
    assert "finishings=4,5" not in options


def test_nothing_asked_for_sends_nothing():
    """A submission with no options must leave the queue's own defaults alone —
    including the colour default #94 exists to get right."""
    assert lp_options(sides=None, finishings=None, capabilities=FINISHER) == []
