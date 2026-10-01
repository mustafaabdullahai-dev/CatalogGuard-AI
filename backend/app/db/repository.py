"""Tenant-scoped repository base (D-011).

Every read and write goes through a `TenantRepository`. There is no method
here that can touch a row without a `TenantContext`, which is the point:
a missing tenant predicate should be impossible to write, not merely
discouraged by convention.

Soft-delete filtering (D-012) is applied here rather than at each call site,
so `deleted_at IS NULL` cannot be forgotten either.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base, TenantContext


class TenantRepository[ModelT: Base]:
    """CRUD scoped to a single workspace.

    Usage:
        repo = ProductRepository(session, tenant)
        await repo.get(product_id)
        await repo.list(limit=50)

    The tenant is fixed at construction. It cannot be changed mid-request,
    so a single repository instance cannot straddle two tenants.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession, tenant: TenantContext) -> None:
        if not isinstance(tenant, TenantContext) or tenant.workspace_id is None:
            raise ValueError(
                "A valid TenantContext is required. Queries without a tenant "
                "would be able to read across workspaces."
            )
        self._session = session
        self._tenant = tenant

    @property
    def workspace_id(self) -> UUID:
        return self._tenant.workspace_id

    def _scoped(self, statement: Select[Any]) -> Select[Any]:
        """Apply tenant scope, and soft-delete filter when applicable."""
        statement = statement.where(
            self.model.workspace_id == self._tenant.workspace_id
        )
        if hasattr(self.model, "deleted_at"):
            statement = statement.where(self.model.deleted_at.is_(None))
        return statement

    async def get(self, entity_id: UUID) -> ModelT | None:
        statement = self._scoped(select(self.model).where(self.model.id == entity_id))
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_many(self, entity_ids: Sequence[UUID]) -> Sequence[ModelT]:
        if not entity_ids:
            return []
        statement = self._scoped(
            select(self.model).where(self.model.id.in_(entity_ids))
        )
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any = None,
        where: Any = None,
    ) -> Sequence[ModelT]:
        statement = select(self.model)
        if where is not None:
            statement = statement.where(where)
        statement = self._scoped(statement)
        if order_by is not None:
            statement = statement.order_by(order_by)
        statement = statement.limit(limit).offset(offset)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def count(self, where: Any = None) -> int:
        statement = select(self.model)
        if where is not None:
            statement = statement.where(where)
        statement = self._scoped(statement)
        result = await self._session.execute(
            statement.with_only_columns(self.model.id).order_by(None).limit(None)
        )
        return len(result.all())

    async def exists(self, entity_id: UUID) -> bool:
        return await self.get(entity_id) is not None

    async def add(self, entity: ModelT) -> ModelT:
        """Insert. Stamps the tenant if not already set."""
        if hasattr(entity, "workspace_id"):
            existing = getattr(entity, "workspace_id", None)
            if existing is None:
                entity.workspace_id = self._tenant.workspace_id
            elif existing != self._tenant.workspace_id:
                raise ValueError(
                    "Refusing to insert a row whose workspace_id does not "
                    "match the request tenant. This would create a "
                    "cross-tenant reference."
                )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        """Soft delete when the model supports it, otherwise hard delete."""
        entity = await self.get(entity_id)
        if entity is None:
            return False

        if hasattr(entity, "deleted_at"):
            entity.deleted_at = _utcnow()
            await self._session.flush()
            return True

        await self._session.delete(entity)
        await self._session.flush()
        return True

    async def hard_delete(self, entity_id: UUID) -> bool:
        """Permanently remove a row. Bypasses soft delete.

        Intended for retention expiry only, never for user-facing deletes.
        """
        statement = (
            delete(self.model)
            .where(self.model.id == entity_id)
            .where(self.model.workspace_id == self._tenant.workspace_id)
        )
        result = await self._session.execute(statement)
        return bool(result.rowcount)

    async def update_fields(self, entity_id: UUID, **values: Any) -> ModelT | None:
        """Patch scalar fields. Raises if the row is outside this tenant."""
        entity = await self.get(entity_id)
        if entity is None:
            return None

        # A caller must never be able to reassign tenancy.
        values.pop("workspace_id", None)
        for key, value in values.items():
            if not hasattr(entity, key):
                raise AttributeError(f"{type(entity).__name__} has no field '{key}'")
            setattr(entity, key, value)

        await self._session.flush()
        return entity

    async def bulk_update(self, entity_ids: Sequence[UUID], **values: Any) -> int:
        if not entity_ids:
            return 0
        values.pop("workspace_id", None)
        values.pop("id", None)
        if not values:
            return 0
        statement = (
            update(self.model)
            .where(self.model.id.in_(entity_ids))
            .where(self.model.workspace_id == self._tenant.workspace_id)
            .values(**values)
        )
        result = await self._session.execute(statement)
        return int(result.rowcount or 0)


def _utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)