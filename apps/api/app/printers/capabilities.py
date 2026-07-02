"""Maps raw IPP Get-Printer-Attributes output to PrintOps's capability schema.

Nothing here is hardcoded per printer make/model — every field is derived from
whatever the specific device reports, per ARCHITECTURE.md §5 ("Capability
Detection"). A printer that doesn't report an attribute simply gets `False`/
`None`/an empty list for the corresponding field, rather than a guess.
"""

from datetime import datetime
from enum import Enum
from typing import Any

# The specific attributes parse_capabilities() below actually reads. Requesting
# this curated list (instead of the IPP "all" shorthand) is a deliberate
# workaround for a pyipp parser bug: some devices (confirmed on a Canon
# imageCLASS MF642C/643C/644C) report enum values — e.g.
# orientation-requested-supported=7 — that pyipp's parser doesn't recognize
# and crashes on (ValueError -> IPPParseError) when attempting to decode
# *any* attribute in the "all" response. None of the attributes that trigger
# this are ones we use, so requesting only what we need avoids the crash
# entirely rather than working around it after the fact.
REQUESTED_ATTRIBUTES: list[str] = [
    "printer-make-and-model",
    "printer-firmware-string-version",
    "sides-supported",
    "color-supported",
    "print-color-mode-supported",
    "copies-supported",
    "printer-resolution-supported",
    "media-supported",
    "media-source-supported",
    "media-type-supported",
    "output-bin-supported",
    "finishings-supported",
    "job-password-supported",
    "job-account-id-supported",
    "job-accounting-user-id-supported",
    "multiple-document-handling-supported",
]

# IPP "finishings" enum values, per the PWG5100.1 Finishings registry.
# Codes not in this table still surface (as "finishing-<code>") instead of
# being silently dropped, in case a device reports something we haven't
# mapped yet.
FINISHINGS_MAP: dict[int, str] = {
    3: "none",
    4: "staple",
    5: "punch",
    6: "cover",
    7: "bind",
    8: "saddle-stitch",
    9: "edge-stitch",
    10: "fold",
    11: "trim",
    12: "bale",
    13: "booklet-maker",
    14: "job-offset",
    20: "staple-top-left",
    21: "staple-bottom-left",
    22: "staple-top-right",
    23: "staple-bottom-right",
    24: "edge-stitch-left",
    25: "edge-stitch-top",
    26: "edge-stitch-right",
    27: "edge-stitch-bottom",
    28: "staple-dual-left",
    29: "staple-dual-top",
    30: "staple-dual-right",
    31: "staple-dual-bottom",
    50: "bind-left",
    51: "bind-top",
    52: "bind-right",
    53: "bind-bottom",
    60: "trim-after-pages",
    61: "trim-after-documents",
    62: "trim-after-copies",
    63: "trim-after-job",
    70: "punch-top-left",
    71: "punch-bottom-left",
    72: "punch-top-right",
    73: "punch-bottom-right",
    74: "punch-dual-left",
    75: "punch-dual-top",
    76: "punch-dual-right",
    77: "punch-dual-bottom",
    78: "punch-triple-left",
    79: "punch-triple-top",
    80: "punch-triple-right",
    81: "punch-triple-bottom",
    82: "punch-quad-left",
    83: "punch-quad-top",
    84: "punch-quad-right",
    85: "punch-quad-bottom",
    86: "punch-multiple",
}


def _scalar(value: Any) -> Any:
    """Unwraps an Enum to its underlying value; passes everything else through."""
    return value.value if isinstance(value, Enum) else value


def _as_list(value: Any) -> list[Any]:
    """IPP attributes with a single value often come back bare, not in a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _json_safe(value: Any) -> Any:
    """Recursively converts a raw pyipp attributes dict into JSON-serializable data."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def sanitize_raw_attributes(raw: dict[str, Any]) -> dict[str, Any]:
    """Prepares a raw pyipp attributes dict for storage in a JSON column."""
    return _json_safe(raw)


def _parse_resolutions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    resolutions = []
    for entry in _as_list(raw.get("printer-resolution-supported")):
        if isinstance(entry, (tuple, list)) and len(entry) >= 2:
            x, y = entry[0], entry[1]
            unit = _scalar(entry[2]) if len(entry) > 2 else None
            resolutions.append({"x": x, "y": y, "unit": unit})
    return resolutions


def _parse_copies_max(raw: dict[str, Any]) -> int | None:
    value = raw.get("copies-supported")
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        values = [v for v in value if isinstance(v, int)]
        return max(values) if values else None
    if isinstance(value, int):
        return value
    return None


def _parse_finishings(raw: dict[str, Any]) -> list[str]:
    codes = [_scalar(v) for v in _as_list(raw.get("finishings-supported"))]
    labels = [FINISHINGS_MAP.get(code, f"finishing-{code}") for code in codes]
    return [label for label in labels if label != "none"]


def _parse_color_supported(raw: dict[str, Any]) -> bool:
    if raw.get("color-supported") is True:
        return True
    color_modes = [_scalar(v) for v in _as_list(raw.get("print-color-mode-supported"))]
    return "color" in color_modes


def _parse_duplex_supported(raw: dict[str, Any]) -> bool:
    sides = [_scalar(v) for v in _as_list(raw.get("sides-supported"))]
    return any(str(s).startswith("two-sided") for s in sides)


def _parse_collation_supported(raw: dict[str, Any]) -> bool:
    modes = [_scalar(v) for v in _as_list(raw.get("multiple-document-handling-supported"))]
    return "separate-documents-collated-copies" in modes


def _parse_pin_printing_supported(raw: dict[str, Any]) -> bool:
    value = raw.get("job-password-supported")
    return bool(value) and (not isinstance(value, int) or value > 0)


def _parse_accounting_supported(raw: dict[str, Any]) -> bool:
    return bool(raw.get("job-account-id-supported")) or bool(
        raw.get("job-accounting-user-id-supported")
    )


def parse_capabilities(raw: dict[str, Any]) -> dict[str, Any]:
    """Maps a raw IPP Get-Printer-Attributes dict to PrintOps's capability schema."""
    return {
        "make_model": raw.get("printer-make-and-model"),
        "firmware_version": raw.get("printer-firmware-string-version"),
        "duplex_supported": _parse_duplex_supported(raw),
        "color_supported": _parse_color_supported(raw),
        "copies_max": _parse_copies_max(raw),
        "resolutions": _parse_resolutions(raw),
        "media_sizes": [_scalar(v) for v in _as_list(raw.get("media-supported"))],
        "media_sources": [_scalar(v) for v in _as_list(raw.get("media-source-supported"))],
        "media_types": [_scalar(v) for v in _as_list(raw.get("media-type-supported"))],
        "output_bins": [_scalar(v) for v in _as_list(raw.get("output-bin-supported"))],
        "finishings": _parse_finishings(raw),
        "collation_supported": _parse_collation_supported(raw),
        "pin_printing_supported": _parse_pin_printing_supported(raw),
        "accounting_supported": _parse_accounting_supported(raw),
    }
