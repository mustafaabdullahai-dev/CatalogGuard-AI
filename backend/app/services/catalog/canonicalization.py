"""Canonicalization (Phase 11, §7, §8, §10, §50).

Turns inert raw `source_records` into canonical `Product` rows.

The pipeline reuses the domain services rather than reimplementing them:
`normalize_record` maps a supplier's column names onto canonical fields,
`build_candidate` reduces a record to the identity question, `resolve_identity`
answers it, and the answer becomes a `Product` plus its identifiers and a v1
snapshot.

Three rules carried from D-015 and §7:

  - **Only an exact identifier match auto-links.** A fuzzy or strong-text match is
    recorded as a merge candidate and a new product is still created, because a
    wrong merge is unrecoverable and a visible duplicate is not.
  - **An unmapped column is retained, never guessed.** Unrecognised columns stay
    in the raw record and are reported; normalizing them into a canonical field
    guessed from the name would corrupt data that later looks authoritative.
  - **History starts at creation** (§50). Every new product gets a v1
    `ProductVersion`, so "what did this look like when it arrived" is answerable
    from the first moment rather than only after the first edit.

Field-level resolution between two sources that disagree is deliberately **not**
here. That is the reconciliation engine (§10, Phase 12): doing it during intake
would mean a product could never be captured in the state its source described,
and every later conflict would be against an already-merged value.

Idempotent. A record is processed only while `source_records.product_id IS NULL`,
so re-running a canonicalization pass over the same source is a no-op rather than
a second copy of the catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import TenantContext
from app.models.catalog import (
    Catalog,
    CatalogSource,
    Product,
    ProductIdentifier,
    ProductStatus,
    ProductVersion,
    SourceRecord,
    SourceType,
)
from app.models.reconciliation import Conflict, ConflictType
from app.services.catalog.identity import (
    IdentityCandidate,
    MatchKind,
    resolve_identity,
)
from app.services.catalog.normalization import normalize_record
from app.services.catalog.repositories import (
    ConflictRepository,
    ProductIdentifierRepository,
    ProductRepository,
    ProductVersionRepository,
    SourceRecordRepository,
)

logger = get_logger(__name__)

# Canonical fields that have a dedicated column on `products`.
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
# Canonical fields with no column. They live in the `attributes` bag, which is
# what the JSON column exists for: they vary by category and are not filtered on.
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
_MEDIA_FIELDS = ("image_url",)
# Identifiers written to `product_identifiers`, in the order §8 lists them.
_IDENTIFIER_TYPES = ("gtin", "upc", "mpn", "sku")

# Upper bound on products scored against one incoming record. Identifier and
# brand blocking usually returns a handful; the cap stops a pathological brand
# collision from turning intake into a quadratic scan (§57).
MAX_CANDIDATES = 500

# Records processed in one pass unless a caller narrows it. Not a correctness
# limit: the pass is resumable because processed records are marked.
DEFAULT_BATCH = 10_000


@dataclass(frozen=True, slots=True)
class RecordMapping:
    """One raw record reduced to canonical fields and an identity question."""

    external_id: str
    fields: dict[str, object]
    columns: dict[str, object]
    attributes: dict[str, object]
    media: dict[str, object]
    unknown_fields: tuple[str, ...]
    candidate: IdentityCandidate


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    """A match the resolver found but refused to apply on its own."""

    created_product_id: str
    candidate_product_id: str | None
    confidence: float
    kind: str
    reason: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalizationSummary:
    records_examined: int
    products_created: int
    products_matched: int
    identifiers_added: int
    versions_written: int
    unmapped_records: int
    unmapped_columns: tuple[str, ...]
    merge_candidates: tuple[MergeCandidate, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "records_examined": self.records_examined,
            "products_created": self.products_created,
            "products_matched": self.products_matched,
            "identifiers_added": self.identifiers_added,
            "versions_written": self.versions_written,
            "unmapped_records": self.unmapped_records,
            "unmapped_columns": list(self.unmapped_columns),
            "merge_candidates": [
                {
                    "created_product_id": c.created_product_id,
                    "candidate_product_id": c.candidate_product_id,
                    "confidence": c.confidence,
                    "kind": c.kind,
                    "reason": c.reason,
                    "evidence": list(c.evidence),
                }
                for c in self.merge_candidates
            ],
        }


def _text(value: object) -> str | None:
    """A trimmed string, or None for a missing or blank value."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def map_record(
    raw: dict[str, object], *, external_id: str, source_type: SourceType
) -> RecordMapping:
    """Reduce one raw record to canonical fields and an identity candidate."""
    normalized = normalize_record(dict(raw))
    fields = dict(normalized.fields)

    columns = {name: fields[name] for name in _COLUMN_FIELDS if name in fields}
    attributes = {name: fields[name] for name in _ATTRIBUTE_FIELDS if name in fields}
    images = [fields[name] for name in _MEDIA_FIELDS if fields.get(name)]
    media: dict[str, object] = {"images": images} if images else {}

    candidate = IdentityCandidate(
        product_id=None,
        sku=_text(fields.get("sku")),
        gtin=_text(fields.get("gtin")),
        upc=_text(fields.get("upc")),
        mpn=_text(fields.get("mpn")),
        brand=_text(fields.get("brand")),
        manufacturer=_text(fields.get("manufacturer")),
        title=_text(fields.get("title")),
        description=_text(fields.get("description")),
        attributes=dict(attributes),
        source=source_type,
    )
    return RecordMapping(
        external_id=external_id,
        fields=fields,
        columns=columns,
        attributes=attributes,
        media=media,
        unknown_fields=tuple(normalized.unknown_fields),
        candidate=candidate,
    )


def deterministic_sku(mapping: RecordMapping, source_id: UUID) -> str:
    """The SKU a product will carry, chosen so a re-run produces the same one.

    `products.sku` is NOT NULL and unique per workspace, and a supplier feed
    frequently has no SKU column. Falling back to the first identifier keeps the
    value meaningful; failing that, a namespaced row number keeps it unique and
    stable. A UUID would be unique and unstable, which defeats the idempotency
    the rest of this module depends on.
    """
    for name in ("sku", "gtin", "upc", "mpn"):
        value = _text(mapping.fields.get(name))
        if value:
            return value[:255]
    return f"auto-{str(source_id)[:8]}-{mapping.external_id}"[:255]


def product_to_candidate(product: Product) -> IdentityCandidate:
    """View an existing product as a record the resolver can compare against."""
    return IdentityCandidate(
        product_id=str(product.id),
        sku=product.sku,
        gtin=product.gtin,
        upc=product.upc,
        mpn=product.mpn,
        brand=product.brand,
        manufacturer=product.manufacturer,
        title=product.title,
        description=product.description,
        attributes=dict(product.attributes or {}),
        source=SourceType.INTERNAL,
    )


async def load_candidates(
    db: AsyncSession, tenant: TenantContext, candidate: IdentityCandidate
) -> list[IdentityCandidate]:
    """Narrow the workspace to products worth scoring against `candidate`.

    Blocking, not filtering: the resolver still decides. Identity columns and
    brand are the two cheap, indexed keys available, and both are exact. Title
    blocking is not attempted in SQL -- a first-token `LIKE` would pull in every
    product whose name starts with the same word, and the exact-identifier rule
    means the extra recall could not change an auto-link decision anyway.
    """
    predicates = []
    for name in ("gtin", "upc", "mpn", "sku"):
        value = getattr(candidate, name, None)
        if value:
            column = getattr(Product, name)
            predicates.append(func.lower(column) == value.casefold())
    if candidate.brand:
        predicates.append(func.lower(Product.brand) == candidate.brand.casefold())
    if not predicates:
        return []

    result = await db.execute(
        select(Product)
        .where(Product.workspace_id == tenant.workspace_id)
        .where(Product.deleted_at.is_(None))
        .where(or_(*predicates))
        .limit(MAX_CANDIDATES)
    )
    return [product_to_candidate(row) for row in result.scalars().all()]


def build_product(mapping: RecordMapping, *, catalog_id: UUID, sku: str) -> Product:
    """Construct the canonical row from a mapped record.

    `status` is left at its default (`active`) deliberately. Canonicalization
    captures catalogue truth that already exists; it does not publish a change,
    and marking every captured product `draft` would hide a real catalogue behind
    a moderation state it was never submitted to.
    """
    columns = mapping.columns
    return Product(
        catalog_id=catalog_id,
        sku=sku,
        # Set here rather than relying on the column default, which SQLAlchemy
        # applies at flush. The v1 snapshot is written in the same transaction
        # as the insert, and a snapshot that recorded `status: None` for a brand
        # new product would read as a defect.
        status=ProductStatus.ACTIVE,
        mpn=_text(columns.get("mpn")),
        gtin=_text(columns.get("gtin")),
        upc=_text(columns.get("upc")),
        brand=_text(columns.get("brand")),
        manufacturer=_text(columns.get("manufacturer")),
        title=_text(columns.get("title")),
        description=_text(columns.get("description")),
        category_path=_text(columns.get("category_path")),
        attributes=dict(mapping.attributes),
        media=dict(mapping.media),
        version=1,
    )


def _identifier_values(mapping: RecordMapping) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for kind in _IDENTIFIER_TYPES:
        value = _text(mapping.fields.get(kind))
        if value:
            values.append((kind, value))
    return values


async def _attach_identifiers(
    db: AsyncSession,
    tenant: TenantContext,
    product: Product,
    mapping: RecordMapping,
    *,
    source_type: SourceType,
    source_record_id: UUID,
) -> int:
    """Link the record's identifiers to the product, without duplicating.

    The existence check is on the full `uq_identifiers_scope` tuple, so the same
    value arriving from two source records is recorded twice rather than treated
    as a repeat -- which is exactly the per-source granularity that scope was
    chosen for (see the model comment): an identifier repeated within one source
    is a defect, and across two it is corroboration.
    """
    repo = ProductIdentifierRepository(db, tenant)
    added = 0
    for kind, value in _identifier_values(mapping):
        exists = await repo.count(
            where=and_(
                ProductIdentifier.product_id == product.id,
                ProductIdentifier.identifier_type == kind,
                ProductIdentifier.identifier_value == value,
                ProductIdentifier.source_record_id == source_record_id,
            )
        )
        if exists:
            continue
        await repo.add(
            ProductIdentifier(
                product_id=product.id,
                identifier_type=kind,
                identifier_value=value,
                source_type=source_type,
                source_record_id=source_record_id,
            )
        )
        added += 1
    return added


def snapshot(product: Product) -> dict[str, object]:
    """The immutable record of a product's state at one version (§50)."""
    return {
        "sku": product.sku,
        "mpn": product.mpn,
        "gtin": product.gtin,
        "upc": product.upc,
        "brand": product.brand,
        "manufacturer": product.manufacturer,
        "title": product.title,
        "description": product.description,
        "category_path": product.category_path,
        "attributes": dict(product.attributes or {}),
        "media": dict(product.media or {}),
        "status": str(product.status),
        "is_parent": product.is_parent,
        "version": product.version,
    }


async def write_version(
    db: AsyncSession, tenant: TenantContext, product: Product, *, reason: str
) -> None:
    await ProductVersionRepository(db, tenant).add(
        ProductVersion(
            product_id=product.id,
            version=product.version,
            snapshot=snapshot(product),
            change_reason=reason,
        )
    )


async def _find_by_sku(
    db: AsyncSession, tenant: TenantContext, sku: str
) -> Product | None:
    result = await db.execute(
        select(Product)
        .where(Product.workspace_id == tenant.workspace_id)
        .where(Product.sku == sku)
        .where(Product.deleted_at.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _record_merge_candidate(
    db: AsyncSession,
    tenant: TenantContext,
    product: Product,
    *,
    candidate_product_id: str | None,
    confidence: float,
    kind: str,
    reason: str,
    evidence: list[str],
) -> None:
    """Persist a refused merge as an inspection item.

    Recorded as an `IDENTITY_MISMATCH` conflict rather than a new table, because
    it is exactly that: two records disagreeing about whether they are one
    product. That also means it surfaces in the same queue as every other
    conflict, with a confidence and its evidence attached.
    """
    await ConflictRepository(db, tenant).add(
        Conflict(
            product_id=product.id,
            reconciliation_run_id=None,
            conflict_type=ConflictType.IDENTITY_MISMATCH,
            field_name="identity",
            competing_values={
                "created_product_id": str(product.id),
                "candidate_product_id": candidate_product_id,
                "confidence": round(confidence, 4),
                "kind": kind,
                "reason": reason,
                "evidence": list(evidence),
            },
        )
    )


async def canonicalize_source(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    source: CatalogSource,
    catalog: Catalog,
    limit: int = DEFAULT_BATCH,
) -> CanonicalizationSummary:
    """Fold a source's un-processed records into canonical products.

    Resumable: only records with `product_id IS NULL` are read, and each is
    marked as it is processed. Running this twice over one source therefore
    creates nothing the second time, which is what lets an operator re-run it
    after a schema fix without duplicating the catalogue.
    """
    if source.workspace_id is not None and source.workspace_id != tenant.workspace_id:
        raise ValueError("source belongs to a different workspace than the tenant")

    record_repo = SourceRecordRepository(db, tenant)
    product_repo = ProductRepository(db, tenant)

    pending = await record_repo.list(
        where=and_(
            SourceRecord.catalog_source_id == source.id,
            SourceRecord.product_id.is_(None),
        ),
        order_by=SourceRecord.external_id,
        limit=limit,
    )

    products_created = 0
    products_matched = 0
    identifiers_added = 0
    versions_written = 0
    unmapped_records = 0
    unmapped_columns: set[str] = set()
    merge_candidates: list[MergeCandidate] = []

    for record in pending:
        mapping = map_record(
            dict(record.raw_data or {}),
            external_id=record.external_id,
            source_type=source.source_type,
        )
        if mapping.unknown_fields:
            unmapped_records += 1
            unmapped_columns.update(mapping.unknown_fields)

        match = resolve_identity(
            mapping.candidate, await load_candidates(db, tenant, mapping.candidate)
        )

        product: Product | None = None
        if match.kind is MatchKind.EXACT_IDENTIFIER and match.proposed_product_id:
            # Identity was stated, not inferred (D-015): the one case that links
            # without a human.
            product = await product_repo.get(UUID(match.proposed_product_id))
            if product is not None:
                products_matched += 1

        if product is None:
            sku = deterministic_sku(mapping, source.id)
            existing = await _find_by_sku(db, tenant, sku)
            if existing is not None:
                # The workspace already uses this SKU. Linking rather than
                # inserting is required -- `uq_products_workspace_sku` would
                # reject the insert -- and it is flagged, because reusing a SKU
                # is exactly the kind of claim a human should confirm.
                product = existing
                products_matched += 1
                reason = f"sku {sku} already exists in this workspace"
                evidence: tuple[str, ...] = (
                    f"incoming {source.name} record {record.external_id}",
                )
                await _record_merge_candidate(
                    db,
                    tenant,
                    product,
                    candidate_product_id=None,
                    confidence=1.0,
                    kind="sku_collision",
                    reason=reason,
                    evidence=list(evidence),
                )
                merge_candidates.append(
                    MergeCandidate(
                        created_product_id=str(product.id),
                        candidate_product_id=None,
                        confidence=1.0,
                        kind="sku_collision",
                        reason=reason,
                        evidence=evidence,
                    )
                )
            else:
                product = build_product(mapping, catalog_id=catalog.id, sku=sku)
                await product_repo.add(product)
                products_created += 1
                await write_version(
                    db,
                    tenant,
                    product,
                    reason=f"created from {source.name} record {record.external_id}",
                )
                versions_written += 1
                if match.match:
                    # Ambiguous identity: a product exists, but only text
                    # similarity connects them. Create the product anyway and
                    # surface the pair for a human (D-015).
                    evidence = tuple(item.describe() for item in match.evidence)
                    await _record_merge_candidate(
                        db,
                        tenant,
                        product,
                        candidate_product_id=match.proposed_product_id,
                        confidence=match.confidence,
                        kind=match.kind.value,
                        reason=match.reason,
                        evidence=list(evidence),
                    )
                    merge_candidates.append(
                        MergeCandidate(
                            created_product_id=str(product.id),
                            candidate_product_id=match.proposed_product_id,
                            confidence=match.confidence,
                            kind=match.kind.value,
                            reason=match.reason,
                            evidence=evidence,
                        )
                    )

        identifiers_added += await _attach_identifiers(
            db,
            tenant,
            product,
            mapping,
            source_type=source.source_type,
            source_record_id=record.id,
        )
        record.product_id = product.id

    await db.flush()
    summary = CanonicalizationSummary(
        records_examined=len(pending),
        products_created=products_created,
        products_matched=products_matched,
        identifiers_added=identifiers_added,
        versions_written=versions_written,
        unmapped_records=unmapped_records,
        unmapped_columns=tuple(sorted(unmapped_columns)),
        merge_candidates=tuple(merge_candidates),
    )
    logger.info(
        "canonicalization_complete",
        workspace_id=str(tenant.workspace_id),
        source_id=str(source.id),
        records_examined=summary.records_examined,
        products_created=summary.products_created,
        products_matched=summary.products_matched,
        merge_candidates=len(summary.merge_candidates),
    )
    return summary


__all__ = [
    "CanonicalizationSummary",
    "MergeCandidate",
    "RecordMapping",
    "build_product",
    "canonicalize_source",
    "deterministic_sku",
    "load_candidates",
    "map_record",
    "product_to_candidate",
    "snapshot",
    "write_version",
]