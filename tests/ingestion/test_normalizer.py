"""Tests for ingestion.normalizer."""

from __future__ import annotations

from datetime import timezone

from email_intel.db.models import MailFolder
from email_intel.ingestion.normalizer import normalize_message, upsert_categories


def _folder() -> MailFolder:
    f = MailFolder()
    f.id = 7
    f.user_id = 1
    f.graph_folder_id = "AAAFOLDER"
    f.well_known_name = "inbox"
    return f


_SAMPLE_RAW = {
    "id": "AQMkADAwATY3ZmYAZi0=",
    "@odata.etag": 'W/"CQAAABYAAAB..."',
    "changeKey": "CQAAABYAAAB",
    "conversationId": "CONV-1",
    "internetMessageId": "<abcd@example.com>",
    "subject": "Hello",
    "receivedDateTime": "2026-04-12T10:15:00Z",
    "sentDateTime": "2026-04-12T10:14:58Z",
    "from": {"emailAddress": {"address": "Alice@Example.COM", "name": "Alice"}},
    "sender": {"emailAddress": {"address": "Alice@Example.COM", "name": "Alice"}},
    "toRecipients": [
        {"emailAddress": {"address": "me@example.com", "name": "Me"}},
    ],
    "ccRecipients": [
        {"emailAddress": {"address": "cc@example.com", "name": "CC"}},
    ],
    "replyTo": [],
    "categories": ["Important", "AI-Respond"],
    "isRead": False,
    "importance": "normal",
    "hasAttachments": False,
    "body": {
        "contentType": "html",
        "content": "<html><body><p>Hi <b>there</b>!</p><script>bad()</script><p>Line 2</p></body></html>",
    },
    "bodyPreview": "Hi there!",
    "webLink": "https://outlook.office.com/...",
    "parentFolderId": "PARENTFOLDERID",
    "internetMessageHeaders": [
        {"name": "List-Unsubscribe", "value": "<mailto:unsub@x>"},
        {"name": "X-Custom", "value": "ignored"},
    ],
}


def test_normalize_basic_fields() -> None:
    record = normalize_message(_SAMPLE_RAW, user_id=1, folder=_folder())

    assert record["graph_message_id"] == "AQMkADAwATY3ZmYAZi0="
    assert record["graph_conversation_id"] == "CONV-1"
    assert record["folder_id"] == 7
    assert record["from_address"] == "alice@example.com"  # lowercased
    assert record["from_name"] == "Alice"
    assert record["sender_address"] == "alice@example.com"
    assert record["etag"] == 'W/"CQAAABYAAAB..."'
    assert record["change_key"] == "CQAAABYAAAB"
    assert record["categories_json"] == ["Important", "AI-Respond"]
    assert record["is_deleted"] is False

    received = record["received_at"]
    assert received is not None
    assert received.tzinfo is not None
    assert received.utcoffset() == timezone.utc.utcoffset(received)

    # HTML stripped, scripts dropped
    body = record["body_text"]
    assert body is not None
    assert "<" not in body
    assert "bad()" not in body
    assert "Hi" in body and "Line 2" in body

    assert record["body_preview"] == "Hi there!"

    # Only whitelisted headers kept
    headers = record["raw_headers_json"]
    assert headers == {"List-Unsubscribe": "<mailto:unsub@x>"}

    # Recipients serialized
    assert record["to_recipients_json"] == [{"address": "me@example.com", "name": "Me"}]
    assert record["cc_recipients_json"] == [{"address": "cc@example.com", "name": "CC"}]


def test_normalize_removed_marker() -> None:
    raw = {"id": "GONE-1", "@removed": {"reason": "deleted"}, "conversationId": "CONV-2"}
    rec = normalize_message(raw, user_id=1, folder=_folder())
    assert rec["is_deleted"] is True
    assert rec["graph_message_id"] == "GONE-1"
    assert rec["graph_conversation_id"] == "CONV-2"


def test_body_truncation_to_100k() -> None:
    raw = dict(_SAMPLE_RAW)
    raw["body"] = {"contentType": "text", "content": "x" * 200_000}
    rec = normalize_message(raw, user_id=1, folder=_folder())
    body = rec["body_text"]
    assert body is not None and len(body) == 100_000


def test_upsert_categories_preserves_user_and_swaps_ai() -> None:
    existing = ["Important", "AI-Respond", "Work"]
    out = upsert_categories(existing, "AI-Act")
    assert "Important" in out
    assert "Work" in out
    assert "AI-Respond" not in out
    assert out.count("AI-Act") == 1


def test_upsert_categories_none_clears_ai() -> None:
    out = upsert_categories(["Important", "AI-Respond"], None)
    assert out == ["Important"]
