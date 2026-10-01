"""Authorization matrix tests (D-003, D-004).

These encode a deliberate security decision: credential custody is separate
from approval authority. The integration role can manage marketplace
connections and sources but can never approve a change. If someone "helpfully"
grants it `approve` because operations were complaining about throughput, these
tests should fail and force an explicit conversation.
"""

from __future__ import annotations

import pytest

from app.models.tenancy import RoleCapabilities, UserRole

ALL_GROUPS = set(RoleCapabilities.ATTRIBUTE_GROUPS)


class TestApprovalAuthority:
    @pytest.mark.parametrize(
        "role",
        [UserRole.ECOMMERCE_OPS_MANAGER, UserRole.DATA_STEWARD],
    )
    def test_authorized_roles_can_approve(self, role: UserRole) -> None:
        assert RoleCapabilities.can_approve(role)

    @pytest.mark.parametrize(
        "role",
        [
            UserRole.DATA_INTEGRATION_ADMIN,
            UserRole.MERCHANDISER,
            UserRole.SEO_SPECIALIST,
        ],
    )
    def test_others_cannot_approve(self, role: UserRole) -> None:
        assert not RoleCapabilities.can_approve(role)

    def test_integration_admin_is_separated_from_approval(self) -> None:
        """The whole point of the role split.

        This role holds marketplace credentials. Were it also able to approve
        changes, a single compromised integration account could both inject
        data and ratify it.
        """
        caps = RoleCapabilities.capabilities_for(UserRole.DATA_INTEGRATION_ADMIN)
        assert "manage_connectors" in caps
        assert "approve" not in caps
        assert "approve" not in str(caps)


class TestAttributeScopedWrite:
    def test_merchandiser_is_scoped_to_financial_and_inventory(self) -> None:
        groups = RoleCapabilities.writable_groups(UserRole.MERCHANDISER)
        assert groups == frozenset({"financial", "inventory"})

    def test_seo_specialist_cannot_touch_identity(self) -> None:
        """Titles are content. GTIN and SKU are identity and out of reach."""
        groups = RoleCapabilities.writable_groups(UserRole.SEO_SPECIALIST)
        assert groups == frozenset({"content"})
        assert not RoleCapabilities.can_write_group(UserRole.SEO_SPECIALIST, "identity")

    def test_seo_specialist_cannot_touch_relationships(self) -> None:
        assert not RoleCapabilities.can_write_group(
            UserRole.SEO_SPECIALIST, "relationship"
        )

    def test_full_write_grants_every_group(self) -> None:
        groups = RoleCapabilities.writable_groups(UserRole.ECOMMERCE_OPS_MANAGER)
        assert groups == ALL_GROUPS

    def test_unknown_group_grants_nothing(self) -> None:
        assert RoleCapabilities.writable_groups(UserRole.DATA_INTEGRATION_ADMIN) == (
            frozenset()
        )


class TestRoleMatrixIntegrity:
    def test_every_role_has_a_capability_set(self) -> None:
        for role in UserRole:
            assert RoleCapabilities.capabilities_for(role), role

    def test_no_unknown_group_names_leak(self) -> None:
        """A typo'd group name would silently grant nothing or everything."""
        for role in UserRole:
            groups = RoleCapabilities.writable_groups(role)
            assert groups <= ALL_GROUPS, f"{role.value} declares unknown group"

    def test_unknown_role_gets_no_capabilities(self) -> None:
        # A role absent from the matrix must deny rather than fall open.
        assert RoleCapabilities.capabilities_for("not_a_role") == frozenset()  # type: ignore[arg-type]

    def test_every_role_can_read(self) -> None:
        """No role is write-only; the UI needs read access to render."""
        for role in UserRole:
            assert "read" in RoleCapabilities.capabilities_for(role), role.value