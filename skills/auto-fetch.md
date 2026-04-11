---
description: "Set up automatic subscription fetching on a cron schedule. Use when: /auto-fetch, 'schedule auto collect', 'set up cron', 'auto fetch', 'periodic collection', '자동 수집 설정'"
allowed-tools: ["Bash", "Read", "CronCreate", "CronList", "CronDelete"]
---

# /auto-fetch — Schedule periodic subscription collection

Set up a recurring cron job that runs `/tick` to collect all due subscriptions automatically.

## Usage

```
/auto-fetch                — Start auto-fetch with default schedule (every 6 hours)
/auto-fetch --interval 2h  — Every 2 hours
/auto-fetch --interval 30m — Every 30 minutes
/auto-fetch --cron "0 9 * * 1-5" — Custom cron (weekdays at 9am)
/auto-fetch stop           — Cancel the auto-fetch cron
/auto-fetch status         — Show current schedule
```

## Workflow

### Step 1: Parse interval

Map the user's requested interval to a cron expression:

| Interval | Cron Expression | Description |
|----------|----------------|-------------|
| `30m` | `*/30 * * * *` | Every 30 minutes |
| `1h` | `23 * * * *` | Every hour at :23 |
| `2h` | `23 */2 * * *` | Every 2 hours at :23 |
| `4h` | `23 */4 * * *` | Every 4 hours at :23 |
| `6h` (default) | `23 */6 * * *` | Every 6 hours at :23 |
| `12h` | `23 */12 * * *` | Twice daily at :23 |
| `24h` | `23 8 * * *` | Daily at 8:23am |
| custom | User-provided cron | As specified |

**Note**: Use off-minute marks (e.g., :23) to avoid API congestion at :00/:30.

### Step 2: Create the cron job

Use `CronCreate` to schedule the recurring job:

```
CronCreate({
  cron: "<resolved_cron>",
  prompt: "Run /fetch-all to collect all active subscriptions from the llm-wiki plugin. This iterates every registered RSS, YouTube, Twitter/X feed, fetches new content, and saves source files to the Obsidian vault. Report the summary when done.",
  recurring: true,
  durable: true
})
```

### Step 3: Confirm to user

Report the created schedule:

```
## Auto-Fetch Scheduled

- Schedule: Every 6 hours (0 */6 * * *)
- Next run: <from CronCreate result>
- Action: /fetch-all (collect all active subscriptions)

Note: This cron job persists for 7 days or until this Claude session ends.
To cancel: /auto-fetch stop
To check status: /auto-fetch status
```

### Stop command

When user says `/auto-fetch stop`:

1. Use `CronList` to find the auto-fetch job
2. Use `CronDelete` with the job ID to cancel it
3. Confirm cancellation

### Status command

When user says `/auto-fetch status`:

1. Use `CronList` to show all scheduled jobs
2. Report the auto-fetch schedule if found, or "No auto-fetch scheduled" if not

## Session Lifecycle

CronCreate jobs are session-scoped (auto-expire after 7 days). Each new Claude Code session needs to re-run `/auto-fetch` to resume periodic collection. This is by design — the user controls when automated collection runs.

## Quick Start

For most users, just run:

```
/auto-fetch
```

This sets up collection every 6 hours with sensible defaults.
