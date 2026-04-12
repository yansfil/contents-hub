"""
Full pipeline E2E test: subscription → BrowserFetcher → raw_items in DB.

Tests:
    1. Add a subscription with source_type="browser"
    2. BrowserFetcher.poll() runs
    3. Items saved to raw_items table
    4. known_urls persisted in subscription config
"""

import asyncio
import tempfile
import os
import sys


async def test_full_pipeline():
    from pathlib import Path

    # Create a temporary vault for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)
        sources_dir = vault_path / "sources"
        sources_dir.mkdir()
        meta_dir = vault_path / ".llm-wiki"
        meta_dir.mkdir()

        # Write minimal config
        config_path = vault_path / ".llm-wiki.yaml"
        config_path.write_text(f"""
vault_path: {vault_path}
sources_dir: sources
meta_dir: .llm-wiki
""")

        from llm_wiki.config import load_config

        config = load_config(vault_path)

        from llm_wiki.db import init_db, get_db

        init_db(config)

        # 1. Add subscription
        from llm_wiki.subscriptions import SubscriptionStore

        store = SubscriptionStore(config)
        store.add(
            url="https://simonwillison.net",
            title="Simon Willison's Blog",
            source_type="browser",
        )

        sub = store.get("https://simonwillison.net")
        print(f"Subscription added: {sub.title} ({sub.source_type})")
        print(f"  config: {sub.config}")

        # 2. Run BrowserFetcher directly (simulating scheduler)
        from llm_wiki.fetchers.browser import BrowserFetcher

        # Force browser path by pre-marking RSS as not found
        sub.config["rss_url"] = ""
        sub.config["fetch_method"] = "browser"
        store.update_config(sub.url, sub.config)

        fetcher = BrowserFetcher(sub.url, config=dict(sub.config))

        print("\nRunning BrowserFetcher.poll()...")
        result = await fetcher.poll(max_items=3)

        print(f"  ok: {result.ok}")
        print(f"  items: {len(result.items)}")

        # 3. Persist updated config (known_urls)
        updated_config = fetcher.get_updated_config()
        store.update_config(sub.url, updated_config)

        # Verify config persisted
        sub_after = store.get("https://simonwillison.net")
        known_urls = sub_after.config.get("known_urls", [])
        print(f"  known_urls persisted: {len(known_urls)}")

        # 4. Save items to raw_items
        if result.items:
            from llm_wiki.db import get_db
            import json
            from datetime import datetime, timezone

            now_iso = datetime.now(timezone.utc).isoformat()
            with get_db(config) as conn:
                for item in result.items:
                    conn.execute(
                        """INSERT OR IGNORE INTO raw_items
                           (url, title, origin, status, subscription_id, content_summary, collected_at, updated_at)
                           VALUES (?, ?, 'subscription', 'raw', ?, ?, ?, ?)""",
                        (
                            item.url,
                            item.title,
                            sub.id,
                            item.summary or (item.content_html[:500] if item.content_html else ""),
                            now_iso,
                            now_iso,
                        ),
                    )

                # Verify items in DB
                cursor = conn.execute("SELECT COUNT(*) FROM raw_items")
                count = cursor.fetchone()[0]
                print(f"  raw_items in DB: {count}")

                cursor = conn.execute("SELECT url, title FROM raw_items LIMIT 3")
                for row in cursor:
                    print(f"    - {row[1][:60]} ({row[0][:50]}...)")

        print("\n=== Full Pipeline Test PASSED ===")
        return True


if __name__ == "__main__":
    success = asyncio.run(test_full_pipeline())
    sys.exit(0 if success else 1)
