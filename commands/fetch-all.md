---
description: "Fetch all active subscriptions — collect new content from every registered RSS/YouTube/Twitter feed in one pass. Use when: /fetch-all, 'fetch all subscriptions', 'collect everything', 'run all feeds', '전체 수집'"
allowed-tools: ["Bash", "Read"]
---

# /fetch-all — Fetch all active subscriptions

Iterate through every registered subscription and collect new content, regardless of individual schedule timers. Designed to be called by a schedule trigger (`/auto-fetch`) or manually.

## Usage

```
/fetch-all                  — Fetch all active subscriptions
/fetch-all --type rss       — Only fetch RSS feeds
/fetch-all --type youtube   — Only fetch YouTube channels
/fetch-all --dry-run        — Show what would be fetched without fetching
```

## Execution

Run the Python CLI to fetch all subscriptions:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.fetch_all --vault "${LLM_WIKI_VAULT:-.}" --json
```

### With source type filter:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.fetch_all --vault "${LLM_WIKI_VAULT:-.}" --type rss --json
```

### Dry run (preview only):

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.fetch_all --vault "${LLM_WIKI_VAULT:-.}" --dry-run --json
```

## Output Format

The `--json` flag returns structured results:

```json
{
  "status": "complete",
  "total_subscriptions": 12,
  "fetched": 10,
  "succeeded": 9,
  "failed": 1,
  "skipped": 2,
  "new_items_total": 15,
  "duration_seconds": 8.3,
  "completed_at": "2024-01-15T12:00:00+00:00",
  "per_subscription": [
    {
      "url": "https://example.com/feed.xml",
      "title": "Example Blog",
      "source_type": "rss",
      "ok": true,
      "new_items": 3,
      "error": "",
      "source_files": ["sources/20240115-post-title-a1b2c3d4.md"],
      "duration_seconds": 1.2
    }
  ]
}
```

## Report to User

After execution, present a summary table:

```
## Fetch All Complete

| Source Type | Subscriptions | New Items | Errors |
|-------------|--------------|-----------|--------|
| RSS         | 6            | 12        | 0      |
| YouTube     | 3            | 2         | 0      |
| Twitter     | 1            | 0         | 1      |
| **Total**   | **10**       | **14**    | **1**  |

Skipped: 2 (paused/manual)
Duration: 8.3s
New source files saved to: vault/sources/
```

If there are errors, show them:

```
### Errors
- ✗ [twitter] @someuser: Connection timeout (will retry next run)
```

## Edge Cases

- **No subscriptions**: Report "No active subscriptions. Use /subscribe add <url> to add sources."
- **All paused**: Report "All subscriptions are paused. Resume with /subscribe set-status <url> active"
- **Network errors**: Report per-feed, don't fail the whole batch
- **Python venv missing**: Report setup instructions
