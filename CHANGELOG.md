# Changelog

## 0.1.3

- Patch-mode (V4A) edits inside the configured workspace now count as local writes. Before this, every patch-mode call was treated as non-local, so a worker at L1 or L2 was stopped for approval on edits to its own workspace. Standing reads the file headers in the patch payload, using the same line shapes Hermes core accepts; every named file and move destination must resolve inside the workspace, and a payload with no readable header still requests approval.
- Deleting or moving a file through a patch is classed as scrub, including loosely spaced headers and `Move File`, which were previously missed.
- A path Standing cannot resolve is never local and no longer raises into the hook's error path.
- README: "Works with" section linking the Deploy Axiom operator-layer plugins.

## 0.1.2

- A worker can always report on its own assigned card (`kanban_block`, `kanban_comment`, `kanban_request_review`, `kanban_heartbeat`) at any level. Previously these were classed as board writes, so an unattended worker at L1/L2 could finish work and be unable to report it. Another task's card still goes through the level gate; `kanban_complete` stays proof-gated.
- README: catalog install path first; the absolute-checkout install is labelled as the contributor workflow.

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
