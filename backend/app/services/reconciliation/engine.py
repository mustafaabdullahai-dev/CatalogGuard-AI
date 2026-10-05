"""Reconciliation engine (Phase 12, §11, §12, §15, §16).

The stage that turns "several sources disagree" into something a human can act
on: a `ReconciliationRun`, `Conflict` rows recording what each source said, and
`ChangeProposal` rows carrying a proposed value, its evidence, and a
**deterministically** risk-scored level.

Boundary that shapes everything here:

  - **Authority decides nothing on its own.** D-002's precedence ladder proposes
    a value; §16 forbids anything but the deterministic risk engine from scoring
    it; and no row reaches the execution layer without a human (§15, §38).
  - **A conflict is not a proposal.** A disagreement is recorded whether or not it
    resolves; a proposal exists only when authority produced a value that differs
    from what the workspace currently believes. A tie therefore writes a conflict
    and no proposal, exactly as §11 requires.
  - **Intake is not reconciliation.** Records arrive as their source described them
    (D-019); this pass is where they are allowed to disagree.

Configuration: source authority uses the D-002 defaults baked into
`services.catalog.authority.FIELD_RANK_OVERRIDES`. `workspace.source_authority_config`
exists but is not read yet -- per §12 the ladder was confirmed before
implementation rather than assumed, and wiring per-workspace overrides is a
deliberate next step rather than something done here silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import TenantContext, utcnow
from app.models.catalog import (
    Catalog,
    CatalogSource,
    MarketplaceProduct,
    Product,
    SourceRecord,
    SourceType,
)
from app.models.reconciliation import (
    ChangeProposal,
    Conflict,
    ProposalStatus,
    ReconciliationRun,
    RiskAssessment,
)
from app.services.catalog.canonicalization import map_record
from app.services.catalog.repositories import ProductRepository
from app.services.reconciliation.conflicts import (
    DetectedConflict,
    FieldObservation,
    detect_field_conflicts,
    detect_missing_attribute_conflicts,
)
from app.services.reconciliation.repositories import (
    ChangeProposalRepository,
    ConflictRepository,
    ReconciliationRunRepository,
    RiskAssessmentRepository,
)
from app.services.reconciliation.risk import (
    SEO_SENSITIVE_FIELDS,
    RiskResult,
    assess,
    context_from_change,
)

logger = get_logger(__name__)

# Canonical fields a product can carry. Kept beside `services.catalog`'s mapping
# rather than imported: that tuple is an implementation detail of intake, and
# these are the fields reconciliation is willing to argue about.
_COLUMN_FIELDS = (
    "sku",
    "mpn",
    "gtin",
    "upc",
    "brand",
    "manufacturer",
    "title",
    "description",
    "category_path",
)
_ATTRIBUTE_FIELDS = (
    "color",
    "size",
    "material",
    "style",
    "price",
    "cost",
    "currency",
    "weight",
    "length",
    "width",
    "height",
)
_TRACKED_FIELDS = (*_COLUMN_FIELDS, *_ATTRIBUTE_FIELDS, "image_url")

# Products per run. Bounded so one request cannot walk an entire catalogue while
# a caller holds a transaction open (§57).
DEFAULT_PRODUCT_LIMIT = 500

# Proposal statuses a re-run is allowed to supersede. `APPROVED` and everything
# after it are excluded: overwriting a decision a human already made would
# destroy the audit trail (D-013), and `REJECTED_BY_VALIDATION`/`FAILED` record
# a finding rather than a pending item.
_OPEN_STATUSES = (
    ProposalStatus.PROPOSED,
    ProposalStatus.VALIDATED,
    ProposalStatus.SIMULATED,
    ProposalStatus.PENDING_APPROVAL,
)

# `change_proposals.confidence` is CHECK-constrained to (0, 1) so no value can be
# read as certainty or as permission (§37). Clamp rather than reject: a confident
# conflict of 1.0 is still not permission to execute.
_MAX_CONFIDENCE = 0.999
_MIN_CONFIDENCE = 0.001

# `risk_assessments.numeric_score` is CHECK-constrained to 0..100, while the risk
# engine sums weights that can reach 422. Clamping loses nothing: the band comes
# from the raw total, and every factor that produced it is stored alongside, so
# the raw score stays recoverable.
_MAX_STORED_SCORE = 100.0


@dataclass(frozen=True, slots=True)
class ProposalPlan:
    """A proposal before it becomes a row: everything deterministic is decided."""

    field_name: str
    current_value: object
    proposed_value: object
    reason: str
    evidence: list[dict[str, object]]
    confidence: float
    conflict: DetectedConflict
    risk: RiskResult


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    run_id: str
    catalog_id: str
    catalog_version: int
    products_analyzed: int
    conflicts_found: int
    proposals_created: int
    proposals_superseded: int
    status: str

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "catalog_id": self.catalog_id,
            "catalog_version": self.catalog_version,
            "products_analyzed": self.products_analyzed,
            "conflicts_found": self.conflicts_found,
            "proposals_created": self.proposals_created,
            "proposals_superseded": self.proposals_superseded,
            "status": self.status,
        }


def product_values(product: Product) -> dict[str, object]:
    """The workspace's current belief about a product, field by field."""
    values: dict[str, object] = {name: getattr(product, name) for name in _COLUMN_FIELDS}
    values.update(dict(product.attributes or {}))
    return values


def build_observations(
    product: Product,
    records: list[tuple[SourceRecord, SourceType]],
) -> dict[str, list[FieldObservation]]:
    """Group every source's claim about a field, including the canonical one.

    Two deliberate details:

    - The product itself contributes an observation flagged
      `is_current_canonical`. Without it, sources could agree with each other and
      disagree with what is stored, and agreement would produce no finding at all.
    - A record that lacks a field still contributes a `None` observation for it,
      so `detect_missing_attribute_conflicts` can see who is absent. Reconciliation
      has to know that a source is silent, not only that it disagrees.
    """
    observations: dict[str, list[FieldObservation]] = {name: [] for name in _TRACKED_FIELDS}

    updated_at = (
        product.updated_at.isoformat() if product.updated_at is not None else None
    )
    for name, value in product_values(product).items():
        if name in observations:
            observations[name].append(
                FieldObservation(
                    source=SourceType.INTERNAL,
                    value=value,
                    source_label="canonical",
                    record_id=str(product.id),
                    is_current_canonical=True,
                    updated_at=updated_at,
                )
            )

    for record, source_type in records:
        fields = map_record(
            dict(record.raw_data or {}),
            external_id=record.external_id,
            source_type=source_type,
        ).fields
        observed_at = (
            record.sequence_timestamp.isoformat()
            if record.sequence_timestamp is not None
            else None
        )
        for name in _TRACKED_FIELDS:
            observations[name].append(
                FieldObservation(
                    source=source_type,
                    value=fields.get(name),
                    record_id=str(record.id),
                    is_current_canonical=False,
                    updated_at=observed_at,
                )
            )
    return observations


def _comparable(value: object) -> str:
    """A form that makes `10.0` and `"10.00"` compare equal.

    Separate from `conflicts._normal_form`, which is keyed to grouping *sources*.
    This one only has to answer "is the stored value the one being proposed", and
    an ERP that writes `10.0` while the catalogue holds `10.00` is agreement.
    """
    if value is None:
        return "∅"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return f"num:{float(value):.6f}"
    text = str(value).strip()
    try:
        return f"num:{float(text.replace(',', '')):.6f}"
    except ValueError:
        return text.casefold()


def _clamp_confidence(value: float) -> float:
    return max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, value))


def plan_proposals(
    product_fields: dict[str, object],
    conflicts: list[DetectedConflict],
    *,
    affected_channels: list[str],
    affected_products: list[str],
) -> list[ProposalPlan]:
    """Turn resolvable conflicts into risk-scored proposals.

    Three filters, in order of cheapness: authority must have produced a value, a
    value must actually be proposed, and it must differ from what is stored. The
    third is what stops the approval queue filling with no-op proposals whose only
    effect would be to restate the current value.
    """
    plans: list[ProposalPlan] = []
    for conflict in conflicts:
        if not conflict.authority.resolved:
            # A tie is recorded as a conflict and proposes nothing (§11).
            continue
        if conflict.proposed_value is None:
            continue

        current = product_fields.get(conflict.field_name)
        if _comparable(current) == _comparable(conflict.proposed_value):
            continue

        confidence = _clamp_confidence(conflict.confidence)
        risk = assess(
            context_from_change(
                field_name=conflict.field_name,
                current_value=current,
                proposed_value=conflict.proposed_value,
                # The run supplies these: a change to one product reaches the
                # channels that product sells on and the product itself.
                affected_channels=affected_channels,
                affected_product_ids=affected_products,
                confidence=confidence,
                evidence=list(conflict.evidence),
            )
        )
        plans.append(
            ProposalPlan(
                field_name=conflict.field_name,
                current_value=current,
                proposed_value=conflict.proposed_value,
                reason=conflict.explanation,
                evidence=list(conflict.evidence),
                confidence=confidence,
                conflict=conflict,
                risk=risk,
            )
        )
    return plans


def change_ref(run_id: UUID, sequence: int) -> str:
    """`CHG-<first run segment>-0001`, unique per workspace by construction.

    The run id is already unique workspace-wide and `change_proposals` carries
    `uq_proposals_change_ref`, so prefixing with it makes collisions impossible
    without a counter query — and the reference stays human-readable, which is
    the point of having one (§15).
    """
    return f"CHG-{str(run_id).split('-')[0].upper()}-{sequence:04d}"


async def _supersede_product(
    db: AsyncSession, tenant: TenantContext, product_id: UUID
) -> int:
    """Close a product's open findings so this run replaces them.

    Without this, re-running reconciliation would double the approval queue on
    every attempt. Only *open* items are superseded, and only for the fields this
    engine owns: an identity merge candidate from D-019, or a proposal someone
    already approved, is never touched.
    """
    superseded = 0

    conflicts = await ConflictRepository(db, tenant).list(
        limit=2000,
        where=and_(
            Conflict.product_id == product_id,
            Conflict.field_name.in_(_TRACKED_FIELDS),
            Conflict.resolved_at.is_(None),
        ),
    )
    conflict_repo = ConflictRepository(db, tenant)
    for row in conflicts:
        await conflict_repo.delete(row.id)
        superseded += 1

    proposals = await ChangeProposalRepository(db, tenant).list(
        limit=2000,
        where=and_(
            ChangeProposal.product_id == product_id,
            ChangeProposal.field_name.in_(_TRACKED_FIELDS),
            ChangeProposal.status.in_(_OPEN_STATUSES),
        ),
    )
    proposal_repo = ChangeProposalRepository(db, tenant)
    for proposal_row in proposals:
        await proposal_repo.delete(proposal_row.id)
        superseded += 1

    return superseded


async def _persist_conflict(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    run_id: UUID,
    product_id: UUID,
    conflict: DetectedConflict,
) -> None:
    """Record the disagreement, and only the disagreement.

    `resolved_value`, `resolved_by` and `resolved_at` stay NULL on purpose. The
    authority's *suggestion* lives on the proposal; the conflict is closed when a
    human decides (Phase 18), not when an algorithm proposes. Storing the
    suggestion here would make an algorithm look like a decision.
    """
    await ConflictRepository(db, tenant).add(
        Conflict(
            product_id=product_id,
            reconciliation_run_id=run_id,
            conflict_type=conflict.conflict_type,
            field_name=conflict.field_name,
            competing_values=conflict.available_values,
            confidence=conflict.confidence,
        )
    )


async def _persist_proposal(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    run_id: UUID,
    product_id: UUID,
    plan: ProposalPlan,
    sequence: int,
    channels: list[str],
) -> None:
    """Write the proposal and its risk evidence.

    `validation_status` is `pending`: the sandbox (Phase 17) has not run against
    this change. Claiming `validated` before a validator exists would be exactly
    the kind of unearned success §67 forbids.
    """
    proposal = ChangeProposal(
        product_id=product_id,
        reconciliation_run_id=run_id,
        change_ref=change_ref(run_id, sequence),
        field_name=plan.field_name,
        current_value=plan.current_value,
        proposed_value=plan.proposed_value,
        reason=plan.reason,
        evidence=plan.evidence,
        confidence=plan.confidence,
        validation_status="pending",
        validation_errors=[],
        risk_level=plan.risk.level,
        # Every proposal requires a human (§5, §38). The risk level decides how
        # many approvers — that rule lives in Phase 18 — not whether one is needed.
        requires_approval=True,
        seo_impact=(
            "HIGH"
            if plan.field_name.casefold() in SEO_SENSITIVE_FIELDS
            else "NONE"
        ),
        compliance_impact="NEUTRAL",
        affected_channels=list(channels),
        affected_relationships=[],
        status=ProposalStatus.PENDING_APPROVAL,
    )
    await ChangeProposalRepository(db, tenant).add(proposal)
    await RiskAssessmentRepository(db, tenant).add(
        RiskAssessment(
            change_proposal_id=proposal.id,
            risk_level=plan.risk.level,
            numeric_score=float(min(_MAX_STORED_SCORE, float(plan.risk.numeric_score))),
            factors=[factor.as_dict() for factor in plan.risk.factors],
            escalated_reason=plan.risk.escalated_reason,
        )
    )


async def _load_inputs(
    db: AsyncSession, tenant: TenantContext, product_ids: list[UUID]
) -> tuple[dict[UUID, list[tuple[SourceRecord, SourceType]]], dict[UUID, set[str]]]:
    """Fetch a run's inputs in two queries rather than two per product.

    One query per product would be N round trips to Postgres on the exact path
    most likely to hold hundreds of products; §57 is explicit that large catalogs
    must not be driven by per-row queries.
    """
    if not product_ids:
        return {}, {}

    record_rows = await db.execute(
        select(SourceRecord, CatalogSource.source_type)
        .join(CatalogSource, SourceRecord.catalog_source_id == CatalogSource.id)
        .where(SourceRecord.workspace_id == tenant.workspace_id)
        .where(SourceRecord.product_id.in_(product_ids))
        .order_by(SourceRecord.sequence_timestamp)
    )
    records: dict[UUID, list[tuple[SourceRecord, SourceType]]] = {}
    for record, source_type in record_rows.all():
        if record.product_id is None:
            # Filtered to a non-empty id list in SQL, so this cannot happen —
            # but `source_records.product_id` is nullable, and assuming the
            # query already proved it would just move the None somewhere later.
            continue
        records.setdefault(record.product_id, []).append((record, source_type))

    channel_rows = await db.execute(
        select(MarketplaceProduct.product_id, MarketplaceProduct.marketplace)
        .where(MarketplaceProduct.workspace_id == tenant.workspace_id)
        .where(MarketplaceProduct.product_id.in_(product_ids))
    )
    channels: dict[UUID, set[str]] = {}
    for product_id, marketplace in channel_rows.all():
        if product_id is None:
            # `marketplace_products.product_id` is nullable (ondelete SET NULL);
            # a listing whose product was deleted still needs to be iterable.
            continue
        channels.setdefault(product_id, set()).add(marketplace)

    return records, channels


async def reconcile_catalog(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    catalog: Catalog,
    limit: int = DEFAULT_PRODUCT_LIMIT,
) -> ReconciliationSummary:
    """Run one reconciliation pass over a catalog.

    The run is created before any product is touched and completed after, so a
    crash mid-way leaves a `running` run with a partial product count rather than
    no record of the attempt at all (§50: findings bind to the state they saw).
    """
    run = await ReconciliationRunRepository(db, tenant).add(
        ReconciliationRun(
            catalog_id=catalog.id,
            catalog_version=catalog.current_version,
            status="running",
        )
    )

    products = await ProductRepository(db, tenant).list(
        where=Product.catalog_id == catalog.id,
        order_by=Product.sku,
        limit=limit,
    )
    records_by_product, channels_by_product = await _load_inputs(
        db, tenant, [product.id for product in products]
    )

    conflicts_found = 0
    proposals_created = 0
    proposals_superseded = 0
    sequence = 0

    for product in products:
        records = records_by_product.get(product.id, [])
        channels = sorted(channels_by_product.get(product.id, set()))
        product_id_str = str(product.id)

        observations = build_observations(product, records)
        detected = [
            *detect_field_conflicts(
                product_id_str,
                observations,
                affected_channels=channels,
                affected_products=[product_id_str],
            ),
            *detect_missing_attribute_conflicts(product_id_str, observations),
        ]

        proposals_superseded += await _supersede_product(db, tenant, product.id)

        for conflict in detected:
            await _persist_conflict(
                db,
                tenant,
                run_id=run.id,
                product_id=product.id,
                conflict=conflict,
            )
            conflicts_found += 1

        for plan in plan_proposals(
            product_values(product),
            detected,
            affected_channels=channels,
            affected_products=[product_id_str],
        ):
            sequence += 1
            await _persist_proposal(
                db,
                tenant,
                run_id=run.id,
                product_id=product.id,
                plan=plan,
                sequence=sequence,
                channels=channels,
            )
            proposals_created += 1

    run.products_analyzed = len(products)
    run.conflicts_found = conflicts_found
    run.status = "completed"
    run.completed_at = utcnow()
    await db.flush()

    summary = ReconciliationSummary(
        run_id=str(run.id),
        catalog_id=str(catalog.id),
        catalog_version=catalog.current_version,
        products_analyzed=len(products),
        conflicts_found=conflicts_found,
        proposals_created=proposals_created,
        proposals_superseded=proposals_superseded,
        status=run.status,
    )
    logger.info(
        "reconciliation_complete",
        workspace_id=str(tenant.workspace_id),
        catalog_id=str(catalog.id),
        products_analyzed=summary.products_analyzed,
        conflicts_found=summary.conflicts_found,
        proposals_created=summary.proposals_created,
        proposals_superseded=summary.proposals_superseded,
    )
    return summary


__all__ = [
    "DEFAULT_PRODUCT_LIMIT",
    "ProposalPlan",
    "ReconciliationSummary",
    "build_observations",
    "change_ref",
    "plan_proposals",
    "product_values",
    "reconcile_catalog",
]