# Channel Delivery And Interaction

Channel adapters are thin. They translate between a messaging platform and the
contents-hub CLI contract.

contents-hub does not ship built-in Telegram, Slack, or Discord bot packages in
the base install. External gateways own message transport, credentials,
webhooks, and platform SDKs.

## Delivery

```bash
contents-hub --vault ~/contents-vault deliver pending --format json
contents-hub --vault ~/contents-vault deliver prepare \
  --collect fetch-all \
  --payload-type raw_item \
  --origin subscription \
  --lens-matched \
  --first-seen-only \
  --limit 20 \
  --format json
```

`deliver pending` does not collect. `deliver prepare` can first run
`fetch-all`, `tick`, or no collector, then returns one JSON object with
`collector` and `delivery` keys. The nested delivery response is an object with
`ok`, `count`, and `items`. Each item includes stable ids such as
`raw_item_id`, `digest_id`, `payload_type`, and adapter conveniences such as
`delivery_key`, `dedupe_key`, `plain_text`, and `markdown`.

contents-hub decides which cards are deliverable. Core candidate selection
handles outbound-message exclusion, raw item origin filters, Lens-matched
filters, and first-seen URL suppression. Runtime and channel adapters should
not query SQLite directly to rediscover pending raw items.

Adapters may send raw item cards or digest cards. Digest cards may include
section context in the JSON, but `raw_item` and `digest` are the only
first-class delivery payload types. The adapter should treat contents-hub ids as
opaque stable references and store the platform's returned message id with
`delivery record`.

After sending a card, record the returned platform message id:

```bash
contents-hub --vault ~/contents-vault delivery record \
  --platform demo \
  --channel-id <channel> \
  --message-id <message> \
  --payload-type raw_item \
  --raw-item-id <raw_item_id>
```

## Interaction

Adapters normalize platform events to:

```json
{
  "platform": "demo",
  "event_id": "event-1",
  "workspace_id": "",
  "channel_id": "channel-1",
  "thread_id": "",
  "message_id": "message-1",
  "user_id": "user-1",
  "kind": "reaction",
  "value": "⭐"
}
```

Then call:

```bash
contents-hub --vault ~/contents-vault interaction handle --event-json '<json>'
```

`interaction handle` resolves the event by matching
`platform + workspace_id + channel_id + thread_id + message_id` against
`outbound_messages`. That mapping exists only if the adapter previously called
`delivery record` after sending the card.
Final-response-only delivery is insufficient for reactions because it does not
create per-card rows in `outbound_messages`.

Default rules:

| Reaction | Action |
| --- | --- |
| `👍` | `save_and_promote` |
| `⭐` | `save_and_promote` |
| `❤️` | `save_and_promote` |
| `❤` | `save_and_promote` |
| `✅` | `mark_read` |
| `🗑` | `archive` |

`save_and_promote` inserts the item into `saved_items` and writes the promoted
raw item as a markdown source document in the vault. The action is idempotent:
repeat reactions to the same normalized event are logged once, and already
promoted items are treated as no-ops.

## Reference Integrations

- Telegram/Hermes: reference integration shape using `delivery record` and
  `interaction handle`.
- Slack and Discord: fixture normalizers in `contents_hub.channels` prove the
  shared event shape without requiring SDKs in the base install.

The base package does not import Telegram, Slack, or Discord SDKs. Full bots can
live in optional integration packages or external runtimes as long as they call
the same CLI contract.

Hermes has two usable patterns:

- Cron final-response delivery: let Hermes run `fetch-all` and `digest`, then
  deliver the final response with `hermes cron create ... --deliver telegram`.
  This is enough for a daily digest but does not create per-card message
  mappings in contents-hub.
- Adapter delivery: a Hermes prompt or external gateway reads
  `deliver prepare --collect fetch-all --format json` or
  `deliver pending --format json`, sends each item through
  Telegram/Discord/etc., records returned message ids with `delivery record`,
  and forwards reactions to `interaction handle`.

Older local Telegram integrations may have written a
`telegram_raw_item_messages` table directly. Current contents-hub migrations copy
compatible rows into `outbound_messages` when any CLI command opens the vault.
New integrations should not write that legacy table; use `delivery record`.
Telegram SDKs and credentials remain outside contents-hub core.
