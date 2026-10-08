"""The copilot endpoint (Phase 22, §31).

One route, one verb, one capability. It answers questions and nothing else: the
tools it can reach are reads plus the sandbox, so there is no "act" path to gate,
no partial state to reconcile, and no route that could be mistaken for approval.

Gates, in the order a request meets them:

- **session and tenant** (`get_current_tenant`), because every tool reads rows;
- **`use_copilot`**, the capability Phase 22 added to the ops manager and the
  data steward (D-024). The merchandiser and the SEO specialist are refused:
  the copilot answers across the whole catalog, and the data-integration admin
  holds credential custody;
- **CSRF**, because this is a POST that spends tokens;
- **input validation** on the question and the history, and a **rate limit** per
  caller.

The three failures a caller can act on are distinct status codes: 400 for a
question that cannot be asked, 429 for over budget, 502 for a model that could
not answer within the rules. The last one is a gateway failure on purpose — the
copilot is an optional surface, and returning 200 with an invented paragraph
would be the one unacceptable outcome.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.client import ChatCompletionClient, StructuredLLM
from app.api.deps import TenantScope, require_capability
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.tenancy import UserRole
from app.schemas.copilot import (
    CopilotCitationOut,
    CopilotQuery,
    CopilotResponse,
)
from app.services.copilot.agent import (
    ANSWER_TIER,
    CopilotAnswerError,
    CopilotRefusedError,
    CopilotTurn,
    ask,
)
from app.services.copilot.rate_limit import CopilotRateLimitError

logger = get_logger(__name__)
router = APIRouter(tags=["copilot"])


def get_structured_llm() -> StructuredLLM:
    """The client the copilot reasons with.

    A dependency rather than a module global so a test can substitute a scripted
    one, and so the client's transport is closed after a single turn instead of
    living for the process.
    """
    return ChatCompletionClient(settings=get_settings())


@router.post(
    "/copilot/query",
    response_model=CopilotResponse,
    summary="Ask a question about this workspace (§31)",
)
async def query_copilot(
    body: CopilotQuery,
    scope: Annotated[TenantScope, Depends(require_capability("use_copilot"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    llm: Annotated[StructuredLLM, Depends(get_structured_llm)],
) -> CopilotResponse:
    try:
        turn = await ask(
            db,
            scope.tenant,
            question=body.question,
            role=_role(scope),
            user_id=scope.context.user.id,
            history=[turn.model_dump() for turn in body.history],
            llm=llm,
        )
    except CopilotRefusedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.reason
        ) from exc
    except CopilotRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "too many copilot questions; wait before asking again "
                f"(about {exc.retry_after_seconds}s)"
            ),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except CopilotAnswerError as exc:
        logger.warning("copilot_unanswerable", workspace_id=str(scope.workspace_id))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "the copilot could not answer this question within its rules: "
                f"{exc.reason}"
            ),
        ) from exc

    return _response(turn, llm)


def _role(scope: TenantScope) -> UserRole:
    """The caller's role as the enum the capability matrix is keyed by.

    `TenantScope.role` is a string because it comes off the membership row; the
    matrix is keyed by `UserRole`, so this is where the two meet. An unknown role
    has no capabilities and the gate above already refused it.
    """
    return UserRole(scope.role)


def _response(turn: CopilotTurn, llm: StructuredLLM) -> CopilotResponse:
    model = llm.model_for(ANSWER_TIER) if hasattr(llm, "model_for") else None
    return CopilotResponse(
        answer=turn.answer.answer,
        citations=[
            CopilotCitationOut(
                tool=citation.tool, reference=citation.reference, detail=citation.detail
            )
            for citation in turn.answer.citations
        ],
        tools_used=turn.tools_called,
        insufficient_data=turn.answer.insufficient_data,
        follow_ups=turn.answer.follow_ups,
        tool_calls=turn.tool_calls,
        run_id=turn.run_id,
        model=model,
        usage={
            "input_tokens": turn.usage.input_tokens,
            "output_tokens": turn.usage.output_tokens,
            "total_tokens": turn.usage.total_tokens,
            "calls": turn.usage.calls,
            "repairs": turn.usage.repairs,
        },
        note=turn.note,
    )