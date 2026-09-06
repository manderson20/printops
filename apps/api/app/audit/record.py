"""Recording an admin action.

One function, called from the endpoint that made the change, writing into that
endpoint's own session. The endpoint's existing `await db.commit()` then commits
the change and the audit row together.

That shared transaction is the entire point and the reason this is not
middleware. An audit log that can record a change that did not happen, or miss
one that did, is not an audit log — and middleware runs after the response, long
after the endpoint committed. Endpoints here commit explicitly and often (27
separate commits in routers/printers.py alone), so there is no single
post-request moment that is still inside the transaction. Calling into the
session the endpoint already holds is the only place that is.

The consequences are worth stating because they are features:

- An endpoint that records an event and then raises records nothing. Correct:
  nothing changed.
- An endpoint that changes something and forgets to call this records nothing,
  silently. That is the real failure mode, and it is why
  tests/test_audit_coverage.py exists rather than trusting anyone to remember.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import client_key
from app.models.audit import AuditEvent
from app.schemas.auth import UserOut

# What a redacted value is shown as. Deliberately not the real length, not a
# hash, not a prefix — any of those leak something about a secret, and the fact
# that it changed is the whole of what an audit reader needs.
REDACTED = "***"

# Fields whose values must never reach the audit table, matched case-insensitively
# against the field name at any call site. This is a backstop, not the primary
# mechanism: call sites pass an explicit list of fields to diff, so a secret has
# to be named twice to leak. It exists because the primary mechanism is a human
# remembering, and this one is not.
SECRET_FIELD_MARKERS = (
    "password",
    "secret",
    "token",
    "credential",
    "community",  # SNMP community strings are passwords wearing another name
    "api_key",
    "apikey",
    "private_key",
    "client_secret",
    "passphrase",
    # Every secret column in this codebase is stored as ciphertext or a digest
    # and named for it — access_token_encrypted, ldap_bind_password_hash,
    # snmp_community_encrypted. That suffix is a more reliable marker than
    # guessing at nouns, and it catches a future column whose name nobody
    # thought to add above. Diffing ciphertext would be noise even if it were
    # safe.
    "encrypted",
    "_hash",
)


def _is_secret(field: str) -> bool:
    lowered = field.lower()
    return any(marker in lowered for marker in SECRET_FIELD_MARKERS)


def _scalar(value: Any) -> Any:
    """JSON-safe, and stable enough to compare across a request boundary.

    The `changes` column is JSON, so a UUID or a datetime coming off a model
    would fail to serialise at flush time — inside the caller's transaction,
    turning an audit bug into a failed admin action. Stringifying here means the
    worst case is an ugly diff rather than a rolled-back settings save.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple):
        return [_scalar(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _scalar(v) for k, v in value.items()}
    return str(value)


def diff(before: dict[str, Any], after: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """What actually changed, over an explicit field list.

    Explicit rather than "every attribute on the model" so that adding a column
    does not silently start logging it — which for a settings table is how a
    credential ends up in an audit row. A field the caller did not name is not
    recorded at all.

    Unchanged fields are dropped: a settings save that touched one checkbox
    should read as one line, not forty identical ones.
    """
    changed: dict[str, Any] = {}
    for field in fields:
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if _is_secret(field):
            changed[field] = {"from": REDACTED, "to": REDACTED}
        else:
            changed[field] = {"from": _scalar(old), "to": _scalar(new)}
    return changed


def record_audit(
    db: AsyncSession,
    actor: UserOut,
    *,
    action: str,
    summary: str,
    entity_type: str | None = None,
    entity_id: str | UUID | None = None,
    entity_label: str | None = None,
    changes: dict[str, Any] | None = None,
    request: Any = None,
) -> AuditEvent:
    """Stage an audit row in the caller's session. Does not commit.

    Returns the row mostly so tests can assert on it; callers normally ignore it
    and let their own commit carry it.
    """
    # Redacted defensively even though diff() already did it — a caller can
    # hand-build a changes dict, and this is the last point before the value is
    # durable.
    safe_changes = None
    if changes:
        safe_changes = {
            field: ({"from": REDACTED, "to": REDACTED} if _is_secret(field) else _scalar(value))
            for field, value in changes.items()
        }

    event = AuditEvent(
        # The dev break-glass admin has no User row, so there is nothing to
        # point the FK at; actor_email carries the identity in that case, same
        # as ImpersonationSession does.
        actor_user_id=_actor_user_id(actor),
        actor_email=actor.email or actor.username,
        actor_role=actor.role,
        impersonated_by=actor.impersonated_by,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        entity_label=entity_label,
        summary=summary,
        changes=safe_changes or None,
        source_ip=client_key(request) if request is not None else None,
    )
    db.add(event)
    return event


def _actor_user_id(actor: UserOut) -> UUID | None:
    """`subject` is the JWT sub: a user UUID for real accounts, the configured
    dev username for the break-glass admin. Anything that is not a UUID belongs
    in actor_email only."""
    try:
        return UUID(str(actor.subject))
    except (ValueError, TypeError):
        return None


# Never worth recording a change to: surrogate keys and the timestamps that
# change on every write regardless.
NON_AUDITABLE_COLUMNS = frozenset({"id", "created_at", "updated_at"})


def auditable_fields(model: Any, exclude: frozenset[str] = frozenset()) -> list[str]:
    """Every column on a model worth diffing.

    Derived from the mapper minus an explicit exclusion set — never the other
    way round. An allow-list is the more careful-looking option and is exactly
    wrong for an audit log: a field left off one is not audited, silently and
    forever, and the failure is invisible because the log simply never mentions
    that kind of change.

    Caught in review on the first version of this. A hand-written list for
    Printer omitted the stored SNMP community, the LDAP bind password and the
    web-login password among others, so rotating a credential on a printer
    produced no event at all. Inverting it makes the default for a new column
    "audited", and forgetting produces noise rather than a hole.

    `exclude` is for state the machine writes on its own — poll results, probe
    errors, page counters. Diffing those buries an admin's edit under churn
    nobody decided on.
    """
    skip = NON_AUDITABLE_COLUMNS | exclude
    return [column.key for column in model.__mapper__.columns if column.key not in skip]


def snapshot(obj: Any, fields: list[str] | None = None) -> dict[str, Any]:
    """The current values of an object's auditable fields.

    Taken before and after a change so diff() can compare them. Call the first
    one *before* mutating: SQLAlchemy hands out live objects, so a snapshot
    taken afterwards is the same values twice and the audit row says nothing
    changed.
    """
    names = fields if fields is not None else auditable_fields(type(obj))
    return {name: getattr(obj, name, None) for name in names}


def record_settings_update(
    db: AsyncSession,
    actor: UserOut,
    *,
    area: str,
    label: str,
    obj: Any,
    before: dict[str, Any],
    request: Any = None,
) -> AuditEvent | None:
    """Record a settings change, or nothing if nothing changed.

    Returns None on a no-op save, which is the common case when someone opens a
    settings page and presses Save without editing. Recording those would fill
    the log with rows that say "changed nothing" and make the real changes
    harder to find.
    """
    fields = auditable_fields(type(obj))
    changes = diff(before, snapshot(obj, fields), fields)
    if not changes:
        return None
    return record_audit(
        db,
        actor,
        action=f"settings.{area}.update",
        summary=f"Updated {label} settings",
        entity_type=f"settings.{area}",
        changes=changes,
        request=request,
    )
