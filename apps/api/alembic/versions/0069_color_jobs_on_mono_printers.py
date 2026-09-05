"""Correct jobs recorded as color on printers that cannot print color.

CUPS reports the color mode a job *asked* for, not what the machine produced.
A monochrome printer accepts a color job and prints it in grey without
complaint, so these rows have always said "color" for pages that came out grey
— and Insights counted them as color pages and billed them at the color rate.

Going forward this is prevented at both write paths (see recorded_color_mode).
This corrects the history those paths already wrote: 18 jobs / 24 pages on this
server when written.

Deliberately narrow. Only printers whose detected capabilities say color is
explicitly unsupported are touched; a printer never successfully probed keeps
its rows untouched, because rewriting a real color printer's history to
monochrome would be the same mistake pointing the other way. Not reversible in
the strict sense — the downgrade cannot know which rows it changed — so the
downgrade is a no-op rather than a guess that would corrupt good data.

The limit worth naming: a job row records which printer *entry* it went to,
not which physical machine. If color hardware were swapped for mono hardware
under one entry, this would rewrite the old machine's genuine color jobs.
Nothing distinguishes those rows, so the bound below is the honest one — a job
cannot predate the entry it belongs to. On this server the question is settled
by operation rather than by data: every printer entry was created 2026-07-02/04
when PrintOps was installed, every affected job postdates that, and the district
confirms no printer has been swapped since — the one hardware change was RM 502,
replaced with an identical model, and that printer is color, so it is not in
the set this touches at all.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0069"
down_revision: str | Sequence[str] | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE jobs
           SET color_mode = 'monochrome'
          FROM printers
         WHERE jobs.printer_id = printers.id
           AND jobs.color_mode = 'color'
           AND printers.capabilities ->> 'color_supported' = 'false'
           AND jobs.created_at >= printers.created_at
        """
    )


def downgrade() -> None:
    """No-op: which rows said "color" before is not recoverable, and guessing
    would put false color back onto grey pages."""
