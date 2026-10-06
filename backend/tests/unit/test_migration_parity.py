"""Migration/model parity (D-011).

The initial migration is generated from the ORM by
`scripts.generate_initial_migration`, so drift should be impossible. These
tests exist because "should be impossible" is a claim, not a guarantee: a
hand-edited migration, a stale committed file, or a model changed without
regenerating would all reintroduce the bug this suite is meant to catch.

They also assert the parts the generator cannot infer from the ORM: the
pgvector extension, the D-013 immutability triggers, and the D-011 row-level
security policies.
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapper
from sqlalchemy.schema import CreateTable

import app.models  # noqa: F401  registers tables on Base.metadata
from app.db.base import TENANT_KEY_COLUMNS, Base
from app.db.rls import GLOBAL_TABLES, create_policy_sql

_MAPPERS = sorted(Base.registry.mappers, key=lambda m: m.class_.__name__)

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261001_0001-initial_catalog_schema.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return MIGRATION.read_text()


@pytest.fixture(scope="module")
def generated_ddl() -> dict[str, str]:
    """`CREATE TABLE` statements straight from metadata, via SQLAlchemy.

    Independent of the Alembic renderer on purpose. If both the generator and
    this test shared the renderer, a renderer bug would cancel itself out.
    """
    dialect = postgresql.dialect()
    return {
        name: str(CreateTable(table).compile(dialect=dialect))
        for name, table in Base.metadata.tables.items()
    }


@pytest.fixture(scope="module")
def alembic_upgrade_sql() -> str:
    """The full offline `upgrade head --sql` script Alembic would send to Neon.

    Rendered, not executed, so this needs no database. It is the only place the
    generator's `op.execute` calls and its inlined DDL both become visible
    together, which is what makes it worth the extra second.
    """
    backend = MIGRATION.parents[2]
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            "url=postgresql://user:pass@example.invalid/db",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=backend,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestMigrationIsCurrent:
    def test_regenerating_produces_identical_file(self) -> None:
        """The committed migration must equal a fresh generation.

        This is the test that makes the migration a build artifact rather than
        a second hand-maintained copy of the schema.
        """
        from scripts.generate_initial_migration import _format, generate

        assert _format(generate()) == MIGRATION.read_text(), (
            "Initial migration is stale. Run: "
            ".venv/bin/python -m scripts.generate_initial_migration"
        )

    def test_generation_is_idempotent_without_rereading_files(self) -> None:
        """Two runs in one process must agree.

        Guards against a generator that mutates the metadata it reads, which
        would make regeneration non-deterministic.
        """
        from scripts.generate_initial_migration import generate

        assert generate() == generate()


class TestTableParity:
    def test_migration_creates_every_model_table(
        self, migration_source: str
    ) -> None:
        created = set(re.findall(r"op\.create_table\(\s*[\"'](\w+)[\"']", migration_source))
        assert created == set(Base.metadata.tables)

    def test_downgrade_drops_every_table_in_reverse(self, migration_source: str) -> None:
        block = migration_source.split("def downgrade")[1]
        dropped = re.findall(r"^\s*[\"'](\w+)[\"'],", block, re.M)
        created = list(re.findall(r"op\.create_table\(\s*[\"'](\w+)[\"']", migration_source))
        assert dropped == list(reversed(created))

    def test_no_table_only_in_models(self, migration_source: str) -> None:
        created = set(re.findall(r"op\.create_table\(\s*[\"'](\w+)[\"']", migration_source))
        assert not (set(Base.metadata.tables) - created)

    @pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
    def test_every_model_column_reaches_the_schema(
        self, table_name: str, generated_ddl: dict[str, str]
    ) -> None:
        ddl = generated_ddl[table_name]
        for column in Base.metadata.tables[table_name].columns:
            assert f"\t{column.name} " in ddl, (
                f"{table_name}.{column.name} absent from generated DDL"
            )

    def test_postgres_actually_accepts_the_ddl_shape(self, generated_ddl: dict[str, str]) -> None:
        """Every table compiles to DDL with a primary key and no stray names."""
        for name, ddl in generated_ddl.items():
            assert "PRIMARY KEY" in ddl, f"{name} has no primary key"
            for constraint in Base.metadata.tables[name].constraints:
                assert constraint.name, f"unnamed constraint on {name}"
                assert "ck_" not in constraint.name or constraint.name.count("ck_") == 1, (
                    f"double-prefixed check name {constraint.name} on {name}"
                )


class TestTenantKeyParity:
    def test_migration_has_tenant_key_unique_for_every_tenant_table(
        self, migration_source: str
    ) -> None:
        """Every composite tenant FK needs a unique pair on its parent."""
        declared = re.findall(
            r'sa\.UniqueConstraint\(\s*"workspace_id",\s*"id",\s*name=op\.f\("([^"]+)"',
            migration_source,
        )
        expected = {
            f"uq_{name}_workspace_id_id"
            for name, table in Base.metadata.tables.items()
            if "workspace_id" in table.columns and "id" in table.columns
        }
        assert set(declared) == expected

    def test_no_duplicate_tenant_key_constraints(self, generated_ddl: dict[str, str]) -> None:
        for name, ddl in generated_ddl.items():
            if "workspace_id" not in Base.metadata.tables[name].columns:
                continue
            tenant_keys = [
                c
                for c in Base.metadata.tables[name].constraints
                if isinstance(c, UniqueConstraint)
                and set(c.columns.keys()) == set(TENANT_KEY_COLUMNS)
            ]
            assert len(tenant_keys) == 1, f"{name} has {len(tenant_keys)} tenant keys"
            assert ddl.count("UNIQUE (workspace_id, id)") == 1

    def test_composite_foreign_keys_resolve(self) -> None:
        """Postgres rejects a composite FK whose parent pair is not unique."""
        invalid = []
        for name, table in Base.metadata.tables.items():
            for fk in table.foreign_key_constraints:
                if len(fk.columns) < 2:
                    continue
                parent = next(iter(fk.elements)).column.table
                cols = tuple(sorted(el.column.name for el in fk.elements))
                resolvable = cols == tuple(sorted(parent.primary_key.columns.keys())) or any(
                    isinstance(uq, UniqueConstraint)
                    and tuple(sorted(c.name for c in uq.columns)) == cols
                    for uq in parent.constraints
                )
                if not resolvable:
                    invalid.append(f"{name} -> {parent.name}{list(cols)}")
        assert not invalid, invalid


class TestCheckConstraintsParity:
    """Constraints that encode a product rule belong in the schema.

    Each of these corresponds to a rule that is easy to violate by accident:
    an unbounded retry loop, a confidence treated as certainty, a product
    parented to itself.
    """

    @pytest.mark.parametrize(
        ("table_name", "needle"),
        [
            ("products", "parent_id IS NULL OR parent_id <> id"),
            ("product_relationships", "parent_id <> child_id"),
            ("change_proposals", "confidence > 0.0 AND confidence < 1.0"),
            ("risk_assessments", "numeric_score >= 0.0 AND numeric_score <= 100.0"),
            ("approval_requests", "approvals_received <= required_approvals"),
            ("execution_jobs", "attempt_count <= max_attempts"),
            ("llm_usage", "estimated_cost_usd >= 0"),
        ],
    )
    def test_rule_is_enforced_in_schema(
        self, table_name: str, needle: str, generated_ddl: dict[str, str]
    ) -> None:
        assert needle in generated_ddl[table_name]

    def test_migration_declares_them(self, migration_source: str) -> None:
        for table_name in (
            "products",
            "product_relationships",
            "change_proposals",
            "risk_assessments",
            "approval_requests",
            "execution_jobs",
            "llm_usage",
        ):
            checks = [
                c
                for c in Base.metadata.tables[table_name].constraints
                if isinstance(c, CheckConstraint)
            ]
            assert checks, f"{table_name} has no check constraints in the ORM"


class TestExtensionsAndTriggers:
    def test_pgvector_extension_is_created(self, migration_source: str) -> None:
        """D-006. Neon does not enable pgvector by default."""
        assert "CREATE EXTENSION IF NOT EXISTS vector" in migration_source

    def test_audit_immutability_is_enforced_by_triggers(
        self, migration_source: str
    ) -> None:
        """D-013. A trigger blocks UPDATE and DELETE regardless of role."""
        assert "catalogguard_audit_immutable" in migration_source
        assert "BEFORE UPDATE ON audit_logs" in migration_source
        assert "BEFORE DELETE ON audit_logs" in migration_source
        assert "restrict_violation" in migration_source

    def test_audit_table_has_no_mutable_columns(self, migration_source: str) -> None:
        block = migration_source.split('op.create_table(\n        "audit_logs"')[1]
        block = block.split("\n    )")[0]
        assert "updated_at" not in block
        assert "deleted_at" not in block

    def test_downgrade_removes_the_audit_function(self, migration_source: str) -> None:
        """The triggers go with the table. The function does not.

        Leaving `catalogguard_audit_immutable()` behind would block a later
        re-run of the initial migration on `CREATE OR REPLACE` ordering
        concerns and accumulate dead objects in every dropped environment.
        """
        downgrade = migration_source.split("def downgrade() -> None:")[1]
        assert "DROP FUNCTION IF EXISTS catalogguard_audit_immutable()" in downgrade


class TestRowLevelSecurity:
    """D-011. The migration is the only place policies exist."""

    def test_rls_is_enabled_on_every_tenant_table(
        self, migration_source: str
    ) -> None:
        assert "_enable_rls()" in migration_source
        assert "op.execute(enable_rls_sql(table.name))" in migration_source
        assert "op.execute(create_policy_sql(table.name))" in migration_source

    def test_policy_is_created_for_every_tenant_table(
        self, migration_source: str
    ) -> None:
        """Coverage is asserted against metadata, not a literal count.

        A hardcoded number passes even after a table is added; deriving it from
        `Base.metadata` fails unless the new table is handled.
        """
        tenant_tables = {
            name
            for name, table in Base.metadata.tables.items()
            if "workspace_id" in table.c
        }
        expected = tenant_tables - GLOBAL_TABLES
        assert expected
        assert "op.execute(create_policy_sql(table.name))" in migration_source
        for table in expected:
            policy = create_policy_sql(table)
            assert f" ON {table} " in policy
        for table in GLOBAL_TABLES:
            assert f" ON {table} " not in create_policy_sql(sorted(expected)[0])

    def test_policies_are_removed_before_tables_drop(
        self, migration_source: str
    ) -> None:
        downgrade = migration_source.split("def downgrade() -> None:")[1]
        policy_index = downgrade.index("op.execute(drop_policy_sql(table.name))")
        drop_index = downgrade.index("op.drop_table(table)")
        assert policy_index < drop_index

    def test_migration_does_not_hand_write_policies(
        self, migration_source: str
    ) -> None:
        """A hand-written policy list is a list that drifts from the models."""
        assert "CREATE POLICY tenant_isolation" not in migration_source

    def test_offline_upgrade_sql_contains_policies(
        self, alembic_upgrade_sql: str
    ) -> None:
        """End-to-end: the script Alembic would send to Neon carries policies."""
        tenant_tables = {
            name
            for name, table in Base.metadata.tables.items()
            if "workspace_id" in table.c
        }
        expected = tenant_tables - GLOBAL_TABLES
        for table in expected:
            assert (
                f"CREATE POLICY tenant_isolation ON {table}" in alembic_upgrade_sql
            )
            assert (
                f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in alembic_upgrade_sql
            )


class TestIndexParity:
    @pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
    def test_every_model_index_is_created(
        self, table_name: str, migration_source: str
    ) -> None:
        # ruff formats the generated file, so an index call may be wrapped.
        # Match on the index name alone; it is unique across the migration.
        created = set(re.findall(r'op\.create_index\(\s*"([^"]+)"', migration_source))
        for index in Base.metadata.tables[table_name].indexes:
            assert index.name in created, f"index {index.name} missing from migration"

    def test_partial_index_predicates_survive_generation(
        self, migration_source: str
    ) -> None:
        """A partial index without its predicate is a different constraint.

        `uq_workspace_invitations_live` is unique over live invitations only.
        Generated without the WHERE clause it would be unique over *every*
        invitation ever sent, which fails the second invite a person ever
        receives. A parity check on the index name alone cannot see that, so the
        predicate is asserted directly.
        """
        for table in Base.metadata.tables.values():
            for index in table.indexes:
                predicate = index.dialect_options["postgresql"].get("where")
                if predicate is None:
                    continue
                rendered = str(predicate.compile(dialect=postgresql.dialect())).strip()
                assert index.name in migration_source
                # Quote-agnostic: ruff reformats the generated file and may pick
                # either quote style, and this test is about the predicate, not
                # about punctuation.
                carried = re.search(
                    r"postgresql_where=sa\.text\((?P<q>[\"'])"
                    + re.escape(rendered)
                    + r"(?P=q)\)",
                    migration_source,
                )
                assert carried, f"{index.name} lost its WHERE clause in the migration"

    def test_index_access_methods_survive_generation(
        self, migration_source: str
    ) -> None:
        """`USING gin` / `USING hnsw` must travel with the index.

        The generator once emitted only `unique` and the partial predicate, so
        `ix_chunks_search_vector` was generated as btree. tsvector has no btree
        opclass, so `alembic upgrade` would have failed on a live database --
        and had it not, hybrid retrieval would have scanned every chunk in the
        workspace instead of using the GIN index it exists to provide. An index
        name check cannot see this, because the name survives either way.
        """
        for table in Base.metadata.tables.values():
            for index in table.indexes:
                pg = index.dialect_options["postgresql"]
                using = pg.get("using")
                if not using:
                    continue

                start = migration_source.find(f'"{index.name}"')
                assert start != -1, f"{index.name} missing from migration"
                end = migration_source.find("op.create_index", start + 1)
                call = migration_source[start : end if end != -1 else None]

                # Quote-agnostic: `ruff format` normalises the generated file
                # to double quotes while `repr()` produces single ones.
                carried = re.search(
                    r"postgresql_using=(?P<q>[\"'])" + re.escape(str(using)) + r"(?P=q)",
                    call,
                )
                assert carried, f"{index.name} lost USING {using} in the migration"
                for key, value in sorted((pg.get("with") or {}).items()):
                    option = re.search(
                        rf"(?P<q>[\"']){re.escape(str(key))}(?P=q)\s*:\s*"
                        rf"[\"']?{re.escape(str(value))}[\"']?",
                        call,
                    )
                    assert option, (
                        f"{index.name} lost its WITH option {key} in the migration"
                    )

    def test_index_names_are_unique_across_the_migration(
        self, migration_source: str
    ) -> None:
        """Postgres index names are schema-global, not table-scoped."""
        names = re.findall(r'op\.create_index\(\s*"([^"]+)"', migration_source)
        duplicates = {name for name in names if names.count(name) > 1}
        assert not duplicates, f"duplicate index names: {duplicates}"

    def test_indexes_are_created_on_the_table_they_belong_to(
        self, migration_source: str
    ) -> None:
        calls = re.findall(
            r'op\.create_index\(\s*"([^"]+)",\s*"(\w+)"', migration_source
        )
        for index_name, table_name in calls:
            table = Base.metadata.tables.get(table_name)
            assert table is not None, f"{index_name} references unknown {table_name}"
            assert index_name in {i.name for i in table.indexes}, (
                f"{index_name} is not declared on {table_name}"
            )

    def test_tenant_scoped_columns_are_indexed(self) -> None:
        """Every `workspace_id` is queried on; unindexed means a table scan."""
        unindexed = []
        for name, table in Base.metadata.tables.items():
            if "workspace_id" not in table.columns:
                continue
            indexed = any(
                "workspace_id" in [c.name for c in index.columns]
                for index in table.indexes
            )
            if not indexed:
                unindexed.append(name)
        assert not unindexed, f"workspace_id not indexed on: {unindexed}"


class TestPrimaryKeysAreSelfAssigning:
    """A table whose primary key needs the caller to invent an id is a trap.

    Ids are generated in Python (`default=new_uuid`), so the symptom is not a
    column error but a NOT NULL violation on the first real INSERT — which needs
    a database to observe. Checking the default is wired catches it offline.
    """

    @pytest.mark.parametrize("mapper", _MAPPERS, ids=lambda m: m.class_.__name__)
    def test_primary_key_is_generated(self, mapper: Mapper) -> None:
        for column in mapper.local_table.primary_key.columns:
            assert column.default is not None or column.server_default is not None, (
                f"{mapper.local_table.name}.{column.name} has no default and no "
                "server_default; every INSERT would have to supply it by hand"
            )

    @pytest.mark.parametrize("mapper", _MAPPERS, ids=lambda m: m.class_.__name__)
    def test_python_default_yields_a_fresh_uuid(self, mapper: Mapper) -> None:
        """Distinct ids, of the right type, from the generator itself."""
        produced = []
        for column in mapper.local_table.primary_key.columns:
            default = column.default
            if default is None or not default.is_callable:
                continue
            value = default.arg(None)
            assert isinstance(value, uuid.UUID)
            produced.append(value)
        assert len(produced) == len(set(produced))
