from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from contents_hub.channels import FakeAdapter, normalize_discord_event, normalize_slack_event
from contents_hub.cli import main as cli_main
from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.delivery import pending_delivery_payload
from contents_hub.interactions import handle_interaction, record_outbound_message


def _cfg(tmp_path) -> WikiConfig:
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()
    return cfg


def _seed_raw_item(cfg: WikiConfig) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_db(cfg) as conn:
        cur = conn.execute(
            """
            INSERT INTO raw_items
                (url, title, body, origin, priority, status,
                 content_summary, metadata_json, collected_at, updated_at)
            VALUES (?, ?, ?, 'manual', 100, 'raw', ?, '{}', ?, ?)
            """,
            (
                "https://example.com/story",
                "Example Story",
                "Full body",
                "Short summary",
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def test_schema_v14_adds_delivery_and_interaction_tables(tmp_path):
    cfg = _cfg(tmp_path)
    with get_db(cfg) as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert version == 14
    assert {"outbound_messages", "interaction_events"}.issubset(tables)


def test_existing_telegram_mapping_table_is_migrated_to_outbound_messages(tmp_path):
    cfg = _cfg(tmp_path)
    raw_item_id = _seed_raw_item(cfg)
    now = datetime.now(timezone.utc).isoformat()
    with get_db(cfg) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_raw_item_messages (
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '',
                raw_item_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'send_message',
                PRIMARY KEY(chat_id, message_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO telegram_raw_item_messages
                (chat_id, message_id, thread_id, raw_item_id, created_at)
            VALUES ('chat-1', 'msg-1', 'thread-1', ?, ?)
            """,
            (raw_item_id, now),
        )
        conn.execute("UPDATE schema_version SET version=13")
        conn.commit()

    init_db(cfg).close()

    with get_db(cfg) as conn:
        row = conn.execute(
            """
            SELECT platform, channel_id, thread_id, message_id, raw_item_id
            FROM outbound_messages
            WHERE platform='telegram'
            """
        ).fetchone()
    assert dict(row) == {
        "platform": "telegram",
        "channel_id": "chat-1",
        "thread_id": "thread-1",
        "message_id": "msg-1",
        "raw_item_id": raw_item_id,
    }


def test_pending_delivery_payload_and_fake_adapter_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    raw_item_id = _seed_raw_item(cfg)
    payload = pending_delivery_payload(cfg, payload_type="raw_item")

    assert payload["ok"] is True
    assert payload["items"][0]["raw_item_id"] == raw_item_id
    assert payload["items"][0]["payload_type"] == "raw_item"

    adapter = FakeAdapter()
    outbound = adapter.send_item(payload["items"][0])
    result = record_outbound_message(
        cfg,
        platform=outbound.platform,
        message_id=outbound.message_id,
        payload_type="raw_item",
        raw_item_id=raw_item_id,
    )
    assert result["ok"] is True

    pending_after = pending_delivery_payload(cfg, payload_type="raw_item")
    assert pending_after["items"] == []


def test_reaction_save_promote_is_idempotent_and_logged(tmp_path):
    cfg = _cfg(tmp_path)
    raw_item_id = _seed_raw_item(cfg)
    record_outbound_message(
        cfg,
        platform="telegram",
        channel_id="chat-1",
        thread_id="thread-1",
        message_id="msg-1",
        payload_type="raw_item",
        raw_item_id=raw_item_id,
    )

    event = {
        "platform": "telegram",
        "event_id": "evt-1",
        "channel_id": "chat-1",
        "thread_id": "thread-1",
        "message_id": "msg-1",
        "user_id": "user-1",
        "kind": "reaction",
        "value": "⭐",
    }
    first = handle_interaction(cfg, event)
    second = handle_interaction(cfg, event)

    assert first["ok"] is True
    assert first["saved"] is True
    assert first["promoted"] is True
    assert second["status"] == "already_handled"

    with get_db(cfg) as conn:
        saved_count = conn.execute("SELECT COUNT(*) FROM saved_items").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM interaction_events").fetchone()[0]
        status = conn.execute(
            "SELECT status FROM raw_items WHERE id=?",
            (raw_item_id,),
        ).fetchone()[0]
    assert saved_count == 1
    assert event_count == 1
    assert status == "promoted"
    assert len(list(cfg.sources_path.glob("*.md"))) == 1


def test_unknown_and_unsupported_interactions_are_safe_json(tmp_path):
    cfg = _cfg(tmp_path)
    ignored = handle_interaction(
        cfg,
        {
            "platform": "telegram",
            "event_id": "evt-ignore",
            "channel_id": "chat-1",
            "message_id": "msg-404",
            "kind": "reaction",
            "value": "👀",
        },
    )
    missing = handle_interaction(
        cfg,
        {
            "platform": "telegram",
            "event_id": "evt-missing",
            "channel_id": "chat-1",
            "message_id": "msg-404",
            "kind": "reaction",
            "value": "⭐",
        },
    )

    assert ignored == {
        "ok": True,
        "status": "ignored",
        "noop": True,
        "action": "",
        "interaction_event_id": ignored["interaction_event_id"],
    }
    assert missing["ok"] is False
    assert missing["status"] == "not_found"
    assert missing["error"] == "outbound_message_not_found"


def test_cli_delivery_and_interaction_flow(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    raw_item_id = _seed_raw_item(cfg)

    rc = cli_main(
        [
            "--vault",
            str(cfg.vault_path),
            "delivery",
            "record",
            "--platform",
            "telegram",
            "--channel-id",
            "chat-1",
            "--thread-id",
            "thread-1",
            "--message-id",
            "msg-1",
            "--raw-item-id",
            str(raw_item_id),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True

    event = {
        "platform": "telegram",
        "event_id": "evt-cli",
        "channel_id": "chat-1",
        "thread_id": "thread-1",
        "message_id": "msg-1",
        "kind": "reaction",
        "value": "❤",
    }
    rc = cli_main(
        [
            "--vault",
            str(cfg.vault_path),
            "interaction",
            "handle",
            "--event-json",
            json.dumps(event, ensure_ascii=False),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["action"] == "save_and_promote"


def test_slack_and_discord_fixtures_normalize_to_shared_shape():
    slack = normalize_slack_event(
        {
            "event_id": "slack-evt",
            "team_id": "team-1",
            "item": {"channel": "chan-1", "ts": "123.4"},
            "user": "user-1",
            "reaction": "⭐",
        }
    )
    discord = normalize_discord_event(
        {
            "id": "discord-evt",
            "guild_id": "guild-1",
            "channel_id": "chan-2",
            "message_id": "msg-2",
            "user_id": "user-2",
            "emoji": {"name": "⭐"},
        }
    )

    for payload in (slack, discord):
        assert set(payload) == {
            "platform",
            "event_id",
            "workspace_id",
            "channel_id",
            "thread_id",
            "message_id",
            "user_id",
            "kind",
            "value",
            "raw_payload",
        }
        assert payload["kind"] == "reaction"
        assert payload["value"] == "⭐"
    assert slack["workspace_id"] == "team-1"
    assert slack["channel_id"] == "chan-1"
    assert slack["thread_id"] == ""
    assert slack["message_id"] == "123.4"


def test_slack_reaction_shape_resolves_default_delivery_thread(tmp_path):
    cfg = _cfg(tmp_path)
    raw_item_id = _seed_raw_item(cfg)
    record_outbound_message(
        cfg,
        platform="slack",
        workspace_id="team-1",
        channel_id="chan-1",
        message_id="123.4",
        payload_type="raw_item",
        raw_item_id=raw_item_id,
    )

    event = normalize_slack_event(
        {
            "event_id": "slack-evt",
            "team_id": "team-1",
            "item": {"channel": "chan-1", "ts": "123.4"},
            "user": "user-1",
            "reaction": "⭐",
        }
    )
    result = handle_interaction(cfg, event)

    assert result["ok"] is True
    assert result["action"] == "save_and_promote"
    assert result["raw_item_id"] == raw_item_id
