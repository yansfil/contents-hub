---
name: contents-hub
description: Use when the user asks how to use the contents-hub CLI, initialize or target a vault, manage subscriptions, fetch/tick sources, run explorations, manage Lenses, run the daemon, produce digests, or troubleshoot the legacy llm-wiki to contents-hub rename.
version: 0.1.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [contents, vault, cli, subscriptions]
    category: productivity
---

# contents-hub CLI

Use this skill to answer practical questions about the local `contents-hub`
command. Prefer concrete commands over theory.

For exploration design sessions that should interview the user, probe browser
surfaces directly with chromux, iterate on lessons learned, and produce a final
`recipe.yaml`, use the dedicated `contents-hub-explore` skill instead of this
general CLI guide.

## Exploration Ownership

Keep exploration orchestration in this skill and the contents-hub app layer.
The Agent SDK receives an approved natural-language mission recipe, browses and
judges autonomously, may delegate or parallelize if useful, and saves qualifying
items through the run-aware persistence tool. Do not ask the Agent SDK to own
user interview, strategy negotiation, recipe revision, approval, or lifecycle
state.

Preferred exploration loop:

1. Draft a recipe workflow from the user's request without opening a browser.
   Include target surfaces, search terms, ranking signals, skip rules,
   extraction fields, persistence expectations, and known risks.
2. Show the workflow to the user for feedback before browser execution when the
   request is broad, multi-surface, expensive, or ambiguous.
3. Probe each target surface independently with chromux. Treat validation as
   feasibility evidence, not full collection. Persist visible extracts,
   blocked reasons, session ids, elapsed time, and any sampled candidates even
   if the final Agent SDK JSON response fails.
4. Run approved recipes as one autonomous Agent SDK mission. The harness creates
   the run record, injects timeout/target item budget, exposes
   `persist_exploration_raw`, and records save/skip/reject trace events.
5. Compile lessons learned from probe/run traces into a revised recipe,
   then ask the user whether to approve, revise, or discard it.

User-facing workflow controls should be semantic: surfaces, recency window,
ranking signals, sample size, required fields, and approval state. Avoid
surfacing Agent SDK implementation details like turn count as product concepts.
Internal guardrails such as wall-clock timeout and persistence cadence are still
valid, but they should protect execution rather than define the user's recipe.

Good division of labor:

- `contents-hub` skill/app: asks clarifying questions, drafts `recipe.yaml`,
  records attempts, stores traces, compares results, revises recipes, and gates
  approval.
- Agent SDK runner: reads the supplied recipe, uses the allowed
  contents-hub/chromux tools, calls `persist_exploration_raw` for accepted
  items, and returns concise execution evidence.
- `chromux`: owns real browser state, visible-page verification, extraction,
  scrolling, and screenshots when useful.

## First Check

When accuracy matters, inspect the installed CLI before answering:

```bash
contents-hub --help
contents-hub sub --help
contents-hub sub add --help
contents-hub fetch --help
contents-hub fetch-all --help
contents-hub daemon --help
contents-hub explore --help
contents-hub exploration --help
contents-hub lens --help
```

If the command is missing, the local checkout can be installed globally with:

```bash
uv tool install -e /Users/hoyeonlee/team-attention/llm-wiki --force
uv tool update-shell
```

## Vault Targeting

Every command accepts `--vault PATH`. Resolution order is:

1. `--vault PATH`
2. `CONTENTS_HUB_VAULT`
3. legacy `LLM_WIKI_VAULT`
4. current working directory

The current canonical metadata paths are:

- `.contents-hub/`
- `.contents-hub.yaml`

Legacy `.llm-wiki/` and `.llm-wiki.yaml` are compatibility fallbacks only.

## Common Commands

Initialize a vault:

```bash
contents-hub --vault /path/to/vault init /path/to/vault
```

List subscriptions:

```bash
contents-hub sub list
contents-hub sub list --format json
contents-hub sub list --type x.profile --format json
```

Add a subscription:

```bash
contents-hub sub add https://www.youtube.com/@team-attention
contents-hub sub add https://x.com/karpathy --type x.profile
contents-hub sub add https://www.threads.net/@example --type threads.profile
contents-hub sub add https://example.substack.com --type substack.publication
```

Optional add flags:

- `--title "Display name"`
- `--type SOURCE_TYPE` or `--source-type SOURCE_TYPE`
- `--filter-prompt "natural language filter"`

Remove a subscription:

```bash
contents-hub sub remove https://x.com/karpathy
```

Fetch one subscription by id or URL:

```bash
contents-hub fetch 15
contents-hub fetch https://x.com/karpathy --max-items 10
```

Subscription fetches use the catalog strategy for the source type and then
persist raw items. They do not auto-explore a site, rewrite recipes, or relearn
after failures; repair/discovery work belongs in the separate exploration
workflow below.

Fetch every active or error subscription regardless of tick schedule:

```bash
contents-hub fetch-all
contents-hub fetch-all --timeout-per-sub 120
contents-hub fetch-all --concurrency 3
```

Collect all due subscriptions:

```bash
contents-hub tick
contents-hub daemon run --json
```

Run the background loop:

```bash
contents-hub daemon loop --interval 30
contents-hub daemon install
contents-hub daemon status
contents-hub daemon uninstall
```

Launch the dashboard:

```bash
contents-hub web --port 8585
```

The dashboard includes:

- `/digests` — latest-first digest list and detail view, backed by the DB.
- `/saved` — raw items explicitly saved from digest article links.

Produce a digest:

```bash
contents-hub digest
```

Digest is a one-shot command. The daemon/fetch loop collects raw items; digest
scheduling is separate unless another scheduler invokes `contents-hub digest`.
Successful digest runs persist the digest body, title, section/item mapping,
and raw item stamps in SQLite. They do not write a markdown file under
`<vault>/digests/`; the CLI returns `path: null`, and the web dashboard reads
the structured DB rows.

Register an exploration from a natural-language request and approved recipe:

```bash
contents-hub explore "Threads feed에서 최근 바이브코딩 노하우 글을 찾고 검색도 같이 활용" --recipe recipe.yaml
contents-hub exploration add "Threads feed에서 최근 바이브코딩 노하우 글을 찾고 검색도 같이 활용" --recipe recipe.yaml
```

Run registered explorations:

```bash
contents-hub exploration list
contents-hub exploration run 3
contents-hub exploration run 3 --timeout 600
contents-hub exploration run-all
contents-hub exploration run-all --timeout-per-exploration 600
```

Explorations are not subscriptions. `explore` / `exploration add` requires
`--recipe` and immediately registers strategy version 1 with the recipe
Markdown or YAML. Missing or empty recipe input returns a JSON error and creates
no exploration row. `exploration run-all` runs registered explorations
sequentially; legacy draft explorations are skipped.

An exploration run is a foreground/manual run. It now uses a single autonomous
Agent SDK path for Markdown or YAML recipes. Recipe fields such as `sources`,
`surfaces`, `steps`, or `fanout` are mission context only; they do not activate
harness-owned harvest/enrich/checkpoint orchestration. In v1, only
`runtime.max_minutes` and `runtime.target_items` are interpreted as structured
controls. The runner exposes `persist_exploration_raw` so accepted candidates
are written directly to `raw_items` during the run, and the tool records
inserted/skipped/rejected trace events so partial progress survives timeouts. It
also exposes `chromux_scroll` and `chromux_scroll_extract` so agents
can scroll/extract feed cards without Bash loops.

Exploration persistence is idempotent by normalized item URL, not by a feed
cursor. Re-running the same registered exploration should not create another
`raw_items` row for the same normalized URL; it records run/discovery
attribution instead. There is no content-diff snapshot model yet, so changed
content at the same URL is not tracked as a separate diff.

Create and manage Lens definitions:

```bash
contents-hub lens create vibe-coding --name "Vibe coding" --description "Concrete vibe coding workflows" --keyword "바이브코딩" --keyword "Claude Code"
contents-hub lens create ai-research --disabled
contents-hub lens list
contents-hub lens list --format json
contents-hub lens list --enabled
contents-hub lens list --disabled
contents-hub lens update vibe-coding --description "Updated criteria" --keyword "바이브 코딩"
contents-hub lens update vibe-coding --clear-keywords
contents-hub lens update vibe-coding --enable
contents-hub lens update vibe-coding --disable
contents-hub lens delete vibe-coding
```

Lens ids are slugs used by `contents-hub explore --lens-id ...` and
subscription default Lens settings. Keywords are repeatable; comma-separated
values are also split.

Lens routing is explicit. Subscriptions use `default_lens_ids`; explorations use
repeatable `--lens-id`. Only Lens-matched raw items enter the Lens inbox/digest
flow.

## Chromux Profile Policy

Browser-backed fetches use the shared `contents-hub` Chromux profile, with
legacy `llm-wiki` fallback for existing login state. The current chromux binary
supports both `CHROMUX_PROFILE=<name>` and `chromux --profile <name> ...`.
This repo's examples use `CHROMUX_PROFILE` so the same commands can be switched
to the legacy `llm-wiki` profile when old login state only exists there.

Browser-backed fetches such as `fetch`, `fetch-all`, `tick`, daemon runs, and
exploration runs default to headed Chromux mode with background tab creation
(`CHROMUX_LAUNCH_MODE=headed`, `CHROMUX_OPEN_BACKGROUND=1`) when the shared
profile is not already running. If the shared profile is already open in visible
headed mode, fetches reuse that visible Chrome profile instead of failing or
trying to mode-switch it to headless. Foreground login/settings flows may still
ask for confirmation before interrupting an existing headless automation
browser. Tracked chromux sessions are closed after fetch/exploration runs; the
shared profile itself is preserved for login state.

## Source Types

Prefer canonical source types when the user asks what to pass to `--type`:

- `rss.feed`
- `youtube.channel`
- `x.profile`
- `linkedin.profile`
- `threads.profile`
- `substack.publication`
- `medium.publication`
- `reddit.subreddit`
- `webpage`

The CLI can infer many URLs, so `--type` is optional unless the user wants to
force a source type.

## Output Contract

`fetch`, `fetch-all`, `tick`, `daemon run --json`, `sub add`,
`sub list --format json`, `explore`, lifecycle-changing `exploration`
commands, and lifecycle-changing `lens` commands are intended to be
machine-readable JSON on stdout. If debugging failures, inspect logs under the
resolved metadata directory:

```bash
tail -n 100 .contents-hub/cli.log
tail -n 100 .contents-hub/web.log
```

## Answering Rules

- In Korean conversations, answer in Korean.
- If the user asks "what arguments do I pass?", start with the exact command
  shape and whether `--type` is optional.
- If the user asks "does this work now?", run a smoke command when local
  access is available.
- For current command syntax, trust `contents-hub --help` over memory.
