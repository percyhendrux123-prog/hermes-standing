# Changelog

## 0.1.1

- Route task-card comments through public `ctx.dispatch_tool("kanban_comment", ...)`; core owns author identity, redaction, approvals and board resolution. No direct Kanban database access.
- Use `hermes_constants.get_hermes_home()` for protected file-proof paths, including host-selected home overrides.
- Default automatic promotion ceiling to L1. L2 automation requires explicit owner opt-in; existing owner settings and actor levels are not lowered.

## 0.1.0 (2026-11-06)

First public release.

- Per-profile trust levels L0-L3 with send/spend/spin/scrub/shell/local-write categories.
- `standing_proof` tool: Standing re-runs file, HTTP and allowlisted command checks itself.
- Proof before done: `kanban_complete` is blocked until the task has a passing proof in the same session.
- Earning and losing: promotion after a clean window of passes, demotion on one failed check. Unavailable checks are neutral.
- Seeds create missing actors only; restarts never undo a demotion.
- Owner commands: `/standing`, `/standing log`, `/standing set` (local only).
- Optional shared same-machine ledger (`db_path`) for a governor and its workers.
- Task-card line after a successful completion or a demotion (`card_line`).
- Deferred `tool_call` batches are unwrapped and checked call by call.
- Hardening after independent review: owner-scoped file roots and HTTP targets, credential-path refusal, no redirects or proxies, bounded command output, strict Content-Length handling, explicit cron action classes.
