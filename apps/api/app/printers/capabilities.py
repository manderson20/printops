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
# this curated list (instead of the IPP "all" shorthand) keeps responses
# small and avoids attributes we have no use for — it is NOT what protects
# against pyipp's enum-parsing crash on vendor-specific codes (multiple
# vendors have hit that; see app/printers/ipp_client.py's
# ATTRIBUTE_ENUM_MAP patch for the actual, general fix).
REQUESTED_ATTRIBUTES: list[str] = [
    "printer-make-and-model",
    "printer-firmware-string-version",
    "sides-supported",
    "color-supported",
    "print-color-mode-supported",
    "copies-supported",
    "printer-resolution-supported",
    "media-supported",
    "media-default",
    "media-source-supported",
    "media-type-supported",
    # 1setOf collection, one entry per tray CURRENTLY loaded with media —
    # the only way to see a copier/MFP's actual per-tray loadout right now.
    # Deliberately media-col-ready, not media-col-database: confirmed live
    # against a Konica Minolta bizhub 750i that media-col-database instead
    # returns the device's full size x source CAPABILITY matrix (every
    # size crossed with every tray that could theoretically hold it — 89
    # entries, mostly duplicates), which is useless for "what's loaded
    # right now" and would have made every copier look like it has dozens
    # of trays.
    "media-col-ready",
    "output-bin-supported",
    "finishings-supported",
    "job-password-supported",
    "job-account-id-supported",
    "job-accounting-user-id-supported",
    "multiple-document-handling-supported",
    "document-format-supported",
    # RFC 8011's signal for IPPS/TLS support — a 1setOf keyword parallel to
    # printer-uri-supported, values like "none"/"tls". Purely advertised,
    # not a live connection test (see _parse_tls_supported below).
    "uri-security-supported",
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


def _parse_firmware_version(raw: dict[str, Any]) -> str | None:
    """Most devices report a single string, but printer-firmware-string-
    version is spec'd as 1setOf text(127) — confirmed on a real Kyocera
    ECOSYS P8060cdn, which reports one value per firmware component
    (engine, network card, fax, ...) instead of one overall version.
    Joined into a single readable string (not just the first value) so
    CapabilitiesOut.firmware_version stays a plain string without losing
    the extra component versions."""
    values = [str(_scalar(v)) for v in _as_list(raw.get("printer-firmware-string-version"))]
    return ", ".join(values) if values else None


def _parse_pin_printing_supported(raw: dict[str, Any]) -> bool:
    value = raw.get("job-password-supported")
    return bool(value) and (not isinstance(value, int) or value > 0)


def _parse_accounting_supported(raw: dict[str, Any]) -> bool:
    return bool(raw.get("job-account-id-supported")) or bool(
        raw.get("job-accounting-user-id-supported")
    )


def _parse_tls_supported(raw: dict[str, Any]) -> bool:
    """Whether the device *advertises* IPPS support (RFC 8011's
    uri-security-supported includes "tls") — not a live connection test,
    same "trust what the device reports, don't guess" convention as every
    other field here. A device that doesn't report this attribute at all
    gets False, same as an unset color-supported/duplex-supported."""
    values = [_scalar(v) for v in _as_list(raw.get("uri-security-supported"))]
    return "tls" in values


# PWG5100.3's media-size collection reports dimensions in hundredths of a
# millimeter (e.g. Letter's 8.5in width is 21590) — this app displays
# everything in inches, matching the "na_letter_8.5x11in"-style names
# media-supported/media-default already use.
HUNDREDTHS_MM_PER_INCH = 2540


def _media_col_dimensions_in(entry: dict[str, Any]) -> tuple[float | None, float | None]:
    media_size = entry.get("media-size")
    if not isinstance(media_size, dict):
        return None, None
    x = media_size.get("x-dimension")
    y = media_size.get("y-dimension")
    width_in = round(x / HUNDREDTHS_MM_PER_INCH, 2) if isinstance(x, (int, float)) else None
    height_in = round(y / HUNDREDTHS_MM_PER_INCH, 2) if isinstance(y, (int, float)) else None
    return width_in, height_in


def _parse_media_trays(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per tray currently loaded with media, from media-col-ready
    — each a parsed IPP collection (pyipp's parse_collection already turns
    these into plain dicts keyed by member name). A device that doesn't
    report this attribute at all (common on simpler, non-MFP devices) just
    gets an empty list, same "no guess" convention as everything else in
    this file."""
    trays = []
    for entry in _as_list(raw.get("media-col-ready")):
        if not isinstance(entry, dict):
            continue
        width_in, height_in = _media_col_dimensions_in(entry)
        source = _scalar(entry.get("media-source"))
        media_type = _scalar(entry.get("media-type"))
        if source is None and width_in is None and height_in is None:
            continue
        trays.append(
            {
                "source": source,
                "type": media_type,
                "width_in": width_in,
                "height_in": height_in,
            }
        )
    return trays


def parse_capabilities(raw: dict[str, Any]) -> dict[str, Any]:
    """Maps a raw IPP Get-Printer-Attributes dict to PrintOps's capability schema."""
    return {
        "make_model": raw.get("printer-make-and-model"),
        "firmware_version": _parse_firmware_version(raw),
        "duplex_supported": _parse_duplex_supported(raw),
        "color_supported": _parse_color_supported(raw),
        "copies_max": _parse_copies_max(raw),
        "resolutions": _parse_resolutions(raw),
        "media_sizes": [_scalar(v) for v in _as_list(raw.get("media-supported"))],
        "default_media_size": _scalar(raw.get("media-default")),
        "media_trays": _parse_media_trays(raw),
        "media_sources": [_scalar(v) for v in _as_list(raw.get("media-source-supported"))],
        "media_types": [_scalar(v) for v in _as_list(raw.get("media-type-supported"))],
        "output_bins": [_scalar(v) for v in _as_list(raw.get("output-bin-supported"))],
        "finishings": _parse_finishings(raw),
        "collation_supported": _parse_collation_supported(raw),
        "pin_printing_supported": _parse_pin_printing_supported(raw),
        "accounting_supported": _parse_accounting_supported(raw),
        "document_formats": [_scalar(v) for v in _as_list(raw.get("document-format-supported"))],
        "tls_supported": _parse_tls_supported(raw),
    }
