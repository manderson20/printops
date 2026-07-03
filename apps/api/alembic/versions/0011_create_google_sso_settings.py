"""create google sso settings table

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-03 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('google_sso_settings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.String(), nullable=True),
    sa.Column('client_secret_encrypted', sa.String(), nullable=True),
    sa.Column('workspace_domain', sa.String(), nullable=True),
    sa.Column('initial_admin_emails', sa.String(), nullable=True),
    sa.Column('redirect_base_url', sa.String(), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('google_sso_settings')
