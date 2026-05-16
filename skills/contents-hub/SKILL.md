---
name: contents-hub
description: Use when the user asks how to use the contents-hub CLI, initialize or target a vault, manage subscriptions, fetch/tick sources, run explorations, manage Lenses, run the daemon, produce digests, or troubleshoot the legacy llm-wiki to contents-hub rename.
---

# contents-hub CLI

Use this skill to answer practical questions about the local `contents-hub`
command. Prefer concrete commands over theory.

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

Produce a digest:

```bash
contents-hub digest
```

Digest is a one-shot command. The daemon/fetch loop collects raw items; digest
scheduling is separate unless another scheduler invokes `contents-hub digest`.

Create an exploration draft from a natural-language request:

```bash
contents-hub explore "Threads feed에서 최근 바이브코딩 노하우 글을 찾고 검색도 같이 활용"
contents-hub exploration add "Threads feed에서 최근 바이브코딩 노하우 글을 찾고 검색도 같이 활용" --surface threads.feed --surface threads.search
```

Validate, approve, and run an exploration:

```bash
contents-hub exploration list
contents-hub exploration validate 3
contents-hub exploration approve 3
contents-hub exploration approve 3 --attempt-id 12
contents-hub exploration run 3
contents-hub exploration run 3 --timeout 600
contents-hub exploration run-all
contents-hub exploration run-all --timeout-per-exploration 600
```

Explorations are not subscriptions. `explore` / `exploration add` creates a
draft only; validation must succeed and `exploration approve` must register the
strategy before `exploration run` can persist raw items. `exploration run-all`
runs registered explorations sequentially; draft explorations are skipped.

An exploration run is a foreground/manual run. It is currently orchestrated as
Phase 1 list harvest followed by Phase 2 detail enrichment. The runner creates a
run-local JSONL checkpoint and exposes an `append_checkpoint` tool so accepted
candidates can survive a timeout. It also exposes `chromux_scroll` and
`chromux_scroll_extract` so agents can scroll/extract feed cards without Bash
loops.

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
exploration runs default to hidden Chromux mode (`CHROMUX_LAUNCH_MODE=hidden`)
when the shared profile is not already running. If the shared profile is already
open in visible headed mode, fetches reuse that visible Chrome profile instead
of failing or trying to mode-switch it to hidden/headless. Foreground
login/settings flows may still ask for confirmation before interrupting an
existing hidden/headless automation browser. Tracked chromux sessions are closed
after fetch/exploration runs; the shared profile itself is preserved for login
state.

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
