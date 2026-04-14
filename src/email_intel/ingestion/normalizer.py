"""Raw Graph message -> canonical internal record.

See project-plan §10.1, §11.3.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from email_intel.db.models import MailFolder

# Headers we keep if present, per spec.
_KEPT_HEADERS = {
    "list-unsubscribe",
    "auto-submitted",
    "precedence",
    "x-auto-response-suppress",
}

_BODY_MAX_CHARS = 100_000
_PREVIEW_MAX_CHARS = 255


class _HTMLStripper(HTMLParser):
    """Minimal HTML -> text extractor; drops <script>/<style> contents."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in ("br", "p", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in ("p", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # Collapse runs of whitespace but preserve paragraph breaks.
        lines = [line.strip() for line in raw.splitlines()]
        lines = [ln for ln in lines if ln]
        return "\n".join(lines)


def _strip_html(html: str) -> str:
    p = _HTMLStripper()
    try:
        p.feed(html)
        p.close()
    except Exception:
        # Parsing HTML should never crash ingestion; fall back to raw content.
        return html
    return p.text()


def _parse_graph_datetime(raw: str | None) -> datetime | None:
    """Parse a Graph ISO8601 timestamp into a tz-aware datetime."""
    if not raw:
        return None
    try:
        # Graph emits ...Z; Python <3.11 needs +00:00 rewrite.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _addr_from_emailaddress(obj: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Extract (address, name) from Graph emailAddress structure."""
    if not obj:
        return None, None
    ea = obj.get("emailAddress") if isinstance(obj, dict) else None
    if ea is None and isinstance(obj, dict) and "address" in obj:
        ea = obj
    if not ea:
        return None, None
    addr = ea.get("address")
    name = ea.get("name")
    return (addr.lower() if isinstance(addr, str) else None, name)


def _recipient_list(raw: list[dict[str, Any]] | None) -> list[dict[str, str | None]]:
    if not raw:
        return []
    out: list[dict[str, str | None]] = []
    for entry in raw:
        addr, name = _addr_from_emailaddress(entry)
        out.append({"address": addr, "name": name})
    return out


def _keep_headers(raw_headers: list[dict[str, Any]] | None) -> dict[str, str] | None:
    if not raw_headers:
        return None
    kept: dict[str, str] = {}
    for h in raw_headers:
        name = h.get("name")
        value = h.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if name.lower() in _KEPT_HEADERS:
            kept[name] = value
    return kept or None


def normalize_message(raw: dict[str, Any], user_id: int, folder: MailFolder) -> dict[str, Any]:
    """Raw Graph message payload -> dict matching Message ORM columns (minus id).

    Handles the `@removed` tombstone by emitting a minimal record flagged
    `is_deleted=True`.
    """
    graph_message_id = raw.get("id")
    if not isinstance(graph_message_id, str):
        raise ValueError("Graph message payload missing 'id'")

    # Tombstone: Graph returns {"id": "...", "@removed": {...}}
    if "@removed" in raw:
        return {
            "user_id": user_id,
            "graph_message_id": graph_message_id,
            "graph_conversation_id": raw.get("conversationId") or "",
            "folder_id": folder.id,
            "is_deleted": True,
        }

    # Body handling
    body_text: str | None = None
    body_obj = raw.get("body")
    if isinstance(body_obj, dict):
        content = body_obj.get("content") or ""
        ctype = (body_obj.get("contentType") or "").lower()
        if ctype == "html":
            body_text = _strip_html(content)
        else:
            body_text = content
        if body_text is not None and len(body_text) > _BODY_MAX_CHARS:
            body_text = body_text[:_BODY_MAX_CHARS]

    body_preview_raw = raw.get("bodyPreview")
    body_preview: str | None = None
    if isinstance(body_preview_raw, str):
        body_preview = body_preview_raw[:_PREVIEW_MAX_CHARS]

    from_addr, from_name = _addr_from_emailaddress(raw.get("from"))
    sender_addr, _ = _addr_from_emailaddress(raw.get("sender"))

    categories = raw.get("categories")
    if not isinstance(categories, list):
        categories = []

    return {
        "user_id": user_id,
        "graph_message_id": graph_message_id,
        "internet_message_id": raw.get("internetMessageId"),
        "graph_conversation_id": raw.get("conversationId") or "",
        "folder_id": folder.id,
        "subject": raw.get("subject"),
        "from_address": from_addr,
        "from_name": from_name,
        "sender_address": sender_addr,
        "to_recipients_json": _recipient_list(raw.get("toRecipients")),
        "cc_recipients_json": _recipient_list(raw.get("ccRecipients")),
        "reply_to_json": _recipient_list(raw.get("replyTo")),
        "received_at": _parse_graph_datetime(raw.get("receivedDateTime")),
        "sent_at": _parse_graph_datetime(raw.get("sentDateTime")),
        "is_read": bool(raw.get("isRead", False)),
        "importance": raw.get("importance"),
        "has_attachments": bool(raw.get("hasAttachments", False)),
        "categories_json": list(categories),
        "body_text": body_text,
        "body_preview": body_preview,
        "web_link": raw.get("webLink"),
        "parent_folder_graph_id": raw.get("parentFolderId"),
        "etag": raw.get("@odata.etag"),
        "change_key": raw.get("changeKey"),
        "raw_headers_json": _keep_headers(raw.get("internetMessageHeaders")),
        "is_deleted": False,
    }


def upsert_categories(existing: list[str], next_ai: str | None) -> list[str]:
    """Strip AI-* categories, add next_ai if provided. Preserve user categories.

    Ordering: user categories first (in original order), then the new AI tag.
    """
    kept = [c for c in existing if not (isinstance(c, str) and c.startswith("AI-"))]
    if next_ai:
        if next_ai not in kept:
            kept.append(next_ai)
    return kept
