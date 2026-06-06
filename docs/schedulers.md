# Scheduler Integration

contents-hub does not need to own scheduling. Any runtime that can run commands
can drive it.

## Common Commands

```bash
contents-hub --vault ~/contents-vault fetch-all
contents-hub --vault ~/contents-vault digest
contents-hub --vault ~/contents-vault exploration run-all
contents-hub --vault ~/contents-vault deliver pending --format json
contents-hub --vault ~/contents-vault interaction handle --event-json '<json>'
```

## cron

```cron
*/30 * * * * contents-hub --vault ~/contents-vault fetch-all
0 8 * * * contents-hub --vault ~/contents-vault digest
0 9 * * * contents-hub --vault ~/contents-vault deliver pending --format json > ~/contents-vault/.contents-hub/latest-delivery.json
```

## launchd

```bash
contents-hub --vault ~/contents-vault daemon install
contents-hub --vault ~/contents-vault daemon status
```

## Hermes

Hermes should own gateway and scheduler lifecycle. It can:

1. run `fetch-all` and `digest` on its schedule
2. run `exploration run` or `exploration run-all` for approved recipes
3. call `deliver pending --format json`
4. send payloads through Telegram
5. call `delivery record`
6. forward reactions to `interaction handle`

## OpenClaw

OpenClaw should own scheduled tasks and channel gateway code. Treat
contents-hub as a local CLI state engine.

Example task sequence:

```bash
contents-hub --vault ~/contents-vault fetch-all
contents-hub --vault ~/contents-vault digest
contents-hub --vault ~/contents-vault deliver pending --format json
```

## Claude Code And Codex Loops

Agent loops can run the same commands manually or on their own schedule. They do
not need a special contents-hub runner unless they are implementing
agent-backed collection.

Useful loop actions:

```bash
contents-hub --vault ~/contents-vault fetch-all
contents-hub --vault ~/contents-vault exploration run-all
contents-hub --vault ~/contents-vault digest
contents-hub --vault ~/contents-vault deliver pending --format json
```
