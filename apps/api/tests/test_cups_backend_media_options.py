"""Dropping the page size must not disturb anything else in a job's options.

infra/cups/backends/printops removes the page-size options from jobs bound for
a printer whose firmware cannot parse the media-col collection CUPS builds out
of them (see app/printers/media_col_probe.py). Everything else in that string
— the job UUID attribution depends on, the color mode, the vendor's own
options — has to survive untouched, and an options string can quote a value
containing a space, so this cannot be a naive split or a regex over the whole
string.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "infra" / "cups" / "backends" / "printops"


@pytest.fixture
def backend_module():
    loader = SourceFileLoader("printops_cups_backend", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_removes_every_spelling_of_the_page_size(backend_module):
    options = (
        "job-uuid=urn:uuid:6ab563d1 media=Letter PageSize=Letter PageRegion=Letter "
        "media-top-margin=400 media-bottom-margin=400 media-left-margin=400 "
        "media-right-margin=400 media-size=na_letter media-col=x print-color-mode=color"
    )
    assert backend_module.strip_media_options(options) == (
        "job-uuid=urn:uuid:6ab563d1 print-color-mode=color"
    )


def test_keeps_everything_else_exactly(backend_module):
    # Straight from a real macOS job on the Graphic Arts Kyocera.
    options = (
        "AP_ColorMatchingMode=AP_ApplicationColorMatching ColorModel=Color "
        "Duplex=None media=Letter PageSize=Letter PrintoutMode=Normal "
        "job-uuid=urn:uuid:6ab563d1 number-up=1"
    )
    assert backend_module.strip_media_options(options) == (
        "AP_ColorMatchingMode=AP_ApplicationColorMatching ColorModel=Color "
        "Duplex=None PrintoutMode=Normal job-uuid=urn:uuid:6ab563d1 number-up=1"
    )


def test_an_option_whose_value_contains_media_is_left_alone(backend_module):
    # The failure this guards against is silent and total: chop a quoted value
    # in half and every option after it is malformed, for every job.
    options = "job-note='printed from media=usb' media=Letter sides=one-sided"
    assert backend_module.strip_media_options(options) == (
        "job-note='printed from media=usb' sides=one-sided"
    )


def test_a_media_option_with_a_quoted_value_still_goes(backend_module):
    options = 'number-up=1 media-size="custom 8x10" sides=one-sided'
    assert backend_module.strip_media_options(options) == "number-up=1 sides=one-sided"


def test_options_that_merely_end_in_media_are_not_page_size(backend_module):
    assert backend_module.strip_media_options("job-media=Letter") == "job-media=Letter"


def test_backslash_escaped_space_holds_a_value_together(backend_module):
    options = r"job-name=Quarterly\ Report media=Letter"
    assert backend_module.strip_media_options(options) == r"job-name=Quarterly\ Report"


def test_empty_and_all_media_strings(backend_module):
    assert backend_module.strip_media_options("") == ""
    assert backend_module.strip_media_options("media=Letter PageSize=Letter") == ""
