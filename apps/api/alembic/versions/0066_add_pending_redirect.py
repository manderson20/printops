"""remember where a printer says it has moved, without moving it

Revision ID: 0066
Revises: 0065
Create Date: 2026-08-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: Union[str, Sequence[str], None] = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    A device switched to TLS-only IPP answers its old address with a redirect
    naming where it now lives, and PrintOps adopts that — because CUPS cannot
    follow a redirect, so a printer that only answers after one is a printer
    nothing can print to (app/printers/discovery.py).

    Adopting the port, scheme and path is routing: the same device, reached
    differently. Adopting the *host* is not. The host is which device this is,
    and taking a machine's word for that means PrintOps could start sending
    documents — including held ones waiting at a release printer — to a box
    nobody chose, on the say-so of the box it replaced. A misconfigured device
    or a recycled DHCP address is enough to do it, and everything would look
    healthy while paper came out somewhere else.

    So a redirect to a different host is recorded here rather than acted on,
    and an admin confirms it. The device gets to say it moved; it does not get
    to move itself.
    """
    op.add_column("printers", sa.Column("pending_redirect", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "pending_redirect")
