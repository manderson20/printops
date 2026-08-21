"""Composes and submits the PrintOps test page.

The page doubles as a printer's identity sheet: an admin standing at the
device can read what PrintOps believes it is, what it can do, how much
toner is left and what its counters said at the moment of printing —
alongside the colour, greyscale and fine-line targets that make it a real
print-quality check rather than a "did anything come out" check.

Everything on the sheet comes from data PrintOps already holds (the
Printer row and its last capability discovery). Nothing here probes the
device — a test print must stay fast and must still work on a printer
that is currently unreachable for SNMP or IPP.
"""

import io
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw, ImageFont

from app.integrations.git_update import get_current_version
from app.models.printer import Printer
from app.models.report import PrinterTonerCartridge

LOGO_PATH = Path(__file__).resolve().parents[3] / "web" / "public" / "printops-logo.png"
FONT_DIR = Path("/usr/share/fonts/truetype/liberation")

DPI = 150
PAGE_W, PAGE_H = int(8.5 * DPI), int(11 * DPI)
MARGIN = 78
CONTENT_W = PAGE_W - 2 * MARGIN

INK = (26, 26, 26)
MUTED = (122, 122, 122)
RULE = (206, 206, 206)
ACCENT = (29, 95, 173)  # --primary in apps/web/src/app/globals.css
YES = (34, 122, 62)

# How many entries of a long capability list get printed before it's
# summarised. A wide-format device can advertise 60+ media sizes; the
# point of the sheet is orientation, not a full IPP dump.
LIST_LIMIT = 8


class TestPrintError(Exception):
    pass


@dataclass(frozen=True)
class TestPageInfo:
    """Everything the page prints, flattened off the ORM before rendering.

    Deliberately a plain frozen dataclass rather than the Printer model:
    rendering happens in a worker thread (see submit_test_print), where
    touching a lazily-loaded SQLAlchemy attribute would either blow up or
    silently open a second session. The router flattens it while it still
    has an awaited session; everything here is already-loaded values."""

    printer_id: str
    name: str
    make_model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    address: str | None = None
    use_tls: bool = False
    ipp_path: str | None = None
    location: str | None = None
    status: str | None = None
    color_supported: bool = False
    duplex_supported: bool = False
    collation_supported: bool = False
    pin_printing_supported: bool = False
    accounting_supported: bool = False
    tls_supported: bool = False
    copies_max: int | None = None
    resolutions: list[str] = field(default_factory=list)
    media_sizes: list[str] = field(default_factory=list)
    default_media_size: str | None = None
    media_trays: list[str] = field(default_factory=list)
    media_types: list[str] = field(default_factory=list)
    output_bins: list[str] = field(default_factory=list)
    finishings: list[str] = field(default_factory=list)
    document_formats: list[str] = field(default_factory=list)
    capabilities_detected_at: datetime | None = None
    # (colour, percent-or-None), already ordered black-first by the router.
    toner: list[tuple[str, int | None]] = field(default_factory=list)
    page_count_total: int | None = None
    page_count_print: int | None = None
    page_count_copy: int | None = None
    page_count_checked_at: datetime | None = None
    app_version: str | None = None


# --------------------------------------------------------------------------
# value formatting
# --------------------------------------------------------------------------

# PWG self-describing names are precise but unreadable on paper. Only the
# sizes a school fleet actually loads are worth spelling out; anything else
# falls back to the middle token of the name, which is already the size's
# common name in every PWG name ("na_letter_8.5x11in" -> "letter").
_MEDIA_NAMES = {
    "na_letter_8.5x11in": "Letter",
    "na_legal_8.5x14in": "Legal",
    "na_ledger_11x17in": "Ledger",
    "na_executive_7.25x10.5in": "Executive",
    "na_invoice_5.5x8.5in": "Statement",
    "na_foolscap_8.5x13in": "Foolscap",
    "na_index-4x6_4x6in": "4x6",
    "na_number-10_4.125x9.5in": "#10 Envelope",
    "iso_a3_297x420mm": "A3",
    "iso_a4_210x297mm": "A4",
    "iso_a5_148x210mm": "A5",
    "iso_a6_105x148mm": "A6",
    "jis_b4_257x364mm": "B4",
    "jis_b5_182x257mm": "B5",
}


def media_label(name: str | None) -> str:
    if not name:
        return "—"
    if name in _MEDIA_NAMES:
        return _MEDIA_NAMES[name]
    parts = name.split("_")
    if len(parts) >= 2 and parts[1]:
        return parts[1].replace("-", " ").title()
    return name


def keyword_label(value: str | None) -> str:
    """IPP keywords are lowercase-hyphenated ("two-sided-long-edge",
    "staple-top-left"). Title-cased with hyphens opened up, they read as
    English without needing a lookup table per vocabulary."""
    if not value:
        return "—"
    return value.replace("-", " ").replace("_", " ").strip().capitalize()


def resolution_label(entry: dict) -> str:
    """capabilities["resolutions"] entries are {"x", "y", "unit"} — unit 3
    is dots-per-centimetre and 4 dots-per-inch in IPP's enum, and every
    device in practice reports 4."""
    x, y = entry.get("x"), entry.get("y")
    if not x or not y:
        return ""
    unit = "dpcm" if entry.get("unit") == 3 else "dpi"
    return f"{x}x{y} {unit}" if x != y else f"{x} {unit}"


def tray_label(entry: dict) -> str:
    """A loaded tray reads best as "Tray 1 · Letter · Plain" — source
    first, since that's what's written on the drawer."""
    bits = []
    if entry.get("source"):
        bits.append(keyword_label(entry["source"]))
    w, h = entry.get("width_in"), entry.get("height_in")
    if w and h:
        bits.append(f'{_trim_number(w)}x{_trim_number(h)}"')
    if entry.get("type"):
        bits.append(keyword_label(entry["type"]))
    return " · ".join(bits)


def _trim_number(value: float) -> str:
    return f"{value:g}"


def _join(values: list[str], limit: int = LIST_LIMIT) -> str:
    """Never silently truncate — a sheet that lists 8 of 60 media sizes
    without saying so reads as a complete list and misleads."""
    kept = [v for v in values if v]
    if not kept:
        return "—"
    if len(kept) <= limit:
        return ", ".join(kept)
    return ", ".join(kept[:limit]) + f"  (+{len(kept) - limit} more)"


def _resolve_timezone(timezone: str | None) -> tzinfo:
    """Turns an IANA zone name from the caller's browser into a tzinfo.
    Anything missing or unrecognised falls back to UTC rather than raising —
    a test page that prints with an odd clock is still a useful test page,
    and this is never worth failing the print over."""
    if not timezone:
        return UTC
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def _stamp(moment: datetime | None, tz: tzinfo) -> str:
    if moment is None:
        return "never"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def _count(value: int | None) -> str:
    return f"{value:,}" if value is not None else "—"


# --------------------------------------------------------------------------
# drawing primitives
# --------------------------------------------------------------------------


def _font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    family = "LiberationMono" if mono else "LiberationSans"
    weight = "Bold" if bold else "Regular"
    return ImageFont.truetype(str(FONT_DIR / f"{family}-{weight}.ttf"), size=size)


def _pt(points: float) -> int:
    """Point sizes are what a print technician talks in, pixels are what
    Pillow draws in; at 150 DPI they differ by ~2x, so converting keeps the
    type-size ladder honest about what it claims to be."""
    return max(1, round(points / 72 * DPI))


def _section(draw: ImageDraw.ImageDraw, y: int, title: str) -> int:
    draw.text((MARGIN, y), title.upper(), font=_font(21, bold=True), fill=ACCENT)
    y += 30
    draw.line([(MARGIN, y), (PAGE_W - MARGIN, y)], fill=RULE, width=2)
    return y + 18


def _rows(draw: ImageDraw.ImageDraw, y: int, pairs: list[tuple[str, str]], cols: int = 2) -> int:
    """Label/value pairs laid out in `cols` columns, filling left to right."""
    label_font, value_font = _font(19), _font(19, bold=True)
    col_w = CONTENT_W // cols
    for index, (label, value) in enumerate(pairs):
        col, row = index % cols, index // cols
        x = MARGIN + col * col_w
        row_y = y + row * 30
        draw.text((x, row_y), label, font=label_font, fill=MUTED)
        draw.text((x + 132, row_y), value, font=value_font, fill=INK)
    rows = (len(pairs) + cols - 1) // cols
    return y + rows * 30


def _wrapped(
    draw: ImageDraw.ImageDraw, y: int, label: str, value: str, *, mono: bool = False
) -> int:
    """A label with a value that may need more than one line — the media
    and format lists routinely do."""
    label_font, value_font = _font(19), _font(17 if mono else 19, mono=mono)
    draw.text((MARGIN, y), label, font=label_font, fill=MUTED)
    x = MARGIN + 132
    avail = PAGE_W - MARGIN - x
    line, lines = "", []
    for word in value.split(" "):
        probe = f"{line} {word}".strip()
        if draw.textlength(probe, font=value_font) <= avail or not line:
            line = probe
        else:
            lines.append(line)
            line = word
    lines.append(line)
    for offset, text in enumerate(lines[:3]):
        draw.text((x, y + offset * 26), text, font=value_font, fill=INK)
    return y + max(1, len(lines[:3])) * 26


def _flags(draw: ImageDraw.ImageDraw, y: int, flags: list[tuple[str, bool]]) -> int:
    """Supported/not as a dot plus an explicit word. The dot alone would be
    ambiguous on a mono device or a photocopy of the sheet, which is
    exactly where this page tends to end up."""
    font, bold = _font(19), _font(19, bold=True)
    col_w = CONTENT_W // 3
    for index, (label, on) in enumerate(flags):
        x = MARGIN + (index % 3) * col_w
        row_y = y + (index // 3) * 30
        draw.ellipse([x, row_y + 7, x + 11, row_y + 18], fill=YES if on else (196, 196, 196))
        draw.text((x + 22, row_y), label, font=font, fill=MUTED)
        draw.text(
            (x + 22 + 148, row_y), "Yes" if on else "No", font=bold, fill=INK if on else MUTED
        )
    rows = (len(flags) + 2) // 3
    return y + rows * 30


def _toner_bars(draw: ImageDraw.ImageDraw, y: int, toner: list[tuple[str, int | None]]) -> int:
    swatch = {
        "black": (24, 24, 24),
        "cyan": (0, 158, 224),
        "magenta": (227, 0, 126),
        "yellow": (255, 221, 0),
    }
    font, bold = _font(19), _font(19, bold=True)
    bar_x, bar_w = MARGIN + 132, 300
    for index, (colour, percent) in enumerate(toner[:4]):
        row_y = y + index * 30
        draw.text((MARGIN, row_y), colour.capitalize(), font=font, fill=MUTED)
        draw.rectangle([bar_x, row_y + 5, bar_x + bar_w, row_y + 21], outline=RULE, width=1)
        if percent is not None:
            fill_w = int(bar_w * max(0, min(100, percent)) / 100)
            if fill_w:
                draw.rectangle(
                    [bar_x, row_y + 5, bar_x + fill_w, row_y + 21],
                    fill=swatch.get(colour.lower(), ACCENT),
                )
        label = f"{percent}%" if percent is not None else "not reported"
        draw.text((bar_x + bar_w + 16, row_y), label, font=bold, fill=INK)
    return y + max(1, len(toner[:4])) * 30


def _quality_targets(draw: ImageDraw.ImageDraw, y: int, colour_device: bool) -> int:
    """The part of the page that is actually a test rather than a report."""
    label_font = _font(15)

    if colour_device:
        patches = [
            ("Cyan", (0, 174, 239)),
            ("Magenta", (236, 0, 140)),
            ("Yellow", (255, 242, 0)),
            ("Red", (237, 28, 36)),
            ("Green", (0, 166, 81)),
            ("Blue", (46, 49, 146)),
            ("Black", (35, 31, 32)),
        ]
        patch_w = CONTENT_W // len(patches)
        for index, (name, rgb) in enumerate(patches):
            x = MARGIN + index * patch_w
            draw.rectangle([x, y, x + patch_w - 10, y + 62], fill=rgb)
            draw.text((x, y + 68), name, font=label_font, fill=MUTED)
        y += 96

    # Greyscale ramp: the single most useful target for spotting a failing
    # drum or a mis-set density — the steps should be evenly separated and
    # 10% must not disappear into the paper.
    steps = 11
    step_w = CONTENT_W // steps
    for index in range(steps):
        shade = 255 - round(index * 255 / (steps - 1))
        x = MARGIN + index * step_w
        draw.rectangle(
            [x, y, x + step_w - 6, y + 46], fill=(shade, shade, shade), outline=RULE, width=1
        )
        draw.text((x, y + 52), f"{index * 10}%", font=label_font, fill=MUTED)
    y += 82

    # Hairlines at known widths, horizontal and vertical, so a resolution
    # or registration problem shows up as lines that merge or break.
    half = CONTENT_W // 2
    draw.text((MARGIN, y), "Fine lines (1–4 px @ 150 dpi)", font=label_font, fill=MUTED)
    draw.text((MARGIN + half, y), "Type size ladder", font=label_font, fill=MUTED)
    line_y = y + 26
    for width in (1, 2, 3, 4):
        draw.line([(MARGIN, line_y), (MARGIN + 190, line_y)], fill=INK, width=width)
        line_y += 16
    for offset, width in enumerate((1, 2, 3, 4)):
        x = MARGIN + 220 + offset * 26
        draw.line([(x, y + 26), (x, y + 90)], fill=INK, width=width)

    text_y = y + 24
    for points in (5, 6, 7, 8, 10):
        draw.text(
            (MARGIN + half, text_y),
            f"{points}pt  The quick brown fox jumps over the lazy dog 0123456789",
            font=_font(_pt(points)),
            fill=INK,
        )
        text_y += _pt(points) + 8
    return max(line_y, text_y) + 6


def _checklist(draw: ImageDraw.ImageDraw, y: int, colour_device: bool) -> int:
    """What the targets above are actually for. Without this the patches
    and ramps are decoration — an admin holding the sheet at the device
    needs to know what a bad one looks like."""
    items = [
        "All four corner marks printed",
        "Hairlines stay separate — none merged or broken",
        "5pt text is legible and sharp, not fuzzy",
        "Grey steps separate evenly, no banding",
        "The 10% grey step is visible against the paper",
    ]
    if colour_device:
        items.insert(0, "Colour patches are solid and distinct")
    font = _font(17)
    col_w = CONTENT_W // 2
    rows = (len(items) + 1) // 2
    for index, text in enumerate(items):
        x = MARGIN + (index // rows) * col_w
        row_y = y + (index % rows) * 26
        draw.rectangle([x, row_y + 3, x + 14, row_y + 17], outline=MUTED, width=1)
        draw.text((x + 26, row_y), text, font=font, fill=INK)
    return y + rows * 26


def _registration_marks(draw: ImageDraw.ImageDraw) -> None:
    """Corner crops at a known inset — if one is clipped, the device's
    printable area is smaller than the page and margins are being lost."""
    inset, arm = 40, 30
    for x, y in (
        (inset, inset),
        (PAGE_W - inset, inset),
        (inset, PAGE_H - inset),
        (PAGE_W - inset, PAGE_H - inset),
    ):
        dx = arm if x < PAGE_W / 2 else -arm
        dy = arm if y < PAGE_H / 2 else -arm
        draw.line([(x, y), (x + dx, y)], fill=INK, width=2)
        draw.line([(x, y), (x, y + dy)], fill=INK, width=2)


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------


def _build_test_page(info: TestPageInfo, username: str, timezone: str | None = None) -> bytes:
    """Composes the one-page colour PDF. A real embedded colour image plus
    process-colour patches is a better colour check than plain text, and
    PDF is in every IPP Everywhere printer's PDL — no need to hand-roll
    PostScript or shell out to a converter."""
    tz = _resolve_timezone(timezone)
    page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(page)

    _registration_marks(draw)

    # Header ---------------------------------------------------------------
    logo = Image.open(LOGO_PATH).convert("RGBA")
    logo_h = 84
    logo = logo.resize((round(logo.width * (logo_h / logo.height)), logo_h))
    page.paste(logo, (MARGIN, MARGIN), logo)
    text_x = MARGIN + logo.width + 24
    draw.text((text_x, MARGIN + 4), "Printer Test Page", font=_font(40, bold=True), fill=INK)
    draw.text((text_x, MARGIN + 52), info.name, font=_font(23), fill=MUTED)
    y = MARGIN + logo_h + 26
    draw.line([(MARGIN, y), (PAGE_W - MARGIN, y)], fill=INK, width=3)
    y += 28

    # Device ---------------------------------------------------------------
    y = _section(draw, y, "Device")
    transport = f"{info.address or '—'}"
    if info.address:
        transport += "  ·  IPPS/TLS" if info.use_tls else "  ·  IPP"
    y = _rows(
        draw,
        y,
        [
            ("Model", info.make_model or "—"),
            ("Serial", info.serial_number or "—"),
            ("Address", transport),
            ("Firmware", info.firmware_version or "—"),
            ("IPP path", info.ipp_path or "—"),
            ("Location", info.location or "—"),
            ("Status", (info.status or "unknown").capitalize()),
        ],
    )
    y = _wrapped(draw, y + 4, "Queue", f"printops-{info.printer_id}", mono=True)
    y += 22

    # Features -------------------------------------------------------------
    y = _section(draw, y, "Features")
    y = _flags(
        draw,
        y,
        [
            ("Colour", info.color_supported),
            ("Duplex", info.duplex_supported),
            ("Collation", info.collation_supported),
            ("PIN printing", info.pin_printing_supported),
            ("Accounting", info.accounting_supported),
            ("IPPS (TLS)", info.tls_supported),
        ],
    )
    y += 10
    y = _wrapped(draw, y, "Resolutions", _join(info.resolutions, 4))
    y = _wrapped(draw, y, "Default media", media_label(info.default_media_size))
    y = _wrapped(draw, y, "Media sizes", _join(info.media_sizes))
    if info.media_trays:
        y = _wrapped(draw, y, "Loaded trays", _join(info.media_trays, 3))
    if info.finishings:
        y = _wrapped(draw, y, "Finishing", _join(info.finishings, 6))
    if info.media_types:
        y = _wrapped(draw, y, "Media types", _join(info.media_types, 6))
    if info.output_bins:
        y = _wrapped(draw, y, "Output bins", _join(info.output_bins, 5))
    y = _wrapped(draw, y, "Formats", _join(info.document_formats, 5))
    if info.copies_max:
        y = _wrapped(draw, y, "Max copies", str(info.copies_max))
    detected = _stamp(info.capabilities_detected_at, tz)
    draw.text((MARGIN, y + 4), f"Capabilities last read {detected}", font=_font(15), fill=MUTED)
    y += 34

    # Supplies -------------------------------------------------------------
    y = _section(draw, y, "Supplies and counters")
    if info.toner:
        y = _toner_bars(draw, y, info.toner)
    else:
        draw.text((MARGIN, y), "No toner levels recorded.", font=_font(19), fill=MUTED)
        y += 30
    y += 8
    y = _rows(
        draw,
        y,
        [
            ("Total pages", _count(info.page_count_total)),
            ("Printed", _count(info.page_count_print)),
            ("Copied", _count(info.page_count_copy)),
            ("Counters read", _stamp(info.page_count_checked_at, tz)),
        ],
    )
    y += 22

    # Quality --------------------------------------------------------------
    y = _section(draw, y, "Print quality")
    y = _quality_targets(draw, y, info.color_supported)
    y = _checklist(draw, y + 16, info.color_supported)

    # Footer ---------------------------------------------------------------
    footer_y = PAGE_H - MARGIN - 44
    draw.line([(MARGIN, footer_y), (PAGE_W - MARGIN, footer_y)], fill=RULE, width=2)
    stamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    left = f"Triggered by {username}  ·  {stamp}"
    right = f"PrintOps {info.app_version}" if info.app_version else "PrintOps"
    draw.text((MARGIN, footer_y + 12), left, font=_font(17), fill=MUTED)
    draw.text((PAGE_W - MARGIN, footer_y + 12), right, font=_font(17), fill=MUTED, anchor="ra")

    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=float(DPI))
    return buf.getvalue()


# Black first, then the process colours in the order every vendor's panel
# lists them — so the sheet matches what the admin sees on the device.
_TONER_ORDER = {"black": 0, "cyan": 1, "magenta": 2, "yellow": 3}


def build_page_info(printer: Printer, cartridges: list[PrinterTonerCartridge]) -> TestPageInfo:
    """Flattens a Printer and its cartridges into the frozen snapshot the
    renderer prints. Must be called while the caller's session is still
    live — see TestPageInfo's docstring for why the render itself can't
    touch the ORM.

    Every field is optional on purpose: a printer added five minutes ago
    that has never been discovered still gets a test page, just a sparser
    one. Printing is the point; the data is a bonus."""
    caps = printer.capabilities or {}

    make_model = caps.get("make_model") or " ".join(
        part for part in (printer.manufacturer, printer.model) if part
    )
    host = printer.ip_address or printer.hostname
    address = f"{host}:{printer.port}" if host else None
    location = " · ".join(
        part for part in (printer.building, printer.room, printer.department) if part
    )

    toner = sorted(
        ((c.color, c.current_level_percent) for c in cartridges),
        key=lambda item: (_TONER_ORDER.get((item[0] or "").lower(), 9), item[0] or ""),
    )

    try:
        app_version = get_current_version()
    except OSError:
        # A VERSION file that can't be read is not a reason to fail a print.
        app_version = None

    return TestPageInfo(
        printer_id=str(printer.id),
        name=printer.name,
        make_model=make_model or None,
        serial_number=printer.serial_number,
        firmware_version=caps.get("firmware_version"),
        address=address,
        use_tls=bool(printer.use_tls),
        ipp_path=printer.ipp_path or printer.ipp_path_detected,
        location=location or None,
        status=printer.status,
        color_supported=bool(caps.get("color_supported")),
        duplex_supported=bool(caps.get("duplex_supported")),
        collation_supported=bool(caps.get("collation_supported")),
        pin_printing_supported=bool(caps.get("pin_printing_supported")),
        accounting_supported=bool(caps.get("accounting_supported")),
        tls_supported=bool(caps.get("tls_supported")),
        copies_max=caps.get("copies_max"),
        resolutions=[
            label for entry in caps.get("resolutions") or [] if (label := resolution_label(entry))
        ],
        media_sizes=[media_label(name) for name in caps.get("media_sizes") or []],
        default_media_size=caps.get("default_media_size"),
        media_trays=[
            label for entry in caps.get("media_trays") or [] if (label := tray_label(entry))
        ],
        media_types=[keyword_label(v) for v in caps.get("media_types") or []],
        output_bins=[keyword_label(v) for v in caps.get("output_bins") or []],
        finishings=[keyword_label(v) for v in caps.get("finishings") or []],
        document_formats=list(caps.get("document_formats") or []),
        capabilities_detected_at=printer.capabilities_detected_at,
        toner=toner,
        page_count_total=printer.page_count_total,
        page_count_print=printer.page_count_print,
        page_count_copy=printer.page_count_copy,
        page_count_checked_at=printer.page_count_checked_at,
        app_version=app_version,
    )


def submit_test_print(info: TestPageInfo, username: str, timezone: str | None = None) -> str:
    """Submits a test page to the printer's CUPS queue via `lp`, so it goes
    through the exact same path (printops backend -> job logging -> real ipp
    backend) as a real job. Requires scripts/sync_cups_queue.sh to have been
    run for this printer already — raises TestPrintError with a clear reason
    otherwise."""
    queue_name = f"printops-{info.printer_id}"
    doc = _build_test_page(info, username, timezone)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(doc)
        path = Path(f.name)

    try:
        result = subprocess.run(
            [
                "lp",
                "-d",
                queue_name,
                "-U",
                username,
                "-t",
                "PrintOps Test Print",
                # The queue's saved default is monochrome (cost-saving for
                # everyday jobs) — override just this job so the embedded
                # logo actually exercises color output, which is the point.
                "-o",
                "print-color-mode=color",
                "-o",
                "ColorModel=RGB",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise TestPrintError("The `lp` command isn't available on the PrintOps server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TestPrintError("Submitting the test print timed out.") from exc
    finally:
        path.unlink(missing_ok=True)

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        if "Unknown destination" in reason or "does not exist" in reason:
            raise TestPrintError(
                "No CUPS queue exists for this printer yet — run "
                f"scripts/sync_cups_queue.sh {info.printer_id} on the print server first."
            )
        raise TestPrintError(reason or "lp exited with an error.")

    return result.stdout.strip()
