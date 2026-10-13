"""Bootstrap the first organization, workspace, and administrator.

Run by hand, by someone who already has database access:

    python -m app.cli.bootstrap --email you@example.com --password ...

There is no HTTP route for this, and that is the design. Creating identities is
the one operation where "who is asking?" has no answer yet, so it stays a
command a stranger cannot send.

Idempotent per email: re-running reuses the existing user, organization, and
workspace rather than creating a second admin. That matters because the first
boot is usually done twice — once to find out the right slug, once for real.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import re
import sys

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security.passwords import PasswordPolicyError, hash_password
from app.db.session import manual_session
from app.models.tenancy import Organization, User, UserRole, Workspace, WorkspaceMembership

logger = get_logger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a slug from {value!r}")
    return slug[:63]


async def bootstrap(
    *,
    email: str,
    password: str,
    organization_name: str,
    workspace_name: str,
    full_name: str | None = None,
) -> dict[str, str]:
    """Create or reuse the organization, workspace, user, and membership."""
    normalized = email.strip().lower()
    org_slug = slugify(organization_name)
    ws_slug = slugify(workspace_name)

    async with manual_session() as db:
        user = await db.scalar(select(User).where(User.email == normalized))
        created_user = user is None
        if user is None:
            user = User(
                email=normalized,
                hashed_password=hash_password(password),
                full_name=full_name,
                # Deliberately False. Superuser bypasses the role matrix, and a
                # superuser flag on the very first account would mean the
                # separation in D-003/D-004 never gets exercised.
                is_superuser=False,
                is_active=True,
            )
            db.add(user)
            await db.flush()

        org = await db.scalar(
            select(Organization).where(Organization.slug == org_slug)
        )
        if org is None:
            org = Organization(name=organization_name, slug=org_slug)
            db.add(org)
            await db.flush()

        workspace = await db.scalar(
            select(Workspace).where(Workspace.slug == ws_slug)
        )
        if workspace is None:
            workspace = Workspace(
                organization_id=org.id,
                name=workspace_name,
                slug=ws_slug,
            )
            db.add(workspace)
            await db.flush()

        membership = await db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.user_id == user.id,
            )
        )
        if membership is None:
            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=UserRole.ECOMMERCE_OPS_MANAGER,
            )
            db.add(membership)
            await db.flush()

        try:
            await db.commit()
        except IntegrityError:
            # Two bootstraps ran concurrently. The unique constraints held, which
            # is the correct outcome; report it rather than a traceback.
            await db.rollback()
            raise SystemExit(
                "Bootstrap lost a race against another run. Re-run to see the "
                "result."
            ) from None

        return {
            "user_id": str(user.id),
            "user_created": str(created_user).lower(),
            "organization_id": str(org.id),
            "organization_slug": org.slug,
            "workspace_id": str(workspace.id),
            "workspace_slug": workspace.slug,
            "role": str(membership.role),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.bootstrap",
        description="Create the first organization, workspace, and administrator.",
    )
    parser.add_argument("--email", required=True, help="Administrator email address")
    parser.add_argument(
        "--password",
        help="Administrator password. Omitted means prompt without echo, so it "
        "never appears in shell history or in `ps`.",
    )
    parser.add_argument("--organization", default="CatalogGuard")
    parser.add_argument("--workspace", default="Default Workspace")
    parser.add_argument("--full-name", default=None)
    args = parser.parse_args(argv)

    # `argparse` has no `prompt=`: `prompt`, `hide_input` and
    # `confirmation_prompt` are click's spelling, and passing them raised
    # `TypeError: unexpected keyword argument 'prompt'` the moment this module
    # was run. The first administrator could therefore never have been created
    # by the documented command — found in Phase 25 while standing up a
    # database to drive a browser against.
    password = args.password or getpass.getpass("Administrator password: ")
    if not args.password:
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("passwords did not match", file=sys.stderr)
            return 2

    configure_logging()
    get_settings()  # fail fast on a placeholder secret before touching the DB
    try:
        result = asyncio.run(
            bootstrap(
                email=args.email,
                password=password,
                organization_name=args.organization,
                workspace_name=args.workspace,
                full_name=args.full_name,
            )
        )
    except PasswordPolicyError as exc:
        print(f"password rejected: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary, message is the output
        print(f"bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    for key, value in result.items():
        print(f"{key}: {value}")
    if result["user_created"] == "true":
        logger.info("bootstrap_created_user", email=args.email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
