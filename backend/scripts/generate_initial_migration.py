"""Regenerate the initial Alembic migration from ORM metadata.

The initial migration is derived from `app.models` rather than written by
hand, because a hand-written copy of 28 tables drifts from the models the
moment either side changes. This generator closes that loop: the migration is
a build artifact.

Run after changing any model:

    .venv/bin/python -m scripts.generate_initial_migration

Two things are deliberately NOT generated, because they express intent that
does not live in the ORM:

  - `CREATE EXTENSION vector` (D-006)
  - the `audit_logs` immutability triggers (D-013)

Both are emitted from the constants below and are asserted by the migration
tests, so they cannot silently disappear.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic.autogenerate import render
from alembic.autogenerate.api import AutogenContext
from alembic.migration import MigrationContext
from pgvector.sqlalchemy import Vector
from sqlalchemy import MetaData, Table, create_mock_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import TSVECTOR

import app.models  # noqa: F401  registers tables on Base.metadata
from app.db.base import Base

# Alembic's type renderer qualifies dialect and third-party types with the
# module they were defined in, producing names like
# `pgvector.sqlalchemy.vector.VECTOR(dim=1536)`. That module path is not a
# name the migration imports, so the rendered code would raise NameError.
#
# `render_item` below intercepts exactly those types and emits the plain,
# importable spelling instead. The migration then needs only these imports.
RENDERED_TYPE_IMPORTS = (
    "from pgvector.sqlalchemy import Vector",
    "from sqlalchemy.dialects.postgresql import TSVECTOR",
)


def _render_item(
    kind: str, obj: Any, _autogen_context: AutogenContext
) -> str | bool:
    """Render pgvector and full-text types under names the migration imports.

    Returning `False` lets Alembic's default renderer handle everything else.
    The signature is fixed by Alembic: `(type_, object_, autogen_context)`.
    """
    if kind != "type":
        return False
    if isinstance(obj, Vector):
        return f"Vector({obj.dim})"
    # `mapped_column(TSVECTOR)` stores an instance, so check by type. Alembic
    # would otherwise qualify this as `postgresql.TSVECTOR()` using a module
    # name the migration does not import.
    if isinstance(obj, TSVECTOR):
        return "TSVECTOR()"
    return False

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "20261001_0001-initial_catalog_schema.py"
)

# D-013. Kept as a literal so the immutability guarantee is visible in the
# migration rather than inferred from a missing column.
AUDIT_IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION catalogguard_audit_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

AUDIT_TRIGGER_SQL = (
    """
CREATE TRIGGER audit_logs_no_update
BEFORE UPDATE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION catalogguard_audit_immutable();
""",
    """
CREATE TRIGGER audit_logs_no_delete
BEFORE DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION catalogguard_audit_immutable();
""",
)


def _autogen_context() -> AutogenContext:
    """A render context that never touches a database.

    `create_mock_engine` supplies the PostgreSQL dialect without a connection,
    which is enough for rendering type names and constraint syntax.
    """
    engine = create_mock_engine("postgresql://", lambda *_args, **_kwargs: None)
    context = MigrationContext.configure(
        dialect=engine.dialect,
        opts={"target_metadata": Base.metadata},
    )
    return AutogenContext(
        context,
        Base.metadata,
        opts={
            "sqlalchemy_module_prefix": "sa.",
            "alembic_module_prefix": "op.",
            # pgvector and the PG dialect types need a module they are
            # importable from; both are imported at the top of the migration.
            "user_module_prefix": None,
            "render_item": _render_item,
            "render_as_batch": False,
        },
    )


def _topological_order(metadata: MetaData) -> list[str]:
    """Tables ordered so a referenced parent is created before its children.

    Postgres rejects a forward reference, and the FK graph here has a genuine
    cycle risk (`products.parent_id` self-reference aside, `audit_logs` and
    `marketplace_connections` reference upstream tables). A stable topological
    sort with a deterministic tiebreak keeps the output reproducible.
    """
    remaining = dict.fromkeys(metadata.tables)
    edges: dict[str, set[str]] = {name: set() for name in remaining}
    for name, table in metadata.tables.items():
        for fk in table.foreign_key_constraints:
            parent = next(iter(fk.elements)).column.table.name
            if parent != name:
                edges[name].add(parent)

    ordered: list[str] = []
    while remaining:
        ready = sorted(name for name in remaining if not (edges[name] & remaining.keys()))
        if not ready:
            # A true cycle: emit what remains in declaration order and let the
            # migration fail loudly rather than hanging.
            ready = sorted(remaining)
        for name in ready:
            ordered.append(name)
            remaining.pop(name)
    return ordered


def _render_table(table: Table, autogen: AutogenContext) -> str:
    """Render one `op.create_table(...)` call.

    Renders from the live metadata rather than through
    `ops.CreateTableOp.from_table`. That constructor copies `table.info` onto a
    freshly constructed Table, which re-fires the tenant-key hook and yields a
    second `UNIQUE (workspace_id, id)` on the copy. Rendering the constraint
    list directly keeps the output a faithful projection of the models.
    """
    args: list[str] = [
        render._render_column(column, autogen) for column in table.columns
    ]
    # Alembic passes an internal namespace object here so a rendered
    # constraint can be marked as already-named and skip `op.f(...)`.
    # Constraints in this metadata already have convention-derived names, and
    # `MetaData.schema` is all the renderer reads, so the real MetaData works.
    args.extend(
        sorted(
            rendered
            for rendered in (
                render._render_constraint(cons, autogen, table.metadata)
                for cons in table.constraints
            )
            if rendered is not None
        )
    )
    lines = [f"op.create_table({table.name!r},"]
    for arg in args:
        lines.append(f"    {arg},")
    if table.comment:
        lines.append(f"    comment={table.comment!r},")
    lines.append(")")
    return "\n".join(lines)


def _indent(block: str, spaces: int = 4) -> str:
    """Indent every non-blank line of `block` to a uniform width.

    Lines are stripped first. The rendered SQL constants carry their own
    leading whitespace, and adding indentation to already-indented text is
    what produced unparseable output earlier.
    """
    pad = " " * spaces
    return "\n".join(
        pad + line.strip() if line.strip() else "" for line in block.splitlines()
    )


def _enum_metadata() -> dict[str, list[str]]:
    """Every enum name and its permitted values, read from the ORM.

    Non-native enums are VARCHAR plus a CHECK constraint, so the CHECK is
    emitted by `create_table` and nothing is created up front. The values are
    still collected so the generated header documents them and the tests can
    assert on them.
    """
    found: dict[str, list[str]] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            column_type = column.type
            if isinstance(column_type, sa.Enum):
                found.setdefault(column_type.name or "", []).extend(
                    str(v) for v in (column_type.enums or ())
                )
    return {name: sorted(set(values)) for name, values in sorted(found.items())}


def _index_calls(table: Table) -> Iterator[str]:
    """Emit `op.create_index` for each index on `table`.

    Deduplicated by name and by column list. A single-column `index=True` on a
    `TenantScopedMixin.workspace_id` produces an index whose name collides with
    an explicitly declared one; Postgres would fail on the second `CREATE`.
    """
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for index in sorted(table.indexes, key=lambda i: i.name or ""):
        key = (index.name or "", tuple(c.name for c in index.columns))
        if key in seen:
            continue
        seen.add(key)
        columns = ", ".join(repr(c.name) for c in index.columns)
        args = f"op.create_index({index.name!r}, {table.name!r}, [{columns}]"
        if index.unique:
            args += ", unique=True"
        pg = index.dialect_options["postgresql"]
        # The access method is the whole point of an index declared with a
        # non-default one. Emitting `ix_chunks_search_vector` as plain btree
        # would fail on live Postgres (tsvector has no btree opclass) and, if
        # it had succeeded, would have left hybrid retrieval doing a sequential
        # scan over every chunk in the workspace.
        using = pg.get("using")
        if using:
            args += f", postgresql_using={using!r}"
        # `WITH (m=16, ef_construction=64)` tunes HNSW build quality; dropping
        # it would change recall silently rather than fail loudly.
        with_options = pg.get("with")
        if with_options:
            rendered = ", ".join(
                f"{key!r}: {value!r}" for key, value in sorted(with_options.items())
            )
            args += f", postgresql_with={{{rendered}}}"
        ops = pg.get("ops")
        if ops:
            # A single-column index declares its operator class as a string; a
            # multi-column one as a `{column: opclass}` mapping. Emitting the
            # mapping form for one column produces `postgresql_ops={'embedding':
            # 'vector_cosine_ops'}`, which SQLAlchemy reads as a per-column
            # mapping with no key for the index's only column — so the operator
            # class silently disappears from the generated migration.
            if isinstance(ops, str):
                raise ValueError(
                    f"index {index.name!r} declares postgresql_ops as a string; "
                    "SQLAlchemy's PostgreSQL dialect ignores it and emits no "
                    "operator class. Use the mapping form, e.g. "
                    "{'embedding': 'vector_cosine_ops'}."
                )
            else:
                rendered = ", ".join(
                    f"{key!r}: {value!r}" for key, value in sorted(ops.items())
                )
                args += f", postgresql_ops={{{rendered}}}"
        include = pg.get("include")
        if include:
            rendered = ", ".join(repr(column) for column in include)
            args += f", postgresql_include=[{rendered}]"
        if pg.get("nulls_not_distinct"):
            args += ", postgresql_nulls_not_distinct=True"
        if pg.get("concurrently"):
            args += ", postgresql_concurrently=True"
        # A partial index is a different constraint, not a nicer one. Dropping
        # the predicate would silently widen a uniqueness guarantee that only
        # holds over the live rows, so the WHERE clause has to travel with it.
        predicate = pg.get("where")
        if predicate is not None:
            rendered = str(predicate.compile(dialect=postgresql.dialect())).strip()
            args += f", postgresql_where=sa.text({rendered!r})"
        yield f"{args})"


def generate() -> str:
    """Render the migration body, unformatted.

    Callers that write the file should pipe this through `_format`. Keeping
    generation and formatting separate is what lets the idempotency test
    compare the formatted result against the committed file.
    """
    metadata = Base.metadata
    autogen = _autogen_context()
    order = _topological_order(metadata)

    lines: list[str] = [
        '"""Initial catalog schema.',
        "",
        "GENERATED FILE. Do not edit by hand. Regenerate, do not reformat.",
        "",
        "Regenerate with:",
        "    .venv/bin/python -m scripts.generate_initial_migration",
        "",
        "Source of truth is `app.models`. Three concerns are not expressible in",
        "the ORM and are emitted from `app.db.rls` and the generator constants:",
        "  - the pgvector extension (D-006)",
        "  - the audit_logs immutability triggers (D-013)",
        "  - row-level security policies (D-011)",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Sequence",
        "",
        "import sqlalchemy as sa",
        *RENDERED_TYPE_IMPORTS,
        "",
        "from alembic import op",
        "",
        "from app.db.base import Base",
        "from app.db.rls import (",
        "    GLOBAL_TABLES,",
        "    create_policy_sql,",
        "    drop_policy_sql,",
        "    enable_rls_sql,",
        ")",
        "",
        'revision: str = "0001"',
        "down_revision: str | None = None",
        "branch_labels: str | Sequence[str] | None = None",
        "depends_on: str | Sequence[str] | None = None",
        "",
        "# D-006: embeddings and near-duplicate detection. Neon does not enable",
        "# pgvector by default.",
        'op.execute("CREATE EXTENSION IF NOT EXISTS vector")',
        "",
    ]

    enums = _enum_metadata()
    lines.append("# Enum values, reflected from the ORM. These are VARCHAR columns")
    lines.append("# with CHECK constraints rather than Postgres enum types, so adding a")
    lines.append("# value is a migration like any other column change.")
    lines.append("_ENUM_VALUES = {")
    for name, values in enums.items():
        lines.append(f"    {name!r}: {tuple(values)!r},")
    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append("def upgrade() -> None:")
    for name in order:
        table = metadata.tables[name]
        lines.append(f"    # {'-' * 68}")
        lines.append(f"    # {name}")
        if table.comment:
            lines.append(f"    # {table.comment}")
        lines.append(f"    # {'-' * 68}")
        lines.append(_indent(_render_table(table, autogen)))
        lines.append("")
        for index_call in _index_calls(table):
            lines.append(f"    {index_call}")
        if list(_index_calls(table)):
            lines.append("")
        if name == "audit_logs":
            lines.append("    # D-013: append-only enforced by the database, not by")
            lines.append("    # application discipline. A trigger blocks UPDATE and DELETE")
            lines.append("    # regardless of role.")
            lines.append("    op.execute('''")
            lines.append(_indent(AUDIT_IMMUTABILITY_SQL.strip(), spaces=8))
            lines.append("    ''')")
            for trigger_sql in AUDIT_TRIGGER_SQL:
                lines.append("    op.execute('''")
                lines.append(_indent(trigger_sql.strip(), spaces=8))
                lines.append("    ''')")

    lines.append("    # D-011: RLS goes on last, once every table exists, so the policy")
    lines.append("    # list is derived from the metadata rather than copied by hand.")
    lines.append("    _enable_rls()")
    lines.append("")
    lines.append("")
    lines.extend(_rls_functions_lines())
    lines.append("")
    lines.append("")
    lines.append("def downgrade() -> None:")
    lines.append("    for table in reversed(Base.metadata.sorted_tables):")
    lines.append("        if table.name in GLOBAL_TABLES or 'workspace_id' not in table.c:")
    lines.append("            continue")
    lines.append("        op.execute(drop_policy_sql(table.name))")
    lines.append("    # Reverse creation order so children drop before parents.")
    lines.append("    for table in (")
    for name in reversed(order):
        lines.append(f"        {name!r},")
    lines.append("    ):")
    lines.append("        op.drop_table(table)")
    lines.append("")
    lines.append("    # The triggers are dropped with the table; the function is not.")
    lines.append("    op.execute('DROP FUNCTION IF EXISTS catalogguard_audit_immutable()')")
    lines.append("")
    return "\n".join(lines)


def _rls_functions_lines() -> list[str]:
    """Source lines for the RLS helpers emitted into the migration.

    A literal block rather than assembled f-strings: the SQL contains braces
    and quotes that would need escaping repeatedly, and the result would be
    harder to read than the SQL itself.
    """
    return [
        "",
        "def _enable_rls() -> None:",
        '    """Attach a tenant policy to every tenant-owned table.',
        "",
        "    Tables in `GLOBAL_TABLES` sit above or at the tenant boundary and are",
        "    skipped deliberately. This runs after every table exists, so the policy",
        "    list comes from the metadata rather than a hand-maintained copy of it.",
        '    """',
        "    for table in Base.metadata.sorted_tables:",
        "        if table.name in GLOBAL_TABLES or 'workspace_id' not in table.c:",
        "            continue",
        "        for statement in enable_rls_sql(table.name):",
        "            op.execute(statement)",
        "        op.execute(create_policy_sql(table.name))",
    ]


_FILENAME = "alembic/versions/generated_migration.py"


def _format(source: str) -> str:
    """Run `ruff format` over generated source.

    `ruff` ships as a binary with a thin Python wrapper, so this shells out.
    The argv is fixed and the input is a path under the repo, not user input,
    and no shell is involved. Formatting here rather than leaving it to a
    developer is what keeps the committed file lint-clean and regenerating
    idempotent.
    """
    import subprocess  # noqa: PLC0415 - only needed on this path

    root = Path(__file__).resolve().parents[1]
    ruff = str(root / ".venv" / "bin" / "ruff")
    formatted = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ruff, "format", "-"],
        input=source,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # `format` does not sort imports, and the renderer emits them in the order
    # it discovers them. `check --fix` applies isort rules without changing
    # behaviour, so the committed file is lint-clean on generation.
    lint = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ruff, "check", "--fix", "--stdin-filename", str(_FILENAME), "-"],
        input=formatted,
        capture_output=True,
        text=True,
        check=False,
    )
    return lint.stdout


def main() -> None:
    path = MIGRATION_PATH
    path.write_text(_format(generate()))
    print(f"wrote {path.relative_to(path.parents[3])}")


if __name__ == "__main__":
    main()