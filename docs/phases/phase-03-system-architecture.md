# Phase 03 — System architecture

**Status:** Complete
**§64 scope:** System architecture
**Decisions:** — (the document records D-001 through D-014 as they were made)

## What this phase delivered

`docs/ARCHITECTURE.md` (426 lines): the pipeline diagram that every later phase
positions itself inside, and the three separations the design rests on.

1. **Proposing vs. deciding.** The model produces a candidate; code decides
   whether it is admissible. A high-confidence output that fails schema
   validation is still rejected.
2. **Authority vs. action.** Who is right (D-002 precedence) is independent of
   whether we may act (risk bands).
3. **Reasoning vs. judgment.** Risk, relationships and validation are
   deterministic services. The model explains consequences; it does not assign
   final risk.

It also carries the sections that later phases cite: why the LLM is not trusted
with structured output, tenancy (D-011) with immutability and history (D-012,
D-013), generated migrations, authentication (D-014), account recovery (D-017),
the reconciliation pipeline so far, roles (D-003, D-004), async execution, and
observability and data residency (D-010).

## Where it lives

- `docs/ARCHITECTURE.md`
- `docs/phases/README.md` and these pages — §62 requires documentation per
  system area; the phase log is the process half of that.

## Verification

Not a coded phase. The architecture's claims are checked where they became
code: the pipeline's deterministic stages exist under `app/services/`, and the
separations are enforced by `require_approval_authority` and by the risk engine
never being callable from a tool.

## Gaps and honest limits

**`ARCHITECTURE.md` has drifted.** Its "Known limitations" section was written
earlier and now contains statements that are no longer true — most obviously
"Authentication is not built yet" (Phase 9 completed it) and "No compliance RAG
at MVP" (Phase 13 built it, which is itself the [D-005 conflict](phase-02-product-requirements.md)).
It also claims Python 3.13 while the committed venv is 3.12.14. Corrected
alongside this page; treat any limitation there as suspect until its phase page
confirms it.
