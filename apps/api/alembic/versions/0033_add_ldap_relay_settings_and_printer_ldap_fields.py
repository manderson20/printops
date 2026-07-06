"""add ldap_relay_settings and printer ldap fields

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0033'
down_revision: Union[str, Sequence[str], None] = '0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ldap_relay_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('base_dn', sa.String(), server_default='dc=printops,dc=local', nullable=False),
        sa.Column('port', sa.Integer(), server_default='389', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.add_column('printers', sa.Column('ldap_enabled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('printers', sa.Column('ldap_bind_username', sa.String(), nullable=True))
    op.add_column('printers', sa.Column('ldap_bind_password_hash', sa.String(), nullable=True))
    op.create_unique_constraint('uq_printers_ldap_bind_username', 'printers', ['ldap_bind_username'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_printers_ldap_bind_username', 'printers', type_='unique')
    op.drop_column('printers', 'ldap_bind_password_hash')
    op.drop_column('printers', 'ldap_bind_username')
    op.drop_column('printers', 'ldap_enabled')
    op.drop_table('ldap_relay_settings')
