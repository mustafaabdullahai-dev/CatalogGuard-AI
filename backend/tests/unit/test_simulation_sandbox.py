"""Sandbox simulation tests (Phase 17, §18, §19, §17).

The sandbox's whole claim is that it is the safe place to find out what a
change does: the input product is never modified, the chain runs in a fixed
order, and a stage that could not run says so instead of passing. Each class
below takes one of §18's stages, and the last two take what the run writes
back.

Offline — no database, no queue, no clock. The compliance stage is exercised
with a stub checker because the real corpus lives in a database D-006 still
records as unconfirmed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from app.db.base import TenantContext
from app.models.reconciliation import ChangeProposal, ProposalStatus, RiskLevel
from app.services.catalog.relationships import RelationshipEdge, TreeNode
from app.services.simulation import (
    CatalogSnapshot,
    ComplianceOutcome,
    ProductState,
    ProposedChange,
    SimulationResult,
    persist_simulation,
    simulate,
    validation_for,
)
from app.services.simulation.persistence import simulation_run_for
from app.services.simulation.sandbox import StageOutcome


def _session() -> MagicMock:
    session = MagicMock()
    session.flush = AsyncMock(return_value=None)
    return session


def _proposal(workspace_id: UUID) -> ChangeProposal:
    return ChangeProposal(
        workspace_id=workspace_id,
        change_ref="CHG-SIM-0001",
        field_name="sku",
        current_value="SKU-1",
        proposed_value="SKU-1",
        reason="phase 17 test",
        confidence=0.9,
        validation_status="pending",
        risk_level=RiskLevel.LOW,
        requires_approval=True,
        status=ProposalStatus.PROPOSED,
    )

PRODUCT_ID = "b1111111-1111-1111-1111-111111111111"
PARENT_ID = "a1111111-1111-1111-1111-111111111111"


def _product(**overrides: object) -> ProductState:
    values: dict[str, object] = {
        "id": PRODUCT_ID,
        "sku": "SKU-1",
        "title": "Blue Shirt",
        "attributes": {"color": "blue", "price": "19.99"},
    }
    values.update(overrides)
    return ProductState(**values)  # type: ignore[arg-type]


def _snapshot(product: ProductState | None = None, **overrides: object) -> CatalogSnapshot:
    values: dict[str, object] = {"product": product or _product(), "catalog_version": 7}
    values.update(overrides)
    return CatalogSnapshot(**values)  # type: ignore[arg-type]


def _change(**overrides: object) -> ProposedChange:
    values: dict[str, object] = {
        "change_ref": "CHG-AAAA-0001",
        "field_name": "title",
        "current_value": "Blue Shirt",
        "proposed_value": "Navy Cotton Shirt",
        "risk_level": RiskLevel.MEDIUM,
    }
    values.update(overrides)
    return ProposedChange(**values)  # type: ignore[arg-type]


def _node(
    node_id: str,
    sku: str,
    *,
    parent_id: str | None = None,
    is_parent: bool = False,
) -> TreeNode:
    return TreeNode(id=node_id, sku=sku, parent_id=parent_id, is_parent=is_parent)


def _stage(result: SimulationResult, name: str) -> StageOutcome:
    return next(stage for stage in result.stages if stage.name == name)


class TestTheInputIsNeverChanged:
    async def test_running_a_change_leaves_the_snapshot_alone(self) -> None:
        snapshot = _snapshot()
        before = snapshot.product

        await simulate(_change(), snapshot)

        assert snapshot.product is before
        assert before.title == "Blue Shirt"
        assert before.value_of("title") == "Blue Shirt"

    async def test_the_same_inputs_produce_the_same_report(self) -> None:
        """§18's reproducibility: nothing here reads a clock or a session."""
        snapshot = _snapshot()
        change = _change()

        first = await simulate(change, snapshot)
        second = await simulate(change, snapshot)

        assert first.diff == second.diff
        assert first.impact_report == second.impact_report
        assert first.explanation == second.explanation


class TestSchemaStage:
    async def test_emptying_the_sku_is_rejected(self) -> None:
        result = await simulate(
            _change(field_name="sku", proposed_value="  "), _snapshot()
        )

        stage = _stage(result, "schema_validation")
        assert stage.status == "failed"
        assert result.schema_valid is False
        assert result.validation_status == "rejected"
        assert result.proposal_status is ProposalStatus.REJECTED_BY_VALIDATION

    async def test_a_gtin_that_is_not_an_identifier_is_rejected(self) -> None:
        result = await simulate(
            _change(field_name="gtin", proposed_value="not-a-gtin"), _snapshot()
        )

        assert result.schema_valid is False
        assert any("8 to 14 digits" in error for error in result.validation_errors)

    async def test_a_gtin_written_with_separators_is_accepted(self) -> None:
        result = await simulate(
            _change(field_name="gtin", proposed_value="012345 678905"), _snapshot()
        )

        assert result.schema_valid is True
        assert _stage(result, "schema_validation").status == "passed"

    async def test_a_negative_price_is_rejected(self) -> None:
        result = await simulate(
            _change(field_name="price", proposed_value=-1), _snapshot()
        )

        assert result.schema_valid is False

    async def test_a_field_with_no_rule_is_reported_as_not_checked(self) -> None:
        """`attributes` has no schema, so the stage must not claim to have run."""
        result = await simulate(_change(field_name="color", proposed_value="navy"), _snapshot())

        stage = _stage(result, "schema_validation")
        assert stage.status == "skipped"
        assert result.schema_valid is None
        # Not checked is not failed: the proposal is still simulatable.
        assert result.validation_status == "validated"
        assert result.proposal_status is ProposalStatus.SIMULATED

    async def test_an_unchanged_field_passes(self) -> None:
        result = await simulate(
            _change(field_name="sku", proposed_value="SKU-1"), _snapshot()
        )

        assert result.schema_valid is True
        assert result.validation_status == "validated"


class TestRelationshipStage:
    async def test_a_child_pointed_at_a_parent_that_does_not_exist_fails(self) -> None:
        snapshot = _snapshot(
            nodes=(
                _node(PARENT_ID, "P-A", is_parent=True),
                _node(PRODUCT_ID, "SKU-1", parent_id=PARENT_ID),
            )
        )

        result = await simulate(
            _change(field_name="parent_id", proposed_value=str(uuid4())),
            snapshot,
        )

        stage = _stage(result, "relationship_validation")
        assert stage.status == "failed"
        assert result.relationship_valid is False
        assert result.validation_status == "rejected"

    async def test_a_valid_move_passes_and_the_diff_shows_both_trees(self) -> None:
        snapshot = _snapshot(
            _product(parent_id=PARENT_ID),
            nodes=(
                _node(PARENT_ID, "P-A", is_parent=True),
                _node(PRODUCT_ID, "SKU-1", parent_id=PARENT_ID),
            ),
            edges=(RelationshipEdge(parent_id=PARENT_ID, child_id=PRODUCT_ID),),
        )

        result = await simulate(
            _change(field_name="parent_id", proposed_value=PARENT_ID),
            snapshot,
        )

        assert result.relationship_valid is True
        assert _stage(result, "relationship_validation").status == "passed"
        assert result.diff["tree"]["before"]["parent"] == PARENT_ID
        assert result.diff["tree"]["after"]["parent"] == PARENT_ID

    async def test_a_snapshot_without_a_tree_skips_rather_than_passing(self) -> None:
        result = await simulate(_change(), _snapshot(nodes=()))

        stage = _stage(result, "relationship_validation")
        assert stage.status == "skipped"
        assert result.relationship_valid is None
        assert "Not checked: relationship_validation" in result.explanation
        # A stage that did not run cannot fail the proposal either.
        assert result.validation_status == "validated"

    async def test_findings_that_already_existed_are_not_blamed_on_the_change(self) -> None:
        """The report shows what *this* change introduced, not the tree's history."""
        # A tree that is already broken: one orphan, and our product as a
        # healthy root. Both states agree with the product value, because
        # `_tree_after` takes parentage from the product.
        orphan = _node("c2222222-2222-2222-2222-222222222222", "ORPHAN")
        snapshot = _snapshot(
            _product(is_parent=True),
            nodes=(orphan, _node(PRODUCT_ID, "SKU-1", is_parent=True)),
        )

        result = await simulate(_change(field_name="title", proposed_value="New"), snapshot)

        # The orphan is in the tree before and after, so it is not this
        # change's doing: reported, counted, not blamed.
        assert _stage(result, "relationship_validation").findings == ()
        assert result.relationship_valid is True
        assert result.validation_status == "validated"
        assert result.impact_report["relationship_findings"]["after"]["kind.orphan_child"] == 1


class TestComplianceStage:
    async def test_no_checker_means_not_run_and_never_passed(self) -> None:
        result = await simulate(_change(), _snapshot())

        stage = _stage(result, "compliance_validation")
        assert stage.status == "skipped"
        assert result.compliance_valid is None

    async def test_a_checker_that_refuses_the_change_fails_the_run(self) -> None:
        class Refusing:
            async def check(self, change: ProposedChange, after: ProductState) -> ComplianceOutcome:
                return ComplianceOutcome(valid=False, findings=("policy forbids it",))

        result = await simulate(_change(), _snapshot(), compliance=Refusing())

        assert result.compliance_valid is False
        assert result.validation_status == "rejected"
        assert result.proposal_status is ProposalStatus.REJECTED_BY_VALIDATION
        assert "compliance_validation: policy forbids it" in result.validation_errors

    async def test_a_checker_that_accepts_the_change_passes_the_run(self) -> None:
        class Accepting:
            async def check(self, change: ProposedChange, after: ProductState) -> ComplianceOutcome:
                return ComplianceOutcome(valid=True)

        result = await simulate(_change(), _snapshot(), compliance=Accepting())

        assert result.compliance_valid is True
        assert _stage(result, "compliance_validation").status == "passed"
        assert result.validation_status == "validated"


class TestConflictStage:
    async def test_when_every_source_already_agrees_the_disagreement_is_over(self) -> None:
        change = _change(
            field_name="color",
            current_value="blue",
            proposed_value="Navy",
            evidence=(
                {
                    "values": {"manufacturer": "Navy", "marketplace": "Navy"},
                    "source_records": [
                        {"source": "manufacturer", "label": "Supplier"},
                        {"source": "marketplace", "label": "Amazon"},
                    ],
                },
            ),
        )

        result = await simulate(change, _snapshot())

        stage = _stage(result, "conflict_detection")
        assert stage.status == "passed"
        assert result.impact_report["conflicts_remaining"] == 0
        assert result.impact_report["sources_still_disagreeing"] == []

    async def test_a_remaining_disagreement_is_impact_not_failure(self) -> None:
        change = _change(
            field_name="color",
            current_value="blue",
            proposed_value="Navy",
            evidence=(
                {
                    "values": {"manufacturer": "Navy", "marketplace": "Blue"},
                    "source_records": [
                        {"source": "manufacturer", "label": "Supplier"},
                        {"source": "marketplace", "label": "Amazon"},
                    ],
                },
            ),
        )

        result = await simulate(change, _snapshot())

        stage = _stage(result, "conflict_detection")
        assert stage.status == "flagged"
        assert result.impact_report["conflicts_remaining"] == 1
        assert result.impact_report["sources_still_disagreeing"] == ["marketplace"]
        # Sources disagreeing with each other is the reason the proposal exists;
        # it is reported, and it does not veto the change.
        assert result.validation_status == "validated"
        assert "1 disagreement(s) remain after the change" in result.explanation
        assert "marketplace still say otherwise" in result.explanation


class TestDiffView:
    async def test_the_changed_field_comes_first_with_its_indicator(self) -> None:
        result = await simulate(_change(), _snapshot())

        rows = result.diff["rows"]
        assert rows[0]["field"] == "title"
        assert rows[0]["current"] == "Blue Shirt"
        assert rows[0]["proposed"] == "Navy Cotton Shirt"
        assert rows[0]["indicator"] == "modified"
        assert rows[0]["requires_approval"] is True
        assert rows[0]["risky"] is False

    async def test_context_rows_are_marked_unchanged(self) -> None:
        result = await simulate(_change(), _snapshot())

        context = {row["field"]: row for row in result.diff["rows"]}
        assert context["title"]["indicator"] == "modified"
        assert context["sku"]["indicator"] == "unchanged"
        assert context["sku"]["current"] == context["sku"]["proposed"] == "SKU-1"
        assert context["sku"]["requires_approval"] is False

    async def test_a_high_risk_change_is_marked_risky(self) -> None:
        result = await simulate(_change(risk_level=RiskLevel.HIGH), _snapshot())

        assert result.diff["rows"][0]["risky"] is True
        assert result.diff["rows"][0]["requires_approval"] is True
        assert result.impact_report["risk"]["level"] == "HIGH"
        assert "approve" in result.impact_report["recommendation"]

    async def test_clearing_a_field_reads_as_removed(self) -> None:
        result = await simulate(
            _change(field_name="description", current_value="old", proposed_value=None),
            _snapshot(_product(description="old")),
        )

        assert result.diff["rows"][0]["indicator"] == "removed"

    async def test_the_report_lists_every_stage_in_order(self) -> None:
        result = await simulate(_change(), _snapshot())

        assert [stage["name"] for stage in result.diff["stages"]] == [
            "relationship_validation",
            "schema_validation",
            "compliance_validation",
            "seo_analysis",
            "conflict_detection",
        ]


class TestSeoStage:
    async def test_an_indexing_sensitive_field_is_flagged_without_a_promise(self) -> None:
        result = await simulate(
            _change(field_name="handle", proposed_value="navy-shirt"), _snapshot()
        )

        stage = _stage(result, "seo_analysis")
        assert stage.status == "flagged"
        assert result.seo_sensitive is True
        assert any("indexing-sensitive" in finding for finding in stage.findings)
        # §17: no outcome is claimed.
        assert "guaranteed" not in result.explanation

    async def test_a_title_emptied_is_called_out(self) -> None:
        result = await simulate(_change(field_name="title", proposed_value=""), _snapshot())

        assert any("no title" in f for f in _stage(result, "seo_analysis").findings)

    async def test_a_plain_field_change_is_not_seo_sensitive(self) -> None:
        result = await simulate(_change(field_name="color", proposed_value="navy"), _snapshot())

        assert result.seo_sensitive is False


class TestStatusTransition:
    def test_only_a_failed_stage_rejects(self) -> None:
        stages = [
            StageOutcome("relationship_validation", "skipped"),
            StageOutcome("schema_validation", "passed"),
            StageOutcome("compliance_validation", "skipped"),
        ]
        status, proposal_status = validation_for(stages)

        assert status == "validated"
        assert proposal_status is ProposalStatus.SIMULATED

    def test_a_failed_stage_rejects_even_when_another_was_skipped(self) -> None:
        stages = [
            StageOutcome("relationship_validation", "failed", ("broken",)),
            StageOutcome("compliance_validation", "skipped"),
        ]
        status, proposal_status = validation_for(stages)

        assert status == "rejected"
        assert proposal_status is ProposalStatus.REJECTED_BY_VALIDATION

    def test_a_flagged_stage_alone_does_not_reject(self) -> None:
        """Impact that must be shown is not the same as a validation failure."""
        status, _ = validation_for([StageOutcome("conflict_detection", "flagged")])

        assert status == "validated"


class TestPersistedRun:
    async def test_the_row_carries_the_flags_report_and_version(self) -> None:
        proposal_id = uuid4()
        result = await simulate(
            _change(field_name="gtin", proposed_value="012345678905"), _snapshot()
        )

        run = simulation_run_for(proposal_id, result)

        assert run.change_proposal_id == proposal_id
        assert run.base_catalog_version == 7
        assert run.status == "completed"
        assert run.schema_valid is True
        assert run.relationship_valid is None
        assert run.compliance_valid is None
        assert run.seo_sensitive is False
        assert run.overall_risk is RiskLevel.MEDIUM
        assert run.diff == result.diff
        assert run.impact_report == result.impact_report
        assert run.explanation == result.explanation
        assert run.completed_at is not None


class TestPersistSimulationWritesTheDecision:
    """Exit criterion two: the status moves *because* a run said so.

    `persist_simulation` is the only writer of `validation_status`, so it is
    the seam where "pending" stops being the default. It runs against a fake
    session — the repositories only add and flush — so what is asserted is
    what gets written, not whether a database accepts it.
    """

    async def test_a_passing_run_moves_the_proposal_to_simulated(self) -> None:
        session = _session()
        tenant = TenantContext(workspace_id=uuid4())
        proposal = _proposal(tenant.workspace_id)
        result = await simulate(
            _change(field_name="sku", proposed_value="SKU-1"), _snapshot()
        )

        run = await persist_simulation(
            session, tenant, proposal=proposal, result=result
        )

        assert proposal.validation_status == "validated"
        assert proposal.status is ProposalStatus.SIMULATED
        assert proposal.validation_errors == []
        # The run first, the proposal it decided second.
        assert [call.args[0] for call in session.add.call_args_list] == [run, proposal]

    async def test_a_failing_run_rejects_and_keeps_the_reason(self) -> None:
        session = _session()
        tenant = TenantContext(workspace_id=uuid4())
        proposal = _proposal(tenant.workspace_id)
        result = await simulate(
            _change(field_name="sku", proposed_value="x" * 300), _snapshot()
        )

        await persist_simulation(
            session, tenant, proposal=proposal, result=result
        )

        assert proposal.validation_status == "rejected"
        assert proposal.status is ProposalStatus.REJECTED_BY_VALIDATION
        assert proposal.validation_errors
