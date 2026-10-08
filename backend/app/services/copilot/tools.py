"""Read-only tools over the caller's catalog (Phase 22, §23, §31).

The copilot's whole claim is that it *asks* rather than remembers. These five
tools are how it asks. Each one is a thin adapter over a service or repository
that already exists, so a tool cannot invent catalog behaviour the API does not
have, and each returns rows that are already tenant-scoped by the repository
that read them.

Each row below is one of §31's example questions, and the column on the right
names the code the answer comes from.

| Tool | Answers | Backs |
|---|---|---|
| `product_lookup` | explain a product; why SKU X is unsafe | §30 detail, which has no route yet |
| `conflict_search` | find products with conflicting colours | §11 conflicts |
| `change_search` | show high-risk changes; Amazon vs Shopify | §15 proposals |
| `compliance_search` | compliance issues in my Amazon catalog | §13 retrieval, cited |
| `sandbox_simulator` | simulate changing the parent of product Y | §18 sandbox |

Three properties are deliberate and each is asserted in the tests:

1. **Read-only.** Every tool takes `read` except the simulator, which takes
   `write` because it records a `SimulationRun` row. No tool can create a
   proposal, approve one, or touch a product (D-024).
2. **Bounded output.** Row counts are clamped by `copilot_max_rows_per_tool`
   before they reach the model: a tool result arrives in a prompt, and a
   thousand-row result is a context-window failure that looks like a bad answer.
3. **The tenant's rows only.** Session and tenant are closed over by the
   builder, so no argument can widen a tool's scope.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import ToolCallRecorder, ToolContext, ToolRegistry, ToolSpec
from app.agents.tools.spec import ToolArgs
from app.core.config import Settings, get_settings
from app.db.base import TenantContext
from app.models.catalog import Product
from app.models.reconciliation import ChangeProposal, Conflict, RiskLevel
from app.rag.service import MetadataFilter
from app.rag.service import search as rag_search
from app.services.catalog.repositories import ProductRepository
from app.services.reconciliation.repositories import (
    ChangeProposalRepository,
    ConflictRepository,
)
from app.services.simulation.service import SimulationRefusedError, simulate_proposal

MAX_TEXT = 300
MAX_IDS = 12


# --------------------------------------------------------------------------
# arguments


class ProductLookupArgs(ToolArgs):
    """Find products by SKU, product id or text, with their open history."""

    sku: str | None = Field(default=None, max_length=120)
    product_id: UUID | None = None
    text: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=5, ge=1, le=50)


class ConflictSearchArgs(ToolArgs):
    """Find recorded disagreements, optionally narrowed to a field."""

    field: str | None = Field(default=None, max_length=80)
    conflict_type: str | None = Field(default=None, max_length=40)
    product_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=50)


class ChangeSearchArgs(ToolArgs):
    """Find change proposals by risk, channel, field or status."""

    risk_level: RiskLevel | None = None
    channel: str | None = Field(default=None, max_length=40)
    field: str | None = Field(default=None, max_length=80)
    status: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=10, ge=1, le=50)


class ComplianceSearchArgs(ToolArgs):
    """Search the compliance corpus for evidence about a rule or a claim."""

    query: str = Field(min_length=2, max_length=400)
    marketplace_code: str | None = Field(default=None, max_length=32)
    top_k: int = Field(default=5, ge=1, le=20)


class SandboxSimulatorArgs(ToolArgs):
    """Run the §18 chain for one proposal. Writes a simulation run, nothing else."""

    proposal_id: UUID


# --------------------------------------------------------------------------
# results


class ProductRow(BaseModel):
    product_id: UUID
    sku: str
    title: str | None
    brand: str | None
    category_path: str | None
    status: str
    version: int


class ConflictRow(BaseModel):
    conflict_id: UUID
    product_id: UUID | None
    field: str
    conflict_type: str
    competing_values: dict[str, Any]
    confidence: float | None


class ProposalRow(BaseModel):
    proposal_id: UUID
    change_ref: str
    product_id: UUID | None
    field: str
    current_value: Any
    proposed_value: Any
    risk_level: str
    requires_approval: bool
    validation_status: str
    confidence: float
    channels: list[Any]
    reason: str = Field(max_length=MAX_TEXT)


class ProductLookupResult(BaseModel):
    products: list[ProductRow]
    conflicts: list[ConflictRow]
    proposals: list[ProposalRow]
    note: str


class ConflictSearchResult(BaseModel):
    conflicts: list[ConflictRow]
    products: list[ProductRow]
    total_open: int
    note: str


class ChangeSearchResult(BaseModel):
    proposals: list[ProposalRow]
    total_matching: int
    note: str


class ComplianceHitRow(BaseModel):
    document_title: str
    section_reference: str | None
    marketplace_code: str | None
    score: float
    signals: list[str]
    excerpt: str = Field(max_length=MAX_TEXT)


class ComplianceSearchResult(BaseModel):
    hits: list[ComplianceHitRow]
    signals: list[str]
    note: str


class SandboxSimulatorResult(BaseModel):
    proposal_id: UUID
    change_ref: str
    validation_status: str
    overall_risk: str
    stages: list[dict[str, Any]]
    diff_changed_fields: int
    recommendation: str | None
    note: str


# --------------------------------------------------------------------------
# handlers (bound per request by the builder below)


async def _product_lookup(
    args: ProductLookupArgs, tenant: TenantContext, session: AsyncSession, cap: int
) -> ProductLookupResult:
    """One product with the disagreement and proposal history behind it.

    This is the answer to "why is SKU X unsafe to modify": the risk band lives on
    the proposal, but the *reasons* are the conflicts and the affected channels,
    so both come back with it rather than requiring three tool calls.
    """
    where: Any = None
    if args.product_id is not None:
        where = Product.id == args.product_id
    elif args.sku:
        where = Product.sku == args.sku
    elif args.text:
        needle = f"%{args.text.strip().lower()}%"
        where = Product.sku.ilike(needle) | Product.title.ilike(needle)

    products = await ProductRepository(session, tenant).list(
        limit=min(args.limit, cap), where=where, order_by=Product.sku
    )
    product_ids = [product.id for product in products]
    conflicts = (
        await ConflictRepository(session, tenant).list(
            limit=cap,
            where=Conflict.product_id.in_(product_ids),
            order_by=Conflict.created_at,
        )
        if product_ids
        else []
    )
    proposals = (
        await ChangeProposalRepository(session, tenant).list(
            limit=cap,
            where=ChangeProposal.product_id.in_(product_ids),
            order_by=ChangeProposal.created_at.desc(),
        )
        if product_ids
        else []
    )
    found = [row(product) for product in products]
    if not found:
        return ProductLookupResult(
            products=[],
            conflicts=[],
            proposals=[],
            note=(
                "No product matched. Say so rather than describing a plausible "
                "one: an invented SKU is the worst thing this tool can return."
            ),
        )
    return ProductLookupResult(
        products=found,
        conflicts=[conflict_row(item) for item in conflicts[:cap]],
        proposals=[proposal_row(item) for item in proposals[:cap]],
        note=(
            f"{len(found)} product(s), their open disagreements and the proposals "
            "raised against them. A proposal that requires approval still needs a "
            "human (D-020)."
        ),
    )


async def _conflict_search(
    args: ConflictSearchArgs, tenant: TenantContext, session: AsyncSession, cap: int
) -> ConflictSearchResult:
    filters: list[Any] = [Conflict.resolved_at.is_(None)]
    if args.field:
        filters.append(Conflict.field_name == args.field)
    if args.conflict_type:
        filters.append(Conflict.conflict_type == args.conflict_type)
    if args.product_id is not None:
        filters.append(Conflict.product_id == args.product_id)
    where = filters[0]
    for condition in filters[1:]:
        where = where & condition

    total = await ConflictRepository(session, tenant).count(where=where)
    conflicts = await ConflictRepository(session, tenant).list(
        limit=min(args.limit, cap), where=where, order_by=Conflict.created_at.desc()
    )
    product_ids = [item.product_id for item in conflicts if item.product_id is not None]
    products = (
        await ProductRepository(session, tenant).list(
            limit=min(len(product_ids), cap), where=Product.id.in_(product_ids)
        )
        if product_ids
        else []
    )
    return ConflictSearchResult(
        conflicts=[conflict_row(item) for item in conflicts],
        products=[row(product) for product in products],
        total_open=total,
        note=(
            "Open disagreements only; a resolved conflict is history. "
            f"{total} match the filter; at most {min(args.limit, cap)} are listed."
        ),
    )


async def _change_search(
    args: ChangeSearchArgs, tenant: TenantContext, session: AsyncSession, cap: int
) -> ChangeSearchResult:
    filters: list[Any] = []
    if args.risk_level is not None:
        filters.append(ChangeProposal.risk_level == args.risk_level)
    if args.field:
        filters.append(ChangeProposal.field_name == args.field)
    if args.status:
        filters.append(ChangeProposal.status == args.status)
    where: Any = None
    if filters:
        where = filters[0]
        for condition in filters[1:]:
            where = where & condition

    candidates = await ChangeProposalRepository(session, tenant).list(
        limit=500, where=where, order_by=ChangeProposal.created_at.desc()
    )
    if args.channel:
        needle = args.channel.strip().lower()
        candidates = [
            proposal
            for proposal in candidates
            if any(str(channel).lower() == needle for channel in proposal.affected_channels or [])
        ]
    # Risk level is the ordering the approval queue itself uses (§16): the
    # urgent first. `confidence` is deliberately not a sort key — it is evidence
    # for a reviewer, not a ranking of what deserves to happen (§37).
    ordered = sorted(candidates, key=lambda proposal: -proposal.risk_level.rank)
    return ChangeSearchResult(
        proposals=[proposal_row(item) for item in ordered[: min(args.limit, cap)]],
        total_matching=len(ordered),
        note=(
            f"{len(ordered)} proposal(s) match, ordered by risk band. Confidence "
            "is evidence, never permission: it does not justify a change."
        ),
    )


async def _compliance_search(
    args: ComplianceSearchArgs, tenant: TenantContext, session: AsyncSession, cap: int
) -> ComplianceSearchResult:
    try:
        result = await rag_search(
            session,
            tenant,
            query=args.query,
            metadata_filter=MetadataFilter(marketplace_code=args.marketplace_code),
            top_k=min(args.top_k, cap),
        )
    except ValueError:
        return ComplianceSearchResult(
            hits=[],
            signals=[],
            note="The query was empty. Ask about a rule, a claim or a category.",
        )
    return ComplianceSearchResult(
        hits=[
            ComplianceHitRow(
                document_title=hit.document_title,
                section_reference=hit.section_reference,
                marketplace_code=hit.marketplace_code,
                score=round(hit.score, 4),
                signals=list(hit.signals),
                excerpt=hit.content[:MAX_TEXT],
            )
            for hit in result.hits
        ],
        signals=list(result.signals),
        # The service reports what ranked the hits; the disclaimer is this tool's,
        # because a tool result is read as an answer rather than as evidence.
        note=(
            "Evidence for a recommendation, not a compliance decision: "
            f"ranked by {result.signals or 'no signal'}. Cite the section "
            "reference in any statement you make from these chunks."
        ),
    )


async def _sandbox_simulator(
    args: SandboxSimulatorArgs, tenant: TenantContext, session: AsyncSession, cap: int
) -> SandboxSimulatorResult:
    try:
        outcome = await simulate_proposal(session, tenant, proposal_id=args.proposal_id)
    except SimulationRefusedError as exc:
        return SandboxSimulatorResult(
            proposal_id=args.proposal_id,
            change_ref="",
            validation_status="refused",
            overall_risk="",
            stages=[],
            diff_changed_fields=0,
            recommendation=None,
            note=f"This proposal cannot be simulated: {exc.reason.replace('_', ' ')}.",
        )
    result = outcome.result
    diff_rows = result.diff.get("rows") if isinstance(result.diff, dict) else None
    changed = sum(
        1
        for entry in (diff_rows or [])
        if isinstance(entry, dict) and entry.get("indicator") != "unchanged"
    )
    impact = result.impact_report if isinstance(result.impact_report, dict) else {}
    return SandboxSimulatorResult(
        proposal_id=args.proposal_id,
        change_ref=outcome.proposal.change_ref,
        validation_status=result.validation_status,
        overall_risk=str(result.overall_risk),
        stages=[
            {"name": stage.name, "status": stage.status, "findings": list(stage.findings)[:5]}
            for stage in result.stages[:cap]
        ],
        diff_changed_fields=changed,
        recommendation=impact.get("recommendation"),
        note=(
            "A prediction on a copy: nothing was applied. A stage reported as "
            "skipped did not pass, it did not run."
        ),
    )


# --------------------------------------------------------------------------
# row helpers (the wire shape the model reads)


def row(product: Product) -> ProductRow:
    return ProductRow(
        product_id=product.id,
        sku=product.sku,
        title=product.title,
        brand=product.brand,
        category_path=product.category_path,
        status=str(product.status),
        version=product.version,
    )


def conflict_row(conflict: Conflict) -> ConflictRow:
    return ConflictRow(
        conflict_id=conflict.id,
        product_id=conflict.product_id,
        field=conflict.field_name,
        conflict_type=str(conflict.conflict_type),
        competing_values=_trimmed(conflict.competing_values),
        confidence=conflict.confidence,
    )


def proposal_row(proposal: ChangeProposal) -> ProposalRow:
    return ProposalRow(
        proposal_id=proposal.id,
        change_ref=proposal.change_ref,
        product_id=proposal.product_id,
        field=proposal.field_name,
        current_value=proposal.current_value,
        proposed_value=proposal.proposed_value,
        risk_level=str(proposal.risk_level),
        requires_approval=proposal.requires_approval,
        validation_status=proposal.validation_status,
        confidence=proposal.confidence,
        channels=list(proposal.affected_channels or []),
        reason=(proposal.reason or "")[:MAX_TEXT],
    )


def _trimmed(values: object) -> dict[str, Any]:
    """Competing values are a free-form bag; keep it small enough for a prompt."""
    if not isinstance(values, dict):
        return {}
    trimmed: dict[str, Any] = {}
    for key in list(values)[:MAX_IDS]:
        value = values[key]
        if isinstance(value, dict):
            trimmed[str(key)[:MAX_TEXT]] = {
                "value": _short(value.get("value")),
                "rank": value.get("rank"),
            }
        else:
            trimmed[str(key)[:MAX_TEXT]] = _short(value)
    return trimmed


def _short(value: object) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TEXT]


# --------------------------------------------------------------------------
# the registry


def copilot_tools(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    settings: Settings | None = None,
) -> list[ToolSpec]:
    """Bind the five tools to one session and one tenant.

    A factory rather than module-level `ToolSpec`s: these are the first tools
    that need a database, and the session is per request. `ToolContext` stays
    exactly as Phase 15 defined it — who is asking — and the session is closed
    over, so no model-supplied argument can change which rows are reachable.
    """
    config = settings or get_settings()
    cap = config.copilot_max_rows_per_tool

    def bound(handler: Any) -> Any:
        """Close the session and tenant over a handler's signature.

        The context is still checked by the registry before this runs — it is the
        session that cannot come from the model.
        """

        async def run(args: Any, _context: ToolContext) -> Any:
            return await handler(args, tenant, session, cap)

        run.__name__ = f"bound_{handler.__name__}"
        return run

    return [
        ToolSpec(
            name="product_lookup",
            description=(
                "Look up canonical products by SKU, id or text, and return each "
                "with its open disagreements and the change proposals raised "
                "against it. Use before explaining why a product is unsafe to "
                "modify."
            ),
            args_model=ProductLookupArgs,
            result_model=ProductLookupResult,
            handler=bound(_product_lookup),
        ),
        ToolSpec(
            name="conflict_search",
            description=(
                "Find recorded disagreements between sources, optionally by "
                "field or type, with the competing values and each product's "
                "identity. Answers 'find products with conflicting colours'."
            ),
            args_model=ConflictSearchArgs,
            result_model=ConflictSearchResult,
            handler=bound(_conflict_search),
        ),
        ToolSpec(
            name="change_search",
            description=(
                "Find change proposals by risk band, affected channel, field or "
                "status, ordered by risk. Answers 'show high-risk changes' and "
                "'where do Amazon and Shopify disagree' (a disagreement about a "
                "channel's listing shows up as a proposal affecting it)."
            ),
            args_model=ChangeSearchArgs,
            result_model=ChangeSearchResult,
            handler=bound(_change_search),
        ),
        ToolSpec(
            name="compliance_search",
            description=(
                "Search marketplace rules and internal policies and return cited "
                "chunks with the signals that ranked them. Evidence only: this "
                "is not a compliance decision (D-005)."
            ),
            args_model=ComplianceSearchArgs,
            result_model=ComplianceSearchResult,
            handler=bound(_compliance_search),
        ),
        ToolSpec(
            name="sandbox_simulator",
            description=(
                "Run §18's validation chain for one change proposal on a copy and "
                "return the stages, the diff size and the recommendation. "
                "Records a simulation; applies nothing."
            ),
            args_model=SandboxSimulatorArgs,
            result_model=SandboxSimulatorResult,
            # `write`, not `read`: it stores a simulation run. A role that cannot
            # write anything cannot drive simulations through an agent.
            capability="write",
            handler=bound(_sandbox_simulator),
        ),
    ]


def copilot_registry(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    settings: Settings | None = None,
    recorder: ToolCallRecorder | None = None,
) -> ToolRegistry:
    """The copilot's tools: the five catalog reads plus Phase 15's pure four.

    The pure tools come along because they answer questions about *why* a rule
    fired — which source is authoritative, whether two records are the same
    product — and they need no session, so they are reused as-is.
    """
    from app.agents.tools.catalog import DEFAULT_TOOLS

    config = settings or get_settings()
    return ToolRegistry(
        [*copilot_tools(session, tenant, settings=config), *DEFAULT_TOOLS],
        max_output_chars=config.copilot_max_tool_output_chars,
        recorder=recorder,
    )