from __future__ import annotations

import subprocess
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from llm_wiki.api import collect_all_due, fetch_subscription
from llm_wiki.chromux import (
    PROFILE_IN_FOREGROUND_REASON,
    chromux_foreground_fetch,
    chromux_profile_state,
    open_chromux_headed,
)
from llm_wiki.config import WikiConfig
from llm_wiki.db import init_db
from llm_wiki.models import ListFetchResult
from llm_wiki.subscriptions import SubscriptionStore
from llm_wiki.web.app import create_app


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    (tmp_path / ".llm-wiki").mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


def test_chromux_profile_state_detects_modes(monkeypatch):
    def fake_run(args, **kwargs):
        assert args[-1] == "ps"
        return SimpleNamespace(stdout="llm-wiki 9222\nother 9444\n")

    class FakeResponse:
        def __init__(self, user_agent: str):
            self.user_agent = user_agent

        def json(self):
            return {"User-Agent": self.user_agent}

    monkeypatch.setattr("llm_wiki.chromux.subprocess.run", fake_run)
    monkeypatch.setattr(
        "llm_wiki.chromux.httpx.get",
        lambda url, timeout: FakeResponse("Mozilla HeadlessChrome/120"),
    )
    assert chromux_profile_state("llm-wiki") == "headless"

    monkeypatch.setattr(
        "llm_wiki.chromux.httpx.get",
        lambda url, timeout: FakeResponse("Mozilla Chrome/120"),
    )
    assert chromux_profile_state("llm-wiki") == "headed"


def test_open_chromux_headed_requires_confirm_then_kills_and_opens(monkeypatch):
    run_calls: list[list[str]] = []
    popen_calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        run_calls.append(args)
        if args[-1] == "ps":
            return SimpleNamespace(stdout="llm-wiki 9222\n")
        return SimpleNamespace(stdout="", returncode=0)

    class FakeResponse:
        def json(self):
            return {"User-Agent": "Mozilla HeadlessChrome/120"}

    monkeypatch.setattr("llm_wiki.chromux.subprocess.run", fake_run)
    monkeypatch.setattr("llm_wiki.chromux.httpx.get", lambda url, timeout: FakeResponse())
    monkeypatch.setattr(
        "llm_wiki.chromux.subprocess.Popen",
        lambda args, **kwargs: popen_calls.append(args),
    )

    first = open_chromux_headed("https://example.com", session="login-1")
    assert first["status"] == "needs_confirm"
    assert not any(call[-2:] == ["kill", "llm-wiki"] for call in run_calls)

    second = open_chromux_headed(
        "https://example.com", session="login-1", confirmed=True
    )
    assert second["status"] == "opened"
    assert any(call[-2:] == ["kill", "llm-wiki"] for call in run_calls)
    assert [call[1:] for call in popen_calls] == [
        ["launch", "llm-wiki"],
        ["open", "login-1", "https://example.com"],
    ]


def test_open_chromux_headed_reopens_blank_tab_when_already_headed(monkeypatch):
    popen_calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        if args[-1] == "ps":
            return SimpleNamespace(stdout="llm-wiki 9222\n")
        return SimpleNamespace(stdout="", returncode=0)

    class FakeResponse:
        def json(self):
            return {"User-Agent": "Mozilla Chrome/120"}

    monkeypatch.setattr("llm_wiki.chromux.subprocess.run", fake_run)
    monkeypatch.setattr("llm_wiki.chromux.httpx.get", lambda url, timeout: FakeResponse())
    monkeypatch.setattr(
        "llm_wiki.chromux.subprocess.Popen",
        lambda args, **kwargs: popen_calls.append(args),
    )

    result = open_chromux_headed(None, session="settings")

    assert result["status"] == "launched"
    assert [call[1:] for call in popen_calls] == [
        ["open", "settings", "about:blank"],
    ]


async def test_collect_due_pauses_chromux_fetch_when_profile_is_headed(
    vault, monkeypatch
):
    store = SubscriptionStore(vault)
    store.add(
        url="https://example.com/",
        title="Example",
        source_type="webpage",
        config={"fetch_method": "browser"},
    )

    async def fail_execute(*args, **kwargs):
        raise AssertionError("executor should not run while profile is headed")

    monkeypatch.setattr("llm_wiki.api.is_chromux_profile_in_foreground", lambda: True)
    monkeypatch.setattr("llm_wiki.api._executor_execute", fail_execute)
    monkeypatch.setattr("llm_wiki.api._executor_list_items", fail_execute)

    result = await collect_all_due(vault)

    assert result.total == 1
    assert result.errors == 0
    assert result.skipped == 1
    assert result.per_subscription[0].error == "paused: profile_in_foreground"
    assert result.per_subscription[0].failure_reason == PROFILE_IN_FOREGROUND_REASON


async def test_fetch_subscription_closes_tracked_chromux_session(vault, monkeypatch):
    store = SubscriptionStore(vault)
    sub = store.add(
        url="https://example.com/",
        title="Example",
        source_type="webpage",
        config={"fetch_method": "browser"},
    )
    closed_sessions: list[str] = []

    async def fake_list_items(sub, **kwargs):
        from llm_wiki.tools.browser import chromux_navigate_handler

        payload = await chromux_navigate_handler(
            url="https://example.com/", session_id="wiki-test"
        )
        assert '"ok": true' in payload
        return ListFetchResult(ok=True, source_url=sub.url, items=[])

    def fake_run_chromux(args, *, env=None, timeout):
        assert args == ["chromux", "open", "wiki-test", "https://example.com/"]
        assert env["CHROMUX_PROFILE"] == "llm-wiki"
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    def fake_close(session_id, **kwargs):
        closed_sessions.append(session_id)
        return subprocess.CompletedProcess(["chromux", "close", session_id], 0)

    monkeypatch.setattr("llm_wiki.api.is_chromux_profile_in_foreground", lambda: False)
    monkeypatch.setattr(
        "llm_wiki.tools.browser.is_chromux_profile_in_foreground", lambda profile: False
    )
    monkeypatch.setattr("llm_wiki.api._executor_list_items", fake_list_items)
    monkeypatch.setattr("llm_wiki.tools.browser._run_chromux", fake_run_chromux)
    monkeypatch.setattr("llm_wiki.chromux.close_chromux_session", fake_close)

    result = await fetch_subscription(vault, sub.url)

    assert result.ok is True
    assert closed_sessions == ["wiki-test"]


async def test_foreground_fetch_context_allows_headed_chromux_navigation(monkeypatch):
    def fake_run_chromux(args, *, env=None, timeout):
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "llm_wiki.tools.browser.is_chromux_profile_in_foreground", lambda profile: True
    )
    monkeypatch.setattr("llm_wiki.tools.browser._run_chromux", fake_run_chromux)

    from llm_wiki.tools.browser import chromux_navigate_handler

    paused = json.loads(
        await chromux_navigate_handler(
            url="https://www.linkedin.com/", session_id="wiki-linkedin"
        )
    )
    assert paused["reason"] == PROFILE_IN_FOREGROUND_REASON

    async with chromux_foreground_fetch():
        allowed = json.loads(
            await chromux_navigate_handler(
                url="https://www.linkedin.com/", session_id="wiki-linkedin"
            )
        )

    assert allowed["ok"] is True


async def test_chromux_browser_tools_match_current_cli_and_session_alias(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append(args)
        assert env["CHROMUX_PROFILE"] == "llm-wiki"
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "llm_wiki.tools.browser.is_chromux_profile_in_foreground", lambda profile: False
    )
    monkeypatch.setattr("llm_wiki.tools.browser._run_chromux", fake_run_chromux)

    from llm_wiki.tools.browser import chromux_extract_handler, chromux_navigate_handler

    opened = json.loads(
        await chromux_navigate_handler(
            url="https://www.linkedin.com/feed/", session="exec-linkedin-001"
        )
    )
    text = json.loads(
        await chromux_extract_handler(session="exec-linkedin-001", mode="text")
    )
    links = json.loads(
        await chromux_extract_handler(
            session="exec-linkedin-001", mode="links", selector="[data-urn]"
        )
    )

    assert opened["ok"] is True
    assert text["ok"] is True
    assert links["ok"] is True
    assert calls == [
        ["chromux", "open", "exec-linkedin-001", "https://www.linkedin.com/feed/"],
        ["chromux", "snapshot", "exec-linkedin-001"],
        [
            "chromux",
            "eval",
            "exec-linkedin-001",
            "JSON.stringify(Array.from(document.querySelectorAll(\"[data-urn] "
            "a[href]\")).map(a => ({text: (a.innerText || '').trim(), href: "
            "a.href})).filter(x => x.href).slice(0, 200))",
        ],
    ]


async def test_chromux_extract_supports_structured_attributes(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append(args)
        assert env["CHROMUX_PROFILE"] == "llm-wiki"
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='[{"data-urn":"urn:li:activity:1","text":"First"}]',
            stderr="",
        )

    monkeypatch.setattr("llm_wiki.tools.browser._run_chromux", fake_run_chromux)

    from llm_wiki.tools.browser import chromux_extract_handler

    result = json.loads(
        await chromux_extract_handler(
            session="exec-linkedin-001",
            selector="[data-urn]",
            attributes=["data-urn"],
            multiple=True,
            limit=25,
        )
    )

    assert result["ok"] is True
    assert result["items"] == [{"data-urn": "urn:li:activity:1", "text": "First"}]
    assert result["attributes"] == ["data-urn"]
    assert result["multiple"] is True
    assert calls[0][:3] == ["chromux", "eval", "exec-linkedin-001"]
    assert "document.querySelectorAll(selector)" in calls[0][3]
    assert "data-urn" in calls[0][3]


def test_settings_resume_background_kills_foreground_profile(vault, monkeypatch):
    monkeypatch.setattr(
        "llm_wiki.web.app.kill_chromux_profile",
        lambda profile: {"status": "killed", "previous_state": "headed", "error": None},
    )

    client = TestClient(create_app(vault))
    resp = client.post("/settings/browser/resume-background")

    assert resp.status_code == 200
    assert resp.json()["status"] == "killed"
