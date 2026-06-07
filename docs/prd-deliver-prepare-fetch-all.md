# PRD: Adapter-Ready `deliver prepare --collect fetch-all` With Reaction Mapping Preservation

## Status

Approved for implementation.

## Background

The current production-like Hermes setup uses a local profile-specific watchdog script to run `contents-hub fetch-all`, query SQLite directly for newly inserted subscription items, send Telegram messages, and call `contents-hub delivery record` so Telegram reactions can later be mapped back to `raw_items` through `contents-hub interaction handle`.

This works, but too much contents-hub state semantics have leaked into the runtime adapter:

- The watchdog computes delivery candidates by querying `raw_items` directly.
- It has historically used row-level diffs (`id > before_max_id`), which is not equivalent to first-seen content.
- It had to learn the SQLite `UNIQUE(subscription_id, url)` + `NULL` pitfall: old `subscription_id IS NULL` rows can coexist with newer rows for the same URL and be resent as if new.
- Lens-matched filtering and first-seen suppression belong in contents-hub core, not in a Hermes/Telegram-specific script.

The user wants a public CLI/state contract that lets a runtime run collection and obtain adapter-ready delivery cards, while preserving the existing reaction mapping behavior. There must be no regression: Telegram/adapter sends must still be recorded in `outbound_messages`, and reactions must continue to resolve through `interaction handle`.

## Decision

Add a canonical `contents-hub deliver prepare` command. The command prepares adapter-ready delivery cards and can optionally run a collector first.

Canonical command shape:

```bash
contents-hub --vault /path/to/vault deliver prepare \
  --collect fetch-all \
  --payload-type raw_item \
  --origin subscription \
  --lens-matched \
  --first-seen-only \
  --limit 20 \
  --format json
```

Do **not** make `fetch-all` emit Telegram-specific output by default. `fetch-all` remains a collection command. If a convenience alias is added later, it must internally reuse the same `deliver prepare` implementation and remain platform-neutral.

## Goals

1. Provide a machine-readable CLI contract that runs `fetch-all` and returns delivery candidates in one JSON object.
2. Keep delivery cards platform-neutral and adapter-ready, not Telegram-specific.
3. Preserve reaction mapping by keeping the adapter responsible for calling `delivery record` with returned platform `message_id` values.
4. Move first-seen URL suppression, Lens-matched filtering, origin filtering, and outbound-message exclusion into contents-hub core.
5. Avoid direct SQLite candidate-selection logic in runtime watchdog scripts.
6. Keep stdout JSON-only and existing command contracts stable.

## Non-goals

- Do not add Telegram Bot API dependencies to core.
- Do not store Telegram credentials in the repo.
- Do not implement a production Telegram sender in core.
- Do not remove or break `deliver pending`.
- Do not rewrite the entire raw item dedupe schema in this PR.
- Do not require network/browser-backed collection in unit tests.

## User-facing CLI Requirements

### `deliver prepare`

Add a new subcommand under `deliver`:

```bash
contents-hub deliver prepare [options]
```

Options:

- `--format json`
  - Only `json` is required now.
- `--payload-type {all,raw_item,digest}`
  - Default: `all`.
  - `--collect fetch-all` is only valid for `raw_item` or `all`; digest cards may still be included if requested and pending after collection.
- `--limit INT`
  - Default: `20`.
  - Clamp to at least `1`.
- `--origin ORIGIN`
  - Optional raw item filter.
  - Example: `--origin subscription`.
  - Applies only to raw items.
- `--lens-matched`
  - Raw item must have at least one row in `raw_item_lenses`.
  - Applies only to raw items.
- `--first-seen-only`
  - Raw item URL must not have appeared in an older `raw_items` row.
  - Hotfix-level first-seen definition:
    ```sql
    NOT EXISTS (
      SELECT 1 FROM raw_items old
      WHERE old.url = ri.url
        AND old.id < ri.id
    )
    ```
  - Only apply when `ri.url` is non-empty. Text-only manual items without URL should not be accidentally filtered out unless future item-key logic handles them.
- `--collect {none,fetch-all,tick}`
  - Default: `none`.
  - `fetch-all`: run `collect_all_active(... include_error=True ...)` before computing delivery candidates.
  - `tick`: run `collect_all_due(...)` before computing delivery candidates.
- `--timeout-per-sub FLOAT`
  - Used by `--collect fetch-all` and `--collect tick`.
  - Default should match existing fetch-all/tick defaults where practical.
- `--concurrency INT`
  - Used by `--collect fetch-all`.
  - Default: `1`.

### `deliver pending` filters

Extend existing `deliver pending` with the same candidate filters where meaningful:

```bash
contents-hub deliver pending \
  --payload-type raw_item \
  --origin subscription \
  --lens-matched \
  --first-seen-only \
  --limit 20 \
  --format json
```

`deliver pending` must not run collection.

## JSON Contract

`deliver prepare` returns exactly one JSON object to stdout.

Required top-level shape:

```json
{
  "ok": true,
  "collector": {
    "command": "fetch-all",
    "ok": true,
    "summary": {},
    "errors": []
  },
  "delivery": {
    "ok": true,
    "payload_type": "raw_item",
    "count": 1,
    "items": []
  }
}
```

When `--collect none`:

```json
"collector": {
  "command": "none",
  "ok": true,
  "summary": {},
  "errors": []
}
```

On collection failure:

- Return `ok: false` if collection throws or returns an unsuccessful payload.
- Still emit exactly one JSON object.
- Include useful error text under `error` and/or `collector.errors`.
- Exit non-zero.

### Delivery card fields

Raw item cards must include existing fields and add adapter conveniences:

```json
{
  "payload_type": "raw_item",
  "raw_item_id": 2776,
  "digest_id": null,
  "delivery_key": "raw_item:2776",
  "dedupe_key": "url:https://example.com/item",
  "title": "Item title",
  "url": "https://example.com/item",
  "summary": "Short summary or body fallback",
  "plain_text": "Item title\n\nShort summary\n\nhttps://example.com/item",
  "markdown": "**Item title**\n\nShort summary\n\nhttps://example.com/item",
  "source_type": "x.profile",
  "origin": "subscription",
  "status": "raw",
  "lens_ids": ["agent-tech"],
  "collected_at": "...",
  "published_at": "..."
}
```

Digest cards should preserve existing fields and may include:

```json
"delivery_key": "digest:123"
```

## Reaction Mapping Requirement

This PR must preserve the existing adapter flow:

1. Adapter calls `deliver prepare` or `deliver pending`.
2. Adapter sends each returned card to Telegram/Slack/Discord/etc.
3. Adapter calls `contents-hub delivery record` with:
   - `payload_type`
   - raw item id or digest id
   - platform
   - channel/thread identifiers if available
   - platform `message_id`
4. Later reaction events call `contents-hub interaction handle`.
5. `interaction handle` resolves the event through `outbound_messages` and applies the configured action.

Acceptance criteria: at least one test must prove this round trip with a fake adapter:

```text
deliver prepare/pending card
→ FakeAdapter.send_item(card)
→ record_outbound_message(... message_id ...)
→ handle_interaction(... same message_id, reaction value ...)
→ raw item is saved/promoted or archived as expected
```

## Candidate Selection Semantics

For raw item candidates:

- Must exclude items already present in `outbound_messages` for payload type `raw_item`.
- If `--origin subscription`, require `ri.origin = 'subscription'`.
- If `--lens-matched`, require at least one `raw_item_lenses` row for the item.
- If `--first-seen-only`, suppress newer rows whose URL appeared in any older `raw_items` row.
- Preserve stable ordering:
  ```sql
  ORDER BY ri.priority DESC, ri.collected_at DESC, ri.id DESC
  ```
- Limit applies to the final combined card list.

For digest candidates:

- Preserve current pending behavior: `digests.status = 'ok'` and no matching outbound message.
- Do not apply raw-item-only filters to digests.

## Tests Required

Add or update tests under `tests/test_delivery_interactions.py` or a focused new file.

Required tests:

1. `deliver pending --origin subscription --lens-matched --first-seen-only` filters raw cards correctly.
   - Seed:
     - old subscription item with URL A and `subscription_id NULL`.
     - newer subscription item with URL A and lens match: must be suppressed by first-seen.
     - newer subscription item with URL B and no lens: suppressed by lens filter.
     - newer manual item with URL C and lens: suppressed by origin filter.
     - newer subscription item with URL D and lens: included.
2. Already recorded outbound messages are not returned.
3. Cards include `delivery_key`, `dedupe_key`, `plain_text`, `markdown`, and `lens_ids`.
4. Fake adapter + `delivery record` + `interaction handle` round trip still works from a `deliver prepare` or filtered pending card.
5. CLI help exposes `deliver prepare` and new filter options for both `deliver pending` and `deliver prepare`.
6. `deliver prepare --collect fetch-all` returns a top-level object with `collector` and `delivery` keys. Unit tests may monkeypatch/stub collection to avoid network.
7. `deliver pending` remains backward-compatible when no new filters are passed.

## E2E/Smoke Verification Required

Run at minimum:

```bash
python -m pytest tests/test_delivery_interactions.py -q
python -m pytest tests/test_public_surface.py tests/test_naming_contract.py -q
python -m pytest -q
contents-hub deliver --help
contents-hub deliver pending --help
contents-hub deliver prepare --help
```

If installed CLI points to a stale editable install, reinstall from source and rerun smoke help:

```bash
uv tool install -e "$PWD" --force
```

## Documentation Updates

Update docs to describe the new core/runtime boundary:

- `docs/channels.md`
- `docs/schedulers.md`
- `docs/hermes-setup.md`
- `skills/contents-hub/SKILL.md` if command syntax changes

Docs must state:

- contents-hub decides what cards are deliverable.
- Runtime/channel adapters send cards and then call `delivery record`.
- Reaction mapping depends on `delivery record`; final-response-only delivery is insufficient for reactions.
- Telegram SDK/credentials remain outside core.

## Implementation Notes

Likely files:

- `src/contents_hub/delivery.py`
- `src/contents_hub/cli.py`
- `tests/test_delivery_interactions.py`
- `docs/channels.md`
- `docs/schedulers.md`
- `docs/hermes-setup.md`
- `skills/contents-hub/SKILL.md`

Prefer extracting a reusable delivery function such as:

```python
def delivery_payload(
    config,
    *,
    payload_type="all",
    limit=20,
    origin=None,
    lens_matched=False,
    first_seen_only=False,
) -> dict:
    ...
```

Then have `pending_delivery_payload` call it for backward compatibility.

Add a prepare function such as:

```python
async def prepare_delivery_payload(
    config,
    *,
    collect="none",
    payload_type="all",
    limit=20,
    origin=None,
    lens_matched=False,
    first_seen_only=False,
    timeout_per_sub=120.0,
    concurrency=1,
) -> dict:
    ...
```

Be careful about imports to avoid circular dependencies between `cli.py`, `delivery.py`, and `api.py`.

## Definition of Done

- `deliver prepare` exists and is documented in CLI help.
- `deliver pending` supports the new filters.
- JSON output is valid and stdout-only for success paths.
- Reaction mapping round trip is tested and passing.
- First-seen duplicate URL suppression is tested and passing.
- Full test suite passes locally.
- Independent code review finds no blocking issues.
- Independent e2e reviewer confirms command behavior and reaction flow.
