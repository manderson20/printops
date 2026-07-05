"""add google_workspace_users.aliases and copier-identity-from-employee-id toggle

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-05 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0031'
down_revision: Union[str, Sequence[str], None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('google_workspace_users', sa.Column('aliases', sa.JSON(), nullable=True))
    op.add_column(
        'google_workspace_settings',
        sa.Column(
            'auto_create_copier_identity_from_employee_id',
            sa.Boolean(),
            server_default='false',
            nullable=False,
        ),
    )
    op.add_column(
        'google_workspace_settings',
        sa.Column('auto_copier_identity_type', sa.String(), server_default='staff_id', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('google_workspace_settings', 'auto_copier_identity_type')
    op.drop_column('google_workspace_settings', 'auto_create_copier_identity_from_employee_id')
    op.drop_column('google_workspace_users', 'aliases')
