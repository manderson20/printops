"""What discovery finds goes in the cache column, never the override.

`ipp_path` is an admin's deliberate choice and `ipp_path_detected` is a cache of
what last answered (split in migration 0061). They need opposite treatment: an
override must survive discovery, a cached guess must be refreshable when the
device moves. While both lived in one column a guess could never be safely
revisited, which is how the LCACTC Kyocera stayed on a "/" detected back when it
still answered plain IPP.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.printer import Printer
from app.printers.discovery import refresh_printer_capabilities
from app.printers.ipp_client import ProbeResult


def _printer(**kwargs):
    return Printer(name="GA Kyocera", ip_address="10.50.1.37", **kwargs)


def _probe_returning(path="/ipp/print", tls=False):
    return AsyncMock(
        return_value=ProbeResult(
            raw_attributes={"printer-make-and-model": "ECOSYS P8060cdn"},
            resolved_path=path,
            resolved_tls=tls,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, ""], ids=["null", "empty-string"])
async def test_what_the_probe_finds_is_cached_not_written_to_the_override(stored):
    printer = _printer(ipp_path=stored)
    with patch("app.printers.discovery.probe_printer", _probe_returning("/ipp/print")):
        await refresh_printer_capabilities(printer)
    assert printer.ipp_path_detected == "/ipp/print"
    assert not printer.ipp_path  # the override column stays untouched
    assert printer.effective_ipp_path == "/ipp/print"


@pytest.mark.asyncio
async def test_the_cache_is_refreshed_when_the_device_moves():
    """The point of the split. A stale detection must be replaceable, or it
    acquires tenure and the printer never follows its device."""
    printer = _printer(ipp_path=None, ipp_path_detected="/")
    with patch("app.printers.discovery.probe_printer", _probe_returning("/ipp/print")):
        await refresh_printer_capabilities(printer)
    assert printer.ipp_path_detected == "/ipp/print"


@pytest.mark.asyncio
async def test_an_explicit_override_is_never_overwritten():
    """A Konica bizhub works on "/" while advertising "/ipp" — someone who set a
    path deliberately must not have it silently changed under them."""
    printer = _printer(ipp_path="/")
    with patch("app.printers.discovery.probe_printer", _probe_returning("/ipp/print")):
        await refresh_printer_capabilities(printer)
    assert printer.ipp_path == "/"
    assert printer.effective_ipp_path == "/"  # override still wins


def test_effective_path_prefers_the_override_then_the_cache():
    assert _printer(ipp_path="/a", ipp_path_detected="/b").effective_ipp_path == "/a"
    assert _printer(ipp_path=None, ipp_path_detected="/b").effective_ipp_path == "/b"
    assert _printer(ipp_path="", ipp_path_detected="/b").effective_ipp_path == "/b"
    assert _printer(ipp_path=None, ipp_path_detected=None).effective_ipp_path is None


@pytest.mark.asyncio
async def test_a_tls_upgrade_is_still_persisted():
    """Unrelated to the path, but the same "don't pay for this discovery twice"
    contract — guarded here because both live in the same branch."""
    printer = _printer(ipp_path=None, use_tls=False)
    with patch("app.printers.discovery.probe_printer", _probe_returning("/ipp/print", tls=True)):
        await refresh_printer_capabilities(printer)
    assert printer.use_tls is True
