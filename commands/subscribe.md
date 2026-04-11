---
description: "Manage source subscriptions — add, remove, or list RSS/YouTube/Twitter/browser feeds"
allowed-tools: ["Bash", "Read", "Glob"]
---

# /subscribe — Manage source subscriptions

Add, remove, or list source subscriptions for your knowledge vault.

## Usage

```
/subscribe add <url> [--title "Title"] [--type rss|youtube|twitter|browser] [--lens ai --lens tech]
/subscribe remove <url>
/subscribe list [--type rss] [--status active|paused|error] [--lens ai] [--format table|json]
/subscribe ls                       — alias for list
/subscribe rm <url>                 — alias for remove
```

## Behavior

Run the Python CLI script to manage subscriptions:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki <subcommand> <args>
```

The vault path is resolved from `$LLM_WIKI_VAULT` or CWD. To override:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki --vault /path/to/vault <subcommand> <args>
```

### Examples

**Add an RSS feed:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki add "https://hnrss.org/frontpage" --title "Hacker News" --lens tech
```

**Add a YouTube channel:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki add "https://youtube.com/@3blue1brown" --title "3Blue1Brown" --lens math --lens education
```

**Add a Twitter/X profile:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki add "https://x.com/karpathy" --title "Andrej Karpathy" --lens ai
```

**Remove a subscription:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki remove "https://hnrss.org/frontpage"
```

**List all subscriptions:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki list
```

**List filtered subscriptions:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki list --type youtube --format json
```

## Report to User

After running, report the result clearly:
- **add**: Show the added URL, detected type, title, and lenses
- **remove**: Confirm the removed URL
- **list**: Show the table or JSON output directly

## Storage

Subscriptions are persisted in `.llm-wiki/subscriptions.yaml` inside the Obsidian vault. The YAML format is human-readable and can be edited manually.
