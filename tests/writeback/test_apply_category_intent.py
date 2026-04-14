"""Unit tests for writeback.categories.apply_category_intent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import ConversationBucket, ConversationState
from email_intel.graph.client import EtagMismatch
from email_intel.schemas.intents import CategoryIntent, CategoryIntentKind
from email_intel.writeback.categories import apply_category_intent

pytestmark = pytest.mark.asyncio


async def test_needs_review_blocks_apply(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    conv = sample_world["conversation"]
    conv.open_action_state = ConversationState.needs_review
    await session.flush()

    res = await apply_category_intent(
        session, graph_placeholder, conv, sample_world["message"],
        CategoryIntent(kind=CategoryIntentKind.apply, target_bucket=ConversationBucket.Act, operation_key="k"),
        now=now,
    )
    assert res["action"] == "noop"
    assert res.get("reason") == "needs_review"
    assert fake_graph.mail.patch_categories.await_count == 0


async def test_apply_preserves_user_cats_swaps_ai(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    msg = sample_world["message"]
    msg.categories_json = ["Work", "AI-Respond", "Personal"]
    await session.flush()

    res = await apply_category_intent(
        session, graph_placeholder, sample_world["conversation"], msg,
        CategoryIntent(kind=CategoryIntentKind.apply, target_bucket=ConversationBucket.Act, operation_key="k-ac"),
        now=now,
    )
    assert res["action"] == "applied"
    assert res["categories"] == ["Work", "Personal", "AI-Act"]

    # DB updated.
    await session.refresh(msg)
    assert msg.categories_json == ["Work", "Personal", "AI-Act"]

    # Graph called once with the merged list.
    args, kwargs = fake_graph.mail.patch_categories.call_args
    # Signature: (client, message_id, categories, etag=...)
    assert args[2] == ["Work", "Personal", "AI-Act"] or kwargs.get("categories") == [
        "Work", "Personal", "AI-Act",
    ]


async def test_clear_strips_ai_only(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    msg = sample_world["message"]
    msg.categories_json = ["Work", "AI-Respond", "AI-Act", "Home"]
    await session.flush()

    res = await apply_category_intent(
        session, graph_placeholder, sample_world["conversation"], msg,
        CategoryIntent(kind=CategoryIntentKind.clear, operation_key="k-cl"),
        now=now,
    )
    assert res["action"] == "cleared"
    assert res["categories"] == ["Work", "Home"]


async def test_etag_mismatch_refetches_and_retries(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    msg = sample_world["message"]
    msg.categories_json = ["AI-Respond"]
    await session.flush()

    # First patch raises EtagMismatch, second succeeds.
    fake_graph.mail.patch_categories.side_effect = [
        EtagMismatch(412, "precondition failed"),
        {"id": msg.graph_message_id, "categories": ["AI-Act"]},
    ]
    fake_graph.mail.get_message.return_value = {
        "id": msg.graph_message_id,
        "categories": ["ServerAdded"],  # user added a category concurrently
        "@odata.etag": 'W/"fresh"',
    }

    res = await apply_category_intent(
        session, graph_placeholder, sample_world["conversation"], msg,
        CategoryIntent(kind=CategoryIntentKind.apply, target_bucket=ConversationBucket.Act, operation_key="k-etag"),
        now=now,
    )
    assert res["action"] == "applied"
    assert fake_graph.mail.patch_categories.await_count == 2
    assert fake_graph.mail.get_message.await_count == 1

    # Retry used the server's category list.
    await session.refresh(msg)
    assert "ServerAdded" in (msg.categories_json or [])
    assert "AI-Act" in (msg.categories_json or [])


async def test_idempotent_repeat_returns_prior(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    msg = sample_world["message"]
    msg.categories_json = ["Work"]
    await session.flush()

    intent = CategoryIntent(
        kind=CategoryIntentKind.apply,
        target_bucket=ConversationBucket.Respond,
        operation_key="k-cat-idem",
    )
    first = await apply_category_intent(
        session, graph_placeholder, sample_world["conversation"], msg, intent, now=now,
    )
    assert first["action"] == "applied"

    second = await apply_category_intent(
        session, graph_placeholder, sample_world["conversation"], msg, intent, now=now,
    )
    assert second == first
    # Patch only called once.
    assert fake_graph.mail.patch_categories.await_count == 1
