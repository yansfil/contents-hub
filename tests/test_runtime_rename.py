from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from contents_hub.cli import build_parser, main
from contents_hub.config import WikiConfig, load_config, resolve_vault_path, save_schedule_defaults
from contents_hub.db import init_db
from contents_hub.launchd import generate_plist, install, status, uninstall
from contents_hub.subscriptions import SubscriptionStore
from contents_hub.web.app import create_app


def test_vault_env_precedence_prefers_canonical_over_legacy(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    for path in (explicit, canonical, legacy):
        path.mkdir()

    monkeypatch.setenv("CONTENTS_HUB_VAULT", str(canonical))
    monkeypatch.setenv("LLM_WIKI_VAULT", str(legacy))

    assert resolve_vault_path() == canonical.resolve()
    assert resolve_vault_path(explicit) == explicit.resolve()

    monkeypatch.delenv("CONTENTS_HUB_VAULT")
    assert resolve_vault_path() == legacy.resolve()


def test_load_config_uses_legacy_metadata_without_orphaning_state(tmp_path):
    legacy_meta = tmp_path / ".llm-wiki"
    legacy_meta.mkdir()
    (legacy_meta / "state.db").write_bytes(b"legacy")

    cfg = load_config(tmp_path)

    assert cfg.meta_dir == ".llm-wiki"
    assert cfg.meta_path == legacy_meta


def test_legacy_metadata_database_is_read_without_creating_empty_canonical_state(tmp_path):
    legacy_cfg = WikiConfig(vault_path=tmp_path, meta_dir=".llm-wiki")
    init_db(legacy_cfg).close()
    SubscriptionStore(legacy_cfg).add(
        "https://example.com/feed.xml",
        title="Legacy Feed",
        source_type="rss.feed",
    )

    cfg = load_config(tmp_path)
    subs = SubscriptionStore(cfg).list_all()

    assert cfg.meta_dir == ".llm-wiki"
    assert [sub.title for sub in subs] == ["Legacy Feed"]
    assert (tmp_path / ".llm-wiki" / "state.db").exists()
    assert not (tmp_path / ".contents-hub" / "state.db").exists()


def test_load_config_prefers_canonical_metadata_when_present(tmp_path):
    (tmp_path / ".llm-wiki").mkdir()
    canonical_meta = tmp_path / ".contents-hub"
    canonical_meta.mkdir()

    cfg = load_config(tmp_path)

    assert cfg.meta_dir == ".contents-hub"
    assert cfg.meta_path == canonical_meta


def test_config_reads_legacy_yaml_and_writes_canonical_yaml(tmp_path):
    (tmp_path / ".llm-wiki.yaml").write_text(
        yaml.safe_dump({"schedule": {"defaults": {"rss": 7}}, "keep": "yes"}),
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    assert cfg.schedule.interval_for("rss") == 7
    assert cfg.config_file == tmp_path / ".contents-hub.yaml"

    save_schedule_defaults(
        tmp_path,
        global_interval=None,
        global_cron="*/15 * * * *",
        per_type={"rss": 9},
    )

    canonical = yaml.safe_load((tmp_path / ".contents-hub.yaml").read_text())
    assert canonical["keep"] == "yes"
    assert canonical["schedule"]["defaults"]["rss"] == 9
    assert canonical["schedule"]["global_cron"] == "*/15 * * * *"
    assert (tmp_path / ".llm-wiki.yaml").exists()


def test_init_creates_canonical_metadata_and_database(tmp_path, capsys):
    vault = tmp_path / "vault"

    rc = main(["init", str(vault)])

    out = capsys.readouterr().out
    assert rc == 0
    assert (vault / ".contents-hub").is_dir()
    assert (vault / ".contents-hub" / "state.db").exists()
    assert not (vault / ".llm-wiki").exists()
    assert "contents_hub" in out
    assert "contents-hub" in out


def test_cli_help_uses_contents_hub_branding():
    help_text = build_parser().format_help()

    assert "contents-hub" in help_text
    assert "llm-wiki" not in help_text


def test_installed_canonical_and_legacy_commands_work_and_legacy_json_stdout_is_clean(tmp_path):
    canonical_vault = tmp_path / "canonical"
    legacy_vault = tmp_path / "legacy"
    canonical_vault.mkdir()
    legacy_vault.mkdir()
    init_db(WikiConfig(vault_path=canonical_vault)).close()
    init_db(WikiConfig(vault_path=legacy_vault)).close()

    contents_hub = shutil.which("contents-hub")
    llm_wiki = shutil.which("llm-wiki")
    assert contents_hub is not None
    assert llm_wiki is not None

    help_result = subprocess.run(
        [contents_hub, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "contents-hub" in help_result.stdout

    canonical_json = subprocess.run(
        [contents_hub, "--vault", str(canonical_vault), "sub", "list", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert canonical_json.returncode == 0
    assert json.loads(canonical_json.stdout) == []

    env = {**os.environ, "LLM_WIKI_VAULT": str(legacy_vault)}
    env.pop("CONTENTS_HUB_VAULT", None)
    legacy_json = subprocess.run(
        [llm_wiki, "sub", "list", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert legacy_json.returncode == 0
    assert json.loads(legacy_json.stdout) == []
    assert "LLM_WIKI_VAULT" not in legacy_json.stdout
    assert "llm-wiki" not in legacy_json.stdout


def test_dashboard_uses_contents_hub_branding(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()

    app = create_app(cfg)
    client = TestClient(app)

    response = client.get("/")

    assert app.title == "contents-hub Dashboard"
    assert response.status_code == 200
    assert "contents-hub" in response.text
    assert "llm-wiki" not in response.text


def test_direct_init_db_uses_canonical_metadata_for_new_configs(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)

    conn = init_db(cfg)
    conn.close()

    assert (tmp_path / ".contents-hub" / "state.db").exists()
    assert not (tmp_path / ".llm-wiki" / "state.db").exists()


def test_launchd_plist_uses_canonical_label_logs_and_env(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)

    plist = plistlib.loads(generate_plist(cfg).encode("utf-8"))

    assert plist["Label"] == "com.contents-hub.daemon"
    assert plist["ProgramArguments"][2] == "contents_hub.daemon"
    assert plist["EnvironmentVariables"] == {"CONTENTS_HUB_VAULT": str(tmp_path)}
    assert ".contents-hub/daemon.log" in plist["StandardOutPath"]
    assert ".contents-hub/daemon-error.log" in plist["StandardErrorPath"]


def test_launchd_install_unloads_and_removes_legacy_plist(tmp_path, monkeypatch):
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    legacy_plist = launch_agents / "com.llm-wiki.daemon.plist"
    legacy_plist.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    msg = install(WikiConfig(vault_path=tmp_path))

    canonical_plist = launch_agents / "com.contents-hub.daemon.plist"
    assert canonical_plist.exists()
    assert not legacy_plist.exists()
    assert ["launchctl", "unload", str(legacy_plist)] in calls
    assert ["launchctl", "load", str(canonical_plist)] in calls
    assert "com.contents-hub.daemon.plist" in msg


def test_launchd_status_reports_legacy_loaded_daemon(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["launchctl", "list"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            "123\t0\tcom.llm-wiki.daemon\n",
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    msg = status()

    assert "Legacy daemon com.llm-wiki.daemon is running" in msg
    assert "com.contents-hub.daemon" in msg


def test_launchd_status_reports_duplicate_canonical_and_legacy_daemons(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["launchctl", "list"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            "123\t0\tcom.llm-wiki.daemon\n456\t0\tcom.contents-hub.daemon\n",
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    msg = status()

    assert "Duplicate daemons loaded" in msg
    assert "com.contents-hub.daemon" in msg
    assert "com.llm-wiki.daemon" in msg


def test_launchd_uninstall_removes_canonical_and_legacy_plists(tmp_path, monkeypatch):
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    canonical_plist = launch_agents / "com.contents-hub.daemon.plist"
    legacy_plist = launch_agents / "com.llm-wiki.daemon.plist"
    canonical_plist.write_text("<plist/>", encoding="utf-8")
    legacy_plist.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    msg = uninstall()

    assert not canonical_plist.exists()
    assert not legacy_plist.exists()
    assert ["launchctl", "unload", str(canonical_plist)] in calls
    assert ["launchctl", "unload", str(legacy_plist)] in calls
    assert "legacy Plist removed" in msg
