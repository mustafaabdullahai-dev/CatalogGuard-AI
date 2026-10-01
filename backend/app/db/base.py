"""Declarative base, mixins, and tenant scoping primitives (D-011).

Tenancy decision: shared schema with a row-level `workspace_id`.

The risk with that choice is a forgotten predicate. A filter that omits the
tenant column reads another customer's catalog, which is a breach rather than
a bug. This module makes that mistake hard to express: `TenantRepository`
has no method that can reach a row without a tenant in hand.

Enforcement is deliberately layered:
  1. Repositories require a tenant (structural, primary).
  2. Relationships join on workspace_id, so ORM traversal cannot cross it.
  3. Postgres row-level security is added in a later migration as a
     backstop for raw SQL, admin tooling, and ORM escape hatches.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, MetaData, Table, UniqueConstraint, event, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable,
# reversible constraint names. Without this, Postgres picks names that
# change between runs and down-migrations break.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TENANT_KEY_COLUMNS = ("workspace_id", "id")

# Marks a Table whose tenant key was added by this module, so the constraint is
# not added twice. Alembic's `CreateTableOp.from_table` reconstructs a Table
# from another, which re-fires `after_parent_attach` and would otherwise append
# a duplicate.
_TENANT_KEY_ADDED = "catalogguard_tenant_key_added"


@event.listens_for(Table, "after_parent_attach")
def _add_tenant_key_unique(table: Table, _metadata: MetaData) -> None:
    """Give every tenant-owned table `UNIQUE (workspace_id, id)`.

    Tenant isolation here is enforced by composite foreign keys, which pair
    `workspace_id` on the child with `workspace_id` on the parent. Postgres
    will only accept such a foreign key if the referenced column pair is
    itself unique, and a primary key on `id` alone does *not* satisfy that.

    This hook adds the constraint automatically so no model can forget it.
    Forgetting is the failure mode that matters: without the constraint the
    migration fails loudly at deploy time rather than silently permitting a
    cross-tenant parent reference.
    """
    if _TENANT_KEY_ADDED in table.info:
        return
    if "workspace_id" not in table.columns or "id" not in table.columns:
        return
    if not table.columns["id"].primary_key:
        return
    # Redundant when workspace_id alone is the primary key (e.g. `workspaces`).
    if set(table.primary_key.columns.keys()) >= set(TENANT_KEY_COLUMNS):
        return
    existing = {
        frozenset(c.name for c in uq.columns)
        for uq in table.constraints
        if isinstance(uq, UniqueConstraint)
    }
    if frozenset(TENANT_KEY_COLUMNS) in existing:
        return
    table.append_constraint(UniqueConstraint(*TENANT_KEY_COLUMNS))
    table.info[_TENANT_KEY_ADDED] = True


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """Creation and modification times. All timestamps are timezone-aware UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid
    )


class TenantScopedMixin:
    """Marks a table as tenant-owned.

    Deliberately NOT a `declared_attr` foreign key. The workspace reference
    is added explicitly per table so composite foreign keys can include
    `workspace_id`, which is what stops a row from pointing at another
    tenant's parent row.
    """

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )

    # Note: the mixin deliberately does not create a `workspaces` foreign key.
    # A composite key is added per table instead — see
    # `_add_tenant_key_unique` for why the composite form is load-bearing.


class SoftDeleteMixin:
    """Soft deletion (D-012).

    Present so a record can be removed from normal query paths while history
    is retained. Repositories must filter `deleted_at IS NULL`; see
    `TenantRepository` below, which does it centrally.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class TimestampedEntity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Abstract base for concrete entities."""

    __abstract__ = True


class TenantEntity(
    UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base
):
    """Abstract base for tenant-owned records."""

    __abstract__ = True


class SoftDeletableTenantEntity(
    UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base
):
    """Abstract base for tenant-owned records that can be soft-deleted."""

    __abstract__ = True


class SoftDeletableEntity(
    UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base
):
    """Abstract base for soft-deletable records that sit outside the tenant boundary.

    `workspaces` needs this. A workspace *is* the tenant, so inheriting
    `workspace_id` would give it a column that points at itself, no parent to
    point at, and a place for a bug to store a different tenant's id.
    """

    __abstract__ = True


class CreatedAtMixin:
    """Creation time only.

    For append-only tables. `updated_at` exists to record mutation, and a table
    with no update path has nothing to record; carrying the column would let a
    stray `UPDATE` look like a legitimate one.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImmutableTenantEntity(
    UUIDPrimaryKeyMixin, CreatedAtMixin, TenantScopedMixin, Base
):
    """Abstract base for append-only tenant records.

    No `updated_at` and no `deleted_at`, so the table's shape asserts that a
    row is written once. Retraction is a new row, not an edit or a delete.
    `TenantRepository` already checks `hasattr(model, "deleted_at")`, so these
    tables are read and written through the same repository as everything else.
    """

    __abstract__ = True


class TenantContext:
    """Request-scoped tenant identity.

    Bound per request. Any repository call requires one, so a query cannot
    execute without a known tenant. This is the mechanism that makes
    forgetting the tenant predicate non-expressible.
    """

    __slots__ = ("workspace_id", "organization_id", "user_id")

    def __init__(
        self,
        workspace_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.organization_id = organization_id
        self.user_id = user_id

    def __repr__(self) -> str:
        return f"TenantContext(workspace_id={self.workspace_id})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TenantContext):
            return NotImplemented
        return self.workspace_id == other.workspace_id

    def __hash__(self) -> int:
        return hash(self.workspace_id)


def tenant_filter(column: Any, tenant: TenantContext) -> Any:
    """Build the canonical tenant predicate.

    Use this in any hand-written query that bypasses a repository.
    """
    return column == tenant.workspace_id


def str_enum(
    python_enum: type[enum.Enum],
    *,
    name: str,
    length: int = 32,
) -> Enum:
    """A `VARCHAR` column constrained to the values of a `StrEnum`.

    `native_enum=False` so the column is a VARCHAR with a CHECK constraint
    rather than a Postgres enum type. Postgres enums are painful to evolve:
    adding a value is a schema change, removing one cannot be done in a
    transaction at all. A CHECK constraint has neither problem.

    `values_callable` is required. Without it SQLAlchemy persists the member
    *names* (`ECOMMERCE_OPS_MANAGER`) instead of the member *values*
    (`ecommerce_ops_manager`), which would contradict the values in the
    migration and in every API response.
    """
    return Enum(
        python_enum,
        name=name,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )