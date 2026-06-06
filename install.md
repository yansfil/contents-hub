---
name: contents-hub-install
description: Install contents-hub and register its agent skills with Codex, Claude Code, or Hermes.
---

# contents-hub Installation

Use this file for CLI installation, vault targeting, daemon setup, and optional
agent-skill registration. Day-to-day usage lives in
`skills/contents-hub/SKILL.md`; exploration recipe design lives in
`skills/contents-hub-explore/SKILL.md`.

contents-hub is a local-first Python CLI and FastAPI dashboard for collecting
subscriptions, raw items, digests, delivery mappings, and channel interactions
into a user-owned vault.

## Requirements

- Python 3.11+
- `uv`
- macOS or Linux for the standard CLI flows
- Optional: `chromux` plus the `claude` extra for agent/browser-backed
  collection

## One-Pass Setup

Run this from any directory. It installs or updates a durable checkout, installs
the CLI, registers the Codex, Claude Code, and Hermes skills, and verifies the
public command surface.

```bash
INSTALL_DIR="${CONTENTS_HUB_DIR:-$HOME/contents-hub}"
REPO_URL="https://github.com/yansfil/contents-hub"

if [ -d "$INSTALL_DIR/.git" ]; then
  cd "$INSTALL_DIR"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "contents-hub checkout has uncommitted changes: $INSTALL_DIR" >&2
    git status --short >&2
    exit 2
  fi
  git pull --ff-only
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

uv sync --all-extras
uv tool install -e "$PWD" --force
uv tool update-shell
command -v contents-hub
contents-hub --help
python -m contents_hub --help

mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub"
cp "$PWD/skills/contents-hub/SKILL.md" "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md"
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore"
cp "$PWD/skills/contents-hub-explore/SKILL.md" "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore/SKILL.md"

mkdir -p "$HOME/.claude/skills/contents-hub"
ln -sf "$PWD/skills/contents-hub/SKILL.md" "$HOME/.claude/skills/contents-hub/SKILL.md"
mkdir -p "$HOME/.claude/skills/contents-hub-explore"
ln -sf "$PWD/skills/contents-hub-explore/SKILL.md" "$HOME/.claude/skills/contents-hub-explore/SKILL.md"

mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub"
ln -sf "$PWD/skills/contents-hub/SKILL.md" "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub/SKILL.md"
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub-explore"
ln -sf "$PWD/skills/contents-hub-explore/SKILL.md" "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub-explore/SKILL.md"
```

New Codex, Claude Code, or Hermes sessions should then load the two
contents-hub skills automatically.

## CLI Setup Only

Clone the repo once into a durable location, then install the CLI globally from
that checkout.

```bash
git clone https://github.com/yansfil/contents-hub "$HOME/contents-hub"
cd "$HOME/contents-hub"
uv sync --all-extras
uv tool install -e "$PWD" --force
uv tool update-shell
contents-hub --help
```

The base install is runtime-neutral. Claude-backed browser/agent features are
available through the `claude` optional extra:

```bash
uv sync --extra claude --extra dev
CONTENTS_HUB_AGENT_RUNNER=claude-sdk contents-hub --help
```

## Vault Targeting

Every command accepts `--vault PATH`. Resolution order is:

1. `--vault PATH`
2. `CONTENTS_HUB_VAULT`
3. current working directory

For repo-outside usage, pin the intended vault in your shell profile:

```bash
export CONTENTS_HUB_VAULT="$HOME/contents-vault"
```

Initialize a new vault once:

```bash
contents-hub --vault "$HOME/contents-vault" init "$HOME/contents-vault"
```

New vault metadata uses `.contents-hub/` and `.contents-hub.yaml`.

## Register Agent Skills

This repo ships two independent skills:

- `contents-hub`: practical CLI and vault operations
- `contents-hub-explore`: exploration design, chromux probing, recipe writing,
  and explicit confirmation before persistent registration or runs

Register both when possible. Register only `contents-hub` if the runtime should
manage subscriptions and daemon tasks but never design explorations.

### Codex

Copy both files as real files under `$CODEX_HOME/skills/`, usually
`~/.codex/skills/`.

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub"
cp "$PWD/skills/contents-hub/SKILL.md" "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md"

mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore"
cp "$PWD/skills/contents-hub-explore/SKILL.md" "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore/SKILL.md"
```

### Claude Code

Symlink both files under `~/.claude/skills/`.

```bash
mkdir -p "$HOME/.claude/skills/contents-hub"
ln -sf "$PWD/skills/contents-hub/SKILL.md" "$HOME/.claude/skills/contents-hub/SKILL.md"

mkdir -p "$HOME/.claude/skills/contents-hub-explore"
ln -sf "$PWD/skills/contents-hub-explore/SKILL.md" "$HOME/.claude/skills/contents-hub-explore/SKILL.md"
```

### Hermes

Symlink both files under `$HERMES_HOME/skills/`, usually `~/.hermes/skills/`.

```bash
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub"
ln -sf "$PWD/skills/contents-hub/SKILL.md" "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub/SKILL.md"

mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub-explore"
ln -sf "$PWD/skills/contents-hub-explore/SKILL.md" "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub-explore/SKILL.md"
```

Avoid symlinking a runtime skill directory to the repo skill directory and then
writing `SKILL.md` through that path. Copy or symlink only the `SKILL.md` file.

## Background Fetch

The fetch loop can be installed as a macOS launchd daemon:

```bash
contents-hub --vault "$HOME/contents-vault" daemon install
contents-hub --vault "$HOME/contents-vault" daemon status
```

Digest generation is a separate one-shot command unless another scheduler
invokes it:

```bash
contents-hub --vault "$HOME/contents-vault" digest
```

Any scheduler that can run shell commands can also call:

```bash
contents-hub --vault "$HOME/contents-vault" fetch-all
contents-hub --vault "$HOME/contents-vault" deliver pending --format json
contents-hub --vault "$HOME/contents-vault" interaction handle --event-json '<json>'
```

## Smoke Test

Run this after installing or updating:

```bash
contents-hub --help
python -m contents_hub --help
contents-hub sub --help
contents-hub raw add --help
contents-hub deliver pending --help
contents-hub delivery record --help
contents-hub interaction handle --help
contents-hub daemon --help
```

## Maintenance Notes

- When CLI behavior changes, update `README.md`, `docs/`, and
  `skills/contents-hub/SKILL.md`.
- When the exploration design lifecycle changes, update
  `skills/contents-hub-explore/SKILL.md`.
- When install paths, runtime registration, editable install behavior, or vault
  targeting changes, update this file and `docs/initialization.md`.
