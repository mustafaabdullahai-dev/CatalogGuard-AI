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
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Protocol, TypeVar, runtime_checkable

import httpx
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages import (
    ToolCall as LangChainToolCall,
)
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from app.agents.parsing import json_only, validation_summary
from app.agents.state.models import Usage
from app.agents.tools import (
    TOOL_OUTPUT_POLICY,
    ToolCall,
    ToolContext,
    ToolRegistry,
    wrap_tool_result,
)
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

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
    tool_calls: list[ToolCall] = Field(default_factory=list)

    def as_message(self) -> AIMessage:
        """The reply as the API must see it when it asked for tools.

        Tool results answer a *specific* assistant turn, so that turn has to go
        back with them — and it has to carry the call ids the model minted, or
        the provider rejects the whole exchange as unmatched.
        """
        return AIMessage(
            content=self.text,
            tool_calls=[
                LangChainToolCall(
                    name=call.name,
                    args=call.arguments,
                    id=call.id,
                    type="tool_call",
                )
                for call in self.tool_calls
            ],
        )


@runtime_checkable
class Completer(Protocol):
    """Transport seam: one model request → text plus token counts.

    Kept separate from validation so tests can script a model's replies without
    a network, and so the validation loop — the part this codebase owns — is what
    gets exercised.
    """

    async def __call__(
        self,
        *,
        model: str,
        messages: Sequence[BaseMessage],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> Completion: ...


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

    async def __call__(
        self,
        *,
        model: str,
        messages: Sequence[BaseMessage],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> Completion:
        client = self._clients.get(model)
        if client is None:
            raise ValueError(f"no chat client configured for model {model!r}")
        request = client.bind_tools(list(tools)) if tools else client
        response = await request.ainvoke(list(messages))
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

    tool_calls = [
        ToolCall(
            id=str(entry.get("id") or f"call_{index}"),
            name=str(entry.get("name") or ""),
            arguments=_arguments(entry.get("args")),
        )
        for index, entry in enumerate(message.tool_calls or [])
    ]

    usage = message.usage_metadata
    return Completion(
        text=text,
        input_tokens=int(usage["input_tokens"]) if usage else 0,
        output_tokens=int(usage["output_tokens"]) if usage else 0,
        tool_calls=tool_calls,
    )


def _arguments(raw: object) -> dict[str, Any]:
    """Tool arguments as a dict, whatever shape they arrived in.

    A model that emits malformed argument JSON still gets an answer — its call
    fails argument validation and it is told why, which is more useful than an
    exception that reaches the graph as if the tool were broken. The raw text is
    kept under `_unparsed` precisely so that *can* happen: every args model
    forbids unknown keys, so a payload nobody can read cannot silently validate
    against a field that happens to have a default.
    """
    if isinstance(raw, dict):
        return dict(raw)
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {"_unparsed": text}
    if isinstance(parsed, dict):
        return parsed
    return {"_unparsed": text}


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
        f"with one JSON object and nothing else.\n{validation_summary(exc)}"
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
        tools: ToolRegistry | None = None,
        tool_context: ToolContext | None = None,
    ) -> tuple[T, Usage]:
        """One validated object, or `StructuredOutputError` — never an unvalidated one.

        With tools, each attempt lets the model ask for them first, bounded by
        `llm_max_tool_rounds`, before it must answer. The two bounds are one
        budget: a model looping on tools spends exactly like a model looping on
        invalid JSON, and D-007 does not distinguish them.
        """
        if (tools is None) != (tool_context is None):
            raise ValueError("tools and tool_context must be supplied together")

        model = self.model_for(tier)
        tool_setup: tuple[ToolRegistry, ToolContext] | None = None
        tool_schemas: list[dict[str, Any]] | None = None
        system_prompt = schema_first_prompt(schema, system)
        if tools is not None and tool_context is not None:
            tool_setup = (tools, tool_context)
            tool_schemas = tools.schemas()
            system_prompt += (
                "\n\n" + TOOL_OUTPUT_POLICY + "\n\nAvailable tools:\n" + tools.describe()
            )
        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user),
        ]

        input_tokens = 0
        output_tokens = 0
        model_calls = 0
        tool_rounds = 0
        attempts = 0
        problems: list[str] = []
        repairs_allowed = self._settings.llm_schema_repair_attempts
        max_tool_rounds = self._settings.llm_max_tool_rounds

        async with self._limiter:
            for attempt in range(1, repairs_allowed + 2):  # first call + bounded repairs
                attempts = attempt
                reply_text: str | None = None

                # One answer attempt plus `max_tool_rounds` chances to ask for
                # tools. If the loop ends without an answer, the model spent
                # every chance on tool calls and the run fails loudly.
                for _round in range(max_tool_rounds + 2):
                    response = await self._completer(
                        model=model, messages=messages, tools=tool_schemas
                    )
                    model_calls += 1
                    input_tokens += response.input_tokens
                    output_tokens += response.output_tokens

                    if not response.tool_calls:
                        reply_text = response.text
                        break
                    if tool_setup is None:
                        messages.append(
                            HumanMessage(
                                content=(
                                    "No tools are configured for this call. Answer from the "
                                    "schema alone, or say what you cannot determine without one."
                                )
                            )
                        )
                        continue
                    if tool_rounds >= max_tool_rounds:
                        # It asked once too often. `reply_text` stays empty, so
                        # the run fails below rather than granting one more round.
                        break
                    tool_rounds += 1
                    registry, context = tool_setup
                    messages.append(response.as_message())
                    for call in response.tool_calls:
                        outcome = await registry.execute(
                            call.name, call.arguments, context=context
                        )
                        messages.append(
                            ToolMessage(
                                content=wrap_tool_result(call.name, outcome.content),
                                tool_call_id=call.id,
                            )
                        )

                if reply_text is None:
                    problems.append(
                        f"the model asked for tools in {max_tool_rounds + 1} consecutive "
                        "turns and never answered"
                    )
                    break

                try:
                    parsed = schema.model_validate_json(json_only(reply_text))
                except ValidationError as exc:
                    problems.append(validation_summary(exc))
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
                    tool_rounds=tool_rounds,
                    usage=_usage_line(input_tokens, output_tokens),
                )
                return parsed, Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    calls=model_calls,
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
            tool_rounds=tool_rounds,
            usage=_usage_line(input_tokens, output_tokens),
        )
        raise error
