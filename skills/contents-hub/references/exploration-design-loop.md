# contents-hub Exploration Design Loop

Use this reference when designing and proving a contents-hub exploration workflow through user interaction, direct chromux probing, iterative lesson learning, and a final `recipe.yaml` for later Agent SDK execution.

## Core boundary

- Agent/Hermes skill layer: user interaction, workflow design, browser probing, evidence collection, lessons learned, recipe revision, final approval.
- `chromux`: real browser access, visible page checks, extraction, scrolling, screenshots, and tab cleanup.
- Agent SDK runner: executes an already-approved recipe as one autonomous mission and calls `persist_exploration_raw` for accepted items.
- `contents-hub` CLI/app: persists explorations, raw items, strategy versions, and run history when the current CLI supports the needed operation.

Do not ask contents-hub to draft, validate, or approve the strategy. The skill produces the recipe; contents-hub registers and runs it.

## Workflow

### 1. Clarify exploration shape

Ask only the major questions needed to make the workflow executable. Clarify surfaces, recency window, ranking signals, required fields, exclusions, and output goal. If the user already gave enough direction, draft the first workflow and ask for feedback rather than over-interviewing.

### 2. Draft workflow V1 before browser work

Use a compact draft with:

```md
# Goal
# Surfaces
# Search Terms
# Ranking Signals
# Candidate Rules
# Harvest Fields
# Probe Plan
# Known Risks
```

Show it to the user when the task is broad, expensive, or ambiguous. For quick experiments, state the draft briefly and proceed to probing.

### 3. Probe with chromux directly

For each surface:

1. Open a dedicated session id.
2. Verify visible state with `snapshot`, `run`, or extracted text.
3. Try the planned search/navigation path.
4. Extract a small sample of visible candidates and reaction signals.
5. Record blockers such as login wall, empty results, rate limit, unstable selectors, missing reaction counts, or slow loading.
6. Close opened tabs unless the user asked to keep them.

Probe surfaces independently; a failure on one surface should become a surface-specific note.

Good evidence includes URL/title after navigation, visible excerpts, repeated selectors/link patterns, reaction count text, login wall text, elapsed time, and screenshot path when visual confirmation matters.

### 4. Revise from evidence

Summarize what worked, what failed, durable selectors/URLs/search terms, visible ranking signals, reliable fields, and skip rules. Revise into Recipe V2. Ask the user if evidence changes the product direction; directly update tactical browser steps.

### 5. Produce final recipe.yaml

Keep the schema intentionally small. The recipe collects `raw_items`; Lens selection is outside the recipe.

```yaml
goal: Collect useful raw_items for the request.
keep:
  - Concrete item rule.
  - Another useful keep rule.
skip:
  - Explicit exclusion.
  - Another skip rule.
sources:
  - surface: news
    search: "AI news May 16"
  - surface: web
    search: "agentic workflow May 16"
  - surface: x
    search: "Claude Code workflow"
  - surface: reddit
    search: "vibe coding agent workflow"
  - surface: linkedin
    search: "AI agent workflow"
runtime:
  max_minutes: 10
  target_items: 12
```

Supported intent fields:

- `goal`: one-sentence collection target.
- `keep`: rules for what enters the raw item queue.
- `skip`: explicit exclusions.
- `sources`: advisory surface/query/URL starting points.
- `surface`: short id such as `news`, `web`, `x`, `reddit`, `threads`, `linkedin`, or `youtube`.
- `search`, `url`, or `urls`: starting point.
- `runtime.max_minutes`: optional wall-clock cap.
- `runtime.target_items`: optional target number of qualifying raw items.

If a source needs browser detail, add plain keys such as `entry_url`, `query_hint`, `ranking`, `blocked_when`, `selector_note`, or `stop_when`. Avoid inventing a large schema without evidence.

### 6. Show recipe summary and ask

Before persistence, show recipe path, one-sentence goal, target surfaces, date/recency boundary, search-term families, ranking signals, accepted/skipped rules, output highlights, runtime limits, persistence expectations, important probe lessons/risks, and readiness state.

### 7. Mandatory persistence confirmation gate

Stop before any real vault mutation. Before `contents-hub exploration add`, `contents-hub explore`, `contents-hub exploration run`, or `contents-hub exploration run-all`, show the original request, final recipe path, summary, target vault path, target surfaces, and whether registration only or registration+run is planned. Ask for explicit confirmation and wait.

Accept compact confirmations such as `yes`, `go`, `등록해`, `실행해`, or `ㅇㅇ`. If the user asks a question or changes scope, answer/revise instead of persisting.

### 8. Register or hand off

Inspect current CLI help if runtime may be stale:

```bash
contents-hub exploration --help
contents-hub exploration add --help
```

Register:

```bash
contents-hub exploration add "request..." --recipe recipe.yaml
```

Run only if explicitly confirmed:

```bash
contents-hub exploration run ID
```

## Operating rules

- Prefer Korean when the user asks in Korean.
- Keep browser probes small and evidence-backed.
- Turn failed probes into recipe constraints.
- Do not persist credentials, cookies, personal data, or one-off task diary prose as durable recipe material.
- Do not use WebFetch/WebSearch as a substitute for browser-backed surfaces when the recipe is meant to prove chromux behavior.
- Use semantic limits (recency, sample size, surface priority, persistence timing), not Agent SDK turn count, as product concepts.
- Say which vault path is being modified before persistent operations.
