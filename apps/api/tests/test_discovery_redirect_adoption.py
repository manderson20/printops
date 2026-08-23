"""Adding a printer should mean typing an IP, not knowing its IPP path, port
and scheme.

A device switched to TLS-only IPP answers its cleartext port with a redirect
naming exactly where it now lives. That is the device stating its own
configuration — better than anyone guessing. On the LCACTC Kyocera (2026-08-20)
the 307 pointed at https://10.50.1.37:443/, precisely the settings someone
otherwise had to work out by hand.

The redirect is adopted only after the new address has answered: CUPS cannot
follow redirects, so believing one without verifying would just move the
printer to a second address that also doesn't print.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.printer import Printer
from app.printers.discovery import _transport_from_redirect, refresh_printer_capabilities
from app.printers.ipp_client import IPPTransportRedirect, PrinterProbeError, ProbeResult


def _printer(**kwargs):
    kwargs.setdefault("port", 631)
    kwargs.setdefault("use_tls", False)
    kwargs.setdefault("ipp_path", None)
    return Printer(name="GA Kyocera", ip_address="10.50.1.37", **kwargs)


def _redirect_error(location, status=307):
    return PrinterProbeError("redirected", redirect=IPPTransportRedirect(status, location))


def _ok(path="/ipp/print"):
    return ProbeResult(
        raw_attributes={"printer-make-and-model": "ECOSYS P8060cdn"},
        resolved_path=path,
        resolved_tls=False,
    )


# ---- parsing the target ----


@pytest.mark.parametrize(
    "location,expected",
    [
        ("https://10.50.1.37:443/", (443, True, "/")),
        ("https://10.50.1.37/", (443, True, "/")),  # port implied by scheme
        ("ipps://10.50.1.37:443/ipp/print", (443, True, "/ipp/print")),
        ("ipp://10.50.1.37:631/ipp/print", (631, False, "/ipp/print")),
        ("http://10.50.1.37/ipp", (80, False, "/ipp")),
    ],
)
def test_usable_redirect_targets_are_parsed(location, expected):
    assert _transport_from_redirect(location) == expected


@pytest.mark.parametrize(
    "location",
    [None, "", "not-a-url", "gopher://10.50.1.37/", "https://", "https://host:notaport/"],
    ids=["none", "empty", "garbage", "wrong-scheme", "no-host", "bad-port"],
)
def test_unusable_targets_are_refused_rather_than_guessed(location):
    """A wrong reconfiguration is worse than none — it moves a working printer
    to an address nobody chose."""
    assert _transport_from_redirect(location) is None


# ---- adopting it ----


@pytest.mark.asyncio
async def test_a_redirect_reconfigures_the_printer_onto_the_new_address():
    printer = _printer()
    probe = AsyncMock(side_effect=[_redirect_error("https://10.50.1.37:443/"), _ok()])

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert (printer.port, printer.use_tls) == (443, True)
    assert printer.ipp_path_detected == "/ipp/print"
    assert printer.effective_ipp_path == "/ipp/print"
    assert printer.capabilities_error is None


@pytest.mark.asyncio
async def test_the_new_address_must_answer_before_it_is_adopted():
    """If the redirect target fails too, the printer keeps its original settings
    and reports an error — rather than being quietly moved somewhere that also
    doesn't work."""
    printer = _printer()
    probe = AsyncMock(
        side_effect=[
            _redirect_error("https://10.50.1.37:443/"),
            PrinterProbeError("nothing there either"),
        ]
    )

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert (printer.port, printer.use_tls) == (631, False)
    assert "nothing there either" in printer.capabilities_error


@pytest.mark.asyncio
async def test_a_redirect_to_the_very_same_address_is_not_retried():
    """Guards against a redirect loop: retrying an address identical in port,
    scheme *and* path would just redirect again."""
    printer = _printer(port=443, use_tls=True, ipp_path="/other")
    probe = AsyncMock(side_effect=[_redirect_error("https://10.50.1.37:443/other")])

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert probe.await_count == 1
    assert printer.capabilities_error is not None


@pytest.mark.asyncio
async def test_a_redirect_that_changes_only_the_path_is_followed():
    """The path is part of the address. A device that keeps its port and
    scheme but names a different resource path is pointing somewhere new, and
    comparing port and TLS alone read that as "the identical address" and gave
    up on a printer that would have answered."""
    printer = _printer(port=631, use_tls=False, ipp_path=None)
    probe = AsyncMock(
        side_effect=[_redirect_error("ipp://10.50.1.37:631/ipp/print"), _ok("/ipp/print")]
    )

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert probe.await_count == 2
    assert probe.await_args.kwargs["ipp_path"] == "/ipp/print"
    assert probe.await_args.kwargs["port"] == 631
    assert printer.capabilities_error is None
    assert printer.ipp_path_detected == "/ipp/print"


@pytest.mark.asyncio
async def test_a_non_redirect_failure_is_reported_unchanged():
    """An ordinary offline printer must not be reconfigured."""
    printer = _printer()
    probe = AsyncMock(side_effect=PrinterProbeError("Could not reach an IPP printer"))

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert (printer.port, printer.use_tls, printer.effective_ipp_path) == (631, False, None)
    assert "Could not reach" in printer.capabilities_error


@pytest.mark.asyncio
async def test_an_explicit_path_survives_a_transport_change():
    """Moving to TLS on another port says nothing about the path — a Konica on
    "/" that gained TLS must not be silently moved to /ipp/print."""
    printer = _printer(ipp_path="/")
    probe = AsyncMock(side_effect=[_redirect_error("https://10.50.1.37:443/"), _ok(path="/")])

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert printer.ipp_path == "/"  # the override survives the transport change
    assert printer.effective_ipp_path == "/"
    assert (printer.port, printer.use_tls) == (443, True)


# ---- a redirect that names a different machine ----


@pytest.mark.asyncio
async def test_a_redirect_to_another_host_is_recorded_not_adopted():
    """Port and scheme are how a printer is reached; the host is which printer
    it is. A device does not get to reassign PrintOps to a different box — it
    could be a misconfigured unit or a recycled DHCP address, and documents
    would come out somewhere nobody chose while everything looked healthy."""
    printer = _printer(port=631, use_tls=False)
    probe = AsyncMock(side_effect=[_redirect_error("https://10.50.1.99:443/ipp/print")])

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert probe.await_count == 1, "the new host must not be probed behind an admin's back"
    assert printer.ip_address == "10.50.1.37", "the printer has not been moved"
    assert printer.pending_redirect["host"] == "10.50.1.99"
    assert printer.pending_redirect["port"] == 443
    assert printer.pending_redirect["tls"] is True
    assert printer.pending_redirect["path"] == "/ipp/print"
    # The probe still failed, because it did: nothing about this printer's
    # state is invented to make the suggestion look tidy.
    assert printer.capabilities_error is not None


@pytest.mark.asyncio
async def test_a_redirect_to_the_same_host_is_still_adopted_outright():
    """The ordinary TLS-only case. Same machine, different port — routing, not
    identity, and it needs no one's permission."""
    printer = _printer(port=631, use_tls=False)
    probe = AsyncMock(
        side_effect=[_redirect_error("https://10.50.1.37:443/ipp/print"), _ok("/ipp/print")]
    )

    with patch("app.printers.discovery.probe_printer", probe):
        await refresh_printer_capabilities(printer)

    assert (printer.port, printer.use_tls) == (443, True)
    assert printer.pending_redirect is None
