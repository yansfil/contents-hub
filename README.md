# contents-hub

contents-hub is a local-first content inbox for people who want agents,
schedulers, and chat channels to feed the same durable knowledge vault.

It stores subscriptions, raw items, Lens matches, digests, outbound delivery
mappings, and interaction events in SQLite under a vault directory. You can run
it from plain cron, launchd, Hermes, OpenClaw, Claude Code, Codex, or another
loop. The runtime owns scheduling and channel transport; contents-hub owns the
content state and actions.

## Launch Maturity

Reliable first-launch path:

- Install the single `contents-hub` skill.
- Let the agent install the local CLI from this repo.
- Initialize a vault.
- Add manual URL/text items for an immediate inbox digest, or create a Lens and
  add RSS feeds.
- Run `fetch-all`, `digest`, and the dashboard.

Optional or experimental paths:

- Browser-backed sources such as X, LinkedIn, Threads, and arbitrary web pages.
- Exploration recipes.
- External channel delivery through Telegram, Slack, Discord, Hermes, OpenClaw,
  or another gateway.

contents-hub does not ship built-in Slack, Discord, or Telegram bot packages in
the base install. External gateways send messages and call the contents-hub CLI
contract.

## Features

- Add manual URL/text items for the reliable first launch path, with an
  automatic `manual-inbox` Lens when no Lens exists yet.
- Subscribe to RSS feeds and route them through user-created Lenses.
- Try YouTube, web, and browser/agent-backed sources as optional paths.
- Add ad-hoc read-later URLs or text with `raw add`.
- Route raw items through Lenses and produce digest notes.
- Promote saved raw items into immutable `sources/*.md` notes.
- Run a FastAPI dashboard for subscriptions, digests, saved items, and inboxes.
- Open the dedicated `contents-hub` browser profile for manual sign-in.
- Generate adapter-ready delivery payloads with `deliver pending`.
- Record outbound message ids with `delivery record`.
- Handle normalized reactions with `interaction handle`.
- Use Telegram/Hermes as a reference integration shape and Slack/Discord
  fixtures as channel contract examples.

## Product Concepts

- **Vault**: a local directory that owns `.contents-hub/`, `sources/`, and
  generated digest notes, for example `~/contents-vault`.
- **Subscription**: a source definition with URL, type, schedule, and optional
  collection guidance, such as an RSS feed or YouTube channel.
- **Raw item**: an unprocessed collected item in SQLite, such as one article.
- **Lens**: a routing rule that decides which raw items belong in a topic view
  or digest, such as `ai-research`.
- **Digest**: a DB-backed briefing built from Lens-routed raw items.
- **Exploration**: a registered recipe for agent-assisted recurring research.
- **Promotion**: turning a raw item into a source note.
- **Source note**: an immutable Markdown file under `sources/`.

## Install

Recommended: install the single `contents-hub` skill in your agent runtime,
then ask the agent to install the CLI and initialize a vault. The skill points
the agent at the public repo, installs the CLI when needed, and uses CLI
commands for all product behavior.

Manual CLI install:

```bash
git clone https://github.com/yansfil/contents-hub
cd contents-hub
uv sync --all-extras
uv run contents-hub --help
```

For an editable CLI:

```bash
uv tool install -e .
contents-hub --help
```

The base install is runtime-neutral. Claude-backed browser/agent features are
behind the `claude` optional extra:

```bash
uv sync --extra claude --extra dev
CONTENTS_HUB_AGENT_RUNNER=claude-sdk contents-hub --help
```

See [install.md](install.md) for the skill-first install contract, CLI fallback,
runtime shape, and smoke tests.

## Agent Skills

contents-hub ships one repo-local skill so coding agents can install the CLI, pick
the correct vault, add subscriptions, fetch content, design explorations, run
digests, and wire channel interactions without re-reading the whole codebase.

- [install.md](install.md) - skill-first setup, CLI fallback, smoke tests, and
  runtime shape
- [skills/contents-hub/SKILL.md](skills/contents-hub/SKILL.md) - CLI, vault,
  subscription, exploration, digest, delivery, and interaction operations
- [AGENTS.md](AGENTS.md) - repository guidance for coding agents

Use `chromux` for browser-backed exploration when it is available. contents-hub
does not require chromux for base RSS/manual/digest workflows.

## Quickstart

```bash
contents-hub init ~/contents-vault
contents-hub --vault ~/contents-vault raw add "A pasted note" --title "Manual note"
contents-hub --vault ~/contents-vault digest
contents-hub --vault ~/contents-vault web --port 8585
```

Open `http://localhost:8585` for the dashboard.

For RSS, create a Lens first, then add a real feed URL:

```bash
contents-hub --vault ~/contents-vault lens create ai --name "AI" --keyword ai
contents-hub --vault ~/contents-vault sub add <rss-feed-url> --type rss.feed --title "Example"
contents-hub --vault ~/contents-vault fetch-all
contents-hub --vault ~/contents-vault digest
```

## Interaction Flow

1. `contents-hub deliver pending --format json` emits item or digest cards.
2. A channel adapter sends the card to Telegram, Slack, Discord, or another
   surface.
3. The adapter calls `contents-hub delivery record` with the returned message id.
4. A user reacts in the channel.
5. The adapter normalizes the event and calls `contents-hub interaction handle`.
6. contents-hub logs the interaction and applies the configured action, such as
   save-and-promote for `⭐` or `❤️`.

## Core CLI

```text
contents-hub init
contents-hub sub add|remove|list
contents-hub raw add
contents-hub fetch
contents-hub fetch-all
contents-hub tick
contents-hub daemon run|loop|install|uninstall|status
contents-hub digest
contents-hub lens create|list|update|delete
contents-hub explore
contents-hub exploration add|list|run|run-all|delete
contents-hub browser open|status|kill
contents-hub deliver pending
contents-hub delivery record|list
contents-hub interaction handle|rules list
contents-hub web
```

## Documentation

- [Quickstart](docs/quickstart.md)
- [Architecture](docs/architecture.md)
- [Runtime Matrix](docs/runtime-matrix.md)
- [Hermes Setup](docs/hermes-setup.md)
- [OpenClaw Setup](docs/openclaw-setup.md)
- [Schedulers](docs/schedulers.md)
- [Channels](docs/channels.md)
- [Initialization](docs/initialization.md)
- [Launch Checklist](docs/launch.md)

## License

MIT
