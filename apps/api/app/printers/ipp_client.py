"""Thin wrapper around pyipp for probing a printer's full IPP attribute set.

We use pyipp purely as an IPP transport (its higher-level `Printer` dataclass
only models monitoring fields like supply levels, not capability attributes),
and parse the raw response ourselves in `app/printers/capabilities.py`.
"""

from dataclasses import dataclass
from typing import Any

from pyipp import IPP
from pyipp.enums import ATTRIBUTE_ENUM_MAP, IppOperation
from pyipp.exceptions import (
    IPPConnectionUpgradeRequired,
    IPPError,
    IPPVersionNotSupportedError,
)

from app.printers.capabilities import REQUESTED_ATTRIBUTES, _as_list, _scalar

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
    """Raised when a printer could not be reached or queried over IPP."""


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
    ipp = IPP(
        host=ip_address,
        port=port,
        base_path=path,
        tls=tls,
        request_timeout=timeout,
        ipp_version=version,
    )
    try:
        response = await ipp.execute(
            IppOperation.GET_PRINTER_ATTRIBUTES,
            {"operation-attributes-tag": {"requested-attributes": requested_attributes}},
        )
        printers = response.get("printers") or []
        return printers[0] if printers else None
    finally:
        await ipp.close()


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
            except IPPVersionNotSupportedError as exc:
                last_error = exc
                continue  # try the next IPP version at this same path
            except IPPError as exc:
                last_error = exc
                break  # not a version problem — try the next path instead

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
