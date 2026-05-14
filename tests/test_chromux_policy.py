from __future__ import annotations

import subprocess
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from contents_hub.api import collect_all_due, fetch_subscription
from contents_hub.chromux import (
    AUTH_PROFILE_UNAVAILABLE_REASON,
    PROFILE_SWITCH_NEEDS_CONFIRM_REASON,
    ChromuxExplorationSessionError,
    chromux_exploration_session,
    chromux_foreground_fetch,
    chromux_profile_state,
    open_chromux_headed,
    prepare_chromux_for_background_fetch,
    resolve_chromux_profile,
)
from contents_hub.config import WikiConfig
from contents_hub.db import init_db
from contents_hub.models import ListFetchResult
from contents_hub.subscriptions import SubscriptionStore
from contents_hub.web.app import create_app


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    (tmp_path / ".llm-wiki").mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


def test_chromux_profile_state_detects_modes(monkeypatch):
    def fake_run(args, **kwargs):
        if args[-1] == "ps":
            return SimpleNamespace(stdout="llm-wiki 9222 111\nother 9444 222\n")
        if args[:3] == ["ps", "-p", "111"]:
            return SimpleNamespace(stdout="/Applications/Google Chrome --remote-debugging-port=9222")
        raise AssertionError(args)

    class FakeResponse:
        def __init__(self, user_agent: str):
            self.user_agent = user_agent

        def json(self):
            return {"User-Agent": self.user_agent}

    monkeypatch.setattr("contents_hub.chromux.subprocess.run", fake_run)
    monkeypatch.setattr(
        "contents_hub.chromux.httpx.get",
        lambda url, timeout: FakeResponse("Mozilla HeadlessChrome/120"),
    )
    assert chromux_profile_state("llm-wiki") == "headless"

    monkeypatch.setattr(
        "contents_hub.chromux.httpx.get",
        lambda url, timeout: FakeResponse("Mozilla Chrome/120"),
    )
    assert chromux_profile_state("llm-wiki") == "headed"

    def fake_hidden_run(args, **kwargs):
        if args[-1] == "ps":
            return SimpleNamespace(stdout="llm-wiki 9222 333\n")
        if args[:3] == ["ps", "-p", "333"]:
            return SimpleNamespace(
                stdout=(
                    "/Applications/Google Chrome --remote-debugging-port=9222 "
                    "--window-position=-10000,-10000 --window-size=1280,900"
                )
            )
        raise AssertionError(args)

    monkeypatch.setattr("contents_hub.chromux.subprocess.run", fake_hidden_run)
    assert chromux_profile_state("llm-wiki") == "hidden"


def test_resolve_chromux_profile_prefers_canonical_when_no_legacy_exists(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CHROMUX_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "contents_hub.chromux.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )

    assert resolve_chromux_profile() == "contents-hub"


def test_resolve_chromux_profile_reuses_existing_legacy_login_profile(
    tmp_path, monkeypatch
):
    legacy_profile = tmp_path / "llm-wiki"
    legacy_profile.mkdir()
    monkeypatch.setenv("CHROMUX_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "contents_hub.chromux.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )

    assert resolve_chromux_profile() == "llm-wiki"


def test_resolve_chromux_profile_prefers_running_canonical_over_legacy_dir(
    tmp_path, monkeypatch
):
    (tmp_path / "llm-wiki").mkdir()
    monkeypatch.setenv("CHROMUX_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "contents_hub.chromux.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="contents-hub 9222\n"),
    )

    assert resolve_chromux_profile() == "contents-hub"


def test_recipe_browser_profile_language_is_canonical_with_legacy_fallback():
    recipe_files = [
        *sorted((REPO_ROOT / "src/llm_wiki/recipes/templates").glob("*_prompt.md")),
        REPO_ROOT / "src/llm_wiki/recipes/seed/linkedin.md",
        REPO_ROOT / "src/llm_wiki/recipes/seed/twitter.md",
    ]

    for path in recipe_files:
        text = path.read_text(encoding="utf-8")
        assert "contents-hub" in text
        if "llm-wiki" in text:
            assert "legacy" in text or "fallback" in text


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

    monkeypatch.setattr("contents_hub.chromux.subprocess.run", fake_run)
    monkeypatch.setattr("contents_hub.chromux.httpx.get", lambda url, timeout: FakeResponse())
    monkeypatch.setattr(
        "contents_hub.chromux.subprocess.Popen",
        lambda args, **kwargs: popen_calls.append(args),
    )

    first = open_chromux_headed("https://example.com", session="login-1")
    assert first["status"] == "needs_confirm"
    assert "Background browser work" in first["error"]
    assert "headless mode" in first["error"]
    assert "Any active fetch" in first["error"]
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


def test_open_chromux_headed_requires_confirm_before_killing_hidden(monkeypatch):
    run_calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        run_calls.append(args)
        if args[-1] == "ps":
            return SimpleNamespace(stdout="llm-wiki 9222 333\n")
        if args[:3] == ["ps", "-p", "333"]:
            return SimpleNamespace(
                stdout=(
                    "/Applications/Google Chrome --remote-debugging-port=9222 "
                    "--window-position=-10000,-10000"
                )
            )
        return SimpleNamespace(stdout="", returncode=0)

    class FakeResponse:
        def json(self):
            return {"User-Agent": "Mozilla Chrome/120"}

    monkeypatch.setattr("contents_hub.chromux.subprocess.run", fake_run)
    monkeypatch.setattr("contents_hub.chromux.httpx.get", lambda url, timeout: FakeResponse())
    monkeypatch.setattr(
        "contents_hub.chromux.subprocess.Popen",
        lambda args, **kwargs: None,
    )

    first = open_chromux_headed("https://example.com", session="login-1")
    assert first["status"] == "needs_confirm"
    assert first["previous_state"] == "hidden"
    assert "hidden mode" in first["error"]
    assert not any(call[-2:] == ["kill", "llm-wiki"] for call in run_calls)

    second = open_chromux_headed(
        "https://example.com", session="login-1", confirmed=True
    )
    assert second["status"] == "opened"
    assert any(call[-2:] == ["kill", "llm-wiki"] for call in run_calls)


def test_open_chromux_headed_reopens_blank_tab_when_already_headed(monkeypatch):
    popen_calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        if args[-1] == "ps":
            return SimpleNamespace(stdout="llm-wiki 9222\n")
        return SimpleNamespace(stdout="", returncode=0)

    class FakeResponse:
        def json(self):
            return {"User-Agent": "Mozilla Chrome/120"}

    monkeypatch.setattr("contents_hub.chromux.subprocess.run", fake_run)
    monkeypatch.setattr("contents_hub.chromux.httpx.get", lambda url, timeout: FakeResponse())
    monkeypatch.setattr(
        "contents_hub.chromux.subprocess.Popen",
        lambda args, **kwargs: popen_calls.append(args),
    )

    result = open_chromux_headed(None, session="settings")

    assert result["status"] == "launched"
    assert [call[1:] for call in popen_calls] == [
        ["open", "settings", "about:blank"],
    ]


def test_background_fetch_prepare_fails_when_profile_is_foreground(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        "contents_hub.chromux.resolve_chromux_profile",
        lambda profile=None: "contents-hub",
    )
    monkeypatch.setattr(
        "contents_hub.chromux.is_chromux_profile_in_foreground",
        lambda profile=None: True,
    )

    with caplog.at_level("WARNING", logger="contents_hub.chromux"):
        result = prepare_chromux_for_background_fetch()

    assert result["ok"] is False
    assert result["status"] == "profile_in_foreground"
    assert result["profile"] == "contents-hub"
    assert "foreground/headed" in result["error"]
    assert "blocked:" in result["error"]
    assert "background chromux fetch blocked" in caplog.text


def test_background_fetch_prepare_allows_hidden_profile(monkeypatch):
    monkeypatch.setattr(
        "contents_hub.chromux.resolve_chromux_profile",
        lambda profile=None: "contents-hub",
    )
    monkeypatch.setattr(
        "contents_hub.chromux.chromux_profile_state",
        lambda profile=None: "hidden",
    )

    result = prepare_chromux_for_background_fetch()

    assert result == {
        "ok": True,
        "status": "ready",
        "profile": "contents-hub",
        "error": None,
    }


async def test_collect_due_fails_when_profile_is_foreground(
    vault, monkeypatch
):
    store = SubscriptionStore(vault)
    store.add(
        url="https://example.com/",
        title="Example",
        source_type="webpage",
        config={"fetch_method": "browser"},
    )

    async def fake_list_items(sub, **kwargs):
        raise AssertionError("background fetch should not run while foreground owns profile")

    monkeypatch.setattr(
        "contents_hub.api.prepare_chromux_for_background_fetch",
        lambda: {
            "ok": False,
            "status": "profile_in_foreground",
            "profile": "contents-hub",
            "error": "blocked: chromux profile 'contents-hub' is currently open in foreground/headed mode",
        },
    )
    monkeypatch.setattr("contents_hub.api._executor_list_items", fake_list_items)

    result = await collect_all_due(vault)

    assert result.total == 1
    assert result.errors == 1
    assert result.skipped == 0
    assert result.per_subscription[0].ok is False
    assert result.per_subscription[0].failure_reason == "blocked"
    assert "foreground/headed" in result.per_subscription[0].error


async def test_fetch_subscription_fails_when_profile_is_foreground(
    vault, monkeypatch
):
    store = SubscriptionStore(vault)
    sub = store.add(
        url="https://example.com/",
        title="Example",
        source_type="webpage",
        config={"fetch_method": "browser"},
    )
    async def fake_list_items(sub, **kwargs):
        raise AssertionError("background fetch should not run while foreground owns profile")

    monkeypatch.setattr(
        "contents_hub.api.prepare_chromux_for_background_fetch",
        lambda: {
            "ok": False,
            "status": "profile_in_foreground",
            "profile": "contents-hub",
            "error": "blocked: chromux profile 'contents-hub' is currently open in foreground/headed mode",
        },
    )
    monkeypatch.setattr("contents_hub.api._executor_list_items", fake_list_items)

    allowed = await fetch_subscription(vault, sub.url)
    assert allowed.ok is False
    assert "foreground/headed" in allowed.error
    assert allowed.failure_reason == "blocked"


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
        from contents_hub.tools.browser import chromux_navigate_handler

        payload = await chromux_navigate_handler(
            url="https://example.com/", session_id="wiki-test"
        )
        assert '"ok": true' in payload
        return ListFetchResult(ok=True, source_url=sub.url, items=[])

    def fake_run_chromux(args, *, env=None, timeout):
        assert args == ["chromux", "open", "wiki-test", "https://example.com/"]
        assert env["CHROMUX_PROFILE"] == "contents-hub"
        assert env["CHROMUX_LAUNCH_MODE"] == "hidden"
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    def fake_close(session_id, **kwargs):
        closed_sessions.append(session_id)
        return subprocess.CompletedProcess(["chromux", "close", session_id], 0)

    monkeypatch.setattr(
        "contents_hub.api.prepare_chromux_for_background_fetch",
        lambda: {"ok": True, "status": "ready", "error": None},
    )
    monkeypatch.setattr(
        "contents_hub.tools.browser.prepare_chromux_for_background_fetch",
        lambda: {"ok": True, "status": "ready", "error": None},
    )
    monkeypatch.setattr(
        "contents_hub.tools.browser.resolve_chromux_profile", lambda profile=None: "contents-hub"
    )
    monkeypatch.setattr("contents_hub.api._executor_list_items", fake_list_items)
    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)
    monkeypatch.setattr("contents_hub.chromux.close_chromux_session", fake_close)

    result = await fetch_subscription(vault, sub.url)

    assert result.ok is True
    assert closed_sessions == ["wiki-test"]


async def test_foreground_fetch_context_allows_headed_chromux_navigation(monkeypatch):
    prepare_calls: list[None] = []

    def fake_run_chromux(args, *, env=None, timeout):
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    def fake_prepare():
        prepare_calls.append(None)
        return {
            "ok": False,
            "status": "profile_in_foreground",
            "error": "foreground",
            "failure_reason": "profile_in_foreground",
        }

    monkeypatch.setattr("contents_hub.tools.browser.prepare_chromux_for_background_fetch", fake_prepare)
    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)

    from contents_hub.tools.browser import chromux_navigate_handler

    closed = json.loads(
        await chromux_navigate_handler(
            url="https://www.linkedin.com/", session_id="wiki-linkedin"
        )
    )
    assert closed["ok"] is False
    assert closed["failure_reason"] == "profile_in_foreground"
    assert prepare_calls == [None]

    async with chromux_foreground_fetch():
        allowed = json.loads(
            await chromux_navigate_handler(
                url="https://www.linkedin.com/", session_id="wiki-linkedin"
            )
        )

    assert allowed["ok"] is True
    assert prepare_calls == [None]


async def test_exploration_session_requires_confirmation_before_interrupting_headless(
    monkeypatch,
):
    monkeypatch.setattr("contents_hub.chromux.resolve_chromux_profile", lambda profile=None: "contents-hub")
    monkeypatch.setattr(
        "contents_hub.chromux.open_chromux_headed",
        lambda *args, **kwargs: {
            "status": "needs_confirm",
            "previous_state": "headless",
            "error": "background owner may fail",
        },
    )

    with pytest.raises(ChromuxExplorationSessionError) as exc_info:
        async with chromux_exploration_session(
            "https://threads.net/",
            session="explore-auth",
        ):
            raise AssertionError("confirmation blocker should stop the run")

    result = exc_info.value.to_result()
    assert result["status"] == "needs_confirm"
    assert result["profile"] == "contents-hub"
    assert result["previous_state"] == "headless"
    assert result["failure_reason"] == PROFILE_SWITCH_NEEDS_CONFIRM_REASON
    assert "background owner" in result["error"]


async def test_exploration_session_reports_profile_start_failure_separately(
    monkeypatch,
):
    monkeypatch.setattr("contents_hub.chromux.resolve_chromux_profile", lambda profile=None: "contents-hub")
    monkeypatch.setattr(
        "contents_hub.chromux.open_chromux_headed",
        lambda *args, **kwargs: {
            "status": "error",
            "previous_state": "not_running",
            "error": "chromux launch failed: missing profile",
        },
    )

    with pytest.raises(ChromuxExplorationSessionError) as exc_info:
        async with chromux_exploration_session(confirmed=True):
            raise AssertionError("profile error should stop the run")

    result = exc_info.value.to_result()
    assert result["status"] == "error"
    assert result["previous_state"] == "not_running"
    assert result["failure_reason"] == AUTH_PROFILE_UNAVAILABLE_REASON
    assert "chromux launch failed" in result["error"]


async def test_exploration_session_allows_foreground_tools_and_closes_run_sessions(
    monkeypatch,
):
    closed_sessions: list[str] = []
    prepare_calls: list[None] = []
    calls: list[tuple[list[str], str]] = []

    def fake_open(url, *, session=None, confirmed=False, profile=None):
        assert url == "https://threads.net/"
        assert session == "explore-run"
        assert confirmed is True
        assert profile == "contents-hub"
        return {"status": "opened", "previous_state": "headless", "error": None}

    def fake_prepare():
        prepare_calls.append(None)
        return {
            "ok": False,
            "status": "profile_in_foreground",
            "error": "foreground",
            "failure_reason": "profile_in_foreground",
        }

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append((args, env["CHROMUX_PROFILE"]))
        assert env["CHROMUX_LAUNCH_MODE"] == "hidden"
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    def fake_close(session_id, **kwargs):
        closed_sessions.append(session_id)
        return subprocess.CompletedProcess(["chromux", "close", session_id], 0)

    monkeypatch.setattr("contents_hub.chromux.resolve_chromux_profile", lambda profile=None: profile or "contents-hub")
    monkeypatch.setattr("contents_hub.tools.browser.resolve_chromux_profile", lambda profile=None: profile or "contents-hub")
    monkeypatch.setattr("contents_hub.chromux.open_chromux_headed", fake_open)
    monkeypatch.setattr("contents_hub.tools.browser.prepare_chromux_for_background_fetch", fake_prepare)
    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)
    monkeypatch.setattr("contents_hub.chromux.close_chromux_session", fake_close)

    from contents_hub.tools.browser import chromux_navigate_handler

    async with chromux_exploration_session(
        "https://threads.net/",
        session="explore-run",
        confirmed=True,
    ) as started:
        assert started["profile"] == "contents-hub"
        payload = json.loads(
            await chromux_navigate_handler(
                url="https://threads.net/@hoyeon",
                session_id="explore-feed",
            )
        )
        assert payload["ok"] is True

    assert prepare_calls == []
    assert calls == [
        (["chromux", "open", "explore-feed", "https://threads.net/@hoyeon"], "contents-hub")
    ]
    assert closed_sessions == ["explore-feed", "explore-run"]


async def test_chromux_browser_tools_match_current_cli_and_session_alias(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append(args)
        assert env["CHROMUX_PROFILE"] == "contents-hub"
        assert env["CHROMUX_LAUNCH_MODE"] == "hidden"
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "contents_hub.tools.browser.prepare_chromux_for_background_fetch",
        lambda: {"ok": True, "status": "ready", "error": None},
    )
    monkeypatch.setattr(
        "contents_hub.tools.browser.resolve_chromux_profile", lambda profile=None: "contents-hub"
    )
    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)

    from contents_hub.tools.browser import chromux_extract_handler, chromux_navigate_handler

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


async def test_chromux_browser_tool_env_reuses_legacy_profile_when_only_legacy_exists(
    tmp_path, monkeypatch
):
    (tmp_path / "llm-wiki").mkdir()
    calls: list[tuple[list[str], str]] = []

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append((args, env["CHROMUX_PROFILE"]))
        assert env["CHROMUX_LAUNCH_MODE"] == "hidden"
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setenv("CHROMUX_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "contents_hub.chromux.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )
    monkeypatch.setattr(
        "contents_hub.tools.browser.prepare_chromux_for_background_fetch",
        lambda: {"ok": True, "status": "ready", "error": None},
    )
    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)

    from contents_hub.tools.browser import chromux_navigate_handler

    opened = json.loads(
        await chromux_navigate_handler(
            url="https://www.linkedin.com/feed/", session="exec-linkedin-legacy"
        )
    )

    assert opened["ok"] is True
    assert calls == [
        (
            ["chromux", "open", "exec-linkedin-legacy", "https://www.linkedin.com/feed/"],
            "llm-wiki",
        )
    ]


async def test_chromux_extract_supports_structured_attributes(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append(args)
        assert env["CHROMUX_PROFILE"] == "contents-hub"
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='[{"data-urn":"urn:li:activity:1","text":"First"}]',
            stderr="",
        )

    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)
    monkeypatch.setattr(
        "contents_hub.tools.browser.resolve_chromux_profile", lambda profile=None: "contents-hub"
    )

    from contents_hub.tools.browser import chromux_extract_handler

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


async def test_chromux_scroll_uses_eval_without_bash(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append(args)
        assert env["CHROMUX_PROFILE"] == "contents-hub"
        return subprocess.CompletedProcess(args, 0, stdout="true", stderr="")

    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)
    monkeypatch.setattr(
        "contents_hub.tools.browser.resolve_chromux_profile", lambda profile=None: "contents-hub"
    )

    from contents_hub.tools.browser import chromux_scroll_handler

    result = json.loads(
        await chromux_scroll_handler(
            session="explore-threads-001",
            direction="down",
            pixels=1200,
            wait_ms=0,
        )
    )

    assert result["ok"] is True
    assert result["pixels"] == 1200
    assert calls == [
        ["chromux", "eval", "explore-threads-001", "window.scrollBy(0, 1200); true"]
    ]


async def test_chromux_scroll_extract_dedupes_across_scroll_passes(monkeypatch):
    calls: list[list[str]] = []
    extract_outputs = [
        '[{"href":"https://example.com/a","text":"A"},{"href":"https://example.com/b","text":"B"}]',
        '[{"href":"https://example.com/b","text":"B again"},{"href":"https://example.com/c","text":"C"}]',
    ]

    def fake_run_chromux(args, *, env=None, timeout):
        calls.append(args)
        assert env["CHROMUX_PROFILE"] == "contents-hub"
        if "scrollBy" in args[3]:
            return subprocess.CompletedProcess(args, 0, stdout="true", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=extract_outputs.pop(0), stderr="")

    monkeypatch.setattr("contents_hub.tools.browser._run_chromux", fake_run_chromux)
    monkeypatch.setattr(
        "contents_hub.tools.browser.resolve_chromux_profile", lambda profile=None: "contents-hub"
    )

    from contents_hub.tools.browser import chromux_scroll_extract_handler

    result = json.loads(
        await chromux_scroll_extract_handler(
            session="explore-threads-001",
            selector="article",
            attributes=["href", "text"],
            unique_by="href",
            max_scrolls=1,
            limit_per_pass=10,
            wait_ms=0,
        )
    )

    assert result["ok"] is True
    assert result["item_count"] == 3
    assert [item["href"] for item in result["items"]] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert result["new_counts"] == [2, 1]
    assert calls[0][:3] == ["chromux", "eval", "explore-threads-001"]
    assert "document.querySelectorAll(selector)" in calls[0][3]
    assert calls[1] == [
        "chromux",
        "eval",
        "explore-threads-001",
        "window.scrollBy(0, 1000); true",
    ]


async def test_append_checkpoint_writes_jsonl_batch(tmp_path):
    from contents_hub.tools.checkpoint import append_checkpoint_handler

    checkpoint_path = tmp_path / "run-1.jsonl"
    result = json.loads(
        await append_checkpoint_handler(
            path=str(checkpoint_path),
            items=[
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/b", "title": "B"},
            ],
        )
    )

    assert result == {
        "ok": True,
        "path": str(checkpoint_path),
        "items_appended": 2,
    }
    assert checkpoint_path.read_text(encoding="utf-8").splitlines() == [
        '{"url": "https://example.com/a", "title": "A"}',
        '{"url": "https://example.com/b", "title": "B"}',
    ]


def test_settings_resume_background_kills_foreground_profile(vault, monkeypatch):
    monkeypatch.setattr(
        "contents_hub.web.app.kill_chromux_profile",
        lambda profile=None: {"status": "killed", "previous_state": "headed", "error": None},
    )

    client = TestClient(create_app(vault))
    resp = client.post("/settings/browser/resume-background")

    assert resp.status_code == 200
    assert resp.json()["status"] == "killed"
