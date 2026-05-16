# AGENTS.md

This file provides guidance to Codex and other coding agents when working with code in this repository.

## Operational docs

- Global CLI installation, vault initialization, and repo-outside usage are documented in `docs/initialization.md`.
- When changing CLI bootstrap, editable install behavior, `CONTENTS_HUB_VAULT` / legacy `LLM_WIKI_VAULT`, launchd setup, or first-run instructions, update `docs/initialization.md` in the same change.
- Agent-facing skills live under `skills/`: `contents-hub` for CLI/vault operations and `contents-hub-explore` for recipe-first exploration design. Runtime registration is documented in `install.md`; Codex installs real `SKILL.md` copies, while Claude Code / Hermes can use symlinks. When the CLI surface (`contents-hub --help`, `sub`, `fetch`, `tick`, `daemon`, `digest`, `explore`, `exploration`, `lens`) changes, update `skills/contents-hub/SKILL.md` in the same change. When the exploration design lifecycle changes, update `skills/contents-hub-explore/SKILL.md`.
- Claude Code imports this file through `CLAUDE.md` via `@AGENTS.md`; keep this file as the repo-local source of truth.

## What this is

A Python tool (`contents_hub`) that collects sources into an Obsidian vault. The legacy `llm_wiki` import path remains as a compatibility shim during the rename window. The user subscribes to RSS/YouTube/Twitter/webpages/LinkedIn; a scheduler daemon polls them on a cron, deduplicates, and stores the raw items. There is a FastAPI web dashboard for subscription management and an opinionated "browser fetcher" that uses the Claude Agent SDK + chromux to scrape sites that don't offer an RSS feed.

**Scope note (0.2).** This repo was aggressively simplified — the former LLM "compile" pipeline (source → synthesized wiki page), lens routing, classify/promote, lint, semantic search, and the Claude Code plugin surface (commands/skills/agents) were all removed. The `pre-simplify-backup` branch preserves them if any feature needs to be restored.

## Common commands

Use the `./dev` wrapper — it runs inside `uv`'s venv.

```
./dev web              # FastAPI dashboard on http://localhost:8585
./dev daemon           # scheduler daemon loop (foreground)
./dev test             # pytest (arg passthrough)
./dev sync             # uv sync --all-extras
```

Single test: `./dev test tests/test_rss.py::TestCase::test_name`.

`pyproject.toml` sets `asyncio_mode = "auto"`, so `async def` tests don't need `@pytest.mark.asyncio`.

No linter is wired up. `.ruff_cache/` exists but no ruff config in `pyproject.toml`.

## CLI surface

`contents-hub` / `python -m contents_hub` subcommands (`src/llm_wiki/cli.py`, exposed through the canonical `contents_hub` package path):

- `init [path]` — scaffold a new vault (`.contents-hub/`, `sources/`, SQLite schema)
- `sub {add,remove,list}` — subscription CRUD
- `daemon {run,loop,install,uninstall,status}` — background collector; `install` writes a macOS launchd plist (`launchd.py`)
- `web [--port N]` — launch the FastAPI dashboard

Every subcommand accepts `--vault PATH`. Fallback order: explicit `--vault`, then `$CONTENTS_HUB_VAULT`, then legacy `$LLM_WIKI_VAULT`, then CWD (`config.resolve_vault_path`).

## Vault layout (user's Obsidian vault, not this repo)

```
<vault>/
├── .contents-hub.yaml        # user config (schedule defaults, etc.)
├── .contents-hub/
│   ├── state.db              # SQLite: subscriptions, raw_items, schedules, job_runs, lenses
│   ├── cli.log / daemon.log / web.log
│   └── plugins/              # optional: bundled sub-plugins (chromux browser-explorer)
├── sources/                  # (future) promoted source files
└── ...
```

Existing `.llm-wiki.yaml` and `.llm-wiki/` vault metadata are legacy compatibility inputs and must not be orphaned by silently creating an empty canonical state.

The `sources/` directory is still the intended landing zone for promoted items, but the promotion pipeline (raw_items → sources/) moved out with the simplification. The daemon currently populates `raw_items` and stops there.

## Architecture — 3 layers

```
 subscriptions       ─► fetchers         ─► raw_items (SQLite)
 (SubscriptionStore)   (RSS/YouTube/
                        Twitter/Browser)
```

### Fetchers (`src/llm_wiki/fetchers/`)

- `rss.py` / `youtube.py` — fast path, pure HTTP. Return `FetchedItem[]`. No agent, no tokens.
- `twitter.py` — Nitter-RSS with chromux fallback.
- `browser.py` — **the interesting one**. 3-mode recipe-driven agent fetch via `AgentRunner`:
  - **EXPLORE**: no recipe yet → agent visits the site, writes a LIST/CONTENT/METADATA recipe, saves as override.
  - **EXECUTE**: recipe exists (seed or override) → agent follows it, returns items.
  - **RELEARN**: ≥3 consecutive failures → agent rewrites the recipe.
- `registry.py` — `source_type → fetcher` factory. Used by `daemon._collect_subscription`.

### Runners (`src/llm_wiki/runners/`)

Thin abstraction so the agent backend can be swapped.

- `base.AgentRunner` protocol — `run(prompt, *, max_turns, timeout) -> str`.
- `claude_sdk.ClaudeSDKRunner` — the only concrete implementation. Wraps `claude_agent_sdk.query()`, loads the bundled `contents-hub-browser` plugin if discoverable, with legacy `llm-wiki-browser` fallback, runs under `bypassPermissions`.
- `get_default_runner()` / `set_default_runner()` — process-wide singleton + test override.

**Rule**: if you need an LLM — whether a multi-turn agent with tools (browser fetch) or a single-turn classifier (filter.py) — call `get_default_runner().run(...)`. Do not import `claude_agent_sdk` or `anthropic` from anywhere except `runners/claude_sdk.py`. No module outside runners should require `ANTHROPIC_API_KEY`.

### Browser automation philosophy

The browser layer should follow the core browser-harness philosophy, adapted to
this repo's `chromux` stack: keep the harness thin, inspectable, and editable;
let agents add small task/site helpers when something is missing; and preserve
what actually works as reusable playbooks instead of rediscovering selectors and
flows every run.

- Prefer a direct browser-control surface over a heavy orchestration layer.
  `chromux_*` tools should expose small primitives: navigate, extract, scroll,
  checkpoint, and raw CDP/JS escape hatches. Do not add a manager framework,
  retry framework, session supervisor, or broad config system unless the current
  failure mode proves it is necessary.
- Treat the browser as a stateful runtime, not a stateless HTTP fetch. After any
  meaningful action, verify the visible/page state before assuming success.
  Prefer snapshots, extracted visible text, screenshots where useful, and
  structured trace artifacts over silent success.
- Use the cheapest suitable surface. Static RSS/YouTube/HTTP paths should stay
  pure HTTP. Use `chromux` only when login, JS-rendered state, scrolling,
  cross-origin UI, or human-visible interaction actually matters.
- For repeated feeds/searches, make the agent capture durable mechanics:
  repeated card selectors, stable URL patterns, pagination/scroll stop rules,
  login-wall signals, hidden waits, and extraction fields. Store these as
  recipe/playbook material, not as one-off run narration.
- Exploration strategies should behave like editable research recipes. Favor
  Markdown playbooks over rigid JSON schemas when the core value is an
  agent-readable sequence of browser actions, skip rules, checkpoint rules, and
  fallback boundaries. If limits, surfaces, or other run constraints are needed,
  write them as normal sections in the same Markdown strategy instead of
  splitting them into a separate metadata contract too early.
- Checkpoint early in long browser runs. If an item is good enough to enter the
  review queue, write it through `append_checkpoint` before changing query,
  surface, or page. A timeout that loses all accepted candidates is a workflow
  failure.
- Keep site knowledge public and non-secret. Never persist credentials, cookies,
  personal data, brittle pixel coordinates, or task diary prose as reusable
  playbook material.

### Recipes (`src/llm_wiki/recipes/`)

Natural-language instructions for the agent. Not code.

- `seed/{source_type}.md` — built-in recipes for common platforms (linkedin, reddit, substack, medium, twitter, youtube, rss).
- `templates/{explore,execute,relearn}_prompt.md` — the prompt skeleton injected into each agent run.
- `RecipeRegistry` (`__init__.py`) — resolves recipe order: subscription-config override → seed.

The subscription's `config.recipe` field is mutated in place when EXPLORE/RELEARN produces a new recipe.

### Daemon (`src/llm_wiki/daemon.py`)

- `daemon_tick(config)` — one cycle: query due subscriptions → dispatch to `_collect_{youtube,browser,rss}` → dedup via `raw_items.url UNIQUE` → update subscription state.
- `daemon_loop(config, interval_minutes)` — tick forever.
- `launchd.py` — generates/installs a macOS LaunchAgent plist that runs `python -m contents_hub daemon loop`. Linux/Windows not supported; legacy `com.llm-wiki.daemon` state must be handled during migration.

## Database

SQLite at `<vault>/.contents-hub/state.db` for new vaults, with legacy `<vault>/.llm-wiki/state.db` fallback for existing vaults. Schema is defined inline in `src/llm_wiki/db.py` with `SCHEMA_VERSION = 8`. Migration scripts are one-offs in `scripts/`.

Tables used by the surviving code: `subscriptions`, `schedules`, `schedule_runs`, `raw_items`, `fetch_cursors`, `job_runs`. The `lenses` / `raw_item_lenses` tables still exist in the schema (not migrated out) but nothing writes to them anymore.

## Web UI (`src/llm_wiki/web/`)

FastAPI + Jinja2. Routes:

- `GET /` — overview (counts, recently saved)
- `GET /subscriptions`, `POST /subscriptions/add`, `POST /subscriptions/classify` (live-classify URL)
- `GET /subscriptions/{id}` — detail (recipe, schedule, history, raw items)
- `POST /subscriptions/{id}/collect|relearn|schedule|delete|open_login|confirm_auth`

The "fetch now" and "relearn" buttons both go through the same `BrowserFetcher` the daemon uses — single source of truth.

## Conventions worth preserving

- **One AgentRunner call-site pattern.** `executor.py` is the only wrapper. All agent traffic funnels through `runner.run()`.
- **Recipe drift is OK.** EXPLORE/RELEARN overwriting a seed is intentional — the seed is a starting point, not a contract. If a seed update looks better than the stored override, the user resets via the web UI (`/subscriptions/{id}/relearn`) or by clearing `config.recipe`.
- **Sources are immutable (future state).** When the promotion pipeline comes back, anything written to `sources/` must not be mutated.
- **Frontmatter via `contents_hub.frontmatter`.** No hand-rolled YAML serialization elsewhere.
- **Flat module layout.** Only `recipes/`, `runners/`, `tools/`, `web/` are allowed subpackages.
- **Agent-agnostic direction.** New agent logic should go through `AgentRunner`. Phase B is to add a `ClaudeCodeRunner` / `CodexRunner` under `runners/`; the core logic should not know which is active.
