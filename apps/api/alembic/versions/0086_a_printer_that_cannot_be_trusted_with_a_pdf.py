"""a printer that cannot be trusted with a pdf

Revision ID: 0086
Revises: 0085
Create Date: 2026-09-14 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: Union[str, Sequence[str], None] = "0085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Every driverless queue hands a PDF straight to the printer, whose own
    interpreter renders it. Some interpreters fail on particular documents —
    one crashed its printer with a firmware error on a single PDF, another
    reported success and printed nothing — and the only remedy is to render on
    the print server instead (scripts/lib/pdf_rendering.sh).

    No back-fill. Off is what every queue does today, and which printers need
    this is something an admin learns from a failure, not something a probe can
    find out without causing one.

    Downgrading leaves any queue that had it on rendering on the server until
    that printer's queues are next rebuilt by a sync that knows the column.
    """
    op.add_column(
        "printers",
        sa.Column(
            "render_pdf_on_server",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "render_pdf_on_server")
