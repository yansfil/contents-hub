---
description: "Run a collection tick — check for due schedules and dispatch source-type-specific collector agents. Use when: /tick, 'collect now', 'run collectors', 'check feeds', 'wiki tick'"
allowed-tools: ["Bash", "Read", "Glob", "Grep", "Agent"]
---

# /tick — Dispatch scheduled collections

Check for due collection schedules and dispatch the appropriate collector agent for each source type (RSS, YouTube, Twitter/X, browser).

## Workflow

### Step 1: Query due schedules

Run the tick CLI to get the dispatch plan:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.tick --vault "${LLM_WIKI_VAULT:-.}" due
```

This returns a JSON dispatch plan grouped by source type:

```json
{
  "status": "dispatch",
  "due_count": 5,
  "vault_path": "/path/to/vault",
  "dispatch": {
    "rss": [{ "subscription_url": "...", "title": "...", "lenses": [...] }, ...],
    "youtube": [{ "subscription_url": "...", "title": "...", "lenses": [...] }, ...],
    "twitter": [{ "subscription_url": "...", "title": "...", "lenses": [...] }]
  }
}
```

If `status` is `"idle"` (no due schedules), report "No collections due" and stop.

### Step 2: Dispatch subagents by source type

For each source type present in the dispatch plan, spawn the appropriate collector agent **in parallel** using the Agent tool. Each agent receives the relevant slice of the dispatch payload.

**IMPORTANT**: Launch all agents in a single message to maximize parallelism.

#### RSS feeds (`source_type: "rss"`)

Spawn agent with `subagent_type: "llm-wiki:rss-collector"`:

```
Collect these RSS feeds. Vault: <vault_path>

Feeds to collect:
- <url1> (title: <title>, lenses: [<lenses>])
- <url2> (title: <title>, lenses: [<lenses>])

Run: cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.scheduler --vault "<vault_path>" --once --json

Then record each result:
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.tick --vault "<vault_path>" record "<url>" --ok --new-items <N>
```

#### YouTube channels (`source_type: "youtube"`)

Spawn agent with `subagent_type: "llm-wiki:youtube-collector"`:

```
Collect these YouTube feeds. Vault: <vault_path>

Channels to collect:
- <url1> (title: <title>, lenses: [<lenses>])

Fetch each YouTube RSS feed, save new videos as sources, and record results.
```

#### Twitter/X accounts (`source_type: "twitter"`)

Spawn agent with `subagent_type: "llm-wiki:twitter-collector"`:

```
Collect these Twitter/X accounts. Vault: <vault_path>

Accounts to collect:
- <url1> (title: <title>, lenses: [<lenses>])

Try Nitter RSS mirrors first, fall back to chromux browser if available. Save new tweets as sources and record results.
```

#### Browser/manual (`source_type: "browser"`)

Spawn agent with `subagent_type: "llm-wiki:browser-collector"`:

```
Collect content for this natural language query. Vault: <vault_path>

Query: <subscription_url or title>
Lenses: [<lenses>]

Search Google, extract content from top results, and save as sources.
```

### Step 3: Persist results (formatter → writer)

After all agents complete, persist their results using the orchestrator.
Each agent should return a JSON `AgentReport` (see `llm_wiki.agent_results`).

Parse each agent's output into an AgentReport and run the persistence pipeline:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.persist --vault "${LLM_WIKI_VAULT:-.}" --input <reports.json>
```

Or call from Python directly:

```python
from llm_wiki.agent_results import AgentReport, CollectedItem, SourceType
from llm_wiki.persist import persist_agent_reports
from llm_wiki.config import load_config

config = load_config(vault_path)
reports = [AgentReport.from_json(agent_json) for agent_json in agent_outputs]
tick_result = persist_agent_reports(config, reports)
```

The orchestrator handles: format item → write source file → record run in DB → dedup via seen_urls.

**Agent result contract** — each agent must return JSON matching `AgentReport`:

```json
{
  "source_type": "rss",
  "subscription_url": "https://example.com/feed.xml",
  "status": "success",
  "items": [
    {
      "url": "https://example.com/post-1",
      "title": "Post Title",
      "source_type": "rss",
      "content": "Post content...",
      "author": "Author Name",
      "published_at": "2024-01-15T12:00:00+00:00",
      "tags": ["tech"],
      "lenses": ["ai"],
      "metadata": { "feed_title": "Example Blog" },
      "status": "ok"
    }
  ],
  "feed_title": "Example Blog"
}
```

### Step 4: Summarize results

Display the tick_result summary table:

```
## Collection Tick Complete

| Source Type | Items | New | Skipped | Errors |
|-------------|-------|-----|---------|--------|
| RSS         | 15    | 12  | 3       | 0      |
| YouTube     | 3     | 2   | 1       | 0      |
| Twitter     | 10    | 8   | 1       | 1      |
| **Total**   |       | **22** | **5** | **1**  |

Source files created: 22
Next tick: ~30 minutes (based on shortest interval)
```

## Edge Cases

- **No vault configured**: Error with message "Set $LLM_WIKI_VAULT or run /setup first"
- **No subscriptions**: Report "No subscriptions found. Use /collect to add sources."
- **All schedules paused**: Report "All schedules are paused. Use /wiki status to review."
- **Python venv missing**: Report setup instructions
- **Mixed results**: Report per-type results, don't fail everything on one type's error

## Manual Override

Users can force a tick even if nothing is due:

```
/tick --force
```

In this case, skip the due check and dispatch all enabled schedules.
