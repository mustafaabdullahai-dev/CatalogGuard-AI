"""Approval workflow tests (Phase 18, §15, §20, §38, §39).

The three exit criteria, each as an assertion: the risk level decides how many
approvers and never zero; the state machine refuses what it should before any
row exists; and every decision leaves an audit row whose hash the row itself
can be checked against.

Offline — a fake session, because the repositories only `list`, `add` and
`flush`. What that leaves unverified is whether Postgres accepts the rows, the
same caveat as every other phase.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

from app.db.base import TenantContext
from app.models.reconciliation import (
    ApprovalDecision,
    ChangeProposal,
    ProposalStatus,
    RiskLevel,
)
from app.services.approval import (
    AlreadyDecidedError,
    ApprovalError,
    NotApprovableError,
    approvable_reason,
    open_request,
    record_decision,
    required_approvals_for,
)
from app.services.audit import audit_log_for, payload_hash, recomputed_hash
from app.services.reconciliation.repositories import ApprovalRequestRepository

WORKSPACE = uuid4()
ACTOR_ONE = uuid4()
ACTOR_TWO = uuid4()


def _matches(row: Any, clause: Any) -> bool:
    """Evaluate the equality clauses these queries use, against one row.

    The workflow only ever asks `=`, `AND` and `OR`, so a small evaluator is
    enough — and it is what makes the fake able to answer "the request you
    already opened" instead of "nothing", which is the difference between
    testing the state machine and testing that it never reads.
    """
    if clause is None:
        return True
    if isinstance(clause, BooleanClauseList):
        results = [_matches(row, part) for part in clause.clauses]
        return all(results) if clause.operator is operators.and_ else any(results)
    if isinstance(clause, BinaryExpression) and clause.operator is operators.eq:
        return getattr(row, clause.left.key, object()) == clause.right.value
    if isinstance(clause, BinaryExpression) and clause.operator is operators.in_op:
        return getattr(row, clause.left.key, object()) in clause.right.value
    if isinstance(clause, BinaryExpression) and clause.operator is operators.is_:
        # The tenant scoping adds `deleted_at IS NULL`.
        wanted = clause.right.value if hasattr(clause.right, "value") else None
        return getattr(row, clause.left.key, object()) is wanted
    raise AssertionError(f"unhandled clause in fake session: {clause!r}")


def _session() -> MagicMock:
    """A session that stores what it is given and answers queries about it.

    Reads run against the rows `add` has received, filtered by the statement's
    own where clause. That is what lets one test follow a request from its
    creation to its second approval without a database.
    """
    session = MagicMock()
    session.rows: list[Any] = []

    def add(row: Any) -> Any:
        # Mimic the INSERT SQLAlchemy would do: the primary key default fires
        # at flush time, so a row the fake holds without one would be
        # indistinguishable from every other row to the queries below.
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        session.rows.append(row)
        return row

    session.add = MagicMock(side_effect=add)
    session.flush = AsyncMock(return_value=None)

    async def execute(statement: Any) -> MagicMock:
        entity = statement.column_descriptions[0]["entity"]
        clause = getattr(statement, "whereclause", None)
        matched = [
            row
            for row in session.rows
            if isinstance(row, entity) and _matches(row, clause)
        ]
        result = MagicMock()
        result.scalars.return_value = result
        result.all.return_value = matched
        result.scalar_one_or_none.return_value = matched[0] if matched else None
        return result

    session.execute = AsyncMock(side_effect=execute)
    return session


def _proposal(**overrides: Any) -> ChangeProposal:
    values: dict[str, Any] = {
        "id": uuid4(),
        "workspace_id": WORKSPACE,
        "change_ref": "CHG-APR-0001",
        "field_name": "title",
        "current_value": "Blue Shirt",
        "proposed_value": "Navy Shirt",
        "reason": "sources disagree on colour",
        "evidence": [{"source": "pim", "value": "navy"}],
        "confidence": 0.8,
        "validation_status": "validated",
        "validation_errors": [],
        "risk_level": RiskLevel.LOW,
        "requires_approval": True,
        "status": ProposalStatus.SIMULATED,
    }
    values.update(overrides)
    return ChangeProposal(**values)


def _tenant() -> TenantContext:
    return TenantContext(workspace_id=WORKSPACE, user_id=ACTOR_ONE)


def _written(session: MagicMock) -> list[Any]:
    return [call.args[0] for call in session.add.call_args_list]


class TestHowManyApprovers:
    @pytest.mark.parametrize(
        ("risk", "expected"),
        [
            (RiskLevel.LOW, 1),
            (RiskLevel.MEDIUM, 1),
            (RiskLevel.HIGH, 1),
            (RiskLevel.CRITICAL, 2),
        ],
    )
    def test_the_level_decides_the_count(self, risk: RiskLevel, expected: int) -> None:
        assert required_approvals_for(risk) == expected

    def test_no_level_asks_for_zero(self) -> None:
        """D-020's rule in the negative: `whether` is never in this table."""
        assert all(count >= 1 for count in map(required_approvals_for, RiskLevel))


class TestWhatMayBeDecided:
    @pytest.mark.parametrize(
        "status", [ProposalStatus.SIMULATED, ProposalStatus.PENDING_APPROVAL]
    )
    def test_a_validated_proposal_in_the_queue_is_decidable(
        self, status: ProposalStatus
    ) -> None:
        assert approvable_reason(_proposal(status=status)) is None

    def test_an_unsimulated_proposal_is_not_decidable(self) -> None:
        reason = approvable_reason(_proposal(validation_status="pending"))
        assert reason is not None
        assert "sandbox" in reason

    def test_a_proposal_the_sandbox_rejected_is_not_decidable(self) -> None:
        reason = approvable_reason(
            _proposal(validation_status="rejected", status=ProposalStatus.REJECTED_BY_VALIDATION)
        )
        assert reason is not None
        assert "rejected" in reason

    @pytest.mark.parametrize(
        "status", [ProposalStatus.APPROVED, ProposalStatus.REJECTED, ProposalStatus.EXECUTED]
    )
    def test_a_decided_proposal_is_not_decidable_again(
        self, status: ProposalStatus
    ) -> None:
        reason = approvable_reason(_proposal(status=status))
        assert reason is not None
        assert status.value in reason

    async def test_the_gate_fires_before_any_row_is_written(self) -> None:
        session = _session()
        proposal = _proposal(status=ProposalStatus.APPROVED)

        with pytest.raises(NotApprovableError):
            await record_decision(
                session,
                _tenant(),
                proposal=proposal,
                decision=ApprovalDecision.APPROVED,
                actor_id=ACTOR_ONE,
            )

        assert session.add.call_count == 0


class TestApprovalsAccumulate:
    async def test_a_critical_change_needs_two_different_people(self) -> None:
        session = _session()
        proposal = _proposal(risk_level=RiskLevel.CRITICAL)

        first = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.APPROVED,
            actor_id=ACTOR_ONE,
            actor_email="one@example.com",
        )

        assert first.approvals_received == 1
        assert first.required_approvals == 2
        assert first.complete is False
        assert first.request.status == "pending"
        # Still waiting: the second approval is the whole point of the level.
        assert first.proposal.status is ProposalStatus.PENDING_APPROVAL

        second = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.APPROVED,
            actor_id=ACTOR_TWO,
            actor_email="two@example.com",
        )

        assert second.approvals_received == 2
        assert second.complete is True
        assert second.request.status == "approved"
        assert second.proposal.status is ProposalStatus.APPROVED

    async def test_the_same_person_cannot_approve_twice(self) -> None:
        session = _session()
        proposal = _proposal(risk_level=RiskLevel.CRITICAL)

        await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.APPROVED,
            actor_id=ACTOR_ONE,
        )

        with pytest.raises(AlreadyDecidedError):
            await record_decision(
                session,
                _tenant(),
                proposal=proposal,
                decision=ApprovalDecision.APPROVED,
                actor_id=ACTOR_ONE,
            )

    async def test_one_approval_closes_a_low_risk_change(self) -> None:
        session = _session()
        proposal = _proposal(risk_level=RiskLevel.LOW)

        outcome = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.APPROVED,
            actor_id=ACTOR_ONE,
        )

        assert outcome.complete is True
        assert outcome.proposal.status is ProposalStatus.APPROVED
        assert outcome.request.decided_at is not None


class TestRejection:
    async def test_one_rejection_is_final_and_carries_its_reason(self) -> None:
        session = _session()
        proposal = _proposal(risk_level=RiskLevel.CRITICAL)

        outcome = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.REJECTED,
            actor_id=ACTOR_ONE,
            notes="the colour is discontinued",
        )

        assert outcome.complete is True
        assert outcome.request.status == "rejected"
        assert outcome.proposal.status is ProposalStatus.REJECTED
        assert outcome.action.notes == "the colour is discontinued"

    async def test_a_rejection_without_a_reason_is_refused(self) -> None:
        session = _session()
        proposal = _proposal()

        with pytest.raises(ApprovalError, match="reason"):
            await record_decision(
                session,
                _tenant(),
                proposal=proposal,
                decision=ApprovalDecision.REJECTED,
                actor_id=ACTOR_ONE,
                notes="   ",
            )

        assert session.add.call_count == 0


class TestModification:
    async def test_a_modified_value_goes_back_through_the_sandbox(self) -> None:
        session = _session()
        proposal = _proposal()

        outcome = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.MODIFIED,
            actor_id=ACTOR_ONE,
            notes="use the darker navy",
            modified_value="Deep Navy",
        )

        assert outcome.proposal.proposed_value == "Deep Navy"
        assert outcome.proposal.validation_status == "pending"
        assert outcome.proposal.status is ProposalStatus.PENDING_APPROVAL
        assert outcome.request.status == "modified"

    async def test_a_modification_without_a_value_is_refused(self) -> None:
        session = _session()
        proposal = _proposal()

        with pytest.raises(ApprovalError, match="edited value"):
            await record_decision(
                session,
                _tenant(),
                proposal=proposal,
                decision=ApprovalDecision.MODIFIED,
                actor_id=ACTOR_ONE,
                notes="something else",
            )

        assert session.add.call_count == 0

    async def test_the_next_decision_opens_a_fresh_request(self) -> None:
        """Approvals recorded against the old value must not carry over."""
        session = _session()
        proposal = _proposal(risk_level=RiskLevel.CRITICAL)

        await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.APPROVED,
            actor_id=ACTOR_ONE,
        )
        await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.MODIFIED,
            actor_id=ACTOR_TWO,
            notes="use the darker navy",
            modified_value="Deep Navy",
        )

        # Back through the sandbox, then a decision: the counter starts again.
        proposal.validation_status = "validated"
        proposal.status = ProposalStatus.SIMULATED
        outcome = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.APPROVED,
            actor_id=ACTOR_ONE,
        )

        assert outcome.approvals_received == 1
        assert outcome.required_approvals == 2
        assert outcome.complete is False


class TestReAnalysis:
    async def test_it_sends_the_proposal_back_to_the_sandbox(self) -> None:
        session = _session()
        proposal = _proposal()

        outcome = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.RE_ANALYSIS_REQUESTED,
            actor_id=ACTOR_ONE,
            notes="the competitor feed changed after this was proposed",
        )

        assert outcome.proposal.validation_status == "pending"
        assert outcome.proposal.status is ProposalStatus.PENDING_APPROVAL
        assert outcome.request.status == "re_analysis_requested"
        assert outcome.action.decision is ApprovalDecision.RE_ANALYSIS_REQUESTED


class TestAuditTrail:
    async def test_every_decision_writes_a_row_that_names_what_it_decided(
        self,
    ) -> None:
        session = _session()
        proposal = _proposal(risk_level=RiskLevel.CRITICAL)

        outcome = await record_decision(
            session,
            _tenant(),
            proposal=proposal,
            decision=ApprovalDecision.APPROVED,
            actor_id=ACTOR_ONE,
            actor_email="one@example.com",
            notes="checked against the PIM",
        )

        audit = outcome.audit
        assert audit.action == "approval.approved"
        assert audit.entity_type == "change_proposal"
        assert audit.entity_id == proposal.id
        assert audit.actor_id == ACTOR_ONE
        assert audit.actor_email == "one@example.com"
        assert audit.risk_level is RiskLevel.CRITICAL
        assert audit.approval_reference == outcome.request.id
        assert audit.reason == "checked against the PIM"
        assert audit.evidence == proposal.evidence
        assert audit in _written(session)

    def test_the_hash_can_be_recomputed_from_the_row(self) -> None:
        """§39's "immutable-style record": an edit is detectable."""
        import datetime as dt

        log = audit_log_for(
            workspace_id=WORKSPACE,
            action="approval.rejected",
            entity_type="change_proposal",
            entity_id=uuid4(),
            created_at=dt.datetime(2026, 10, 7, tzinfo=dt.UTC),
            actor_id=ACTOR_ONE,
            reason="discontinued",
            risk_level=RiskLevel.HIGH,
        )

        assert len(log.payload_hash) == 64
        assert recomputed_hash(log) == log.payload_hash

        log.reason = "a different reason entirely"
        assert recomputed_hash(log) != log.payload_hash

    def test_the_hash_depends_on_the_content_not_the_key_order(self) -> None:
        first = payload_hash({"a": 1, "b": [1, 2]})
        second = payload_hash({"b": [1, 2], "a": 1})
        assert first == second


class TestOpenRequest:
    async def test_it_reuses_an_open_request_rather_than_opening_a_second(self) -> None:
        session = _session()
        proposal = _proposal()
        repository = ApprovalRequestRepository(session, _tenant())
        existing = await repository.add(
            repository.model(
                workspace_id=WORKSPACE,
                change_proposal_id=proposal.id,
                status="pending",
                required_approvals=2,
                approvals_received=1,
            )
        )
        found = MagicMock()
        found.scalars.return_value = found
        found.all.return_value = [existing]
        session.execute = AsyncMock(return_value=found)

        request = await open_request(session, _tenant(), proposal=proposal)

        assert request is existing
        assert request.approvals_received == 1
        assert request.required_approvals == 2
