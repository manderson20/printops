import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyipp.exceptions import (
    IPPConnectionError,
    IPPConnectionUpgradeRequired,
    IPPError,
    IPPVersionNotSupportedError,
)

from app.printers.ipp_client import (
    DEFAULT_CANDIDATE_PATHS,
    IPP_VERSIONS,
    IPPTransportRedirect,
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


# --- Transport-redirect detection -------------------------------------------
#
# A device that answers plain IPP with an HTTP redirect is reachable, and
# aiohttp will happily chase the redirect and return a valid response — so the
# probe succeeds and the printer looks healthy. CUPS' `ipp` backend does not
# follow redirects, so print jobs to that same printer fail outright.
#
# That gap is what made the LCACTC Kyocera outage (2026-08-20) take six hours
# to find: the dashboard read "online / Ready." the entire time. These tests
# pin the rule that PrintOps must not be more permissive than the printing
# path it reports on.


def _session_reporting(redirects):
    """Patches _redirect_watching_session to hand back a throwaway session and
    a pre-populated redirect list, standing in for aiohttp's trace hook having
    observed those redirects during the request."""
    session = MagicMock()
    session.close = AsyncMock()
    return lambda: (session, list(redirects)), session


async def test_followed_redirect_fails_the_probe_even_though_the_device_answered():
    """The whole point: execute() succeeded (aiohttp chased the redirect and
    got real IPP back), and the probe must still fail."""
    factory, _calls = _make_ipp_factory([{"printers": [{"printer-state": 3}]}])
    maker, session = _session_reporting([IPPTransportRedirect(307, "https://10.50.1.37:443/")])

    with (
        patch("app.printers.ipp_client.IPP", side_effect=factory),
        patch("app.printers.ipp_client._redirect_watching_session", maker),
    ):
        with pytest.raises(PrinterProbeError) as exc:
            await _get_printer_attributes("10.50.1.37", 631, False, 5, "/", ["printer-state"])

    message = str(exc.value)
    # Must name the target, or whoever reads it cannot act on it...
    assert "https://10.50.1.37:443/" in message
    assert "307" in message
    # ...and must say why a reachable printer is being called broken, rather
    # than sending someone to check power and cabling.
    assert "CUPS does not follow redirects" in message
    session.close.assert_awaited()


async def test_redirect_short_circuits_remaining_candidate_paths():
    """Every path on a redirecting endpoint redirects identically, so walking
    the rest is pure latency on the 60s status poll — one IPP() construction,
    not four."""
    factory, calls = _make_ipp_factory(
        [{"printers": [{"printer-state": 3}]}] * (len(DEFAULT_CANDIDATE_PATHS) * len(IPP_VERSIONS))
    )
    maker, _session = _session_reporting([IPPTransportRedirect(308, "https://host/ipp/print")])

    with (
        patch("app.printers.ipp_client.IPP", side_effect=factory),
        patch("app.printers.ipp_client._redirect_watching_session", maker),
    ):
        with pytest.raises(PrinterProbeError):
            await _get_printer_attributes("10.50.1.37", 631, False, 5, None, ["printer-state"])

    assert len(calls) == 1


async def test_redirect_with_no_location_header_still_reports_actionably():
    """A redirect without a Location header is malformed, but it is still the
    reason printing is failing — don't degrade to the generic 'could not
    reach' message that sends people hunting for a network fault."""
    factory, _calls = _make_ipp_factory([{"printers": [{"printer-state": 3}]}])
    maker, _session = _session_reporting([IPPTransportRedirect(302, None)])

    with (
        patch("app.printers.ipp_client.IPP", side_effect=factory),
        patch("app.printers.ipp_client._redirect_watching_session", maker),
    ):
        with pytest.raises(PrinterProbeError) as exc:
            await _get_printer_attributes("10.50.1.37", 631, False, 5, "/", ["printer-state"])

    assert "CUPS does not follow redirects" in str(exc.value)


async def test_no_redirect_leaves_a_healthy_probe_untouched():
    """The overwhelmingly common case must be completely unaffected."""
    factory, _calls = _make_ipp_factory([{"printers": [{"printer-state": 3}]}])
    maker, session = _session_reporting([])

    with (
        patch("app.printers.ipp_client.IPP", side_effect=factory),
        patch("app.printers.ipp_client._redirect_watching_session", maker),
    ):
        result = await _get_printer_attributes(
            "10.30.2.206", 631, False, 5, "/ipp/print", ["printer-state"]
        )

    assert result.raw_attributes == {"printer-state": 3}
    session.close.assert_awaited()


# --- TLS aimed at a cleartext port -------------------------------------------
#
# pyipp flattens every connection failure into the same
# IPPConnectionError("Error occurred while communicating with IPP server."),
# which reads identically to the printer being switched off. Enabling TLS on a
# printer still configured for port 631 produces exactly that, and it is the
# obvious thing to try when a device starts demanding IPPS — so the message has
# to name the port rather than send someone to check the power cable.


def _connection_error_caused_by(ssl_exc, depth=1):
    """An IPPConnectionError with an SSL error buried `depth` levels down its
    cause chain.

    Live, the chain is IPPConnectionError -> aiohttp.ClientConnectorSSLError ->
    ssl.SSLError. The aiohttp wrapper is stood in for by a plain exception: it
    cannot be constructed without a real connection key, and the detection walks
    the chain by looking for ssl.SSLError rather than by matching the wrapper,
    so what sits in between is exactly what must not matter."""
    current = ssl_exc
    for level in range(depth):
        wrapper = OSError(f"Cannot connect to host (wrapper {level})")
        wrapper.__cause__ = current
        current = wrapper
    outer = IPPConnectionError("Error occurred while communicating with IPP server.")
    outer.__cause__ = current
    return outer


async def test_tls_against_a_cleartext_port_names_the_port():
    """The live signature from the Kyocera: SSL WRONG_VERSION_NUMBER."""
    error = ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
    factory, _calls = _make_ipp_factory([_connection_error_caused_by(error)] * len(IPP_VERSIONS))

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        with pytest.raises(PrinterProbeError) as exc:
            await _get_printer_attributes("10.50.1.37", 631, True, 5, "/", ["printer-state"])

    message = str(exc.value)
    assert "631" in message
    assert "cleartext" in message
    assert "443" in message  # where IPPS normally lives, i.e. what to change it to
    # Must not degrade into the generic unreachable message.
    assert "Could not reach an IPP printer" not in message


async def test_a_certificate_problem_is_not_reported_as_a_port_problem():
    """Opposite fix: the port is right and the certificate is the issue.
    Telling someone to change the port here would waste their time."""
    error = ssl.SSLCertVerificationError("certificate verify failed: self signed certificate")
    factory, _calls = _make_ipp_factory([_connection_error_caused_by(error)] * len(IPP_VERSIONS))

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        with pytest.raises(PrinterProbeError) as exc:
            await _get_printer_attributes(
                "10.50.1.37", 443, True, 5, "/ipp/print", ["printer-state"]
            )

    message = str(exc.value)
    assert "certificate" in message
    assert "cleartext" not in message


async def test_an_ordinary_unreachable_printer_still_reports_generically():
    """A printer that is simply switched off must not be mislabelled as a TLS
    misconfiguration — there is no SSL error in its cause chain."""
    factory, _calls = _make_ipp_factory(
        [IPPConnectionError("Error occurred while communicating with IPP server.")]
        * (len(DEFAULT_CANDIDATE_PATHS) * len(IPP_VERSIONS))
    )

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        with pytest.raises(PrinterProbeError) as exc:
            await _get_printer_attributes("10.20.3.104", 631, False, 5, None, ["printer-state"])

    assert "Could not reach an IPP printer" in str(exc.value)
    assert "cleartext" not in str(exc.value)


async def test_the_ssl_cause_is_found_however_deeply_it_is_wrapped():
    """The library stack between pyipp and the socket is not ours to depend on;
    a future aiohttp adding another wrapper layer must not silently turn this
    back into the useless generic message."""
    error = ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
    factory, _calls = _make_ipp_factory(
        [_connection_error_caused_by(error, depth=4)] * len(IPP_VERSIONS)
    )

    with patch("app.printers.ipp_client.IPP", side_effect=factory):
        with pytest.raises(PrinterProbeError) as exc:
            await _get_printer_attributes("10.50.1.37", 631, True, 5, "/", ["printer-state"])

    assert "cleartext" in str(exc.value)
