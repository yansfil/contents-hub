# Launch Checklist

Use this checklist before showing contents-hub directly to public users.

## First User Promise

The reliable first-success path is:

1. install the single `contents-hub` skill
2. let the agent install the local CLI from this repo
3. initialize a vault
4. add a manual URL/text item for the shortest path, or create a Lens and add
   an RSS feed
5. run `fetch-all`
6. run `digest`
7. open the dashboard

## Required Verification

```bash
git status --short --branch
uv sync --all-extras
uv run contents-hub --help
uv run python -m contents_hub --help
./dev test
```

Run a fresh-checkout smoke outside the active checkout, then run a fresh-vault
first-success smoke.

## Runtime QA

- manual first-success smoke with automatic `manual-inbox`
- RSS first-success smoke with an explicit Lens
- dashboard HTTP/browser smoke
- `contents-hub browser open/status/kill` smoke without third-party login
- `platform demo` delivery/interaction smoke

## Release Hygiene

- Public surface scans pass.
- Remote branch surface is intentional.
- Release notes are drafted.
- Launch tag is created or ready for human approval.
- Follow-up issues exist for exact skill installer syntax, Slack adapter,
  Discord adapter, Telegram/Hermes reference adapter, PyPI distribution, MCP
  bridge, and extra agent runners.
