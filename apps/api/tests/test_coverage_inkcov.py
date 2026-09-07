"""Reading Ghostscript's coverage output.

The parser is separated from the subprocess because the interesting cases are
all in the text: a page that failed to render, a document whose own words
happen to look like a data line, a build that reports something out of range.
Each of those, read wrongly, produces a plausible number rather than an error —
which is the failure worth testing, since nothing downstream would question it.
"""

import pytest

from app.coverage.inkcov import Coverage, parse

# A real four-page job measured on a live queue. The last page is nearly blank
# and the first is eight times denser, which is exactly the spread a page count
# discards.
REAL = """ 0.11069  0.10946  0.10915  0.08235 CMYK OK
 0.08590  0.08394  0.08833  0.06784 CMYK OK
 0.09764  0.09414  0.09764  0.06691 CMYK OK
 0.01301  0.01301  0.01301  0.01301 CMYK OK
"""


def test_the_mean_is_taken_across_pages():
    coverage = parse(REAL)
    assert coverage is not None
    assert coverage.pages == 4
    assert coverage.black == pytest.approx((0.08235 + 0.06784 + 0.06691 + 0.01301) / 4)
    assert coverage.cyan == pytest.approx((0.11069 + 0.08590 + 0.09764 + 0.01301) / 4)


def test_a_page_that_failed_to_render_is_skipped_not_counted_as_blank():
    """A page that errored is not a blank page. Averaging it in as zero
    understates the document by however many pages failed, and the result still
    looks like a perfectly ordinary measurement."""
    with_error = """ 0.20000  0.00000  0.00000  0.10000 CMYK OK
 0.00000  0.00000  0.00000  0.00000 CMYK ERROR
"""
    coverage = parse(with_error)
    assert coverage is not None
    assert coverage.pages == 1, "the failed page is not measured"
    assert coverage.black == pytest.approx(0.10000)


def test_document_text_is_not_mistaken_for_a_measurement():
    """Ghostscript writes the measurements to the same stream as anything else
    it has to say. A line has to look exactly like a page report to count."""
    noisy = """GPL Ghostscript 10.06: warning, something happened
 0.5 0.5 0.5 0.5 these are four numbers but not a page
Page 1 of 4
 0.10000  0.10000  0.10000  0.10000 CMYK OK
"""
    coverage = parse(noisy)
    assert coverage is not None
    assert coverage.pages == 1


def test_nothing_measurable_is_distinct_from_measuring_zero():
    """ "No coverage" and "coverage of zero" are different facts, and a report
    that cannot tell them apart averages one into the other."""
    assert parse("") is None
    assert parse("GPL Ghostscript: Unrecoverable error, exit code 1") is None

    blank = parse(" 0.00000  0.00000  0.00000  0.00000 CMYK OK\n")
    assert blank is not None and blank.pages == 1
    assert blank.total_ink == 0.0


def test_an_out_of_range_channel_is_clamped():
    """A build that reports something above 1.0 should not make a document look
    impossibly dense — and thereby cost several times what it did."""
    coverage = parse(" 3.50000  0.00000  0.00000  0.00000 CMYK OK\n")
    assert coverage is not None
    assert coverage.cyan == 1.0


def test_total_ink_sums_the_channels():
    """A page at ISO test conditions is about 5% per colorant — roughly 0.20
    total across four channels. The distinction matters: a cartridge yield is
    quoted per colorant, so costing against a total would assume a colour mix
    nobody measured."""
    coverage = Coverage(pages=1, cyan=0.05, magenta=0.05, yellow=0.05, black=0.05)
    assert coverage.total_ink == pytest.approx(0.20)
