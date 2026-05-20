from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

from contents_hub.api import collect_all_active, collect_all_due, fetch_subscription
from contents_hub.cli import main as cli_main
from contents_hub.config import WikiConfig
from contents_hub.db import init_db
from contents_hub.executor import content_items, list_items
from contents_hub.models import FetchedItem, FetchResult, ListItem
from contents_hub.recipes import RecipeRegistry
from contents_hub.runners import get_default_runner, set_default_runner
from contents_hub.source_router import classify
from contents_hub.source_types import SOURCE_TYPES
from contents_hub.subscriptions import SubscriptionStore


class _StubRunner:
    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        return self.response


class _SequenceRunner:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("runner called more times than expected")
        return self.responses.pop(0)


def test_classify_returns_canonical_source_type_and_recipe_pin():
    info = classify("https://www.youtube.com/@openai")

    assert info["source_type"] == "youtube.channel"
    assert info["recipe_id"] == "youtube.channel.default"
    assert info["recipe_version"] == 1
    assert info["execution_method"] == "feed"


def test_classify_detects_github_releases_and_substack_tag():
    github = classify("https://github.com/anthropics/claude-code/releases")
    substack_tag = classify("https://www.a16z.news/t/technology")

    assert github["source_type"] == "github.releases"
    assert github["recipe_id"] == "github.releases.default"
    assert github["execution_method"] == "feed"
    assert substack_tag["source_type"] == "substack.tag"
    assert substack_tag["recipe_id"] == "substack.tag.default"
    assert substack_tag["execution_method"] == "api"


def test_subscription_add_pins_default_recipe(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)

    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )

    assert sub.source_type == "rss.feed"
    assert sub.config["recipe_id"] == "rss.feed.default"
    assert sub.config["recipe_version"] == 1
    assert sub.config["fetch_method"] == "feed"
    assert RecipeRegistry.get_recipe(sub)


def test_every_catalog_source_type_has_a_seed_recipe():
    for spec in SOURCE_TYPES:
        sub = type(
            "S",
            (),
            {
                "url": "https://example.com",
                "source_type": spec.id,
                "config": {},
            },
        )()
        assert RecipeRegistry.get_recipe(sub), spec.id


def test_x_recipe_requires_login_and_preserves_timeline_order():
    recipe = RecipeRegistry.get_seed("x.profile.default")

    assert recipe is not None
    assert "로그인 세션을 요구" in recipe
    assert "article 이 일부 보여도 최신순 신뢰가 없으므로" in recipe
    assert "time[datetime]" in recipe
    assert "published_hint" in recipe
    assert "리포스트는 포함" in recipe
    assert "프로필 타임라인 DOM 순서를 유지" in recipe
    assert "/analytics" in recipe


def test_cli_sub_add_auto_detects_and_pins_recipe(tmp_path, capsys):
    exit_code = cli_main(
        [
            "--vault",
            str(tmp_path),
            "sub",
            "add",
            "https://www.youtube.com/@openai",
            "--collection-prompt",
            "Only collect links from the Videos tab.",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["subscription_id"] > 0
    assert payload["source_type"] == "youtube.channel"
    assert payload["recipe_id"] == "youtube.channel.default"
    assert payload["fetch_method"] == "feed"
    assert payload["collection_prompt"] == "Only collect links from the Videos tab."

    cfg = WikiConfig(vault_path=tmp_path)
    sub = SubscriptionStore(cfg).get("https://www.youtube.com/@openai")
    assert sub is not None
    assert sub.status.value == "active"
    assert sub.config["recipe_id"] == "youtube.channel.default"
    assert sub.config["collection_prompt"] == "Only collect links from the Videos tab."


def test_cli_sub_add_accepts_source_type_override_alias(tmp_path, capsys):
    exit_code = cli_main(
        [
            "--vault",
            str(tmp_path),
            "sub",
            "add",
            "https://example.com/karpathy",
            "--type",
            "x",
            "--title",
            "Karpathy X",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_type"] == "x.profile"
    assert payload["recipe_id"] == "x.profile.default"
    assert payload["fetch_method"] == "browser"
    assert payload["title"] == "Karpathy X"


def test_rss_list_uses_direct_feed_parser_without_agent(monkeypatch):
    sub = type(
        "S",
        (),
        {
            "url": "https://example.com/feed.xml",
            "source_type": "rss.feed",
            "config": {},
        },
    )()

    async def fake_fetch(url: str) -> dict:
        assert url == "https://example.com/feed.xml"
        return {
            "ok": True,
            "body": """
            <feed xmlns=\"http://www.w3.org/2005/Atom\">
              <title>Example Feed</title>
              <entry>
                <title>Post 1</title>
                <link href=\"https://example.com/post-1\" />
                <updated>2026-05-20T09:00:00+09:00</updated>
                <summary>Summary 1</summary>
              </entry>
            </feed>
            """,
        }

    runner = _StubRunner("{}")
    monkeypatch.setattr("contents_hub.executor._fetch_json_url", fake_fetch)

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert [item.url for item in result.items] == ["https://example.com/post-1"]
    assert result.items[0].title_hint == "Post 1"
    assert result.items[0].card_text == "Summary 1"
    assert result.items[0].source_payload["feed_item"]["title"] == "Post 1"
    assert runner.prompts == []


def test_rss_content_uses_feed_entry_without_agent():
    sub = type(
        "S",
        (),
        {
            "url": "https://example.com/feed.xml",
            "source_type": "rss.feed",
            "config": {},
        },
    )()
    runner = _StubRunner("{}")

    result = asyncio.run(
        content_items(
            sub,
            [
                ListItem(
                    item_key="https://example.com/post-1",
                    url="https://example.com/post-1",
                    title_hint="Post 1",
                    published_hint="2026-05-20T09:00:00+09:00",
                    card_text="Summary 1",
                    source_payload={
                        "feed_item": {
                            "url": "https://example.com/post-1",
                            "title": "Post 1",
                            "summary": "Summary 1",
                            "author": "Author",
                            "published_at": "2026-05-20T09:00:00+09:00",
                            "content_html": "<p>Body 1</p>",
                            "tags": ["tag-a"],
                        }
                    },
                )
            ],
            runner=runner,
        )
    )

    assert result.ok is True
    assert result.items[0].title == "Post 1"
    assert result.items[0].summary == "Summary 1"
    assert result.items[0].author == "Author"
    assert result.items[0].content_html == "<p>Body 1</p>"
    assert result.items[0].tags == ["tag-a"]
    assert result.items[0].extra["body_status"] == "feed_entry"
    assert runner.prompts == []


def test_github_releases_list_uses_atom_feed_without_agent(monkeypatch):
    sub = type(
        "S",
        (),
        {
            "url": "https://github.com/anthropics/claude-code/releases",
            "source_type": "github.releases",
            "config": {},
        },
    )()

    async def fake_fetch(url: str) -> dict:
        assert url == "https://github.com/anthropics/claude-code/releases.atom"
        return {
            "ok": True,
            "body": """
            <feed xmlns=\"http://www.w3.org/2005/Atom\">
              <title>Release notes</title>
              <entry>
                <id>tag:v1.0.0</id>
                <title>v1.0.0</title>
                <link href=\"https://github.com/anthropics/claude-code/releases/tag/v1.0.0\" />
                <updated>2026-05-20T00:00:00Z</updated>
                <summary>Release summary</summary>
              </entry>
            </feed>
            """,
        }

    runner = _StubRunner("{}")
    monkeypatch.setattr("contents_hub.executor._fetch_json_url", fake_fetch)

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert result.source_url == "https://github.com/anthropics/claude-code/releases"
    assert [item.url for item in result.items] == [
        "https://github.com/anthropics/claude-code/releases/tag/v1.0.0"
    ]
    assert result.items[0].source_payload["feed_url"].endswith("/releases.atom")
    assert runner.prompts == []


def test_github_releases_content_uses_atom_entry_without_agent():
    sub = type(
        "S",
        (),
        {
            "url": "https://github.com/anthropics/claude-code/releases",
            "source_type": "github.releases",
            "config": {},
        },
    )()
    runner = _StubRunner("{}")

    result = asyncio.run(
        content_items(
            sub,
            [
                ListItem(
                    item_key="tag:v1.0.0",
                    url="https://github.com/anthropics/claude-code/releases/tag/v1.0.0",
                    title_hint="v1.0.0",
                    source_payload={
                        "feed_item": {
                            "url": "https://github.com/anthropics/claude-code/releases/tag/v1.0.0",
                            "title": "v1.0.0",
                            "summary": "Release summary",
                            "published_at": "2026-05-20T00:00:00Z",
                            "content_html": "<p>Release body</p>",
                        }
                    },
                )
            ],
            runner=runner,
        )
    )

    assert result.ok is True
    assert result.items[0].source_type == "github.releases"
    assert result.items[0].title == "v1.0.0"
    assert result.items[0].content_html == "<p>Release body</p>"
    assert result.items[0].extra["body_status"] == "feed_entry"
    assert runner.prompts == []


def test_reddit_list_uses_json_listing_without_agent(monkeypatch):
    sub = type(
        "S",
        (),
        {
            "url": "https://www.reddit.com/r/SideProject",
            "source_type": "reddit.subreddit",
            "config": {},
        },
    )()

    async def fake_fetch(url: str) -> dict:
        assert url == "https://www.reddit.com/r/SideProject/new.json?limit=50"
        return {
            "ok": True,
            "body": json.dumps(
                {
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "id": "abc123",
                                    "name": "t3_abc123",
                                    "title": "Launch day",
                                    "permalink": "/r/SideProject/comments/abc123/launch_day/",
                                    "created_utc": 1779286747.0,
                                    "selftext": "We shipped a tiny product.",
                                    "url": "https://www.reddit.com/r/SideProject/comments/abc123/launch_day/",
                                    "author": "maker",
                                    "subreddit": "SideProject",
                                    "score": 12,
                                    "num_comments": 3,
                                    "link_flair_text": "Showoff",
                                }
                            }
                        ]
                    }
                }
            ),
        }

    runner = _StubRunner("{}")
    monkeypatch.setattr("contents_hub.executor._fetch_json_url", fake_fetch)

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert result.items[0].item_key == "reddit:post:abc123"
    assert result.items[0].url == "https://www.reddit.com/r/SideProject/comments/abc123/launch_day/"
    assert result.items[0].title_hint == "Launch day"
    assert result.items[0].source_payload["reddit_post"]["author"] == "maker"
    assert runner.prompts == []


def test_reddit_content_uses_listing_snapshot_without_agent():
    sub = type(
        "S",
        (),
        {
            "url": "https://www.reddit.com/r/SideProject",
            "source_type": "reddit.subreddit",
            "config": {},
        },
    )()
    runner = _StubRunner("{}")

    result = asyncio.run(
        content_items(
            sub,
            [
                ListItem(
                    item_key="reddit:post:abc123",
                    url="https://www.reddit.com/r/SideProject/comments/abc123/launch_day/",
                    title_hint="Launch day",
                    published_hint="2026-05-20T00:00:00+00:00",
                    card_text="We shipped a tiny product.",
                    source_payload={
                        "reddit_post": {
                            "id": "abc123",
                            "title": "Launch day",
                            "created_at": "2026-05-20T00:00:00+00:00",
                            "selftext": "We shipped a tiny product.",
                            "url": "https://example.com/product",
                            "author": "maker",
                            "subreddit": "SideProject",
                            "score": 12,
                            "num_comments": 3,
                            "link_flair_text": "Showoff",
                        }
                    },
                )
            ],
            runner=runner,
        )
    )

    assert result.ok is True
    assert result.items[0].title == "Launch day"
    assert result.items[0].author == "maker"
    assert result.items[0].tags == ["Showoff"]
    assert result.items[0].extra["external_url"] == "https://example.com/product"
    assert result.items[0].extra["body_status"] == "full"
    assert runner.prompts == []


def test_substack_tag_list_uses_archive_api_without_agent(monkeypatch):
    sub = type(
        "S",
        (),
        {
            "url": "https://www.a16z.news/t/technology",
            "source_type": "substack.tag",
            "config": {},
        },
    )()

    async def fake_fetch(url: str) -> dict:
        assert url == "https://www.a16z.news/api/v1/archive?sort=new&tag=technology&limit=50"
        return {
            "ok": True,
            "body": json.dumps(
                [
                    {
                        "id": 101,
                        "title": "Need Series C?",
                        "subtitle": "AI adoption notes",
                        "description": "AI adoption notes",
                        "slug": "need-series-c",
                        "canonical_url": "https://www.a16z.news/p/need-series-c",
                        "post_date": "2026-05-19T14:00:52.670Z",
                        "body_html": "<p>Full post body</p>",
                        "publishedBylines": [{"name": "Alex Danco"}],
                        "postTags": [{"name": "Technology", "slug": "technology"}],
                    }
                ]
            ),
        }

    runner = _StubRunner("{}")
    monkeypatch.setattr("contents_hub.executor._fetch_json_url", fake_fetch)

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert result.items[0].item_key == "substack:post:101"
    assert result.items[0].url == "https://www.a16z.news/p/need-series-c"
    assert result.items[0].title_hint == "Need Series C?"
    assert result.items[0].source_payload["substack_post"]["body_html"] == "<p>Full post body</p>"
    assert runner.prompts == []


def test_substack_tag_content_uses_archive_snapshot_without_agent():
    sub = type(
        "S",
        (),
        {
            "url": "https://www.a16z.news/t/technology",
            "source_type": "substack.tag",
            "config": {},
        },
    )()
    runner = _StubRunner("{}")

    result = asyncio.run(
        content_items(
            sub,
            [
                ListItem(
                    item_key="substack:post:101",
                    url="https://www.a16z.news/p/need-series-c",
                    title_hint="Need Series C?",
                    published_hint="2026-05-19T14:00:52.670Z",
                    card_text="AI adoption notes",
                    source_payload={
                        "substack_post": {
                            "id": 101,
                            "title": "Need Series C?",
                            "subtitle": "AI adoption notes",
                            "canonical_url": "https://www.a16z.news/p/need-series-c",
                            "post_date": "2026-05-19T14:00:52.670Z",
                            "body_html": "<p>Full post body</p>",
                            "publishedBylines": [{"name": "Alex Danco"}],
                            "postTags": [{"name": "Technology", "slug": "technology"}],
                        }
                    },
                )
            ],
            runner=runner,
        )
    )

    assert result.ok is True
    assert result.items[0].title == "Need Series C?"
    assert result.items[0].author == "Alex Danco"
    assert result.items[0].tags == ["Technology"]
    assert result.items[0].content_html == "<p>Full post body</p>"
    assert result.items[0].extra["body_status"] == "full"
    assert "body_html" not in result.items[0].extra["source_payload"]["substack_post"]
    assert runner.prompts == []


def test_webpage_content_uses_fetch_url_parse_html_without_agent(monkeypatch):
    sub = type(
        "S",
        (),
        {
            "url": "https://example.com/articles",
            "source_type": "webpage",
            "config": {},
        },
    )()

    async def fake_fetch_url(url: str, **kwargs) -> str:
        assert url == "https://example.com/articles/post-1"
        assert kwargs["mode"] == "raw"
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "url": url,
                "content_type": "text/html; charset=utf-8",
                "body": """
                <html>
                  <head>
                    <title>Example Article</title>
                    <meta property="og:title" content="Example Article">
                    <meta property="og:description" content="Metadata summary">
                    <meta property="article:published_time" content="2026-05-20T00:00:00Z">
                    <meta name="author" content="Example Author">
                  </head>
                  <body>
                    <nav>Navigation</nav>
                    <article><h1>Example Article</h1><p>Body paragraph one.</p></article>
                  </body>
                </html>
                """,
                "raw_body_chars": 600,
            }
        )

    runner = _StubRunner("{}")
    monkeypatch.setattr("contents_hub.tools.fetchers.fetch_url", fake_fetch_url)

    result = asyncio.run(
        content_items(
            sub,
            [
                ListItem(
                    item_key="post-1",
                    url="https://example.com/articles/post-1",
                    title_hint="Post 1",
                )
            ],
            runner=runner,
        )
    )

    assert result.ok is True
    assert result.items[0].title == "Example Article"
    assert result.items[0].summary == "Metadata summary"
    assert result.items[0].author == "Example Author"
    assert "Body paragraph one." in result.items[0].content_html
    assert result.items[0].extra["body_status"] == "partial"
    assert result.items[0].extra["detail_fetch_method"] == "fetch_url_parse_html"
    assert runner.prompts == []


def test_youtube_list_uses_videos_page_fallback_without_agent(monkeypatch):
    sub = type(
        "S",
        (),
        {
            "url": "https://www.youtube.com/@Example",
            "source_type": "youtube.channel",
            "config": {},
        },
    )()

    async def fake_fetch(url: str) -> dict:
        if url == "https://www.youtube.com/@Example":
            return {"ok": True, "body": '"browseId":"UCexample0000000000000000"'}
        if url.startswith("https://www.youtube.com/feeds/videos.xml"):
            return {"ok": False, "status": 404, "body": ""}
        if url == "https://www.youtube.com/@Example/videos":
            return {
                "ok": True,
                "body": (
                    '"videoId":"EWvNQjAaOHw"'
                    '"videoId":"EWvNQjAaOHw"'
                    '"videoId":"7xTGNNLPyMI"'
                ),
            }
        raise AssertionError(f"unexpected fetch {url}")

    runner = _StubRunner("{}")
    monkeypatch.setattr("contents_hub.executor._fetch_json_url", fake_fetch)

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert [item.url for item in result.items] == [
        "https://www.youtube.com/watch?v=EWvNQjAaOHw",
        "https://www.youtube.com/watch?v=7xTGNNLPyMI",
    ]
    assert runner.prompts == []


def test_youtube_content_uses_page_metadata_without_agent(monkeypatch):
    sub = type(
        "S",
        (),
        {
            "url": "https://www.youtube.com/@Example",
            "source_type": "youtube.channel",
            "config": {},
        },
    )()

    async def fake_fetch(url: str) -> dict:
        assert url == "https://www.youtube.com/watch?v=EWvNQjAaOHw"
        return {
            "ok": True,
            "body": (
                '<meta property="og:title" content="Demo Video - YouTube">'
                '<meta property="og:description" content="Demo summary">'
                '"ownerChannelName":"Example Channel"'
                '"publishDate":"2026-05-13"'
            ),
        }

    runner = _StubRunner("{}")
    monkeypatch.setattr("contents_hub.executor._fetch_json_url", fake_fetch)

    result = asyncio.run(
        content_items(
            sub,
            [
                ListItem(
                    item_key="yt:video:EWvNQjAaOHw",
                    url="https://www.youtube.com/watch?v=EWvNQjAaOHw",
                )
            ],
            runner=runner,
        )
    )

    assert result.ok is True
    assert result.items[0].title == "Demo Video"
    assert result.items[0].summary == "Demo summary"
    assert result.items[0].author == "Example Channel"
    assert result.items[0].published_at is not None
    assert result.items[0].extra["body_status"] == "metadata_only"
    assert runner.prompts == []


def test_x_content_uses_list_snapshot_without_agent():
    sub = type(
        "S",
        (),
        {
            "url": "https://x.com/garrytan",
            "source_type": "x.profile",
            "config": {},
        },
    )()
    runner = _StubRunner("{}")

    result = asyncio.run(
        content_items(
            sub,
            [
                ListItem(
                    item_key="x:status:1",
                    url="https://x.com/garrytan/status/1",
                    title_hint="Garry Tan posted",
                    published_hint="2026-05-13T01:00:00Z",
                    card_text="Garry Tan\n@garrytan\nNew post body",
                    source_payload={"status_author": "garrytan"},
                )
            ],
            runner=runner,
        )
    )

    assert result.ok is True
    assert result.items[0].title == "Garry Tan posted"
    assert result.items[0].content_html == "Garry Tan\n@garrytan\nNew post body"
    assert result.items[0].author == "garrytan"
    assert result.items[0].extra["body_status"] == "list_card_snapshot"
    assert runner.prompts == []


def test_fetch_subscription_persists_items_with_catalog_recipe(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )

    published = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    runner = _StubRunner(
        """
        {
          "items": [
            {
              "url": "https://example.com/post-1",
              "title": "Post 1",
              "summary": "summary",
              "content": "body",
              "published_at": "%s"
            }
          ],
          "errors": []
        }
        """
        % published
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert result.items[0].source_type == "rss.feed"
    assert result.items[0].extra["fetch_method"] == "feed"
    assert runner.prompts
    assert "rss.feed.default" in store.get_by_id(sub.id).config["recipe_id"]

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            "SELECT title, body, published_at FROM raw_items WHERE subscription_id = ?",
            (int(sub.id),),
        ).fetchone()

    assert row == ("Post 1", "body", published)


def test_fetch_subscription_diffs_before_content_fetch(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )
    existing_url = "https://example.com/post-1"

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            "INSERT INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                existing_url,
                "Existing",
                "body",
                int(sub.id),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    runner = _SequenceRunner(
        [
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1/",
                  "title_hint": "Existing"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert result.items == []
    assert result.total_available == 1
    assert len(runner.prompts) == 1
    assert "LIST_STRATEGY 만" in runner.prompts[0]


def test_collect_all_due_diffs_before_content_fetch(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )
    existing_url = "https://example.com/post-1"

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            "INSERT INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                existing_url,
                "Existing",
                "body",
                int(sub.id),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    runner = _SequenceRunner(
        [
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1/",
                  "title_hint": "Existing"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(collect_all_due(cfg))
    finally:
        set_default_runner(original)

    assert result.total == 1
    assert result.new == 0
    assert result.skipped == 1
    assert result.errors == 0
    assert len(runner.prompts) == 1


def test_collect_all_active_ignores_due_schedule_and_dedupes(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )
    existing_url = "https://example.com/post-1"
    now_iso = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            "UPDATE subscriptions SET last_fetched_at = ?, schedule_interval_minutes = ? WHERE id = ?",
            (now_iso, 1440, int(sub.id)),
        )
        conn.execute(
            "INSERT INTO raw_items (url, title, body, subscription_id, "
            "collected_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                existing_url,
                "Existing",
                "body",
                int(sub.id),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()

    runner = _SequenceRunner(
        [
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1/",
                  "title_hint": "Existing"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        due = asyncio.run(collect_all_due(cfg))
        forced = asyncio.run(collect_all_active(cfg))
    finally:
        set_default_runner(original)

    assert due.total == 0
    assert forced.total == 1
    assert forced.new == 0
    assert forced.skipped == 1
    assert forced.errors == 0
    assert len(runner.prompts) == 1


def test_collect_all_active_can_include_error_for_manual_fetch_all(tmp_path, monkeypatch):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    active = store.add(
        url="https://example.com/active.xml",
        title="Active Feed",
        source_type="rss.feed",
    )
    errored = store.add(
        url="https://example.com/error.xml",
        title="Errored Feed",
        source_type="rss.feed",
    )

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        conn.execute(
            """UPDATE subscriptions
               SET status = 'error', last_error = 'previous failure',
                   consecutive_errors = 1
               WHERE id = ?""",
            (int(errored.id),),
        )
        conn.commit()

    calls: list[str] = []

    async def fake_incremental_execute(*, conn, sub, sub_id_int, max_items):
        calls.append(sub.url)
        return FetchResult(ok=True, source_url=sub.url, items=[]), 0

    monkeypatch.setattr(
        "contents_hub.api._incremental_executor_execute",
        fake_incremental_execute,
    )

    default = asyncio.run(collect_all_active(cfg))
    assert default.total == 1
    assert calls == [active.url]

    calls.clear()
    manual = asyncio.run(collect_all_active(cfg, include_error=True))
    assert manual.total == 2
    assert manual.errors == 0
    assert calls == [active.url, errored.url]

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            "SELECT status, last_error, consecutive_errors FROM subscriptions WHERE id = ?",
            (int(errored.id),),
        ).fetchone()

    assert row == ("active", "", 0)


def test_collect_all_active_timeout_excludes_post_fetch_lenses(tmp_path, monkeypatch):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
    )

    async def fake_incremental_execute(*, conn, sub, sub_id_int, max_items):
        return (
            FetchResult(
                ok=True,
                source_url=sub.url,
                items=[
                    FetchedItem(
                        url="https://example.com/post-1",
                        title="Post 1",
                        content_html="body",
                    )
                ],
            ),
            0,
        )

    lens_calls: list[tuple[int, tuple[int, ...]]] = []

    async def slow_lens_evaluation(*, config, subscription_id, inserted_ids):
        lens_calls.append((subscription_id, inserted_ids))
        await asyncio.sleep(0.05)

    monkeypatch.setattr(
        "contents_hub.api._incremental_executor_execute",
        fake_incremental_execute,
    )
    monkeypatch.setattr(
        "contents_hub.api._evaluate_post_fetch_lenses",
        slow_lens_evaluation,
    )

    result = asyncio.run(
        collect_all_active(cfg, per_subscription_timeout_seconds=0.01)
    )

    assert result.total == 1
    assert result.errors == 0
    assert result.new == 1
    assert result.per_subscription[0].ok is True
    assert lens_calls and lens_calls[0][0] == int(sub.id)


def test_collect_all_active_times_out_one_subscription_and_continues(tmp_path, monkeypatch):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    slow = store.add(
        url="https://example.com/slow.xml",
        title="Slow Feed",
        source_type="rss.feed",
    )
    fast = store.add(
        url="https://example.com/fast.xml",
        title="Fast Feed",
        source_type="rss.feed",
    )
    calls: list[str] = []

    async def fake_incremental_execute(*, conn, sub, sub_id_int, max_items):
        calls.append(sub.url)
        if sub.url == slow.url:
            await asyncio.sleep(0.1)
        return FetchResult(ok=True, source_url=sub.url, items=[]), 0

    monkeypatch.setattr(
        "contents_hub.api._incremental_executor_execute",
        fake_incremental_execute,
    )

    result = asyncio.run(
        collect_all_active(cfg, per_subscription_timeout_seconds=0.01)
    )

    assert result.total == 2
    assert result.errors == 1
    assert result.per_subscription[0].url == slow.url
    assert result.per_subscription[0].failure_reason == "timeout"
    assert result.per_subscription[1].url == fast.url
    assert result.per_subscription[1].ok is True
    assert calls == [slow.url, fast.url]


def test_collect_all_active_concurrency_runs_subscriptions_in_parallel(
    tmp_path,
    monkeypatch,
):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    first = store.add(
        url="https://example.com/first.xml",
        title="First Feed",
        source_type="rss.feed",
    )
    second = store.add(
        url="https://example.com/second.xml",
        title="Second Feed",
        source_type="rss.feed",
    )

    started: list[str] = []
    conn_ids: list[int] = []
    active = 0
    max_active = 0
    both_started = asyncio.Event()

    async def fake_incremental_execute(*, conn, sub, sub_id_int, max_items):
        nonlocal active, max_active
        started.append(sub.url)
        conn_ids.append(id(conn))
        active += 1
        max_active = max(max_active, active)
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return FetchResult(ok=True, source_url=sub.url, items=[]), 0
        finally:
            active -= 1

    monkeypatch.setattr(
        "contents_hub.api._incremental_executor_execute",
        fake_incremental_execute,
    )

    result = asyncio.run(
        collect_all_active(
            cfg,
            per_subscription_timeout_seconds=1.0,
            concurrency=2,
        )
    )

    assert result.total == 2
    assert result.errors == 0
    assert max_active == 2
    assert len(set(conn_ids)) == 2
    assert [entry.url for entry in result.per_subscription] == [first.url, second.url]


def test_collect_all_active_concurrency_preserves_per_sub_timeout(
    tmp_path,
    monkeypatch,
):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    slow = store.add(
        url="https://example.com/slow.xml",
        title="Slow Feed",
        source_type="rss.feed",
    )
    fast = store.add(
        url="https://example.com/fast.xml",
        title="Fast Feed",
        source_type="rss.feed",
    )

    async def fake_incremental_execute(*, conn, sub, sub_id_int, max_items):
        if sub.url == slow.url:
            await asyncio.sleep(0.1)
        return FetchResult(ok=True, source_url=sub.url, items=[]), 0

    monkeypatch.setattr(
        "contents_hub.api._incremental_executor_execute",
        fake_incremental_execute,
    )

    result = asyncio.run(
        collect_all_active(
            cfg,
            per_subscription_timeout_seconds=0.01,
            concurrency=2,
        )
    )

    assert result.total == 2
    assert result.errors == 1
    assert result.per_subscription[0].url == slow.url
    assert result.per_subscription[0].failure_reason == "timeout"
    assert result.per_subscription[1].url == fast.url
    assert result.per_subscription[1].ok is True


def test_fetch_subscription_content_fetches_only_new_list_items(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg)
    store = SubscriptionStore(cfg)
    sub = store.add(
        url="https://example.com/feed.xml",
        title="Example Feed",
        source_type="rss.feed",
        config={"collection_prompt": "Only collect launch posts."},
    )

    published = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    runner = _SequenceRunner(
        [
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1",
                  "title_hint": "Post 1"
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """,
            """
            {
              "items": [
                {
                  "url": "https://example.com/post-1",
                  "title": "Post 1",
                  "summary": "summary",
                  "body_markdown": "body",
                  "published_at": "%s",
                  "body_status": "full",
                  "visible_metrics": {"comments": 7},
                  "outbound_urls": ["https://example.com/reference"],
                  "top_comments": [
                    {"author": "reader", "text": "This adds a useful caveat."}
                  ]
                }
              ],
              "errors": [],
              "failure_reason": null
            }
            """
            % published,
        ]
    )

    original = get_default_runner()
    try:
        set_default_runner(runner)  # type: ignore[arg-type]
        result = asyncio.run(fetch_subscription(cfg, sub.id, max_items=10))
    finally:
        set_default_runner(original)

    assert result.ok is True
    assert len(result.items) == 1
    assert len(runner.prompts) == 2
    assert "LIST_STRATEGY 만" in runner.prompts[0]
    assert "Only collect launch posts." in runner.prompts[0]
    assert "CONTENT_STRATEGY + METADATA" in runner.prompts[1]
    assert "Only collect launch posts." in runner.prompts[1]
    assert '"url": "https://example.com/post-1"' in runner.prompts[1]

    with sqlite3.connect(cfg.meta_path / "state.db") as conn:
        row = conn.execute(
            """SELECT title, body, published_at, metadata_json
               FROM raw_items WHERE subscription_id = ?""",
            (int(sub.id),),
        ).fetchone()

    assert row[:3] == ("Post 1", "body", published)
    metadata = json.loads(row[3])
    assert metadata["body_status"] == "full"
    assert metadata["visible_metrics"]["comments"] == 7
    assert metadata["outbound_urls"] == ["https://example.com/reference"]
    assert metadata["top_comments"][0]["text"] == "This adds a useful caveat."
