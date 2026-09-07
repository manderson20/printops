"""Turning measured coverage into money.

The arithmetic is one multiplication, which is exactly why it is worth testing:
every way of getting it wrong produces a plausible number. Comparing a total
against a per-channel baseline overstates colour about fourfold; using the
colour rate for a mono job prices black at four cartridges; a zero baseline
divides by zero and reports infinity as a cost.
"""

from dataclasses import dataclass

import pytest

from app.reports.formulas import ChannelRates, PrinterTonerRate, measured_toner_cost

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


# --- per channel, not averaged ----------------------------------------------

CHANNELS = ChannelRates(cyan=0.10, magenta=0.10, yellow=0.10, black=0.01)


def test_a_black_heavy_colour_page_is_priced_off_black():
    """The mistake averaging hides.

    A page printed in colour that puts down only black consumes only the black
    cartridge — usually much the cheapest of the four. Averaging the channels
    and multiplying by the summed CMYK rate charges it a quarter of the total
    instead, which for these rates is nearly eight times too much.
    """
    rate = PrinterTonerRate(mono_cost_per_page=0.01, color_cost_per_page=0.31)
    black_only = Coverage(black=ISO)

    honest = measured_toner_cost(1, "color", black_only, rate, ISO, CHANNELS)
    averaged = measured_toner_cost(1, "color", black_only, rate, ISO, None)

    assert honest is not None and averaged is not None
    assert honest.toner_cost == pytest.approx(0.01), "the black cartridge's own rate"
    assert averaged.toner_cost == pytest.approx(0.31 / 4)
    assert averaged.toner_cost > honest.toner_cost * 7


def test_per_channel_still_agrees_at_the_test_conditions():
    """The anchor has to survive the change: a page exactly like the
    manufacturer's test page must cost exactly the rated rate, whichever way it
    is computed."""
    rate = PrinterTonerRate(
        mono_cost_per_page=CHANNELS.black,
        color_cost_per_page=CHANNELS.cyan + CHANNELS.magenta + CHANNELS.yellow + CHANNELS.black,
    )
    at_iso = Coverage(cyan=ISO, magenta=ISO, yellow=ISO, black=ISO)

    result = measured_toner_cost(1, "color", at_iso, rate, ISO, CHANNELS)
    assert result is not None
    assert result.toner_cost == pytest.approx(rate.color_cost_per_page)
    assert result.ratio == pytest.approx(1.0)


def test_channel_rates_need_every_cartridge():
    """A partial set has no honest per-channel answer, and the same condition
    governs whether cartridge pricing is used at all."""
    from dataclasses import dataclass as _dataclass

    @_dataclass
    class Cart:
        color: str
        cost: float
        yield_pages: int

    three = [Cart("black", 50, 5000), Cart("cyan", 60, 3000), Cart("magenta", 60, 3000)]
    assert ChannelRates.from_cartridges(three) is None

    four = [*three, Cart("yellow", 60, 3000)]
    rates = ChannelRates.from_cartridges(four)
    assert rates is not None
    assert rates.black == pytest.approx(0.01)


def test_a_zero_cost_cartridge_is_configured():
    """Matching compute_printer_rate: configured means a yield, whatever the
    cost. A bundled cartridge rates at zero rather than disabling the set."""
    from dataclasses import dataclass as _dataclass

    @_dataclass
    class Cart:
        color: str
        cost: float
        yield_pages: int

    rates = ChannelRates.from_cartridges(
        [Cart(c, 0.0 if c == "black" else 60, 3000) for c in ("black", "cyan", "magenta", "yellow")]
    )
    assert rates is not None
    assert rates.black == 0.0


# --- the displayed ratio must follow the same rule as the cost ---------------


def test_the_displayed_ratio_uses_black_alone_for_a_mono_job():
    """The API computes a ratio for display; costing computes one for money.
    They have to agree.

    Averaging four channels for a mono job divides by four, so an ordinary mono
    page at exactly the test coverage reads 0.25x — it looks like a bargain
    when by definition it is exactly 1.0x. Wrong numbers are worse than absent
    ones here: nothing about 0.25x invites a second look.
    """
    from app.routers.jobs import _coverage_out

    @dataclass
    class Row:
        cyan: float
        magenta: float
        yellow: float
        black: float
        pages_measured: int = 1
        measured_at: object = None

    mono_page = Row(cyan=0.0, magenta=0.0, yellow=0.0, black=ISO)

    shown = _coverage_out(mono_page, ISO, "monochrome")
    assert shown is not None
    assert shown.ratio == pytest.approx(1.0), "a mono test page is exactly the rated page"

    # And it matches what costing charges for the same job.
    charged = measured_toner_cost(1, "monochrome", mono_page, RATE, ISO)
    assert charged is not None
    assert shown.ratio == pytest.approx(charged.ratio)


def test_the_displayed_ratio_averages_channels_for_a_colour_job():
    from app.routers.jobs import _coverage_out

    @dataclass
    class Row:
        cyan: float
        magenta: float
        yellow: float
        black: float
        pages_measured: int = 1
        measured_at: object = None

    at_iso = Row(cyan=ISO, magenta=ISO, yellow=ISO, black=ISO)
    shown = _coverage_out(at_iso, ISO, "color")
    assert shown is not None
    assert shown.ratio == pytest.approx(1.0)
