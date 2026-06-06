# Quickstart

## 1. Install

Agent install:

```bash
curl -L https://raw.githubusercontent.com/yansfil/contents-hub/main/install.md \
  -o /tmp/contents-hub-install.md
# Run the "One-Pass Agent Setup" block from install.md.
```

Manual install:

```bash
git clone https://github.com/yansfil/contents-hub
cd contents-hub
uv sync --all-extras
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

```bash
contents-hub --vault ~/contents-vault sub add https://example.com/feed.xml --title "Example"
contents-hub --vault ~/contents-vault raw add https://example.com/story
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

## 6. Try Delivery And Interaction

```bash
contents-hub --vault ~/contents-vault deliver pending --format json
contents-hub --vault ~/contents-vault delivery record \
  --platform telegram \
  --channel-id demo-channel \
  --message-id demo-message \
  --raw-item-id 1
contents-hub --vault ~/contents-vault interaction handle \
  --platform telegram \
  --channel-id demo-channel \
  --message-id demo-message \
  --kind reaction \
  --value "⭐"
```

See `install.md` for Codex, Claude Code, Hermes, and generic agent skill
registration.
