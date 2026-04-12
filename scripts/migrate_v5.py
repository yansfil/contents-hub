"""Schema v5 migration.

- Rewrites subscriptions.source_type ``'browser'`` → ``'webpage'``.
- Sets ``config.fetch_method = 'browser'`` for every webpage subscription
  that doesn't already have a fetch_method recorded (``rss`` values are
  preserved).

Idempotent: safe to re-run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from llm_wiki.config import load_config
from llm_wiki.db import get_db


def migrate(config_dir: str = ".") -> tuple[int, int]:
    cfg = load_config(config_dir)
    with get_db(cfg) as conn:
        cur = conn.execute(
            "UPDATE subscriptions SET source_type='webpage' "
            "WHERE source_type='browser'"
        )
        browser_to_webpage = cur.rowcount or 0

        rows = conn.execute(
            "SELECT id, url, config FROM subscriptions WHERE source_type='webpage'"
        ).fetchall()

        fetch_method_set = 0
        for row in rows:
            raw = row["config"] if isinstance(row, dict) or hasattr(row, "keys") else row[2]
            try:
                conf = json.loads(raw or "{}")
            except json.JSONDecodeError:
                conf = {}

            current = conf.get("fetch_method")
            if current == "rss":
                continue
            if current == "browser":
                continue
            conf["fetch_method"] = "browser"
            conn.execute(
                "UPDATE subscriptions SET config=? WHERE id=?",
                (json.dumps(conf), row["id"] if hasattr(row, "keys") else row[0]),
            )
            fetch_method_set += 1

    return browser_to_webpage, fetch_method_set


def main(argv: list[str]) -> int:
    config_dir = argv[1] if len(argv) > 1 else "."
    n1, n2 = migrate(config_dir)
    print(f"Migrated {n1} browser->webpage; set fetch_method on {n2} webpage subs")
    print("Idempotent: safe to re-run.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
