"""Embedding port (§14, D-006, D-010).

The seam between "does this deployment index semantically?" and "does anything
use a model?". Retrieval works either way, so an unconfigured deployment still
returns rules — it just returns them on keyword and metadata evidence only.

The default adapter produces **no vectors rather than fake ones**. A hash-derived
pseudo-embedding would be a number-shaped lie: it is stable, it is fast, it
orders chunks, and it has nothing to do with meaning. Every existing port in this
codebase (mail, file scanning) follows the same rule — the adapter states what it
did not do instead of claiming success (§67).

Cost accounting (§56) lives with the provider, not here: `llm_usage` is written by
the caller that knows which run asked for the tokens.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Fixed by `compliance_chunks.embedding` (Vector(1536)); changing the model
# dimension means a migration, so it is a constant rather than a setting.
EMBEDDING_DIMENSION = 1536


class EmbeddingUnavailableError(RuntimeError):
    """Raised when a caller asks for vectors without a configured provider."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors. Implementations must be honest about availability.

    The attributes are declared as read-only properties so the frozen dataclass
    and the plain-class provider both satisfy the protocol. As settable
    attributes they would only match the latter.
    """

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def available(self) -> bool: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input, in order. Raises if unavailable."""
        ...


@dataclass(frozen=True, slots=True)
class NullEmbedder:
    """No embedding service configured (`RAG_EMBEDDING_MODE=none`).

    `available=False` is the contract callers check; `embed` still raises rather
    than returning `[]`, so a caller that forgets the check fails loudly instead
    of silently storing rows that look indexed and never will be.
    """

    name: str = "none"
    model: str = "none"
    dimension: int = EMBEDDING_DIMENSION
    available: bool = False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        del texts  # Accepted by the protocol, never used: this provider has none.
        raise EmbeddingUnavailableError(
            "no embedding provider is configured; set RAG_EMBEDDING_MODE=qroq "
            "and confirm pgvector is enabled before indexing semantically"
        )


class QroqEmbeddingProvider:
    """OpenAI-compatible `/embeddings` client (D-007).

    NOT VERIFIED. It has never been called: the API key is still a placeholder
    and pgvector has not been confirmed on Neon. Treat every vector produced here
    as unproven until that path runs once against a live endpoint.

    Failure is not swallowed — a request that errors raises, because a chunk
    stored without a vector it was supposed to have is indistinguishable from one
    that was never indexed.
    """

    name = "qroq"
    dimension = EMBEDDING_DIMENSION
    available = True

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: int) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._url,
                headers=self._headers,
                json={"model": self.model, "input": list(texts)},
            )
            response.raise_for_status()
            payload = response.json()

        data = sorted(payload["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in data]
        if len(vectors) != len(texts):
            raise EmbeddingUnavailableError(
                f"provider returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors


def get_embedder() -> EmbeddingProvider:
    """The configured embedder.

    Raising on an unknown mode rather than falling back: a deployment that
    believes it is indexing semantically while silently skipping it is the exact
    failure this port exists to prevent.
    """
    settings = get_settings()
    if settings.rag_embedding_mode == "none":
        return NullEmbedder()
    if settings.rag_embedding_mode == "qroq":
        return QroqEmbeddingProvider(
            api_key=settings.qroq_api_key,
            base_url=settings.qroq_base_url,
            model=settings.qroq_embedding_model,
            timeout=settings.llm_timeout_seconds,
        )
    raise ValueError(f"unsupported RAG_EMBEDDING_MODE: {settings.rag_embedding_mode}")


async def embed_if_available(
    provider: EmbeddingProvider, texts: Sequence[str]
) -> list[list[float]] | None:
    """Vectors for `texts`, or None when no provider can produce them.

    The only caller-facing contract: `None` means "store unindexed and say so",
    never "pretend it worked".
    """
    if not provider.available:
        logger.info(
            "embedding_skipped",
            provider=provider.name,
            count=len(texts),
            reason="no embedding provider configured",
        )
        return None
    return await provider.embed(texts)
