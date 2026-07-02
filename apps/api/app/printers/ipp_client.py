"""Thin wrapper around pyipp for probing a printer's full IPP attribute set.

We use pyipp purely as an IPP transport (its higher-level `Printer` dataclass
only models monitoring fields like supply levels, not capability attributes),
and parse the raw response ourselves in `app/printers/capabilities.py`.
"""

from dataclasses import dataclass
from typing import Any

from pyipp import IPP
from pyipp.enums import IppOperation
from pyipp.exceptions import IPPError

# Real IPP Everywhere printers commonly respond at "/ipp/print" or "/".
# CUPS-backed queues instead require the queue name in the path
# ("/printers/<name>") — set Printer.ipp_path explicitly for those.
DEFAULT_CANDIDATE_PATHS = ["/ipp/print", "/", "/ipp/printer"]

DEFAULT_PORT = 631
DEFAULT_TIMEOUT_SECONDS = 5


class PrinterProbeError(Exception):
    """Raised when a printer could not be reached or queried over IPP."""


@dataclass
class ProbeResult:
    raw_attributes: dict[str, Any]
    resolved_path: str


async def probe_printer(
    ip_address: str,
    port: int = DEFAULT_PORT,
    tls: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ipp_path: str | None = None,
) -> ProbeResult:
    """Queries a printer's IPP endpoint for its full attribute set.

    Tries `ipp_path` if given, otherwise falls through `DEFAULT_CANDIDATE_PATHS`
    and returns the first one that responds.
    """
    candidate_paths = [ipp_path] if ipp_path else DEFAULT_CANDIDATE_PATHS
    last_error: Exception | None = None

    for path in candidate_paths:
        ipp = IPP(host=ip_address, port=port, base_path=path, tls=tls, request_timeout=timeout)
        try:
            response = await ipp.execute(
                IppOperation.GET_PRINTER_ATTRIBUTES,
                {"operation-attributes-tag": {"requested-attributes": ["all"]}},
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
