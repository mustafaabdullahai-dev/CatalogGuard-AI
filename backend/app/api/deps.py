"""Request dependencies: who is calling, and on whose behalf.

Two layers, deliberately separate:

  - `get_auth_context` answers "who is this person". It touches only global
    tables, so it works before any workspace has been chosen.
  - `get_current_tenant` answers "which workspace, and can they act there". It
    requires a membership, and it binds the transaction for RLS (D-011).

Keeping them apart means an endpoint that legitimately does not need a tenant
(`/auth/me`, health, brand assets) does not have to invent one, and every
endpoint that does need one gets it from a dependency that cannot forget the
membership check.

The `X-Workspace-Id` header selects the workspace. It is a hint, not an
authority: it is validated against the user's memberships here, and RLS
enforces the same answer at the database. A header naming a workspace the user
does not belong to is a 403, never a wider scope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import TenantContext
from app.db.session import bind_tenant, get_session
from app.models.tenancy import AuthSession, RoleCapabilities, User, WorkspaceMembership
from app.services.auth import sessions as session_service

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Cookie"},
)
_FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Insufficient permissions",
)
_NO_TENANT = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="X-Workspace-Id header is required for this endpoint",
)


@dataclass(frozen=True, slots=True)
class AuthContext:
    """The authenticated caller, before any workspace is chosen."""

    user: User
    session: AuthSession
    memberships: tuple[WorkspaceMembership, ...]


@dataclass(frozen=True, slots=True)
class TenantScope:
    """A caller acting inside one workspace."""

    context: AuthContext
    tenant: TenantContext
    role: str

    @property
    def user(self) -> User:
        return self.context.user

    @property
    def workspace_id(self) -> UUID:
        return self.tenant.workspace_id

    def has(self, capability: str) -> bool:
        from app.models.tenancy import UserRole

        try:
            role = UserRole(self.role)
        except ValueError:
            # An unrecognized role has no capabilities. Failing closed on a bad
            # value is the point of the wrapper.
            return False
        return capability in RoleCapabilities.capabilities_for(role)


def _session_token(request: Request) -> str | None:
    return request.cookies.get(get_settings().session_cookie_name)


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Resolve the session cookie to a live session and its user.

    Every failure is the same 401: no cookie, unknown token, expired, revoked,
    disabled user. Distinguishing them would turn the endpoint into an oracle.
    """
    from sqlalchemy import select

    token = _session_token(request)
    if not token:
        raise _UNAUTHENTICATED

    record = await session_service.resolve_session(db, token)
    if record is None:
        raise _UNAUTHENTICATED

    result = await db.execute(
        select(User)
        .where(User.id == record.user_id)
        .select_from(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(User.is_active.is_(True), User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise _UNAUTHENTICATED

    memberships = await db.execute(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
    )
    await session_service.touch_session(record)
    return AuthContext(
        user=user,
        session=record,
        memberships=tuple(memberships.scalars()),
    )


async def get_current_user(
    context: AuthContext = Depends(get_auth_context),
) -> User:
    return context.user


async def get_current_tenant(
    context: AuthContext = Depends(get_auth_context),
    workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
    db: AsyncSession = Depends(get_session),
) -> TenantScope:
    """Resolve the workspace header to a membership and bind RLS for it."""
    if not workspace_id:
        raise _NO_TENANT
    try:
        requested = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Workspace-Id must be a UUID",
        ) from exc

    membership = next(
        (m for m in context.memberships if m.workspace_id == requested), None
    )
    if membership is None:
        # Not a 404. A 404 would confirm the workspace does not exist, which is
        # itself information a user without access should not have.
        raise _FORBIDDEN

    await bind_tenant(db, membership.workspace_id)
    return TenantScope(
        context=context,
        tenant=TenantContext(
            workspace_id=membership.workspace_id,
            organization_id=None,
            user_id=context.user.id,
        ),
        role=str(membership.role),
    )


def require_capability(
    capability: str,
) -> Callable[[TenantScope], Awaitable[TenantScope]]:
    """Dependency factory: this endpoint needs `capability` in the caller's role.

    Built on `RoleCapabilities`, so the answer lives in one matrix (D-003, D-004)
    rather than being restated per route. `admin` is deliberately *not* implied
    by `write` or by `approve`.
    """

    async def dependency(scope: TenantScope = Depends(get_current_tenant)) -> TenantScope:
        if not scope.has(capability):
            raise _FORBIDDEN
        return scope

    return dependency


def require_approval_authority() -> Callable[[TenantScope], Awaitable[TenantScope]]:
    """This endpoint approves changes to catalog data.

    A named dependency rather than `require_capability("approve")` because
    approval is the one capability where the separation in D-003/D-004 matters:
    the integration role holds credential custody and must never acquire it. The
    dedicated name makes an audit of "what can approve" a single grep, and gives
    the test suite one place to pin the rule.
    """
    return require_capability("approve")


def require_workspace_admin() -> Callable[[TenantScope], Awaitable[TenantScope]]:
    """This endpoint administers a workspace: members, roles, configuration."""
    return require_capability("admin")


def require_ingestion_authority() -> Callable[[TenantScope], Awaitable[TenantScope]]:
    """This endpoint ingests raw catalog data (Phase 10, §49).

    Resolves to the `write` capability today: uploading a supplier file creates
    `source_records`, which is catalog mutation and belongs to whoever may write
    catalog data.

    OPEN DECISION. An alternative reading is that ingestion is an integration
    activity and should require `configure_sources`, which only the data
    integration admin (D-004) holds -- but that role deliberately cannot write,
    so gating on it would let the integration admin ingest data they are barred
    from approving while barring the ops manager from their own feed. Named as a
    single dependency so the answer is a one-line change and one grep, not a
    policy scattered across handlers.
    """
    return require_capability("write")


def require_reconciliation_authority() -> Callable[[TenantScope], Awaitable[TenantScope]]:
    """This endpoint runs or reads a reconciliation pass (Phase 12).

    Gated on `write`, not `resolve_conflicts`. Running a pass *creates* conflicts
    and proposals — it is analysis that writes rows, and both the ops manager and
    the data steward hold `write` (D-003). `resolve_conflicts` is reserved for
    the act of *deciding* a conflict (Phase 18), which the integration admin must
    never be able to do; keeping the two separate is what makes that distinction
    enforceable rather than merely intended.
    """
    return require_capability("write")


def require_compliance_authority() -> Callable[[TenantScope], Awaitable[TenantScope]]:
    """This endpoint reads or writes the compliance corpus (Phase 13, §13, §14).

    Decided in D-021: `write`, on all three routes.

    The OPEN DECISION was `write` versus `configure_sources`, and it came down
    to what the corpus *is*. §14 lists internal catalog policies, business rules
    and historical catalog decisions alongside marketplace documentation, so
    authorship spans text the tenant wrote itself and text it copied from a
    marketplace. `configure_sources` belongs to the data integration admin
    (D-004), who cannot write catalog data at all -- gating authorship there
    would make an internal policy unwritable by anyone who actually owns
    policy.

    The separation-of-duties concern behind that alternative -- that a role
    which can approve could author the evidence an approval cites -- is
    recorded rather than dismissed. It is bounded by D-005 (no rule-cited
    compliance decision in the MVP, so these documents supply evidence a human
    weighs) and by D-013's append-only audit log, which records who added every
    document. If that proves insufficient once approvals start citing the
    corpus, this is one line plus one grep.

    Reads are gated on `write` rather than `read` for the reason on
    `require_catalog_authority`: one gate per router. The cost is named rather
    than hidden -- the SEO specialist and the integration admin hold `read` and
    cannot search the corpus, even though §13's citations are exactly the sort
    of evidence those roles act on. Splitting retrieval out is the same
    one-line change.
    """
    return require_capability("write")


def require_catalog_authority() -> Callable[[TenantScope], Awaitable[TenantScope]]:
    """This endpoint reads or builds the canonical catalog (Phase 11).

    Reads are gated by `write` here even though a read normally needs only
    `read`, because the one mutating route on this router
    (`/catalog-sources/{id}/canonicalize`) creates products. Splitting read and
    write into two dependencies for four routes would add a name without adding
    a decision; if product reads ever need a separate grant, `read` is the change
    and this is the single place to make it.
    """
    return require_capability("write")
