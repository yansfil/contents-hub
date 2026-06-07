# OpenClaw Setup Runbook

Use this runbook when an OpenClaw agent is asked to install contents-hub,
initialize a vault, add subscriptions, or wire contents-hub into an OpenClaw
task loop.

OpenClaw should treat contents-hub as a local CLI state engine. OpenClaw owns the
agent session, project/global skill scope, scheduling surface, and any external
gateway integration. contents-hub owns the SQLite vault and CLI actions.

## Skill Install

The repo-local skill lives at `skills/contents-hub/SKILL.md`. OpenClaw Git skill
installs expect `SKILL.md` at the install source root, so do not install the repo
root as a skill.

Use a local checkout subdirectory:

```bash
git clone https://github.com/yansfil/contents-hub "$HOME/contents-hub"
cd "$HOME/contents-hub"
openclaw skills install ./skills/contents-hub --as contents-hub --global
```

Omit `--global` if the user wants the skill only for the active OpenClaw
workspace. Start a new OpenClaw session after installing so skills reload.

If `openclaw` is not installed, use the CLI fallback and ask the agent to read
`skills/contents-hub/SKILL.md` from the checkout.

## CLI And Vault Setup

Install the CLI locally:

```bash
git clone https://github.com/yansfil/contents-hub "$HOME/contents-hub"
cd "$HOME/contents-hub"
uv tool install -e "$PWD" --force
uv tool update-shell
contents-hub --help
```

Initialize or reuse a vault:

```bash
contents-hub --vault "$HOME/contents-vault" init "$HOME/contents-vault"
contents-hub --vault "$HOME/contents-vault" raw add "OpenClaw setup smoke" --title "OpenClaw setup"
contents-hub --vault "$HOME/contents-vault" digest
contents-hub --vault "$HOME/contents-vault" web --port 8585
```

For RSS:

```bash
contents-hub --vault "$HOME/contents-vault" lens create ai --name "AI" --keyword ai
contents-hub --vault "$HOME/contents-vault" sub add <rss-feed-url> --type rss.feed --title "Example"
contents-hub --vault "$HOME/contents-vault" fetch-all
contents-hub --vault "$HOME/contents-vault" digest
```

For browser-backed sources, open the fixed contents-hub profile and let the user
sign in manually:

```bash
contents-hub --vault "$HOME/contents-vault" browser open
contents-hub --vault "$HOME/contents-vault" browser status
```

## Existing Vault Safety

Before changing an existing install:

```bash
VAULT="$HOME/contents-vault"
cp "$VAULT/.contents-hub/state.db" "$VAULT/.contents-hub/state.db.backup.$(date +%Y%m%d%H%M%S)"
contents-hub --vault "$VAULT" sub list --format json
contents-hub --vault "$VAULT" lens list --format json
contents-hub --vault "$VAULT" delivery list --limit 20
```

Do not delete `.contents-hub/state.db` during ordinary setup. Do not clear
`raw_items`, `digests`, `lenses`, `raw_item_lenses`, `saved_items`,
`outbound_messages`, or `interaction_events` unless the user explicitly asked
for a destructive reset and a backup exists.

## Recommended Task Topology

Use the scheduling surface that exists in the user's OpenClaw environment. The
commands should be deterministic and parse exit codes:

```bash
contents-hub --vault "$HOME/contents-vault" fetch-all
contents-hub --vault "$HOME/contents-vault" digest
contents-hub --vault "$HOME/contents-vault" deliver pending --format json
```

`fetch-all` exits non-zero when any subscription fails. A scheduler should stop
or report the failure instead of blindly running `digest`.

Recommended jobs:

- Hourly fetch watchdog: run `fetch-all`, report only on non-zero exit or
  `ok:false`.
- Daily digest: run `fetch-all`; if it succeeds, run `digest`; deliver or store
  the digest result.
- Optional exploration: run `exploration run-all` only for user-approved
  explorations.

## Channel Delivery

OpenClaw can use either pattern:

| Pattern | Description |
| --- | --- |
| Runtime final response | OpenClaw runs `fetch-all` and `digest`, then sends the final answer through its own channel/gateway. |
| Adapter delivery | A gateway reads `deliver pending`, sends each card, records message ids with `delivery record`, and forwards reactions to `interaction handle`. |

Adapter loop:

```bash
contents-hub --vault "$HOME/contents-vault" deliver pending --format json
# send each item through an external gateway or platform bot
contents-hub --vault "$HOME/contents-vault" delivery record \
  --platform demo \
  --channel-id <channel_id> \
  --message-id <message_id> \
  --payload-type raw_item \
  --raw-item-id <raw_item_id>
contents-hub --vault "$HOME/contents-vault" interaction handle --event-json '<normalized-event>' --format json
```

The adapter must store platform message ids through `delivery record`. Without
that mapping, reactions cannot be resolved back to contents-hub items.

## Agent Checklist

Before setup:

- Confirm whether the user wants global or workspace-only OpenClaw skill scope.
- Confirm vault path.
- Back up `state.db` if the vault exists.
- List existing subscriptions, Lenses, and delivery mappings.
- Confirm whether delivery is runtime final-response or adapter delivery.

After setup:

- `contents-hub --vault <vault> raw add "Smoke" --title "Smoke"`.
- `contents-hub --vault <vault> digest`.
- `contents-hub --vault <vault> fetch-all` for any configured feeds.
- `contents-hub --vault <vault> web --port 8585`.
- Demo `deliver pending`, `delivery record`, and `interaction handle`.
- Verify the OpenClaw scheduler/gateway task points at the same vault path.
