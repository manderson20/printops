"""Running a copier user-sync in the background, with progress.

A sync is one HTTP round trip per account against a device that allows a
single admin session, so a few hundred accounts takes minutes. As a
blocking request that is indistinguishable from a hang — the browser shows
a spinner and an admin has no way to tell whether anything is happening.
So the request starts a job and returns immediately, and the job reports
progress as it goes.

The job also owns the provisioning record: which person ended up on which
account number. The device cannot be asked this afterwards (it never
reveals an account's password), so if it is not written at the moment of
the write it is lost — and the next sync re-adds everyone and fails.
"""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.copiers.connector import CapabilityNotSupported
from app.copiers.device_admin import DeviceCredentialsMissing
from app.copiers.provisioning import build_provisioning_plan
from app.copiers.registry import get_connector
from app.db import AsyncSessionLocal
from app.models.copier_provisioning import CopierProvisionedAccount, CopierSyncJob
from app.models.mfp_device import MfpDevice

logger = logging.getLogger(__name__)

# Progress is written to the DB so the UI can poll it, but not on every
# single account — that would be one UPDATE per account on top of the
# device round trip. Every few accounts is frequent enough to look live.
PROGRESS_WRITE_EVERY = 5


async def provisioned_identity_values(db, device_id: UUID) -> set[str]:
    """Identity values PrintOps has already put on this device and not
    since removed."""
    result = await db.execute(
        select(CopierProvisionedAccount.identity_value).where(
            CopierProvisionedAccount.mfp_device_id == device_id,
            CopierProvisionedAccount.removed_at.is_(None),
        )
    )
    return {value for (value,) in result.all()}


async def run_sync_job(job_id: UUID, rewrite: bool = False) -> None:
    """Execute a queued job. Owns its own DB session because it outlives
    the request that created it."""
    async with AsyncSessionLocal() as db:
        job = await db.get(CopierSyncJob, job_id)
        if job is None:
            return
        device = await db.get(MfpDevice, job.mfp_device_id)
        if device is None:
            job.status = "failed"
            job.message = "The copier was deleted before the sync started."
            job.finished_at = datetime.now(UTC)
            await db.commit()
            return

        job.status = "running"
        job.started_at = datetime.now(UTC)
        await db.commit()

        try:
            plan = await build_provisioning_plan(db, device)
            seen = set() if rewrite else await provisioned_identity_values(db, device.id)
            job.total = len([i for i in plan.identities if i.identity_value not in seen])
            job.skipped = len(plan.identities) - job.total
            await db.commit()

            async def on_progress(done: int, total: int, synced: int, failed: int) -> None:
                if done % PROGRESS_WRITE_EVERY and done != total:
                    return
                job.completed = synced
                job.failed = failed
                await db.commit()

            connector = get_connector(device.connector_type)
            result = await connector.sync_users_to_device(
                device,
                plan.identities,
                already_provisioned=seen,
                on_progress=on_progress,
                rewrite=rewrite,
            )
        except (CapabilityNotSupported, DeviceCredentialsMissing) as exc:
            # Setup problems with a fix a person can carry out — recorded as
            # the message, not swallowed into a generic failure.
            job.status = "failed"
            job.message = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Copier sync job %s failed", job_id)
            job.status = "failed"
            job.message = str(exc)[:500]
        else:
            now = datetime.now(UTC)
            if rewrite:
                # The rewrite authoritatively re-seats everyone, so the old
                # records no longer describe the device. Closed rather than
                # deleted — historic usage stays attributable to whoever
                # held the account at the time.
                for stale in (
                    (
                        await db.execute(
                            select(CopierProvisionedAccount).where(
                                CopierProvisionedAccount.mfp_device_id == device.id,
                                CopierProvisionedAccount.removed_at.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                ):
                    stale.removed_at = now
            for account in result.accounts:
                db.add(
                    CopierProvisionedAccount(
                        mfp_device_id=device.id,
                        staff_email=account.staff_email,
                        identity_value=account.identity_value,
                        identity_type=account.identity_type,
                        device_account_id=account.device_account_id,
                        device_account_name=account.device_account_name,
                        provisioned_at=now,
                    )
                )
            job.completed = result.synced_count
            job.failed = result.failed_count
            job.skipped = result.skipped_count
            job.message = result.message
            job.status = "succeeded" if result.failed_count == 0 else "failed"
            device.last_user_sync_at = now
            device.last_user_sync_ok = result.failed_count == 0
            device.last_user_sync_message = result.message
        finally:
            job.finished_at = datetime.now(UTC)
            await db.commit()


async def create_sync_job(db, device: MfpDevice, trigger: str = "manual") -> CopierSyncJob | None:
    """Create the job row, or return None if one is already in flight for
    this device. Split from start_sync_job so the scheduled loop can await
    the run instead of spawning it — the loop wants devices handled one at
    a time."""
    existing = (
        (
            await db.execute(
                select(CopierSyncJob).where(
                    CopierSyncJob.mfp_device_id == device.id,
                    CopierSyncJob.status.in_(("pending", "running")),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return None
    job = CopierSyncJob(mfp_device_id=device.id, status="pending", trigger=trigger)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def start_sync_job(
    db, device: MfpDevice, trigger: str = "manual", rewrite: bool = False
) -> CopierSyncJob:
    """Queue a sync and hand back the job immediately.

    Refuses to queue a second job for a device that already has one in
    flight: the device only has one admin session, so a second run would
    simply block on the lock and then find everything already done —
    confusing rather than useful."""
    existing = (
        (
            await db.execute(
                select(CopierSyncJob).where(
                    CopierSyncJob.mfp_device_id == device.id,
                    CopierSyncJob.status.in_(("pending", "running")),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    job = CopierSyncJob(mfp_device_id=device.id, status="pending", trigger=trigger)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    asyncio.create_task(run_sync_job(job.id, rewrite=rewrite))
    return job
