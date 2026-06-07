# Hermes Setup Runbook

Use this runbook when a Hermes agent is asked to install contents-hub, configure
scheduled digest jobs, or connect a Hermes gateway or external adapter delivery
path.

Hermes owns profiles, gateway lifecycle, cron scheduling, and message delivery.
contents-hub owns local CLI state, the SQLite vault, subscriptions, raw items,
digests, outbound message mappings, and interaction handling.

## Setup Decision

First decide which Hermes pattern the user wants:

| Pattern | Use When | contents-hub state |
| --- | --- | --- |
| Manual smoke | First install or troubleshooting | Vault only |
| Cron final response | Daily digest delivered by Hermes | Digests only |
| Adapter delivery | Per-card message ids and reactions | Digests, outbound mappings, interaction events |
| Script-only cron | Silent watchdogs or deterministic fetch jobs | Vault only unless script calls delivery commands |

Do not promise reaction round-trips unless you are implementing adapter delivery.
Hermes cron final-response delivery sends the final agent response; it does not
automatically call `delivery record` for each contents-hub card.

## Profile-Aware Install

Hermes profiles can have separate skills, config, gateway state, and cron jobs.
The default profile and the profile that owns the gateway may be different.

Inspect before changing anything:

```bash
hermes profile list
hermes gateway list
hermes cron list
```

If the user names a profile, use it explicitly:

```bash
hermes --profile <profile> skills install skills-sh/yansfil/contents-hub/skills/contents-hub --yes
hermes --profile <profile> skills list
```

If your Hermes version does not accept global `--profile` before `skills`, switch
profiles first or install from the target profile's session. For cron jobs, pin
the run profile with `--profile <profile>`:

```bash
hermes cron create "0 8 * * *" \
  "Use the contents-hub skill. Run contents-hub --vault ~/contents-vault fetch-all. If it succeeds, run contents-hub --vault ~/contents-vault digest. Report the digest id and any fetch errors." \
  --skill contents-hub \
  --profile <profile> \
  --workdir "$HOME/contents-hub" \
  --deliver local \
  --name contents-hub-daily
```

After creating jobs:

```bash
hermes cron list
hermes cron status
```

## CLI And Vault Setup

Install the local CLI once per machine:

```bash
git clone https://github.com/yansfil/contents-hub "$HOME/contents-hub"
cd "$HOME/contents-hub"
uv tool install -e "$PWD" --force
uv tool update-shell
contents-hub --help
```

Initialize or reuse one vault:

```bash
contents-hub --vault "$HOME/contents-vault" init "$HOME/contents-vault"
contents-hub --vault "$HOME/contents-vault" raw add "Hermes setup smoke" --title "Hermes setup"
contents-hub --vault "$HOME/contents-vault" digest
```

For RSS feeds, create a Lens first and force the RSS source type:

```bash
contents-hub --vault "$HOME/contents-vault" lens create ai --name "AI" --keyword ai
contents-hub --vault "$HOME/contents-vault" sub add <rss-feed-url> --type rss.feed --title "Example"
contents-hub --vault "$HOME/contents-vault" fetch-all
contents-hub --vault "$HOME/contents-vault" digest
```

For browser-backed sources, open the one fixed profile and let the user sign in:

```bash
contents-hub --vault "$HOME/contents-vault" browser open
contents-hub --vault "$HOME/contents-vault" browser status
```

## Existing Vault Safety

Default rule: preserve the vault.

Before reset, migration, or cron rewiring:

```bash
VAULT="$HOME/contents-vault"
cp "$VAULT/.contents-hub/state.db" "$VAULT/.contents-hub/state.db.backup.$(date +%Y%m%d%H%M%S)"
contents-hub --vault "$VAULT" sub list --format json
contents-hub --vault "$VAULT" lens list --format json
contents-hub --vault "$VAULT" delivery list --limit 20
```

Do not delete these tables during setup:

- `raw_items`
- `digests`
- `lenses`
- `raw_item_lenses`
- `saved_items`
- `outbound_messages`
- `interaction_events`

If the user asks to reset subscriptions only, prefer removing subscriptions
through CLI commands. If direct SQLite work is unavoidable, stop and ask for
explicit confirmation after backing up `state.db`.

Remember that Hermes profile state and contents-hub vault state are separate:
removing a Hermes cron job does not delete `.contents-hub/state.db`, and deleting
the vault does not remove Hermes cron jobs.

## Recommended Cron Topology

### Hourly Fetch Watchdog

Use an agent cron when you want a readable report:

```bash
hermes cron create "0 * * * *" \
  "Use the contents-hub skill. Run contents-hub --vault ~/contents-vault fetch-all. If it succeeds with no errors, respond with [SILENT]. Otherwise report the failed subscription details from the JSON." \
  --skill contents-hub \
  --profile <profile> \
  --workdir "$HOME/contents-hub" \
  --deliver telegram \
  --name contents-hub-fetch-hourly
```

Use script-only no-agent cron when the output is deterministic and you want less
LLM involvement. Scripts must live under the Hermes scripts directory for the
profile that owns the job.

```bash
cat > "$HOME/.hermes/scripts/contents-hub-fetch.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
OUT="$(contents-hub --vault "$HOME/contents-vault" fetch-all)"
python -c 'import json,sys; p=json.loads(sys.argv[1]); sys.exit(0 if p.get("ok") else 1)' "$OUT"
SH
chmod +x "$HOME/.hermes/scripts/contents-hub-fetch.sh"

hermes cron create "0 * * * *" \
  --script contents-hub-fetch.sh \
  --no-agent \
  --profile <profile> \
  --deliver telegram \
  --name contents-hub-fetch-watchdog
```

### Daily Digest

```bash
hermes cron create "0 8 * * *" \
  "Use the contents-hub skill. Run contents-hub --vault ~/contents-vault fetch-all. If fetch-all reports ok:false, report the error and stop. Otherwise run contents-hub --vault ~/contents-vault digest and summarize the digest id and item count." \
  --skill contents-hub \
  --profile <profile> \
  --workdir "$HOME/contents-hub" \
  --deliver telegram \
  --name contents-hub-daily-digest
```

### Optional Exploration

Only schedule approved explorations:

```bash
hermes cron create "30 7 * * *" \
  "Use the contents-hub skill. Run contents-hub --vault ~/contents-vault exploration run-all. Report inserted item counts and errors." \
  --skill contents-hub \
  --profile <profile> \
  --workdir "$HOME/contents-hub" \
  --deliver local \
  --name contents-hub-explorations
```

## Gateway Setup

Configure the gateway in the Hermes profile that will deliver messages:

```bash
hermes --profile <profile> gateway setup
hermes --profile <profile> gateway install
hermes --profile <profile> gateway start
hermes --profile <profile> gateway status
```

For cron delivery, Hermes supports targets such as:

```text
origin
local
telegram
discord
signal
platform:chat_id
```

Run `hermes cron create --help` on the user's machine before using a
platform-specific target. Hermes versions can expose additional gateway targets.
Use the delivery target that the user already configured in Hermes. If no
gateway target is configured, keep `--deliver local` until messaging is ready.

## Adapter Delivery

Use adapter delivery only when the user wants per-card message tracking and
reaction handling.

Adapter loop:

```bash
contents-hub --vault "$HOME/contents-vault" deliver pending --format json
# send each item with a runtime adapter or platform bot
contents-hub --vault "$HOME/contents-vault" delivery record \
  --platform telegram \
  --channel-id <chat_id> \
  --message-id <message_id> \
  --payload-type raw_item \
  --raw-item-id <raw_item_id>
contents-hub --vault "$HOME/contents-vault" interaction handle --event-json '<normalized-event>' --format json
```

The adapter must preserve platform message ids. Without `delivery record`, a
later reaction cannot be mapped back to the original raw item or digest.

## Agent Checklist

Before setup:

- Confirm Hermes profile: `hermes profile list`.
- Confirm gateway owner: `hermes gateway list`.
- Confirm vault path.
- Back up existing `state.db` if it exists.
- List existing cron jobs: `hermes cron list`.
- List existing subscriptions and delivery mappings.

After setup:

- `contents-hub --vault <vault> sub list --format json`.
- `contents-hub --vault <vault> fetch-all`.
- `contents-hub --vault <vault> digest`.
- `contents-hub --vault <vault> web --port 8585`.
- `hermes cron list`.
- `hermes gateway status` if delivery is not local.
- Send one demo delivery/interaction smoke before enabling real adapter
  reaction handling.
