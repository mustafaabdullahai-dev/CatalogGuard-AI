# Phase log

One document per phase in the §64 development process, plus an index here.

**How these documents were written.** Phases 0–17 are complete; each page
records what that phase actually delivered, where the code lives, how it was
verified, and what remains unverified. The pages for phases 0–13 were written
retroactively on 2026-10-06 from the artifacts that survive — `DECISIONS.md`,
the commits, the test suite and `docs/ARCHITECTURE.md` — because no
contemporaneous phase log was kept; nothing on those pages is claimed beyond
what those artifacts show. Phases 14 to 17 were written as they were built. The remaining pages carry the §64 scope, the master-prompt
sections that will drive them, and a status row above that says how far any of
it has actually got — no page claims delivery it did not verify.

**Phase numbering is §64.** Where `DECISIONS.md` still labels D-014 through
D-017 with an earlier local numbering (8 through 11), the mapping below is the
one to trust. D-018 onward already use §64 numbers.

## Status

| Phase | Name | Status | Decisions |
|---|---|---|---|
| [00](phase-00-product-discovery.md) | Product discovery | Complete | — |
| [01](phase-01-ui-ux-discovery.md) | UI/UX discovery | Complete | D-001 |
| [02](phase-02-product-requirements.md) | Product requirements | Complete | D-002 – D-005 |
| [03](phase-03-system-architecture.md) | System architecture | Complete | — |
| [04](phase-04-technology-selection.md) | Technology selection | Complete | D-006 – D-010 |
| [05](phase-05-repository-structure.md) | Repository structure | Complete | — |
| [06](phase-06-development-environment.md) | Development environment | Complete | — |
| [07](phase-07-database-architecture.md) | Database architecture | Complete | D-011 – D-013 |
| [08](phase-08-backend-foundation.md) | Backend foundation | Complete | — |
| [09](phase-09-authentication-authorization.md) | Authentication / authorization | Complete | D-014, D-017 |
| [10](phase-10-catalog-ingestion.md) | Catalog ingestion | Complete | D-018 |
| [11](phase-11-canonical-product-model.md) | Canonical product model | Complete | D-019 |
| [12](phase-12-reconciliation-engine.md) | Reconciliation engine | Complete | D-020 |
| [13](phase-13-compliance-rag.md) | Compliance RAG | Complete | D-021 |
| [14](phase-14-langgraph-orchestration.md) | LangGraph orchestration | Complete | D-004, D-007 |
| [15](phase-15-ai-tools.md) | AI tools | Complete | D-003, D-004, D-007 |
| [16](phase-16-risk-engine.md) | Risk engine | Complete | D-016 |
| [17](phase-17-sandbox-simulation.md) | Sandbox simulation | Complete | — |
| [18](phase-18-approval-workflow.md) | Approval workflow | Not started | — |
| [19](phase-19-marketplace-connectors.md) | Marketplace connectors | Deferred (D-005) | — |
| [20](phase-20-execution-engine.md) | Execution engine | Not started | — |
| [21](phase-21-frontend.md) | Frontend | Partially built | D-001 |
| [22](phase-22-ai-copilot.md) | AI Copilot | Not started | — |
| [23](phase-23-observability.md) | Observability | Not started | D-010 |
| [24](phase-24-testing.md) | Testing | In progress | — |
| [25](phase-25-browser-devtools-validation.md) | Browser DevTools validation | Not started | — |
| [26](phase-26-security-testing.md) | Security testing | Not started | — |
| [27](phase-27-performance-testing.md) | Performance testing | Not started | — |
| [28](phase-28-docker-deployment.md) | Docker / deployment | Not started | — |
| [29](phase-29-production-readiness.md) | Production readiness | Not started | — |

## Phases to commits

The suite is **867 tests**: 821 in `backend/tests/unit` and 46 in
`backend/tests/agent` — 43 offline and 3 live-provider tests that run only when
`CATALOGGUARD_LIVE_LLM=1` is set. `integration` and `e2e` exist as directories
and are empty. A default run reports **864 passed, 3 skipped**.

| Phase | Commit |
|---|---|
| 5, 6, 8 — structure, environment, backend foundation | `0a4d3e7` |
| 7 — tenant-isolated data layer, generated migration | `a48a007` |
| 9 — authentication | `dd394c2` |
| 9 — password reset, invitations | `107e669` |
| 10 — catalog ingestion, async seam | `cfe1ede` |
| 11 — canonical product model | `fe5d087` |
| 12 — reconciliation engine | `e70f52c` |
| 21 — operations frontend shell, auth screens | `a6207d6`, `10067ae` |
| 13 — type-safety cleanup (mypy gate) | `7b3bc62` |
| 13 — compliance RAG, migration index fix | `adabaf0` |
| 13 — compliance capability gate settled | `9e3483c` |
| 13 — post-phase review fixes, this page set's phase-13 entry | `0b0a576` |
| provider model-id correction (prerequisite for 14) | `504009f` |
| 14 — LangGraph orchestration, D-007 validation layer | `6268cc0` |
| 15 — read-only tools, tool-call loop, §34 containment | `1cc3931` |
| 16 — §16 conformance audit, inventory impact, stored risk evidence | `ec65470` |
| 17 — sandbox simulation, §19 diff, simulate route | `f6b96d0` |

## Work that §64 has no phase of its own for

Two bodies of work landed before the phases that needed them and are recorded
in `DECISIONS.md` under their own numbers:

- **Catalog domain (D-015)** — normalization with an auditable alias table,
  exact-identifier identity resolution, parent-child validation, source
  authority. §64 Phase 10 and Phase 11 both depend on it, and Phase 12 cannot
  run without it. Implemented in `21eee9e`.
- **Conflict detection and risk (D-011 through D-016 territory)** — conflict
  rows with evidence and impact, and the deterministic risk engine. Conflict
  detection is §11; the risk engine is §64 Phase 16, whose own audit, inventory
  factor and stored-evidence work landed in `ec65470`. Implemented in
  `21eee9e`.

## Conventions in these pages

- **Verified** means a test asserts it offline. **Unverified** means the code
  exists and no test can prove it without infrastructure — a live database, a
  live queue, a live model.
- Every claim of "not verified" is deliberate (§67). If a page says nothing
  about verification, assume nothing was run beyond the unit suite.
