from __future__ import annotations

import asyncio
import json

from contents_hub.executor import list_items
from contents_hub.platform_lists import (
    linkedin_list_items_from_records,
    x_list_items_from_article_records,
)


class _UnexpectedRunner:
    def __init__(self):
        self.prompts: list[str] = []

    async def run(self, prompt, *, max_turns=30, timeout=600.0):
        self.prompts.append(prompt)
        raise AssertionError("agent fallback should not run")


def test_linkedin_records_normalize_activity_urns():
    items = linkedin_list_items_from_records(
        [
            {
                "data-urn": "urn:li:activity:7448514963196911616",
                "text": "Satya Nadella\n1h\nUpdate text",
            },
            {
                "data-urn": "urn:li:activity:7448514963196911616",
                "text": "duplicate",
            },
            {"data-urn": "urn:li:comment:123", "text": "not an activity"},
        ]
    )

    assert [item.url for item in items] == [
        "https://www.linkedin.com/feed/update/urn:li:activity:7448514963196911616/"
    ]
    assert items[0].item_key == "linkedin:activity:7448514963196911616"
    assert items[0].card_text == "Satya Nadella\n1h\nUpdate text"


def test_x_article_scoped_parser_keeps_original_after_pinned():
    items = x_list_items_from_article_records(
        [
            {
                "text": "Pinned\nGarry Tan\n@garrytan\nAug 12, 2023",
                "outerHTML": (
                    '<article><a href="/garrytan/status/1690019429965537281">'
                    '<time datetime="2023-08-11T15:16:45.000Z"></time></a></article>'
                ),
            },
            {
                "text": "Garry Tan\n@garrytan\n10h\nGBrain update\n14 PRs merged",
                "outerHTML": (
                    '<article><a href="/garrytan/status/2054055071017538028">'
                    '<time datetime="2026-05-12T04:24:26.000Z"></time></a></article>'
                ),
            },
        ],
        profile_url="https://x.com/garrytan",
    )

    assert [item.url for item in items] == [
        "https://x.com/garrytan/status/2054055071017538028"
    ]
    assert items[0].item_key == "x:status:2054055071017538028"
    assert items[0].published_hint == "2026-05-12T04:24:26.000Z"


def test_x_article_scoped_parser_keeps_reposts_but_excludes_unrelated_statuses():
    items = x_list_items_from_article_records(
        [
            {
                "text": "Garry Tan reposted\nSomeone Else\n@other\n2h\npost",
                "outerHTML": (
                    '<article><a href="/other/status/111">'
                    '<time datetime="2026-05-12T12:00:00.000Z"></time></a></article>'
                ),
            },
            {
                "text": "Garry Tan\n@garrytan\n1h\nquoting someone",
                "outerHTML": (
                    '<article><a href="/other/status/222"></a>'
                    '<a href="/garrytan/status/333">'
                    '<time datetime="2026-05-12T13:00:00.000Z"></time></a></article>'
                ),
            },
        ],
        profile_url="https://x.com/garrytan",
    )

    assert [item.url for item in items] == [
        "https://x.com/other/status/111",
        "https://x.com/garrytan/status/333",
    ]
    assert items[0].source_payload["is_repost"] is True
    assert items[0].source_payload["status_author"] == "other"
    assert items[1].source_payload["is_repost"] is False


def test_x_direct_list_accepts_repost_when_no_originals(monkeypatch):
    async def fake_navigate_handler(**kwargs):
        return json.dumps({"ok": True, "session_id": "x-empty"})

    async def fake_extract_handler(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "items": [
                    {
                        "text": "Pinned\nGarry Tan\n@garrytan",
                        "outerHTML": (
                            '<article><a href="/garrytan/status/1690019429965537281">'
                            '<time datetime="2023-08-11T15:16:45.000Z"></time>'
                            "</a></article>"
                        ),
                    },
                    {
                        "text": "Garry Tan reposted\nSomeone Else\n@other",
                        "outerHTML": (
                            '<article><a href="/other/status/2054055071017538028">'
                            '<time datetime="2026-05-12T04:24:26.000Z"></time>'
                            "</a></article>"
                        ),
                    },
                ],
            }
        )

    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_navigate_handler",
        fake_navigate_handler,
    )
    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_extract_handler",
        fake_extract_handler,
    )
    monkeypatch.setattr(
        "contents_hub.platform_lists._scroll_x_timeline",
        lambda session_id: asyncio.sleep(0, result=False),
    )

    sub = type(
        "S",
        (),
        {
            "url": "https://x.com/garrytan",
            "source_type": "x.profile",
            "config": {},
        },
    )()
    runner = _UnexpectedRunner()

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert [item.url for item in result.items] == [
        "https://x.com/other/status/2054055071017538028"
    ]
    assert result.items[0].source_payload["is_repost"] is True
    assert runner.prompts == []


def test_x_direct_list_scrolls_when_first_view_has_no_originals(monkeypatch):
    calls = {"extract": 0, "scroll": 0}

    async def fake_navigate_handler(**kwargs):
        return json.dumps({"ok": True, "session_id": "x-scroll"})

    async def fake_extract_handler(**kwargs):
        calls["extract"] += 1
        if calls["extract"] == 1:
            records = [
                {
                    "text": "Pinned\nGarry Tan\n@garrytan",
                    "outerHTML": (
                        '<article><a href="/garrytan/status/1690019429965537281">'
                        '<time datetime="2023-08-11T15:16:45.000Z"></time>'
                        "</a></article>"
                    ),
                },
            ]
        else:
            records = [
                {
                    "text": "Garry Tan\n@garrytan\n1d\nGBrain update",
                    "outerHTML": (
                        '<article><a href="/garrytan/status/2054055071017538028">'
                        '<time datetime="2026-05-12T04:24:26.000Z"></time>'
                        "</a></article>"
                    ),
                }
            ]
        return json.dumps({"ok": True, "items": records})

    async def fake_scroll(session_id):
        calls["scroll"] += 1
        return True

    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_navigate_handler",
        fake_navigate_handler,
    )
    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_extract_handler",
        fake_extract_handler,
    )
    monkeypatch.setattr("contents_hub.platform_lists._scroll_x_timeline", fake_scroll)

    sub = type(
        "S",
        (),
        {
            "url": "https://x.com/garrytan",
            "source_type": "x.profile",
            "config": {},
        },
    )()
    runner = _UnexpectedRunner()

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert [item.url for item in result.items] == [
        "https://x.com/garrytan/status/2054055071017538028"
    ]
    assert calls == {"extract": 2, "scroll": 1}
    assert runner.prompts == []


def test_x_direct_list_uses_search_when_profile_has_no_originals(monkeypatch):
    navigated: list[str] = []

    async def fake_navigate_handler(**kwargs):
        url = kwargs["url"]
        navigated.append(url)
        session_id = "x-search" if "/search?" in url else "x-profile"
        return json.dumps({"ok": True, "session_id": session_id})

    async def fake_extract_handler(**kwargs):
        if kwargs["session_id"] == "x-profile":
            records = [
                {
                    "text": "Pinned\nGarry Tan\n@garrytan",
                    "outerHTML": (
                        '<article><a href="/garrytan/status/1690019429965537281">'
                        '<time datetime="2023-08-11T15:16:45.000Z"></time>'
                        "</a></article>"
                    ),
                },
            ]
        else:
            records = [
                {
                    "text": "Garry Tan\n@garrytan\n22h\nIt's not that AI lets you write code faster.",
                    "outerHTML": (
                        '<article><a href="/garrytan/status/2054065076852625582">'
                        '<time datetime="2026-05-12T05:04:11.000Z"></time>'
                        "</a></article>"
                    ),
                }
            ]
        return json.dumps({"ok": True, "items": records})

    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_navigate_handler",
        fake_navigate_handler,
    )
    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_extract_handler",
        fake_extract_handler,
    )
    monkeypatch.setattr(
        "contents_hub.platform_lists._scroll_x_timeline",
        lambda session_id: asyncio.sleep(0, result=False),
    )

    sub = type(
        "S",
        (),
        {
            "url": "https://x.com/garrytan",
            "source_type": "x.profile",
            "config": {},
        },
    )()
    runner = _UnexpectedRunner()

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert [item.url for item in result.items] == [
        "https://x.com/garrytan/status/2054065076852625582"
    ]
    assert navigated == [
        "https://x.com/garrytan",
        "https://x.com/search?q=from%3Agarrytan%20-filter%3Areplies&src=typed_query&f=live",
    ]
    assert runner.prompts == []


def test_linkedin_direct_list_uses_chromux_records_without_agent(monkeypatch):
    async def fake_navigate_handler(**kwargs):
        assert kwargs["url"] == "https://www.linkedin.com/in/example/recent-activity/all/"
        return json.dumps({"ok": True, "session_id": "li-example"})

    async def fake_extract_handler(**kwargs):
        assert kwargs["session_id"] == "li-example"
        assert kwargs["selector"] == "[data-urn]"
        return json.dumps(
            {
                "ok": True,
                "items": [
                    {
                        "data-urn": "urn:li:activity:7448514963196911616",
                        "text": "Example\n2h\nPost text",
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_navigate_handler",
        fake_navigate_handler,
    )
    monkeypatch.setattr(
        "contents_hub.platform_lists.chromux_extract_handler",
        fake_extract_handler,
    )

    sub = type(
        "S",
        (),
        {
            "url": "https://www.linkedin.com/in/example/",
            "source_type": "linkedin.profile",
            "config": {},
        },
    )()
    runner = _UnexpectedRunner()

    result = asyncio.run(list_items(sub, runner=runner))  # type: ignore[arg-type]

    assert result.ok is True
    assert [item.url for item in result.items] == [
        "https://www.linkedin.com/feed/update/urn:li:activity:7448514963196911616/"
    ]
    assert runner.prompts == []
