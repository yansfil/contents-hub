"""
E2E test for BrowserFetcher.

Tests the full flow:
    1. BrowserFetcher.poll() with a real URL
    2. Claude Agent SDK spawns agent
    3. Agent uses chromux to browse
    4. Content links extracted, body fetched
    5. Results returned as FetchedItems
"""

import asyncio
import sys

# Run from project root with: .venv/bin/python tests/test_browser_e2e.py


async def test_browser_fetcher_e2e():
    from llm_wiki.fetchers.browser import BrowserFetcher

    url = "https://news.ycombinator.com"
    config = {}  # Fresh config, no known_urls

    print(f"\n=== E2E Test: BrowserFetcher ===")
    print(f"URL: {url}")
    print(f"Config: {config}")
    print()

    fetcher = BrowserFetcher(url, config=config)

    print("--- Step 1: Calling poll() ---")
    print("  (This will check for RSS first, then use browser agent)")
    print()

    result = await fetcher.poll(max_items=5)

    print(f"\n--- Results ---")
    print(f"  ok: {result.ok}")
    print(f"  error: {result.error or 'none'}")
    print(f"  total_available: {result.total_available}")
    print(f"  items count: {len(result.items)}")
    print()

    if result.items:
        print("--- Items ---")
        for i, item in enumerate(result.items[:5]):
            print(f"  [{i+1}] {item.title}")
            print(f"      URL: {item.url}")
            print(f"      Summary: {item.summary[:100]}..." if item.summary else "      Summary: (none)")
            print()

    # Check updated config
    updated_config = fetcher.get_updated_config()
    print(f"--- Updated Config ---")
    print(f"  fetch_method: {updated_config.get('fetch_method', 'unknown')}")
    print(f"  rss_url: {updated_config.get('rss_url', 'not checked')}")
    known = updated_config.get("known_urls", [])
    print(f"  known_urls count: {len(known)}")
    if known:
        print(f"  first 3 known_urls: {known[:3]}")
    print()

    # Assertions
    assert result.ok, f"Fetch failed: {result.error}"
    print("=== E2E Test PASSED ===\n")

    return result, updated_config


if __name__ == "__main__":
    result, config = asyncio.run(test_browser_fetcher_e2e())
