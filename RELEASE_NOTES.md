# Release Notes

## v0.2.0 - Public User Launch

This launch prepares contents-hub for a first public showing as a skill-first,
local-first content inbox.

### First Success Path

- Install the single `contents-hub` skill.
- Let an agent install the local CLI from this repo.
- Initialize a vault.
- Add a manual URL/text item for the shortest path, or create a Lens and add an
  RSS feed.
- Run `fetch-all`.
- Run `digest`.
- Open the dashboard.

### Included

- Single public `contents-hub` skill.
- Local checkout CLI install with `uv tool install -e`.
- Runtime-neutral manual digest path with automatic `manual-inbox`.
- RSS digest path through user-created Lenses.
- Fixed `contents-hub` browser profile CLI:
  - `contents-hub browser open`
  - `contents-hub browser status`
  - `contents-hub browser kill`
- Transport-neutral channel contract:
  - `contents-hub deliver pending`
  - `contents-hub delivery record`
  - `contents-hub interaction handle`
- Demo delivery/interaction smoke using `platform demo`.
- Public launch checklist.

### Boundaries

- Browser-backed social sources and explorations are optional/experimental.
- Third-party login is manual through the browser profile and is not required
  for first success.
- Telegram, Slack, and Discord message transport belongs to external gateways
  or agent runtimes.
- The base package does not ship built-in Telegram, Slack, or Discord bot
  packages.

### Follow-Ups

- Exact `npx skills` or `skills.sh` one-line installer command.
- Slack adapter.
- Discord adapter.
- Telegram/Hermes reference adapter.
- PyPI or package registry distribution.
- MCP bridge.
- Additional agent runners beyond `claude-sdk`.
