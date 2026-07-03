"""add org_unit_path and staff_org_unit_path setting

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-03 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0021'
down_revision: Union[str, Sequence[str], None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('google_workspace_users', sa.Column('org_unit_path', sa.String(), nullable=True))
    op.add_column(
        'google_workspace_settings', sa.Column('staff_org_unit_path', sa.String(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('google_workspace_settings', 'staff_org_unit_path')
    op.drop_column('google_workspace_users', 'org_unit_path')
