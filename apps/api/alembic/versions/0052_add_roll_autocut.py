"""add roll_autocut

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0052'
down_revision: Union[str, Sequence[str], None] = '0051'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'printers',
        sa.Column(
            'roll_autocut',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Back-fill for printers already discovered as roll-cutters: any device
    # whose probed capabilities advertise the IPP "trim" finishing (see
    # app/printers/capabilities.py — finishings enum 11) should keep cutting.
    # Without this, the first queue re-sync after deploy would regenerate the
    # -m everywhere PPD with its no-cut default and silently stop cutting on
    # printers that were cutting before (e.g. the Canon TM-300 plotter whose
    # queue default was set by hand). capabilities is a plain JSON column, so
    # it's cast to jsonb for the @> containment check.
    op.execute(
        """
        UPDATE printers
        SET roll_autocut = true
        WHERE capabilities IS NOT NULL
          AND (capabilities::jsonb -> 'finishings') @> '["trim"]'::jsonb
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('printers', 'roll_autocut')
