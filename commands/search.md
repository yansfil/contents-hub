---
description: "Search wiki pages, sources, and tags in the Obsidian vault"
allowed-tools: ["Bash", "Read", "Glob", "Grep"]
---

# /search — Search your knowledge vault

Search wiki pages by title, alias, tag, full-text content, BM25-ranked relevance,
or semantic similarity (embedding-based).
Results include context snippets, file paths, highlighted matches, and relevance scores.

## Usage

```
/search <query>                        — Search across titles, aliases, and tags
/search <query> --tag                  — Search by tag only
/search <query> --content              — Full-text content search with snippets
/search <query> --ranked               — BM25-ranked search with relevance scores
/search <query> --semantic             — Semantic (embedding) search with similarity %
/search --tags                         — List all tags with usage counts
/search <query> --sources              — Also search in sources/ directory
/search <query> --lens ai-research     — Filter results to a specific lens
/search <query> --format json          — Output as JSON (for piping)
/search <query> --top 20               — Limit results (default: 10)
```

## Behavior

1. **Parse the user's query** and determine the search mode:
   - Default: combined search across title + aliases + tags (fast, frontmatter-based)
   - `--tag`: tag-only search
   - `--content`: full-text search through wiki page bodies with context snippets
   - `--ranked`: BM25-ranked full-text search with relevance scores and excerpts
   - `--semantic`: embedding-based similarity search with cosine similarity scores
   - `--tags`: list all tags in the vault
   - `--sources`: include sources/ directory in the search
   - `--lens LENS_ID`: filter results to pages under a specific lens (by wiki_directory or default_tags)

2. **Run the search**:
   ```bash
   cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "<query>" [flags]
   ```

   Flags: `--tag`, `--content`, `--ranked`, `--semantic`, `--sources`, `--lens LENS_ID`, `--format json`, `--top N`

3. **Report results** to the user:
   - Results are formatted with context snippets, file paths, and highlighted matches
   - Metadata search: title, match type (TITLE/ALIAS/TAG), tags, aliases, file path
   - Content search: file title, matched lines with surrounding context, line numbers
   - Ranked search: BM25 relevance score, title, excerpt, tags, wikilink
   - Semantic search: cosine similarity % with relevance label, title, excerpt, tags, wikilink
   - Tag listing: tag name, usage count, visual bar chart
   - Suggest `/open <path>` to open a result in Obsidian

## Search Modes

### Default (metadata)
Fastest mode. Searches frontmatter fields: title, aliases, tags.
Good for finding known pages by name.

### --content (full-text)
Searches inside page bodies. Shows matched lines with 2 lines of surrounding context.
Good for finding specific phrases or concepts mentioned in page text.

### --ranked (BM25)
Uses BM25 ranking algorithm for relevance-scored full-text search.
Shows relevance scores and query-centered excerpts.
Best for natural language queries like "how does attention mechanism work".

### --semantic (embedding similarity)
Uses embedding vectors and cosine similarity for meaning-based search.
Finds conceptually related pages even when they don't share exact keywords.
Requires: `OPENAI_API_KEY` or `VOYAGE_API_KEY` environment variable.
Falls back to `--ranked` if no embedding API key is configured or no embeddings exist.

**Relevance labels:**
- **Very High** (≥85%): Near-exact semantic match
- **High** (≥70%): Strong conceptual overlap
- **Medium** (≥50%): Related topic
- **Low** (≥30%): Loosely related
- **Marginal** (<30%): Weak connection

## Examples

### Search by title/alias/tag (combined)
User: `/search transformer`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "transformer"
```

### Full-text content search with snippets
User: `/search "attention mechanism" --content`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "attention mechanism" --content
```

### BM25-ranked search
User: `/search transformer attention --ranked`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "transformer attention" --ranked
```

### Semantic search
User: `/search "how do neural networks learn" --semantic`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "how do neural networks learn" --semantic
```

### List all tags
User: `/search --tags`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli --tags
```

### Filter by lens
User: `/search transformer --lens ai-research`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "transformer" --lens ai-research
```

### Combine lens with ranked search
User: `/search "attention mechanism" --ranked --lens ai-research`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "attention mechanism" --ranked --lens ai-research
```

### JSON output (for piping)
User: `/search transformer --semantic --format json`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.search_cli "transformer" --semantic --format json
```

## Configuration

The vault path is resolved from:
1. `$LLM_WIKI_VAULT` environment variable
2. Current working directory

For `--semantic` mode, one of these environment variables must be set:
- `OPENAI_API_KEY` — uses OpenAI text-embedding-3-small (1536d)
- `VOYAGE_API_KEY` — uses Voyage AI voyage-3-lite (512d)

## After Search

- Suggest `/open <path>` to open any result in Obsidian
- If no results found, suggest `--content` for full-text, `--ranked` for BM25, or `--semantic` for meaning-based search
- If many results, suggest narrowing with `--tag` or adding more specific terms
- For conceptual/fuzzy queries, recommend `--semantic` over keyword-based modes
