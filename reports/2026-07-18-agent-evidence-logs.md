# Agent Evidence Logs Pattern - July 18, 2026

## Signal

Agent developer tools are converging on evidence-rich execution: operators want
to know what was searched, what was changed, which tests ran, which messages
were sent, and why a PR was or was not opened. A plain success line is no longer
enough for autonomous maintenance.

## Pattern

Use `tiny-log` for structured decision logs that can be replayed later:

```python
from tiny_log import configure, get_logger

configure(format="json", level="INFO")
log = get_logger("repo-maintenance").bind(run_id="2026-07-18T13:00Z")

log.info("repo.selected", repo="tiny-cli", reason="operator CLI report")
log.info("test.passed", repo="tiny-cli", command="python -m unittest")
log.warning("pr.blocked", repo="EdgeChains", reason="fine-grained PAT pull-only")
```

## Event Names

Prefer stable dotted event names:

- `trend.seen`
- `repo.selected`
- `file.changed`
- `test.passed`
- `test.failed`
- `pr.created`
- `pr.blocked`
- `message.sent`
- `budget.exhausted`

Stable names make grep, jq, dashboards, and incident summaries much easier.

## Evidence Fields

Every autonomous repo-maintenance run should capture:

- `run_id`
- `repo`
- `branch`
- `commit`
- `command`
- `exit_code`
- `report_path`
- `destination_thread`
- `blocker`

Not every event needs every field, but every field should have a consistent
meaning across repos.

## Why `tiny-log`

Most agent workflows start as cron jobs and shell scripts. `tiny-log` adds
structured evidence without adding a logging backend, async worker, or agent
platform dependency. Text mode is readable during manual runs; JSON mode is
ready for aggregation.

## Companion Tools

- `tiny-config` for run metadata and message routing.
- `tiny-trace` or `tiny-otel` for spans across tool calls.
- `tiny-validator` for enforcing the event envelope.
- `tiny-budget` for logging cost guardrail decisions.

## Last Verified

2026-07-18 - created during the developer tool reports cron after reviewing
agent observability and autonomous workflow auditability trends.
