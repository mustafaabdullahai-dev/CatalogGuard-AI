"""Structured LLM client (Phase 14, §6, §35, §36, D-007).

This is D-007's validation layer, which that decision calls a constraint rather
than a preference:

1. Every model call declares a Pydantic schema and validates its output.
2. A validation failure starts a **bounded** repair loop — re-ask with the error
   attached — and never silent acceptance.
3. After the bounded repairs the call **fails loudly** and records the error. It
   does not fall through, and the graph stops rather than reasoning over a value
   nobody validated.
4. Schema-first prompting: the JSON Schema travels in the prompt. The provider
   is OpenAI-*compatible*, not OpenAI, and D-007 records that its provider-side
   schema enforcement is materially weaker — so the schema is ours to state and
   ours to check.
5. Token accounting includes repair rounds, because a repair re-spends the same
   input and cost control that ignores it under-reports the free tier's burn.

Model routing (§35) lives here too: a `FAST` tier for classification,
normalization and extraction, and a `REASONING` tier for reconciliation,
relationship analysis and risk explanation. Paying the reasoning model to read a
file name is the failure §35 exists to prevent.

The transport is LangChain's `ChatOpenAI` (§6 assigns model integration to
LangChain); everything the model says is parsed, validated and bounded here.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, TypeVar, runtime_checkable

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.agents.state.models import Usage
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

JSON_ONLY_INSTRUCTION = (
    "Respond with a single JSON object and nothing else: no prose, no markdown "
    "fences, no trailing commentary."
)


class ModelTier(StrEnum):
    """§35's routing categories. The tier picks the model, not the caller."""

    FAST = "fast"
    REASONING = "reasoning"


class StructuredOutputError(RuntimeError):
    """Output still did not validate after the bounded repair loop (D-007 #3).

    Carries the usage it burned, because a failed call still costs tokens and
    §56 cost accounting must not silently drop the expensive failures.
    """

    def __init__(
        self,
        *,
        schema_name: str,
        attempts: int,
        problems: Sequence[str],
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self.schema_name = schema_name
        self.attempts = attempts
        self.problems = list(problems)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        super().__init__(
            f"{schema_name} did not validate after {attempts} attempt(s): "
            f"{self.problems[-1] if self.problems else 'no detail'}"
        )


class Completion(BaseModel):
    """A raw model reply, before anything has validated it."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class Completer(Protocol):
    """Transport seam: one model request → text plus token counts.

    Kept separate from validation so tests can script a model's replies without
    a network, and so the validation loop — the part this codebase owns — is what
    gets exercised.
    """

    async def __call__(self, *, model: str, messages: Sequence[BaseMessage]) -> Completion: ...


class ChatModelCompleter:
    """LangChain `ChatOpenAI` transport (§6: model integration is LangChain's).

    It owns its own `httpx.AsyncClient` rather than accepting the library's
    default. That default is an `@lru_cache`d process-wide client, so the first
    event loop to use it becomes the only loop that may: a second run — a second
    test, a worker recycling a loop — then drives a transport bound to a dead one
    and fails with "Event loop is closed" on a request that never left. One
    client per completer, closed by `aclose`, has no such ownership to leak.
    """

    def __init__(self, clients: dict[str, ChatOpenAI], http_client: httpx.AsyncClient) -> None:
        self._clients = dict(clients)
        self._http_client = http_client

    async def __call__(self, *, model: str, messages: Sequence[BaseMessage]) -> Completion:
        client = self._clients.get(model)
        if client is None:
            raise ValueError(f"no chat client configured for model {model!r}")
        response = await client.ainvoke(list(messages))
        return _completion_from(response)

    async def aclose(self) -> None:
        """Release the transport. Await this when a completer is per-run."""
        for client in self._clients.values():
            await client.root_async_client.close()
        await self._http_client.aclose()


def _completion_from(message: AIMessage) -> Completion:
    """Extract text and usage from a LangChain message.

    `content` is a string for chat models today and a list of blocks for
    multimodal ones, so both are read rather than assuming the shape that happens
    to be current.
    """
    content = message.content
    if isinstance(content, str):
        text = content
    else:
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(str(block["text"]))
        text = "".join(parts)

    usage = message.usage_metadata
    return Completion(
        text=text,
        input_tokens=int(usage["input_tokens"]) if usage else 0,
        output_tokens=int(usage["output_tokens"]) if usage else 0,
    )


def _json_only(text: str) -> str:
    """The JSON object in a reply, ignoring any wrapping the model added.

    The prompt asks for JSON alone; a model that fences it anyway is normal, and
    rejecting the whole reply over a fence would spend a repair round teaching it
    something the parser already knows.
    """
    fenced = _FENCE_RE.search(text)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _validation_summary(exc: ValidationError, *, limit: int = 3) -> str:
    """A repair message the model can act on: the fields, not the whole dump."""
    messages = []
    for error in exc.errors()[:limit]:
        location = ".".join(str(part) for part in error["loc"])
        messages.append(f"{location}: {error['msg']}")
    remaining = len(exc.errors()) - limit
    if remaining > 0:
        messages.append(f"... and {remaining} more")
    return "; ".join(messages)


def _usage_line(input_tokens: int, output_tokens: int) -> str:
    """Token counts under a key that edge masking will not redact (§55, §56).

    `app.core.masking` redacts every key matching `*token*`, which is right for
    `access_token` and would also redact `input_tokens` — a count, not a
    credential — leaving cost accounting (§56) as two redacted fields. The
    numbers ride under `usage` instead, and the value-shape rule that catches
    real keys still applies to it.
    """
    return f"{input_tokens} in / {output_tokens} out"


def schema_first_prompt(schema: type[BaseModel], role: str) -> str:
    """D-007 #4: state the schema in the prompt rather than trusting the provider."""
    schema_json = json.dumps(schema.model_json_schema(), indent=2, sort_keys=True)
    return (
        f"{role.strip()}\n\n"
        f"{JSON_ONLY_INSTRUCTION}\n"
        "The JSON object must validate against this JSON Schema:\n"
        f"```json\n{schema_json}\n```"
    )


def repair_prompt(exc: ValidationError) -> str:
    """D-007 #2: re-ask with the validation error, bounded by config."""
    return (
        "That JSON did not validate against the schema. Fix only what the errors "
        "below require, keep every field the schema requires, and reply again "
        f"with one JSON object and nothing else.\n{_validation_summary(exc)}"
    )


class StructuredLLM(Protocol):
    """What the graph's nodes are written against: schema in, model out."""

    async def complete(
        self,
        *,
        tier: ModelTier,
        schema: type[T],
        system: str,
        user: str,
    ) -> tuple[T, Usage]: ...


class ChatCompletionClient:
    """D-007's validation layer over a LangChain chat model.

    `llm_max_concurrency` bounds in-flight calls (D-004: the free tier
    throttles), and `llm_schema_repair_attempts` bounds repairs. Neither is
    optional and neither is unbounded.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        completer: Completer | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._limiter = asyncio.Semaphore(self._settings.llm_max_concurrency)
        self._models: dict[ModelTier, str] = {
            ModelTier.FAST: self._settings.qroq_fast_model,
            ModelTier.REASONING: self._settings.qroq_reasoning_model,
        }
        if completer is not None:
            self._completer = completer
        else:
            http_client = httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds)
            self._completer = ChatModelCompleter(
                {
                    model: ChatOpenAI(
                        model=model,
                        api_key=self._settings.qroq_api_key,
                        base_url=self._settings.qroq_base_url,
                        temperature=self._settings.llm_temperature,
                        timeout=self._settings.llm_timeout_seconds,
                        max_retries=self._settings.llm_max_retries,
                        http_async_client=http_client,
                    )
                    for model in dict.fromkeys(self._models.values())
                },
                http_client,
            )

    async def aclose(self) -> None:
        """Release HTTP resources held by this client.

        A client created for one run — a test, a CLI invocation, a job that ends —
        must not outlive its event loop. A client that serves the process for its
        whole lifetime can simply be left alone.
        """
        if isinstance(self._completer, ChatModelCompleter):
            await self._completer.aclose()

    def model_for(self, tier: ModelTier) -> str:
        """The model id a tier routes to (§35)."""
        try:
            return self._models[tier]
        except KeyError as exc:
            raise ValueError(f"unknown model tier: {tier!r}") from exc

    async def complete(
        self,
        *,
        tier: ModelTier,
        schema: type[T],
        system: str,
        user: str,
    ) -> tuple[T, Usage]:
        """One validated object, or `StructuredOutputError` — never an unvalidated one."""
        model = self.model_for(tier)
        messages: list[BaseMessage] = [
            SystemMessage(content=schema_first_prompt(schema, system)),
            HumanMessage(content=user),
        ]

        input_tokens = 0
        output_tokens = 0
        problems: list[str] = []
        attempts = 0
        repairs_allowed = self._settings.llm_schema_repair_attempts

        async with self._limiter:
            for attempt in range(1, repairs_allowed + 2):  # first call + bounded repairs
                attempts = attempt
                response = await self._completer(model=model, messages=messages)
                input_tokens += response.input_tokens
                output_tokens += response.output_tokens
                try:
                    parsed = schema.model_validate_json(_json_only(response.text))
                except ValidationError as exc:
                    problems.append(_validation_summary(exc))
                    messages = [
                        *messages,
                        HumanMessage(content=repair_prompt(exc)),
                    ]
                    continue

                logger.info(
                    "llm_structured_output_ok",
                    tier=tier.value,
                    model=model,
                    schema=schema.__name__,
                    attempts=attempt,
                    repairs=attempt - 1,
                    usage=_usage_line(input_tokens, output_tokens),
                )
                return parsed, Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    calls=attempt,
                    repairs=max(0, attempt - 1),
                )

        error = StructuredOutputError(
            schema_name=schema.__name__,
            attempts=attempts,
            problems=problems,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        # Metadata only: prompts and completions can carry tenant catalog data,
        # which §55 keeps out of logs.
        logger.warning(
            "llm_structured_output_failed",
            tier=tier.value,
            model=model,
            schema=schema.__name__,
            attempts=attempts,
            usage=_usage_line(input_tokens, output_tokens),
        )
        raise error
