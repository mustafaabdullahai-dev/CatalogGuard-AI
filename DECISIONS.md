# Architecture & Product Decision Records

Each entry follows: Decision / Options / Chosen / Reason / Trade-offs / Date / Status.

---

## D-001 — Product UI/UX Direction

**Phase:** 1 — UI/UX Discovery
**Date:** 2026-10-01
**Status:** Decided (pending implementation)

### Decision

CatalogGuard AI ships as an **AI Enterprise Command Center**: dark-first, neutral/zinc monochrome base with indigo→violet reserved exclusively for AI affordances, collapsible 240–260px sidebar, minimal top bar, and a three-panel reconciliation canvas.

### Options considered

1. Dark futuristic AI console — rejected: high visual noise, weak table density.
2. Enterprise data platform — rejected as sole direction: functional but no AI-native identity.
3. AI-native chat-first — rejected: conflicts with product principle (evidence and workflow, not chatbot).
4. Linear/Vercel-inspired minimal with AI-native reconciliation layer — **chosen**.

### Chosen specifics

| Area | Decision |
|---|---|
| Visual DNA | Linear (nav/interaction) · Vercel (typography/minimalism) · Stripe (data & ops workflows) · Datadog (observability) · Attio (AI-native interaction) |
| Theme | Dark-first **with** Light Mode |
| Base palette | `#09090B` bg · `#111113` surface · `#18181B` surface-2 · `#27272A` border |
| Text | `#FAFAFA` primary · `#A1A1AA` secondary · `#71717A` muted |
| Semantic colors | AI = indigo/violet · Success = green · Warning = amber · Danger = red · Info = blue |
| Color discipline | Purple means AI, green means safe, amber means attention, red means blocked. No app-wide purple/blue. |
| Typography | Geist (fallback Inter). Page 24–28px, section 16–18px, body 14px, table 13–14px, meta 12px. No oversized type. |
| Radius/shadow | Medium radius, thin borders, very subtle shadows |
| Navigation | Compact collapsible sidebar, icon+text, grouped: Overview / CATALOG / INTELLIGENCE / SAFETY / CHANNELS / SYSTEM |
| Top bar | Search + ⌘K command palette, notifications, workspace selector, user menu. No button clutter. |
| Home | Command Center, not a generic dashboard: greeting + health score → 4 KPI tiles → Requires Attention triage → Recent AI Activity feed |
| Signature workspace | Three-panel resizable canvas: **Source Data → Canonical Truth → AI Analysis** |
| Signature visual | Source → Truth → Channel provenance flow, per-attribute attribution |
| Sandbox | Git-diff × Figma-prototype: Before / Proposed / Impact columns + validation checklist + overall risk |
| AI behavior | Embedded intelligence with evidence, not a chat widget. Copilot available but subordinate. |
| Animations | Restrained: pulse while agent runs, streaming status, skeleton loading, panel transitions, small AI sparkle. No glowing borders, particles, or moving backgrounds. |
| Cards | Flat, subtle. AI Finding cards show finding + confidence + risk. No "✨ AI POWERED ✨" theatrics. |
| Devices | Fully responsive including mobile |

### Reason

Catalog operations are data-dense and require drill-down from finding → evidence → simulation → approval in one place. Stripe-style operational tables plus Linear interaction discipline serve that; AI credibility comes from showing evidence and provenance rather than decorative branding.

### Trade-offs accepted

- **Fully responsive incl. mobile** was chosen against the architect's desktop-first recommendation. Risk: Stripe-density tables, three-panel canvas, and diff viewers degrade on small screens. Mitigation to specify in Phase 21: mobile collapses to single-column flow, dense tables switch to a card/row-detail pattern below `md`, and the three-panel canvas becomes a tabbed/stepped view. This is recorded as accepted risk, not an oversight.
- Dark-first + Light Mode doubles token and component QA surface (every state validated twice).
- Dual reference set (Linear + Stripe) can conflict on density vs. whitespace; Linear's compactness wins for tables.

### Open items deferred to later phases

- Font delivery method (next/font vs. self-host) — Phase 4.
- Light-mode semantic ramp for the AI accent — Phase 21.
- Command palette scope definition — Phase 21.

---

## D-002 — Source Authority: Attribute-Level Precedence + Risk Thresholds

**Phase:** 2 — Product Requirements
**Date:** 2026-10-01
**Status:** Decided (pending implementation)

### Decision

Authority is enforced **per data attribute** through a deterministic, machine-readable precedence matrix — never at the system level, and never by agent negotiation. Whether a resolved conflict may be applied automatically is a separate decision made by risk thresholds.

### Options considered

1. Fixed global hierarchy — rejected: a single ranking is wrong for most real catalogs (price authority ≠ identifier authority).
2. AI negotiates / guesses a winner — **rejected on data-integrity grounds**: produces systemic errors and breaks referential integrity. The prompt's priority order puts data integrity above convenience.
3. Always escalate to a human — rejected as the sole policy: unworkable approval volume on large catalogs.
4. Attribute-level precedence matrix gated by risk thresholds — **chosen**.

### Precedence matrix (baseline defaults, per-tenant overridable)

| Attribute | Level 1 authority | Level 2 authority | Level 3 fallback |
|---|---|---|---|
| Inventory count | WMS | ERP / PIM | Auto-resolve to **lowest** value to prevent overselling |
| Base pricing | ERP / financial ledger | Shopify storefront | Halt and flag if margin falls below threshold |
| Product titles / SEO | PIM | Marketplace content sheet | Auto-overwrite losing sources |
| Shipping address | Courier-validated API input | CRM profile | Escalate to human on structural postcode mismatch |

### Risk thresholds

| Band | Behaviour |
|---|---|
| LOW | Auto-resolve + log. Formatting differences, metadata variance, stock updates within expected velocity. Losing sources are updated to match authority; evidence ledger records it. |
| MEDIUM | Conditional auto-resolve. E.g. pricing within a margin deviation (< 5%): apply authoritative ledger value, raise an immediate dashboard notification. |
| HIGH | Mandatory human-in-the-loop. Parent-child structural breaks, pricing below strict margin floor, multi-source conflicts with no explicit matrix winner. Execution is **frozen for that entity** and routed to a data steward. |

### Evidence ledger / "company brain"

Every resolution becomes durable system knowledge, not a one-off patch:

1. **Stamp** — every sync packet carries machine-readable origin, sequence timestamp (freshness), and authorization context.
2. **Correction loop** — a human override of an agent proposal is stored as a permanent check rule.
3. **Downstream sync** — once a winner is verified, secondary channels read from the corrected source, preventing recurring conflict loops.

### Reason

Separating *who is right* (deterministic matrix) from *may we act* (risk gate) is what keeps the system trustworthy. Agents reason about evidence and consequences; they never arbitrate authority.

### Trade-offs accepted

- The precedence matrix is tenant configuration surface — it must be editable in the UI, and misconfiguration by a tenant can produce confidently wrong auto-resolutions. Mitigation: matrix changes are themselves audited, versioned, and effective-dated; a matrix edit cannot retroactively alter a past resolution's recorded justification.
- The "auto-resolve to lowest value" inventory rule is conservative and can understate available stock. Accepted deliberately: overstating stock causes overselling and channel suspensions.
- Automatic downstream sync after a resolution means a single accepted conflict fans out to every connected channel. This is the highest-blast-radius operation in the product and therefore inherits HIGH risk banding whenever the resolved attribute is identity-, price-, or relationship-related.
- MEDIUM conditional auto-resolve requires live margin thresholds per tenant; these are business policy and must be configurable, not hardcoded.

### Open items deferred

- Exact margin thresholds and velocity parameters — tenant configuration (Phase 18).
- Effective-dating and version history of the precedence matrix — Phase 7 schema.
- Human-override rule promotion ("correction loop" → permanent check rule) — Phase 13/15, depends on RAG rule representation.

---

## D-003 — Personas, Roles, and Approval Authority

**Phase:** 2 — Product Requirements
**Date:** 2026-10-01
**Status:** Decided (pending implementation)

### Decision

Four operational personas, each with a distinct default landing surface. Approval is **role-gated**: only catalog-management and operations roles may approve changes.

### Roles

| Role | Nature | Capabilities | Default landing surface |
|---|---|---|---|
| **E-commerce Operations Manager** | Admin / super-user | Full access. Configures attribute precedence matrices, financial risk thresholds, cross-department policy | System Health & Configuration — pipeline stability, sync success rate, macro revenue risk |
| **Data Steward / Catalog Manager** | Write + resolve | Heaviest daily user. Reviews high-risk listing mismatches, resolves parent-child variant breaks, overrides agent suggestions, approves changes | Conflict Escalation Queue (inbox), optimized for rapid triage and bulk approval |
| **Merchandiser / Inventory Planner** | Attribute-scoped read/write | Monitors stock and price flow across marketplaces against physical supply | Inventory Sync & Price Auditing Ledger |
| **SEO / Content Specialist** | Limited write | Edits titles, descriptions, metadata. Explicitly **no** access to inventory, financial, or identity attributes | Metadata & Content Optimization |

### Approval authority

Role-gated. Catalog/PIM management and e-commerce ops roles hold approval rights; read-oriented roles (SEO, merchandising) may propose and comment but cannot approve. No separate two-person rule for CRITICAL at MVP — configurable later.

### Reason

Approval is the control that makes the whole system safe, so it must not sit with the most junior role. The Data Steward is the intended daily operator, which is why the escalation queue — not a generic dashboard — is their home surface. Attribute-scoped write for merchandising and SEO keeps high-blast-radius fields (price, GTIN, parent relationships) out of reach of users who have no reason to touch them.

### Trade-offs accepted

- **No data/engineering role.** Ingestion, connector configuration, and source management are core MVP features but have no owning persona in this set. Open risk, see below.
- **No CRITICAL two-person rule.** CRITICAL changes get a single role-gated approval. Acceptable at MVP given the human is already gated; revisit before enterprise rollout.
- **Four personas with four default landings** multiplies the QA surface for navigation and permission-state testing. Mitigation: landings are configurable per role, not hardcoded per route.
- Attribute-scoped permissions add real complexity to both RBAC and the UI (per-field affordance hiding). Budget for this explicitly rather than bolting it on.

### Open items

1. Whether SEO/content write is fully blocked from identity fields or blocked only from *approving* them.
2. Permission granularity: attribute-scoped by data domain (e.g. "financial") or literal per-attribute.

---

## D-004 — Data/Integration Admin Role

**Phase:** 2 — Product Requirements
**Date:** 2026-10-01
**Status:** Decided (pending implementation)

### Decision

Add a fifth role, **Data/Integration Admin**, owning catalog ingestion, marketplace connections, source mapping, and source precedence configuration.

### Reason

CSV/Excel ingestion is MVP scope but had no owning persona. Folding it into the ops manager contradicts that persona's stated policy-setting focus and concentrates ingestion authority plus policy authority in one identity. The Data Steward was also rejected — marketplace credential custody should be separate from conflict resolution authority, so that compromising an ingestion account does not grant approval rights.

### Capabilities

- Upload and manage catalog sources; configure schema mapping and normalization rules
- Configure the D-002 attribute precedence matrix and risk thresholds
- Manage marketplace connections and credentials
- Read all catalog data

### Explicitly cannot

- Approve changes
- Resolve conflicts or override agent proposals

### Trade-offs accepted

Five roles increases RBAC and permission-state QA surface. Accepted: credential custody separated from approval authority is a deliberate security boundary, not incidental complexity.

### Open items

- Whether this role can be assigned at organization level only, or per-workspace. Recommend per-workspace, since a tenant may operate marketplaces regionally.

---

## D-005 — MVP Scope

**Phase:** 2 — Product Requirements
**Date:** 2026-10-01
**Status:** Decided

### Decision

Section 71 adopted verbatim. No marketplace integrations in MVP.

### In scope

1. Authentication
2. Workspace
3. CSV/Excel ingestion
4. Canonical product model
5. Catalog normalization
6. Conflict detection
7. Parent-child validation
8. AI reconciliation
9. Change proposal
10. Risk engine
11. Sandbox
12. Approval workflow
13. Audit logs
14. Dashboard

### Deferred to post-MVP

Shopify, Amazon, TikTok Shop, and further marketplace connectors. These follow only once the core architecture is stable.

### Reason

Establishes a reliable core. Building connectors before the reconciliation, risk, and sandbox layers are proven would mean validating an API abstraction against an unstable internal model.

### Trade-offs accepted

- **No marketplace API integration at MVP.** The `MarketplaceConnector` abstraction and one mock adapter should still be built during Phase 19 so the seam is proven, but with no live calls. Consequence: the end-to-end execution path is exercised only against the mock, so real-world rate limits, auth quirks, and partial-failure modes remain unvalidated until Phase 19+.
- **No compliance RAG in MVP.** Consequence: compliance findings in early versions are heuristic rather than rule-cited, which is a material difference from the Section 13 promise. Must be labelled honestly in the UI rather than presented as authoritative.
- **No AI Copilot** (Phase 22). Users navigate via sidebar and command palette initially. The evidence-rich finding cards carry the AI value at MVP.

### Amendment (Phase 19, 2026-10-07)

The trade-off above required the seam; the seam now exists, so the deferral
has a boundary instead of an absence.

- **`MarketplaceConnector` carries §22's nine operations plus `rollback`.**
  Rollback is §21's requirement and `supports_rollback` is its honesty check:
  an adapter that cannot restore a previous state refuses the call rather than
  reporting one. Errors form one vocabulary with a `retryable` flag, so a
  bounded-retry loop decides by class instead of by parsing a message.
- **One adapter: `MockConnector`.** In-memory, credential-checked, no clock
  and no network. It deliberately does not simulate latency, OAuth or rate
  limiting — a fabricated version of each would be tested as though it were
  real. `fail_once` lets a caller prove its retry loop stops; that is the
  behaviour worth checking before a marketplace exists.
- **Registry, not branches.** `CONNECTOR_FACTORIES` holds `mock` and nothing
  else. An unknown marketplace raises `UnknownMarketplaceError` naming what is
  registered; falling back to the mock would report writes against a store
  that does not exist. No module in `app/` is named after a deferred
  marketplace, and a test asserts it.
- **Credentials reach the database only through `encrypt_credentials()`**, and
  `create_connection` is the first writer of `marketplace_connections` — the
  claim in §33 and in this decision's column comment is now held by a test
  that reads the table's columns, not by prose.
- **Still true:** no live calls, and nothing in production calls the seam yet.
  Phase 20 is its first caller.

---

## D-006 — Vector Store: PostgreSQL + pgvector

**Phase:** 4 — Technology Selection
**Date:** 2026-10-01
**Status:** Decided

### Decision

pgvector inside the primary PostgreSQL instance. No separate vector database.

### Reason

Avoids a second infrastructure service and a second source of truth. Keeps vectors, relational filters, and transactional writes in one place — which matters because compliance retrieval must filter by marketplace, category, version, and effective date, and those filters are relational by nature. Sufficient for MVP catalog volumes.

### Trade-offs accepted

- Vector search and transactional OLTP load share resources; large-scale embedding workloads will eventually warrant read replicas or a dedicated store.
- pgvector index build and recall tuning require attention as the corpus grows. `HNSW` assumed at launch; `IVFFlat` as the alternative.
- Hosted providers must expose pgvector. Neon does natively. If the chosen host does not, this decision must be revisited.

---

## D-007 — LLM Provider: Qroq, with a mandatory validation layer

**Phase:** 4 — Technology Selection
**Date:** 2026-10-01
**Status:** Decided

### Decision

Qroq as the sole LLM provider for the MVP, using the latest model available on the account. Every LLM call is wrapped in strict JSON-schema validation with repair-and-retry.

### Reason

Zero inference cost. The validation layer preserves the architectural guarantee that free-form model output never reaches the execution layer.

### Mandatory engineering consequences

This is a constraint, not a preference. Because Qroq is OpenAI-API-*compatible* rather than OpenAI itself, its strict JSON-schema structured output is materially weaker. Therefore:

1. Every model call declares a Pydantic schema and validates output before use.
2. Validation failure triggers a bounded repair loop (re-ask with the validation error), never silent acceptance.
3. After N failed repairs the task fails loudly and records the error — it does **not** fall through to the execution layer.
4. Schema-first prompting (instructions to emit JSON matching the schema) rather than relying on provider-side schema enforcement.
5. Token accounting must include repair-loop retries (Section 56 cost control).

### Trade-offs accepted

- **Rate limiting is the principal risk.** Free-tier Qroq will very likely throttle a bulk reconciliation run over thousands of SKUs. Mitigation: deterministic pre-filtering so the LLM is called only on genuine conflicts, batched calls, and a concurrency limiter. Bulk runs must degrade to graceful queueing, not failure.
- More code and more tokens than provider-native strict schemas would require.
- A model upgrade on Qroq's side can change output behaviour without our changes; the validation layer is what absorbs this.

### Security note

The Qroq API key was exposed in plaintext during this session and **must be rotated**. It is stored only in `.env` (git-ignored), never committed, never logged, never echoed.

---

## D-008 — Job Queue: Dramatiq + Redis

**Phase:** 4 — Technology Selection
**Date:** 2026-10-01
**Status:** Decided

### Decision

Dramatiq with Upstash Redis.

### Reason

Native asyncio fit for an async FastAPI app, simpler configuration than Celery, and built-in retry semantics appropriate for Section 21's controlled-retry requirement.

### Trade-offs accepted

- Dramatiq has no built-in delayed-job scheduling. Scheduled compliance scans will need a separate scheduler (APScheduler or a cron-driven enqueue). Flagged for Phase 28.
- Retries must be explicitly configured with finite attempt limits; unbounded retry is prohibited by Section 21.
- Redis is an ephemeral tier here — losing it loses queued jobs. Requires durable job records in Postgres so in-flight work is recoverable.
- Upstash enforces TLS. All Redis connection strings use `rediss://`. A `redis://` scheme fails at runtime, not at config load, so the scheme is asserted explicitly in code.

---

## D-009 — Database Hosting: Neon (Postgres) + Upstash (Redis)

**Phase:** 4 — Technology Selection
**Date:** 2026-10-01
**Status:** Decided — pending account creation

### Decision

Hosted free tiers. Neon for PostgreSQL with pgvector, Upstash for Redis.

### Reason

This machine has **no sudo and no apt-get**, so local Postgres and Redis installation is impossible. Hosted free tiers are the only zero-cost route, and they match the production topology.

### Trade-offs accepted

- **Free tiers are not production.** Neon suspends idle databases and Upstash caps throughput. Acceptable for MVP; production cost is a later phase.
- TLS to a remote host rather than a local socket.
- Latency to hosted services affects ingestion and simulation response times.
- Requires outbound network access from the dev environment.
- The product owner must create both accounts and supply connection strings before Phase 6 can complete.

### Rejected

- SQLite for early development — rejected. No pgvector (breaks D-006), divergent SQL dialect, and a migration rewrite later. Not worth unblocking a day early.

### Endpoint status

- **Postgres (Neon):** connected and verified. Host `ep-dry-resonance-a5u9w8p1-pooler.us-east-1.aws.neon.tech`, db `neondb`, user `neondb_owner`, region us-east-1, TLS required and confirmed. Credentials held only in `.env`; password must be rotated because it was exposed in plaintext.
- **Redis (Upstash):** connected and verified over TLS. Redis 8.4.0, standalone mode. **TLS is mandatory** — the connection string must use the `rediss://` scheme, not `redis://`, or Dramatiq will attempt a plaintext connection and fail at runtime.

### Open items

- `pgvector` extension must be confirmed enabled on the Neon database. D-006 depends on it. Not yet verified: requires a live authenticated connection.
- **AMENDED (Phase 24, 2026-10-10).** The pgvector side of this decision is now
  verified against a real server, and two of its assumptions were wrong.
  pgvector 0.8.6 requires an explicit operator class for an HNSW index; the
  generated index named none, so `alembic upgrade head` failed on any real
  database. The index now declares `vector_cosine_ops`, and the migration
  generator raises on a string-valued operator class rather than dropping it.
  Retrieval is exercised end to end against real vectors, including the
  1536-dimension column, ordering, SQL-level metadata filters and citations.
  **Still unverified:** the Neon instance itself. Everything above ran against
  an ephemeral `pgvector/pgvector:pg16` container, which proves the schema and
  the queries, not that the hosted provider exposes the extension or accepts
  these settings.

---

## D-010 — Observability & Data Residency

**Phase:** 4 — Technology Selection
**Date:** 2026-10-01
**Status:** Decided

### Decision

Self-hosted **Langfuse**, deployed inside the product owner's own cloud instance or VPC. Third-party LLM observability platforms are **prohibited**.

### Reason

Catalogs contain proprietary manufacturer files, wholesale margins, and sensitive marketplace listings. Transmitting this content to a third-party observability SaaS is an unacceptable disclosure risk, regardless of the vendor's data-processing terms. Tracing value does not justify moving proprietary commercial data outside the data perimeter.

### Mandatory engineering rules

1. **Allowed destination:** self-hosted Langfuse only.
2. **Prohibited:** LangSmith, public cloud log aggregators, and any third-party SaaS observability product that ingests raw LLM call content.
3. **Edge masking is mandatory, not optional.** The application logging layer must sanitize designated data keys before telemetry reaches any log processor — including `sku`, `price`, `supplier_cost`, `unreleased_description`, and any tenant-configurable additions. Masking happens at the serialization boundary so it applies to traces, errors, and exception payloads alike, not just log lines.
4. Secrets must never appear in telemetry. Applies to LLM traces, tool-call arguments, and error stack payloads.

### Trade-offs accepted

- Self-hosting adds a service to operate, secure, back up, and monitor, and Langfuse requires its own Postgres database.
- Losing some hosted-platform convenience: no managed eval UI, no vendor-side cost dashboard. Token and cost accounting must be built in-app (D-007 already mandates this).
- Masking reduces trace fidelity for debugging. Accepted deliberately; the alternative is leaking commercial data.
- Requires a deployment target to exist. If none is available yet, structured application logging (Section 55) carries observability until Langfuse is stood up, and LangSmith stays prohibited in the interim.

---

## D-011 — Tenant Isolation: Shared Schema + ORM Scoping + PostgreSQL RLS

**Phase:** 7 — Data & Persistence
**Date:** 2026-10-02
**Status:** Decided

### Decision

Every tenant-owned table carries a NOT NULL `workspace_id` and is enforced at
three independent layers:

1. **Schema.** Tenant references are composite: `FOREIGN KEY (workspace_id, parent_id)
   REFERENCES parent(workspace_id, id)`. Referenced tables carry
   `UNIQUE (workspace_id, id)`. A row therefore cannot point at another
   tenant's parent row even if the application asks it to.
2. **ORM.** `TenantRepository` refuses to be constructed without a
   `TenantContext`, applies the tenant predicate and the soft-delete predicate
   centrally, and drops `workspace_id`/`id` from update payloads.
3. **Database.** Row-level security with `FORCE ROW LEVEL SECURITY`, comparing
   `workspace_id` against the transaction-local setting
   `app.current_workspace`.

### Reason

A shared schema is required for pgvector similarity search across a tenant's
corpus and for cross-table reconciliation queries, so schema-per-tenant is not
available. Layer 1 is invisible to ad-hoc SQL. Layer 2 is invisible to raw SQL,
migrations, reporting, and a future service account. Layer 3 is the only one
that holds for all of them, which is why RLS ships even though the ORM already
scopes correctly.

### Mandatory engineering rules

1. **Fail closed.** Policies use
   `workspace_id = NULLIF(current_setting('app.current_workspace', true), '')::uuid`.
   An unset or blank setting compares against NULL and matches nothing. A
   connection with no bound tenant sees zero rows; it must never see all rows.
2. **`FORCE` is mandatory.** PostgreSQL exempts a table's owner from its own
   policies, and the application connects as the owner of these tables. Without
   `FORCE`, the policies would be present in the schema and inert in production.
3. **`SET LOCAL` only.** Tenant binding uses `set_config(..., true)`, which
   discards the value at commit or rollback. A plain `SET` would survive on a
   pooled connection and leak one tenant into the next request.
4. **Bind, never interpolate.** The workspace id is always a bound parameter.
5. **`WITH CHECK` on every policy.** Without it the read side is protected and
   the write side is not, and a row can be inserted into another tenant.
6. **Global tables are declared, not inferred.** `organizations`, `users`, and
   `workspaces` carry no policy, and the exemption list is explicit so adding a
   table is a deliberate act.

### Consequences

- A tenant-scoped query is now denied unless the request bound a tenant.
  Shipping RLS before the authentication layer (Phase 8) means tenant queries
  fail closed rather than open. That is the intended order.
- Every tenant table carries an extra unique constraint and a `workspace_id`
  index, and the initial migration is generated from ORM metadata so those stay
  in step with the models.
- Migration and admin tooling that legitimately needs cross-tenant access must
  either bind a tenant explicitly or use a separately privileged role. There is
  no bypass role yet; adding one is a separate decision.

### Alternatives rejected

- **Schema or database per tenant.** Required by some regulators, incompatible
  with the single-query pgvector similarity search and with operational
  simplicity at MVP scale. Reconsider at enterprise scale.
- **Application-only filtering.** One missed predicate is a breach. RLS exists
  because the ORM is not the only thing that will ever touch this database.
- **`current_setting(name)` without the missing-ok flag.** Raises on an unbound
  connection, turning a policy failure into an availability failure.

### Amendment — Phase 24 (2026-10-10): self-readable memberships

**Status:** Implemented. The three-layer model above is unchanged; layer 3 now
has one documented exception.

Phase 24 ran the system against a real database and found that **every
signed-in user resolved to zero workspaces.** `workspace_memberships` is
tenant-scoped, so it carries the `tenant_isolation` policy — and
`get_auth_context` must read a user's memberships to learn which workspaces
they belong to. Nothing can bind a workspace before that read, because the read
is what names the workspace. The policy returned nothing, `/auth/me` answered
with a valid user and an empty workspace list, and every subsequent request
failed the tenant check.

The decision now adds a second binding, `app.current_user_id`, with the same
`SET LOCAL` containment as the workspace:

- `workspace_memberships` is the only self-readable table. Its policy is
  `USING (user_id = current_user OR workspace_id = current_workspace)`.
- **`WITH CHECK` is not widened.** Writes still require the bound workspace, so
  self-read cannot become self-enrolment.
- `get_auth_context` binds the user id from the session it has already resolved
  — never from a request, header or body.
- Tables other than this one are unchanged: a user binding grants nothing on a
  normal tenant table.

Rejected: exempting `workspace_memberships` from RLS, and adding a
privileged-read role. Both solve availability by making every user's
memberships readable to anyone who can query the table. Three tests in
`tests/integration/test_schema_real.py` pin the narrowness — own rows visible,
other users' rows invisible, self-enrolment refused.


---

## D-012 — Soft Deletion + Explicit Version History

**Phase:** 7 — Data & Persistence
**Date:** 2026-10-02
**Status:** Decided

### Decision

Mutating business tables are soft-deleted: a `deleted_at` timestamp and no row
removal. Repositories filter `deleted_at IS NULL` centrally, so a soft-deleted
record leaves query paths without every call site remembering to exclude it.

Immutable tables (`audit_logs`, `product_versions`, `tool_calls`, `llm_usage`)
declare neither `updated_at` nor `deleted_at`. Their shape asserts write-once:
retraction is a new row, never an edit or a delete.

Product history is explicit, not incidental. `product_versions` records the
exact snapshot a reconciliation observed, so a proposal can always be replayed
against the state it was computed from.

### Reason

Section 50 requires auditable change history and rollback. Deleting a product
row would destroy the evidence of what a proposal was approved against, which
is precisely what an auditor asks for. Making immutability structural rather
than conventional means a `soft_delete` call on a version row fails at the type
level instead of silently corrupting history.

### Trade-offs accepted

- Storage grows without automatic reclamation. Acceptable at MVP volumes.
- Soft-deleted rows still occupy unique key space: a SKU freed by a soft delete
  stays taken. Reusing a SKU would break replay of historical runs, so the
  conservative reading wins.
- Two base classes instead of one. The cost is small; the benefit is that
  "immutable" is a type, not a comment.

### Alternatives rejected

- **Hard delete everywhere.** Destroys audit evidence. Unacceptable.
- **A single `deleted_at` on every table.** Would let immutable history be
  tombstoned, contradicting the append-only requirement in D-013.


---

## D-013 — Audit Logs Are Append-Only, Enforced by the Database

**Phase:** 7 — Data & Persistence
**Date:** 2026-10-02
**Status:** Decided

### Decision

`audit_logs` has no `updated_at` and no `deleted_at` columns. In addition, the
database refuses modification: a `BEFORE UPDATE` and a `BEFORE DELETE` trigger
both call `catalogguard_audit_immutable()`, which raises
`restrict_violation` for every operation, for every role, including the table
owner.

### Reason

Application-level append-only is a convention that a bug, a direct `psql`
session, or a future migration can bypass. The audit trail is the artifact an
incident review depends on; its immutability is worth enforcing where it
cannot be forgotten. RLS (D-011) covers who may read a row; this covers what
may happen to one.

### Trade-offs accepted

- An erroneous audit row cannot be corrected or removed. It can only be
  contradicted by a later row, which is the correct accounting model.
- Retaining audit rows after a tenant offboards is a policy question, and GDPR
  erasure obligations may eventually require a documented purge path. That is a
  separate decision, and it must be explicit rather than an accidental side
  effect of a cascade.


---

## D-014 — Authentication: Email and Password, Server-Side Sessions

**Phase:** 8 — Authentication & Authorization
**Date:** 2026-10-02
**Status:** Decided

### Decision

Humans sign in with an email address and a password, verified against an
Argon2id hash, and receive an **opaque session cookie**. The session lives in
Postgres in `auth_sessions`; the cookie carries a 256-bit random token and only
its SHA-256 digest is stored.

- Argon2id at 64 MiB, 3 passes, 4 lanes (OWASP recommendation). Parameters are
  recorded in each hash, so they can be raised later and rehashed on the owner's
  next successful login instead of forcing a reset.
- Minimum password length 12, no composition rules. See trade-offs.
- Cookie is `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`.
- CSRF protection on every state-changing API call, including login, using a
  **signed** double-submit token.
- Two independent login counters, in Postgres: per email and per client IP.
- No HTTP route creates a user or grants a role. The first administrator is
  created by `python -m app.cli.bootstrap`, which prompts for the password so it
  never appears in shell history.

### Reason

**Why not Django.** The application is FastAPI on async SQLAlchemy. Mounting
`django.contrib.auth` alongside it means two ORMs, two migration chains, two
settings systems, and synchronous session lookups in the request path — all to
obtain the same capability list. The user experience Django's auth provides is
reproduced here in a few hundred lines with one dependency
(`argon2-cffi`) and no second framework.

**Why a server-side session rather than a JWT.** Revocation. This system's
approval authority is the thing being protected, so a removed membership or a
suspected stolen cookie must take effect immediately. A self-contained JWT stays
valid until it expires regardless of what the server now believes, which makes
"revoke this session" and "log out everywhere" inexpressible.

**Why signed double-submit CSRF.** Plain double-submit compares a cookie against
a header, which is only as strong as the cookie: an attacker who can set a
cookie on the domain — a subdomain takeover, a sibling app on the same
registrable domain — chooses both halves. Signing the nonce with a server secret
means a forged cookie can never produce a matching header.

**Why throttles live in Postgres.** A lockout is a security control, and Redis
is a cache that can be evicted or flushed. Losing the counter must not hand an
attacker a fresh budget of attempts.

### Mandatory engineering rules

1. **One response for every login failure.** Wrong password, unknown address,
   disabled account: identical 401, identical body. The real reason is logged
   server-side and never returned.
2. **Hash the work regardless of whether the user exists.** An unknown address
   must cost the same CPU as a known one, or response time enumerates accounts.
3. **Normalize the address before anything else looks at it.** Otherwise
   `Ops@example.com` is a separate lookup miss and a separate throttle budget.
4. **Throttle before lookup**, and commit the counter on the failure path. A
   failed attempt must outlive the request that recorded it.
5. **Clear the throttle only on success.** Clearing on failure returns the budget
   on every attempt, which is a counter that can never reach its limit.
6. **The workspace header is a hint, not authority.** `X-Workspace-Id` is checked
   against the caller's memberships; a workspace the user does not belong to is a
   403, never a 404 (which would confirm existence).
7. **A capability is never implied by another.** `write` does not imply `approve`
   or `admin`, and `is_superuser` is not a bypass. The bootstrap account is
   created with it False specifically so the role matrix is exercised from day
   one.
8. **CSRF is enforced before routing.** The middleware rejects on method, path,
   and headers, so the guarantee does not depend on any handler remembering.

### Trade-offs accepted

- **No password reset, no email delivery.** Reset needs an outbound mail
  provider, which is a dependency and a deliverability problem. Until one exists,
  a lost password is resolved by an operator with database access re-running the
  bootstrap CLI. Recorded as a gap rather than silently omitted.
- **No SSO or MFA.** Both are deferred. MFA is the obvious next addition and
  attaches to the session record rather than to the password path.
- **Every authenticated request costs one indexed lookup** for the session and
  one for the membership list. Caching either re-introduces the revocation
  problem D-014 was chosen to solve.
- **No composition rules.** A 12-character floor with no character-class
  requirement is weaker against naive cracking than a complex rule, but complex
  rules reliably produce `Password1!`. Argon2's cost is what actually buys
  resistance here.
- **Users cannot self-serve.** Onboarding a second user needs an operator. For
  MVP teams that is acceptable; it is the first thing to change.

### Alternatives rejected

- **Django's auth stack.** A second ORM, a second migration chain, and sync
  session lookups in an async request path, for a capability list.
- **JWT in a cookie or in `localStorage`.** Not revocable; `localStorage` is
  readable by any injected script.
- **Passwordless magic link.** Depends entirely on an email provider before
  anything can be tested end to end, and gives no credential for an operator
  running the bootstrap CLI.
- **Redis-backed throttles.** An evictable cache guarding a security control.
- **`X-Forwarded-For` as the throttle key.** Client-controlled unless a trusted
  proxy is in front; it would let a caller mint a fresh budget per request.

---

## D-015 — Catalog Domain: Normalization, Identity, Relationships, Authority

**Phase:** 9 — Catalog domain
**Date:** 2026-10-02
**Status:** Decided

### Decision

Four deterministic services, no model calls, each returning evidence with its
answer:

1. **Normalization** (`app/services/catalog/normalization.py`) maps intake
   columns onto a canonical field list through an explicit alias table, records
   the rule that fired, and leaves anything unmapped alone.
2. **Identity resolution** (`identity.py`) links records to existing products.
   **Only an exact identifier match may link automatically.** Everything else —
   fuzzy titles, brand plus title, an LLM's opinion — becomes a proposal.
3. **Relationship validation** (`relationships.py`) reports structural defects
   and has no write path at all.
4. **Source authority** (`authority.py`) resolves per-field authority from a
   default ladder plus per-field overrides, escalating ties.

### Answers to the questions the requirements require asking

The requirements (§12) say to ask the operator how source authority should be
configured before implementing the final logic. Asked and answered:

- **Authority model:** per-field overrides on a default ladder. PIM wins titles,
  ERP wins price and cost, WMS wins inventory, the manufacturer wins identifiers.
- **Equal-rank disagreement:** raise a conflict for a human. Never choose.
- **Identity matches:** only exact identifiers auto-link.

### Mandatory engineering rules

1. **An unmapped column stays unmapped.** Guessing a canonical field is worse
   than reporting an unknown one: the guess looks authoritative.
2. **GS1 codes are compared as digits.** `4006381333931` and
   `04006381333931` are the same code. Comparing raw strings manufactures a
   conflict between sources that agree.
3. **Absent evidence is not contradictory evidence.** A record with no GTIN is
   not in conflict with one that has a different GTIN.
4. **A stated identifier disagreement is disqualifying.** No amount of title
   similarity survives it.
5. **Size, material and style differences are disqualifying**; colour
   differences only lower confidence. Suppliers name colours inconsistently —
   "M" and "L" are genuinely different products.
6. **A merge always needs approval.** Even an exact identifier match may link a
   marketplace listing to a canonical product, but merging two products that both
   exist is never automatic.
7. **AI inference is never authoritative**, and a tenant's override map cannot
   promote it.
8. **Relationship validation never writes.** Findings become
   `ProductRelationship` rows through the approval flow, or not at all.
9. **Cycles are reported once, normalized by rotation**, and bundle edges are
   checked separately from parent edges.
10. **Cosmetic differences are one disagreement.** `10.0` and `"10.00"` are one
    price; "Navy Blue" and "Dark Blue" are one color argument.

### Trade-offs accepted

- **Blocking keys, not a vector index.** Identity scoring is token overlap plus
  character ratio. It is explainable and reproducible, and it will not catch a
  paraphrase with no shared vocabulary. An embedding index (pgvector is already
  in the schema) is the next step for the ambiguous band.
- **SKUs cannot establish identity.** They are vendor-local, so the same string
  from two vendors is routinely two products.
- **The alias table needs human maintenance.** New supplier schemas mean new
  aliases. `propose_aliases` surfaces candidates from real intake data rather
  than guessing at runtime.
- **No runtime fuzzy field-name matching.** It would map things nobody
  reviewed, into fields nobody checks.
- **Relationship depth is capped at 6 levels** as a warning, not an error;
  marketplaces and reports give up before that.

### Alternatives rejected

- **LLM-only matching.** Prohibited by §8, and a confident wrong merge is
  unrecoverable.
- **Auto-linking above 0.95 confidence.** Text similarity measures wording, not
  identity.
- **A single global authority ladder.** Pricing and titles follow different rules.
- **Picking a winner on ties.** Makes the result depend on iteration order.
- **Writing repairs automatically.** §9 forbids it; the absence of a write path
  is the only way to hold that line.

---

## D-016 — Risk Engine: Fixed Weights With Floors

**Phase:** 10 — Conflict detection and risk
**Date:** 2026-10-02
**Status:** Decided

### Decision

Risk is a sum of fixed, published weights with four bands (LOW / MEDIUM / HIGH /
CRITICAL), plus floors that specific consequences cannot be talked below.

### Reason

§16 requires a deterministic score and forbids the LLM from deciding final risk.
A weighted table satisfies that in the strongest available way: the approver can
add up the factors and predict the band, and can say precisely which factor they
disagree with. A trained classifier cannot be argued with, and a risk score
nobody can argue with is a risk score nobody trusts.

The floors exist because the factors are a floor and not a ceiling. A GTIN
change, a deletion, a broken relationship, or a compliance violation carries a
consequence that does not shrink because the arithmetic came out low.

### Mandatory engineering rules

1. **The score is the sum of the listed factors.** No hidden adjustments.
2. **Factors are sorted by weight, descending**, so the reason is visible at the
   top of the approval screen.
3. **HIGH and CRITICAL always require explicit approval.** LOW and MEDIUM are
   applied only through the same approval flow, which is the more conservative
   choice taken deliberately.
4. **A floor that raises the band records why**, in `escalated_reason`.
5. **Evidence and confidence are themselves risk factors.** A change nobody can
   justify is risky because it is unexplainable to the person approving it.
6. **The engine takes no clock and makes no network call.** Same context, same
   result — tested.

### Trade-offs accepted

- **Weights are opinionated.** They encode a view about which changes are costly,
  stated openly and tunable per workspace rather than hidden in a model.
- **No cross-factor interaction.** A low-confidence change touching four channels
  adds its weights without compounding. Interaction terms would be harder to
  argue about, which is the property worth keeping.
- **Sales figures are supplied by the caller.** The engine does not query an
  analytics store, so a caller that passes zeros will understate risk. The
  adapter is where that data must be attached.

### Alternatives rejected

- **LLM risk assessment.** Explicitly prohibited by §16.
- **Trained classifier.** Not explainable, not reviewable, not reproducible.
- **Binary risky / not risky.** §16 asks for four bands, and the middle two are
  where review effort actually goes.

### Amendment (Phase 16, 2026-10-06)

Checked against §16 rather than against this decision alone, as the phase's
first exit criterion requires. §16's thirteen potential factors are now all
scored — **inventory impact** was the one missing and gained a weight
(`inventory_impact`, 20, fired by the field being touched or by the units a
caller says are affected). Two §16 lines share codes deliberately: *indexing
impact* is scored through `seo_sensitive_field` and `url_or_slug_change`,
because charging a deindexing twice would charge the same consequence twice.

Seven codes remain D-016's own, unnamed in §16: `product_deletion`,
`broken_relationship`, `title_change`, `description_change`, `bulk_change`,
`low_confidence`, `thin_evidence`. The table in
`backend/app/services/reconciliation/risk.py` is the one to read, and the test
that keeps both tables exhaustive is
`TestSection16Conformance.test_every_weight_is_claimed_by_section_16_or_by_d_016`.

## D-017 — Account Recovery and Invitations: Outbox Delivery, Sessions Revoked

**Phase:** 11 — Password reset and self-service onboarding
**Date:** 2026-10-02
**Status:** Decided

### Decision

Password reset and workspace invitations ship together, behind a `MailDelivery`
port whose only adapter writes to an `email_outbox` table. Reset requests are
answered identically whether or not the account exists. Reset tokens are
single-use, and a successful reset revokes every session and issues none.

### Answers to the questions the requirements require asking

**Q: May a reset request reveal whether an address has an account?**
No. Same 202, same body, same wording, and the response says the message was
*queued* rather than *sent*. The reset form is reachable by people who cannot
prove who they are; an existence oracle would leak the user list to whoever
typed an address into one text box.

**Q: Which session identifiers are affected by a reset?**
All of them, in every workspace, on every device, and no new one is issued. A
reset usually means a password was exposed, and revoking only the requesting
device would leave the credential the attacker already holds usable. Issuing a
session at reset time would authenticate whoever read the inbox.

**Q: What role does a new user get, and who chooses it?**
The issuing administrator, at issue time, recorded in the row. `accept()` takes
no role parameter, so there is nothing for the invitation holder to tamper with.
**Q: What if the invited address already has an account?**
The membership is added and the existing password is left alone. Supplying a
password for an account that has one is an error, not an overwrite, so an
invitation link is never a password-reset vector.
**Q: What if the password is rejected by policy?**
The invitation stays live and the same link works on a second attempt. Someone
who cannot reach the inbox cannot obtain a replacement link.
**Q: May a mail provider be plugged in later without touching callers?**
Yes. Callers depend on `MailDelivery`, never on a provider. The queued row, its
`sent_at`, and its retry count are the transport's business, not the caller's.

### Mandatory engineering rules

- 256-bit `secrets.token_urlsafe`, stored as a SHA-256 digest. Plaintext tokens
  exist only in the request that mints them and in the queued message body.
- Single-use is enforced by a conditional `UPDATE ... WHERE consumed_at IS NULL
  AND expires_at > now`. Expired, consumed and revoked tokens are all "no row
  matched"; no prior read decides who wins.
- The role and workspace come from the invitation row, never from the acceptance
  body. Revocation is scoped by workspace in the `WHERE` clause.
- One live invitation per address per workspace, enforced by a partial unique
  index. Resend replaces the token on the same row, so revoking is unambiguous.
- Reset throttling uses its own scopes (`reset_email`, `reset_ip`), separate from
  login. A shared budget lets anyone keep a victim locked out of signing in —
  DoS aimed at the person the feature helps.
- Message bodies and live tokens are never logged in any environment. D-010 masks
  any run of 32+ characters, which covers every token length here.
- The outbox row is written in the same transaction as the token that caused it.

### Trade-offs accepted

- **Nothing is actually sent yet.** With no provider configured, a queued message
  reaches a human who runs `python -m app.cli.outbox`. That is the honest state
  of the feature, and the port means the swap is one adapter plus one setting.
- **The identical response costs diagnosability.** A queued message nobody reads
  is indistinguishable from an address that does not exist. That is the price of
  not publishing the user list, and it is a deliberate one.
- **Global tables for reset and outbox.** `password_reset_tokens` and
  `email_outbox` have no `workspace_id`. Resolution must work before anyone has
  chosen a workspace, and mail is not tenant data; only `workspace_invitations` is
  tenant-scoped.
- **Retiring the oldest live token on re-request.** Three live tokens per address
  keeps an attacker from invalidating a genuine link with a flood of requests,
  at the cost of sometimes invalidating one too early.

### Alternatives rejected

- **Email confirmation on sign-up.** No route may create an identity (D-014);
  onboarding goes through an administrator who already has the capability to
  grant it.
- **"Forgot your password?" revealing a live token to the caller.** Convenient for
  development and a credential leak in every other environment.
- **Revoking only the current session.** Faster for the person resetting, and
  exactly what a compromised account does not want.
- **A `django-password`-style module-level cache.** Tokens that must be
  single-use belong in the only store that can enforce it atomically.
- **Throttling resets through the login limiter.** One shared counter turns a
  safety feature into a remote lockout.

---

## D-018 — Catalog Ingestion: Local Formats, a Sync/Async Seam, and Raw-Only Storage

**Status:** Implemented (Phase 10). The async worker is unverified — no live Redis.

### Decision

Ingestion (§49, Phase 10) accepts **CSV, JSON and Excel (.xlsx)** only. A file at
or below `INGESTION_INLINE_MAX_BYTES` is parsed inside the request; a larger file is
handed to a Dramatiq worker (§28) **only when `INGESTION_EXECUTION=dramatiq`**,
which defaults to `inline`. The pipeline writes **raw `source_records` and nothing
else** — normalization into canonical products is Phase 11, a separate pass. The
file-security step is deterministic (extension allowlist, magic bytes, size cap,
binary refusal, CSV formula neutralisation, row and column caps) plus a pluggable
scanner port whose default adapter reports `none` and says so.

### Context

§49 lists CSV, Excel, JSON, XML, PDF, API and marketplace feeds, and requires the
validation, security-check, parsing, schema-detection, normalization, validation,
canonical-mapping and storage stages, with large files handled asynchronously
(§28). None of it existed: `catalog_sources` and `source_records` were defined in
the ORM with no write path at all.

### Rationale

- **XML and PDF are excluded, not forgotten.** Neither `defusedxml` nor a PDF
  extractor is installed, and a parser is the most dangerous component in an intake
  path. Accepting a format nothing can read produces a source with zero rows and no
  explanation; a 415 does not. The extension allowlist is configuration, so adding
  a format is one setting plus one adapter.
- **Raw-only storage is the replay guarantee.** §50 requires that every finding be
  re-derivable against the catalog state it observed. If ingestion normalized as it
  stored, a change to the alias table would silently orphan every earlier decision.
  Keeping the record verbatim means normalization can be re-run without re-upload.
- **The sync/async decision is a pure function.** `plan_execution(size)` isolates
  the boundary, so it is testable with no broker. Defaulting to `inline` is honest
  while no worker runs: a queue with no consumer would make an upload look accepted
  and never processed.
- **The scanner port mirrors `MailDelivery` (D-017).** The seam is real; the adapter
  admits it does not scan. A "clean" that implies a scan happened would be worse
  than no scanner.

### Trade-offs accepted

- **No XML or PDF yet.** A real requirement is unserved until a parser decision is
  made — stated rather than hidden behind an extension that fails at parse time.
- **`inline` can hold a request open.** A 90 MB CSV parses in-request unless
  `dramatiq` is configured. Bounded by `MAX_UPLOAD_MB`, and the seam to move it
  exists.
- **Rows truncate, columns abort.** `INGESTION_MAX_ROWS` truncates with a warning
  (large catalogs are legitimate); `INGESTION_MAX_COLUMNS` refuses the file (more
  columns than expected is a different file, not a bigger one).
- **The worker is unverified.** No Redis is reachable, so the async path is
  exercised through unit tests of the functions it calls, not end to end.

### Open decision

Which capability gates ingestion. Resolved to `write` today and concentrated in
`require_ingestion_authority()`, because uploading a supplier file creates catalog
rows. The competing reading — that ingestion is an integration activity for the data
integration admin (D-004) — is recorded in that dependency's docstring. One line to
change either way.

### Alternatives rejected

- **Normalize during ingestion.** Fast, and it destroys the ability to replay a
  finding against the data it was drawn from.
- **Accept every §49 format now.** A parser for a format nothing reads is an attack
  surface with no user.
- **A real ClamAV integration in this phase.** Requires a daemon that is not
  deployed; the port is the part that survives either way.
- **All-async ingestion.** Needs a live broker, and it would make the feature
  unverifiable until Redis exists.

---

## D-019 — Canonical Product Model: Capture, Not Reconcile

**Status:** Implemented (Phase 11).

### Decision

Phase 11 turns raw `source_records` into canonical `products`:

- A pass maps each record through `normalize_record`, reduces it to an identity
  question, and runs `resolve_identity`.
- **Only an exact identifier match auto-links** (D-015). A fuzzy or strong-text
  match still creates a product, and the pair is recorded as an
  `IDENTITY_MISMATCH` conflict carrying its confidence and evidence.
- A record whose SKU already exists in the workspace is linked to that product
  rather than inserted, and is flagged as a `sku_collision` conflict.
- Products are created **`active`**, with a **v1 `product_versions` snapshot** in
  the same transaction.
- A workspace's **Default catalog** is created idempotently on first use, because
  `products.catalog_id` is NOT NULL and nothing created a catalog before.
- `source_records.product_id` (new nullable column, migration regenerated from the
  ORM) records which product a record folded into. **NULL means "not canonicalized
  yet", which is what makes the pass idempotent.**
- **Cross-source field resolution is deferred to Phase 12.** Each source is
  captured exactly as it described itself.

### Context

§7–§10 ask for a canonical product model built from normalization, identity and
relationships, with §50 requiring history that a finding can be replayed against.
The model tables existed; nothing could write to them, because there was no
`Catalog` and no path from a record to a product.

### Rationale

- **Capture-then-reconcile.** If the pass resolved disagreement while storing, a
  product could never be captured in the state its source described, and Phase 12
  would be reconciling an already-merged value — with no way to tell what each
  source actually said. Keeping intake faithful is what makes reconciliation
  explainable.
- **Exact-identifier auto-link only.** A missed merge is a visible duplicate; a
  wrong merge interleaves two products' histories permanently (D-015). Fuzzy
  matches become conflicts, which is the same queue every other disagreement
  already uses, so nothing new has to be taught to the UI.
- **Idempotency needs provenance, not heuristics.** The alternative — detecting
  "already canonicalized" by SKU — would either miss rows without a SKU or treat
  SKU as identity, which D-015 explicitly rejects. A nullable `product_id` says it
  directly and is useful afterwards: "which raw record produced this product" is
  a question the diff view will ask.
- **`status=active` at construction.** The column default is applied at flush, and
  the v1 snapshot is written in the same transaction. Relying on the default would
  have recorded `status: null` for a brand-new product, which reads as a defect.
- **`active` rather than `draft`.** Ingestion captures catalogue state that
  already exists; it does not publish a change. Drafting would hide a real
  catalogue behind a moderation state it was never submitted to.

### Trade-offs accepted

- **A SKU collision is linked, not rejected.** Two sources legitimately sharing a
  SKU must map to one product or the insert fails on `uq_products_workspace_sku`.
  Linking is the only non-erroring option, so it is linked and flagged for a
  human rather than merged silently.
- **Blocking uses only identity columns and brand.** Title-only blocking is not
  attempted in SQL. Since only exact identifiers can auto-link, extra recall could
  not change the decision, and a first-token `LIKE` would scan on large catalogs.
- **`product_id` is `ON DELETE SET NULL`.** Deleting a product returns its records
  to "not canonicalized". That is the correct direction: they must be re-resolved
  rather than left pointing at nothing.
- **The pass has not run against a live database.** Unit-tested offline only.

### Alternatives rejected

- **Normalize into products during ingestion.** Destroys replay, and makes a
  later alias-table change silently re-map history.
- **Detecting "already processed" by SKU.** Either misses rows or treats SKU as
  identity — the exact error D-015 exists to prevent.
- **Adding a `merge_candidates` table.** A merge candidate *is* a disagreement
  about identity, so it belongs in `conflicts`, which already has the type, the
  confidence, the evidence and the approval path.
- **Defer provenance to a later phase.** Without it the pass cannot be re-run
  safely, and "re-run after fixing the mapping" is the normal operation of one.

---

## D-020 — Reconciliation: Authority Proposes, Risk Scores, Humans Decide

**Status:** Implemented (Phase 12).

### Decision

- **Source authority uses the D-002 defaults.** §12 requires asking before
  implementing the final logic, so the ladder was confirmed rather than assumed.
  `workspace.source_authority_config` remains unread; wiring per-workspace
  overrides is a deliberate next step.
- **A run is scoped to one catalog** and records a `ReconciliationRun` bound to
  `catalog_version`, so findings replay against the state they saw (§50).
- **A conflict is always written; a proposal only when authority resolved and the
  proposed value differs from the stored one.** A tie writes a conflict and no
  proposal (§11), and a value that already matches never reaches the queue.
- **Every proposal is `requires_approval=True` → `PENDING_APPROVAL`.** The risk
  level decides *how many* approvers (Phase 18), never *whether* one exists.
- **`validation_status` is `pending`** until the sandbox (Phase 17) has run.
- **Re-runs supersede:** open conflicts and proposals for the same
  (product, tracked field) are soft-deleted first. `APPROVED` and beyond are
  never touched, so an approval is never overwritten.
- **Conflicts do not store `resolved_value`/`resolved_by`/`resolved_at`.** The
  authority's suggestion lives on the proposal; a conflict closes when a human
  decides.

### Context

§11 (conflict detection), §12 (configurable source authority), §15 (change
proposals) and §16 (deterministic risk) all had their logic implemented —
`detect_field_conflicts`, `authority.resolve`, `risk.assess` — with **no writer
anywhere**. Nothing created a `Conflict`, a `ChangeProposal`, a `RiskAssessment`
or a `ReconciliationRun`, so the product's central claim was unimplemented.

### Rationale

- **Authority proposes; it does not decide.** §11 says the system "must not simply
  select one". Recording the disagreement *and* a proposal keeps both: the queue
  shows what is proposed, and the conflict keeps every competing value.
- **Supersede rather than append.** Re-running a pass is the normal operation
  after a mapping fix. Without supersede, every re-run would double the approval
  queue — and a reviewer who clears a queue that instantly refills stops reading it.
- **Confidence is clamped into (0, 1), never stored as 0 or 1.** The column is
  CHECK-constrained for exactly that reason (§37): a 1.0 would read as certainty
  and as permission to execute.
- **`numeric_score` clamped to 100.** The CHECK allows 0..100 while the risk
  engine sums weights up to 422. Nothing is lost: the band comes from the raw
  total and every contributing factor is stored beside it, so the raw score is
  recoverable.

### Trade-offs accepted

- **Workspace-specific ladders are not supported yet.** §12 explicitly allows a
  business to have a different source of truth; until overrides are wired, every
  workspace gets D-002's defaults.
- **No `ApprovalRequest` rows yet.** Phase 12 writes the proposal with its risk
  level; the approval gate that records *who* may approve is Phase 18. A pending
  proposal therefore has no approver assigned until that phase exists.
- **Products are processed in `limit` batches, not streamed.** A catalog larger
  than the limit needs several runs; the run records what it analysed so the
  partial pass is honest rather than silent.
- **`build_observations` re-normalizes each record per run.** Correct but not
  cached; profiling belongs with Phase 57 (performance), not here.

### Alternatives rejected

- **Auto-approving LOW-risk proposals.** §5 and §38 say humans approve. Deciding
  that risk level *skips* a human is a policy for Phase 18 to argue for with data,
  not for the engine to assume.
- **Keeping conflicts and proposals in one table.** They answer different
  questions — "do sources disagree" versus "should this value change" — and a tie
  must be able to produce the first without the second.
- **Marking conflicts resolved at authority time.** An algorithm would be closing
  its own conflict before anyone saw it, which makes `resolved_at` meaningless.
- **Per-product queries for run inputs.** N round trips on the path most likely to
  hold hundreds of products; §57 forbids exactly this.

### Amendment (Phase 18, 2026-10-07)

The two things this decision deferred are settled, and the rejected alternative
above stands.

- **The approver count comes from §38's ladder.** LOW, MEDIUM and HIGH each
  need one approval; CRITICAL needs two. §38's "explicit approval" for HIGH is
  a requirement that a human say yes — not a second human — while CRITICAL's
  "multi-step approval" is the multi-person case, which is what
  `approval_requests`' comment has always said about CRITICAL. The mapping is
  `REQUIRED_APPROVALS` in `backend/app/services/approval/workflow.py`, and a
  test asserts no level ever maps to zero.
- **Approval requests are written.** A proposal's first decision opens one with
  `required_approvals` set from its risk level. A second approval must come
  from a second actor — one account cannot supply both votes. Rejection is
  final at any count. A modification or a re-analysis closes the request, and
  the next decision opens a fresh counter, because approvals given to the old
  value say nothing about the new one.
- **Auto-approval was not adopted.** "Approve all low-risk" is a bulk *human*
  action, so §38's LOW band is cheaper to clear without becoming automatic.
- **Every decision writes an `AuditLog`** — D-013's table, first written in
  this phase — carrying the actor, the reason, the evidence, old and new
  value, the risk, and the approval it belongs to, plus a `payload_hash`
  recomputable from the row's own fields.

---

## D-021 — Compliance RAG: Two Deterministic Passes, Cited as Evidence

**Status:** Implemented (Phase 13).

### Decision

- **Hybrid retrieval with two independent passes** — `ts_rank` over a GIN
  `tsvector` and cosine distance over a pgvector `HNSW` column — merged and
  reranked by a deterministic function. No model call sits in the ranking path
  (§14: "Avoid naïve vector-only retrieval").
- **`search_vector` is a plain column filled after insert** by one
  bound-parameter `UPDATE ... SET search_vector = to_tsvector('simple', content)`
  scoped to (workspace, document). Index and query both use the `simple`
  configuration, so the two can never disagree about stemming.
- **`websearch_to_tsquery('simple', :q)`**, never `to_tsquery`: a user typing
  `!` must be searching, not editing the query grammar.
- **Section-aware chunking.** Markdown headings and `Section 12.3` style lines
  become `section_reference` on every chunk, so a citation points at a rule
  rather than at a paragraph. 1200 characters with 150 of overlap, token counts
  recorded per chunk.
- **Embeddings sit behind a Protocol with a null adapter.** With no provider
  configured, `embedding` stays NULL, retrieval degrades to keyword plus
  metadata, and every response names the signals that fired. A zero vector
  would be a worse answer than an absent one.
- **Idempotent ingest by content hash** (sha256 of the document text, unique
  per workspace): a scheduled refetch of a marketplace's rules writes nothing
  the second time.
- **Metadata filters are pushed into SQL** — marketplace, country, document
  type, rule type, version, category path, effective date — so a rule for the
  wrong marketplace is never inside the candidate set to begin with.
- **Both passes are built by pure functions** (`keyword_statement`,
  `vector_statement`) whose compiled WHERE clauses the test suite asserts
  offline, including the tenant predicate.
- **This router returns evidence, never a verdict.** No rule-cited compliance
  decision (D-005); the search response says "Evidence for a recommendation,
  not a compliance decision" in words the UI can show as written.
- **Gated on `write`** via `require_compliance_authority`, on all three routes.
  The OPEN DECISION (`write` versus `configure_sources`) is resolved here and
  the rejected alternative is argued in the dependency's docstring; the gate is
  one named dependency so the answer stays a one-line change.
- **No seeded corpus.** §68 ships seed documents as a separate task; nothing
  here is demo data.

### Context

§13 requires the AI to identify the exact rule or document section supporting a
recommendation. §14 requires semantic search, keyword search, metadata
filtering, hybrid retrieval and reranking over marketplace compliance,
internal policies, specifications, manufacturer documentation, business rules
and historical decisions — and explicitly rejects vector-only retrieval. D-006
had already put `compliance_documents` / `compliance_chunks` with a `tsvector`
and a `Vector(1536)` column in the schema, but nothing anywhere chunked a
document, embedded it, indexed it or searched it.

### Rationale

- **Each pass covers the other's blind spot.** Exact identifiers — character
  limits, category codes, section numbers, attribute rules — are what embedding
  similarity handles worst, and they are what a compliance rule is mostly made
  of. Paraphrase is what term matching handles worst ("limited-time offer"
  versus "flash sale"). §14 asks for both for exactly this reason.
- **Ranking must be checkable.** A reviewer being told "this rule supports the
  change" needs to be able to see why it ranked first. A learned or model-based
  reranker in the citation path would contradict "the LLM proposes,
  deterministic code validates" and would cost a model call per search to add a
  claim no human can audit.
- **Signals travel with every hit.** §37's warning about confidence applies to
  retrieval: a keyword match and a semantic match arrive looking identical in
  JSON, and only one of them may be a coincidental exact-term overlap. The
  response names the signals, and a keyword-only deployment says so in its
  note rather than sounding equally sure.
- **The pass builders are separate functions** for the reason D-013 pushes
  guarantees into the database: a tenant predicate that lives only inside a
  query body is a predicate no test reaches. Compiling both statements and
  asserting the workspace id is in them is the offline version of that check.
- **One gate, `write`, over both the marketplace half and the internally
  authored half of the corpus.** `configure_sources` would have handed
  authorship to a role that cannot write catalog data at all, which for §14's
  internal policies and business rules means the policy owner cannot publish
  their own policy. The concern the alternative addresses -- an approver
  authoring the evidence their approval cites -- is bounded by D-005 (evidence,
  never a verdict) and visible in D-013's audit log, and is written down on the
  dependency so it can be revisited as a one-line change.

### Trade-offs accepted

- **Rerank runs over a 50-candidate pool, not the whole corpus.** Correct for a
  per-workspace policy corpus of a few hundred documents; a larger corpus needs
  a real candidate-generation strategy, which belongs with Phase 57.
- **The reranker is term overlap plus a phrase bonus blended with cosine
  distance**, weighted for explainability rather than fitted on a labelled set.
  Relevance has no measurement harness yet, so a "better" weighting would be an
  opinion.
- **`search_vector` is filled by an explicit UPDATE rather than a generated
  column.** The fill is visible, scoped to one document, and assertable
  offline. A computed `tsvector` column has not been exercised on this
  ORM-plus-generator path and would move indexing behaviour into DDL the suite
  does not inspect.
- **No query log and no relevance feedback**, so nothing yet records whether
  retrieval is getting better or worse.
- **Embeddings are the untested path.** No embedding provider has ever been
  run against this system; the null adapter is the path with coverage, and the
  API reports which path was taken rather than implying otherwise.
- **The initial migration's index access methods had to be corrected.** The
  generator emitted only `unique` and a partial predicate, so the GIN and HNSW
  indexes were written as plain btree — invisible to an index-name parity check
  and fatal on `alembic upgrade` (neither `tsvector` nor `vector` has a btree
  opclass). The generator now emits `using`, `with`, `ops`, `include`,
  `concurrently` and `nulls_not_distinct`, and a test asserts the access method
  survives generation.

### Alternatives rejected

- **Vector-only retrieval.** §14 forbids it, and the corpus is full of the
  exact tokens it would miss.
- **Keyword-only retrieval.** No paraphrase matching: a search for claims
  language would fail whenever the wording differs, which is how prohibited
  claims are usually worded differently from the rule that bans them.
- **An LLM reranker.** A non-deterministic component underneath a product whose
  promise is that the recommendation is checkable, bought at the cost of a model
  call on every search.
- **Embeddings in a JSON payload.** Not indexable — HNSW needs a typed vector
  column — and metadata filtering must stay relational, which is D-006's whole
  reason for putting vectors in the primary database.
- **Applying metadata filters after retrieval.** The top-k would already be
  shaped by documents the caller excluded, so filtering afterwards returns
  fewer results from a candidate set that was wrong to begin with.
- **A compliance verdict from this router.** D-005 rules out a rule-cited
  compliance engine in the MVP; these routes supply evidence and the UI must
  label early compliance findings as heuristic.

---

## D-022 — Execution: One Writer, Bounded Attempts, Rollback From Observed State

**Status:** Implemented (Phase 20). Unverified against a live marketplace — no connector other than the mock exists.

### Decision

- **One writer.** `execute_proposal()` is the only code path that changes a
  listing. Refusals (not approved, already executed, targets no product) raise
  `ExecutionRefusedError` before a job row exists; anything that fails after
  dispatch is an `execution_jobs` row with the original error preserved and
  recovery instructions written per outcome (§21).
- **Verification is a re-read, not the write's return value.** After
  `update_product` the engine calls `get_product` and compares. A mismatch is
  retried inside the same bounded budget, then rolled back with the value it
  actually reads recorded in the error.
- **Attempts are bounded by the database.** `attempt_count <= max_attempts`
  is a `CHECK` on `execution_jobs` (default 3), and only
  `ConnectorError.retryable` errors are retried — rejected credentials and
  unknown ids stop after one attempt.
- **Rollback is automatic when the adapter declares it, from the state
  observed before the first write.** `previous_state` and
  `rollback_reference` are captured before dispatch, so a restore can never
  depend on data the write may have changed. No rollback capability means
  `rollback_supported=False` and instructions saying restoration is manual
  (§51); a rollback that itself fails lands at `PARTIALLY_FAILED`.
- **The target is resolved from `marketplace_products.external_product_id`,
  never stored on the job.** A missing listing is a recoverable failure with
  "sync the product" instructions, not a programming error.
- **Tool calls are recorded at the single dispatch point.** An optional
  recorder on `ToolRegistry` (arguments masked per D-010, results stored as
  metadata) writes one `tool_calls` row per invocation; registries built
  without a recorder write nothing, keeping the graph free of database writes
  by default (D-016-era contract).

### Context

§21 defines execution as validate → dispatch → validate response → verify →
log, with recovery instructions on failure; §51 requires rollback to be
designed before the first live write and forbids promising universal rollback;
§23 requires a tool-call record. Phases 17–19 built proposals, approvals and
the connector seam, so `execute_proposal` is the seam's first real caller and
`execution_jobs` (D-006 schema) had no writer.

### Rationale

- **A refusal that becomes a row claims work that never began.** Nothing was
  dispatched, so the honest record is an error to the caller, not a job whose
  attempts, response and recovery text are all empty. Failures after dispatch
  must be rows: §21 asks for the error preserved and the failure recorded, and
  rows survive the process.
- **The write's return value is a claim by the same party that made the
  change.** Only re-reading the listing shows what the marketplace kept,
  which is why verification is a second `get_product` in every successful test.
- **Rollback cannot depend on "before".** Storing the state we intend to
  restore is the only way to guarantee it exists when the write fails
  halfway; reading it after a failure would restore whatever is there now.
- **A generic failure message is a support ticket waiting to happen.**
  "Validation was pending, no call was made", "credentials rejected on
  attempt 1 of 3, before any write", "does not support rollback; restore
  manually" each answer the question a human actually asks.

### Trade-offs accepted

- **No HTTP route, no worker.** Nothing calls `execute_proposal` except
  tests; the capability matrix has no `execute` capability yet, and choosing
  who may press the button is a decision, not a default.
- **One operation.** Proposals change one field on an existing product, so
  only `update_product` is reachable; `create_product`/`delete_product`
  exist on the seam and cannot be reached from an approval.
- **The job does not record its target id.** Reproducible from the proposal
  plus the listing; a denormalised column could drift from the one the
  dispatch actually used.
- **Records exist only where something called them.** No recorder, no
  `tool_calls` row; no caller, no `agent_runs` row. The seam is proven, the
  production callers are not built.

### Alternatives rejected

- **Returning the connector's response instead of re-reading.** Cheaper by
  one round trip, and it cannot detect a marketplace that normalised,
  truncated or rejected the value asynchronously — the exact case the
  verification stage exists for.
- **A job row for every refusal.** Pollutes the queue the batch drain will
  read, and records an attempt count of zero with an error that is really an
  API-level rejection.
- **Retrying verification mismatches until they pass.** A marketplace that
  uppercases titles never converges; the bounded budget plus rollback is what
  makes the loop terminate honestly.
- **Storing the previous state only when rollback is supported.** Then a
  no-rollback marketplace could never be restored manually either, and §51's
  "the plan exists before the write" would be a phrase rather than a row.

---

## D-023 — Frontend Data Access: Forwarded Session, Stated Fallbacks, URL Filters

**Status:** Implemented (Phase 21). **Amended in Phase 25** — verified against the real backend for the first time, which required a normalisation seam between the API's responses and the console's model.

**Phase 25 amendment.** Phase 21 drove the console against a stub serving the
frontend's own fixtures, so the fixtures, the console and the backend each
agreed with themselves and not with each other. Against the real API,
`/reconciliation` crashed the server: every list endpoint returns a named
collection with no envelope (`{sources: [...]}`), the console works in
`Page<T>` (`{items, total, limit, offset}`), and `plural()` calls
`count.toLocaleString()` on `undefined`.

- **`lib/normalize.ts` owns that mapping**, and `load()` now *requires* a
  normaliser argument. The `as T` cast it replaced is what let the
  disagreement survive two phases: nothing forced a function to state what the
  response meant.
- **A source's status is derived, not invented.** The API has no status field;
  the console's badges read one. It comes from the record count, which the API
  does send, so a badge marked `active` means something observable.
- **Rows pass through unchanged.** The console's row types are the API's field
  names; renaming them here would create a second vocabulary to keep in step.
- **The fixtures keep the console's shape.** They are what the screens are
  designed against and what the 93 frontend tests assert. The gap was never
  that the fixtures looked like the console — it was that nothing compared
  them to the API.

### Decision

- **Reads happen in server components and forward the session.** `lib/api.ts`
  reads `cookies()` from the incoming request and passes them to the backend.
  The previous version called the API with an environment variable and no
  cookie, so every tenant route answered 401 and the app could only ever show
  fixtures.
- **The workspace comes from the session, not the environment.**
  `GET /auth/me` reports memberships; the first is used unless a `cg_workspace`
  cookie names another one the caller actually belongs to. A stale cookie falls
  back rather than 403-ing the console.
- **Every read returns where its data came from.** `Loaded<T> = { data, source,
  reason }` with reasons `offline | unreachable | forbidden | server-error |
  disabled`, and the page prints the reason. "Not signed in" and "no data" look
  identical in a table and mean opposite things.
- **Writes happen in the browser through the same-origin `/api/*` rewrite**,
  with `credentials: "include"`, the CSRF token from `GET /auth/csrf`, and the
  header name that endpoint reports. Server components read; they do not
  mutate. No CORS grant is load-bearing.
- **Filters are GET forms over the query string.** No client store for table
  state: a filtered view is a URL, works without JavaScript, and survives a
  reload. Pagination reports rows shown versus rows on the page whenever a
  client-side filter narrows them.
- **No new npm dependencies.** Next 16, React 19 and TypeScript as D-001 chose
  them; no Tailwind, no shadcn/ui, no TanStack Query/Table, no React Hook Form,
  no Zod — §29's list was a proposal to confirm, not a mandate.

### Context

Phase 21's job was to reach §29–§30 with the routes Phases 7–20 had built. The
backend exposes tenant-scoped list endpoints with `limit`/`offset` and
`X-Workspace-Id`; the frontend had three pages reading fixtures, with approve
buttons deliberately disabled and filters that were decoration.

### Rationale

- **A silent fallback is a lie with a table on top.** The fixture path is the
  state an operator will meet most often while the backend is down, so it names
  its own cause every time.
- **A refused write must say why, not fail silently.** The write client surfaces
  the backend's `detail` string, so "A CRITICAL proposal needs a second
  approver" reaches the operator instead of a spinner that stops.
- **URL state beats a store here.** With one data source — the query string —
  the back button, a reload and a shared link all do the obvious thing, and the
  filter bar still works with JavaScript disabled, which is the §61 floor.
- **Contrast is a correctness property for the risk bands.** The band colours
  doubled as badge text and measured 1.9:1–3.1:1 on their own tints, so
  `--band-*` text tokens were introduced: same hues, readable lightness. A risk
  band nobody can read is a risk band the console cannot communicate.

### Trade-offs accepted

- **The catalog's text and status filters are page-local.** `GET /products` has
  no query parameters; the UI labels the limitation and the pagination status
  line reports what is actually on screen.
- **Three §30 pages are missing** (Product Detail, Marketplace Connections,
  Agent Activity): no route serves a product, a connection or an agent run.
  Their data appears as labelled fixtures or as an explicit "no route" card
  rather than as invented pages.
- **Verification used a stub API.** The real API needs Postgres and no database
  has ever been reachable, so a throwaway stub answered the same routes to drive
  the console in a browser. That proves the frontend's contract, not the
  backend's behaviour.
- **One HTTP client per direction** (`api.ts` server, `writeClient.ts` browser)
  rather than one shared module: the two differ in credentials, headers and
  cookie access, and merging them would mean importing `next/headers` into
  client bundles.

### Alternatives rejected

- **TanStack Query for caching and mutations.** A second source of truth
  beside the URL for filters, and a client cache that would show stale approval
  state next to an audit row written a second earlier.
- **Reading with the workspace in an environment variable.** It encodes one
  workspace per deployment, which is a single-tenant console wearing a
  multi-tenant schema.
- **Calling the backend directly from the browser.** Needs the frontend origin
  in the CORS allowlist and puts the session cookie on a second origin, which
  means `SameSite=None` and two origins to keep in sync (D-014's revocation
  guarantees rest on one origin holding the cookie).
- **An empty state for every unreachable backend.** It tells an operator their
  catalog is clean when nothing was read at all.
- **Optimistic bulk decisions.** D-020 keeps a person in the loop; showing a
  decided item before the server agrees would show a decision that was refused.

---

### Amendment (Phase 22, 2026-10-08)

The "no AI Copilot" trade-off above is lifted, with the boundary D-024 draws: a
read-only, cited, tool-backed copilot behind one capability, reachable as an API
and with no console surface yet. The reasoning behind the deferral — that
evidence-rich cards should carry the AI value — is kept: the copilot explains
and retrieves those cards' evidence, and cannot change what they say.

---

## D-024 — Copilot: Read-Only, Cited Answers, One Capability

**Status:** Implemented (Phase 22). Unverified against a live model — the Qroq key is still unrotated, so nothing here has called a provider.

### Decision

- **Five read-only tools over the tenant's own rows**, plus Phase 15's four pure
  tools: `product_lookup`, `conflict_search`, `change_search`,
  `compliance_search`, `sandbox_simulator`. No tool creates a proposal, approves
  one, or writes a product. The simulator takes `write` rather than `read`
  because it records a `SimulationRun` row.
- **One capability, `use_copilot`,** held by the ops manager and the data steward.
  Not the merchandiser, not the SEO specialist, and not the data-integration
  admin: the copilot answers across the whole catalog, and the integration admin
  holds credential custody (D-003, D-004).
- **Citations are enforced, not requested.** A citation naming a tool that never
  ran is dropped; an answer left making claims without one goes back for exactly
  one repair with the complaint in the prompt; a second failure is a 502 rather
  than a paragraph. `tools_used` in the answer is replaced with what the
  registry actually executed — a model's account of its own tool use is an
  intention, not a record.
- **`insufficient_data` is a first-class answer.** "The tools cannot cover this,
  and here is what is missing" is a successful response, not a failure.
- **Records per turn, no privileges**: one `agent_runs` row, one `tool_calls`
  row per call (Phase 20's recorder), one immutable `copilot.query` audit row
  carrying the question and the tools that answered it.
- **One POST, no act path.** The router exposes exactly one route and it cannot
  change anything, so there is no second gate to keep in step with the first.
- **Rate limiting is per process**, `copilot_requests_per_minute` per
  (workspace, user), and documented as per process. A shared limit belongs on
  Redis, whose credentials are still the originals awaiting rotation.

### Context

§31 asks for an optional copilot that "must use tools rather than hallucinating
database information". Phases 14–15 built the graph, the tool loop and the
validation layer; Phases 17–20 built the rows a copilot would need to ask about;
Phase 21's console is where an operator would use it. D-005 had deferred this
phase for MVP, on the grounds that evidence-rich cards carried the AI value
instead. The deferral is lifted here with a boundary, the same way Phase 19 gave
the marketplace deferral one.

### Rationale

- **Subordinate to the workflow, not beside it.** Every D-024 rule pushes toward
  the deterministic surfaces: the copilot may retrieve, explain, score and
  simulate, and a person still decides (D-020). A copilot that could approve
  would make the approval center's audit trail a record of a model's mood.
- **An uncited claim is the failure mode, so citations are checked
  mechanically.** The prompt asks for them; the service verifies each against the
  tools that ran. Relying on a model's compliance with an instruction is how a
  workspace ends up quoting a SKU that does not exist.
- **One route cannot be mis-gated.** Two copilot routes — ask and act — would
  mean two capability checks, and the second one is the one someone would forget.
- **A refusal is an answer.** The copilot that says "I cannot see that" is
  usable; the one that guesses is not, and `insufficient_data` exists so the
  difference is visible in the response body.

### Trade-offs accepted

- **No streaming.** The answer arrives whole, with its citations, so a partial
  stream cannot be read before it is complete. §57 lists streaming as a
  consideration, not a requirement; SSE is not built.
- **No UI.** §31's copilot is reachable as an API only. Phase 21's console has
  nowhere to put it yet, and a chat panel built without an accessible live
  region, focus management and a streaming story would be worse than none.
- **The model's account of itself is not trusted.** `tools_used` from the model
  is discarded; only the registry's execution list is returned.
- **Rate limiting does not survive multiple workers.** Documented in
  `app/services/copilot/rate_limit.py` rather than presented as a control.
- **One repair attempt.** Beyond that the service reports failure, because a
  model that cannot cite after being told exactly why is not going to cite on a
  third attempt — it is going to produce a longer paragraph.

### Alternatives rejected

- **Grant the copilot `read` for everyone.** A merchandiser's scoped write on two
  attribute groups implies a scoped view of the catalog; the copilot has none.
- **Let it create proposals.** That is D-020's territory and it needs a decision
  of its own: a proposal created by a model is a proposal nobody read.
- **Trust the prompt alone for citations.** Instructions are not enforcement, and
  the cost of the failure is a plausible, confident, wrong answer about a
  customer's catalog.
- **Stream tokens immediately.** A stream invites reading a half-finished claim
  as a finding, and the citations that make it checkable arrive last.
- **A dashboard-wide chat that preloads rows.** Sending the catalog into a prompt
  without a tool is §31's failure mode with extra steps: whatever the model
  recalls about rows it never asked for.

---

### Amendment (Phase 23, 2026-10-08)

The fourth trade-off above had no code behind it: `bind_context` existed and
nothing called it, and the four Langfuse settings had no reader. Phase 23 makes
the interim state real rather than aspirational — bound request context on every
log line, the Langfuse adapter written and env-gated behind `TraceSink`, and an
in-app summary over rows the system already writes. The destination is still
unset; what changed is that setting it is configuration rather than a build.

---

## D-025 — Observability: Correlate Everything, Account In-App, Trace Through a Port

**Status:** Implemented (Phase 23). The Langfuse wire call is unverified — no instance has ever been reachable.

### Decision

- **Every request binds its own context.** `RequestContextMiddleware` puts
  `request_id`, `method`, `path`, `status` and `duration_ms` on every log line,
  returns the id as `X-Request-Id`, and clears the context afterwards.
  `get_current_tenant` binds `workspace_id` and `user_id` at the first point
  either is known. §55's field list was absent rather than incomplete: the
  helper existed since Phase 8 and nothing called it.
- **An inbound request id is honoured only when it is a short opaque token.**
  Anything else is replaced, because a log line is not a place to echo a caller's
  string unexamined.
- **Traces go through a port with a null default.** `TraceSink` has two
  implementations: `NullTraceSink` (counts, discards) and `LangfuseTraceSink`
  (self-hosted, D-010's only destination), selected by configuration. No call
  site branches on whether observability is enabled, and a deployment with no
  configuration exports nothing.
- **Events carry counts, durations, statuses and ids — never content.** Keys
  shaped like content (`query`, `prompt`, `sku`, `arguments`, …) are masked at
  the event boundary as well as in the log processor, so D-010's rule 3 holds one
  layer above the log line it was written for.
- **A failing sink never fails a request.** Emitting is wrapped, the transport is
  bounded by `telemetry_timeout_seconds`, and plaintext to a remote host is
  refused. An observability backend that can take down a product change is worse
  than no backend.
- **Accounting is in-app and read-only.** `GET /api/v1/observability/summary`
  aggregates the rows the system already writes and is gated on `admin`. It has
  no write sibling: an endpoint that could silence telemetry or change a sink
  would be a way to make a system look healthy.
- **A signal nobody emitted is `null`; only a real zero is `0`.** The response
  carries `unavailable` in words and maps each of §32's requirements to the field
  feeding it, so a reader can tell "none happened" from "we cannot see".

### Context

§32 lists nine signals to track, §55 lists nine fields for structured logs, §56
asks for cost control, and D-010 decided where traces may go and that masking is
mandatory. Phase 4 left four Langfuse settings in the config with no reader, and
Phase 8 left a `bind_context` helper with no caller. Phases 17–20 then produced
exactly the rows an observability summary needs — simulation runs, agent runs,
tool calls, execution jobs — with nothing reading them.

### Rationale

- **Correlation before dashboards.** Two of §32's nine signals were unreachable
  without a sink; the other seven were already in the database. Binding request
  context and aggregating rows delivers most of the value with no external
  dependency, which is what D-010's "structured logging carries observability
  until Langfuse is stood up" anticipates.
- **A port, because the destination does not exist yet.** Phase 28 owns the
  deployment. Writing the adapter now means that phase configures something
  rather than building it — and writing it as an adapter means the alternative
  (instrumenting the code again at deployment time) is not on the table.
- **Counts only, because a trace is not a data export.** A trace carrying product
  text would move commercial data to a tracing backend under a masking rule that
  was written for log lines. The adapter's `input`/`output` fields are counts by
  construction, and the tests assert no credential and no content appears in a
  payload.
- **`admin`, not `read`, for the summary.** Token spend and failure rates are
  workspace-wide operational data. D-003 separated credential custody from
  approval authority for the same reason: some facts are about the system, not
  about the catalog, and both belong with the ops manager.

### Trade-offs accepted

- **Retrieval quality and model usage report `null` until a sink is configured.**
  They are traces, not rows. Writing them to a table would create an
  observability schema that D-010's destination does not need.
- **One log line per request**, which at volume is most of the log volume.
  Correlation is worth the bytes; Phase 28 decides where they land.
- **The summary is capped at 2,000 rows per section** and says so in
  `unavailable` when it bites. No aggregation table exists.
- **Tokens, not currency.** There is no price table, so the payload reports token
  counts. D-010 traded away the vendor cost dashboard; this works around that
  rather than solving it.
- **No Prometheus endpoint, no dashboards, no alerts.** §32 asks what to track.
  Charting and paging belong to the deployment phase, and an alert nobody has
  agreed a threshold for is noise.

### Alternatives rejected

- **A `/metrics` Prometheus endpoint now.** It would need a cardinality policy, a
  scrape target and a retention decision — all deployment questions — before it
  could be right, and the summary endpoint already gives a dashboard something
  stable to poll.
- **Writing tool-call and LLM payloads to a table so every signal works today.**
  That duplicates Langfuse's own storage in the transactional database, where
  masked, low-value rows would have to be migrated, retained and eventually
  deleted.
- **Emitting traces by default to a local collector.** It would look like
  observability working while silently buffering unbounded prompt-adjacent data
  in a process that has no backpressure story.
- **One log line per component call.** §55 asks for request-scoped fields, not
  per-call tracing; the request line plus the component lines already correlate,
  and the component lines are what a debugging session actually needs.
- **Gating the summary on `read`.** Every role has `read`, including the
  merchandiser whose view of the system should not include its failure rates.

---

## D-026 — Test Infrastructure: A Real Database, the Served Spec, and CI as the Gate

**Phase:** 24 — Testing
**Date:** 2026-10-10
**Status:** Implemented

### Decision

- **The suite starts a real Postgres.** `tests/integration` provisions
  `pgvector/pgvector:pg16` through the Docker CLI when
  `CATALOGGUARD_TEST_DATABASE_URL` is unset, and uses that URL when it is set.
  With neither available the suite **skips loudly** rather than passing against
  nothing.
- **RLS assertions run as a `NOSUPERUSER` role.** Superusers bypass policies
  even when `FORCE`d, so an isolation test run as the owner observes nothing.
  The suite creates `catalogguard_app` and runs as it.
- **No `testcontainers` dependency.** Four `subprocess` calls against a Docker
  CLI that Phase 28 requires anyway. No `pytest-docker` plugin, either.
- **The API contract is asserted against `app.openapi()`**, not against
  in-process status codes: every operation under the configured prefix, a
  schema on every response, a documented failure mode, tenant header coverage,
  and authentication declared as the session cookie on all 33 tenant routes.
- **CI runs the gates on every push**: ruff, mypy, unit and agent suites,
  integration against a service container, a migration-parity check that
  regenerates and diffs, and the frontend's four gates. No coverage threshold —
  a number that gates merges gets gamed; the parity check cannot.
- **`ruff format` is not enforced.** The codebase has never been formatted with
  it, and adopting it inside a testing phase would bury the change.

### Reason

Phase 24 found three bugs that 1119 passing unit tests could not: an HNSW index
missing its operator class, RLS never enabled anywhere, and a login flow that
resolved every user to zero workspaces. Each was invisible to a mock because
each is a fact about a string reaching a parser, or a fact about a policy
meeting a query.

The contract test found two more that are smaller but the same species: three
routes documenting no failure, and a spec that declared no authentication —
so `/docs` described an API that appeared to need no credentials.

The decisions above are the ones a reasonable person might have made
differently. Docker-lifecycle ownership, superuser-versus-role, and a
spec-level contract rather than a request-level one were the real choices; the
rest follows.

### Trade-offs accepted

- **A bare `pytest` now starts a container** (~20s) instead of finishing in
  seconds. CI's unit job uses `-m "not integration"`. The alternative is a
  coverage claim that depends on a flag nobody sets.
- **CI cannot run Windows or macOS.** One PostgreSQL dialect is the one D-006
  commits to; testing three would test the platform, not this system.
- **Migration parity is asserted on the generated file**, so a hand-edited
  migration fails CI. That is the intent, and it means the migration is a build
  artifact (§6) rather than a file to hand-maintain.
- **No E2E, security or performance coverage yet.** Those are Phases 25–27, and
  a testing phase that claimed them would be claiming work §64 assigns
  elsewhere.

### Alternatives rejected

- **Testcontainers.** A dependency for four Docker calls, with its own socket
  detection, image-pull retry and readiness race.
- **Running the integration suite as the database owner.** The tenant-bound read
  returned another workspace's row in the suite's first version; the assertion
  caught it, and the owner is still wrong.
- **A coverage percentage gate.** It measures lines executed, not behaviour, and
  Phase 24's bugs would all have sat comfortably inside a threshold.
- **Contract tests generated from a committed OpenAPI snapshot.** The snapshot
  would drift and nobody would notice until the diff grew teeth; asserting
  against the document the app actually serves keeps the two identical by
  construction.
- **Defeating `ruff format` on this phase.** It is a codebase-wide reformat
  wearing a testing phase's clothes.

---

## D-027 — Browser Validation: Drive the Built Console Against the Real API, Over Raw CDP

**Phase:** 25 — Browser DevTools validation
**Date:** 2026-10-13
**Status:** Implemented

### Decision

- **Validation runs against the built console, not the dev server.** `next dev`
  (Turbopack, Next 16.3.8) serves HTML that never hydrates in this environment,
  with no console error. `next start` hydrates and works. §41 asks whether the
  product works; the dev server is not the product, and a finding that only
  reproduces on a non-hydrating dev server would be a finding about the tool.
- **The harness speaks CDP directly, with no dependencies.** Node 22's global
  `WebSocket` and an installed Chrome are enough for the console, network,
  cookie, storage and performance panels. Playwright was declined for now and
  remains the better answer if the console grows.
- **Aborted requests are classified, not counted as failures.** The console
  prefetches every route and cancels those in flight on navigation. 64 of them
  appeared in the first run and buried everything else. An abort that is *not* a
  prefetch is still a failure.
- **A validator that cannot fail is worse than none.** Three harness defects
  were found and fixed, each of which produced a false pass: a hydration check
  that missed hydrated React because the fibre key is non-enumerable, synthetic
  input that left React's value tracker unchanged, and a `waitFor` that
  returned a DOM node to `returnByValue`. They are recorded on the phase page
  because the next person to write a browser test will hit the same three.
- **A write, not just a read.** The approve path is driven end to end and the
  result verified in the database, because a successful write is the only
  thing that proves CSRF, the workspace header and the session forwarding all
  at once.

### Reason

Phase 21 verified the console against a stub serving the console's own
fixtures. Each half of the system had been tested; the seam between them had
never been executed by anything. Four defects lived there, and the most
damaging — signing in twice returning 500 for every request — was invisible to
the entire backend suite because the query that broke was mocked.

### Alternatives rejected

- **Playwright as a dev dependency.** Better selectors, better waits, better
  traces; also ~40 MB of browser downloads and a second source of truth about
  which browser version runs. Reconsider when the suite needs more than one
  page and a click.
- **Driving `next dev`.** It reproduces a Turbopack defect this project does
  not own, and the resulting report would describe the wrong system.
- **Asserting on screenshots.** A screenshot proves something rendered; it does
  not prove a cookie is HttpOnly or that a table scrolls inside its box. The
  panels are the signal §41 asks for, and they are queryable.
- **Treating every failed request as a defect.** 64 of the first run's 66
  failures were the console cancelling its own prefetches.

### Gaps

- `backend/tests/e2e` remains empty. This harness needs a running stack, so it
  is not part of `npm test`; wiring it into CI means the containers Phase 28
  will build.
- The dev-mode hydration failure is recorded as observed, not diagnosed.
