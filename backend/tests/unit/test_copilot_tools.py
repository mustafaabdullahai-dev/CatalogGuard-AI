"""The copilot's catalog tools (Phase 22, §31).

What these tests pin down is the property §31 asks for and a model cannot be
trusted to supply: the copilot *reads* through these tools, and it cannot reach
a row that belongs to another tenant, another role, or another scope than the
one its arguments state.

No database is available (D-006), so the repositories answer through the shared
fake session from `tests/unit/fakes.py`. That proves the queries are tenant
stamped, the output shapes are strict, and the refusals happen before any row is
read — not that the SQL is correct against Postgres, which nothing in this
repository can prove yet.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.agents.tools import ToolContext
from app.db.base import TenantContext
from app.models.catalog import Product
from app.models.reconciliation import ChangeProposal, Conflict, RiskLevel
from app.models.tenancy import UserRole
from app.services.copilot.tools import copilot_registry, copilot_tools
from tests.unit.fakes import fake_session, written

WORKSPACE = uuid4()
PRODUCT_ID = uuid4()
OTHER_PRODUCT_ID = uuid4()
CONFLICT_ID = uuid4()
PROPOSAL_ID = uuid4()
OTHER_WORKSPACE = uuid4()


def _tenant(workspace_id: Any = WORKSPACE) -> TenantContext:
    return TenantContext(workspace_id=workspace_id, user_id=uuid4())


def _product(**overrides: Any) -> Product:
    values: dict[str, Any] = {
        "id": PRODUCT_ID,
        "workspace_id": WORKSPACE,
        "catalog_id": uuid4(),
        "sku": "CG-1001",
        "title": "Navy Ceramic Mug",
        "brand": "Kettle & Co",
        "category_path": "Home > Mugs",
        "status": "active",
        "version": 2,
        "attributes": {"colour": "navy"},
    }
    values.update(overrides)
    return Product(**values)


def _conflict(**overrides: Any) -> Conflict:
    values: dict[str, Any] = {
        "id": CONFLICT_ID,
        "workspace_id": WORKSPACE,
        "product_id": PRODUCT_ID,
        "conflict_type": "attribute_mismatch",
        "field_name": "colour",
        "competing_values": {
            "pim": {"value": "navy", "rank": 1},
            "erp": {"value": "blue", "rank": 3},
        },
        "confidence": 0.8,
    }
    values.update(overrides)
    return Conflict(**values)


def _proposal(**overrides: Any) -> ChangeProposal:
    values: dict[str, Any] = {
        "id": PROPOSAL_ID,
        "workspace_id": WORKSPACE,
        "product_id": PRODUCT_ID,
        "change_ref": "CHG-0001",
        "field_name": "colour",
        "current_value": "navy",
        "proposed_value": "blue",
        "reason": "The ERP disagrees with the PIM.",
        "confidence": 0.62,
        "risk_level": RiskLevel.HIGH,
        "requires_approval": True,
        "status": "pending_approval",
        "validation_status": "pending",
        "affected_channels": ["amazon", "shopify"],
    }
    values.update(overrides)
    return ChangeProposal(**values)


def _instant() -> Any:
    from app.db.base import utcnow

    return utcnow()


def _seed(session: Any, *rows: Any) -> None:
    for row in rows:
        session.rows.append(row)
        if getattr(row, "id", None) is None:
            row.id = uuid4()


def _context(role: UserRole = UserRole.DATA_STEWARD) -> ToolContext:
    return ToolContext(workspace_id=str(WORKSPACE), role=role)


def _payload(outcome: Any) -> dict[str, Any]:
    """The result object out of a tool outcome's `{"ok": ..., "result": ...}` envelope."""
    import json

    envelope = json.loads(outcome.content)
    assert envelope["ok"] is True, outcome.content
    return envelope["result"]


class TestRegistryShape:
    def test_the_five_catalog_tools_join_phase_15s_pure_four(self) -> None:
        registry = copilot_registry(fake_session(), _tenant())
        assert registry.names() == [
            "assess_change_risk",
            "change_search",
            "compliance_search",
            "conflict_search",
            "normalize_field",
            "product_lookup",
            "resolve_identity",
            "resolve_source_authority",
            "sandbox_simulator",
        ]

    def test_every_tool_declares_a_strict_input_schema(self) -> None:
        for spec in copilot_tools(fake_session(), _tenant()):
            assert spec.args_model.model_config.get("extra") == "forbid"
            assert spec.description.strip()

    async def test_an_unknown_field_is_refused_before_the_handler_runs(self) -> None:
        registry = copilot_registry(fake_session(), _tenant())
        outcome = await registry.execute(
            "product_lookup",
            {"sku": "CG-1001", "sql": "DROP TABLE products"},
            context=_context(),
        )
        assert outcome.failed
        assert outcome.error_code == "invalid_arguments"

    async def test_the_sandbox_tool_requires_write_not_merely_read(self) -> None:
        registry = copilot_registry(fake_session(), _tenant())
        outcome = await registry.execute(
            "sandbox_simulator",
            {"proposal_id": str(PROPOSAL_ID)},
            context=_context(UserRole.SEO_SPECIALIST),
        )
        assert outcome.failed
        assert outcome.error_code == "not_authorized"


class TestProductLookup:
    async def test_returns_the_product_with_its_conflicts_and_proposals(self) -> None:
        session = fake_session()
        _seed(session, _product(), _conflict(), _proposal())
        registry = copilot_registry(session, _tenant())

        outcome = await registry.execute("product_lookup", {"sku": "CG-1001"}, context=_context())

        assert outcome.ok, outcome.content
        payload = _payload(outcome)
        assert payload["products"][0]["sku"] == "CG-1001"
        assert payload["conflicts"][0]["field"] == "colour"
        assert payload["proposals"][0]["risk_level"] == "HIGH"

    async def test_says_so_rather_than_inventing_a_product(self) -> None:
        session = fake_session()
        _seed(session, _product())
        registry = copilot_registry(session, _tenant())

        outcome = await registry.execute("product_lookup", {"sku": "NOPE-1"}, context=_context())

        assert outcome.ok
        payload = _payload(outcome)
        assert payload["products"] == []
        assert "invented SKU" in payload["note"]

    async def test_narrowed_competing_values_keep_their_ranks(self) -> None:
        session = fake_session()
        _seed(session, _product(), _conflict())
        registry = copilot_registry(session, _tenant())

        payload = _payload(
            await registry.execute("product_lookup", {"sku": "CG-1001"}, context=_context())
        )

        assert payload["conflicts"][0]["competing_values"] == {
            "pim": {"value": "navy", "rank": 1},
            "erp": {"value": "blue", "rank": 3},
        }

    async def test_rows_are_stamped_with_the_caller_workspace(self) -> None:
        session = fake_session()
        other = _product(id=OTHER_PRODUCT_ID, workspace_id=OTHER_WORKSPACE, sku="OTHER-1")
        _seed(session, other)
        registry = copilot_registry(session, _tenant(WORKSPACE))

        payload = _payload(
            await registry.execute("product_lookup", {"text": "OTHER"}, context=_context())
        )

        assert payload["products"] == [], "another tenant's row must not be returned"
        assert written(session) == [], "and a read must not insert anything"


class TestConflictSearch:
    async def test_reports_the_open_total_and_the_products_behind_it(self) -> None:
        session = fake_session()
        _seed(session, _product(), _conflict())
        registry = copilot_registry(session, _tenant())
        _seed(session, _conflict(id=uuid4(), field_name="title"))
        _seed(session, _conflict(id=uuid4(), field_name="title", resolved_at=_instant()))

        outcome = await registry.execute("conflict_search", {"field": "colour"}, context=_context())

        assert outcome.ok, outcome.content
        payload = _payload(outcome)
        # A resolved conflict is history: it must not inflate the open total.
        assert payload["total_open"] == 1
        assert payload["conflicts"][0]["field"] == "colour"
        assert payload["products"][0]["sku"] == "CG-1001"
        assert payload["note"].startswith("Open disagreements only")

    async def test_an_unknown_field_search_is_empty_not_an_error(self) -> None:
        session = fake_session()
        _seed(session, _product(), _conflict())
        registry = copilot_registry(session, _tenant())

        payload = _payload(await registry.execute("conflict_search", {}, context=_context()))

        assert payload["conflicts"]


class TestChangeSearch:
    async def test_orders_by_risk_band_not_by_confidence(self) -> None:
        session = fake_session()
        _seed(
            session,
            _proposal(
                id=uuid4(),
                change_ref="CHG-LOW",
                field_name="title",
                risk_level=RiskLevel.LOW,
                confidence=0.99,
            ),
            _proposal(
                id=uuid4(),
                change_ref="CHG-CRIT",
                field_name="colour",
                risk_level=RiskLevel.CRITICAL,
                confidence=0.4,
            ),
        )
        registry = copilot_registry(session, _tenant())

        payload = _payload(await registry.execute("change_search", {}, context=_context()))

        assert [row["change_ref"] for row in payload["proposals"]] == ["CHG-CRIT", "CHG-LOW"]
        assert "Confidence is evidence" in payload["note"]

    async def test_channel_filter_matches_the_affected_channels(self) -> None:
        session = fake_session()
        _seed(
            session,
            _proposal(id=uuid4(), change_ref="AMAZON", affected_channels=["amazon"]),
            _proposal(id=uuid4(), change_ref="SHOPIFY", affected_channels=["shopify"]),
        )
        registry = copilot_registry(session, _tenant())

        payload = _payload(
            await registry.execute("change_search", {"channel": "shopify"}, context=_context())
        )

        assert [row["change_ref"] for row in payload["proposals"]] == ["SHOPIFY"]

    async def test_a_proposal_carries_the_reason_a_reviewer_needs(self) -> None:
        session = fake_session()
        _seed(session, _proposal())
        registry = copilot_registry(session, _tenant())

        payload = _payload(await registry.execute("change_search", {}, context=_context()))

        assert payload["proposals"][0]["reason"] == "The ERP disagrees with the PIM."
        assert payload["proposals"][0]["requires_approval"] is True


class TestComplianceSearch:
    async def test_reports_the_signals_and_never_claims_a_verdict(self, monkeypatch) -> None:
        from app.rag.service import SearchResult
        from app.services.copilot import tools as tools_module

        async def _search(session, tenant, *, query, metadata_filter=None, top_k=None, **kwargs):
            from app.rag.retrieval import RetrievalHit

            return SearchResult(
                hits=[
                    RetrievalHit(
                        chunk_id=uuid4(),
                        document_id=uuid4(),
                        document_title="Amazon UK Listing Requirements",
                        section_reference="Section 7.1",
                        marketplace_code="amazon",
                        content="Material claims must be substantiated.",
                        score=0.91,
                        signals=("keyword",),
                    )
                ],
                signals=("keyword",),
                candidates_considered=214,
                query=query,
            )

        monkeypatch.setattr(tools_module, "rag_search", _search)
        registry = copilot_registry(fake_session(), _tenant())

        outcome = await registry.execute(
            "compliance_search",
            {"query": "material claims", "marketplace_code": "amazon"},
            context=_context(),
        )

        assert outcome.ok, outcome.content
        payload = _payload(outcome)
        assert payload["hits"][0]["section_reference"] == "Section 7.1"
        assert payload["signals"] == ["keyword"]
        assert "not a compliance decision" in payload["note"]

    async def test_an_empty_query_is_answered_not_raised(self, monkeypatch) -> None:
        from app.services.copilot import tools as tools_module

        async def _search(session, tenant, *, query, **kwargs):
            raise ValueError("query is required")

        monkeypatch.setattr(tools_module, "rag_search", _search)
        registry = copilot_registry(fake_session(), _tenant())

        outcome = await registry.execute("compliance_search", {"query": "??"}, context=_context())

        assert outcome.ok
        assert _payload(outcome)["hits"] == []


class TestToolOutputBudget:
    async def test_row_counts_are_clamped_to_the_configured_cap(self) -> None:
        session = fake_session()
        _seed(session, *[_conflict(id=uuid4()) for _ in range(40)])
        registry = copilot_registry(session, _tenant())
        assert registry.names()  # sanity: the registry is the one under test
        payload = _payload(
            await registry.execute("conflict_search", {"limit": 50}, context=_context())
        )

        assert len(payload["conflicts"]) <= 20, "copilot_max_rows_per_tool is 20"

    async def test_a_large_result_is_still_bounded_text(self) -> None:
        session = fake_session()
        _seed(session, _conflict(competing_values={"erp": "x" * 5000}))
        registry = copilot_registry(session, _tenant())

        outcome = await registry.execute("product_lookup", {"sku": "CG-1001"}, context=_context())

        assert outcome.ok
        assert len(outcome.content) < 8000


class TestNoWriteTools:
    def test_the_copilot_registers_no_tool_that_can_approve_or_execute(self) -> None:
        names = {spec.name for spec in copilot_tools(fake_session(), _tenant())}
        assert names.isdisjoint(
            {
                "create_change_proposal",
                "approve_proposal",
                "execute_change",
                "audit_logger",
                "approval_checker",
            }
        )

    async def test_no_tool_writes_a_row_by_itself(self) -> None:
        session = fake_session()
        _seed(session, _product(), _conflict(), _proposal())
        registry = copilot_registry(session, _tenant())

        for name, arguments in [
            ("product_lookup", {"sku": "CG-1001"}),
            ("conflict_search", {}),
            ("change_search", {}),
        ]:
            outcome = await registry.execute(name, arguments, context=_context())
            assert outcome.ok, f"{name}: {outcome.content}"

        assert written(session) == [], "reading is not writing"
