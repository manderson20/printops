"""Choosing which staff get accounts on a given copier.

Separate from the connectors on purpose: *who* should be on a device is a
vendor-neutral question (org-wide copier-accounting OUs, narrowed by the
device's own scope), while *how* to put them there is the vendor-specific
part. Keeping the selection here means every connector provisions the same
set, and the preview an admin sees before pressing the button is computed
by the same code that the sync then uses — not a second implementation
that can drift.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.copiers.device_admin import device_provisioning_scope
from app.integrations.google_workspace import (
    org_unit_included,
    resolve_copier_identity_org_units,
)
from app.models.google_workspace import GoogleWorkspaceSettings, GoogleWorkspaceUser
from app.models.mfp_device import MfpDevice
from app.models.staff_copier_identity import StaffCopierIdentity


@dataclass
class ProvisioningPlan:
    """What a sync would do, so it can be shown before it is done."""

    identities: list[StaffCopierIdentity]
    org_unit_paths: list[str]
    excluded_org_unit_paths: list[str]
    skipped_no_org_unit: int = 0

    @property
    def count(self) -> int:
        return len(self.identities)


async def build_provisioning_plan(
    db: AsyncSession, device: MfpDevice, identity_type: str | None = None
) -> ProvisioningPlan:
    """The staff who should have an account on `device`.

    Resolution order — org-wide copier-accounting OUs, narrowed by the
    device's own provision_org_unit_paths, then each identity matched to a
    synced directory user to read their OU. An identity whose email isn't
    in the directory is skipped rather than guessed at: provisioning a code
    PrintOps can't attribute to a real person would produce usage records
    nobody can resolve."""
    settings = (
        await db.execute(select(GoogleWorkspaceSettings).limit(1))
    ).scalar_one_or_none() or GoogleWorkspaceSettings()

    org_includes, org_excludes = resolve_copier_identity_org_units(settings)
    includes, excludes = device_provisioning_scope(device, org_includes, org_excludes)

    wanted_type = identity_type or settings.auto_copier_identity_type
    result = await db.execute(
        select(StaffCopierIdentity)
        .where(StaffCopierIdentity.identity_type == wanted_type)
        .order_by(StaffCopierIdentity.staff_email)
    )
    identities = list(result.scalars().all())

    ou_result = await db.execute(
        select(GoogleWorkspaceUser.email, GoogleWorkspaceUser.org_unit_path)
    )
    org_unit_by_email = {email.lower(): path for email, path in ou_result.all()}

    in_scope: list[StaffCopierIdentity] = []
    skipped = 0
    for identity in identities:
        org_unit = org_unit_by_email.get((identity.staff_email or "").lower())
        if org_unit is None:
            skipped += 1
            continue
        if not org_unit_included(org_unit, includes, excludes):
            continue
        in_scope.append(identity)

    # A code held by two people can't be attributed either way, so no
    # holder of it is pushed. This has to be decided after the whole
    # in-scope set is known, not while walking it: skipping duplicates as
    # they appear would still push the first holder, leaving both able to
    # log in with the code while every page it produces is credited to
    # whoever sorted first — the precise failure the rule exists to
    # prevent, and one that would quietly corrupt per-person cost reports.
    holders = Counter(identity.identity_value for identity in in_scope)
    selected = [identity for identity in in_scope if holders[identity.identity_value] == 1]

    return ProvisioningPlan(
        identities=selected,
        org_unit_paths=includes,
        excluded_org_unit_paths=excludes,
        skipped_no_org_unit=skipped,
    )
