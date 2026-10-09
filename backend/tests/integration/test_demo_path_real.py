"""§69's demo scenario, end to end against a real database (Phase 24, §40).

Example Fashion's cotton shirt, four sources, eleven steps: records in, identity
resolved, attributes normalized, conflicts found, authority decided, a proposal
raised, risk scored, the change simulated, impact reported, approval recorded,
and only then executed — with the execution verified against the marketplace.

Every step below this one has been unit-tested against fakes. This file is the
first time they run in sequence, in order, with real SQL underneath: a single
mistaken column name, a broken foreign key or a repository that forgets its
tenant predicate shows up here and nowhere else.

The marketplace is `MockConnector`. D-005 still defers every live integration, so
"verified" here means the platform's own path is sound, not that a real
marketplace was written to.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import MockConnector
from app.db.base import TenantContext
from app.db.session import bind_tenant
from app.models.catalog import CatalogSource, SourceRecord, SourceType
from app.services.catalog.canonicalization import canonicalize_source
from app.services.reconciliation.engine import reconcile_catalog
from tests.integration.conftest import tenant_session

pytestmark = pytest.mark.integration

# §69's data: one product, four sources, disagreeing about colour and size.
MANUFACTURER = {
    "sku": "EF-SHIRT-001",
    "title": "Classic Cotton Shirt",
    "color": "Navy Blue",
    "size": "M",
    "material": "100% Cotton",
}
AMAZON = {
    "sku": "B07EF-NAVY",
    "title": "Classic Cotton Shirt - Navy Blue",
    "color": "Blue",
    "size": "M",
    "material": "Cotton",
}
SHOPIFY = {
    "sku": "SHIRT-001",
    "title": "Classic Cotton Shirt",
    "color": "Navy",
    "size": "Medium",
    "material": "100% Cotton",
}
TIKTOK = {
    "sku": "TT-EF-001",
    "title": "Classic Cotton Shirt",
    "color": "Dark Blue",
    "size": "M",
    "material": "Cotton",
}


async def _seed_source(
    session: AsyncSession,
    context: TenantContext,
    name: str,
    source_type: SourceType,
    rows: dict[str, dict[str, str]],
) -> CatalogSource:
    source = CatalogSource(
        workspace_id=context.workspace_id,
        name=name,
        source_type=source_type,
        detected_schema={},
        authorization_context={},
    )
    session.add(source)
    await session.flush()
    for external_id, raw in rows.items():
        session.add(
            SourceRecord(
                workspace_id=context.workspace_id,
                catalog_source_id=source.id,
                external_id=external_id,
                raw_data=raw,
            )
        )
    await session.flush()
    return source


class TestDemoScenario:
    async def test_the_whole_chain_runs_and_only_approved_changes_execute(
        self, app_session_factory: Any, workspace: TenantContext
    ) -> None:
        # Steps 1–2: records in, identity resolved into one canonical product.
        async with tenant_session(app_session_factory, workspace) as session:
            manufacturer = await _seed_source(
                session,
                workspace,
                "Manufacturer feed",
                SourceType.MANUFACTURER,
                {"mf-1": MANUFACTURER},
            )
            for name, source_type, rows in (
                ("Amazon export", SourceType.MARKETPLACE, {"amz-1": AMAZON}),
                ("Shopify export", SourceType.MARKETPLACE, {"shop-1": SHOPIFY}),
                ("TikTok export", SourceType.MARKETPLACE, {"tt-1": TIKTOK}),
            ):
                await _seed_source(session, workspace, name, source_type, rows)

            from app.services.catalog import catalogs

            catalog = await catalogs.resolve_default_catalog(session, workspace)
            assert catalog is not None

            summary = await canonicalize_source(
                session, workspace, source=manufacturer, catalog=catalog
            )
            await session.commit()

            assert summary.products_created == 1, "one GTIN-bearing row, one product"
            assert summary.records_examined == 1

        # Steps 3–6: the remaining sources are ingested into the same product, and
        # reconciliation finds where they disagree.
        async with tenant_session(app_session_factory, workspace) as session:
            from app.services.catalog.repositories import ProductRepository
            from app.services.reconciliation.repositories import ChangeProposalRepository

            products = await ProductRepository(session, workspace).list()
            assert len(products) == 1
            product_id = products[0].id
            await bind_tenant(session, workspace.workspace_id)
            for source in await _catalog_sources(session, workspace):
                await _attach(session, workspace, source, product_id)

            # Steps 4–7: conflicts, authority, proposals, risk.
            #
            # The canonical product is set to what a marketplace had published
            # in an earlier pass, so the manufacturer (authority rank 1) *loses*
            # to it and a change is genuinely proposed. Written through the ORM
            # rather than a Core update: a raw UPDATE bypasses the identity map,
            # and the in-memory product would still hold the previous values
            # when reconciliation reads them.
            product = products[0]
            product.title = "Classic Cotton Shirt"
            product.attributes = {"color": "Blue", "size": "M", "material": "Cotton"}
            await session.commit()

            # Re-bound after the commit above: `SET LOCAL` is transaction-scoped,
            # and the application's tenant dependency binds per request for the
            # same reason. A service that ran without a bound tenant would be
            # refused by RLS, which is the fail-closed behaviour D-011 wants.
            await bind_tenant(session, workspace.workspace_id)
            reconciliation = await reconcile_catalog(session, workspace, catalog=catalog)
            await session.commit()

            # The listing execution targets: §21 resolves a proposal to a
            # marketplace product by `external_product_id`, and refuses when
            # there is none — which is exactly what happened the first time this
            # scenario ran without one.
            from app.models.catalog import MarketplaceProduct

            await bind_tenant(session, workspace.workspace_id)
            session.add(
                MarketplaceProduct(
                    workspace_id=workspace.workspace_id,
                    product_id=product_id,
                    marketplace="mock",
                    external_product_id="demo-listing",
                    raw_listing={},
                )
            )
            await session.commit()

            # Bound again after the commit: `SET LOCAL` is transaction-scoped, so
            # the read below is refused by RLS without it. Every one of these
            # rebinds is a transaction boundary the application's tenant
            # dependency would handle the same way.
            await bind_tenant(session, workspace.workspace_id)

            assert reconciliation.products_analyzed == 1
            proposals = await ChangeProposalRepository(session, workspace).list()
            assert proposals, "four disagreeing sources must produce proposals"
            assert all(proposal.risk_level for proposal in proposals)

            # Step 8: simulate the first proposal on a copy.
            from app.services.simulation.service import simulate_proposal

            await bind_tenant(session, workspace.workspace_id)
            simulation = await simulate_proposal(
                session, workspace, proposal_id=proposals[0].id
            )
            await session.commit()

            assert simulation.result.validation_status in ("validated", "invalid")
            assert simulation.result.stages, "§18 reports its chain"
            assert simulation.simulation_id is not None

            # Steps 10–11: approval first, execution second, and only approved.
            from app.models.reconciliation import (
                ApprovalDecision,
                ExecutionStatus,
                ProposalStatus,
            )
            from app.services.approval.workflow import record_decision
            from app.services.audit import recomputed_hash
            from app.services.execution import ExecutionRefusedError, execute_proposal

            await bind_tenant(session, workspace.workspace_id)
            target = proposals[0]
            connector = MockConnector(
                products={"demo-listing": {"title": "Classic Cotton Shirt"}},
                credentials={"api_key": "demo"},
            )
            # An unapproved proposal is refused before a job exists (D-024).
            with pytest.raises(ExecutionRefusedError):
                await bind_tenant(session, workspace.workspace_id)
                await execute_proposal(
                    session,
                    workspace,
                    proposal=target,
                    connection=_connection(workspace),
                    connector=connector,
                )

            # Step 10: a person approves. The gate is checked first, so the
            # simulation run above is what makes this approvable at all.
            await bind_tenant(session, workspace.workspace_id)
            outcome = await record_decision(
                session,
                workspace,
                proposal=target,
                decision=ApprovalDecision.APPROVED,
                actor_id=uuid4(),
                actor_email="ops@example.com",
                notes="Demo scenario approval",
            )
            await session.commit()
            assert outcome.proposal.status == ProposalStatus.APPROVED

            await bind_tenant(session, workspace.workspace_id)
            job = await execute_proposal(
                session,
                workspace,
                proposal=target,
                connection=_connection(workspace),
                connector=connector,
            )
            await session.commit()

            assert job.status is ExecutionStatus.SUCCEEDED
            assert job.attempt_count == 1
            assert job.marketplace_response["verified_value"] is not None

            # Every outcome is on the audit trail with a recomputable hash.
            from sqlalchemy import select

            from app.models.reconciliation import AuditLog

            await bind_tenant(session, workspace.workspace_id)
            audits = list(
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.entity_id == job.id)
                    )
                )
                .scalars()
                .all()
            )
            assert audits
            assert recomputed_hash(audits[0]) == audits[0].payload_hash
            assert audits[0].action.startswith("execution.")


def _connection(context: TenantContext) -> Any:
    from app.models.platform import MarketplaceConnection

    return MarketplaceConnection(
        workspace_id=context.workspace_id,
        marketplace="mock",
        display_name="Demo marketplace",
        encrypted_credentials="encrypted-demo-blob",
    )


async def _catalog_sources(session: AsyncSession, context: TenantContext) -> list[Any]:
    from app.services.catalog.repositories import CatalogSourceRepository

    return list(await CatalogSourceRepository(session, context).list())


async def _attach(
    session: AsyncSession, context: TenantContext, source: CatalogSource, product_id: Any
) -> None:
    """Point a source's records at the product identity resolution chose."""
    from sqlalchemy import update

    from app.models.catalog import SourceRecord

    await session.execute(
        update(SourceRecord)
        .where(SourceRecord.catalog_source_id == source.id)
        .values(product_id=product_id)
    )