"""Execution engine tests (Phase 20, §21, §51).

The §64 exit criteria as assertions: only approved operations execute, every
execution is verified by re-reading the marketplace, attempts are bounded, and
rollback is offered or refused according to what the connector says — with the
recovery instructions §21 asks for written per outcome.

Offline, against `MockConnector` and a fake session. That is the state D-005
put the project in: the engine's behaviour is proven, and no marketplace has
ever been written to.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.connectors import (
    ConnectorAuthError,
    ConnectorRateLimitError,
    ConnectorTransientError,
    MockConnector,
)
from app.db.base import TenantContext
from app.models.catalog import MarketplaceProduct
from app.models.platform import MarketplaceConnection
from app.models.reconciliation import (
    AuditLog,
    ChangeProposal,
    ExecutionJob,
    ExecutionStatus,
    ProposalStatus,
    RiskLevel,
)
from app.services.audit import recomputed_hash
from app.services.execution import ExecutionRefusedError, execute_proposal
from app.services.reconciliation.repositories import (
    ExecutionJobRepository,
    MarketplaceProductRepository,
)
from tests.unit.fakes import fake_session, written

WORKSPACE = uuid4()
PRODUCT = uuid4()
CREDENTIALS = {"api_key": "mock-key-0001"}
PRODUCTS = {"p-1": {"title": "Blue Mug", "price": 12}}


class AlwaysRateLimited(MockConnector):
    """Every write hits the limit, however many times it is attempted."""

    async def update_product(self, product_id: str, fields: Any) -> Any:
        self.calls.append("update_product")
        raise ConnectorRateLimitError("slow down", marketplace="mock")


class RateLimitedWithBrokenRollback(AlwaysRateLimited):
    async def rollback(self, product_id: str, previous_state: Any) -> Any:
        self.calls.append("rollback")
        raise ConnectorTransientError("rollback endpoint 500", marketplace="mock")


class NormalisingMarketplace(MockConnector):
    """Accepts the write, stores its own version of the value."""

    async def update_product(self, product_id: str, fields: Any) -> Any:
        normalised = {
            key: value.upper() if isinstance(value, str) else value
            for key, value in fields.items()
        }
        return await super().update_product(product_id, normalised)


def _proposal(**overrides: Any) -> ChangeProposal:
    values: dict[str, Any] = {
        "id": uuid4(),
        "workspace_id": WORKSPACE,
        "product_id": PRODUCT,
        "change_ref": "CHG-EXE-0001",
        "field_name": "title",
        "current_value": "Blue Mug",
        "proposed_value": "Navy Mug",
        "reason": "manufacturer states navy",
        "evidence": [{"source": "manufacturer", "value": "navy"}],
        "confidence": 0.9,
        "validation_status": "validated",
        "validation_errors": [],
        "risk_level": RiskLevel.LOW,
        "requires_approval": True,
        "status": ProposalStatus.APPROVED,
    }
    values.update(overrides)
    return ChangeProposal(**values)


def _connection() -> MarketplaceConnection:
    return MarketplaceConnection(
        workspace_id=WORKSPACE,
        marketplace="mock",
        display_name="Mock store",
        encrypted_credentials="not-a-real-blob",
    )


def _connector(**overrides: Any) -> MockConnector:
    return MockConnector(
        products=PRODUCTS, credentials=CREDENTIALS, **overrides
    )


async def _env(**overrides: Any) -> dict[str, Any]:
    session = fake_session()
    tenant = TenantContext(workspace_id=WORKSPACE, user_id=uuid4())
    if overrides.pop("listing", True):
        await MarketplaceProductRepository(session, tenant).add(
            MarketplaceProduct(
                workspace_id=WORKSPACE,
                product_id=PRODUCT,
                marketplace="mock",
                external_product_id="p-1",
            )
        )
    return {
        "session": session,
        "tenant": tenant,
        "proposal": overrides.pop("proposal", None) or _proposal(),
        "connection": overrides.pop("connection", None) or _connection(),
        "connector": overrides.pop("connector", None) or _connector(),
        **overrides,
    }


async def _execute(env: dict[str, Any], **kwargs: Any) -> ExecutionJob:
    return await execute_proposal(
        env["session"],
        env["tenant"],
        proposal=env["proposal"],
        connection=env["connection"],
        connector=env["connector"],
        **kwargs,
    )


def _rows(session: Any, kind: type) -> list[Any]:
    return [row for row in written(session) if isinstance(row, kind)]


class TestOnlyApprovedOperationsExecute:
    async def test_an_unapproved_proposal_is_refused_before_a_job_exists(self) -> None:
        env = await _env(proposal=_proposal(status=ProposalStatus.SIMULATED))

        with pytest.raises(ExecutionRefusedError, match="approved"):
            await _execute(env)

        assert _rows(env["session"], ExecutionJob) == []
        assert env["connector"].calls == []

    async def test_a_proposal_that_already_executed_is_refused(self) -> None:
        env = await _env()
        proposal = env["proposal"]
        await ExecutionJobRepository(env["session"], env["tenant"]).add(
            ExecutionJob(
                change_proposal_id=proposal.id,
                connector="mock",
                operation="update_product",
                status=ExecutionStatus.SUCCEEDED,
            )
        )

        with pytest.raises(ExecutionRefusedError, match="already executed"):
            await _execute(env)

        assert len(_rows(env["session"], ExecutionJob)) == 1, "no second job"
        assert env["connector"].calls == []

    async def test_a_proposal_with_no_product_is_refused(self) -> None:
        env = await _env(proposal=_proposal(product_id=None))

        with pytest.raises(ExecutionRefusedError, match="targets no product"):
            await _execute(env)

        assert _rows(env["session"], ExecutionJob) == []

    async def test_a_connector_for_another_marketplace_is_a_programming_error(
        self,
    ) -> None:
        env = await _env()
        connection = _connection()
        connection.marketplace = "somewhere_else"

        with pytest.raises(ValueError, match="does not match"):
            await execute_proposal(
                env["session"],
                env["tenant"],
                proposal=env["proposal"],
                connection=connection,
                connector=env["connector"],
            )

        assert _rows(env["session"], ExecutionJob) == []

    async def test_an_approved_proposal_executes_and_is_verified(self) -> None:
        env = await _env()
        approval_id = uuid4()

        job = await _execute(env, approval_request_id=approval_id)

        assert job.status is ExecutionStatus.SUCCEEDED
        assert job.attempt_count == 1
        assert job.connector == "mock"
        assert job.operation == "update_product"
        # The state observed before the write is what rollback would restore.
        assert job.previous_state["title"] == "Blue Mug"
        assert job.rollback_supported is True
        assert job.rollback_reference == "mock:p-1"
        assert job.marketplace_response["verified_value"] == "Navy Mug"
        assert job.recovery_instructions is None

        connector: MockConnector = env["connector"]
        # Read, write, read again: the verification is a second read, not the
        # write's own return value. Checked before this test adds its own call.
        assert connector.calls == ["get_product", "update_product", "get_product"]
        product = await connector.get_product("p-1")
        assert product.attributes["title"] == "Navy Mug"

        audits = _rows(env["session"], AuditLog)
        assert len(audits) == 1
        audit = audits[0]
        assert audit.action == "execution.succeeded"
        assert audit.entity_type == "execution_job"
        assert audit.entity_id == job.id
        assert audit.approval_reference == approval_id
        assert recomputed_hash(audit) == audit.payload_hash


class TestPreExecutionValidation:
    async def test_an_unvalidated_proposal_never_reaches_the_marketplace(
        self,
    ) -> None:
        env = await _env(proposal=_proposal(validation_status="pending"))

        job = await _execute(env)

        assert job.status is ExecutionStatus.FAILED
        assert job.attempt_count == 0
        assert "validation status is 'pending'" in (job.error_message or "")
        assert (job.recovery_instructions or "").startswith("No marketplace call")
        assert env["connector"].calls == []

    async def test_a_missing_listing_is_a_failure_with_instructions(self) -> None:
        env = await _env(listing=False)

        job = await _execute(env)

        assert job.status is ExecutionStatus.FAILED
        assert "no 'mock' listing exists" in (job.error_message or "")
        assert "sync the product" in (job.recovery_instructions or "")
        assert env["connector"].calls == []
        # Still a row: §21 wants the failure recorded even when nothing ran.
        assert len(_rows(env["session"], ExecutionJob)) == 1


class TestRetriesAreControlled:
    async def test_a_retryable_failure_is_retried_within_the_budget(self) -> None:
        env = await _env(
            connector=_connector(
                fail_once={"update_product": ConnectorRateLimitError("slow down")}
            )
        )

        job = await _execute(env, max_attempts=3)

        assert job.status is ExecutionStatus.SUCCEEDED
        assert job.attempt_count == 2
        assert env["connector"].calls.count("update_product") == 2

    async def test_a_non_retryable_failure_stops_after_one_attempt(self) -> None:
        env = await _env(
            connector=_connector(
                fail_once={"get_product": ConnectorAuthError("expired")}
            )
        )

        job = await _execute(env, max_attempts=3)

        assert job.status is ExecutionStatus.FAILED
        assert job.attempt_count == 1, "rejected credentials must not be retried"
        assert "ConnectorAuthError" in (job.error_message or "")
        assert "before any write" in (job.recovery_instructions or "")
        assert "rollback" not in env["connector"].calls

    async def test_attempts_stop_at_the_maximum(self) -> None:
        env = await _env(
            connector=AlwaysRateLimited(products=PRODUCTS, credentials=CREDENTIALS)
        )

        job = await _execute(env, max_attempts=2)

        assert job.status is ExecutionStatus.ROLLED_BACK
        assert job.attempt_count == 2, "bounded, never unbounded (§21)"
        assert "2 of 2 attempt(s)" in (job.recovery_instructions or "")

    async def test_a_failure_without_rollback_says_so(self) -> None:
        connector = AlwaysRateLimited(
            products=PRODUCTS, credentials=CREDENTIALS, supports_rollback=False
        )
        env = await _env(connector=connector)

        job = await _execute(env, max_attempts=1)

        assert job.status is ExecutionStatus.FAILED
        assert job.rollback_supported is False
        assert job.previous_state["title"] == "Blue Mug"
        assert "does not support rollback" in (job.recovery_instructions or "")
        assert "rollback" not in connector.calls

    async def test_a_value_the_marketplace_normalises_is_detected(self) -> None:
        connector = NormalisingMarketplace(products=PRODUCTS, credentials=CREDENTIALS)
        env = await _env(connector=connector)

        job = await _execute(env, max_attempts=2)

        assert job.status is ExecutionStatus.ROLLED_BACK
        assert "reads 'NAVY MUG'" in (job.error_message or "")
        # Restored to what was there before our write, not to ours.
        assert job.previous_state["title"] == "Blue Mug"
        product = await connector.get_product("p-1")
        assert product.attributes["title"] == "Blue Mug"

    async def test_a_failed_rollback_is_reported_as_partially_failed(self) -> None:
        connector = RateLimitedWithBrokenRollback(
            products=PRODUCTS, credentials=CREDENTIALS
        )
        env = await _env(connector=connector)

        job = await _execute(env, max_attempts=1)

        assert job.status is ExecutionStatus.PARTIALLY_FAILED
        assert "rollback failed" in (job.error_message or "")
        assert "rollback failed too" in (job.recovery_instructions or "")
        assert "inspect" in (job.recovery_instructions or "").lower()


class TestEveryOutcomeIsRecorded:
    async def test_a_refusal_leaves_an_audit_row_of_the_failure(self) -> None:
        env = await _env(listing=False)

        job = await _execute(env)

        audits = _rows(env["session"], AuditLog)
        assert len(audits) == 1
        audit = audits[0]
        assert audit.action == "execution.failed"
        assert audit.entity_id == job.id
        assert audit.new_value["status"] == str(ExecutionStatus.FAILED)
        assert recomputed_hash(audit) == audit.payload_hash

    async def test_a_rollback_is_recorded_with_what_was_restored(self) -> None:
        env = await _env(
            connector=AlwaysRateLimited(products=PRODUCTS, credentials=CREDENTIALS)
        )

        job = await _execute(env, max_attempts=1)

        assert job.status is ExecutionStatus.ROLLED_BACK
        audits = _rows(env["session"], AuditLog)
        assert len(audits) == 1
        assert audits[0].action == "execution.rolled_back"
        assert audits[0].old_value["title"] == "Blue Mug"
        assert recomputed_hash(audits[0]) == audits[0].payload_hash
