import io
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LOGO_PATH = Path(__file__).resolve().parents[3] / "web" / "public" / "printops-logo.png"
FONT_PATH = Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")

DPI = 150
PAGE_SIZE = (int(8.5 * DPI), int(11 * DPI))
LOGO_WIDTH = 260


class TestPrintError(Exception):
    pass


def _build_test_page(printer_name: str, username: str) -> bytes:
    """Composes a one-page color PDF: the PrintOps logo plus identifying
    text. A real embedded color image is a better color check than plain
    text, and PDF is in every IPP Everywhere printer's PDL — no need to
    hand-roll PostScript or shell out to a converter."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    page = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(page)

    logo = Image.open(LOGO_PATH).convert("RGBA")
    logo_height = int(logo.height * (LOGO_WIDTH / logo.width))
    logo = logo.resize((LOGO_WIDTH, logo_height))
    logo_x = (PAGE_SIZE[0] - LOGO_WIDTH) // 2
    page.paste(logo, (logo_x, DPI), logo)

    font = ImageFont.truetype(str(FONT_PATH), size=22)
    lines = [
        "PrintOps Test Print",
        f"Printer: {printer_name}",
        f"Triggered by: {username}",
        f"Time: {timestamp}",
        "If this printed in color, color output works end to end.",
    ]
    text_y = DPI + logo_height + 60
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_x = (PAGE_SIZE[0] - (bbox[2] - bbox[0])) // 2
        draw.text((text_x, text_y), line, fill="black", font=font)
        text_y += 36

    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=float(DPI))
    return buf.getvalue()


def submit_test_print(printer_id: str, printer_name: str, username: str) -> str:
    """Submits a test page to the printer's CUPS queue via `lp`, so it goes
    through the exact same path (printops backend -> job logging -> real ipp
    backend) as a real job. Requires scripts/sync_cups_queue.sh to have been
    run for this printer already — raises TestPrintError with a clear reason
    otherwise."""
    queue_name = f"printops-{printer_id}"
    doc = _build_test_page(printer_name, username)

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
                f"scripts/sync_cups_queue.sh {printer_id} on the print server first."
            )
        raise TestPrintError(reason or "lp exited with an error.")

    return result.stdout.strip()
