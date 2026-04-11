---
description: "Open a wiki page, source file, or the vault itself in Obsidian"
allowed-tools: ["Bash", "Read", "Glob", "Grep"]
---

# /open — Open in Obsidian

Open a wiki page, source file, or the entire vault in Obsidian using the `obsidian://` URI scheme.

## Usage

```
/open <file_path>              — Open a specific file in Obsidian
/open                          — Open the vault root in Obsidian
/open --search "query"         — Search within the vault in Obsidian
/open --uri-only <file_path>   — Print the obsidian:// URI without opening
```

## Behavior

1. **Determine what to open** from the user's input:
   - A file path (relative to vault root or absolute)
   - A wiki page title (will be slugified to find the file)
   - `--search` flag for vault-wide search in Obsidian
   - No argument = open the vault root

2. **If a title is given instead of a path**, locate the file first:
   ```bash
   # Find by title/slug in the vault
   cd ${CLAUDE_PLUGIN_ROOT} && python3 -c "
   from llm_wiki.config import load_config
   from llm_wiki.writer import wiki_filename, resolve_wikilink
   config = load_config()
   result = resolve_wikilink(config, '<TITLE>')
   if result:
       print(result.relative_to(config.vault_path))
   else:
       print('NOT_FOUND')
   "
   ```

3. **Run the opener**:
   ```bash
   cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener [--vault /path/to/vault] [FILE_PATH]
   ```

   For search:
   ```bash
   cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener --search "query"
   ```

   For URI-only (scripting):
   ```bash
   cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener --uri-only [FILE_PATH]
   ```

### Examples

**Open a specific wiki page:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener "topics/ai-research/transformers.md"
```

**Open by page title (resolve first):**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener "transformer-architecture.md"
```

**Open the vault:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener
```

**Search in Obsidian:**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener --search "attention mechanism"
```

**Get URI without opening (for integration):**
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.obsidian_opener --uri-only "topics/transformers.md"
```

## After Opening

Report to the user:
- The `obsidian://` URI used
- The vault name and file path
- Whether the open was successful
- If failed, suggest checking that Obsidian is installed and the vault is registered

## Configuration

The vault path is resolved from:
1. `--vault` argument (explicit)
2. `$LLM_WIKI_VAULT` environment variable
3. Current working directory
