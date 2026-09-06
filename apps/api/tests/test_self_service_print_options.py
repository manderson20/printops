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
    offered_sides,
    supports_duplex,
)

FINISHER = {
    "duplex_supported": True,
    "sides_supported": ["two-sided-long-edge", "two-sided-short-edge"],
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
STAPLE_ONLY = {
    "duplex_supported": True,
    "sides_supported": ["two-sided-long-edge", "two-sided-short-edge"],
    "finishings": ["staple", "job-offset"],
}
# Reports one binding edge only — the shape already in test_printers_api.py.
LONG_EDGE_ONLY = {
    "duplex_supported": True,
    "sides_supported": ["two-sided-long-edge"],
    "finishings": [],
}
# Probed before sides_supported existed: all that is known is the boolean.
LEGACY_DUPLEX = {"duplex_supported": True, "finishings": []}
PLOTTER = {"duplex_supported": False, "sides_supported": [], "finishings": ["trim"]}
PLAIN = {
    "duplex_supported": True,
    "sides_supported": ["two-sided-long-edge"],
    "finishings": [],
}
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


def test_the_default_choice_adds_no_option():
    """ "Printer default" is the absence of a -o sides= argument, and says so on
    the form. The first version labelled this choice "One-sided" while sending
    nothing, so a queue whose admin-set default is duplex printed duplex under a
    form that read One-sided."""
    assert lp_options(sides=DEFAULT_SIDES, finishings=[], capabilities=FINISHER) == []


def test_choosing_one_sided_actually_sends_one_sided():
    """The half the first version got wrong. If the form says One-sided and the
    queue defaults to duplex, the job has to come out one-sided."""
    assert lp_options(sides="one-sided", finishings=[], capabilities=FINISHER) == [
        "-o",
        "sides=one-sided",
    ]


def test_one_sided_is_offered_even_where_duplex_is_not():
    """A printer with no duplex unit still prints one-sided; the control is
    simply not shown for it, and a direct request is harmless."""
    assert lp_options(sides="one-sided", finishings=[], capabilities=PLOTTER) == [
        "-o",
        "sides=one-sided",
    ]


def test_only_the_binding_edges_a_printer_reports_are_offered():
    """A machine that binds only on the long edge must not be offered
    short-edge: the job comes out bound the wrong way, or is rejected."""
    assert offered_sides(LONG_EDGE_ONLY) == ["two-sided-long-edge"]
    assert (
        lp_options(sides="two-sided-short-edge", finishings=[], capabilities=LONG_EDGE_ONLY) == []
    )


def test_a_row_probed_before_sides_supported_falls_back_to_long_edge():
    """Long-edge is the ordinary meaning of "double-sided" and the safe half of
    the guess. The rediscovery loop replaces it with the real list within
    thirty minutes."""
    assert offered_sides(LEGACY_DUPLEX) == ["two-sided-long-edge"]


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
        (["staple", "punch"], FINISHER, ["-o", "finishings=4,5"]),
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


def test_several_finishings_travel_as_one_comma_joined_option():
    """CUPS stores options by name and replaces on repeat (cupsAddOption), so
    `-o finishings=4 -o finishings=5` keeps only the punch and silently drops
    the staple. The first version did exactly that, and this test asserted it
    was correct — which is how an enshrined bug survives a green suite."""
    options = lp_options(
        sides="two-sided-long-edge", finishings=["staple", "punch"], capabilities=FINISHER
    )
    assert options == ["-o", "sides=two-sided-long-edge", "-o", "finishings=4,5"]
    assert options.count("-o") == 2


def test_nothing_asked_for_sends_nothing():
    """A submission with no options must leave the queue's own defaults alone —
    including the colour default #94 exists to get right."""
    assert lp_options(sides=None, finishings=None, capabilities=FINISHER) == []
