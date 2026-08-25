"""Everything around the media-col workaround except the probe itself.

Three separate ways this could quietly stop protecting the printer it was
written for, all of them silent until a queue stops again:

- probing a device that never advertises media-col, and marking it broken for
  dropping a collection CUPS was never going to send it;
- letting a re-probe that couldn't reach the device erase a fault already
  confirmed against it;
- stripping the page size on the ordinary print path but not when a *held*
  job is released, which goes out through a queue with no PrintOps backend on
  it at all.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.printer import Printer
from app.printers.discovery import _detect_media_col
from app.printers.ipp_client import ProbeResult
from app.printers.media_options import strip_media_options
from app.printers.release import submit_released_job

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "infra" / "cups" / "backends" / "printops"

ADVERTISES = {"media-col-supported": ["media-size", "media-source"]}


def printer(**kwargs) -> Printer:
    return Printer(name="LCACTC - GA Kyocera", ip_address="10.50.1.37", port=443, **kwargs)


def probe_result(raw: dict) -> ProbeResult:
    return ProbeResult(raw_attributes=raw, resolved_path="/ipp/print", resolved_tls=True)


# --- when the question is worth asking at all -------------------------------


async def test_a_device_that_never_advertises_media_col_is_not_probed():
    with patch("app.printers.discovery.detect_media_col_broken") as probe:
        result = await _detect_media_col(printer(), probe_result({}), None)
    assert result is False
    # Not merely "answered False" — never asked. CUPS builds the collection
    # because the device advertises it, so this device cannot meet the fault,
    # and a legacy device that drops on any unknown collection must not be
    # marked broken for it.
    probe.assert_not_called()


async def test_a_device_that_advertises_media_col_is_probed():
    with patch("app.printers.discovery.detect_media_col_broken", return_value=True) as probe:
        result = await _detect_media_col(printer(), probe_result(ADVERTISES), None)
    assert result is True
    probe.assert_called_once()


# --- keeping a confirmed fault ----------------------------------------------


async def test_an_unreachable_reprobe_keeps_a_confirmed_fault():
    with patch("app.printers.discovery.detect_media_col_broken", return_value=None):
        result = await _detect_media_col(printer(), probe_result(ADVERTISES), True)
    # The backend acts only on a truthy value, so None here would switch the
    # workaround off — and a printer coming back from offline, when the probe
    # is likeliest to time out, is exactly when the next job arrives.
    assert result is True


async def test_a_clean_answer_still_clears_a_confirmed_fault():
    with patch("app.printers.discovery.detect_media_col_broken", return_value=False):
        result = await _detect_media_col(printer(), probe_result(ADVERTISES), True)
    # An explicit False is the device answering; a firmware fix has to be able
    # to take effect without anyone editing a database row.
    assert result is False


async def test_nothing_confirmed_and_nothing_learned_stays_unknown():
    with patch("app.printers.discovery.detect_media_col_broken", return_value=None):
        assert await _detect_media_col(printer(), probe_result(ADVERTISES), None) is None


# --- the release path -------------------------------------------------------


@pytest.fixture
def lp_argv():
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "request id is printops-release-x-1 (1 file(s))"
        stderr = ""

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return Result()

    with patch("app.printers.release.subprocess.run", side_effect=fake_run):
        yield calls


HELD_OPTIONS = "job-uuid=urn:uuid:6ab media=Letter PageSize=Letter print-color-mode=color"


def test_releasing_to_an_affected_printer_drops_the_page_size(lp_argv):
    submit_released_job("p1", "/spool/doc", "Advisory.pdf", 1, HELD_OPTIONS, True)
    [argv] = lp_argv
    assert "media=Letter" not in argv
    assert "PageSize=Letter" not in argv
    # Everything else the job was submitted with still goes.
    assert "print-color-mode=color" in argv
    assert "job-uuid=urn:uuid:6ab" in argv


def test_releasing_to_an_ordinary_printer_keeps_the_page_size(lp_argv):
    submit_released_job("p1", "/spool/doc", "Advisory.pdf", 1, HELD_OPTIONS, False)
    [argv] = lp_argv
    assert "media=Letter" in argv


def test_release_defaults_to_leaving_options_alone(lp_argv):
    submit_released_job("p1", "/spool/doc", "Advisory.pdf", 1, HELD_OPTIONS)
    [argv] = lp_argv
    assert "media=Letter" in argv


# --- the two copies of the stripping rule -----------------------------------


@pytest.fixture
def backend_module():
    loader = SourceFileLoader("printops_cups_backend", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "options",
    [
        "",
        "media=Letter",
        "job-uuid=urn:uuid:6ab media=Letter PageSize=Letter print-color-mode=color",
        'number-up=1 media-size="custom 8x10" sides=one-sided',
        "job-note='printed from media=usb' media=Letter",
        r"job-name=Quarterly\ Report media=Letter",
        "job-media=Letter finishings=none",
        "PrintoutMode=Normal media-top-margin=400 OKControl=Auto",
    ],
)
def test_backend_and_api_strip_identically(backend_module, options):
    # The CUPS backend is standalone (no package imports, runs as root outside
    # this venv) so it cannot import app.printers.media_options. Two copies of
    # one rule is a liability; this is the thing that notices when they drift.
    assert backend_module.strip_media_options(options) == strip_media_options(options)
