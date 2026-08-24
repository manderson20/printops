"""Correct jobs recorded as colour on printers that cannot print colour.

CUPS reports the colour mode a job *asked* for, not what the machine produced.
A monochrome printer accepts a colour job and prints it in grey without
complaint, so these rows have always said "color" for pages that came out grey
— and Insights counted them as colour pages and billed them at the colour rate.

Going forward this is prevented at both write paths (see recorded_color_mode).
This corrects the history those paths already wrote: 18 jobs / 24 pages on this
server when written.

Deliberately narrow. Only printers whose detected capabilities say colour is
explicitly unsupported are touched; a printer never successfully probed keeps
its rows untouched, because rewriting a real colour printer's history to
monochrome would be the same mistake pointing the other way. Not reversible in
the strict sense — the downgrade cannot know which rows it changed — so the
downgrade is a no-op rather than a guess that would corrupt good data.
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
        """
    )


def downgrade() -> None:
    """No-op: which rows said "color" before is not recoverable, and guessing
    would put false colour back onto grey pages."""
