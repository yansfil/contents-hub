---
name: contents-hub-install
description: Install contents-hub and register its agent skills with Codex, Claude Code, or Hermes.
---

# contents-hub installation

Use this file for contents-hub CLI installation, vault targeting, daemon setup,
and agent-skill registration. For day-to-day CLI usage, read
`skills/contents-hub/SKILL.md`. For exploration design sessions, read
`skills/contents-hub-explore/SKILL.md`.

contents-hub is a Python CLI and FastAPI dashboard for collecting subscription
and exploration results into an Obsidian-style vault. Browser-backed fetches and explorations use chromux through the app/runtime layer.
Ad-hoc read-later intake uses `contents-hub raw add <url_or_text>`: text is
inserted directly, while URL input is canonicalized, deduped, and enriched by
static HTTP first with Chromux/browser fallback when static extraction cannot
produce body text.

## Agent Install Contract

If you are an AI agent and the user asks you to install contents-hub from this
file, do the work end to end without asking follow-up questions unless the next
step requires a user-owned action, such as choosing a vault path, entering a
password, logging into a browser profile, or resolving uncommitted changes in an
existing checkout.

The default supported install target is macOS/Linux.

## One-Pass Agent Setup

Run this from any directory. It installs or updates contents-hub from a durable
checkout, registers the Codex, Claude Code, and Hermes skills, and verifies the
CLI surface.

```bash
INSTALL_DIR="${CONTENTS_HUB_DIR:-$HOME/team-attention/llm-wiki}"
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

uv tool install -e "$PWD" --force
uv tool update-shell
command -v contents-hub
contents-hub --help
contents-hub raw --help
contents-hub raw add --help
contents-hub exploration --help

mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub"
[ -L "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md" ] && rm "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md"
cp "$PWD/skills/contents-hub/SKILL.md" "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md"
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore"
[ -L "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore/SKILL.md" ] && rm "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore/SKILL.md"
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

New Codex, Claude Code, or Hermes sessions should now load the two contents-hub
skills automatically.

## Recommended CLI Setup

Clone the repo once into a durable location, then install the CLI globally from
that checkout so `contents-hub` works from any directory.

```bash
git clone https://github.com/yansfil/contents-hub "$HOME/team-attention/llm-wiki"
cd "$HOME/team-attention/llm-wiki"
uv tool install -e "$PWD" --force
uv tool update-shell
command -v contents-hub
contents-hub --help
```

`llm-wiki` remains a legacy executable alias during the rename window, but new
docs and scripts should prefer `contents-hub`.

## Vault Targeting

Every command accepts `--vault PATH`. Resolution order is:

1. `--vault PATH`
2. `CONTENTS_HUB_VAULT`
3. legacy `LLM_WIKI_VAULT`
4. current working directory

For repo-outside usage, pin the intended vault in your shell profile:

```bash
export CONTENTS_HUB_VAULT="/path/to/obsidian-vault"
```

Initialize a new vault only once:

```bash
contents-hub --vault /path/to/obsidian-vault init /path/to/obsidian-vault
```

New vault metadata uses `.contents-hub/` and `.contents-hub.yaml`. Legacy
`.llm-wiki/` and `.llm-wiki.yaml` are compatibility fallbacks only.

## Register The Agent Skills

This repo ships two independent skills:

- `contents-hub`: practical CLI and vault operations
- `contents-hub-explore`: exploration design loop, chromux probing, recipe
  production, and explicit confirmation before persistent registration/runs

Register both when possible. Register only `contents-hub` if the runtime should
manage subscriptions and daemon tasks but never design explorations.

### Codex

Add both files as global skills under `$CODEX_HOME/skills/`, usually
`~/.codex/skills/`. For Codex, copy `SKILL.md` as a real file instead of
symlinking it; current Codex skill loading may omit symlinked `SKILL.md` files
from the model-visible Available skills list.

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub"
[ -L "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md" ] && rm "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md"
cp "$PWD/skills/contents-hub/SKILL.md" "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub/SKILL.md"

mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore"
[ -L "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore/SKILL.md" ] && rm "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore/SKILL.md"
cp "$PWD/skills/contents-hub-explore/SKILL.md" "${CODEX_HOME:-$HOME/.codex}/skills/contents-hub-explore/SKILL.md"
```

After changing installed Codex skills, verify model-visible availability with a
new Codex session or:

```bash
codex debug prompt-input | rg "contents-hub|contents-hub-explore"
```

### Claude Code

Add both files as Claude Code skills under `~/.claude/skills/`. Symlinks are
acceptable here and keep the runtime pointed at the repo copy.

```bash
mkdir -p "$HOME/.claude/skills/contents-hub"
ln -sf "$PWD/skills/contents-hub/SKILL.md" "$HOME/.claude/skills/contents-hub/SKILL.md"

mkdir -p "$HOME/.claude/skills/contents-hub-explore"
ln -sf "$PWD/skills/contents-hub-explore/SKILL.md" "$HOME/.claude/skills/contents-hub-explore/SKILL.md"
```

### Hermes

Add both files as Hermes skills under `$HERMES_HOME/skills/`, usually
`~/.hermes/skills/`.

```bash
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub"
ln -sf "$PWD/skills/contents-hub/SKILL.md" "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub/SKILL.md"

mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub-explore"
ln -sf "$PWD/skills/contents-hub-explore/SKILL.md" "${HERMES_HOME:-$HOME/.hermes}/skills/contents-hub-explore/SKILL.md"
```

## Background Fetch

The fetch loop can be installed as a macOS launchd daemon:

```bash
contents-hub daemon install
contents-hub daemon status
```

The daemon collects raw items. Digest generation is currently a separate
one-shot command unless another scheduler invokes it:

```bash
contents-hub digest
```

Digest output is DB-backed. Successful runs return `path: null` and are viewed
through the dashboard:

```bash
contents-hub web --port 8585
```

## Smoke Test

Run this after installing or updating:

```bash
contents-hub --help
contents-hub sub --help
contents-hub exploration --help
contents-hub exploration add --help
contents-hub daemon --help
```

Expected exploration surface:

- `contents-hub exploration` includes `add`, `list`, `run`, `run-all`, `delete`
- `contents-hub exploration add` accepts `--recipe`
- legacy draft validation commands are absent

## Maintenance Notes

- When CLI behavior changes, update `skills/contents-hub/SKILL.md`.
- When the exploration design lifecycle changes, update
  `skills/contents-hub-explore/SKILL.md`.
- When install paths, runtime registration, editable install behavior, or vault
  targeting changes, update this file and `docs/initialization.md`.
