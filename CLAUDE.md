# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code plugin + Python package (`llm_wiki`) that turns an Obsidian vault into a personal knowledge base. The user subscribes to RSS/YouTube/Twitter/webpages; a scheduler collects new items into immutable source files; an LLM compile step synthesizes them into wiki pages with wikilinks and tags.

The repo wears three hats simultaneously, and edits often need to keep all three consistent:

1. **A Claude Code plugin** — `plugin.json` at the root registers `commands/`, `skills/`, `agents/`. These are the user-facing surface (`/collect`, `/compile`, `/search`, `/pipeline`, `/tick`, etc.) and they invoke the Python package via bash.
2. **A Python package** (`src/llm_wiki/`) — the actual implementation. Invoked as `python -m llm_wiki <subcommand>` or imported from skill/agent shell snippets.
3. **A FastAPI dashboard** (`src/llm_wiki/web/app.py`) — a separate UI launched by `./dev web`.

## Common commands

All dev tasks go through the `./dev` wrapper (it uses `uv` to run inside the project venv):

```
./dev web              # FastAPI dashboard on http://localhost:8585
./dev web --port 9000  # override port
./dev daemon           # scheduler daemon loop
./dev test             # pytest (pytest args passthrough: ./dev test -k foo -x)
./dev sync             # uv sync --all-extras (after pulling)
```

Skills/agents that shell out to Python expect `.venv/bin/python` (not `uv run`) — e.g. `cd ${CLAUDE_PLUGIN_ROOT} && .venv/bin/python -m llm_wiki ...`. Run `./dev sync` to create/refresh `.venv`.

Single test: `./dev test tests/test_compile_orchestrator.py::TestName::test_case`.

Pytest is configured with `asyncio_mode = "auto"` (see `pyproject.toml`), so `async def` tests don't need `@pytest.mark.asyncio`.

There is no lint/format command wired up. `.ruff_cache/` exists but no ruff config is in `pyproject.toml`.

## CLI surface

`python -m llm_wiki` subcommands (`src/llm_wiki/cli.py`):

- `collect` — save a URL/text/memo/file as a source (see `collect_cli.py`)
- `sub {add,remove,list}` — subscription CRUD (SQLite-backed via `subscriptions.py`)
- `lens {create,list}` — lens CRUD (SQLite-backed via `lens_store.py`)
- `daemon {run,loop,install,uninstall,status}` — scheduled fetch; `install` registers a macOS launchd job (`launchd.py`)
- `classify` — promote `raw_items` to source files by matching against lenses (`classifier.py`)
- `pipeline` / `pipe` — build a compile plan from pending sources (`pipeline.build_compile_plan`)
- `web --port 8585` — launch the FastAPI dashboard

Every subcommand accepts `--vault PATH`. Without it, vault is resolved from `$LLM_WIKI_VAULT`, then CWD (`config.resolve_vault_path`).

## Vault layout (written to the user's Obsidian vault, not this repo)

```
<vault>/
├── .llm-wiki.yaml            # user config (schedule defaults, etc.)
├── .llm-wiki/
│   ├── state.db              # SQLite (subscriptions, schedules, raw_items, job_runs, pending_confirmations, embeddings...)
│   ├── cli.log / daemon.log / web.log
│   └── plugins/              # locally-bundled sub-plugins (e.g. llm-wiki-browser)
├── sources/                  # immutable collected items (frontmatter + body)
└── <lens.wikiDirectory>/     # compiled wiki pages, one per lens
```

`sources/` is **append-only**. Files are marked `status: pending` when collected and `status: compiled` after the compile step writes the wiki page.

## Pipeline architecture

The three pipeline stages are strictly separated — do not blend them:

```
 fetch (collectors/) ─► sources/*.md ─► evaluate ─► LLM compile ─► execute ─► wiki/*.md
                          ▲                          (in skill)      (writer)
                          │
                       collect (CLI / webhook)
```

Key design rule: **LLM compilation is NOT in `pipeline.py`** — it happens inside the `/compile` skill, where Claude has direct access to source content. `pipeline.py` orchestrates everything *around* the LLM call (scanning, evaluating, executing the decisions). See the module docstring in `src/llm_wiki/pipeline.py` for the contract.

Stage modules:

- **Fetch** — `collectors/` (rss, youtube, twitter, browser) produce `FetchedItem[]`. Orchestrated by `daemon.py`, `fetch_all.py`, `scheduler_engine.py`.
- **Sources** — `source_writer.py`, `collect_cli.py`, `collect.py` write to `sources/`.
- **Evaluate** — `compile_evaluate.py` reads a source, searches the existing vault (`compile_search.py`, `vault_search.py`, `bm25.py`, `embeddings.py`, `vector_store.py`), and emits `EvaluationResult` with a `Decision` (CREATE / UPDATE / SKIP).
- **Compile orchestration** — `compile_orchestrator.py` groups evaluations by lens + `compileStrategy` (merge / per-source / append) into a tiered plan. Tier 0 runs before tier 1; jobs inside a tier can run concurrently.
- **Preview / confirm** — `preview.py`, `confirmation.py`, `approval.py`. Confirmations can be interrupted and resumed (`pending_confirmations` table).
- **Execute** — `compile_executor.execute_decisions()` dispatches CREATE → `note_creator.create_note()` (Write) and UPDATE → `note_updater.update_note()` (Edit). Missing-target UPDATE falls back to CREATE.

### Lenses

A lens is a user-defined topic bucket (`examples/lenses/ai-research.yml`). It controls:
- `wikiDirectory` — where compiled pages live (e.g. `topics/ai-research`)
- `compileStrategy` — `merge` (default, one page per topic), `per-source` (one page per source), `append` (running log)
- `defaultTags`, `keywords`, `compileInstructions` (injected into the LLM prompt)
- `priority` — lower numbers compile first across tiers

Routing (`routing.py`, `source_router.py`) maps each source to one or more lenses, primarily by matching keywords/tags.

## Plugin, skill, and agent surface

`plugin.json` is the registry. When editing anything under `commands/`, `skills/`, or `agents/`, add/remove it there too — Claude Code only loads what's listed.

- **Commands** (`commands/*.md`) are slash commands. Frontmatter declares `allowed-tools`. Body is a prompt template with instructions to shell out.
- **Skills** (`skills/*.md`) are multi-step workflows that wrap commands. They own the preview/confirm flow and the LLM compilation step that `pipeline.py` intentionally leaves out.
- **Agents** (`agents/*.md`) are subagent specs used by `/tick` to collect one source type each. The browser-collector uses `chromux` CLI for Google + page scraping.

Shell snippets in skill/command bodies use `${CLAUDE_PLUGIN_ROOT}` to reach this repo and read `$LLM_WIKI_VAULT` (or default to `.`) to reach the user's vault.

## Database

SQLite at `<vault>/.llm-wiki/state.db`. Schema is defined inline in `src/llm_wiki/db.py` with `SCHEMA_VERSION = 5`; migrations are one-off scripts in `scripts/` (e.g. `migrate_v5.py`). If you change the schema, bump `SCHEMA_VERSION` and write a migration script — do not silently mutate existing tables.

Main tables: `subscriptions`, `schedules`, `schedule_runs`, `raw_items`, `job_runs`, `fetch_cursors`, `lenses`, `pending_confirmations`, `embeddings`.

## Configuration gotchas

- `.llm-wiki.yaml` in the **vault root** (not this repo) supplies `schedule.defaults`, `schedule.global_interval`, `schedule.global_cron`. See `config.ScheduleConfig`.
- Semantic search (`/search --semantic`) requires `OPENAI_API_KEY` or `VOYAGE_API_KEY`; falls back to BM25 otherwise.
- The browser-collector and `twitter` fetcher depend on external `chromux` CLI being on PATH.
- The daemon's macOS install writes a launchd plist via `launchd.py` — do not use for Linux.

## Conventions worth preserving

- **Sources are immutable.** Never modify a file under `sources/` after creation. Fixes go to the compiled wiki page, not the source.
- **One concern per module.** The file list in `src/llm_wiki/` is long but flat on purpose — resist the urge to build package subdirectories (the existing `collectors/`, `fetchers/`, `recipes/`, `web/` are the only allowed groupings).
- **Frontmatter is centralized.** All YAML frontmatter reads/writes go through `llm_wiki.frontmatter` (`parse_frontmatter`, `assemble_markdown`, `update_file_frontmatter`). Don't hand-roll YAML emission.
- **Tests mirror modules.** `src/llm_wiki/foo.py` ↔ `tests/test_foo.py`. When adding a new module, add a test file even if it only covers imports.
