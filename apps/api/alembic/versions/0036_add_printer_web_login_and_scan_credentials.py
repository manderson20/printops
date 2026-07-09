"""add printer web login and scan-to-email credentials

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0036'
down_revision: Union[str, Sequence[str], None] = '0035'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('printers', sa.Column('web_login_username', sa.String(), nullable=True))
    op.add_column(
        'printers', sa.Column('web_login_password_encrypted', sa.String(), nullable=True)
    )
    op.add_column('printers', sa.Column('scan_email_address', sa.String(), nullable=True))
    op.add_column('printers', sa.Column('scan_password_encrypted', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('printers', 'scan_password_encrypted')
    op.drop_column('printers', 'scan_email_address')
    op.drop_column('printers', 'web_login_password_encrypted')
    op.drop_column('printers', 'web_login_username')
