"""create attribution_aliases

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-05 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0029'
down_revision: Union[str, Sequence[str], None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'attribution_aliases',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('alias', sa.String(), nullable=False),
        sa.Column('resolved_email', sa.String(), nullable=False),
        sa.Column('source', sa.String(), server_default='manual', nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_attribution_aliases_alias', 'attribution_aliases', ['alias'], unique=True)
    op.create_index('ix_attribution_aliases_resolved_email', 'attribution_aliases', ['resolved_email'])
    op.create_index('ix_attribution_aliases_source', 'attribution_aliases', ['source'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_attribution_aliases_source', 'attribution_aliases')
    op.drop_index('ix_attribution_aliases_resolved_email', 'attribution_aliases')
    op.drop_index('ix_attribution_aliases_alias', 'attribution_aliases')
    op.drop_table('attribution_aliases')
