---
description: "Collect a URL, text snippet, or memo into the Obsidian vault's sources/ directory"
allowed-tools: ["Bash", "Read", "Write", "Glob", "Grep", "WebFetch"]
---

# /collect — Save sources to your knowledge vault

Collect URLs, text snippets, or memos into the `sources/` directory of your Obsidian vault. Sources are immutable — once saved, they are never modified.

## Usage

```
/collect <url>                      — Save a URL (auto-detects type)
/collect <url> --memo "annotation"  — Save URL with annotation
/collect --text "content"           — Save a text snippet
/collect --memo "thought or idea"   — Save a personal memo
/collect --file path/to/file        — Import a local file
```

## Behavior

1. **Parse the user input** to determine the source type:
   - If input looks like a URL (starts with http/https or contains a domain), treat as URL collection
   - If `--text` flag, treat as text snippet
   - If `--memo` flag, treat as personal memo
   - If `--file` flag, import from local file

2. **For URL sources**, optionally fetch the page title using WebFetch:
   - Only fetch if no `--title` is provided
   - Extract `<title>` from the HTML
   - If fetch fails, use the URL as fallback title

3. **Run the collector script**:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/src/collect.py <subcommand> <args>
   ```

4. **Report the result** to the user, showing:
   - The created file path (relative to vault)
   - Source type detected
   - Title and tags if any

## Source File Format

Each source is a Markdown file in `sources/` with Obsidian-compatible frontmatter:

```markdown
---
type: webpage
url: "https://example.com/article"
title: Example Article
collected_at: 2024-01-15T10:30:00+00:00
status: pending
tags:
  - ai
  - research
---

# Example Article

> User's annotation here

Source: https://example.com/article
```

### Frontmatter fields
- `type`: webpage | youtube | twitter | reddit | github | arxiv | substack | text | memo | file
- `url`: Original URL (for URL sources)
- `title`: Human-readable title
- `collected_at`: ISO 8601 timestamp
- `status`: Always `pending` (consumed by compile step)
- `tags`: User-provided tags as YAML list

## Configuration

The script reads `.llm-wiki.json` from the vault root:

```json
{
  "vault_path": "~/obsidian/my-vault",
  "sources_dir": "sources"
}
```

If no config exists, falls back to `$LLM_WIKI_VAULT` env var, then CWD.

## Examples

### Collect a URL
User: `/collect https://arxiv.org/abs/2301.00001`
→ Fetches title, saves to `sources/20240115-attention-is-all-you-need-a1b2c3d4.md`

### Collect with tags and memo
User: `/collect https://youtube.com/watch?v=abc --tags ai,transformers --memo "Great explanation of attention mechanism"`
→ Saves with tags and memo annotation

### Collect a memo
User: `/collect --memo "Idea: combine RAG with personal wiki for better context"`
→ Saves to `sources/20240115-memo-idea-combine-rag-with-personal-e5f6g7h8.md`

### Collect text
User: `/collect --text "The key insight is that attention mechanisms allow..."`
→ Saves text snippet as source
