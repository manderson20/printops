"""Measuring how much ink a spooled document would lay down.

Ghostscript's `inkcov` device renders each page and reports the fraction
covered by each colorant, as four numbers per page:

    0.11069  0.10946  0.10915  0.08235 CMYK OK

That is the measurement page counts throw away. A real four-page job on this
estate ran 11%, 8.6%, 9.8% and 1.3% black — an eight-fold spread inside one
document, every page of it counted identically by every report before this.

Parsing is separated from running so the awkward part — what a truncated file,
a device warning or a locale with comma decimals produces — is testable without
Ghostscript or a spool.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

# " 0.11069  0.10946  0.10915  0.08235 CMYK OK" — and its failure form, which
# ends "CMYK ERROR" instead. Anchored so a stray line of document text that
# happens to contain four numbers cannot be read as a page.
_PAGE = re.compile(r"^\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+CMYK\s+(OK|ERROR)\s*$")

# Ghostscript renders at this resolution for the measurement. Lower is faster
# and coarser; 72dpi is enough to average coverage over a page and is what
# keeps a long document to a second or two rather than a minute.
RENDER_DPI = 72

# A document nobody is going to finish measuring in reasonable time. Hit only
# by something pathological — a page of thousands of vector paths — and better
# abandoned with a reason than left holding the loop.
TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class Coverage:
    """Mean fraction of a page covered by each colorant, over the pages read."""

    pages: int
    cyan: float
    magenta: float
    yellow: float
    black: float

    @property
    def total_ink(self) -> float:
        """Summed across channels — 0.20 for a page at ISO test conditions.

        Useful for a headline, never for costing: a cartridge yield is quoted
        per colorant, so pricing against a total would assume a colour mix that
        was never measured.
        """
        return self.cyan + self.magenta + self.yellow + self.black


def parse(output: str) -> Coverage | None:
    """The mean coverage across every page Ghostscript reported.

    Pages it marked ERROR are skipped rather than counted as zero: a page that
    failed to render is not a blank page, and averaging one in understates the
    document by however many pages failed.

    Returns None when nothing parsed at all, which is a different fact from
    "measured, and it was blank" and is recorded differently.
    """
    pages = 0
    totals = [0.0, 0.0, 0.0, 0.0]

    for line in output.splitlines():
        match = _PAGE.match(line)
        if match is None or match.group(5) != "OK":
            continue
        try:
            values = [float(match.group(index)) for index in range(1, 5)]
        except ValueError:
            continue
        # Ghostscript reports fractions. Anything above 1 is a channel this
        # build measures differently, and clamping keeps one odd page from
        # making a document look impossibly dense.
        totals = [total + min(value, 1.0) for total, value in zip(totals, values)]
        pages += 1

    if pages == 0:
        return None
    return Coverage(
        pages=pages,
        cyan=totals[0] / pages,
        magenta=totals[1] / pages,
        yellow=totals[2] / pages,
        black=totals[3] / pages,
    )


async def measure(path: Path, *, gs: str = "gs") -> tuple[Coverage | None, str | None]:
    """Measure one spooled document. Returns (coverage, error detail).

    The file is copied to a temporary directory first. Ghostscript ships under
    an AppArmor profile that denies it the CUPS spool, so reading in place
    fails with an unhelpful `undefinedfilename` — and the copy is cheap next to
    rendering. The copy is removed whatever happens.

    Runs Ghostscript with no access to the network or the outside world beyond
    the file itself (-dSAFER), because a print job is somebody else's document
    and a renderer is a large attack surface.
    """
    with tempfile.TemporaryDirectory(prefix="printops-inkcov-") as work:
        local = Path(work) / "job.pdf"
        try:
            await asyncio.to_thread(shutil.copyfile, path, local)
        except FileNotFoundError:
            return None, "spool file no longer exists"
        except OSError as exc:
            return None, f"could not read the spool file: {exc}"

        process = await asyncio.create_subprocess_exec(
            gs,
            "-q",
            "-dSAFER",
            "-dNOPAUSE",
            "-dBATCH",
            f"-r{RENDER_DPI}",
            "-sDEVICE=inkcov",
            "-o",
            "-",
            str(local),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT_SECONDS)
        except TimeoutError:
            process.kill()
            await process.wait()
            return None, f"rendering did not finish within {TIMEOUT_SECONDS}s"

    coverage = parse(stdout.decode("utf-8", "replace"))
    if coverage is None:
        detail = stderr.decode("utf-8", "replace").strip().splitlines()
        return None, (detail[-1] if detail else "no pages could be measured")
    return coverage, None
