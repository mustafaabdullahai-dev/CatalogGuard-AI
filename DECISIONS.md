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
