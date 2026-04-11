"""Tests for config module — schedule defaults, YAML loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llm_wiki.config import ScheduleConfig, WikiConfig, load_config


# ---------------------------------------------------------------------------
# ScheduleConfig
# ---------------------------------------------------------------------------


class TestScheduleConfig:
    def test_builtin_defaults(self):
        cfg = ScheduleConfig()
        assert cfg.interval_for("rss") == 30
        assert cfg.interval_for("youtube") == 60
        assert cfg.interval_for("twitter") == 15
        assert cfg.interval_for("browser") == 0

    def test_custom_defaults(self):
        cfg = ScheduleConfig(defaults={"rss": 10, "youtube": 120, "twitter": 15, "browser": 0})
        assert cfg.interval_for("rss") == 10
        assert cfg.interval_for("youtube") == 120

    def test_unknown_type_falls_back_to_30(self):
        cfg = ScheduleConfig()
        assert cfg.interval_for("unknown") == 30

    def test_global_interval_overrides_all(self):
        cfg = ScheduleConfig(global_interval=5)
        assert cfg.interval_for("rss") == 5
        assert cfg.interval_for("youtube") == 5
        assert cfg.interval_for("browser") == 5

    def test_global_cron(self):
        cfg = ScheduleConfig(global_cron="0 */2 * * *")
        assert cfg.cron_for("rss") == "0 */2 * * *"
        assert cfg.cron_for("youtube") == "0 */2 * * *"

    def test_no_cron_by_default(self):
        cfg = ScheduleConfig()
        assert cfg.cron_for("rss") is None

    def test_from_dict_empty(self):
        cfg = ScheduleConfig.from_dict({})
        assert cfg.interval_for("rss") == 30  # built-in default

    def test_from_dict_with_overrides(self):
        cfg = ScheduleConfig.from_dict({
            "defaults": {"rss": 15, "youtube": 30},
            "global_interval": None,
        })
        assert cfg.interval_for("rss") == 15
        assert cfg.interval_for("youtube") == 30
        assert cfg.interval_for("twitter") == 15  # built-in preserved

    def test_from_dict_ignores_invalid_values(self):
        cfg = ScheduleConfig.from_dict({
            "defaults": {"rss": "not_a_number", "youtube": -1},
        })
        assert cfg.interval_for("rss") == 30  # invalid → built-in
        assert cfg.interval_for("youtube") == 60  # negative → built-in


# ---------------------------------------------------------------------------
# WikiConfig with YAML loading
# ---------------------------------------------------------------------------


class TestLoadConfigWithYAML:
    def test_loads_schedule_from_yaml(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()

        config_data = {
            "schedule": {
                "defaults": {"rss": 10, "youtube": 45},
                "global_cron": "*/20 * * * *",
            }
        }
        (vault / ".llm-wiki.yaml").write_text(
            yaml.dump(config_data), encoding="utf-8"
        )

        cfg = load_config(str(vault))
        assert cfg.schedule.interval_for("rss") == 10
        assert cfg.schedule.interval_for("youtube") == 45
        assert cfg.schedule.global_cron == "*/20 * * * *"

    def test_no_yaml_uses_builtin_defaults(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()

        cfg = load_config(str(vault))
        assert cfg.schedule.interval_for("rss") == 30
        assert cfg.schedule.global_interval is None

    def test_empty_yaml_uses_builtin_defaults(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".llm-wiki.yaml").write_text("", encoding="utf-8")

        cfg = load_config(str(vault))
        assert cfg.schedule.interval_for("rss") == 30

    def test_config_file_property(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg = WikiConfig(vault_path=vault)
        assert cfg.config_file == vault / ".llm-wiki.yaml"
