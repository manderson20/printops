"""add mfp_devices admin credentials

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0054'
down_revision: Union[str, Sequence[str], None] = '0053'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    The device's own admin web UI login, for connectors that actually log
    into the device (per-user accounting pulls, user provisioning) — see
    app/copiers/device_admin.py.

    Purely additive and both columns are nullable: every existing device
    keeps working with no credential stored, and the connectors that need
    one raise DeviceCredentialsMissing (a 4xx explaining which device needs
    setting up) rather than failing obscurely.

    Deliberately NOT back-filled from printers.web_login_password_encrypted,
    even though that column already holds the same secret for nine copiers.
    That field is documented as reference-only storage PrintOps never
    authenticates with (see Printer's docstring); silently promoting it into
    a live credential would widen how a stored password is used without an
    admin ever deciding that. Copy them across deliberately instead.
    """
    op.add_column(
        'mfp_devices',
        sa.Column('admin_username', sa.String(), nullable=True),
    )
    op.add_column(
        'mfp_devices',
        sa.Column('admin_password_encrypted', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('mfp_devices', 'admin_password_encrypted')
    op.drop_column('mfp_devices', 'admin_username')
