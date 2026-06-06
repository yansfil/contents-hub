# Channel Delivery And Interaction

Channel adapters are thin. They translate between a messaging platform and the
contents-hub CLI contract.

contents-hub does not ship built-in Telegram, Slack, or Discord bot packages in
the base install. External gateways own message transport, credentials,
webhooks, and platform SDKs.

## Delivery

```bash
contents-hub --vault ~/contents-vault deliver pending --format json
```

Each card includes stable ids such as `raw_item_id`, `digest_id`, and
`payload_type`.

Adapters may send raw item cards, digest cards, or digest section/item cards.
The adapter should treat contents-hub ids as opaque stable references and store
the platform's returned message id with `delivery record`.

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

Default rules:

| Reaction | Action |
| --- | --- |
| `⭐` | `save_and_promote` |
| `❤️` | `save_and_promote` |
| `❤` | `save_and_promote` |
| `✅` | `mark_read` |
| `🗑` | `archive` |

## Reference Integrations

- Telegram/Hermes: reference integration shape using `delivery record` and
  `interaction handle`.
- Slack and Discord: fixture normalizers in `contents_hub.channels` prove the
  shared event shape without requiring SDKs in the base install.

The base package does not import Telegram, Slack, or Discord SDKs. Full bots can
live in optional integration packages or external runtimes as long as they call
the same CLI contract.
