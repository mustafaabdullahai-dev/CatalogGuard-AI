"""Reconciliation engine tests (Phase 12).

The engine's decisions are the ones worth pinning: that a tie produces a conflict
and *no* proposal, that a resolvable disagreement differing from what is stored
becomes a risk-scored proposal, that an identical value never reaches the approval
queue, and that confidence can never be stored as certainty (§37).

All offline — no Postgres, no Redis, no model. The database-touching half of the
run is exercised through the same functions these test, not through a server.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
from httpx import ASGITransport

from app.main import app
from app.models.catalog import Product, ProductStatus, SourceRecord, SourceType
from app.models.reconciliation import RiskLevel
from app.services.reconciliation.conflicts import (
    FieldObservation,
    detect_field_conflicts,
)
from app.services.reconciliation.engine import (
    build_observations,
    change_ref,
    plan_proposals,
    product_values,
)

CATALOG_ID = uuid4()
PRODUCT_ID = uuid4()


def _product(**overrides: object) -> Product:
    fields: dict[str, object] = {
        "catalog_id": CATALOG_ID,
        "sku": "ABC",
        "title": "Old Title",
        "status": ProductStatus.ACTIVE,
        "attributes": {},
    }
    fields.update(overrides)
    return Product(**fields)  # type: ignore[arg-type]


def _record(raw: dict[str, object], *, external_id: str = "r1") -> SourceRecord:
    return SourceRecord(
        id=uuid4(),
        catalog_source_id=uuid4(),
        external_id=external_id,
        raw_data=raw,
    )


class TestProductValues:
    def test_columns_and_attributes_are_both_read(self) -> None:
        product = _product(attributes={"material": "Cotton"})
        values = product_values(product)
        assert values["sku"] == "ABC"
        assert values["title"] == "Old Title"
        assert values["material"] == "Cotton"
        # Unset columns are present as None: the workspace genuinely believes
        # "we do not know", which is different from not having considered it.
        assert values["gtin"] is None


class TestBuildObservations:
    def test_the_canonical_product_is_itself_an_observation(self) -> None:
        product = _product(title="Old Title")
        observations = build_observations(product, [])
        canonical = observations["title"]
        assert len(canonical) == 1
        assert canonical[0].is_current_canonical is True
        assert canonical[0].source is SourceType.INTERNAL

    def test_a_record_contributes_none_for_fields_it_lacks(self) -> None:
        record = _record({"title": "From Supplier"}, external_id="r1")
        # The product knows its material; the source does not say.
        product = _product(attributes={"material": "Cotton"})
        observations = build_observations(product, [(record, SourceType.PIM)])

        # Present: the canonical value plus the source's claim.
        assert len(observations["title"]) == 2
        # Absent: one present (canonical) and one None (source silent), which is
        # exactly what `detect_missing_attribute_conflicts` needs to see.
        material = observations["material"]
        assert len(material) == 2
        assert sorted(obs.value is None for obs in material) == [False, True]

    def test_a_field_nobody_has_records_no_observation_from_the_product(self) -> None:
        """Silence is not an observation.

        The product has no `material` at all, so it contributes nothing rather
        than a null claim — otherwise every untouched field would look like a
        source disagreeing with the catalogue about a value neither holds.
        """
        record = _record({"title": "From Supplier"}, external_id="r1")
        observations = build_observations(_product(), [(record, SourceType.PIM)])
        assert len(observations["material"]) == 1
        assert observations["material"][0].value is None

    def test_raw_columns_are_normalized_before_comparison(self) -> None:
        product = _product()
        record = _record({"Product Colour": "Navy Blue"}, external_id="r1")
        observations = build_observations(product, [(record, SourceType.PIM)])
        source_values = [
            obs.value for obs in observations["color"] if not obs.is_current_canonical
        ]
        assert source_values == ["Navy Blue"]


class TestPlanProposals:
    def test_a_tie_produces_no_proposal(self) -> None:
        """ERP and WMS share rank 2 for `material` and have no per-field override.

        D-002 is explicit that equal rank with disagreement escalates rather than
        choosing, and §11 requires a conflict with no winner to produce neither a
        value nor a proposal.
        """
        observations = {
            "material": [
                FieldObservation(source=SourceType.ERP, value="Cotton"),
                FieldObservation(source=SourceType.WMS, value="Polyester"),
            ]
        }
        conflicts = detect_field_conflicts(str(PRODUCT_ID), observations)
        assert conflicts, "the disagreement itself must be recorded"
        assert (
            plan_proposals({}, conflicts, affected_channels=[], affected_products=[])
            == []
        )

    def test_a_resolved_disagreement_becomes_a_risk_scored_proposal(self) -> None:
        observations = {
            "material": [
                FieldObservation(source=SourceType.MANUFACTURER, value="Cotton"),
                FieldObservation(source=SourceType.WMS, value="Polyester"),
            ]
        }
        conflicts = detect_field_conflicts(str(PRODUCT_ID), observations)
        plans = plan_proposals(
            {"material": "Polyester"},
            conflicts,
            affected_channels=[],
            affected_products=[str(PRODUCT_ID)],
        )
        assert len(plans) == 1
        plan = plans[0]
        assert plan.proposed_value == "Cotton"
        assert plan.current_value == "Polyester"
        assert isinstance(plan.risk.level, RiskLevel)
        assert plan.risk.factors, "risk must be explainable, not just a band"
        # §37: never 0 or 1, so it cannot read as certainty or as permission.
        assert 0.0 < plan.confidence < 1.0

    def test_a_value_that_already_matches_never_reaches_the_queue(self) -> None:
        observations = {
            "material": [
                FieldObservation(source=SourceType.MANUFACTURER, value="Cotton"),
                FieldObservation(source=SourceType.WMS, value="Polyester"),
            ]
        }
        conflicts = detect_field_conflicts(str(PRODUCT_ID), observations)
        plans = plan_proposals(
            {"material": "Cotton"},
            conflicts,
            affected_channels=[],
            affected_products=[],
        )
        assert plans == []

    def test_numeric_formatting_is_not_a_disagreement(self) -> None:
        """`10.0` in the catalogue and `10.00` from the source are the same price."""
        observations = {
            "price": [
                FieldObservation(source=SourceType.MANUFACTURER, value="10.00"),
                FieldObservation(source=SourceType.MARKETPLACE, value=10.0),
            ]
        }
        conflicts = detect_field_conflicts(str(PRODUCT_ID), observations)
        plans = plan_proposals(
            {"price": 10.0},
            conflicts,
            affected_channels=[],
            affected_products=[],
        )
        assert plans == []


class TestChangeRef:
    def test_is_readable_and_run_scoped(self) -> None:
        run_id = uuid4()
        first = change_ref(run_id, 1)
        assert first.startswith("CHG-")
        assert first.endswith("-0001")
        assert first != change_ref(run_id, 2)
        assert first != change_ref(uuid4(), 1)


class TestReconciliationApi:
    async def test_routes_are_in_the_spec(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            paths = (await client.get("/openapi.json")).json()["paths"]
        assert "/api/v1/catalogs/{catalog_id}/reconcile" in paths
        assert "/api/v1/conflicts" in paths
        assert "/api/v1/change-proposals" in paths
        assert "/api/v1/reconciliation-runs" in paths

    async def test_every_route_requires_a_session(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in (
                "/api/v1/conflicts",
                "/api/v1/change-proposals",
                "/api/v1/reconciliation-runs",
            ):
                response = await client.get(path)
                assert response.status_code == 401, path

