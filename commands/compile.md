---
description: "Compile collected sources into structured wiki pages using LLM — filter by lens, preview, approve"
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Agent", "AskUserQuestion"]
---

# /compile — Compile sources into wiki pages

Compile collected sources into structured Obsidian wiki pages using LLM.
Supports Lens filtering, preview, and user approval before writing.

## Usage

```
/compile                               — Compile all pending sources
/compile --lens ai                     — Compile only sources routed to the "ai" lens
/compile --lens ai --lens devops       — Compile sources in multiple lenses
/compile --dry-run                     — Show routing plan without compiling
/compile --force                       — Re-compile already-processed sources
/compile --resume                      — Resume a pending confirmation
```

## Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--lens` | string (repeatable) | all lenses | Filter compilation to specific lens IDs. Only sources routed to these lenses are compiled. |
| `--dry-run` | flag | false | Show the routing plan (which sources map to which lenses) without running LLM compilation. |
| `--force` | flag | false | Re-compile sources even if they were already processed. |
| `--resume` | flag | false | Resume a previously interrupted confirmation instead of starting fresh. |

## Behavior

### 1. Check for pending confirmation (if `--resume`)

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.config import load_config
from llm_wiki.db import get_db
import os

vault = os.environ.get('LLM_WIKI_VAULT', '.')
config = load_config(vault)
db = get_db(config)
pending = db.get_pending_confirmation()
if pending:
    print(f'Found pending confirmation from {pending[\"created_at\"]}')
    print(f'{pending[\"num_pages\"]} pages awaiting approval')
else:
    print('No pending confirmation found')
"
```

If a pending confirmation exists and `--resume` is passed, skip to Step 4 (preview + confirm).

### 2. Identify and route pending sources

Find unprocessed sources and route them to lenses:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.config import load_config
from llm_wiki.routing import route_sources
import os, json

vault = os.environ.get('LLM_WIKI_VAULT', '.')
config = load_config(vault)
plan = route_sources(config)
print(json.dumps(plan, indent=2, ensure_ascii=False))
"
```

If no sources are found, report:
> No sources to compile. Use `/collect` to add sources first.

#### Lens filtering

If `--lens` is specified, filter the routing plan to only include the named lenses:

```python
if lens_filters:
    plan = {k: v for k, v in plan.items() if k in lens_filters}
    if not plan:
        print(f"No sources routed to lens(es): {', '.join(lens_filters)}")
        print("Available lenses with pending sources: ...")
```

If `--dry-run`, print the routing plan and stop here.

### 3. LLM compile

Delegate to the compile skill workflow (Step 3 of `skills/compile.md`).
For each lens group, use Claude to compile source content into wiki pages with:
- Obsidian frontmatter (tags, aliases, related links)
- `[[wikilinks]]` to related existing pages
- `#tags` consistent with the lens taxonomy
- Source attribution

### 4. Preview and confirm

Show a full preview using the compile skill's preview/confirmation workflow
(Step 4 of `skills/compile.md`).

Present the user with choices:
1. **Approve** — Write all pages to vault
2. **Reject** — Discard all changes
3. **Modify** — Give feedback and re-compile
4. **Partial** — Select which pages to approve

### 5. Execute and report

Execute approved decisions and report results:

```
## Compile Complete

Wrote 3 pages to vault:
  - ai/transformer-architecture.md (NEW, 450 words)
  - ai/attention-mechanisms.md (NEW, 320 words)
  - ai/neural-networks.md (UPDATED, 580 words)

Lens: ai
Sources processed: 12
```

## Examples

### Compile everything
User: `/compile`
→ Routes all pending sources to their lenses, compiles, previews, asks for approval.

### Compile a specific lens
User: `/compile --lens ai`
→ Only compiles sources routed to the "ai" lens. Other lenses are untouched.

### Compile multiple lenses
User: `/compile --lens ai --lens devops`
→ Compiles sources in both "ai" and "devops" lenses.

### Dry-run to preview routing
User: `/compile --dry-run`
→ Shows which sources would route to which lenses, without running LLM.

### Force re-compile
User: `/compile --lens ai --force`
→ Re-compiles all "ai" sources, including those already processed.

### Resume interrupted session
User: `/compile --resume`
→ Loads the last pending confirmation and shows the preview again.
