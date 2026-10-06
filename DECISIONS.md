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
