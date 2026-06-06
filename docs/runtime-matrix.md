# Runtime Matrix

| Runtime | Owns | contents-hub commands |
| --- | --- | --- |
| Manual shell | Human-triggered runs | `browser open`, `fetch-all`, `digest`, `web` |
| cron | Schedule | `fetch-all`, `digest`, `exploration run-all`, `deliver pending` |
| macOS launchd | Long-running daemon | `daemon loop` |
| Hermes | Schedule and Telegram gateway | `fetch-all`, `digest`, `deliver pending`, `delivery record`, `interaction handle` |
| OpenClaw | Schedule and gateway | `fetch-all`, `digest`, delivery and interaction CLI |
| Claude Code loop | Agent loop and scheduling | `fetch-all`, `exploration run-all`, `digest` |
| Codex loop | Agent loop and scheduling | `fetch-all`, `exploration run-all`, `digest` |

## Runner Selection

The base install is runtime-neutral:

```bash
CONTENTS_HUB_AGENT_RUNNER=none
```

Agent-backed collection or synthesis uses an optional runner:

```bash
uv sync --extra claude --extra dev
CONTENTS_HUB_AGENT_RUNNER=claude-sdk
```

Core modules must not import provider SDKs unless the optional runner path is
selected or imported directly.
