"""Tests for subscription management, YAML persistence, and validation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from llm_wiki.config import WikiConfig
from llm_wiki.subscriptions import (
    CollectionSchedule,
    ScheduleConfig,
    SourceType,
    Subscription,
    SubscriptionStatus,
    SubscriptionStore,
    _normalize_feed_url,
    validate_subscription,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory."""
    return tmp_path / "vault"


@pytest.fixture
def config(vault: Path) -> WikiConfig:
    """WikiConfig pointing at the temp vault."""
    vault.mkdir(parents=True, exist_ok=True)
    return WikiConfig(vault_path=vault)


@pytest.fixture
def store(config: WikiConfig) -> SubscriptionStore:
    """Fresh SubscriptionStore backed by temp vault."""
    return SubscriptionStore(config)


# ---------------------------------------------------------------------------
# Tests: Subscription dataclass
# ---------------------------------------------------------------------------


class TestSubscription:
    def test_defaults(self):
        sub = Subscription(url="https://example.com/feed.xml")
        assert sub.url == "https://example.com/feed.xml"
        assert sub.title == ""
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.schedule.preset == CollectionSchedule.DAILY
        assert sub.added_at is not None
        assert sub.last_fetched_at is None
        assert sub.last_fetched_count == 0
        assert sub.lenses == []
        assert sub.tags == []
        assert sub.config == {}
        assert sub.id != ""  # auto-generated UUID

    def test_url_normalization(self):
        sub = Subscription(url="https://example.com/feed/")
        assert sub.url == "https://example.com/feed"

    def test_auto_detect_rss(self):
        sub = Subscription(url="https://example.com/feed.xml")
        assert sub.source_type == "rss"

    def test_auto_detect_youtube(self):
        sub = Subscription(url="https://youtube.com/@channel")
        assert sub.source_type == "youtube"

    def test_auto_detect_twitter(self):
        sub = Subscription(url="https://x.com/user")
        assert sub.source_type == "twitter"

    def test_auto_detect_natural_language(self):
        sub = Subscription(url="", config={"query": "AI research papers"})
        assert sub.source_type == "natural-language"

    def test_key_url_based(self):
        sub = Subscription(url="https://example.com/feed.xml")
        assert sub.key == "https://example.com/feed.xml"

    def test_key_natural_language(self):
        sub = Subscription(
            url="",
            source_type="natural-language",
            config={"query": "test"},
        )
        assert sub.key.startswith("nl:")
        assert sub.id in sub.key

    def test_record_fetch_success(self):
        sub = Subscription(url="https://example.com/feed.xml")
        sub.record_fetch(item_count=10)
        assert sub.last_fetched_at is not None
        assert sub.last_fetched_count == 10
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.error_message == ""

    def test_record_fetch_error(self):
        sub = Subscription(url="https://example.com/feed.xml")
        sub.record_fetch(item_count=0, error="HTTP 503")
        assert sub.status == SubscriptionStatus.ERROR
        assert sub.error_message == "HTTP 503"

    def test_record_fetch_clears_error(self):
        sub = Subscription(url="https://example.com/feed.xml")
        sub.record_fetch(item_count=0, error="Timeout")
        assert sub.status == SubscriptionStatus.ERROR
        sub.record_fetch(item_count=5)
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.error_message == ""

    def test_roundtrip_dict(self):
        now = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        sub = Subscription(
            url="https://example.com/feed.xml",
            title="Test Blog",
            added_at=now,
            lenses=["tech", "ai"],
            tags=["blog", "daily"],
            schedule=ScheduleConfig(preset=CollectionSchedule.WEEKLY),
            config={"custom_key": "value"},
        )
        sub.record_fetch(item_count=3)

        d = sub.to_dict()
        restored = Subscription.from_dict(d)
        assert restored.url == sub.url
        assert restored.title == sub.title
        assert restored.id == sub.id
        assert restored.added_at == sub.added_at
        assert restored.lenses == sub.lenses
        assert restored.tags == ["blog", "daily"]
        assert restored.schedule.preset == CollectionSchedule.WEEKLY
        assert restored.config == {"custom_key": "value"}
        assert restored.last_fetched_count == 3
        assert restored.status == SubscriptionStatus.ACTIVE

    def test_roundtrip_natural_language(self):
        sub = Subscription(
            url="",
            title="AI Research",
            source_type="natural-language",
            config={
                "query": "Latest transformer architecture papers",
                "searchHints": ["arxiv.org"],
                "freshness": "recent",
                "maxPerRun": 10,
            },
        )
        d = sub.to_dict()
        restored = Subscription.from_dict(d)
        assert restored.source_type == "natural-language"
        assert restored.config["query"] == "Latest transformer architecture papers"
        assert restored.config["searchHints"] == ["arxiv.org"]
        assert restored.id == sub.id

    def test_from_dict_missing_fields(self):
        """Gracefully handle minimal dict."""
        sub = Subscription.from_dict({"url": "https://example.com/feed"})
        assert sub.url == "https://example.com/feed"
        assert sub.title == ""
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.schedule.preset == CollectionSchedule.DAILY

    def test_from_dict_invalid_status_defaults(self):
        """Invalid status string defaults to ACTIVE."""
        sub = Subscription.from_dict({
            "url": "https://example.com/feed",
            "status": "unknown_status",
        })
        assert sub.status == SubscriptionStatus.ACTIVE

    def test_from_dict_invalid_schedule_defaults(self):
        """Invalid schedule string defaults to DAILY."""
        sub = Subscription.from_dict({
            "url": "https://example.com/feed",
            "schedule": "biweekly",
        })
        assert sub.schedule.preset == CollectionSchedule.DAILY

    def test_to_dict_omits_empty_optional_fields(self):
        sub = Subscription(url="https://example.com/feed.xml")
        d = sub.to_dict()
        assert "last_fetched_at" not in d
        assert "last_fetched_count" not in d
        assert "error_message" not in d
        assert "lenses" not in d
        assert "tags" not in d
        assert "config" not in d


# ---------------------------------------------------------------------------
# Tests: Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_rss(self):
        sub = Subscription(url="https://example.com/feed.xml")
        assert validate_subscription(sub) == []

    def test_valid_natural_language(self):
        sub = Subscription(
            url="",
            source_type="natural-language",
            config={"query": "AI papers"},
        )
        assert validate_subscription(sub) == []

    def test_invalid_url_rss(self):
        sub = Subscription.__new__(Subscription)
        sub.url = "not-a-url"
        sub.source_type = "rss"
        sub.config = {}
        errors = validate_subscription(sub)
        assert any("Invalid URL" in e for e in errors)

    def test_missing_query_natural_language(self):
        sub = Subscription(
            url="",
            source_type="natural-language",
            config={},
        )
        errors = validate_subscription(sub)
        assert any("config.query" in e for e in errors)

    def test_unknown_source_type(self):
        sub = Subscription.__new__(Subscription)
        sub.url = "https://example.com"
        sub.source_type = "email"
        sub.config = {}
        errors = validate_subscription(sub)
        assert any("Unknown source_type" in e for e in errors)


# ---------------------------------------------------------------------------
# Tests: SubscriptionStore CRUD
# ---------------------------------------------------------------------------


class TestStoreAdd:
    def test_add_basic(self, store: SubscriptionStore):
        sub = store.add("https://example.com/feed.xml", title="My Blog")
        assert sub.url == "https://example.com/feed.xml"
        assert sub.title == "My Blog"
        assert store.count == 1

    def test_add_with_lenses(self, store: SubscriptionStore):
        sub = store.add(
            "https://example.com/feed.xml",
            title="AI Blog",
            lenses=["ai", "research"],
        )
        assert sub.lenses == ["ai", "research"]

    def test_add_with_tags(self, store: SubscriptionStore):
        sub = store.add(
            "https://example.com/feed.xml",
            title="Blog",
            tags=["daily-read", "tech"],
        )
        assert sub.tags == ["daily-read", "tech"]

    def test_add_with_schedule(self, store: SubscriptionStore):
        sub = store.add(
            "https://example.com/feed.xml",
            schedule="weekly",
        )
        assert sub.schedule.preset == CollectionSchedule.WEEKLY

    def test_add_with_config(self, store: SubscriptionStore):
        sub = store.add(
            "https://example.com/blog",
            source_type="webpage",
            config={"selector": "article.post", "mode": "list"},
        )
        assert sub.config["selector"] == "article.post"

    def test_add_natural_language(self, store: SubscriptionStore):
        sub = store.add(
            "",
            title="AI Research",
            source_type="natural-language",
            config={"query": "Latest AI papers on RAG"},
            lenses=["ai"],
        )
        assert sub.source_type == "natural-language"
        assert sub.config["query"] == "Latest AI papers on RAG"
        assert store.count == 1

    def test_add_natural_language_without_query_raises(self, store: SubscriptionStore):
        with pytest.raises(ValueError, match="config.query"):
            store.add(
                "",
                title="Bad NL",
                source_type="natural-language",
                config={},
            )

    def test_add_duplicate_raises(self, store: SubscriptionStore):
        store.add("https://example.com/feed.xml")
        with pytest.raises(ValueError, match="Already subscribed"):
            store.add("https://example.com/feed.xml")

    def test_add_duplicate_trailing_slash(self, store: SubscriptionStore):
        store.add("https://example.com/feed")
        with pytest.raises(ValueError, match="Already subscribed"):
            store.add("https://example.com/feed/")

    def test_add_invalid_url(self, store: SubscriptionStore):
        with pytest.raises(ValueError, match="Invalid feed URL"):
            store.add("not-a-url")

    def test_add_ftp_url_rejected(self, store: SubscriptionStore):
        with pytest.raises(ValueError, match="Invalid feed URL"):
            store.add("ftp://example.com/feed")

    def test_add_generates_uuid(self, store: SubscriptionStore):
        sub = store.add("https://example.com/feed.xml")
        assert sub.id != ""
        assert len(sub.id) == 36  # UUID format


class TestStoreRemove:
    def test_remove(self, store: SubscriptionStore):
        store.add("https://example.com/feed.xml", title="Blog")
        removed = store.remove("https://example.com/feed.xml")
        assert removed.title == "Blog"
        assert store.count == 0

    def test_remove_not_found(self, store: SubscriptionStore):
        with pytest.raises(KeyError, match="Not subscribed"):
            store.remove("https://example.com/nope")

    def test_remove_by_id(self, store: SubscriptionStore):
        sub = store.add(
            "",
            title="NL Sub",
            source_type="natural-language",
            config={"query": "test"},
        )
        removed = store.remove_by_id(sub.id)
        assert removed.title == "NL Sub"
        assert store.count == 0

    def test_remove_by_id_not_found(self, store: SubscriptionStore):
        with pytest.raises(KeyError, match="No subscription with id"):
            store.remove_by_id("nonexistent-id")


class TestStoreGet:
    def test_get_existing(self, store: SubscriptionStore):
        store.add("https://example.com/feed.xml", title="Blog")
        sub = store.get("https://example.com/feed.xml")
        assert sub is not None
        assert sub.title == "Blog"

    def test_get_normalized(self, store: SubscriptionStore):
        store.add("https://example.com/feed")
        assert store.get("https://example.com/feed/") is not None

    def test_get_missing(self, store: SubscriptionStore):
        assert store.get("https://example.com/nope") is None

    def test_get_by_id(self, store: SubscriptionStore):
        sub = store.add("https://example.com/feed.xml", title="Blog")
        found = store.get_by_id(sub.id)
        assert found is not None
        assert found.title == "Blog"

    def test_get_by_id_missing(self, store: SubscriptionStore):
        assert store.get_by_id("nonexistent-id") is None


class TestStoreList:
    def test_list_all_empty(self, store: SubscriptionStore):
        assert store.list_all() == []

    def test_list_all_sorted_newest_first(self, store: SubscriptionStore):
        t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2024, 6, 1, tzinfo=timezone.utc)

        store._feeds["https://a.com/feed"] = Subscription(
            url="https://a.com/feed", title="Old", added_at=t1
        )
        store._feeds["https://b.com/feed"] = Subscription(
            url="https://b.com/feed", title="New", added_at=t2
        )

        result = store.list_all()
        assert result[0].title == "New"
        assert result[1].title == "Old"

    def test_list_by_status(self, store: SubscriptionStore):
        store.add("https://a.com/feed", title="Active")
        store.add("https://b.com/feed", title="Paused")
        store.set_status("https://b.com/feed", SubscriptionStatus.PAUSED)

        active = store.list_by_status(SubscriptionStatus.ACTIVE)
        paused = store.list_by_status(SubscriptionStatus.PAUSED)
        assert len(active) == 1
        assert active[0].title == "Active"
        assert len(paused) == 1
        assert paused[0].title == "Paused"

    def test_list_by_source_type(self, store: SubscriptionStore):
        store.add("https://youtube.com/@chan", title="YT")
        store.add("https://example.com/feed.xml", title="RSS")

        yt = store.list_by_source_type("youtube")
        assert len(yt) == 1
        assert yt[0].title == "YT"

    def test_list_by_lens(self, store: SubscriptionStore):
        store.add("https://a.com/feed", title="AI Blog", lenses=["ai", "tech"])
        store.add("https://b.com/feed", title="Cooking", lenses=["food"])

        ai_feeds = store.list_by_lens("ai")
        assert len(ai_feeds) == 1
        assert ai_feeds[0].title == "AI Blog"

    def test_list_by_lens_case_insensitive(self, store: SubscriptionStore):
        store.add("https://a.com/feed", lenses=["AI"])
        assert len(store.list_by_lens("ai")) == 1

    def test_list_by_schedule(self, store: SubscriptionStore):
        store.add("https://a.com/feed", schedule="hourly")
        store.add("https://b.com/feed", schedule="daily")

        hourly = store.list_by_schedule("hourly")
        assert len(hourly) == 1


class TestStoreUpdate:
    def test_update_title(self, store: SubscriptionStore):
        store.add("https://a.com/feed")
        store.update_title("https://a.com/feed", "Updated Title")
        assert store.get("https://a.com/feed").title == "Updated Title"

    def test_update_lenses(self, store: SubscriptionStore):
        store.add("https://a.com/feed", lenses=["old"])
        store.update_lenses("https://a.com/feed", ["new1", "new2"])
        assert store.get("https://a.com/feed").lenses == ["new1", "new2"]

    def test_update_tags(self, store: SubscriptionStore):
        store.add("https://a.com/feed", tags=["old"])
        store.update_tags("https://a.com/feed", ["new1", "new2"])
        assert store.get("https://a.com/feed").tags == ["new1", "new2"]

    def test_update_schedule(self, store: SubscriptionStore):
        store.add("https://a.com/feed")
        store.update_schedule("https://a.com/feed", "weekly")
        assert store.get("https://a.com/feed").schedule.preset == CollectionSchedule.WEEKLY

    def test_update_config(self, store: SubscriptionStore):
        store.add("https://a.com/feed")
        store.update_config("https://a.com/feed", {"custom": "value"})
        assert store.get("https://a.com/feed").config["custom"] == "value"

    def test_set_status(self, store: SubscriptionStore):
        store.add("https://a.com/feed")
        store.set_status("https://a.com/feed", SubscriptionStatus.PAUSED)
        assert store.get("https://a.com/feed").status == SubscriptionStatus.PAUSED

    def test_record_fetch(self, store: SubscriptionStore):
        store.add("https://a.com/feed")
        store.record_fetch("https://a.com/feed", item_count=7)
        sub = store.get("https://a.com/feed")
        assert sub.last_fetched_count == 7
        assert sub.last_fetched_at is not None

    def test_record_fetch_error(self, store: SubscriptionStore):
        store.add("https://a.com/feed")
        store.record_fetch("https://a.com/feed", item_count=0, error="Timeout")
        sub = store.get("https://a.com/feed")
        assert sub.status == SubscriptionStatus.ERROR
        assert sub.error_message == "Timeout"

    def test_needs_fetch(self, store: SubscriptionStore):
        store.add("https://a.com/feed")
        store.add("https://b.com/feed")
        store.set_status("https://b.com/feed", SubscriptionStatus.PAUSED)
        fetchable = store.needs_fetch()
        assert len(fetchable) == 1
        assert fetchable[0].url == "https://a.com/feed"


# ---------------------------------------------------------------------------
# Tests: YAML persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_creates_directory(self, config: WikiConfig):
        """Meta directory is created on first save."""
        store = SubscriptionStore(config)
        store.add("https://example.com/feed.xml", title="Test")
        assert config.subscriptions_file.exists()
        assert config.meta_path.is_dir()

    def test_save_and_reload(self, config: WikiConfig):
        """Data survives a full reload cycle."""
        store1 = SubscriptionStore(config)
        store1.add(
            "https://a.com/feed",
            title="Alpha",
            lenses=["tech"],
            tags=["daily"],
            schedule="weekly",
        )
        store1.add("https://b.com/feed", title="Beta")
        store1.record_fetch("https://a.com/feed", item_count=5)

        # New store instance loads from disk
        store2 = SubscriptionStore(config)
        assert store2.count == 2
        alpha = store2.get("https://a.com/feed")
        assert alpha is not None
        assert alpha.title == "Alpha"
        assert alpha.lenses == ["tech"]
        assert alpha.tags == ["daily"]
        assert alpha.schedule.preset == CollectionSchedule.WEEKLY
        assert alpha.last_fetched_count == 5

    def test_save_and_reload_natural_language(self, config: WikiConfig):
        """Natural-language subscriptions persist correctly."""
        store1 = SubscriptionStore(config)
        sub = store1.add(
            "",
            title="AI Research",
            source_type="natural-language",
            config={"query": "Latest AI papers", "freshness": "recent"},
            lenses=["ai"],
        )
        sub_id = sub.id

        store2 = SubscriptionStore(config)
        assert store2.count == 1
        found = store2.get_by_id(sub_id)
        assert found is not None
        assert found.title == "AI Research"
        assert found.source_type == "natural-language"
        assert found.config["query"] == "Latest AI papers"

    def test_yaml_is_human_readable(self, config: WikiConfig):
        """YAML file should be readable and editable by humans."""
        store = SubscriptionStore(config)
        store.add(
            "https://example.com/feed.xml",
            title="My Blog",
            lenses=["tech"],
            tags=["blog"],
            schedule="weekly",
        )

        content = config.subscriptions_file.read_text()
        data = yaml.safe_load(content)
        assert "feeds" in data
        assert len(data["feeds"]) == 1
        feed = data["feeds"][0]
        assert feed["title"] == "My Blog"
        assert feed["status"] == "active"
        assert feed["schedule"] == "weekly"
        assert feed["lenses"] == ["tech"]
        assert feed["tags"] == ["blog"]
        assert "id" in feed

    def test_empty_file_loads_ok(self, config: WikiConfig):
        """An empty YAML file should not crash."""
        config.meta_path.mkdir(parents=True, exist_ok=True)
        config.subscriptions_file.write_text("", encoding="utf-8")
        store = SubscriptionStore(config)
        assert store.count == 0

    def test_corrupted_file_loads_empty(self, config: WikiConfig):
        """Corrupted YAML should not crash, just load empty."""
        config.meta_path.mkdir(parents=True, exist_ok=True)
        config.subscriptions_file.write_text(": [invalid yaml{{{", encoding="utf-8")
        store = SubscriptionStore(config)
        assert store.count == 0

    def test_remove_persists(self, config: WikiConfig):
        store = SubscriptionStore(config)
        store.add("https://a.com/feed")
        store.add("https://b.com/feed")
        store.remove("https://a.com/feed")

        reloaded = SubscriptionStore(config)
        assert reloaded.count == 1
        assert reloaded.get("https://a.com/feed") is None
        assert reloaded.get("https://b.com/feed") is not None

    def test_reload_picks_up_external_changes(self, config: WikiConfig):
        """reload() picks up changes made externally to the YAML file."""
        store = SubscriptionStore(config)
        store.add("https://a.com/feed", title="Original")

        # Simulate external edit
        data = yaml.safe_load(config.subscriptions_file.read_text())
        data["feeds"][0]["title"] = "Edited Externally"
        config.subscriptions_file.write_text(
            yaml.dump(data, default_flow_style=False), encoding="utf-8"
        )

        store.reload()
        assert store.get("https://a.com/feed").title == "Edited Externally"

    def test_id_preserved_across_reloads(self, config: WikiConfig):
        """Subscription IDs are stable across save/load cycles."""
        store1 = SubscriptionStore(config)
        sub = store1.add("https://example.com/feed.xml")
        original_id = sub.id

        store2 = SubscriptionStore(config)
        reloaded = store2.get("https://example.com/feed.xml")
        assert reloaded.id == original_id


# ---------------------------------------------------------------------------
# Tests: URL normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_trailing_slash_removed(self):
        assert _normalize_feed_url("https://a.com/feed/") == "https://a.com/feed"

    def test_root_slash_preserved(self):
        assert _normalize_feed_url("https://a.com/") == "https://a.com/"

    def test_fragment_removed(self):
        assert _normalize_feed_url("https://a.com/feed#section") == "https://a.com/feed"

    def test_whitespace_stripped(self):
        assert _normalize_feed_url("  https://a.com/feed  ") == "https://a.com/feed"

    def test_no_change_for_clean_url(self):
        assert _normalize_feed_url("https://a.com/feed.xml") == "https://a.com/feed.xml"

    def test_empty_url(self):
        assert _normalize_feed_url("") == ""


# ---------------------------------------------------------------------------
# Tests: Source type detection
# ---------------------------------------------------------------------------


class TestSourceTypeDetection:
    def test_rss_default(self):
        sub = Subscription(url="https://example.com/feed.xml")
        assert sub.source_type == "rss"

    def test_youtube(self):
        sub = Subscription(url="https://youtube.com/@channel")
        assert sub.source_type == "youtube"

    def test_twitter_x(self):
        sub = Subscription(url="https://x.com/user")
        assert sub.source_type == "twitter"

    def test_twitter_classic(self):
        sub = Subscription(url="https://twitter.com/user")
        assert sub.source_type == "twitter"

    def test_natural_language_via_config(self):
        sub = Subscription(url="", config={"query": "AI papers"})
        assert sub.source_type == "natural-language"

    def test_webpage_no_url(self):
        sub = Subscription(url="")
        assert sub.source_type == "webpage"

    def test_explicit_source_type_not_overridden(self):
        sub = Subscription(url="https://example.com", source_type="webpage")
        assert sub.source_type == "webpage"
