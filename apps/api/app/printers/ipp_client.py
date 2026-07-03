"""Thin wrapper around pyipp for probing a printer's full IPP attribute set.

We use pyipp purely as an IPP transport (its higher-level `Printer` dataclass
only models monitoring fields like supply levels, not capability attributes),
and parse the raw response ourselves in `app/printers/capabilities.py`.
"""

from dataclasses import dataclass
from typing import Any

from pyipp import IPP
from pyipp.enums import ATTRIBUTE_ENUM_MAP, IppOperation
from pyipp.exceptions import IPPError

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


class PrinterProbeError(Exception):
    """Raised when a printer could not be reached or queried over IPP."""


@dataclass
class ProbeResult:
    raw_attributes: dict[str, Any]
    resolved_path: str


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
        ipp = IPP(host=ip_address, port=port, base_path=path, tls=tls, request_timeout=timeout)
        try:
            response = await ipp.execute(
                IppOperation.GET_PRINTER_ATTRIBUTES,
                {"operation-attributes-tag": {"requested-attributes": requested_attributes}},
            )
            printers = response.get("printers") or []
            if not printers:
                last_error = PrinterProbeError(f"No printer attributes returned at {path}")
                continue
            return ProbeResult(raw_attributes=printers[0], resolved_path=path)
        except IPPError as exc:
            last_error = exc
            continue
        finally:
            await ipp.close()

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
        state_message=raw.get("printer-state-message") or None,
    )
