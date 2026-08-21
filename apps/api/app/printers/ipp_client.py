"""Thin wrapper around pyipp for probing a printer's full IPP attribute set.

We use pyipp purely as an IPP transport (its higher-level `Printer` dataclass
only models monitoring fields like supply levels, not capability attributes),
and parse the raw response ourselves in `app/printers/capabilities.py`.
"""

import ssl
from dataclasses import dataclass
from typing import Any

import aiohttp
from pyipp import IPP
from pyipp.enums import ATTRIBUTE_ENUM_MAP, IppOperation
from pyipp.exceptions import (
    IPPConnectionUpgradeRequired,
    IPPError,
    IPPVersionNotSupportedError,
)

from app.printers.capabilities import REQUESTED_ATTRIBUTES, _as_list, _scalar

# HTTP redirect statuses. 307/308 preserve the method and body, so aiohttp will
# happily re-POST an IPP request to wherever the device points — which is the
# whole problem this guards against (see _redirect_watching_session).
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class IPPTransportRedirect(Exception):
    """The device answered an IPP request with an HTTP redirect instead of
    serving it.

    Raised in place of transparently following that redirect, because CUPS's
    own `ipp` backend — the thing that actually delivers print jobs — does not
    follow redirects. It reads the non-IPP response as
    `server-error-service-unavailable` and, worse, retries with no backoff at
    all.

    Following it here would make PrintOps *more* permissive than the printing
    path it exists to monitor, which is exactly the failure it caused on the
    LCACTC Kyocera (2026-08-20). That device was switched to TLS-only IPP and
    began redirecting port 631 to https://…:443/. aiohttp followed the
    redirect, so the status poll kept reporting "online / Ready." for six
    hours while every print job failed and CUPS hammered the device at ~600
    connections a second trying again.

    A printer PrintOps cannot print to must not look healthy, however well it
    answers when you let the client chase it.
    """

    def __init__(self, status: int, location: str | None) -> None:
        self.status = status
        self.location = location
        super().__init__(f"HTTP {status} redirect to {location or 'an unspecified location'}")


def _redirect_watching_session() -> tuple[aiohttp.ClientSession, list[IPPTransportRedirect]]:
    """An aiohttp session that records any redirect it follows, plus the list
    it records into.

    aiohttp defaults to allow_redirects=True and pyipp never overrides it, so
    the session is injected into pyipp (IPP(session=...)) to observe that
    behaviour without patching the library. A TraceConfig is used rather than
    subclassing ClientSession, which aiohttp explicitly discourages.

    The redirect is still followed, and deliberately so: it costs one extra
    hop to the same device and in exchange we learn not just that the endpoint
    redirects but that the target genuinely speaks IPP — which is what makes
    the resulting message specific enough to act on.
    """
    seen: list[IPPTransportRedirect] = []

    async def _on_redirect(_session, _context, params) -> None:
        if params.response.status in _REDIRECT_STATUSES:
            seen.append(
                IPPTransportRedirect(
                    params.response.status, params.response.headers.get("Location")
                )
            )

    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(_on_redirect)
    return aiohttp.ClientSession(trace_configs=[trace]), seen


# pyipp maps several IPP attribute values to its own narrow IntEnums
# (finishings, orientation-requested, print-quality, printer-state,
# job-state, document-state...), each covering only a subset of what the
# relevant PWG/RFC registry actually defines. A device reporting a
# perfectly valid code pyipp's enum happens not to include crashes its
# parser (raises ValueError deep inside parse_response, swallowed into an
# opaque, message-less IPPParseError by pyipp.IPP.execute()) — an
# otherwise-successful response ends up looking like a total probe
# failure. Confirmed hit twice already across different vendors/attributes
# (Canon imageCLASS MF642C/643C/644C on orientation-requested-supported;
# Konica Minolta bizhub 750i on finishings-supported, job-offset=14 and
# the punch-dual-*/punch-triple-* codes) — this isn't a one-device fluke,
# it's a structural gap that will keep recurring across the vendor mix
# (Canon/HP/Kyocera/Lexmark/Konica Minolta) this app targets.
#
# capabilities.py never depends on getting pyipp's enum types back — every
# field goes through _scalar()/_as_list(), which already unwrap Enum
# members to their raw int (see FINISHINGS_MAP and friends, which key off
# plain ints and have an explicit fallback for unmapped codes). So pyipp's
# enum coercion buys us nothing and is actively harmful — disable it
# entirely rather than special-casing attributes one vendor crash at a
# time. Leave "status-code" mapped: it's part of pyipp's own internal
# response-status handling, not a capability attribute we parse.
for _key in list(ATTRIBUTE_ENUM_MAP):
    if _key != "status-code":
        ATTRIBUTE_ENUM_MAP.pop(_key, None)

# Real IPP Everywhere printers commonly respond at "/ipp/print" or "/".
# "/ipp" (no "/print") is another common default, seen on Kyocera and
# Lexmark lines. CUPS-backed queues instead require the queue name in the
# path ("/printers/<name>") — set Printer.ipp_path explicitly for those.
DEFAULT_CANDIDATE_PATHS = ["/ipp/print", "/", "/ipp/printer", "/ipp"]

DEFAULT_PORT = 631
DEFAULT_TIMEOUT_SECONDS = 5

# pyipp defaults to requesting IPP/2.0. Several older or budget devices
# (confirmed: HP LaserJet 4250, ~2004-era firmware, predates IPP/2.0
# entirely) reject that outright with IPPVersionNotSupportedError rather
# than negotiating down — retried per path, at 1.1, since a version
# mismatch is a per-request thing pyipp can't detect up front without
# asking. Not retried for other error types (connection refused, no
# printer returned, etc.) — those aren't version problems and 1.1 won't
# fix them, so we move on to the next candidate path instead.
IPP_VERSIONS: list[tuple[int, int]] = [(2, 0), (1, 1)]


class PrinterProbeError(Exception):
    """Raised when a printer could not be reached or queried over IPP.

    `redirect` carries the structured redirect when that was the cause, so
    callers can act on it rather than re-parsing the message. Discovery uses it
    to reconfigure the printer onto the address the device pointed at, which is
    the difference between "this printer is broken, go and work out why" and
    the system fixing itself."""

    def __init__(self, message: str, redirect: "IPPTransportRedirect | None" = None) -> None:
        self.redirect = redirect
        super().__init__(message)


@dataclass
class ProbeResult:
    raw_attributes: dict[str, Any]
    resolved_path: str
    # Whether the response actually came back over TLS. This can be True even
    # when the caller asked for tls=False, if the device demanded an upgrade
    # (see _get_printer_attributes) — callers persist it so later probes skip
    # the wasted cleartext attempt. Defaults to the conservative "no upgrade
    # happened", so a probe that never negotiated TLS can't be mistaken for
    # one that did.
    resolved_tls: bool = False


async def _execute(
    ip_address: str,
    port: int,
    path: str,
    tls: bool,
    timeout: int,
    version: tuple[int, int],
    requested_attributes: list[str],
) -> dict[str, Any] | None:
    """One Get-Printer-Attributes call. Returns the first printer's attribute
    dict, or None if the device answered but reported no printer at all."""
    # pyipp only closes a session it created itself, so this one is ours to
    # close — hence the nested try/finally rather than relying on ipp.close().
    session, redirects = _redirect_watching_session()
    ipp = IPP(
        host=ip_address,
        port=port,
        base_path=path,
        tls=tls,
        request_timeout=timeout,
        ipp_version=version,
        session=session,
    )
    try:
        response = await ipp.execute(
            IppOperation.GET_PRINTER_ATTRIBUTES,
            {"operation-attributes-tag": {"requested-attributes": requested_attributes}},
        )
        # Checked after a *successful* execute, not instead of one: a probe
        # that only works because aiohttp chased a redirect is precisely the
        # case that looks healthy here and fails in CUPS.
        if redirects:
            raise redirects[0]
        printers = response.get("printers") or []
        return printers[0] if printers else None
    finally:
        await ipp.close()
        await session.close()


def _tls_mismatch(exc: BaseException) -> ssl.SSLError | None:
    """The SSL error underneath a failed probe, if the failure was a TLS one.

    pyipp flattens every connection failure into the same
    `IPPConnectionError("Error occurred while communicating with IPP server.")`,
    which is indistinguishable from the printer being switched off. The useful
    detail is in the cause chain.
    """
    seen = 0
    cause: BaseException | None = exc
    while cause is not None and seen < 6:
        if isinstance(cause, ssl.SSLError):
            return cause
        cause = cause.__cause__ or cause.__context__
        seen += 1
    return None


def _tls_mismatch_message(ip_address: str, port: int, error: ssl.SSLError) -> str:
    """Turns an SSL failure into the specific thing to change.

    Split by kind, because the two have opposite fixes and telling someone to
    check the port when the real problem is a certificate wastes their time:
    a protocol mismatch means TLS is aimed at a cleartext port, while a
    verification failure means the right port with an unacceptable
    certificate."""
    if isinstance(error, ssl.SSLCertVerificationError):
        return (
            f"TLS to {ip_address}:{port} failed on certificate verification: {error}. "
            "The port is right but the certificate isn't acceptable."
        )
    return (
        f"TLS was requested but {ip_address}:{port} answered in cleartext ({error}). "
        "That port speaks plain IPP — a device set to require IPPS normally serves it "
        "on 443 instead. Turning on TLS without also changing the port cannot work."
    )


async def _get_printer_attributes(
    ip_address: str,
    port: int,
    tls: bool,
    timeout: int,
    ipp_path: str | None,
    requested_attributes: list[str],
) -> ProbeResult:
    """Shared candidate-path IPP Get-Printer-Attributes call, used by both the
    full capability probe and the lightweight state probe below. Tries
    `ipp_path` if given, otherwise falls through `DEFAULT_CANDIDATE_PATHS` and
    returns the first one that responds."""
    candidate_paths = [ipp_path] if ipp_path else DEFAULT_CANDIDATE_PATHS
    last_error: Exception | None = None
    redirect: IPPTransportRedirect | None = None
    tls_error: ssl.SSLError | None = None

    for path in candidate_paths:
        for version in IPP_VERSIONS:
            attempt_tls = tls
            try:
                try:
                    attributes = await _execute(
                        ip_address, port, path, tls, timeout, version, requested_attributes
                    )
                except IPPConnectionUpgradeRequired:
                    # The device answered plain IPP with HTTP 426 Upgrade
                    # Required (+ an "Upgrade: TLS/1.0, HTTP/1.1" header)
                    # instead of serving the request: it accepts IPP only over
                    # TLS on this same port. Confirmed live against an Epson
                    # ET-3950 Series, whose printer-uri-supported advertises
                    # both ipps:// and ipp:// on 631 but which 426s every
                    # cleartext request — so this is not something the caller
                    # can reliably know up front from the URI alone. CUPS'
                    # ipptool performs this upgrade transparently, pyipp does
                    # not, so an otherwise-healthy printer failed all four
                    # candidate paths and looked completely unreachable.
                    # Retry the same path/version over TLS rather than moving
                    # on: the device just told us exactly what it wants.
                    if tls:
                        raise  # already TLS — nothing left to upgrade to
                    attempt_tls = True
                    attributes = await _execute(
                        ip_address, port, path, True, timeout, version, requested_attributes
                    )
                if attributes is None:
                    last_error = PrinterProbeError(f"No printer attributes returned at {path}")
                    break  # not a version problem — try the next path instead
                return ProbeResult(
                    raw_attributes=attributes, resolved_path=path, resolved_tls=attempt_tls
                )
            except IPPTransportRedirect as exc:
                redirect = exc
                last_error = exc
                break  # inner loop; the outer one is broken out of below
            except IPPVersionNotSupportedError as exc:
                last_error = exc
                continue  # try the next IPP version at this same path
            except IPPError as exc:
                last_error = exc
                tls_error = tls_error or _tls_mismatch(exc)
                break  # not a version problem — try the next path instead

        if redirect is not None:
            # A redirect is a property of the endpoint, not the path — every
            # remaining candidate will redirect identically. Walking them all
            # would quadruple this printer's share of the 60s status poll, and
            # each attempt is itself two requests since the redirect is
            # followed.
            break

    if redirect is not None:
        # Deliberately specific: this is a configuration mismatch with exactly
        # one fix, and the generic "could not reach" message below sends
        # whoever reads it hunting for a network or power fault that isn't
        # there. Six hours were lost to that on 2026-08-20.
        raise PrinterProbeError(
            f"{ip_address}:{port} redirected the IPP request (HTTP "
            f"{redirect.status}) to {redirect.location or 'another address'}. "
            "CUPS does not follow redirects, so print jobs to this printer "
            "will fail even though the device is reachable — update this "
            "printer's port/TLS/IPP path to point at the redirect target.",
            redirect=redirect,
        )

    if tls_error is not None:
        # Ahead of the generic message below for the same reason as the
        # redirect: "could not reach" sends someone to check power and cabling
        # for what is a one-field configuration mismatch.
        raise PrinterProbeError(_tls_mismatch_message(ip_address, port, tls_error))

    raise PrinterProbeError(
        f"Could not reach an IPP printer at {ip_address}:{port} "
        f"(tried {candidate_paths}): {last_error}"
    )


async def probe_printer(
    ip_address: str,
    port: int = DEFAULT_PORT,
    tls: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ipp_path: str | None = None,
) -> ProbeResult:
    """Queries a printer's IPP endpoint for its full attribute set."""
    return await _get_printer_attributes(
        ip_address, port, tls, timeout, ipp_path, REQUESTED_ATTRIBUTES
    )


# Attributes for the lightweight status poll (app/printers/status.py) — kept
# separate from capabilities.REQUESTED_ATTRIBUTES since this runs on a 60s
# background loop against every printer and has no use for the (much larger,
# rarely-changing) capability set.
STATE_ATTRIBUTES: list[str] = [
    "printer-state",
    "printer-state-reasons",
    "printer-state-message",
]

STATE_TIMEOUT_SECONDS = 5


@dataclass
class PrinterStateResult:
    printer_state: int | None
    state_reasons: list[str]
    state_message: str | None


def _parse_state_message(raw: dict[str, Any]) -> str | None:
    """printer-state-message is spec'd (RFC 8011 §5.4.13) as a single
    text(255) value, but confirmed live: a real device reports it as a
    1setOf text instead — same "device doesn't actually follow the
    single-value spec" quirk as printer-firmware-string-version (see
    app/printers/capabilities.py:_parse_firmware_version). Blank entries
    (e.g. a device reporting ["", ""] — no real message) are dropped
    rather than joined into noise; a status_message the app then tries to
    write into a plain VARCHAR column crashes the whole status poll cycle
    for that printer if this isn't unwrapped to a string first."""
    values = [str(_scalar(v)) for v in _as_list(raw.get("printer-state-message"))]
    non_blank = [v for v in values if v.strip()]
    return ", ".join(non_blank) if non_blank else None


async def probe_printer_state(
    ip_address: str,
    port: int = DEFAULT_PORT,
    tls: bool = False,
    ipp_path: str | None = None,
    timeout: int = STATE_TIMEOUT_SECONDS,
) -> PrinterStateResult:
    """Lightweight counterpart to probe_printer(), fetching just the
    printer-state* attributes — see app/printers/status.py:derive_status for
    how these map to PrintOps's online/error/offline status."""
    result = await _get_printer_attributes(
        ip_address, port, tls, timeout, ipp_path, STATE_ATTRIBUTES
    )
    raw = result.raw_attributes
    reasons = [str(_scalar(v)) for v in _as_list(raw.get("printer-state-reasons"))]
    return PrinterStateResult(
        printer_state=_scalar(raw.get("printer-state")),
        state_reasons=reasons,
        state_message=_parse_state_message(raw),
    )
