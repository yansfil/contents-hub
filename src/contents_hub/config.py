"""
Configuration and vault path resolution for contents-hub.

Reads `.contents-hub.yaml` from the vault root, then applies environment/CWD
vault resolution.

Schedule defaults can be overridden in `.contents-hub.yaml`:

    schedule:
      defaults:
        rss: 30          # minutes
        youtube: 60
        twitter: 15
        webpage: 1440    # daily
      global_interval: null  # override all source types (minutes)
      global_cron: null      # override all source types (cron expr)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from contents_hub.naming import CONFIG_FILE, METADATA_DIR, VAULT_ENV_VARS
from contents_hub.source_types import schedule_defaults

logger = logging.getLogger(__name__)

# Source-type default polling intervals (minutes)
_BUILTIN_DEFAULTS: dict[str, int] = schedule_defaults()


@dataclass(frozen=True)
class ScheduleConfig:
    """Schedule polling configuration.

    Provides per-source-type default intervals and optional global overrides.
    """

    defaults: dict[str, int] = field(default_factory=lambda: dict(_BUILTIN_DEFAULTS))
    global_interval: Optional[int] = None    # if set, overrides all per-type defaults
    global_cron: Optional[str] = None        # if set, applies cron to all schedules

    def interval_for(self, source_type: str) -> int:
        """Return the polling interval in minutes for a source type.

        Priority:
            1. global_interval (if set)
            2. per-type override from config
            3. built-in default
        """
        if self.global_interval is not None:
            return self.global_interval
        return self.defaults.get(source_type, _BUILTIN_DEFAULTS.get(source_type, 30))

    def cron_for(self, source_type: str) -> Optional[str]:
        """Return the cron expression for a source type (if any)."""
        return self.global_cron

    @classmethod
    def from_dict(cls, data: dict) -> ScheduleConfig:
        """Create from a YAML-loaded dict."""
        defaults = dict(_BUILTIN_DEFAULTS)
        if raw_defaults := data.get("defaults"):
            for key, val in raw_defaults.items():
                if isinstance(val, int) and val >= 0:
                    defaults[key] = val
        return cls(
            defaults=defaults,
            global_interval=data.get("global_interval"),
            global_cron=data.get("global_cron"),
        )


@dataclass(frozen=True)
class WikiConfig:
    """Resolved configuration for contents-hub."""

    vault_path: Path
    sources_dir: str = "sources"
    meta_dir: str = METADATA_DIR.canonical
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    @property
    def meta_path(self) -> Path:
        """Directory for contents-hub metadata files."""
        return self.vault_path / self.meta_dir

    @property
    def sources_path(self) -> Path:
        """Directory for collected source files."""
        return self.vault_path / self.sources_dir

    @property
    def digests_path(self) -> Path:
        """Directory for digest Obsidian notes.

        Resolves to ``<vault>/digests/``. The directory is NOT created on
        property access — ``digest.dispatch_digest`` performs a lazy
        ``mkdir(parents=True, exist_ok=True)`` on first write (R-T15.1, R-U4.1).
        """
        return self.vault_path / "digests"

    @property
    def subscriptions_file(self) -> Path:
        """Path to the subscriptions state file."""
        return self.meta_path / "subscriptions.yaml"

    @property
    def config_file(self) -> Path:
        """Path to the user config YAML file."""
        return self.vault_path / CONFIG_FILE.canonical


def resolve_vault_path(explicit: str | Path | None = None) -> Path:
    """Resolve the Obsidian vault path.

    Priority:
        1. Explicit argument
        2. $CONTENTS_HUB_VAULT environment variable
        3. Current working directory

    Returns:
        Resolved absolute Path to the vault root.

    Raises:
        FileNotFoundError: If the resolved path does not exist.
    """
    if explicit:
        p = Path(explicit).expanduser().resolve()
    elif env := os.environ.get(VAULT_ENV_VARS.canonical):
        p = Path(env).expanduser().resolve()
    else:
        p = Path.cwd()

    if not p.is_dir():
        raise FileNotFoundError(f"Vault path does not exist: {p}")

    return p


def _load_yaml_config(vault_path: Path) -> dict:
    """Read vault YAML config, returning empty dict on missing/error."""
    config_file = vault_path / CONFIG_FILE.canonical
    if not config_file.exists():
        return {}
    try:
        text = config_file.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}
    except Exception as exc:
        logger.warning("Failed to read %s: %s", config_file, exc)
        return {}


def load_config(vault_path: str | Path | None = None) -> WikiConfig:
    """Load WikiConfig with resolved vault path and schedule settings.

    Reads `.contents-hub.yaml` from vault root for schedule configuration.
    """
    resolved = resolve_vault_path(vault_path)
    raw = _load_yaml_config(resolved)

    schedule_data = raw.get("schedule", {})
    schedule = ScheduleConfig.from_dict(schedule_data) if schedule_data else ScheduleConfig()

    return WikiConfig(
        vault_path=resolved,
        meta_dir=METADATA_DIR.canonical,
        schedule=schedule,
    )


def save_schedule_defaults(
    vault_path: Path,
    *,
    global_interval: Optional[int],
    global_cron: Optional[str],
    per_type: dict[str, int],
) -> None:
    """Write the `schedule:` block of `.contents-hub.yaml`, preserving other sections.

    Any existing top-level keys outside `schedule` are kept as-is. Empty
    values for global_interval / global_cron are persisted as `null` so the
    YAML stays self-describing.
    """
    config_file = vault_path / CONFIG_FILE.canonical
    raw: dict = {}
    if config_file.exists():
        try:
            text = config_file.read_text(encoding="utf-8")
            raw = yaml.safe_load(text) or {}
            if not isinstance(raw, dict):
                raw = {}
        except Exception as exc:
            logger.warning("Failed to read %s before write: %s", config_file, exc)
            raw = {}

    schedule_block = {
        "defaults": {k: int(v) for k, v in per_type.items() if isinstance(v, int) and v >= 0},
        "global_interval": global_interval,
        "global_cron": global_cron,
    }
    raw["schedule"] = schedule_block

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
