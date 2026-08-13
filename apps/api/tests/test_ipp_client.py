from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyipp.exceptions import (
    IPPConnectionUpgradeRequired,
    IPPError,
    IPPVersionNotSupportedError,
)

from app.printers.ipp_client import (
    DEFAULT_CANDIDATE_PATHS,
    IPP_VERSIONS,
    PrinterProbeError,
    _get_printer_attributes,
)


def _make_ipp_factory(side_effects):
    """Builds a fake IPP() constructor — each call returns a fresh mock
    instance whose execute() raises/returns the next entry in
    `side_effects`, consumed in the exact order _get_printer_attributes
    constructs IPP() instances (one per (path, version) attempt).

    Uses return_value (not side_effect) for non-exception entries — Mock
    treats a dict passed to side_effect as an iterable of successive
    return values (iterating its keys), not a single return value, which
    would silently hand back "printers" the string instead of the dict."""
    calls = []
    iterator = iter(side_effects)

    def factory(**kwargs):
        instance = MagicMock()
        entry = next(iterator)
        if isinstance(entry, Exception):
            instance.execute = AsyncMock(side_effect=entry)
        else:
            instance.execute = AsyncMock(return_value=entry)
        instance.close = AsyncMock()
        calls.append(kwargs)
        return instance

    return factory, calls


async def test_falls_back_to_ipp_1_1_after_version_rejection():
    """Confirmed live against a real HP LaserJet 4250: it rejects IPP/2.0
    outright with IPPVersionNotSupportedError, but works fine at 1.1."""
    responses = [
        IPPVersionNotSupportedError("nope"),  # first path @ 2.0
        {"printers": [{"printer-state": 3}]},  # same path @ 1.1 succeeds
    ]
    factory, calls = _make_ipp_factory(responses)

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        result = await _get_printer_attributes("10.10.2.88", 631, False, 5, None, ["printer-state"])

    assert result.raw_attributes == {"printer-state": 3}
    assert result.resolved_path == DEFAULT_CANDIDATE_PATHS[0]
    assert len(calls) == 2
    assert calls[0]["base_path"] == calls[1]["base_path"] == DEFAULT_CANDIDATE_PATHS[0]
    assert calls[0]["ipp_version"] == (2, 0)
    assert calls[1]["ipp_version"] == (1, 1)


async def test_non_version_error_moves_to_next_path_not_next_version():
    """A connection-refused/parse error isn't a version problem — retrying
    the same path at 1.1 wouldn't help, so it should skip straight to the
    next candidate path instead."""
    responses = [
        IPPError("connection refused"),  # first path @ 2.0
        {"printers": [{"printer-state": 3}]},  # second path @ 2.0 succeeds
    ]
    factory, calls = _make_ipp_factory(responses)

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        result = await _get_printer_attributes("10.0.0.1", 631, False, 5, None, ["printer-state"])

    assert result.resolved_path == DEFAULT_CANDIDATE_PATHS[1]
    assert len(calls) == 2  # did not retry the first path at 1.1
    assert calls[0]["base_path"] == DEFAULT_CANDIDATE_PATHS[0]
    assert calls[1]["base_path"] == DEFAULT_CANDIDATE_PATHS[1]
    assert calls[1]["ipp_version"] == (2, 0)


async def test_all_paths_and_versions_failing_raises_clear_error():
    attempts = len(DEFAULT_CANDIDATE_PATHS) * len(IPP_VERSIONS)
    responses = [IPPVersionNotSupportedError("nope")] * attempts
    factory, calls = _make_ipp_factory(responses)

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        with pytest.raises(PrinterProbeError, match="Could not reach an IPP printer"):
            await _get_printer_attributes("10.0.0.1", 631, False, 5, None, ["printer-state"])

    assert len(calls) == attempts


async def test_upgrade_required_retries_same_path_over_tls():
    """Confirmed live against a real Epson ET-3950 Series: it answers every
    cleartext IPP request on 631 with HTTP 426 Upgrade Required, and serves
    the identical request fine over TLS on that same port."""
    responses = [
        IPPConnectionUpgradeRequired("upgrade"),  # first path @ 2.0, cleartext
        {"printers": [{"printer-state": 3}]},  # same path @ 2.0, over TLS
    ]
    factory, calls = _make_ipp_factory(responses)

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        result = await _get_printer_attributes("10.10.2.86", 631, False, 5, None, ["printer-state"])

    assert result.raw_attributes == {"printer-state": 3}
    assert result.resolved_path == DEFAULT_CANDIDATE_PATHS[0]
    assert result.resolved_tls is True
    assert len(calls) == 2  # did not fall through to the next path
    assert calls[0]["base_path"] == calls[1]["base_path"] == DEFAULT_CANDIDATE_PATHS[0]
    assert calls[0]["ipp_version"] == calls[1]["ipp_version"] == (2, 0)
    assert calls[0]["tls"] is False
    assert calls[1]["tls"] is True


async def test_successful_cleartext_probe_reports_resolved_tls_false():
    """The upgrade retry must not make every probe look like a TLS one —
    discovery persists resolved_tls onto Printer.use_tls."""
    factory, calls = _make_ipp_factory([{"printers": [{"printer-state": 3}]}])

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        result = await _get_printer_attributes("10.0.0.1", 631, False, 5, None, ["printer-state"])

    assert result.resolved_tls is False
    assert len(calls) == 1


async def test_upgrade_required_over_tls_moves_to_next_path():
    """A device already being probed over TLS that still demands an upgrade
    has nothing left to upgrade to — treat it like any other IPP error and
    move on rather than retrying the same path forever."""
    responses = [
        IPPConnectionUpgradeRequired("upgrade"),  # first path, already TLS
        {"printers": [{"printer-state": 3}]},  # second path succeeds
    ]
    factory, calls = _make_ipp_factory(responses)

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        result = await _get_printer_attributes("10.0.0.1", 631, True, 5, None, ["printer-state"])

    assert result.resolved_path == DEFAULT_CANDIDATE_PATHS[1]
    assert result.resolved_tls is True
    assert len(calls) == 2
    assert calls[0]["tls"] is calls[1]["tls"] is True


async def test_explicit_ipp_path_still_gets_version_fallback():
    """A printer with Printer.ipp_path already set (skips candidate-path
    probing) should still get the 1.1 retry — the fallback isn't tied to
    the default-path-discovery flow."""
    responses = [
        IPPVersionNotSupportedError("nope"),
        {"printers": [{"printer-state": 3}]},
    ]
    factory, calls = _make_ipp_factory(responses)

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        result = await _get_printer_attributes(
            "10.10.2.88", 631, False, 5, "/printers/queue-name", ["printer-state"]
        )

    assert result.resolved_path == "/printers/queue-name"
    assert len(calls) == 2
    assert calls[0]["base_path"] == calls[1]["base_path"] == "/printers/queue-name"
