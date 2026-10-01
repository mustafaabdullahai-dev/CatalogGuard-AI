"""Tenancy, identity, and access control models.

Organization -> Workspace -> Users -> Catalogs -> Marketplace Connections.

Role definitions follow D-003 and D-004. The separation between the
integration role and the approval roles is deliberate: credential custody
must not imply authority to approve changes.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    SoftDeletableEntity,
    TenantEntity,
    new_uuid,
    str_enum,
)


class UserRole(enum.StrEnum):
    """Application roles (D-003, D-004)."""

    ECOMMERCE_OPS_MANAGER = "ecommerce_ops_manager"
    DATA_STEWARD = "data_steward"
    DATA_INTEGRATION_ADMIN = "data_integration_admin"
    MERCHANDISER = "merchandiser"
    SEO_SPECIALIST = "seo_specialist"


class RoleCapabilities:
    """Capability matrix. Single source of truth for authorization.

    Attribute groups let write access be scoped without enumerating every
    field: the merchandiser owns `financial` and `inventory`, the SEO
    specialist owns `content` and nothing else.
    """

    ATTRIBUTE_GROUPS = frozenset(
        {"identity", "content", "financial", "inventory", "relationship", "compliance"}
    )

    _ROLE_CAPABILITIES: dict[UserRole, frozenset[str]] = {
        UserRole.ECOMMERCE_OPS_MANAGER: frozenset(
            {"read", "write", "approve", "configure_policy", "admin"}
        ),
        UserRole.DATA_STEWARD: frozenset(
            {"read", "write", "approve", "resolve_conflicts"}
        ),
        # Explicitly cannot approve: holds credential custody, so approval
        # rights here would mean one compromised account can change listings.
        UserRole.DATA_INTEGRATION_ADMIN: frozenset(
            {"read", "configure_sources", "manage_connectors"}
        ),
        UserRole.MERCHANDISER: frozenset(
            {"read", "write:financial", "write:inventory"}
        ),
        UserRole.SEO_SPECIALIST: frozenset({"read", "write:content"}),
    }

    @classmethod
    def capabilities_for(cls, role: UserRole) -> frozenset[str]:
        return cls._ROLE_CAPABILITIES.get(role, frozenset())

    @classmethod
    def can_approve(cls, role: UserRole) -> bool:
        return "approve" in cls.capabilities_for(role)

    @classmethod
    def writable_groups(cls, role: UserRole) -> frozenset[str]:
        """Attribute groups this role may write.

        A bare `write` capability grants every group; a scoped
        `write:<group>` grants only that group. Unknown groups yield nothing.
        """
        caps = cls.capabilities_for(role)
        if "write" in caps:
            return cls.ATTRIBUTE_GROUPS
        return frozenset(
            cap.split(":", 1)[1]
            for cap in caps
            if cap.startswith("write:")
            and cap.split(":", 1)[1] in cls.ATTRIBUTE_GROUPS
        )

    @classmethod
    def can_write_group(cls, role: UserRole, group: str) -> bool:
        return group in cls.writable_groups(role)


class Organization(Base):
    """Billing and top-level isolation boundary. Not tenant-scoped itself."""

    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = ({"comment": "Top-level customer boundary"},)

    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Workspace(SoftDeletableEntity):
    """Primary tenant boundary.

    Regional operation is the reason workspaces exist as distinct rows rather
    than as a flag: a single organization may run separate marketplaces and
    credential sets per region.
    """

    __tablename__ = "workspaces"
    # Note: no `workspace_id`. This row is the tenant, so `id` is the tenant
    # id every other table points at.

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    # D-002 precedence matrix is tenant configuration, so it lives per workspace.
    source_authority_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    # D-002 risk thresholds. Business policy, therefore per workspace.
    risk_config: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="workspaces")

    __table_args__ = (
        Index("uq_workspaces_org_slug", "organization_id", "slug", unique=True),
        {"comment": "Primary tenant boundary for all catalog data"},
    )


class User(Base):
    """User record.

    Global rather than tenant-scoped: a person may belong to several
    workspaces, and their identity must be consistent across them.
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class WorkspaceMembership(TenantEntity):
    """Role assignment. A user holds one role per workspace.

    Role is per-membership rather than global because the same person can be a
    steward in one workspace and read-only in another.
    """

    __tablename__ = "workspace_memberships"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, name="user_role", length=64), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (
        Index("uq_memberships_workspace_user", "workspace_id", "user_id", unique=True),
        {"comment": "Per-workspace role assignment"},
    )

class AuthSession(Base):
    """A server-side login session (D-014).

    Global rather than tenant-scoped: a session outlives any single workspace,
    and it must be revocable without reference to one. The `users` table is
    global for the same reason.

    The cookie carries an opaque token; only its SHA-256 digest is stored. A
    database disclosure therefore does not yield usable session cookies, and a
    token can be revoked by deleting one row.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # hex digest of the cookie token, never the token itself.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Rotated on privilege change: a password reset or a revoked role must not
    # leave existing cookies working.
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Recorded for the security log: a session that appears from a new network
    # or user agent is worth noticing.
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship()

    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        # The only query that matters in the hot path: resolve a token.
        Index("ix_auth_sessions_expires_at", "expires_at"),
        {"comment": "Server-side login sessions; stores token digests only"},
    )


class LoginThrottle(Base):
    """Failed-login counter (D-014).

    Two scopes, one table, distinguished by `scope`:

      - `email` counts attempts against one account, defeating guessing.
      - `ip` counts attempts from one address across accounts, defeating a spray.

    Global, not tenant-scoped: a throttle must apply to someone who has not yet
    established which workspace they belong to.
    """

    __tablename__ = "login_throttles"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # Lower-cased email or client IP. Stored hashed for the email scope, since
    # an address is personal data with no reason to sit in plaintext here.
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_login_throttles_scope_key"),
        {"comment": "Failed-login counters for account and IP scopes"},
    )
