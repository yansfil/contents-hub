# contents-hub-explore

This skill owns the exploration design loop. It is intentionally fatter than the
Agent SDK runner: interview the user, draft the workflow, use `chromux` directly
to probe real browser surfaces, revise the workflow from evidence, and produce a
final `recipe.yaml`. The Agent SDK later reads that recipe as a mission brief,
executes autonomously, and saves accepted items through the run-aware persistence
tool; do not make the Agent SDK own the user interview or approval loop.

## Core Boundary

- This skill: user interaction, workflow design, browser probing, evidence
  collection, lesson learned, recipe revision, and final approval.
- `chromux`: real browser access, visible page checks, extraction, scrolling,
  screenshots, and tab cleanup.
- Agent SDK: executes an already-approved recipe as one autonomous mission and
  calls `persist_exploration_raw` for accepted items.
- `contents-hub` CLI/app: persists explorations, raw items, strategy versions,
  and run history when the current CLI supports the needed operation.

Do not ask contents-hub to draft, validate, or approve the strategy. This skill
produces the recipe; contents-hub only registers the final recipe and runs it.

## Workflow

### 1. Clarify the Exploration Shape

Ask only the major questions needed to make the workflow executable. Prefer
compact questions and make reasonable defaults for details.

Clarify:

- surfaces: Threads, X, Reddit, LinkedIn, YouTube, web, etc.
- recency window: latest, last 24h, 7d, month, all time
- ranking signals: likes, reposts, comments, upvotes, views, freshness
- required fields: URL, author, published time, summary, reaction counts,
  comments/replies, screenshots, outbound links
- exclusions: ads, job posts, promotions, duplicates, low-context posts
- output goal: raw item queue, report, digest input, or recurring recipe

If the user already gave enough direction, do not over-interview. Draft the
first workflow and ask for feedback.

### 2. Draft Workflow V1

Create a brief workflow draft before opening the browser. Include:

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

Show the draft to the user when the task is broad, expensive, or ambiguous.
For quick experiments, state the draft briefly and proceed to probing.

### 3. Probe With chromux Directly

Use `chromux` yourself; do not delegate probing to the Agent SDK.

For each surface:

1. Open the surface in a dedicated session id.
2. Verify the visible state with `snapshot`, `run`, or extracted text.
3. Try the planned search/navigation path.
4. Extract a small sample of visible candidates and reaction signals.
5. Record blocked reasons such as login wall, empty results, rate limit,
   unstable selectors, missing reaction counts, or slow loading.
6. Close tabs you opened unless the user asked to keep them.

Probe surfaces independently. A Reddit probe failure should not invalidate a
Threads recipe, and an X login wall should become a surface-specific note.

Use direct `chromux` commands and raw page state as evidence. Good evidence:

- URL and title after navigation
- visible text excerpts
- repeated card selectors or link patterns
- reaction count text
- login wall text
- elapsed time per surface
- screenshot path when visual confirmation matters

### 4. Revise Workflow From Evidence

After probing, write a short lesson-learned summary:

- what worked
- what failed or was blocked
- which selectors/URLs/search terms looked durable
- which ranking signals were visible
- which fields can be collected reliably
- what should be skipped in the execution recipe

Then revise the workflow into Recipe V2. If the evidence changes the product
direction, ask the user before continuing. If the evidence only changes tactical
browser steps, update the recipe directly.

### 5. Produce Final recipe.yaml

The final artifact should usually be a simple YAML recipe. The recipe's job is
only to collect `raw_items`; Lens selection is outside the recipe. If no Lens is
selected when the exploration is registered, contents-hub evaluates all enabled
Lenses by default after raw items are persisted.

Use this main template:

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

Keep the schema intentionally small:

- `goal`: one sentence describing the raw-item collection target.
- `keep`: what should enter the raw item queue.
- `skip`: what should be ignored.
- `sources`: advisory surface/query starting points. The harness does not fan
  them out; the autonomous agent chooses how to use or delegate them.
- `surface`: short surface id such as `news`, `web`, `x`, `reddit`, `threads`,
  `linkedin`, or `youtube`.
- `search`, `url`, or `urls`: where the agent should start.
- `runtime.max_minutes`: optional wall-clock cap for the run.
- `runtime.target_items`: optional target number of qualifying raw items.

The harness stays thin: pass the recipe text as the mission brief, inject
runtime limits, expose `persist_exploration_raw`, dedupe by item key, record
inserted/skipped/rejected trace events, persist `raw_items`, and evaluate
Lenses. Do not put Lens ids, reports, digest formatting, or final synthesis
instructions in the recipe.

If a surface needs more browser detail, put it directly on that source item with
plain keys such as `entry_url`, `query_hint`, `ranking`, `blocked_when`,
`selector_note`, or `stop_when`. Avoid inventing a large schema unless the
current probe proves that field is needed.

### 6. Show Recipe Summary And Ask

After producing the final recipe file, show a compact recipe summary in the
chat before asking for persistence. The user should not have to open the file to
understand what will happen.

Include:

- recipe path
- goal in one sentence
- target surfaces
- recency/date boundary
- search-term families
- ranking signals
- accepted/skipped candidate rules
- output schema highlights
- runtime limits and persistence expectations
- important probe lessons or known risks
- whether the recipe is ready to register, needs edits, or should only be kept
  as a handoff artifact

Then ask the user what to do next. Accept compact instructions such as
"수정해", "등록해", "등록하고 실행해", "요약만", or "다시 프로브해".

If the user asks for changes, revise the recipe and show the updated summary
again. Do not proceed to persistence until the user has seen this summary and
then explicitly confirms registration or execution.

### 7. Mandatory Persistence Confirmation Gate

Stop before any persistent contents-hub operation. This is required even when
the user gave a broad exploration request and even when the recipe seems obvious.

Before running `contents-hub exploration add`, show the user:

- the original request
- the final recipe path
- the recipe summary from step 6, or a clear note that it was already shown and
  remains unchanged
- the target vault path
- the target surfaces
- whether you will only register or also start a manual run

Ask for explicit confirmation in plain language, then wait. Do not treat silence,
a status update, or earlier task approval as permission to register. Do not run
`contents-hub exploration add`, `contents-hub explore`, `contents-hub exploration
run`, or `contents-hub exploration run-all` until the user confirms this
specific persistence step.

Accept compact confirmations such as "yes", "go", "등록해", "실행해", "ㅇㅇ",
or equivalent. If the user only asks a question or changes scope, answer or
revise the recipe instead of persisting.

### 8. Register or Hand Off

Register the final recipe file with contents-hub:

```bash
contents-hub exploration add "request..." --recipe recipe.yaml
```

Use the current CLI only after inspecting it when the runtime may be stale:

```bash
contents-hub exploration --help
contents-hub exploration add --help
```

After registration, use `contents-hub exploration run ID` for a manual run only
if the user explicitly confirmed that run in the persistence gate.

## Operating Rules

- Prefer Korean when the user asks in Korean.
- Keep browser probes small and evidence-backed.
- Do not hide failed probes; turn them into recipe constraints.
- Do not persist credentials, cookies, personal data, or one-off task diary
  prose as durable recipe material.
- Do not use WebFetch/WebSearch as a substitute for browser-backed surfaces
  when the recipe is meant to prove chromux behavior.
- Do not make Agent SDK turn count a user-facing workflow concept. Use semantic
  limits such as recency, sample size, surface priority, and persistence timing.
- If using a real vault, say which vault path is being modified before creating
  persistent exploration records.
- Never create, register, run, or run-all an exploration in a real vault without
  the explicit confirmation required by the persistence gate.
