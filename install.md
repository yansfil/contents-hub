---
name: contents-hub-install
description: Install the single contents-hub skill, then let the agent install and operate the contents-hub CLI from that skill.
---

# contents-hub Installation

contents-hub is meant to be installed skill-first.

Install the single `contents-hub` skill in your agent runtime. After that, ask
the agent to install, initialize, subscribe, fetch, digest, deliver, or handle
interactions. The skill contains the CLI install and operating playbook, and the
actual product behavior lives in the `contents-hub` CLI.

## Recommended: Skill-First

Use your agent runtime's skill installer and point it at:

```text
https://github.com/yansfil/contents-hub
skills/contents-hub/SKILL.md
```

If your runtime supports an `npx skills` style installer, install only the
`contents-hub` skill from this repo. The exact command depends on that runtime's
skill installer, but the target is the single `skills/contents-hub/SKILL.md`
file.

Then start a new agent session and ask:

```text
Install contents-hub, initialize a vault at ~/contents-vault, and add this RSS feed: https://example.com/feed.xml
```

The agent should:

1. Read the `contents-hub` skill.
2. Check whether `contents-hub` is already installed.
3. Clone or update a durable checkout.
4. Install the editable CLI with `uv tool install`.
5. Initialize or reuse the requested vault.
6. Use CLI commands such as `sub add`, `fetch-all`, `digest`, `deliver pending`,
   `delivery record`, and `interaction handle`.

## Agent Contract

When an agent has the `contents-hub` skill, it should do the work end to end
unless a user-owned action is required, such as installing Python, installing
`uv`, authenticating to GitHub, choosing a vault path, or resolving uncommitted
changes in an existing checkout.

The default install target is macOS/Linux. Prefer a durable checkout path such
as `$HOME/contents-hub`, not `/tmp`.

## CLI Install Fallback

If you do not use skills, install the CLI directly:

```bash
git clone https://github.com/yansfil/contents-hub "$HOME/contents-hub"
cd "$HOME/contents-hub"
uv sync --all-extras
uv tool install -e "$PWD" --force
uv tool update-shell
contents-hub --help
```

The base install is runtime-neutral. Claude-backed browser/agent features are
optional:

```bash
uv sync --extra claude --extra dev
CONTENTS_HUB_AGENT_RUNNER=claude-sdk contents-hub --help
```

## Runtime Shape

All runtimes use the same CLI contract.

Hermes, OpenClaw, Codex, Claude Code, cron, launchd, Slack, Discord, Telegram,
or another gateway should own scheduling, credentials, webhooks, and message
transport. contents-hub owns local state and content actions.

Common runtime commands:

```bash
contents-hub --vault "$HOME/contents-vault" sub add https://example.com/feed.xml
contents-hub --vault "$HOME/contents-vault" browser open https://x.com/login
contents-hub --vault "$HOME/contents-vault" fetch-all
contents-hub --vault "$HOME/contents-vault" digest
contents-hub --vault "$HOME/contents-vault" deliver pending --format json
contents-hub --vault "$HOME/contents-vault" delivery record ...
contents-hub --vault "$HOME/contents-vault" interaction handle --event-json '<json>'
```

## Smoke Test

Run this after installing or updating:

```bash
VAULT="$(mktemp -d)/vault"
contents-hub --vault "$VAULT" init "$VAULT"
contents-hub --vault "$VAULT" raw add https://example.com/story --title "Example story"
contents-hub --vault "$VAULT" digest
contents-hub --vault "$VAULT" deliver pending --format json
contents-hub --vault "$VAULT" delivery record \
  --platform demo \
  --channel-id demo-channel \
  --message-id demo-message \
  --payload-type raw_item \
  --raw-item-id 1
contents-hub --vault "$VAULT" interaction handle \
  --platform demo \
  --channel-id demo-channel \
  --message-id demo-message \
  --kind reaction \
  --value "⭐" \
  --format json
```

Expected result: the CLI creates a vault, accepts a raw item, produces a
runtime-neutral digest response, emits delivery JSON, records a demo outbound
message, and handles the reaction without provider credentials.

## What To Install

Install one skill:

```text
contents-hub
```

Do not install another contents-hub skill for exploration. Recipe design lives
inside the same `contents-hub` skill, and execution happens through the CLI.
