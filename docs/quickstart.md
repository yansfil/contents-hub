# Quickstart

## 1. Install

Agent install:

Install the single `contents-hub` skill in your agent runtime, then ask the
agent to install the CLI and initialize a vault. See `install.md` for the
skill-first contract.

Manual install:

```bash
git clone https://github.com/yansfil/contents-hub
cd contents-hub
uv sync
```

For an editable command:

```bash
uv tool install -e .
```

## 2. Initialize A Vault

```bash
contents-hub init ~/contents-vault
```

This creates:

```text
~/contents-vault/
  .contents-hub/
    state.db
  sources/
```

## 3. Add Content

Manual URL/text is the shortest first-launch path. If no Lens exists yet,
contents-hub creates and attaches a `manual-inbox` Lens automatically so the
item can appear in the next digest:

```bash
contents-hub --vault ~/contents-vault raw add "A pasted note" --title "Manual note"
```

For RSS, create at least one Lens first so fetched items can be matched into a
digest:

```bash
contents-hub --vault ~/contents-vault lens create ai --name "AI" --keyword ai
contents-hub --vault ~/contents-vault sub add <rss-feed-url> --type rss.feed --title "Example"
contents-hub --vault ~/contents-vault sub list --format json
```

## 4. Fetch And Digest

```bash
contents-hub --vault ~/contents-vault fetch-all
contents-hub --vault ~/contents-vault digest
```

## 5. Open The Dashboard

```bash
contents-hub --vault ~/contents-vault web --port 8585
```

Then open `http://localhost:8585`.

## 6. Sign In For Browser-Backed Sources

For sites that require login, open the dedicated contents-hub browser profile
and sign in manually:

```bash
contents-hub browser open https://x.com/login
contents-hub browser status
```

contents-hub does not store credentials. Browser cookies stay inside the
chromux Chrome profile.

## 7. Try Delivery And Interaction

This smoke uses a demo platform. Real Telegram, Slack, or Discord transport is
owned by an external gateway or agent runtime.

```bash
PENDING="$(contents-hub --vault ~/contents-vault deliver pending --format json)"
RAW_ITEM_ID="$(python3 -c 'import json,sys; p=json.loads(sys.argv[1]); print(p["items"][0]["raw_item_id"])' "$PENDING")"
contents-hub --vault ~/contents-vault delivery record \
  --platform demo \
  --channel-id demo-channel \
  --message-id demo-message \
  --payload-type raw_item \
  --raw-item-id "$RAW_ITEM_ID"
contents-hub --vault ~/contents-vault interaction handle \
  --platform demo \
  --channel-id demo-channel \
  --message-id demo-message \
  --kind reaction \
  --value "⭐"
```

See `install.md` for skill-first setup and generic runtime integration.
