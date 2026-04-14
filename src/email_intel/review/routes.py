"""Review console HTTP routes. See project-plan §16, §12-§13 review concepts.

Two distinct review concepts (project-plan §9):
  - conversations.state_review_reason: reducer raised, blocks writeback
  - classifications.classification_review_reason: classifier flagged for audit

The review UI is read/write to the DB only. It MUST NOT import reducer,
classify, graph, ingestion, or writeback modules. Overrides record intent via
conversation_events; Wave 3 will wire background triggers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_intel.db.models import (
    Classification,
    Conversation,
    ConversationBucket,
    ConversationEvent,
    ConversationEventType,
    ConversationState,
    EventActor,
    ReviewStatus,
    TodoTask,
)

_HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"

router = APIRouter(prefix="/review", tags=["review"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Static files mount (CSS). Tests that mount only the router get this for free.
router.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="review-static",
)


# ---------- Session dependency ----------


async def get_session(request: Request) -> AsyncSession:
    """Yield an AsyncSession from app.state.session_factory.

    Tests may override this dependency via ``app.dependency_overrides``.
    """
    factory = cast(
        "async_sessionmaker[AsyncSession]",
        request.app.state.session_factory,
    )
    async with factory() as sess:
        return sess


# ---------- Helpers ----------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _load_conversation(session: AsyncSession, conversation_id: int) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


async def _state_review_queue(session: AsyncSession) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.open_action_state == ConversationState.needs_review)
        .order_by(desc(Conversation.latest_received_at))
        .limit(200)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _classification_review_queue(
    session: AsyncSession,
) -> list[tuple[Classification, Conversation | None]]:
    stmt = (
        select(Classification, Conversation)
        .outerjoin(Conversation, Conversation.id == Classification.conversation_id)
        .where(Classification.review_status == ReviewStatus.pending)
        .where(Classification.classification_review_reason.is_not(None))
        .order_by(desc(Classification.created_at))
        .limit(200)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def _recent_events(
    session: AsyncSession, limit: int = 50, offset: int = 0
) -> list[tuple[ConversationEvent, Conversation | None]]:
    stmt = (
        select(ConversationEvent, Conversation)
        .outerjoin(Conversation, Conversation.id == ConversationEvent.conversation_id)
        .order_by(desc(ConversationEvent.occurred_at))
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


# ---------- Routes ----------


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the review dashboard with three queues."""
    state_items = await _state_review_queue(session)
    classification_items = await _classification_review_queue(session)
    events = await _recent_events(session, limit=50)
    return templates.TemplateResponse(request, "dashboard.html", {"state_items": state_items,
            "classification_items": classification_items,
            "events": events,
        },
    )


@router.get("/conversations/{conversation_id}", response_class=HTMLResponse)
async def conversation_detail(
    conversation_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the per-conversation detail / override console."""
    conv = await _load_conversation(session, conversation_id)

    events_stmt = (
        select(ConversationEvent)
        .where(ConversationEvent.conversation_id == conversation_id)
        .order_by(desc(ConversationEvent.occurred_at))
        .limit(100)
    )
    events = list((await session.execute(events_stmt)).scalars().all())

    classifications_stmt = (
        select(Classification)
        .where(Classification.conversation_id == conversation_id)
        .order_by(desc(Classification.created_at))
        .limit(10)
    )
    classifications = list((await session.execute(classifications_stmt)).scalars().all())

    active_task_stmt = (
        select(TodoTask)
        .where(TodoTask.conversation_id == conversation_id)
        .where(TodoTask.status.in_(["notStarted", "inProgress"]))
        .order_by(desc(TodoTask.created_at))
        .limit(1)
    )
    active_task = (await session.execute(active_task_stmt)).scalars().first()

    return templates.TemplateResponse(request, "conversation.html", {"conv": conv,
            "events": events,
            "classifications": classifications,
            "active_task": active_task,
            "states": [s.value for s in ConversationState],
            "buckets": [b.value for b in ConversationBucket],
        },
    )


@router.post(
    "/conversations/{conversation_id}/override",
    response_class=HTMLResponse,
)
async def post_override(
    conversation_id: int,
    request: Request,
    target_state: str = Form(...),
    target_bucket: str | None = Form(None),
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Record an override intent. Does NOT call the reducer (Wave 3)."""
    conv = await _load_conversation(session, conversation_id)

    try:
        state_enum = ConversationState(target_state)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid target_state: {target_state}"
        ) from exc
    bucket_value: str | None = None
    if target_bucket:
        try:
            bucket_value = ConversationBucket(target_bucket).value
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid target_bucket: {target_bucket}"
            ) from exc

    payload: dict[str, Any] = {
        "target_state": state_enum.value,
        "target_bucket": bucket_value,
        "note": note,
    }

    event_row = ConversationEvent(
        user_id=conv.user_id,
        conversation_id=conv.id,
        event_type=ConversationEventType.override_applied,
        before_state=conv.open_action_state,
        after_state=conv.open_action_state,
        payload_json=payload,
        actor=EventActor.user_override,
        occurred_at=_utcnow(),
    )
    session.add(event_row)

    conv.state_review_reason = None
    await session.commit()

    return templates.TemplateResponse(request, "partials/override_result.html", {"conv": conv,
            "payload": payload,
            "message": "Override recorded.",
        },
    )


@router.post(
    "/conversations/{conversation_id}/clear-review",
    response_class=HTMLResponse,
)
async def post_clear_review(
    conversation_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Clear state_review_reason; append a needs_review_resolved event."""
    conv = await _load_conversation(session, conversation_id)
    conv.state_review_reason = None

    event_row = ConversationEvent(
        user_id=conv.user_id,
        conversation_id=conv.id,
        event_type=ConversationEventType.needs_review_resolved,
        before_state=conv.open_action_state,
        after_state=conv.open_action_state,
        payload_json={"source": "review_ui"},
        actor=EventActor.user_override,
        occurred_at=_utcnow(),
    )
    session.add(event_row)
    await session.commit()

    return templates.TemplateResponse(request, "partials/override_result.html", {"conv": conv,
            "payload": {},
            "message": "State review cleared.",
        },
    )


@router.post(
    "/classifications/{classification_id}/resolve",
    response_class=HTMLResponse,
)
async def post_classification_resolve(
    classification_id: int,
    request: Request,
    decision: str = Form(...),
    target_bucket: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Resolve a classification review row via accept or override."""
    classification = await session.get(Classification, classification_id)
    if classification is None:
        raise HTTPException(status_code=404, detail="classification not found")

    if decision not in ("accept", "override"):
        raise HTTPException(
            status_code=422, detail="decision must be 'accept' or 'override'"
        )

    conv = await session.get(Conversation, classification.conversation_id)
    if conv is None:  # pragma: no cover - FK guarantees presence
        raise HTTPException(status_code=404, detail="conversation not found")

    if decision == "accept":
        classification.review_status = ReviewStatus.resolved_accept
        message = "Accepted classification."
    else:
        if not target_bucket:
            raise HTTPException(
                status_code=422,
                detail="target_bucket required when decision='override'",
            )
        try:
            bucket_enum = ConversationBucket(target_bucket)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid target_bucket: {target_bucket}"
            ) from exc
        classification.review_status = ReviewStatus.resolved_override

        event_row = ConversationEvent(
            user_id=conv.user_id,
            conversation_id=conv.id,
            event_type=ConversationEventType.override_applied,
            before_state=conv.open_action_state,
            after_state=conv.open_action_state,
            payload_json={
                "scope": "classification",
                "classification_id": classification.id,
                "target_bucket": bucket_enum.value,
            },
            actor=EventActor.user_override,
            occurred_at=_utcnow(),
        )
        session.add(event_row)
        message = f"Overrode classification to {bucket_enum.value}."

    await session.commit()

    return templates.TemplateResponse(request, "partials/override_result.html", {"conv": conv,
            "payload": {"classification_id": classification.id, "decision": decision},
            "message": message,
        },
    )


@router.get(
    "/conversations/{conversation_id}/events",
    response_class=HTMLResponse,
)
async def get_conversation_events(
    conversation_id: int,
    request: Request,
    offset: int = 0,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Paginated event timeline partial (for infinite scroll)."""
    await _load_conversation(session, conversation_id)
    stmt = (
        select(ConversationEvent)
        .where(ConversationEvent.conversation_id == conversation_id)
        .order_by(desc(ConversationEvent.occurred_at))
        .offset(offset)
        .limit(limit)
    )
    events = list((await session.execute(stmt)).scalars().all())
    next_offset: int | None = offset + limit if len(events) == limit else None
    return templates.TemplateResponse(request, "partials/timeline.html", {"events": events,
            "conversation_id": conversation_id,
            "next_offset": next_offset,
        },
    )
