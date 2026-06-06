"""Channel adapter contract and SDK-free reference helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class OutboundMessageRef:
    platform: str
    message_id: str
    workspace_id: str = ""
    channel_id: str = ""
    thread_id: str = ""


class ChannelAdapter(Protocol):
    """Minimal adapter contract for external channel gateways."""

    platform: str

    def send_item(self, item_card: dict[str, Any]) -> OutboundMessageRef:
        ...

    def send_digest(self, digest_card: dict[str, Any]) -> OutboundMessageRef:
        ...

    def normalize_interaction(self, event: dict[str, Any]) -> dict[str, Any]:
        ...


class FakeAdapter:
    """Deterministic adapter used by tests and examples."""

    platform = "fake"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def _send(self, card: dict[str, Any]) -> OutboundMessageRef:
        message_id = f"fake-{len(self.sent) + 1}"
        self.sent.append({"message_id": message_id, "card": card})
        return OutboundMessageRef(platform=self.platform, message_id=message_id)

    def send_item(self, item_card: dict[str, Any]) -> OutboundMessageRef:
        return self._send(item_card)

    def send_digest(self, digest_card: dict[str, Any]) -> OutboundMessageRef:
        return self._send(digest_card)

    def normalize_interaction(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "event_id": str(event.get("event_id", "")),
            "workspace_id": str(event.get("workspace_id", "")),
            "channel_id": str(event.get("channel_id", "")),
            "thread_id": str(event.get("thread_id", "")),
            "message_id": str(event.get("message_id", "")),
            "user_id": str(event.get("user_id", "")),
            "kind": str(event.get("kind", "reaction")),
            "value": str(event.get("value", "")),
            "raw_payload": event,
        }


def normalize_telegram_event(event: dict[str, Any]) -> dict[str, Any]:
    reaction = event.get("reaction") or event.get("value") or ""
    return {
        "platform": "telegram",
        "event_id": str(event.get("event_id") or event.get("update_id") or ""),
        "workspace_id": "",
        "channel_id": str(event.get("chat_id") or event.get("channel_id") or ""),
        "thread_id": str(event.get("thread_id") or ""),
        "message_id": str(event.get("message_id") or ""),
        "user_id": str(event.get("user_id") or ""),
        "kind": "reaction",
        "value": str(reaction),
        "raw_payload": event,
    }


def normalize_slack_event(event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    thread_id = str(event.get("thread_ts") or "")
    return {
        "platform": "slack",
        "event_id": str(event.get("event_id") or event.get("event_ts") or ""),
        "workspace_id": str(event.get("team_id") or ""),
        "channel_id": str(item.get("channel") or event.get("channel") or ""),
        "thread_id": thread_id,
        "message_id": str(item.get("ts") or event.get("message_ts") or ""),
        "user_id": str(event.get("user") or ""),
        "kind": "reaction",
        "value": str(event.get("reaction") or event.get("value") or ""),
        "raw_payload": event,
    }


def normalize_discord_event(event: dict[str, Any]) -> dict[str, Any]:
    emoji = event.get("emoji") if isinstance(event.get("emoji"), dict) else {}
    value = emoji.get("name") or event.get("value") or ""
    return {
        "platform": "discord",
        "event_id": str(event.get("id") or ""),
        "workspace_id": str(event.get("guild_id") or ""),
        "channel_id": str(event.get("channel_id") or ""),
        "thread_id": str(event.get("thread_id") or ""),
        "message_id": str(event.get("message_id") or ""),
        "user_id": str(event.get("user_id") or ""),
        "kind": "reaction",
        "value": str(value),
        "raw_payload": event,
    }
