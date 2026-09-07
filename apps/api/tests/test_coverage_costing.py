"""Turning measured coverage into money.

The arithmetic is one multiplication, which is exactly why it is worth testing:
every way of getting it wrong produces a plausible number. Comparing a total
against a per-channel baseline overstates colour about fourfold; using the
colour rate for a mono job prices black at four cartridges; a zero baseline
divides by zero and reports infinity as a cost.
"""

from dataclasses import dataclass

import pytest

from app.reports.formulas import PrinterTonerRate, measured_toner_cost

ISO = 0.05
RATE = PrinterTonerRate(mono_cost_per_page=0.01, color_cost_per_page=0.08)


@dataclass
class Coverage:
    cyan: float = 0.0
    magenta: float = 0.0
    yellow: float = 0.0
    black: float = 0.0


def test_a_page_at_the_test_conditions_costs_the_rated_rate():
    """The anchor: if a job covers exactly what the yield was measured
    against, the measured cost and the rated cost must agree. Any scaling
    error shows up here first."""
    at_iso = Coverage(black=ISO)
    result = measured_toner_cost(10, "mono", at_iso, RATE, ISO)

    assert result is not None
    assert result.ratio == pytest.approx(1.0)
    assert result.toner_cost == pytest.approx(0.01 * 10)


def test_a_denser_page_costs_proportionally_more():
    heavy = Coverage(black=0.20)
    result = measured_toner_cost(1, "mono", heavy, RATE, ISO)

    assert result is not None
    assert result.ratio == pytest.approx(4.0), "four times the test page"
    assert result.toner_cost == pytest.approx(0.04)


def test_a_nearly_blank_page_costs_a_fraction():
    """A real measured job on the first estate came in at 0.07 of the test
    page. Charging it the rated rate overstates it fourteenfold."""
    faint = Coverage(black=0.0035)
    result = measured_toner_cost(1, "mono", faint, RATE, ISO)

    assert result is not None
    assert result.ratio == pytest.approx(0.07)


def test_colour_compares_per_channel_not_against_total_ink():
    """The mistake worth guarding: a CMYK page at ISO conditions carries about
    20% ink across four channels. Comparing that total against a 5%
    per-channel baseline would price it at four times the rated rate, when by
    definition it should be exactly the rated rate."""
    at_iso = Coverage(cyan=ISO, magenta=ISO, yellow=ISO, black=ISO)
    result = measured_toner_cost(1, "color", at_iso, RATE, ISO)

    assert result is not None
    assert result.ratio == pytest.approx(1.0), "total ink is 0.20, but per channel it is 0.05"
    assert result.toner_cost == pytest.approx(0.08)


def test_a_mono_job_prices_off_black_alone():
    """Whatever the document held, a mono job puts down black. Pricing it off
    the colour rate would charge four cartridges for one."""
    colourful_document = Coverage(cyan=0.9, magenta=0.9, yellow=0.9, black=ISO)
    result = measured_toner_cost(1, "mono", colourful_document, RATE, ISO)

    assert result is not None
    assert result.ratio == pytest.approx(1.0), "the colour channels are not printed"
    assert result.toner_cost == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("baseline", "pages", "because"),
    [
        (0.0, 1, "a zero baseline would divide by zero and report infinity as a cost"),
        (-0.05, 1, "a negative baseline would report a negative cost"),
        (0.05, 0, "a job with no pages has no measured cost to give"),
    ],
)
def test_an_unusable_baseline_gives_no_figure_rather_than_a_wrong_one(baseline, pages, because):
    """The baseline is admin-settable, so it can be set to nonsense. No answer
    is recoverable; a cost of infinity propagates into every total that job
    appears in."""
    assert measured_toner_cost(pages, "mono", Coverage(black=0.05), RATE, baseline) is None, because


def test_the_baseline_scales_every_figure():
    """Someone whose datasheets quote 10% coverage halves every derived cost.
    Worth asserting explicitly: this single setting multiplies the lot, so a
    site that sets it wrong is wrong everywhere at once."""
    page = Coverage(black=0.10)
    at_five = measured_toner_cost(1, "mono", page, RATE, 0.05)
    at_ten = measured_toner_cost(1, "mono", page, RATE, 0.10)

    assert at_five is not None and at_ten is not None
    assert at_five.toner_cost == pytest.approx(at_ten.toner_cost * 2)
