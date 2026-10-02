"""Read the outbound mail outbox.

    python -m app.cli.outbox --latest
    python -m app.cli.outbox --to ops@example.com

Exists because there is no mail provider yet (D-017). A password reset or an
invitation is queued rather than sent, and the D-010 masker redacts tokens in
logs, so this is the only way to see the link while testing the flows offline.

Refuses to run in production. Here the rows contain live bearer secrets; a
terminal in a deploy environment is not the place to print them, and the check
costs nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import manual_session
from app.models.tenancy import EmailOutbox

logger = get_logger(__name__)


async def show(*, recipient: str | None, limit: int) -> list[EmailOutbox]:
    statement = select(EmailOutbox).order_by(EmailOutbox.created_at.desc()).limit(limit)
    if recipient:
        statement = (
            select(EmailOutbox)
            .where(EmailOutbox.recipient == recipient.strip().lower())
            .order_by(EmailOutbox.created_at.desc())
            .limit(limit)
        )
    async with manual_session() as db:
        return list((await db.execute(statement)).scalars())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.outbox",
        description="Show queued outbound messages, including their links.",
    )
    parser.add_argument("--to", dest="recipient", default=None, help="Filter by recipient")
    parser.add_argument(
        "--latest", action="store_true", help="Show only the newest message"
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="How many messages to show (default 10)"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.is_production:
        print(
            "refusing to print live tokens in production; "
            "use `python -m app.cli.outbox` against a development database",
            file=sys.stderr,
        )
        return 1

    configure_logging()
    rows = asyncio.run(
        show(recipient=args.recipient, limit=1 if args.latest else args.limit)
    )
    if not rows:
        print("outbox is empty")
        return 0
    for row in rows:
        status = "sent" if row.sent_at is not None else "queued (never sent)"
        print("-" * 72)
        print(f"to:      {row.recipient}")
        print(f"subject: {row.subject}")
        print(f"status:  {status} via {row.delivery_channel}")
        print(f"when:    {row.created_at.isoformat()}")
        print()
        print(row.body_text.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
