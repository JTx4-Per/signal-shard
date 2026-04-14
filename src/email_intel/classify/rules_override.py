"""Stage C · rule final override. See project-plan §12.1 Stage C.

Each rule is tagged with a ``rule_id`` and appends itself to ``reason_short``
when it fires. VIP and blocked-domain lists are optional: loaded from
``config/vip_senders.json`` / ``config/blocked_domains.json`` if present.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from email_intel.db.models import ConversationBucket
from email_intel.schemas.classifier import ClassifierOutput
from email_intel.schemas.snapshot import (
    CanonicalMessage,
    ThreadSnapshot,
    UserRecipientPosition,
)

__all__ = ["OverrideConfig", "load_override_config", "apply_final_override"]

log = structlog.get_logger(__name__)

_NOREPLY_RE = re.compile(
    r"^(noreply|no-reply|donotreply|do-not-reply|mailer-daemon|postmaster)@",
    re.IGNORECASE,
)
_CALENDAR_SUBJECT_RE = re.compile(
    r"^\s*(accepted|declined|tentative)\s*:", re.IGNORECASE
)
_QUESTION_TAIL_RE = re.compile(r"\?\s*$")

_ACTIONABLE: frozenset[ConversationBucket] = frozenset(
    {
        ConversationBucket.Act,
        ConversationBucket.Respond,
        ConversationBucket.Delegate,
        ConversationBucket.WaitingOn,
    }
)


@dataclass(frozen=True)
class OverrideConfig:
    """User-supplied VIP and block lists. Empty lists disable the rules."""

    vip_senders: frozenset[str]
    blocked_domains: frozenset[str]


def load_override_config(
    vip_path: Path | None = None,
    block_path: Path | None = None,
    config_dir: Path | None = None,
) -> OverrideConfig:
    """Load optional VIP / block lists from JSON files."""
    base = config_dir if config_dir is not None else Path.cwd() / "config"
    if vip_path is None:
        vip_path = base / "vip_senders.json"
    if block_path is None:
        block_path = base / "blocked_domains.json"

    vip: set[str] = set()
    blocked: set[str] = set()

    if vip_path.is_file():
        try:
            data = json.loads(vip_path.read_text(encoding="utf-8") or "[]")
            if isinstance(data, list):
                vip = {str(s).strip().lower() for s in data if str(s).strip()}
        except (json.JSONDecodeError, OSError):
            log.warning("override.vip_load_failed", path=str(vip_path))

    if block_path.is_file():
        try:
            data = json.loads(block_path.read_text(encoding="utf-8") or "[]")
            if isinstance(data, list):
                blocked = {str(s).strip().lower() for s in data if str(s).strip()}
        except (json.JSONDecodeError, OSError):
            log.warning("override.block_load_failed", path=str(block_path))

    return OverrideConfig(
        vip_senders=frozenset(vip),
        blocked_domains=frozenset(blocked),
    )


def _latest_inbound(snapshot: ThreadSnapshot) -> CanonicalMessage | None:
    for msg in reversed(snapshot.messages):
        if not msg.is_from_user:
            return msg
    return None


def _has_list_unsubscribe(msg: CanonicalMessage) -> bool:
    for k in msg.headers or {}:
        if k.lower() == "list-unsubscribe":
            return True
    return False


def _sender_addr(msg: CanonicalMessage) -> str:
    return (msg.from_address or msg.sender_address or "").strip().lower()


def _sender_domain(msg: CanonicalMessage) -> str:
    addr = _sender_addr(msg)
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1]


def _appended_reason(existing: str, rule_id: str) -> str:
    if not existing or existing == "no-rules-fired":
        return rule_id
    if rule_id in existing.split("; "):
        return existing
    return f"{existing}; {rule_id}"


def _body_text(msg: CanonicalMessage) -> str:
    return (msg.body_text or msg.body_preview or "") or ""


def _direct_ask_present(msg: CanonicalMessage) -> bool:
    body = _body_text(msg)
    if _QUESTION_TAIL_RE.search(body.rstrip()):
        return True
    return False


def _replace(
    output: ClassifierOutput,
    rule_id: str,
    **changes: object,
) -> ClassifierOutput:
    base = output.model_dump()
    base.update(changes)
    base["reason_short"] = _appended_reason(output.reason_short, rule_id)
    return ClassifierOutput(**base)


def apply_final_override(
    provisional: ClassifierOutput,
    snapshot: ThreadSnapshot,
    rule_version: str,
    config: OverrideConfig | None = None,
) -> ClassifierOutput:
    """Apply Stage C overrides after (skipped) model stage. See §12.1 Stage C."""
    if config is None:
        config = load_override_config()

    latest = _latest_inbound(snapshot)
    if latest is None:
        return provisional

    sender = _sender_addr(latest)
    domain = _sender_domain(latest)
    list_unsub = _has_list_unsubscribe(latest)
    subject = latest.subject or ""
    has_direct_ask = _direct_ask_present(latest)

    out = provisional

    # ---- C-noreply-forces-fyi ----
    if _NOREPLY_RE.search(sender):
        target = (
            ConversationBucket.DeleteOrUnsubscribe
            if list_unsub
            else ConversationBucket.FYI
        )
        log.info(
            "override.fired",
            rule_id="C-noreply-forces-fyi",
            new_bucket=target.value,
            rule_version=rule_version,
        )
        out = _replace(
            out,
            "C-noreply-forces-fyi",
            primary_bucket=target,
            should_create_task=False,
            confidence=max(out.confidence, 0.95),
        )

    # ---- C-list-unsubscribe-forces-delete ----
    if list_unsub and not has_direct_ask:
        log.info(
            "override.fired",
            rule_id="C-list-unsubscribe-forces-delete",
            rule_version=rule_version,
        )
        out = _replace(
            out,
            "C-list-unsubscribe-forces-delete",
            primary_bucket=ConversationBucket.DeleteOrUnsubscribe,
            should_create_task=False,
            unsubscribe_candidate=True,
            delete_candidate=True,
            confidence=max(out.confidence, 0.9),
        )

    # ---- C-calendar-accept-forces-fyi ----
    if _CALENDAR_SUBJECT_RE.search(subject):
        log.info(
            "override.fired",
            rule_id="C-calendar-accept-forces-fyi",
            rule_version=rule_version,
        )
        out = _replace(
            out,
            "C-calendar-accept-forces-fyi",
            primary_bucket=ConversationBucket.FYI,
            should_create_task=False,
            confidence=max(out.confidence, 0.9),
        )

    # ---- C-domain-block ----
    if config.blocked_domains and (
        domain in config.blocked_domains
        or any(domain.endswith("." + d) for d in config.blocked_domains)
    ):
        log.info(
            "override.fired",
            rule_id="C-domain-block",
            domain=domain,
            rule_version=rule_version,
        )
        out = _replace(
            out,
            "C-domain-block",
            primary_bucket=ConversationBucket.DeleteOrUnsubscribe,
            should_create_task=False,
            delete_candidate=True,
            confidence=max(out.confidence, 0.95),
        )

    # ---- C-vip-allow ----
    if config.vip_senders and (
        sender in config.vip_senders
        or (domain and domain in config.vip_senders)
    ):
        original_bucket = provisional.primary_bucket
        was_actionable = original_bucket in _ACTIONABLE
        # Never downgrade to Noise/FYI if original was actionable.
        downgraded = (
            out.primary_bucket
            in {
                ConversationBucket.FYI,
                ConversationBucket.DeleteOrUnsubscribe,
            }
            and was_actionable
        )
        if downgraded or out.confidence < 0.8:
            log.info(
                "override.fired",
                rule_id="C-vip-allow",
                rule_version=rule_version,
                restored_bucket=(original_bucket.value if original_bucket else None),
            )
            new_bucket = original_bucket if downgraded else out.primary_bucket
            out = _replace(
                out,
                "C-vip-allow",
                primary_bucket=new_bucket,
                confidence=max(out.confidence, 0.8),
            )

    # ---- C-respond-but-user-sent-last ----
    if (
        out.primary_bucket == ConversationBucket.Respond
        and snapshot.user_sent_last
    ):
        log.info(
            "override.fired",
            rule_id="C-respond-but-user-sent-last",
            rule_version=rule_version,
        )
        out = _replace(
            out,
            "C-respond-but-user-sent-last",
            primary_bucket=ConversationBucket.WaitingOn,
            should_create_task=False,
            confidence=max(out.confidence, 0.7),
        )

    # Silence unused parameter lint
    _ = UserRecipientPosition

    return out
