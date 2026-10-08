"""Per-caller request budget for the copilot (§33's rate limiting).

**Per process, and documented as such.** A single worker can enforce this; several
workers cannot, because the counters live in memory. A shared limit belongs on the
Redis the deployment already has (D-018's Dramatiq broker), and that wiring is not
built: the Redis credentials are still the originals, which are shared in
plaintext and await rotation (see the root README's blockers). Writing a
`INCR`-per-minute counter against those credentials would be a rate limiter that
looks load-bearing and is not.

What *is* enforced here, and enforced deterministically:

- `copilot_requests_per_minute` per (workspace, user) in this process.
- the question length cap and the history cap, in `ask`.

A limiter injected for a test is the same interface, so the rule is asserted
without sleeping a minute.
"""

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from typing import Protocol
from uuid import UUID

from app.core.config import Settings, get_settings


class CopilotRateLimitError(RuntimeError):
    """The caller has asked more often than the budget allows."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("copilot request rate exceeded")
        self.retry_after_seconds = retry_after_seconds


class RateLimiter(Protocol):
    def check(self, workspace_id: UUID, user_id: UUID) -> None: ...


class InProcessRateLimiter:
    """A sliding window over the last minute, per (workspace, user).

    Deques are pruned on access and the map is small by construction: one entry
    per caller who asked in the last minute, dropped when it empties.
    """

    def __init__(self, *, limit: int, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[tuple[UUID, UUID], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, workspace_id: UUID, user_id: UUID) -> None:
        now = monotonic()
        key = (workspace_id, user_id)
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                wait = max(1, int(self._window - (now - hits[0])) + 1)
                raise CopilotRateLimitError(wait)
            hits.append(now)
            if not hits:
                del self._hits[key]

    def forget(self, workspace_id: UUID, user_id: UUID) -> None:
        with self._lock:
            self._hits.pop((workspace_id, user_id), None)


_LIMITERS: dict[UUID, InProcessRateLimiter] = {}


def limiter_for(
    workspace_id: UUID,
    *,
    settings: Settings | None = None,
) -> InProcessRateLimiter:
    """One limiter per workspace, cached.

    Per workspace rather than global because a tenant's traffic must not consume
    another's budget; the window itself is per (workspace, user), so one person
    looping cannot exhaust their colleagues' allowance.
    """
    config = settings or get_settings()
    limiter = _LIMITERS.get(workspace_id)
    if limiter is None:
        limiter = InProcessRateLimiter(limit=config.copilot_requests_per_minute)
        _LIMITERS[workspace_id] = limiter
    return limiter


def reset_limiters() -> None:
    """Test hook: drop every window. Not a cache-clear for production use."""
    _LIMITERS.clear()