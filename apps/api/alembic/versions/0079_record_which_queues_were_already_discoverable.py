"""record which queues were already discoverable

Revision ID: 0079
Revises: 0078
Create Date: 2026-09-06 16:10:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0079"
down_revision: Union[str, Sequence[str], None] = "0078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    `airprint_enabled` has never done anything. It writes a static Avahi
    service file per printer, which works — but cupsd was separately
    advertising every shared queue over DNS-SD on its own
    (`BrowseLocalProtocols dnssd`), and every PrintOps queue is shared because
    CUPS refuses network job submission to one that isn't. So the Printers page
    reported "Queue discovery: Hidden" for all 53 printers while a browse of
    `_ipp._tcp` returned 108 records, every one of them a PrintOps queue. See
    #110.

    0.75.2 stops cupsd doing that, which finally makes the toggle mean
    something. That is the point of the change and also its hazard: every row
    in this table says false, so honouring the column without touching the data
    would take all 53 printers off the network at once — an outage nobody asked
    for, arriving inside a change described as fixing a label.

    So this records what was already true. Every existing printer *was*
    discoverable, so every existing printer is marked discoverable, and the
    column starts telling the truth about the state the estate is actually in
    rather than resetting it. Nobody loses a printer from their Add Printer
    picker on the deploy, and an admin who now wants one hidden has, for the
    first time, a switch that does it.

    Deliberately not changing the column default. A newly added printer should
    still start hidden until someone opts in — that policy was always right,
    it just never had any effect on the ones already here. This backfills
    history; it does not change what happens next.

    Archived printers are excluded, and an earlier version of this migration
    had that wrong on a wrong premise — that their queues were still on the box
    being advertised. They are not: archiving removes the CUPS queue and
    scripts/remove_cups_queue.sh deletes the Avahi service file with it. An
    archived printer is not discoverable, so marking it discoverable would
    record something untrue, and unarchiving it later would then quietly
    publish a printer the admin never asked to publish. There are none on this
    estate today, which is exactly why it is worth getting right now rather
    than discovering it the first time somebody unarchives something.
    """
    op.execute("UPDATE printers SET airprint_enabled = true WHERE archived_at IS NULL")


def downgrade() -> None:
    """Downgrade schema.

    Restores the column to the state every row was in before this ran. That is
    lossy in principle — an admin who turns a printer off after upgrading would
    have that choice discarded — but there is nothing to preserve it in, and
    the alternative is a downgrade that leaves a column this migration is
    entirely about in a state it never had. Downgrading also implies restoring
    cupsd's blanket advertisement, at which point the column means nothing
    again either way.

    Scoped to the same rows the upgrade touched, so an archived printer that
    was already false is not "restored" to a value it never left.
    """
    op.execute("UPDATE printers SET airprint_enabled = false WHERE archived_at IS NULL")
