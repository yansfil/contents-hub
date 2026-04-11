---
description: "Check vault health — find broken wikilinks, orphan pages, and stale content"
allowed-tools: ["Bash", "Read", "Glob", "Grep"]
---

# /lint — Obsidian vault health check

Run lint rules against your Obsidian vault to detect structural issues:
broken `[[wikilinks]]`, orphan pages (unreachable from other pages), and
stale content (not updated within a threshold).

## Usage

```
/lint                                  — Run all rules
/lint --rules broken-link              — Run only broken link detection
/lint --rules orphan,stale             — Run specific rules
/lint --max-age-days 60                — Custom staleness threshold (default: 90)
/lint --format json                    — Machine-readable JSON output
```

## Behavior

1. **Run the lint CLI script**:
   ```bash
   cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.lint_cli <args>
   ```

   Pass through any user-provided flags (`--rules`, `--max-age-days`, `--format`).

   The vault path is resolved from `$LLM_WIKI_VAULT` or CWD. To override:
   ```bash
   cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.lint_cli --vault /path/to/vault <args>
   ```

2. **Check the exit code**:
   - `0` — Clean: no errors found (warnings may exist)
   - `1` — Errors found: at least one broken wikilink detected
   - `2` — Configuration error (vault not found, invalid rule name, etc.)

3. **Report the result** to the user:
   - If clean (exit 0): confirm the vault is healthy, mention any warnings
   - If errors (exit 1): list the errors and suggest fixes
     - Broken links: suggest creating the missing page or fixing the link
     - Orphan pages: suggest adding a wikilink from a related page
     - Stale pages: suggest re-compiling or reviewing the content
   - If config error (exit 2): show the error message and help the user fix it

## Lint Rules

| Rule | Severity | What it checks |
|------|----------|----------------|
| `broken-link` | error | `[[wikilinks]]` pointing to non-existent pages |
| `orphan` | warning | Pages with no incoming wikilinks from other pages |
| `stale` | warning | Pages not updated within `--max-age-days` threshold |

## Examples

### Full vault health check
User: `/lint`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.lint_cli
```

### Check only broken links
User: `/lint --rules broken-link`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.lint_cli --rules broken-link
```

### Strict staleness (30 days)
User: `/lint --max-age-days 30`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.lint_cli --max-age-days 30
```

### JSON output for further processing
User: `/lint --format json`
```bash
cd ${CLAUDE_PLUGIN_ROOT} && python3 -m llm_wiki.lint_cli --format json
```
