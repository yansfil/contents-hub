---
description: "Compile collected sources into wiki pages with LLM, preview results, and confirm before writing. Use when: /compile, 'compile wiki', 'build wiki', 'process sources', 'wiki compile', '위키 컴파일', '소스 정리'"
allowed-tools: ["Bash", "Read", "Write", "Glob", "Grep", "Agent", "AskUserQuestion"]
---

# /compile — Compile sources into wiki pages

Compile collected sources into structured wiki pages using LLM, preview the results, and ask the user to approve before writing to the vault.

## Workflow

### Step 1: Identify pending sources

Find unprocessed sources in the vault:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.config import load_config
from llm_wiki.writer import list_source_files
import os

vault = os.environ.get('LLM_WIKI_VAULT', '.')
config = load_config(vault)
sources = list_source_files(config)
print(f'Found {len(sources)} source files')
for s in sources[:20]:
    print(f'  {s.relative_to(config.vault_path)}')
if len(sources) > 20:
    print(f'  ... and {len(sources) - 20} more')
"
```

If no sources found, report "No sources to compile. Use /collect to add sources first." and stop.

### Step 2: Route sources to lenses and build orchestration plan

Determine which lens each source maps to, apply each lens's CompileStrategy (merge/per-source/append), and build a tiered orchestration plan:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.config import load_config
from llm_wiki.compile_orchestrator import CompileOrchestrator, route_sources
from llm_wiki.lens import LensStore
from llm_wiki.pipeline import build_compile_plan
import os, json

vault = os.environ.get('LLM_WIKI_VAULT', '.')
config = load_config(vault)

# Step 2a: Route sources to lenses
routing = route_sources(config)
print(json.dumps(routing, indent=2, ensure_ascii=False))

# Step 2b: Build orchestration plan with lens strategies
store = LensStore(config)
orch = CompileOrchestrator(config, store)
compile_plan = build_compile_plan(config)
plan = orch.build_plan(compile_plan)
print(plan.summary())
print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
"
```

The orchestration plan organizes jobs into priority **tiers**:
- **Lower priority number** = compiles first (dependency ordering)
- Jobs in the same tier can run **concurrently** (different lenses, same priority)
- Jobs in different tiers run **sequentially** (tier 0 completes before tier 1 starts)

Each lens's **CompileStrategy** determines how sources are grouped:
- **merge** (default): Multiple sources about the same topic → one wiki page
- **per-source**: Each source → its own wiki page (good for paper reviews)
- **append**: New content appended to existing running pages (good for logs)

### Step 3: LLM compile (generate wiki content)

Execute the orchestration plan tier by tier. For each job, use the job's built-in prompt builder which injects lens-specific instructions:

```python
# For each tier (sequential):
for tier in plan.tiers:
    # For each job in the tier (can be parallel):
    for job in tier.pending_jobs:
        system_prompt = orch.system_prompt
        user_prompt = job.build_prompt()  # includes lens-specific instructions
        # ... send system_prompt + user_prompt to Claude ...
        # ... get response ...
        job.set_result(llm_response)
```

The LLM reads the source content and generates structured wiki markdown with:
- Proper Obsidian frontmatter (tags, aliases, related links)
- [[wikilinks]] to related existing pages
- #tags consistent with the lens taxonomy and lens default_tags
- Source attribution

### Step 4: Preview and confirm

**This is the critical user-control step.** After compilation, show the user a full preview and ask for confirmation.

#### 4a. Generate preview

Format the compile results using the preview module:

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.preview import preview_compile_batch, CompileResult, CompileAction, SourceReference
from llm_wiki.confirmation import format_batch_confirmation_prompt, confirmation_prompt_json
import json

# results would come from the compile step
# ... (construct CompileResult objects from LLM output)

# Format for terminal display
prompt = format_batch_confirmation_prompt(results, color=False)
print(prompt)
"
```

#### 4b. Present confirmation to user

Display the preview output to the user, then present the confirmation choices. The preview shows:

1. **Summary header**: Total pages, creates vs updates, word count
2. **Per-page preview**: Frontmatter, body excerpt, tags, wikilinks, sources
3. **Confirmation choices**:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Ready to write: 3 new pages, 1 update

  What would you like to do?

  [1] ✅ Approve — Write all pages to the vault
  [2] ❌ Reject  — Discard all changes
  [3] ✏️  Modify  — Give feedback and re-compile
  [4] 🔀 Partial — Select which pages to approve

  Pages:
    [1] [NEW] Transformer Architecture
    [2] [NEW] Attention Mechanisms
    [3] [UPD] Neural Networks
    [4] [NEW] RLHF Training

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Ask the user** which option they choose. Wait for their response.

#### 4c. Parse user response

Parse the user's choice using the confirmation module:

```python
from llm_wiki.confirmation import parse_user_choice, ConfirmationChoice

choice, feedback, indices = parse_user_choice(user_response, num_results=len(results))
```

**Response patterns the parser recognizes:**

| User says | Parsed as |
|-----------|-----------|
| `1`, `yes`, `approve`, `ok`, `lgtm`, `승인` | **approve** |
| `2`, `no`, `reject`, `cancel`, `거절` | **reject** |
| `3`, `modify: add more detail about X`, `수정` | **modify** (with feedback) |
| `4`, then `1,3` or `1 3` | **partial** (pages 1 and 3) |
| Long text (>20 chars) | **modify** (entire text as feedback) |

### Step 5: Execute based on choice

#### If APPROVE:

Execute all approved compile decisions using the compile executor, which dispatches
CREATE decisions to `note_creator.create_note()` (Write) and UPDATE decisions to
`note_updater.update_note()` (Edit):

```bash
cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -c "
from llm_wiki.config import load_config
from llm_wiki.compile_executor import execute_decisions
import os

vault = os.environ.get('LLM_WIKI_VAULT', '.')
config = load_config(vault)

# evaluations: list[EvaluationResult] from Step 2-3
# compiled_contents: dict[source_path, str] from Step 3
report = execute_decisions(config, evaluations, compiled_contents)
print(report.summary())
"
```

The executor automatically:
- **CREATE** decisions → `create_note()` (Write new file with frontmatter, wikilinks, tags)
- **UPDATE** decisions → `update_note()` (Edit existing file using merge strategy: append/rewrite/section)
- **SKIP** decisions → no-op (logged)
- **UPDATE with missing target** → falls back to CREATE automatically

Report: "Wrote N pages to vault" with file paths.

#### If REJECT:

Report: "Discarded all changes. No files were modified."

#### If MODIFY:

1. Show the user's feedback
2. Re-run Step 3 (LLM compile) incorporating the feedback
3. Go back to Step 4 (preview + confirm again)
4. Show attempt number: "Compile attempt #2 (revised)"

#### If APPROVE_PARTIAL:

Filter evaluations to only the user-selected indices, then execute:

1. Map user-selected indices to evaluations
2. Execute only those via `execute_decisions()` (others are excluded)
3. Report which pages were written and which were skipped

### Step 6: Report results

```
## Compile Complete

✅ Wrote 3 pages to vault:
  - ai/transformer-architecture.md (NEW, 450 words)
  - ai/attention-mechanisms.md (NEW, 320 words)
  - ai/neural-networks.md (UPDATED, 580 words)

Skipped:
  - ai/rlhf-training.md (user chose to skip)

Sources processed: 12
Lenses: ai
```

## Edge Cases

- **No vault configured**: Error with "Set $LLM_WIKI_VAULT or run /setup first"
- **No sources**: "No sources to compile. Use /collect to add sources first."
- **All sources already compiled**: "All sources are up to date. No new content to compile."
- **Modify loop**: After 3 modify attempts, suggest approving or rejecting
- **Session interrupted**: Pending confirmations are saved in SQLite and can be resumed
- **Conflicting pages**: If two sources compile to the same page title, merge them

## Resuming a pending confirmation

If a confirmation was interrupted (e.g., Claude Code session ended), the next `/compile` run will detect the pending confirmation and resume:

```
Found pending confirmation from 2024-01-15 10:30:00
3 pages awaiting your approval. Showing preview...
```
