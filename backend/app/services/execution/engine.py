"""Approved changes, executed (Phase 20, §21, §51).

What this module is
    The only thing that calls a marketplace on our behalf. A proposal that a
    human approved becomes one `ExecutionJob` row and one sequence of connector
    calls: validate, write, re-read to confirm, record. Nothing else in `app/`
    is allowed to update a listing, which is what makes §21's "only approved
    operations execute" a property of the codebase rather than of a route.

Why refusals and failures are different shapes
    A refusal — not approved, already executed, no product to act on — is an
    exception with no row, because nothing was attempted and a job row would
    record work that never began. A failure after dispatch is a row: §21 asks
    that the error be preserved, the failure recorded, the affected change
    identified and recovery instructions given, and a row is the only shape
    that keeps all four after the process exits.

Why verification re-reads rather than trusting the write
    `update_product` returning the new state proves only that the adapter
    believes it. §21's post-execution verification asks the marketplace what
    the value *is*, so a write that was accepted and then normalized, clamped
    or dropped is caught here — and, because the marketplace may be eventually
    consistent, a mismatch is retried within the same bounded attempt budget
    rather than reported on the first read.

Why rollback is automatic when it exists
    A failed write leaves the marketplace somewhere between the old state and
    the new one. §51 says never to promise universal rollback, so the job
    records `rollback_supported` from the adapter's own capability, restores
    the state captured *before* our first write when it can, and says in
    `recovery_instructions` when it cannot. Attempts are bounded by the model's
    own `attempt_count <= max_attempts` check, so no path here can loop.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ConnectorError, MarketplaceConnector
from app.core.logging import get_logger
from app.db.base import TenantContext, utcnow
from app.models.catalog import MarketplaceProduct
from app.models.platform import MarketplaceConnection
from app.models.reconciliation import (
    ChangeProposal,
    ExecutionJob,
    ExecutionStatus,
    ProposalStatus,
)
from app.services.audit import audit_log_for
from app.services.reconciliation.repositories import (
    AuditLogRepository,
    ExecutionJobRepository,
    MarketplaceProductRepository,
)

logger = get_logger(__name__)

OPERATION = "update_product"
DEFAULT_MAX_ATTEMPTS = 3


class ExecutionRefusedError(Exception):
    """Nothing was attempted: no connector call, no job row."""


class VerificationError(Exception):
    """The write was accepted but the marketplace does not read back our value."""

    retryable = True


async def execute_proposal(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    proposal: ChangeProposal,
    connection: MarketplaceConnection,
    connector: MarketplaceConnector,
    approval_request_id: UUID | None = None,
    actor_id: UUID | None = None,
    actor_email: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> ExecutionJob:
    """Execute one approved proposal against one marketplace.

    Returns the job in whatever state it ended in — `SUCCEEDED`, `FAILED`,
    `ROLLED_BACK` or `PARTIALLY_FAILED` — because a failure is an answer a
    caller must handle rather than an exception to swallow. It raises
    `ExecutionRefusedError` only for the cases where no job exists to return.
    """
    if proposal.status is not ProposalStatus.APPROVED:
        raise ExecutionRefusedError(
            f"{proposal.change_ref} is {proposal.status.value}; "
            "only an approved proposal may execute"
        )
    if connector.marketplace != connection.marketplace:
        # A mismatch cannot be recovered from and must never be guessed at:
        # dispatching to the wrong marketplace is the failure this check exists
        # to make impossible.
        raise ValueError(
            f"connector {connector.marketplace!r} does not match connection "
            f"{connection.marketplace!r}"
        )
    if proposal.product_id is None:
        raise ExecutionRefusedError(
            f"{proposal.change_ref} targets no product, so there is nothing "
            "to update on a marketplace"
        )

    already = await ExecutionJobRepository(db, tenant).list(
        where=(ExecutionJob.change_proposal_id == proposal.id)
        & (ExecutionJob.status == ExecutionStatus.SUCCEEDED)
    )
    if already:
        raise ExecutionRefusedError(
            f"{proposal.change_ref} has already executed successfully "
            f"(job {already[0].id}); executing again would apply the change twice"
        )

    moment = now or utcnow()
    job = await ExecutionJobRepository(db, tenant).add(
        ExecutionJob(
            change_proposal_id=proposal.id,
            approval_request_id=approval_request_id,
            connector=connection.marketplace,
            operation=OPERATION,
            status=ExecutionStatus.PENDING,
            attempt_count=0,
            max_attempts=max_attempts,
            rollback_supported=bool(connector.supports_rollback),
            started_at=moment,
        )
    )

    listings = await _listing_for(
        db, tenant, proposal=proposal, connection=connection
    )
    problems = _pre_execution_check(
        proposal=proposal,
        connection=connection,
        connector=connector,
        has_listing=bool(listings),
    )
    if problems:
        job.status = ExecutionStatus.FAILED
        job.completed_at = utcnow()
        job.error_message = "; ".join(problems)
        job.recovery_instructions = _recovery(
            job, kind="validation", problems=problems, previous_written=False
        )
        await _audit(
            db,
            tenant,
            job=job,
            proposal=proposal,
            approval_request_id=approval_request_id,
            actor_id=actor_id,
            actor_email=actor_email,
            previous=None,
            moment=moment,
        )
        logger.warning(
            "execution_refused_pre_execution",
            job_id=str(job.id),
            change_ref=proposal.change_ref,
            problems=len(problems),
        )
        return job

    target = listings[0].external_product_id
    job.marketplace_response = {
        "product_id": target,
        "field": proposal.field_name,
        "value": _jsonable(proposal.proposed_value),
    }

    previous: dict[str, Any] | None = None
    last_error: str | None = None

    for attempt in range(1, job.max_attempts + 1):
        job.attempt_count = attempt
        job.status = ExecutionStatus.RUNNING
        await db.flush()
        try:
            if previous is None:
                before = await connector.get_product(target)
                previous = dict(before.attributes)
                job.previous_state = previous
                if connector.supports_rollback:
                    job.rollback_supported = True
                    job.rollback_reference = f"{connection.marketplace}:{target}"

            await connector.update_product(
                target, {proposal.field_name: proposal.proposed_value}
            )

            after = await connector.get_product(target)
            actual = after.attributes.get(proposal.field_name)
            if actual != proposal.proposed_value:
                raise VerificationError(
                    f"{proposal.field_name} reads {actual!r} after the write, "
                    f"expected {proposal.proposed_value!r}"
                )

            job.status = ExecutionStatus.SUCCEEDED
            job.completed_at = utcnow()
            job.error_message = None
            job.marketplace_response = {
                **(job.marketplace_response or {}),
                "verified_value": _jsonable(actual),
                "attempts": attempt,
            }
            await _audit(
                db,
                tenant,
                job=job,
                proposal=proposal,
                approval_request_id=approval_request_id,
                actor_id=actor_id,
                actor_email=actor_email,
                previous=previous,
                moment=moment,
            )
            logger.info(
                "execution_succeeded",
                job_id=str(job.id),
                change_ref=proposal.change_ref,
                connector=connection.marketplace,
                attempts=attempt,
            )
            return job
        except ConnectorError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if not exc.retryable:
                # Retrying rejected credentials or an unknown id spends attempts
                # on an answer that will not change. §21: controlled retries.
                break
        except VerificationError as exc:
            # Worth another attempt inside the same budget: a marketplace that
            # is briefly behind our own write settles, and one that will not
            # settle exhausts `max_attempts` like any other persistent failure.
            last_error = str(exc)

    job.error_message = last_error or "execution failed without a recorded error"
    job.completed_at = utcnow()
    status = ExecutionStatus.FAILED

    if previous is not None and connector.supports_rollback:
        try:
            await connector.rollback(target, previous)
            status = ExecutionStatus.ROLLED_BACK
        except ConnectorError as exc:
            status = ExecutionStatus.PARTIALLY_FAILED
            job.error_message = f"{job.error_message}; rollback failed: {type(exc).__name__}: {exc}"

    job.status = status
    job.recovery_instructions = _recovery(
        job, kind=status.value, problems=[], previous_written=previous is not None
    )
    await _audit(
        db,
        tenant,
        job=job,
        proposal=proposal,
        approval_request_id=approval_request_id,
        actor_id=actor_id,
        actor_email=actor_email,
        previous=previous,
        moment=moment,
    )
    logger.warning(
        "execution_failed",
        job_id=str(job.id),
        change_ref=proposal.change_ref,
        connector=connection.marketplace,
        status=job.status.value,
        attempts=job.attempt_count,
    )
    return job


def _pre_execution_check(
    *,
    proposal: ChangeProposal,
    connection: MarketplaceConnection,
    connector: MarketplaceConnector,
    has_listing: bool,
) -> list[str]:
    """Everything that must hold before the first connector call (§21).

    Returned as a list rather than raised: each problem is a thing an operator
    can fix, and showing all of them beats finding them one dispatch at a time.
    """
    problems: list[str] = []
    if proposal.validation_status != "validated":
        problems.append(
            f"validation status is {proposal.validation_status!r}, not "
            "'validated'; re-run the sandbox for this proposal"
        )
    if not (proposal.field_name or "").strip():
        problems.append("the proposal has no field to update")
    if proposal.proposed_value is None:
        problems.append("the proposal has no value to write")
    if not has_listing:
        problems.append(
            f"no {connection.marketplace!r} listing exists for product "
            f"{proposal.product_id}; sync the product to that marketplace first"
        )
    if connector.marketplace != connection.marketplace:
        problems.append(
            f"connector {connector.marketplace!r} does not serve "
            f"{connection.marketplace!r}"
        )
    return problems


async def _listing_for(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    proposal: ChangeProposal,
    connection: MarketplaceConnection,
) -> list[MarketplaceProduct]:
    """The marketplace row that says where this product lives there.

    `marketplace_products.external_product_id` is the only mapping from our
    product to a marketplace's id, so its absence is not a coding error — it is
    a listing that has not been made yet, which an operator can fix.
    """
    return list(
        await MarketplaceProductRepository(db, tenant).list(
            where=(MarketplaceProduct.product_id == proposal.product_id)
            & (MarketplaceProduct.marketplace == connection.marketplace)
        )
    )


async def _audit(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    job: ExecutionJob,
    proposal: ChangeProposal,
    approval_request_id: UUID | None,
    actor_id: UUID | None,
    actor_email: str | None,
    previous: Mapping[str, Any] | None,
    moment: datetime,
) -> None:
    """One immutable record of what execution did (§39).

    Written for every outcome, including a pre-execution refusal: "we checked
    and did not act" is exactly the kind of thing that is denied later.
    """
    audit = audit_log_for(
        workspace_id=tenant.workspace_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=f"execution.{job.status.value}",
        entity_type="execution_job",
        entity_id=job.id,
        created_at=moment,
        old_value=dict(previous) if previous is not None else None,
        new_value={
            "status": str(job.status),
            "connector": job.connector,
            "operation": job.operation,
            "attempts": job.attempt_count,
            "error": job.error_message,
            "marketplace_response": job.marketplace_response,
        },
        reason=f"executed approved change {proposal.change_ref}",
        evidence=list(proposal.evidence),
        risk_level=proposal.risk_level,
        approval_reference=approval_request_id,
    )
    await AuditLogRepository(db, tenant).add(audit)


def _recovery(
    job: ExecutionJob,
    *,
    kind: str,
    problems: list[str],
    previous_written: bool,
) -> str:
    """What a person should do next (§21: provide recovery instructions).

    Written per outcome rather than as one generic apology, because "your
    credentials were rejected" and "rollback also failed" need different next
    steps, and a message that fits both helps with neither. The attempt budget
    is repeated in every failure: §21 forbids pretending a retry is free.
    """
    target = job.rollback_reference or "the marketplace product"
    budget = (
        f"{job.attempt_count} of {job.max_attempts} attempt(s) made; retries "
        "are bounded by design"
    )
    if kind == "validation":
        return (
            "No marketplace call was made and nothing changed. Fix each problem "
            "listed above and dispatch again: "
            + "; ".join(problems)
        )
    if kind == str(ExecutionStatus.ROLLED_BACK):
        return (
            f"The change to {target} was restored to the state observed before "
            f"the attempt, so the marketplace holds no partial edit ({budget}). "
            "Correct the proposal (or the marketplace) and dispatch again."
        )
    if kind == str(ExecutionStatus.PARTIALLY_FAILED):
        return (
            f"The change to {target} failed and rollback failed too: the "
            "marketplace may hold a partial edit. Inspect that product on the "
            "marketplace before retrying; the last observed state is in "
            f"`previous_state` ({budget})."
        )
    if not previous_written:
        return (
            f"The failure happened before any write, so {target} still holds "
            f"its previous state. {job.error_message}; {budget}. Fix the cause "
            "and dispatch again."
        )
    if not job.rollback_supported:
        return (
            f"This connector does not support rollback, so the last observed "
            f"state was not restored. Inspect {target} on the marketplace; if "
            "the change landed, apply the value in `previous_state` manually "
            f"or raise a corrective proposal ({budget})."
        )
    return (
        f"{job.error_message}; {budget}. Fix the cause on the marketplace, then "
        "dispatch again."
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
