---
description: "Run the full collect → compile → write pipeline. Fetches subscriptions, evaluates sources, compiles wiki pages, and writes to vault. Use when: /pipeline, 'run pipeline', 'collect and compile', 'full wiki update', '전체 파이프라인'"
allowed-tools: ["Bash", "Read", "Write", "Glob", "Grep", "Agent", "AskUserQuestion"]
---

# /pipeline — Full collect → compile → write flow

Run the end-to-end pipeline: fetch subscriptions → save sources → evaluate → compile → write wiki pages.

## Workflow

### Step 1: Collect & build compile plan

Run the pipeline CLI to fetch all subscriptions and build a compile plan:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.pipeline collect --vault "${LLM_WIKI_VAULT:-.}" --json
```

This returns a JSON result with:
- `collection`: Fetch results per subscription
- `compile_plan`: Sources to compile with CREATE/UPDATE/SKIP decisions

If no subscriptions exist, try building a plan from existing sources:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki.pipeline plan --vault "${LLM_WIKI_VAULT:-.}" --json
```

If the plan is empty (`status: "empty"`), report "No sources to compile" and stop.

### Step 2: Read source content for compilation

For each candidate in the plan, read the source file to get the content:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.pipeline import scan_pending_sources, build_compile_plan
from llm_wiki.config import load_config
import os, json

vault = os.environ.get('LLM_WIKI_VAULT', '.')
config = load_config(vault)
plan = build_compile_plan(config)

for c in plan.candidates:
    print(json.dumps({
        'source_path': c.source.relative_path,
        'title': c.source.title,
        'url': c.source.url,
        'body': c.source.body[:3000],
        'action': c.evaluation.action.value,
        'target_title': c.evaluation.target_title,
        'lens_directory': c.lens_directory,
        'tags': c.source.tags,
        'lenses': c.source.lenses,
    }, ensure_ascii=False))
"
```

### Step 3: LLM compile (generate wiki content)

For each candidate, use Claude to compile the source into a wiki page. The output should be:
- Structured markdown with proper headings
- Obsidian-native [[wikilinks]] to related concepts
- #tags consistent with the source tags and lens
- Source attribution

### Step 4: Preview and confirm

Show the user a preview of what will be written:

```
## Pipeline Preview

Collection: 5 subscriptions fetched (5 ok, 0 error)
  New items: 12

Compile plan: 8 sources (6 new, 2 updates, 4 skipped)

Pages to write:
  [1] [NEW] Transformer Architecture [ai-research]
  [2] [NEW] Self-Attention Mechanisms [ai-research]
  [3] [UPD] Neural Networks [ai-research]
  ...

What would you like to do?
  [1] Approve — Write all pages
  [2] Reject — Discard
  [3] Modify — Give feedback
  [4] Partial — Select pages
```

**Ask the user** for confirmation before writing.

### Step 5: Execute

After approval, execute the plan:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.pipeline import build_compile_plan, execute_compile_plan
from llm_wiki.config import load_config
import os, json

vault = os.environ.get('LLM_WIKI_VAULT', '.')
config = load_config(vault)
plan = build_compile_plan(config)

# compiled_contents: dict mapping source_path -> LLM-compiled content
compiled_contents = { ... }  # from Step 3

report = execute_compile_plan(config, plan, compiled_contents)
print(report.summary())
"
```

### Step 6: Report

```
## Pipeline Complete

Collection: 5 subscriptions fetched (5 ok, 0 error)
  New items: 12

Execution: 6 created, 2 updated, 0 failed
  - ai-research/transformer-architecture.md (NEW, 450 words)
  - ai-research/self-attention.md (NEW, 320 words)
  - ai-research/neural-networks.md (UPD, 580 words)
  ...

Duration: 15.3s
```

## Pipeline-only mode (no collection)

If the user just wants to compile existing sources without fetching:

```
/pipeline --compile-only
```

Skip Step 1 collection and go directly to plan + compile + write.

## Edge Cases

- **No vault configured**: Error with "Set $LLM_WIKI_VAULT or run /setup first"
- **No subscriptions + no pending sources**: "Nothing to do. Use /subscribe add or /collect first."
- **Fetch errors**: Report per-subscription errors, continue with successful sources
- **Compile errors**: Skip failed sources, report which failed
- **Source already compiled**: Skipped automatically (status != pending)
