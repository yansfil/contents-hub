"""Unit tests for Lens routing fallback to all enabled automatic-routing lenses.

When an owner (subscription or exploration) has no explicit Lens IDs
configured, Lens evaluation must fall back to every enabled automatic-routing
Lens.  An owner with a non-empty explicit list is still pinned to that list.
Disabled lenses and the legacy manual-inbox Lens are excluded in fallback paths.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.explorations import ExplorationStore
from contents_hub.lenses import (
    LensOwnerContext,
    load_enabled_default_lenses,
    load_exploration_lenses,
    load_lenses_for_owner,
)
from contents_hub.subscriptions import SubscriptionStore


def _cfg(tmp_path: Path) -> WikiConfig:
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    return cfg


def _seed_lens(
    cfg: WikiConfig,
    lens_id: str,
    *,
    keywords: list[str] | None = None,
    enabled: bool = True,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db(cfg) as conn:
        conn.execute(
            """INSERT INTO lenses
               (id, name, description, keywords, enabled, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?, ?)""",
            (
                lens_id,
                lens_id,
                json.dumps(keywords or []),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Subscription fallback
# ---------------------------------------------------------------------------


def test_subscription_explicit_default_lens_ids_returns_only_those_enabled(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "rust")
    _seed_lens(cfg, "other")

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Pinned",
        source_type="rss.feed",
        lenses=["ai", "rust"],
    )

    with get_db(cfg) as conn:
        lenses = load_enabled_default_lenses(conn, int(sub.id))

    assert [lens.id for lens in lenses] == ["ai", "rust"]


def test_subscription_without_default_lens_ids_falls_back_to_all_enabled(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "rust")
    _seed_lens(cfg, "infra")

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Defaulting",
        source_type="rss.feed",
    )

    with get_db(cfg) as conn:
        lenses = load_enabled_default_lenses(conn, int(sub.id))

    assert {lens.id for lens in lenses} == {"ai", "rust", "infra"}


def test_subscription_fallback_excludes_disabled_lenses(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "muted", enabled=False)

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Defaulting",
        source_type="rss.feed",
    )

    with get_db(cfg) as conn:
        lenses = load_enabled_default_lenses(conn, int(sub.id))

    assert [lens.id for lens in lenses] == ["ai"]


def test_subscription_fallback_excludes_manual_inbox(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "manual-inbox")

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Defaulting",
        source_type="rss.feed",
    )

    with get_db(cfg) as conn:
        lenses = load_enabled_default_lenses(conn, int(sub.id))

    assert [lens.id for lens in lenses] == ["ai"]


def test_subscription_explicit_list_does_not_broaden_when_lenses_are_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "muted", enabled=False)

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Pinned to disabled",
        source_type="rss.feed",
        lenses=["muted"],
    )

    with get_db(cfg) as conn:
        lenses = load_enabled_default_lenses(conn, int(sub.id))

    # Non-empty configured list pins routing — even though only disabled lenses
    # remain after filtering, the fallback must NOT broaden to all enabled.
    assert lenses == []


def test_subscription_unknown_id_returns_empty(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")

    with get_db(cfg) as conn:
        assert load_enabled_default_lenses(conn, 9999) == []


# ---------------------------------------------------------------------------
# Exploration fallback
# ---------------------------------------------------------------------------


def test_exploration_explicit_lens_ids_returns_only_those_enabled(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "rust")
    _seed_lens(cfg, "other")

    store = ExplorationStore(cfg)
    exploration = store.create_draft(
        display_name="Pinned",
        original_request="Find ai/rust posts",
        lens_ids=["ai", "rust"],
    )

    with get_db(cfg) as conn:
        lenses = load_exploration_lenses(conn, exploration.id)

    assert [lens.id for lens in lenses] == ["ai", "rust"]


def test_exploration_without_lens_ids_falls_back_to_all_enabled(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "rust")
    _seed_lens(cfg, "infra")

    store = ExplorationStore(cfg)
    exploration = store.create_draft(
        display_name="Defaulting",
        original_request="Find anything interesting",
    )

    with get_db(cfg) as conn:
        lenses = load_exploration_lenses(conn, exploration.id)

    assert {lens.id for lens in lenses} == {"ai", "rust", "infra"}


def test_exploration_fallback_excludes_disabled_lenses(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "muted", enabled=False)

    store = ExplorationStore(cfg)
    exploration = store.create_draft(
        display_name="Defaulting",
        original_request="Find anything",
    )

    with get_db(cfg) as conn:
        lenses = load_exploration_lenses(conn, exploration.id)

    assert [lens.id for lens in lenses] == ["ai"]


def test_exploration_fallback_excludes_manual_inbox(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "manual-inbox")

    store = ExplorationStore(cfg)
    exploration = store.create_draft(
        display_name="Defaulting",
        original_request="Find anything",
    )

    with get_db(cfg) as conn:
        lenses = load_exploration_lenses(conn, exploration.id)

    assert [lens.id for lens in lenses] == ["ai"]


def test_exploration_explicit_list_does_not_broaden_when_lenses_are_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "muted", enabled=False)

    store = ExplorationStore(cfg)
    exploration = store.create_draft(
        display_name="Pinned to disabled",
        original_request="Find muted posts",
        lens_ids=["muted"],
    )

    with get_db(cfg) as conn:
        lenses = load_exploration_lenses(conn, exploration.id)

    assert lenses == []


def test_exploration_unknown_id_returns_empty(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")

    with get_db(cfg) as conn:
        assert load_exploration_lenses(conn, 9999) == []


# ---------------------------------------------------------------------------
# load_lenses_for_owner routing
# ---------------------------------------------------------------------------


def test_load_lenses_for_owner_subscription_falls_back_when_empty(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "rust")

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Defaulting",
        source_type="rss.feed",
    )

    owner = LensOwnerContext(
        owner_type="subscription",
        subscription_id=int(sub.id),
    )
    with get_db(cfg) as conn:
        lenses = load_lenses_for_owner(conn, owner)

    assert {lens.id for lens in lenses} == {"ai", "rust"}


def test_load_lenses_for_owner_exploration_falls_back_when_empty(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "rust")

    store = ExplorationStore(cfg)
    exploration = store.create_draft(
        display_name="Defaulting",
        original_request="Find anything",
    )

    owner = LensOwnerContext(
        owner_type="exploration",
        owner_id=exploration.id,
    )
    with get_db(cfg) as conn:
        lenses = load_lenses_for_owner(conn, owner)

    assert {lens.id for lens in lenses} == {"ai", "rust"}


def test_load_lenses_for_owner_context_lens_ids_pin_routing(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_lens(cfg, "ai")
    _seed_lens(cfg, "rust")
    _seed_lens(cfg, "other")

    store = ExplorationStore(cfg)
    exploration = store.create_draft(
        display_name="Defaulting",
        original_request="Find anything",
    )

    # An explicit lens_ids on the context wins over the owner row's empty list.
    owner = LensOwnerContext(
        owner_type="exploration",
        owner_id=exploration.id,
        lens_ids=("ai",),
    )
    with get_db(cfg) as conn:
        lenses = load_lenses_for_owner(conn, owner)

    assert [lens.id for lens in lenses] == ["ai"]
