# Phase 24 — Testing

**Status:** Complete — a real Postgres, an OpenAPI contract over the served spec, §40's three missing agent cases, and CI that runs every gate. Browser E2E, security and performance testing remain Phases 25–27
**§64 scope:** Testing
**Governing sections:** §40 testing strategy, §42 API testing
**Decisions:** [D-026](../../DECISIONS.md); [D-006](../../DECISIONS.md) amended (pgvector confirmed against 0.8.6; hosted instance still unverified); [D-011](../../DECISIONS.md) amended (self-readable memberships)
**Commits:** `3fc8143`, `1fd86c3`, `7670864`, `b3f4c11`

> This page replaces the 2026-10-06 stub, which said "integration and e2e
> directories are empty" and "every existing claim of coverage is unit-level
> with a mocked database". That was accurate, and it understated the risk:
> nothing in this system had ever been executed by a database.

## What this phase delivered

The suite had 1068 passing tests and no database. Every assertion about
persistence was made against a mock, which means the two most load-bearing
claims in the codebase — that the migration produces the schema the ORM
describes, and that row-level security isolates tenants — had never been
executed by anything but a renderer.

Both were wrong, and a third bug was worse than either.

- **Migration failed on a real database.** The HNSW index on
  `compliance_chunks` named no operator class. pgvector 0.8 rejects that DDL
  outright, so `alembic upgrade head` could never have completed, and D-006's
  vector search could not have started. The ORM now declares
  `vector_cosine_ops`, and the generator raises rather than silently dropping a
  string-valued operator class — which is how this shipped in the first place.
- **Row-level security had never been enabled anywhere.** `enable_rls_sql`
  joined `ENABLE` and `FORCE` into one string with a semicolon, which asyncpg
  refuses ("cannot insert multiple commands into a prepared statement"). The
  migration failed before that point, so RLS was inert on every database that
  existed, leaving repository scoping as the only thing between one workspace's
  rows and another's. Two statements, executed separately.
- **Every signed-in user resolved to zero workspaces.** Found by the auth test
  written for this phase's exit criteria, and it is the reason that test is
  worth more than the other 41. `workspace_memberships` is tenant-scoped, so it
  carries a policy — but session resolution must read a user's memberships to
  learn which workspaces they belong to, so nothing can bind a workspace before
  that read. With no workspace bound, the policy returned nothing: `/auth/me`
  answered with a valid user and no workspaces, and every later request failed
  the tenant check. The product did not work. Fix and reasoning are in
  [D-011](../../DECISIONS.md#d-011) and below.

Beyond the bugs, the phase delivered what it was scoped to deliver:

- **An integration suite** of 42 tests over `pgvector/pgvector:pg16`: the
  migrated schema and its constraints, RLS enforced by a `NOSUPERUSER` role
  rather than bypassed by a superuser, repositories as real SQL, auth over
  HTTP, ingestion over real multipart, retrieval over real vectors, and §69's
  chain end to end.
- **An OpenAPI contract test** — 15 tests over the served document, covering
  §42's six requirements. It found three session routes documenting only
  success, and that all 40 operations declared no authentication at all,
  because a session cookie read by a dependency is invisible to FastAPI. The
  spec now declares the cookie and marks the 33 tenant routes as requiring it.
- **§40's three untested agent failure modes**: prompt injection through tool
  results, incomplete data, and hallucination resistance.
- **CI** — lint, types, both suites, migration parity and the frontend gates on
  every push, with the integration suite pointed at a service container.

## The bug that mattered, in full

It deserves its own section because it is the argument for everything else
here, and because the fix is a narrowing rather than a loosening.

```
request ─► resolve cookie ─► read user          (users: global, no RLS)
                    │
                    └─► read memberships       (tenant-scoped, policy applies)
                            │
                            └─► ??? a workspace cannot be bound yet:
                                the membership is what names it
```

`get_auth_context` binds a workspace as soon as it picks a membership, and it
picks one from those memberships. The dependency order makes the read happen
before anything that would satisfy the policy guarding the read.

Three ways out, and why two of them are wrong:

- **Exempt the table from RLS.** Simple, and it makes every user's memberships
  readable by anyone who can query the table — a cross-tenant disclosure to
  solve an availability problem.
- **Disable the read path for RLS.** Same disclosure with extra machinery.
- **Let a user read their own rows.** The policy becomes
  `USING (user_id = current_user OR workspace_id = current_workspace)
   WITH CHECK (workspace_id = current_workspace)`.

The third is what shipped. `get_auth_context` binds `app.current_user_id` from
the session it has already verified — never from anything a caller supplied —
and the write side keeps the narrower predicate. The result: a signed-in person
can find their own memberships before a workspace exists, cannot read anyone
else's, and still cannot add themselves to a workspace by pointing the setting
at their own id. Three tests in
`tests/integration/test_schema_real.py::TestMembershipSelfRead` exist solely to
stop a later reader from widening that predicate.

## How the suite gets a database

```
CATALOGGUARD_TEST_DATABASE_URL set?  ── yes ─► use it, migrate, done
          │
          no
          ▼
docker run pgvector/pgvector:pg16  ── wait for a real query, not a socket
          └─► alembic upgrade head
          └─► CREATE ROLE catalogguard_app NOSUPERUSER   ← RLS cannot be bypassed
          └─► run as the app role
```

Three decisions worth naming:

- **No `testcontainers` dependency.** The lifecycle is `subprocess` against a
  Docker CLI that has to exist anyway for Phase 28. A dependency that manages
  containers also manages a socket, an image pull and a readiness race of its
  own.
- **The non-superuser role is the point.** PostgreSQL superusers bypass RLS even
  when it is `FORCE`d, so an isolation test run as `postgres` observes nothing
  and passes while asserting the wrong thing. The suite's first version did
  exactly that — a tenant-bound read returned another workspace's row and the
  assertion caught it.
- **CI provides the database instead.** `CATALOGGUARD_TEST_DATABASE_URL` makes
  the suite use what the workflow's service container already started, which is
  faster and puts the lifecycle somewhere with logs. Both paths are exercised:
  the CI-shaped path was reproduced locally against a provided database.

`SET LOCAL` is transaction-scoped, so every transaction binds its tenant again
after a commit. That is not defensive coding; it is what the tests now pin.

## Gaps

- **`backend/tests/e2e` is still empty.** §69's chain runs through real tables
  and a `MockConnector`, but nothing drives a browser. Phase 25.
- **The agent tests prove the framing, not the model's obedience.** They assert
  that hostile content is delimited and marked untrusted, that the copilot role
  names the injection case, that a claim with no retrievable evidence is
  refused. Whether a particular provider ignores all of that is a live-LLM
  question, and the three opt-in live tests in `tests/agent` are where it would
  be answered.
- **Security and performance testing are Phases 26 and 27.** Nothing here
  attempts injection, traversal or load.
- **The integration suite runs in a bare `pytest`.** There is no marker to
  deselect it, so `pytest` starts a container. That is deliberate — a coverage
  claim should not depend on a flag nobody remembers to set — but CI's unit job
  uses `-m "not integration"` to keep the fast path fast.
- **`RAG_EMBEDDING_MODE=qroq` retrieval is still unverified** against a real
  provider. The retrieval tests use a deterministic local embedder: the database
  half is exercised, the provider half is D-007's contract.

## Verification

| Claim | How it was verified |
|---|---|
| Migration produces a working schema | Applied to pgvector 0.8.6; 34 tables including `alembic_version` |
| HNSW builds | `pg_indexes` shows `vector_cosine_ops`; 1536-dimension column |
| RLS isolates tenants | Run as `catalogguard_app`; 26 tenant tables enabled and forced |
| A session stores no usable token | Column equals SHA-256 of the cookie; wrong password writes no row |
| Revocation is a database fact | `revoked_at` set; replayed cookie rejected |
| Upload stores raw data | Multipart CSV becomes `source_records`, `navy` and `navy blue` both survive |
| Another tenant's source is a 404 | Real id, real authorization chain |
| Retrieval orders correctly | Nearest document first, filters in SQL, no cross-tenant corpus |
| Membership self-read stays narrow | Own rows visible, others' invisible, self-enrolment refused |
| Spec satisfies §42 | 15 tests over `app.openapi()` |
| Injection is contained | 16 agent tests over framed tool output and the copilot role |
| CI is correct | Each step reproduced locally, including the provided-database path |

Backend **1141 passed, 3 skipped**; integration **42 passed**; ruff clean; mypy
clean over 121 files; frontend **79 passed** with lint, typecheck and build
green.