"""add copier identity OU scoping

Revision ID: 0055
Revises: 0054
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0055'
down_revision: Union[str, Sequence[str], None] = '0054'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Makes "who counts as trackable staff" configurable for copier
    accounting, org-wide and per device.

    All three columns are nullable with no back-fill, and null means
    "fall back to staff_org_unit_path" (org-wide) or "no extra narrowing"
    (per device). So an existing install behaves exactly as before until
    an admin configures something — except for the bug fix this enables:
    the auto-created copier identities now honour the same OU filter the
    PIN roster export already did, instead of no filter at all.

    Deliberately does NOT seed an exclusion like "/Employees/Inactive
    Employees": OU naming is per-district (see staff_org_unit_path's own
    note), so a seeded default would be wrong for everyone but the org it
    was copied from.
    """
    op.add_column(
        'google_workspace_settings',
        sa.Column('copier_identity_org_unit_paths', sa.JSON(), nullable=True),
    )
    op.add_column(
        'google_workspace_settings',
        sa.Column('copier_identity_excluded_org_unit_paths', sa.JSON(), nullable=True),
    )
    op.add_column(
        'mfp_devices',
        sa.Column('provision_org_unit_paths', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('mfp_devices', 'provision_org_unit_paths')
    op.drop_column('google_workspace_settings', 'copier_identity_excluded_org_unit_paths')
    op.drop_column('google_workspace_settings', 'copier_identity_org_unit_paths')
