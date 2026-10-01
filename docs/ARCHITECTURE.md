# Architecture

CatalogGuard AI — catalog reconciliation and change-safety platform.

---

## The core principle

The LLM never writes to production catalog data. Not directly, not indirectly,
not through a tool call. The pipeline is deliberately split so that reasoning
and authority never sit in the same place.

```
User request
   ↓
Orchestrator (LangGraph)
   ↓
Data retrieval
   ↓
Normalization
   ↓
Reconciliation
   ↓
LLM reasoning ── proposes, with evidence and confidence
   ↓
Structured proposal (validated schema)
   ↓
Deterministic validation ── decides whether it is technically valid
   ↓
Risk engine ── scores consequences
   ↓
Sandbox simulation ── predicts downstream impact
   ↓
Human approval ── controls production
   ↓
Execution engine ── performs approved operations
   ↓
Post-execution validation
   ↓
Audit log
```

Three separations carry the whole design:

1. **Proposing vs. deciding.** The model produces a candidate. Code decides
   whether it is admissible. A high-confidence model output that fails schema
   validation is still rejected.
2. **Authority vs. action.** Who is right (D-002 precedence matrix) is
   independent of whether we may act (risk bands).
3. **Reasoning vs. judgment.** Risk, relationships, and validation are
   deterministic services. The model explains consequences; it does not assign
   final risk.

---

## Why the LLM is not trusted with structured output

Qroq is OpenAI-API-*compatible*, not OpenAI. Its strict JSON-schema structured
output is materially weaker. Since the entire proposal pipeline depends on
schema-enforced output, this is treated as an architectural constraint:

1. Every model call declares a Pydantic schema and validates before use.
2. Validation failure triggers a bounded repair loop.
3. After the repair budget is exhausted, the task fails loudly. It never falls
   through.
4. Prompts are schema-first, since provider-side enforcement cannot be trusted.
5. Retry tokens are counted against the cost budget.

The benefit of this design is that a provider swap later is a change to one
adapter, not to every node.

---

## Source authority (D-002)

Authority is resolved **per attribute**, never system-wide, and never by agent
negotiation. Letting models arbitrate authority produces confidently wrong
results and breaks referential integrity.

| Attribute | Level 1 | Level 2 | Fallback |
|---|---|---|---|
| Inventory | WMS | ERP / PIM | Lowest value (prevents overselling) |
| Pricing | ERP ledger | Shopify | Halt if margin floor breached |
| Titles / SEO | PIM | Content sheet | Overwrite losing sources |
| Shipping address | Courier API | CRM | Escalate on postcode mismatch |

Risk bands gate whether resolution may be applied automatically:

| Band | Behaviour |
|---|---|
| LOW | Auto-resolve, log to evidence ledger |
| MEDIUM | Conditional auto-resolve within margin deviation, notify |
| HIGH | Freeze, route to a data steward |
| CRITICAL | Explicit approval, no automation |

**One rule is deliberately stricter than the attribute's nominal band:** any
resolution that fans out to downstream channels after a human confirms a winner
is treated as HIGH regardless. A single accepted conflict propagating to every
connected channel is the highest blast-radius operation in the product.

---

## Tenancy (D-011)

`Organization → Workspace → Users → Catalogs → Connections`. Every query is
workspace-scoped. Cross-tenant reads are treated as a security defect, not a
bug.

The unit of isolation is the **workspace**, not the organization: one
organization may run separate marketplaces and credential sets per region, so
those are separate tenants with separate approvals and separate risk
configuration.

Isolation is enforced at three layers, because each covers cases the others
miss.

**1. Schema.** Tenant references are composite:

```sql
FOREIGN KEY (workspace_id, catalog_id) REFERENCES catalogs (workspace_id, id)
UNIQUE (workspace_id, id)   -- on every referenced tenant table
```

A row cannot point at another tenant's parent row even if the application
constructs the insert itself. The single-column `catalog_id → catalogs.id` form
is deliberately absent: it would bypass the pairing and make the ORM
relationship ambiguous.

**2. ORM.** `TenantRepository` takes a `TenantContext` in its constructor and
refuses to exist without one. Tenant and soft-delete predicates are applied
centrally, and `workspace_id` is stripped from update payloads so a caller
cannot move a row between tenants.

**3. Database.** Row-level security, `FORCE`d, on all 25 tenant tables:

```sql
CREATE POLICY tenant_isolation ON products FOR ALL
USING      (workspace_id = NULLIF(current_setting('app.current_workspace', true), '')::uuid)
WITH CHECK (workspace_id = NULLIF(current_setting('app.current_workspace', true), '')::uuid)
```

Three details are load-bearing:

- **It fails closed.** An unset or blank setting compares against NULL and
  matches nothing, so an unbound connection sees zero rows, not all rows.
- **`FORCE` is mandatory.** PostgreSQL exempts a table's owner from its own
  policies, and the application connects as that owner. Without `FORCE` the
  policies would exist in the schema and be inert in production.
- **`WITH CHECK` is mandatory.** Read-side protection alone still allows
  inserting rows attributed to another workspace.

The application binds a tenant per transaction with
`set_config('app.current_workspace', <uuid>, true)`. The third argument is
`SET LOCAL`, which discards the value at commit or rollback — required under a
transaction-mode pooler, where a plain `SET` would survive on the backend
connection and leak into the next request.

`organizations`, `users`, and `workspaces` carry no policy: they sit at or above
the boundary, and a policy keyed on a workspace's own `workspace_id` would be
circular. That exemption list is explicit, so a newly added table is a
deliberate act rather than an omission.

`workspaces` therefore has no `workspace_id` column of its own — it *is* the
tenant, and `id` is what every other table points at.

### Immutability and history (D-012, D-013)

| Table | Shape | Reason |
|---|---|---|
| `audit_logs` | `created_at` only; UPDATE and DELETE rejected by trigger | The trail an incident review depends on must not be editable, by any role |
| `product_versions` | `created_at` only | A proposal must be replayable against the exact state it observed |
| `tool_calls`, `llm_usage` | `created_at` only | Per-call records; revision would undermine cost accounting |
| everything else | `updated_at`, `deleted_at` | Soft-deleted so history survives |

Immutable tables have no `updated_at` and no `deleted_at` columns at all, so
"append-only" is a type choice rather than a convention. Retraction is a new
row. `TenantRepository` adapts via `hasattr(model, "deleted_at")`, so both
shapes are read and written through the same repository.

### Migrations are generated

The initial migration is a build artifact produced from ORM metadata, so a
model change without a regeneration fails the test suite. RLS policies are
derived from the metadata at migration time rather than listed by hand, which
means a newly added tenant table is protected by default.

---

## Roles (D-003, D-004)

| Role | Owns | Cannot |
|---|---|---|
| E-commerce Ops Manager | Precedence matrices, risk thresholds, policy | — |
| Data Steward / Catalog Manager | Conflict resolution, approvals | Connectors, ingestion |
| Data / Integration Admin | Ingestion, connectors, source mapping | Approve anything |
| Merchandiser | Inventory and price attributes | Approve |
| SEO / Content Specialist | Titles, descriptions, metadata | Identity, inventory, financial fields |

Credential custody is deliberately separated from approval authority, so
compromising an ingestion account does not grant the right to approve changes.

---

## Async execution

Catalog reconciliation cannot run inside an HTTP request. Dramatiq over Redis
handles ingestion, normalization, reconciliation, embeddings, simulation, and
batch execution.

Constraints: retries are bounded (never unbounded), job state is persisted to
Postgres so in-flight work survives losing Redis, and low free-tier LLM
concurrency means bulk runs queue rather than fail.

---

## Observability and data residency (D-010)

Catalogs contain proprietary manufacturer files, wholesale margins, and
marketplace listings. Telemetry must not leave our infrastructure.

- Self-hosted Langfuse only. LangSmith and public log aggregators are prohibited.
- Edge masking runs in the logging processor chain, before formatting, so it
  covers traces, errors, and exception payloads — not just log lines.
- Token and cost accounting is built in-house, since there is no vendor
  dashboard.

---

## Known limitations

- **Untested against a live database.** The schema, migration, and RLS are
  rendered to SQL in both directions and asserted against the ORM, but no
  authenticated connection has exercised them. RLS behaviour in particular
  (policy denial, `FORCE` under the owning role) needs a real Neon session to
  confirm.
- **No RLS bypass role.** Cross-tenant migration and admin tooling must bind a
  tenant explicitly today. A separately privileged role is a deliberate future
  decision, not an oversight.
- **Authentication is not built yet.** Nothing binds a tenant in production, so
  RLS currently denies every tenant query. This is the intended order: the
  policy ships before the identity layer that satisfies it.
- **Python 3.13.** Very new. Works, but a few dependencies may publish late
  wheels; 3.12 is the safer floor if that bites.
- **No compliance RAG at MVP.** Compliance findings will be heuristic rather
  than rule-cited. The UI must label them as such rather than presenting them
  as authoritative.
- **Execution path is mock-only.** Real marketplace rate limits and
  partial-failure modes remain unvalidated.
- **Dramatiq has no delayed-job scheduling.** Scheduled compliance scans will
  need APScheduler or cron-driven enqueue.