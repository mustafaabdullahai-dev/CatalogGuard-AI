"""Authorization and tenant-binding tests (D-003, D-004, D-011, D-014).

Two claims under test, both about the boundary between a session and a tenant:

  1. A session identifies a person. It grants nothing on its own.
  2. A workspace header is a hint, checked against the caller's memberships.
     It can never widen the caller's scope.

`get_current_tenant` is exercised directly rather than through HTTP, because the
dependency's entire contract is "which queries run, in which tenant" and the
mocked session captures exactly that: the statements it executes and the tenant
it was bound to.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.deps import (
    AuthContext,
    TenantScope,
    get_current_tenant,
    require_approval_authority,
    require_capability,
)
from app.db.base import TenantContext
from app.models.tenancy import RoleCapabilities, User, UserRole, WorkspaceMembership


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="ops@example.com",
        hashed_password="x",
        is_active=True,
    )


def _membership(workspace_id: uuid.UUID, role: UserRole) -> WorkspaceMembership:
    return WorkspaceMembership(workspace_id=workspace_id, user_id=uuid.uuid4(), role=role)


def _context(*memberships: WorkspaceMembership) -> AuthContext:
    return AuthContext(
        user=_user(),
        session=MagicMock(),
        memberships=tuple(memberships),
    )


def _db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=[])))
    return db


async def _tenant(
    context: AuthContext, workspace_id: str | None
) -> TenantScope:
    return await get_current_tenant(
        context=context, workspace_id=workspace_id, db=_db()
    )


class TestTenantResolution:
    async def test_membership_grants_a_tenant(self) -> None:
        workspace_id = uuid.uuid4()
        context = _context(_membership(workspace_id, UserRole.DATA_STEWARD))
        scope = await _tenant(context, str(workspace_id))
        assert scope.workspace_id == workspace_id
        assert scope.tenant.workspace_id == workspace_id
        assert scope.role == str(UserRole.DATA_STEWARD)

    async def test_tenant_context_carries_the_user_id(self) -> None:
        """Audit rows are attributed to a person, not just a tenant."""
        context = _context(_membership(uuid.uuid4(), UserRole.DATA_STEWARD))
        scope = await _tenant(context, str(scope_workspace(context)))
        assert scope.tenant.user_id == context.user.id

    async def test_foreign_workspace_is_forbidden(self) -> None:
        """A header naming someone else's workspace is a refusal.

        It is 403 rather than 404 on purpose: a 404 would confirm whether the
        workspace exists, which is itself information.
        """
        context = _context(_membership(uuid.uuid4(), UserRole.DATA_STEWARD))
        with pytest.raises(HTTPException) as excinfo:
            await _tenant(context, str(uuid.uuid4()))
        assert excinfo.value.status_code == 403

    async def test_missing_header_is_rejected(self) -> None:
        context = _context(_membership(uuid.uuid4(), UserRole.DATA_STEWARD))
        with pytest.raises(HTTPException) as excinfo:
            await _tenant(context, None)
        assert excinfo.value.status_code == 400

    async def test_malformed_header_is_rejected(self) -> None:
        context = _context(_membership(uuid.uuid4(), UserRole.DATA_STEWARD))
        with pytest.raises(HTTPException) as excinfo:
            await _tenant(context, "not-a-uuid")
        assert excinfo.value.status_code == 400

    async def test_no_memberships_means_no_tenant(self) -> None:
        """A valid user with no workspace has nothing to act on."""
        context = _context()
        with pytest.raises(HTTPException) as excinfo:
            await _tenant(context, str(uuid.uuid4()))
        assert excinfo.value.status_code == 403

    async def test_role_is_per_membership_not_per_user(self) -> None:
        """The same person is a steward in one workspace and a reader in another.

        Role is on the membership for exactly this reason.
        """
        steward_workspace = uuid.uuid4()
        viewer_workspace = uuid.uuid4()
        context = _context(
            _membership(steward_workspace, UserRole.DATA_STEWARD),
            _membership(viewer_workspace, UserRole.SEO_SPECIALIST),
        )
        as_steward = await _tenant(context, str(steward_workspace))
        as_specialist = await _tenant(context, str(viewer_workspace))
        assert as_steward.has("approve")
        assert not as_specialist.has("approve")


def scope_workspace(context: AuthContext) -> uuid.UUID:
    return context.memberships[0].workspace_id


class TestTenantBinding:
    async def test_rls_is_bound_before_the_handler_runs(self) -> None:
        """The dependency, not the handler, sets the tenant.

        A handler that forgot to bind would read nothing under RLS rather than
        everything, which is the safe direction to fail. Binding here means no
        handler has to remember.
        """
        workspace_id = uuid.uuid4()
        context = _context(_membership(workspace_id, UserRole.DATA_STEWARD))
        db = _db()
        await get_current_tenant(
            context=context, workspace_id=str(workspace_id), db=db
        )
        statement = db.execute.await_args_list[0].args[0]
        assert "set_config" in str(statement)
        params = statement.compile().params
        assert "app.current_workspace" in params.values()
        assert str(workspace_id) in params.values()

    async def test_binding_uses_a_transaction_local_setting(self) -> None:
        """`SET LOCAL` is what makes this safe under a connection pooler."""
        workspace_id = uuid.uuid4()
        context = _context(_membership(workspace_id, UserRole.DATA_STEWARD))
        db = _db()
        await get_current_tenant(
            context=context, workspace_id=str(workspace_id), db=db
        )
        statement = str(db.execute.await_args_list[0].args[0]).replace("\n", " ")
        assert "set_config(" in statement
        assert statement.rstrip().endswith("true)")


class TestCapabilityGuards:
    @pytest.mark.parametrize(
        ("role", "capability"),
        [
            (UserRole.ECOMMERCE_OPS_MANAGER, "approve"),
            (UserRole.ECOMMERCE_OPS_MANAGER, "configure_policy"),
            (UserRole.DATA_STEWARD, "approve"),
            (UserRole.DATA_STEWARD, "resolve_conflicts"),
            (UserRole.DATA_INTEGRATION_ADMIN, "manage_connectors"),
            (UserRole.MERCHANDISER, "write:financial"),
            (UserRole.SEO_SPECIALIST, "write:content"),
        ],
    )
    async def test_granted_capability_passes(self, role: UserRole, capability: str) -> None:
        context = _context(_membership(uuid.uuid4(), role))
        scope = await _tenant(context, str(scope_workspace(context)))
        guard = require_capability(capability)
        assert await guard(scope) is scope

    @pytest.mark.parametrize(
        ("role", "capability"),
        [
            # The separation D-004 exists for.
            (UserRole.DATA_INTEGRATION_ADMIN, "approve"),
            (UserRole.DATA_INTEGRATION_ADMIN, "admin"),
            (UserRole.MERCHANDISER, "approve"),
            (UserRole.SEO_SPECIALIST, "approve"),
            # `write` is not `admin`.
            (UserRole.DATA_STEWARD, "admin"),
            (UserRole.DATA_STEWARD, "configure_policy"),
        ],
    )
    async def test_missing_capability_is_forbidden(
        self, role: UserRole, capability: str
    ) -> None:
        context = _context(_membership(uuid.uuid4(), role))
        scope = await _tenant(context, str(scope_workspace(context)))
        guard = require_capability(capability)
        with pytest.raises(HTTPException) as excinfo:
            await guard(scope)
        assert excinfo.value.status_code == 403

    async def test_unknown_role_fails_closed(self) -> None:
        """An unrecognized role string grants nothing.

        Roles come from the database, so a value this build does not know is a
        data problem, not a licence.
        """
        workspace_id = uuid.uuid4()
        context = _context(_membership(workspace_id, UserRole.DATA_STEWARD))
        scope = await _tenant(context, str(workspace_id))
        scope = TenantScope(
            context=scope.context, tenant=scope.tenant, role="root"
        )
        assert not scope.has("approve")
        assert not scope.has("read")

    async def test_superuser_is_not_a_bypass(self) -> None:
        """`is_superuser` must not become capability escalation.

        The bootstrap account is created with it False for this reason. If a
        future change made superuser imply everything, the role matrix would stop
        being the thing that decides.
        """
        workspace_id = uuid.uuid4()
        user = _user()
        user.is_superuser = True
        context = AuthContext(
            user=user,
            session=MagicMock(),
            memberships=(_membership(workspace_id, UserRole.SEO_SPECIALIST),),
        )
        scope = await _tenant(context, str(workspace_id))
        with pytest.raises(HTTPException):
            await require_approval_authority()(scope)


class TestApprovalAuthorityIsPinned:
    """The capability matrix is shared; these pin the rule that matters most."""

    @pytest.mark.parametrize(
        "role",
        [UserRole.ECOMMERCE_OPS_MANAGER, UserRole.DATA_STEWARD],
    )
    def test_approvers(self, role: UserRole) -> None:
        assert RoleCapabilities.can_approve(role)

    @pytest.mark.parametrize(
        "role",
        [
            UserRole.DATA_INTEGRATION_ADMIN,
            UserRole.MERCHANDISER,
            UserRole.SEO_SPECIALIST,
        ],
    )
    def test_non_approvers(self, role: UserRole) -> None:
        assert not RoleCapabilities.can_approve(role)

    def test_no_role_has_admin_except_ops_manager(self) -> None:
        holders = [
            role
            for role in UserRole
            if "admin" in RoleCapabilities.capabilities_for(role)
        ]
        assert holders == [UserRole.ECOMMERCE_OPS_MANAGER]


class TestTenantContextConstruction:
    def test_tenant_context_rejects_a_missing_workspace(self) -> None:
        with pytest.raises(ValueError):
            TenantContext(workspace_id=None, user_id=uuid.uuid4())  # type: ignore[arg-type]

    def test_two_tenants_are_distinct(self) -> None:
        """Identity comparison is what stops a tenant being reused by mistake."""
        a = TenantContext(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
        b = TenantContext(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
        assert a != b
