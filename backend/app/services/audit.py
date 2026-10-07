"""Immutable audit records (Section 39, D-013).

The first writer of `audit_logs`. Everything a reviewer must later be able to
answer — who, what, when, why, the evidence, the old and new value, the risk
and the approval it belongs to — goes in one row, and the row carries a hash of
its own contents so a silent edit is detectable by recomputing it.

Immutability itself is not this module's job: the table has no `updated_at`, no
ORM path updates it, and a `BEFORE UPDATE`/`BEFORE DELETE` trigger in migration
0001 revokes both at the database. This module only makes sure there is
something worth not being able to edit.

The hash covers the payload, not a chain — `audit_logs` has no previous-hash
column, so "tamper evident" here means *recomputable*, not *linked*. Adding the
link needs a migration and is recorded as a gap rather than pretended at.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Any
from uuid import UUID

from app.models.reconciliation import AuditLog, RiskLevel


def _jsonable(value: Any) -> Any:
    """Reduce a payload to what survives a JSON round trip, deterministically."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def payload_hash(payload: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of `payload`.

    Sorted keys and fixed separators, so the same facts in a different key
    order hash the same. This is the function a later reader uses to check a
    row against itself.
    """
    canonical = json.dumps(
        _jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def audit_log_for(
    *,
    workspace_id: UUID,
    action: str,
    entity_type: str,
    entity_id: UUID | None,
    created_at: datetime,
    actor_id: UUID | None = None,
    actor_email: str | None = None,
    old_value: Any | None = None,
    new_value: Any | None = None,
    reason: str | None = None,
    evidence: list[Any] | None = None,
    risk_level: RiskLevel | None = None,
    approval_reference: UUID | None = None,
) -> AuditLog:
    """One immutable record of one thing that happened.

    Pure: `created_at` is a parameter so tests can assert the hash they
    recompute, rather than a clock they cannot pin down.
    """
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "actor_email": actor_email,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
        "evidence": evidence,
        "risk_level": risk_level,
        "approval_reference": approval_reference,
        "created_at": created_at,
    }
    return AuditLog(
        workspace_id=workspace_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=_jsonable(old_value),
        new_value=_jsonable(new_value),
        reason=reason,
        evidence=_jsonable(evidence) if evidence is not None else None,
        risk_level=risk_level,
        approval_reference=approval_reference,
        created_at=created_at,
        payload_hash=payload_hash(payload),
    )


def recomputed_hash(log: AuditLog) -> str:
    """The hash a reader computes from a stored row, for comparison.

    Deliberately reuses the stored columns rather than a saved copy of the
    payload: that is the only check that would notice an edit.
    """
    return payload_hash(
        {
            "workspace_id": log.workspace_id,
            "actor_id": log.actor_id,
            "actor_email": log.actor_email,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "reason": log.reason,
            "evidence": log.evidence,
            "risk_level": log.risk_level,
            "approval_reference": log.approval_reference,
            "created_at": log.created_at,
        }
    )


__all__ = ["audit_log_for", "payload_hash", "recomputed_hash"]
