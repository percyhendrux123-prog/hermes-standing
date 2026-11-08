# Standing

**Earned trust for Hermes workers.** A worker's record changes what needs your approval. A task cannot be marked done just because the worker says it is: Standing first needs a check it ran itself.

Version 0.1.2. MIT. Requires Hermes >=0.22.5, plugin API 1. See [CHANGELOG.md](CHANGELOG.md), [SPEC.md](SPEC.md) and [SPEC-NOTES.md](SPEC-NOTES.md).

Requires Python 3.10+ (Hermes's own runtime).

## Why

A convincing completion message is not evidence. At the other extreme, asking for approval on every harmless draft makes an assistant hard to use. Standing keeps a small local record of passing and failing checks, and uses that record to request fewer approvals as a worker earns trust.

It does not replace Hermes's security policy, other plugins, or the owner's judgment. It does not decide whether a check actually proves the whole task was done well.

## Levels

| Level | No additional Standing action approval for |
| --- | --- |
| L0 — read | Known read/search tools |
| L1 — draft | L0 plus local file writes inside the configured workspace |
| L2 — act | L1 plus shells/code, sends, job/deployment actions and unknown tools, inside existing Hermes gates |
| L3 — trusted | All actions; other Hermes gates still apply |

Spending and deletion/move actions request approval below L3. Shell/code and unknown tools/actions request approval at L0/L1, but are allowed at L2 so proven unattended workers can work. Multiple categories must all be allowed; non-local/shared `write` mutations still request approval below L3 by default. The category map is configurable by the owner.

For `cronjob` / `cronjob_manage`, action names are classified explicitly (case/outer whitespace ignored): `list/get/show/status` are read; `create/add/update/edit/resume/enable/run/trigger` are spin; `remove/delete/clear/pause/stop/disable/cancel` are scrub. Pausing/stopping/disabling/cancelling disrupts scheduled work, so it deliberately needs scrub approval below L3. Missing or unrecognized actions are unknown, never assumed spin. This classifier vocabulary does not claim every alias exists in every Hermes version.

Local writes mean `write_file` and single-file `patch` calls with a recognized path. Relative paths use the session's working folder, not the gateway's process folder. Resolved paths, including symlinks, must stay inside the configured workspace. Bulk patches request approval because their actual targets live inside a separate patch payload. `terminal` and `execute_code` calls use the `shell` category when no scrub/send/spin rule matches, including pipes, substitutions and scripts (even `pwd` is a shell call). Recognized scrub/send/spin categories remain combined and take precedence. L1 requests approval for shells; L2 allows them, but still requests approval for recognized scrub and spend. This is earned permission, not proof arbitrary code is read-only/local, and not a shell sandbox.

Deferred `tool_call` batches evaluate every inner call with the same policy and completion-proof check as direct calls, returning the strictest directive (`block` > `approve` > none). Nested wrappers are unwrapped iteratively. Malformed/empty batches request approval; if any inner name is `kanban_complete`, a malformed batch blocks even with a previous proof. `tool_describe` and `tool_search` remain read tools.

**Unattended runs: an approval request is not permission. In cron/gateway runs, Hermes's own approval policy handles Standing's `approve` directive; the default unattended outcome is deny. YOLO/off/smart modes, approval caching, and other host policies remain host-owned. Do not assume an interactive human prompt will appear.**

L3 bypasses action-level Standing gates, **not** the separate proof-before-completion check. Disable that check or exempt an exact profile only as an explicit owner decision.

## Install (on the owner's Hermes machine)

From the Hermes Marketplace (once listed):

```bash
hermes plugins install hermes-standing
hermes plugins enable hermes-standing
```

Contributor workflow (local checkout):

Review the code and configure the workspace/seeds before enabling. This plugin has no runtime Python dependencies.

```sh
hermes plugins validate /absolute/path/to/hermes-standing --install-deps
hermes plugins doctor /absolute/path/to/hermes-standing
hermes plugins install /absolute/path/to/hermes-standing
hermes plugins enable hermes-standing
```

Then start a fresh session. Nothing in this repository auto-installs, deploys, or changes live data.

## Configuration

The SPEC's `standing.seed`, `standing.default_level`, and other Standing settings live in Hermes's plugin settings namespace:

```yaml
plugins:
  entries:
    hermes-standing:
      enabled: true
      settings:
        seed:
          worker: L1
          reviewer: L0
        default_level: L1
        workspace: /absolute/path/to/throwaway-workspace
        file_proof_roots: []  # empty uses workspace; or list absolute artifact folders/files
        db_path: ""  # optional: same absolute SQLite path in governor AND workers
        http_proof_targets:
          - http://127.0.0.1:8000/health
        levels:
          L0: [read]
          L1: [read, local_write]
          L2: [read, local_write, shell, send, spin, unknown]
          L3: ['*']
        promote_after: 10
        max_auto_level: L1  # L2 requires explicit owner opt-in
        require_proof_on_complete: true
        card_line: true  # false disables task-card comments only
        exempt_profiles: []
        command_allowlist:
          - [git, rev-parse, HEAD]
          - [git, status, --short]
        proof_timeout: 10
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `seed` | `{}` | Initial L0-L2 levels by exact profile name, only when the actor row is first created; never modifies existing rows |
| `default_level` | `L1` | Starting level for unlisted profiles, L0-L2 |
| `workspace` | empty | Absolute folder allowing local writes at L1/L2; empty means writes request approval |
| `file_proof_roots` | `[]` | Absolute artifact folders or exact files; empty uses configured workspace, and no workspace means file proofs refused. Credential exclusions cannot be overridden |
| `db_path` | empty | Optional absolute SQLite path, identical in governor/workers for same-machine sharing; empty retains home-local plugin storage. No automatic copy/merge/migration |
| `http_proof_targets` | `[]` | Owner-selected HTTP(S) prefixes; empty disables HTTP proofs. Same origin and exact query; trailing-slash path permits subtree, otherwise exact endpoint |
| `levels` | table above | Allowed categories by level; omitted levels retain defaults |
| `promote_after` | `10` | Passes in a clean window before one automatic promotion |
| `max_auto_level` | `L1` | Automatic ceiling, L0-L2; above L1 requires explicit owner opt-in. Never automatically lowers an existing level |
| `require_proof_on_complete` | `true` | Require a same-task, same-session passing proof before `kanban_complete` |
| `card_line` | `true` | Best-effort task-card score/demotion comments; set false to disable both, without changing proof policy |
| `exempt_profiles` | `[]` | Exact profile names exempt only from the completion proof check |
| `command_allowlist` | `[]` | Exact complete argv lists or command strings, not executable/prefix permissions |
| `proof_timeout` | `10` | HTTP/socket and command timeout seconds, 1-20 |

Allowed category names are `read`, `local_write`, `shell`, `send`, `spend`, `spin`, `scrub`, `write`, and `unknown`. `write` includes non-local/shared mutations, so adding it to a level deliberately broadens that level. `*` is accepted only for L3, which always bypasses Standing's action gate. Leave the defaults unless you intend to change this policy. An explicitly configured old L2 list is preserved: update it or omit that level to use the new defaults, then reload/restart.

Settings are read at plugin load; reload/restart to apply changes. Invalid settings are logged without private values. Ordinary tool hooks return no directive, and protected task completion stays blocked until the owner repairs the settings. An invalid configuration does not silently grant a fallback level.

Seeds and `default_level` apply only when an actor row is first created. They never raise or lower an existing row at load, even if the configured seed/default changes. **Restarts never undo a demotion.** To change an existing actor manually, the owner uses `/standing set`; normal proof-based earning/demotion still applies. L3 is available only through that local owner command, not a seed or auto-promotion. No migration is needed: existing actor rows and event history are kept as-is; historical seed restorations are not automatically reversed.

## Proof before done

The model calls `standing_proof` with `kind`, `target`, and `expect`. Standing runs the check itself; it never accepts a model's assertion that a check passed.

```json
{"kind":"file","target":"dist/index.html","expect":"Ready for review"}
```

```json
{"kind":"file","target":"artifact.txt","expect":"sha256:<actual 64-hex digest>"}
```

```json
{"kind":"http","target":"http://127.0.0.1:8000/health","expect":"ready"}
```

```json
{"kind":"command","target":["git","rev-parse","HEAD"],"expect":"<expected commit prefix>"}
```

- **File:** only inside owner-configured `file_proof_roots` (artifact folders or exact file targets); empty roots use the configured workspace, **not** the caller's arbitrary cwd. With neither, no file is opened. Both requested and resolved paths must be in scope. Known credential paths are rejected before and after resolution: `auth.json`, names containing `credentials` or `keychain`, `*.pem`, `*.key`, `id_*`, `.env*`, `.netrc`, `.npmrc`, `.pypirc`, `.ssh/.aws/.gnupg`, `.config/gcloud`, `.docker/config.json`, and anything under the active home returned by `hermes_constants.get_hermes_home()` (including host home overrides) except the configured workspace. Name checks are case-insensitive and cannot be overridden by broad roots. Resolution occurs once; held parent descriptors and no-follow opens bind content access to the validated canonical path, refusing parent/leaf symlink swaps. A regular file must contain expected UTF-8 bytes, or match a 64-hex SHA-256 (optional `sha256:` prefix). Substring search reads the first 2 MiB; hashing streams the whole file. This is **not** content-based secret detection or artifact immutability: never put secrets or hardlinks to credential content in an approved artifact root. Other OS-authorized processes can still change file contents; Standing is not their sandbox.
- **HTTP:** disabled until the owner configures `http_proof_targets`; unconfigured destinations are refused **before constructing an opener or connecting**. Entries pin scheme + hostname + effective port. A path ending in `/` permits that subtree; other paths permit only the exact endpoint. Queries must match the configured query exactly (normally empty): callers cannot append data to an approved URL. Example: `/proofs/` allows `/proofs/result`, not `/delete`; `/health` does not allow `/health-other`. Dot-segment traversal, ambiguous/double-encoded paths, backslashes, fragments, credentials and controls are refused. GET only, status exactly 200, expected UTF-8 bytes in the first 2 MiB. A truncated declared-length response is unavailable if it ends before that proof prefix is complete, even if the expected text already arrived. Malformed, comma-combined or duplicate Content-Length headers are refused as unavailable rather than treated as an unknown-length body. No redirects, ambient proxies, cookies, login, or credential-bearing URLs. Choose only non-sensitive endpoints where GET is safe; allowing a broad prefix deliberately permits every matching path. This is URL scoping, not DNS/IP pinning or a guarantee of server behavior. Socket timeouts are not an end-to-end wall-clock guarantee for slow streaming servers.
- **Command:** exact argv equality against the owner's read-only list; `shell=False`; shell operators, substitutions, wildcards, control characters and other metacharacters rejected in strings **and** argv lists. A zero exit code and expected stdout substring are required. The child has no stdin, stderr is discarded, and only a minimal explicit environment is supplied (not the owner's secrets). Stdout is buffered transiently in memory, never spooled to disk: at most 2 MiB plus one overflow-detection byte is read. Exceeding the budget makes the entire proof unavailable even if the marker appeared earlier; reading stops and the isolated command process group is killed and the child reaped. A wall-clock timeout covers stdout reading and waiting for exit, including a child that closes stdout but keeps running. Kernel pipe buffers and normal interpreter/allocation overhead are separate from the 2 MiB payload budget. This POSIX implementation targets Linux/macOS, not Windows. The owner must ensure a listed command is genuinely read-only; arbitrary commands/scripts are not automatically audited or sandboxed. Prefer fixed trusted executable paths when practical. A silent command such as `test -f` cannot pass a non-empty stdout expectation; use a file proof for existence/content instead.

Expected results cannot be empty or whitespace-only. Checks have three outcomes:

| Result | Meaning | Standing change |
| --- | --- | --- |
| `pass` | The check ran and matched | Earns a pass and a same-session/task receipt |
| `fail` | The check ran but did not match (including a nonzero command exit) | Demotes by one, increments failures, resets the pass window |
| `unavailable` | The check could not run: missing target, refused path, network error, timeout, absent allowlist entry, invalid request, or exceeded safety budget | No pass, no demotion, no counter/window change, no receipt, no task-card line |

Output is not returned: the tool returns a proof handle, `result`, `passed` (`true` / `false` / `null` respectively), a plain explanation, and the current record. Unavailable attempts are logged as `unavailable`, never as failed claims. They explain proof locations and owner scope without exposing targets or exception text. A storage failure does not count a pass.

Sandboxed workers: use http or allowlisted command proofs; file proofs run on the machine Standing runs on. HTTP endpoints must be reachable from that machine and included in `http_proof_targets`; command proofs run there too and require an exact owner-allowlisted read-only command. A path inside a worker's separate sandbox is not a host file proof.

Each pass adds one to the current window. After `promote_after` passes with no failure in that window, the level rises by one, capped at `max_auto_level`, and the window resets. **The default ceiling is L1: passing proofs alone never grant L2 action permission.** The owner must confirm higher access explicitly with `/standing set <profile> L2`, or deliberately opt into automatic L2 promotion with `max_auto_level: L2` and reload. Existing actor levels and explicit owner settings are preserved, never silently lowered by this default change. A failure immediately lowers the level by one (floor L0), increments lifetime failures, and starts a new clean window. Owner level changes also reset the window. At the ceiling, passes continue accumulating without promoting further.

A worker can always report on **its own** assigned card at any level: `kanban_block`, `kanban_comment`, `kanban_request_review` and `kanban_heartbeat` pass without a Standing prompt when the target task is the worker's `HERMES_KANBAN_TASK` (or no task_id is given). Without this an unattended worker could finish and never be able to say so. The same calls on another task's card, or with no assigned task, go through the normal level gate. `kanban_complete` stays proof-gated as below.

Before `kanban_complete`, Standing needs at least one passing proof for the active **profile + HERMES_KANBAN_TASK + session**. An explicit completion task must match that assigned task. Missing task/session identity, another session's proof, a previous process's proof, or another task's proof does not qualify. Run a new check after plugin reload or session finalization/reset. Sessionless tool calls may earn ledger credit but do not authorize completion. Ordinary non-worker chats have no assigned task and need an exemption or owner-disabled proof policy to complete a board task.

A completion check uses the passing receipt from this session; it does not re-run the mutable target at completion time. A later failing proof demotes the actor, but does not erase a different earlier passing receipt. Repeated passing checks each count; there is no semantic relevance scoring, deduplication, or freshness policy. Choose meaningful checks rather than treating any pass as proof of the whole deliverable.

If no proof is present, the message is:

> Standing needs a passing check before this task can be marked done. Run standing_proof for this task in this session, then try again.

A broken completion check fails closed with an owner-readable message. Other internal hook errors are logged by exception **type**, without raw arguments, and return no directive as required by the SPEC. This fail-open behavior is deliberate and means Standing is not a hard security boundary.

## What the owner sees

**The record:**

```text
/standing
/standing log worker
/standing set worker L2 checked the deliverable myself
```

The table shows actor, level, passes in the current window, lifetime failures, and last event. Logs show the last 20 events. Owner reasons are hashed, not stored as text.

**The approval box text**, when an action is outside the worker's level (example):

> Standing: worker is at L1. This action needs the owner’s approval. Other Hermes safety gates still apply.

Hermes owns the box and its choices; in an unattended run its policy may deny rather than display an interactive box. Standing does not grant or simulate human approval.

**The task-card line**, after `kanban_complete` actually returns `{"ok": true}` (example):

> Standing: worker L2 · check passed (http example.com) · 3 passes this window / 0 fails all time.

One comment is attempted through the public `kanban_comment` tool per profile/task/session with a matching passing receipt. Hermes core chooses the author and applies its normal redaction; Standing does not supply an author. A failed/rejected completion gets no success line. Repeated hook delivery does not duplicate it. The level and counters are read from the ledger at completion; the proof label comes only from that session's most recent passing receipt, not from completion prose. Passes are the current promotion window; failures are lifetime totals, **not** failures in that window. A promotion resets the pass window, so a successful check can correctly show zero passes.

A failed proof that actually lowers the level also attempts one comment on the assigned task (no known task, or already L0, means no demotion comment):

> Standing: worker L2 -> L1 · a check failed.

Only the proof kind and HTTP hostname are disclosed: no URL path/query/port/credentials, expected text, command arguments or file paths. File/command checks say `check passed (file)` / `check passed (command)` with no target. Non-ASCII/unsafe host labels are omitted. These lines describe checks, not the quality or completeness of the deliverable.

Set `card_line: false` and reload to turn **both** comments off; the proof-before-completion rule is unaffected. The service retains the plugin context and calls `ctx.dispatch_tool("kanban_comment", {"task_id": task, "body": body, "board": board})`. Hermes core sets the author, applies redaction and approvals, and resolves the board with its existing fences. An explicit completion board is forwarded unchanged; demotion passes no explicit board, using the worker's default pinned board. Standing never opens or writes the Kanban database directly and has no fallback that bypasses the public tool. Dispatch errors log only their type; tool refusals/errors are best-effort skips. Neither changes completion or loses the proof record. An attempted completion comment is not retried after a dispatch error or refusal (a commit may already have happened). Receipts/notification guards are memory-only and cleared on session finalization/reset; this is not durable exactly-once delivery across reloads.

Level changes are accepted only from a local CLI/TUI/Desktop/local slash-command context with no remote transport identity. There is no model-facing level-setting tool. Remote/missing caller context is refused: v0.1 does not guess which messaging user is the owner. Local OS/Hermes access is trusted; this is not multi-user authentication.

**Storage is home-local by default; same-machine sharing is opt-in.** With empty `db_path`, `/standing` and `/standing set` use the active home's plugin ledger as before; commands in another home do not affect it. To administer workers from a governing profile, configure the **same absolute `db_path` in every participating profile** and give their OS user access. The governor's table/log and owner commands then use the very same SQLite actors/events as worker proofs and action gates. Workers read the current level for every action; an owner's level change takes effect on the next call. This is shared local storage, not cross-machine synchronization or a new authorization boundary. Use a local disk, not a network filesystem.

**Seed/migration policy:** defaults and seeds only insert missing actors. Existing levels, counters and history are never changed by seeds at profile load, including demotions and earned/owner-set levels. Use consistent seed/promotion settings across participants; use `/standing set` for an owner's deliberate level change. Restarts never undo a demotion. No migration is needed for this insert-only seed policy, and no past seed restoration or failed event is automatically corrected. Selecting `db_path` never copies, merges, deletes, or overwrites profile-local records. A nonexistent path creates a new ledger; it does **not** preserve old trust automatically. Before switching existing profiles, stop them, back up every ledger, and have the owner choose/copy a canonical SQLite database with SQLite's backup API, then reconcile any other actors explicitly (do not sum passes or take a lower seed). Verify the table before enabling workers. Keep the old databases for rollback. Never silently reset an established deployment onto an empty shared ledger.

## Disclosure

**Reads:** owner plugin settings via `ctx.get_config`; active profile via `ctx.profile_name`; dispatcher task ID via `HERMES_KANBAN_TASK`; session-local ID and working directory; tool names and arguments transiently for classification; file path/symlink metadata for scope checks and only approved proof file/URL/command content; its own home-local or explicitly selected shared SQLite record. It does not scan credential directories for content.

**Stores:** `actors(name, level, passes_window, fails_total, updated_at)` and `events(id, at, actor, task_id, kind, detail_hash, result, reason)`. Proof detail is a SHA-256 of kind/target/expect; owner reasons are hashes. Event reasons are fixed explanations, not private content. SQLite transactions are committed/rolled back and connections explicitly closed. In-memory receipts hold only the safe kind/HTTP-host label, keyed by actor/task/session; receipts and completion-notification guards are cleared at finalization/reset. With `card_line: true`, the host Kanban store also persists the generated score/demotion comments described above, by the core-attributed author (not an author supplied by Standing), plus its normal comment event; comments contain only profile, level/counters and the safe proof label.

**Transient buffering vs persistent storage:** file/HTTP bytes and command stdout exist in memory while checking. Command stdout has a hard 2 MiB payload budget (one additional byte detects overflow), no temporary-file spool, and is discarded after the call; stderr goes to the null device. These buffers are not ledger records. Standing does **not persist** chat/message bodies, raw tool arguments, full proof targets (only HTTP hostnames in enabled card lines), expected content, file/HTTP bodies, stdout/stderr, owner reason text, credentials, or chat histories. Hashes are not encryption; predictable target strings can be guessed. Hermes itself can retain the original tool call in its transcript according to its own settings. The OS may swap memory or retain process-level data independently of this plugin.

**Never does on its own:** read credential stores, log in, call an LLM, send an external message (the configured local Kanban comments above are the visibility exception), spend money, deploy, create a job, run a shell, use telemetry, or make background network calls. The only network path is a requested HTTP proof; the only subprocess path is an exact owner-allowlisted proof command. Standing is not an OS/container sandbox and cannot prevent separate tools/processes from altering its files if they already have permission.

Host compatibility facts were verified against copied Hermes source commit `2bd378ed1b`:

- `hermes_cli/plugins.py:401-409`: `ctx.profile_name` derives the name from `HERMES_HOME`, with `default` fallback. Missing/empty profile metadata also falls back to `default` here.
- `tools/kanban_tools.py:189-206`: the dispatcher sets `HERMES_KANBAN_TASK`. Standing uses this context rather than treating hook `task_id` as a separate actor. Core Kanban ownership checks still decide whether completion may actually happen.
- `plugins/plugin_storage.py:36-46`: documented `plugin_db('hermes-standing')` fallback when `ctx.plugin_db()` is absent. With empty `db_path`, data lives under `<HERMES_HOME>/plugin-data/hermes-standing/data.db`, not in the plugin install directory. Explicit `db_path` bypasses both factories and uses the same configured SQLite file in every participant.
- Public developer guide, “Register slash commands”: `get_session_env` for request-scoped identity and `agent.runtime_cwd.resolve_agent_cwd()` for relative paths. On an older host lacking these helpers, the compatibility fallback is the process session ID/cwd; verify on that host before use.

## Tests and demo

```sh
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir pytest
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -W error --tb=short -p no:cacheprovider
```

The test suite uses real temporary files, SQLite, loopback HTTP, and short allowlisted commands. Hermes registration/identity APIs are stubbed. This is **not** proof that a native approval prompt appeared or that a model-driven board task completed.

**Live run (2026-11-06, real gateway, model-driven worker on a dispatched Kanban task):** completion without a proof was blocked with the message above; an HTTP proof passed and completion went through with a Standing card line; a deliberately false proof failed and lowered the worker one level; `/standing` showed the change. A later live run confirmed an unavailable proof is logged without demotion. On the host: `hermes plugins validate` passed, `hermes plugins doctor` OK (1 tool, 5 hooks), and the suite passes under `-W error` on Python 3.14.

## Not in v0.1

- Friction budgets, delegated-subagent actors, cross-machine sync, a desktop UI, or LLM calls.
- Automatic discovery/merging of other homes' ledgers, remote owner-authentication rules, or cross-machine synchronization. Optional same-machine `db_path` sharing is implemented; it is not automatic migration.
- An OS sandbox, full shell parser, all arbitrary plugin/tool side effects, execution-middleware rewrite enforcement, or native approval policy overrides.
- Semantic proof relevance, anti-gaming/deduplication, proof expiry, completion-time rechecks, or proof target immutability.
- HTTP credentials, redirects, proxy support, DNS/IP pinning, whole-body substring scans above 2 MiB, or a complete secret-content detector.
