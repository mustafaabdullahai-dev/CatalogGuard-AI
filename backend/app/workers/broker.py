"""Dramatiq broker (D-008).

One Redis broker, built lazily. Nothing connects at import: constructing a
`RedisBroker` opens no socket, and `get_broker` is called only on the path that
actually enqueues work. `inline` ingestion -- the default -- therefore never
touches Redis.
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import get_settings

_broker: RedisBroker | None = None


def get_broker() -> RedisBroker:
    global _broker
    if _broker is None:
        # dramatiq ships no type information; the constructor is untyped.
        broker = RedisBroker(url=get_settings().redis_url)  # type: ignore[no-untyped-call]
        dramatiq.set_broker(broker)
        _broker = broker
    return _broker
