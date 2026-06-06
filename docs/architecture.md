# Architecture

contents-hub has four boundaries.

## 1. Vault State

The vault stores user-owned content and local runtime state.

```text
<vault>/
  .contents-hub/
    state.db
    cli.log
    daemon.log
  sources/
  digests/
```

SQLite owns subscriptions, schedules, raw items, Lenses, digests, saved items,
outbound message mappings, and interaction events.

## 2. Collection And Digest

Subscriptions and manual raw items become rows in `raw_items`. Lenses attach
topic-specific summaries. Digest generation reads Lens-routed raw items and
stores digest rows.

Deterministic sources should use HTTP/RSS paths. Browser or agent-backed
collection should use the `AgentRunner` protocol and the selected optional
runner.

## 3. Delivery And Interaction

`deliver pending` emits adapter-ready cards. Channel adapters send cards and
record returned message ids with `delivery record`.

When users interact with a message, adapters normalize the event and call
`interaction handle`. contents-hub resolves the message mapping, logs the
event, and applies an idempotent content action.

The core action set is `save`, `save_and_promote`, `archive`, and `mark_read`.
Repeated events return stable no-op or already-handled JSON instead of creating
duplicate rows or source notes.

## 4. Runtime Boundary

contents-hub owns local content state and CLI contracts.

External runtimes own:

- scheduling
- webhook or gateway lifecycle
- channel credentials
- message transport
- account permissions

This lets cron, launchd, Hermes, OpenClaw, Claude Code, Codex, and other loops
drive the same core.
