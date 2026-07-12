"""move toner cartridge model from printers to per-color printer_toner_cartridges

Revision ID: 0048
Revises: 0047
Create Date: 2026-07-12 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0048'
down_revision: Union[str, Sequence[str], None] = '0047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('printer_toner_cartridges', sa.Column('model', sa.String(), nullable=True))
    # Backfill onto each printer's black cartridge row only — exactly right
    # for mono printers, and usually what was actually being tracked for
    # color ones too. A printer whose generic field was set but which never
    # had a black cartridge row configured (no cost/yield ever entered) has
    # nothing to backfill onto and is left as-is.
    op.execute(
        "UPDATE printer_toner_cartridges "
        "SET model = printers.toner_cartridge_model "
        "FROM printers "
        "WHERE printer_toner_cartridges.printer_id = printers.id "
        "AND printer_toner_cartridges.color = 'black' "
        "AND printers.toner_cartridge_model IS NOT NULL "
        "AND printers.toner_cartridge_model != ''"
    )
    op.drop_column('printers', 'toner_cartridge_model')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('printers', sa.Column('toner_cartridge_model', sa.String(), nullable=True))
    op.execute(
        "UPDATE printers "
        "SET toner_cartridge_model = printer_toner_cartridges.model "
        "FROM printer_toner_cartridges "
        "WHERE printer_toner_cartridges.printer_id = printers.id "
        "AND printer_toner_cartridges.color = 'black' "
        "AND printer_toner_cartridges.model IS NOT NULL"
    )
    op.drop_column('printer_toner_cartridges', 'model')
