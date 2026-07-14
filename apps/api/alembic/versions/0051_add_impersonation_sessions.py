"""add impersonation_sessions

Revision ID: 0051
Revises: 0050
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0051'
down_revision: Union[str, Sequence[str], None] = '0050'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'impersonation_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('admin_user_id', sa.Uuid(), nullable=True),
        sa.Column('admin_email', sa.String(), nullable=False),
        sa.Column('target_user_id', sa.Uuid(), nullable=False),
        sa.Column('target_email', sa.String(), nullable=False),
        sa.Column('target_role', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_impersonation_sessions_admin_user_id', 'impersonation_sessions', ['admin_user_id']
    )
    op.create_index(
        'ix_impersonation_sessions_target_user_id', 'impersonation_sessions', ['target_user_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_impersonation_sessions_target_user_id', table_name='impersonation_sessions')
    op.drop_index('ix_impersonation_sessions_admin_user_id', table_name='impersonation_sessions')
    op.drop_table('impersonation_sessions')
