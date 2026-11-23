# Standing for Claude Code

A task is done when a check passes, not when the agent says so. Standing keeps a small local record of checks that held and checks that didn't, and uses it to decide how much an agent may do before you are asked.

This is the Claude Code counterpart of [Standing for Hermes Agent](https://github.com/percyhendrux123-prog/hermes-standing). It is a mod: JavaScript that runs inside Claude Code and sits on the tool-permission step. It keeps the same contract: the record only ever adds approvals, the model cannot grant itself a level, and a model's claim that a check passed is never accepted.

It does not replace Claude Code's permission rules, modes or hooks, and it does not decide whether a check proves the whole task was done well.

## What it does

1. **Level gate.** Each actor (`main`, or the subagent type such as `general-purpose`) has a level, L0 to L3. When an actor makes a tool call its level doesn't cover, Standing turns the decision into an approval request. It never turns one into an approval: if the level allows the call, Claude Code's own decision stands untouched.
2. **Proof before done.** `TaskUpdate` with `status: completed` is refused until a check that Standing ran itself has passed for that task id in this session.
3. **The record.** `standing_proof` runs a check (file, HTTP or an allowlisted command), records pass, fail or unavailable, and moves the actor's level. `/standing` shows the record.

| Level | No additional Standing approval for |
| --- | --- |
| L0, read | Reads and searches |
| L1, draft | L0, plus edits to files inside the configured workspace |
| L2, act | L1, plus shell, sends, subagents and scheduling, unknown tools |
| L3, trusted | Every action. Other Claude Code permission checks still apply |

Spending and deleting or moving things ask below L3. A call that falls in several categories (`git push && rm x`) must be allowed in all of them. The map is configurable.

Passing checks earn a level slowly: after `promote_after` passes in a clean window the level rises by one, up to `max_auto_level`, which defaults to L1. **Proofs alone never grant L2, and never grant L3.** A failed check lowers the level by one at once, counts as a lifetime failure and starts a new window. A check that could not run (missing file, refused path, timeout, command not on your list) changes nothing.

## Never self-promote

- There is no tool, setting or file edit the model can use to raise a level. The only tool, `standing_proof`, takes `kind`, `target`, `expect` and an optional `task` id.
- `/standing set <actor> <L0-L3> [reason]` works only when it comes from your own keystroke at the Claude Code prompt. Claude Code stamps that on the command (`origin.kind` is `composer`). A plugin, the SDK or `claude -p`, Remote Control from a phone, a schedule, a peer session or a channel is refused. L3 is reachable only this way: it can't be a seed, a default or an automatic ceiling.
- Seeds and the starting level only fill in an actor with no record. They never raise or lower an existing one, so restarting never undoes a demotion.
- The model is stopped from reading or editing Standing's record file with file tools or shell text that names it. This is a best-effort text match, not a sandbox (see Known gaps).
- Your reason for a level change is stored as a SHA-256 hash, not text.

## Before you install

A mod is code that runs inside Claude Code with your permissions. Read what this one asks for: run `claude plugin validate ./claude-plugin/standing` and look at the `hooks:` and `calls:` lines. They list exactly what it does.

- **Files it writes:** its own record, in Claude Code's per-plugin store (`<config dir>/plugins/store/axiom-standing_*.json`, created `0600`). Nothing else.
- **Files it reads:** only a file you point a file check at, and only inside your configured roots, and its own record. Not credential files (`.env*`, `.ssh`, `.aws`, `*.pem`, `*.key`, `id_*`, `.netrc` and similar), and nothing inside Claude Code's config folder unless that is your workspace.
- **Network:** none, except an HTTP check against a URL you listed. Nothing is sent anywhere else, and there is no telemetry.
- **Processes:** none, except an exact command you listed for command checks.
- **Environment read:** `HOME` and `CLAUDE_CONFIG_DIR`, to keep file checks out of Claude Code's own config folder.
- **Requires:** Claude Code with mods (v2.1.287 or later). Built and tested on v2.1.325 and v2.1.330.
- **Where it ships from:** GitHub. A plugin with a mod is not eligible for the Claude plugin directory unless the publishing organization is verified, and Deploy Axiom is not.

## Install

```
claude plugin marketplace add percyhendrux123-prog/hermes-standing
claude plugin install axiom-standing@deploy-axiom-standing
```

To try it for one session without installing: `claude --plugin-dir ./claude-plugin/standing`. If you install while a session is open, run `/reload-plugins`.

Check that it loaded: run `/plugin` and look for `axiom-standing` in the mod line, or run `/standing`.

## Configure

Claude Code asks for these when you enable the plugin, and shows them in `/plugin`. Everything has a safe default; nothing needs setting to start.

| Setting | Default | Meaning |
| --- | --- | --- |
| `default_level` | `L1` | Level for an actor with no record: L0, L1 or L2 |
| `max_auto_level` | `L1` | Highest level checks can earn: L0, L1 or L2 |
| `promote_after` | `10` | Passes in a clean window before one promotion |
| `workspace` | empty | Folder where L1 and L2 may edit files without an extra prompt. Empty means every edit asks |
| `seed` | none | Starting levels, as `main=L2` or `general-purpose=L1`. Used only when an actor is first recorded |
| `require_proof_on_complete` | on | Refuse `TaskUpdate` completed until a check has passed for that task id |
| `exempt_actors` | none | Actors exempt from that rule only, not from the level gate |
| `file_proof_roots` | none | Folders or files file checks may read. Empty uses the workspace; with neither, file checks are refused |
| `http_proof_targets` | none | URLs HTTP checks may call. A path ending in `/` allows that subtree, otherwise only that endpoint. Empty disables HTTP checks |
| `command_allowlist` | none | Exact read-only commands, such as `git rev-parse HEAD` |
| `proof_timeout` | `10` | Seconds, 1 to 20 |
| `levels` | table above | Advanced: `L2=read,local_write,shell,send,spin,unknown`. Categories: `read`, `local_write`, `shell`, `send`, `spend`, `spin`, `scrub`, `write`, `unknown`. Only L3 may use `*` |
| `transcript_line` | on | A dim score line after a check-backed completion and after a demotion. Claude does not read it |

For lists, the dialog takes several entries. From the command line, `claude plugin configure axiom-standing@deploy-axiom-standing --values-stdin` saves one single-line string per setting; separate several entries with `;`. Settings are read when the plugin loads, so restart or `/reload-plugins` to apply a change. An invalid setting is never read as a fallback grant: the level gate steps aside, `/standing` says the settings need attention, and task completion stays refused until you fix them.

## Using it

```
/standing                           the record: actor, level, passes in this window, lifetime fails, last event
/standing log general-purpose       the last 20 events for one actor
/standing set general-purpose L2 checked the deliverable myself
```

Claude calls `standing_proof` itself. Examples of what it sends:

```json
{"kind":"file","target":"dist/index.html","expect":"Ready for review","task":"3"}
{"kind":"file","target":"artifact.txt","expect":"sha256:<64 hex digits>"}
{"kind":"http","target":"http://127.0.0.1:8000/health","expect":"ready"}
{"kind":"command","target":"git rev-parse HEAD","expect":"<expected commit prefix>"}
```

Standing runs the check and returns only the result, a plain explanation and the actor's level and counters. It never returns the content it read. The `task` is the id Claude passes to `TaskUpdate`; a check without one still counts toward the record but cannot authorize a completion.

- **File:** inside your roots only. The requested path and its real path (after symlinks) must both be inside. The content must contain the expected text in its first 2 MiB, or hash to the expected SHA-256 (files up to 4 MiB).
- **HTTP:** GET only, and only a URL covered by `http_proof_targets`, checked before any request is made. Status must be exactly 200 and the first 2 MiB must contain the expected text. Dot segments, encoded traversal, credentials in the URL and fragments are refused.
- **Command:** the exact argument list must be on your list. No shell, no quotes, no metacharacters. Exit code 0 and the expected text in stdout. You must be sure each listed command only reads.

If a completion is refused, Claude sees:

> Standing needs a passing check before this task can be marked done. Run standing_proof for this task in this session, passing its task id, then try again.

A refused level, in a session where a person can answer, is a normal Claude Code approval request with this reason:

> Standing: general-purpose is at L0. This action needs the owner’s approval. Other Claude Code permission checks still apply.

**Unattended runs: an approval request is not permission.** In `claude -p`, or any run with nobody to ask, Claude Code treats the request as a refusal. That was observed even with `--permission-mode bypassPermissions`: Standing's request still refused the call. Do not assume a prompt will appear.

## What it records

Per actor: level, passes in the current window, lifetime failures, last update time. Per event (the last 50 per actor): time, the task id Claude supplied, the check kind (`file`, `http`, `command`, `invalid`, `level`, `owner`), a SHA-256 of kind, target and expected text, the result, and a fixed explanation sentence. No targets, expected text, file or HTTP content, command output, tool arguments, owner reason text or messages are stored. Hashes are not encryption: a guessable target can be guessed. The in-memory receipts that let a completion through hold only the check kind and, for HTTP, the host, and are cleared when the session ends.

Claude Code itself keeps the tool calls in its transcript according to its own settings. Standing does not change that.

## Known gaps

What the Hermes plugin does that this one does not, or does differently. Nothing below is claimed as parity.

- **Not a sandbox.** Classification reads the tool name and, for Bash, PowerShell and Monitor, the command text with a short pattern list. Disguised commands, wrappers and scripts are labelled `shell`, which L2 allows. Unknown MCP tools are `unknown`, also allowed at L2. A call Claude Code reports as read-only (`isReadOnly`) is labelled `read`; this only removes Standing's extra prompt and never approves anything.
- **Approval boxes are Claude Code's.** Standing returns an `ask`; Claude Code owns the dialog. The unattended refusal was observed in `claude -p`. The interactive dialog itself was not observed in this check. How a mod's `ask` behaves under auto mode's classifier was not tested.
- **Completion is `TaskUpdate` only.** Hermes gates a board card's `kanban_complete`. Claude Code has no board; the equivalent is the session task list. `TodoWrite` is not gated. Per Anthropic's tools reference the task tools are absent by default on newer models (they need `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` or an `--allowedTools` entry); with no task tools there is nothing to gate. Claude can also finish work and simply never mark it complete; Standing refuses a false "done", it does not make anyone claim one.
- **No task-card comments.** The Hermes plugin posts a score line on the kanban card. Here `transcript_line` writes a dim line into your transcript instead. It is not durable and not delivered anywhere.
- **The "own card" exception is wider.** Hermes lets a worker always report on its own assigned card. Here the session task tools (`TaskCreate`, `TaskUpdate`, `TodoWrite`) and `SubagentHandback` are never gated by level, so a worker can always keep its list and hand back its report. Completing a task is still held by the proof rule.
- **No deferred `tool_call` batches and no V4A patch parser.** Neither exists in Claude Code. `Edit`, `Write` and `NotebookEdit` are the local-write tools; the target's real path must sit inside the workspace (for a new file, its folder's real path). `~` paths are never local.
- **Actors are coarse.** `main`, or the subagent type. Every `general-purpose` subagent shares one record. There is no per-task actor and no delegated-subagent record beyond the type.
- **Proof execution is weaker than Hermes in three ways, because Claude Code's mods API does not offer more.** (1) HTTP: `$.http.fetch` follows up to 5 redirects and cannot be told not to, so a listed URL that redirects elsewhere is followed; no proxy or DNS controls. (2) Command: the child inherits Claude Code's environment (only git config variables are overridden), stdout is capped by the host at 4 MiB (over the cap makes the check unavailable), and the timeout is the host's. (3) File: Standing resolves and checks the real path, then reads it by path; it cannot hold file descriptors, so a symlink swapped between check and read is not caught.
- **The record file.** It is the mod's JSON store, shared by every session on the machine under one Claude Code config folder. Read-then-write is not atomic across sessions, so two sessions finishing checks at the same instant can lose one update. There is no `db_path`, no cross-profile sharing and no migration; a different `CLAUDE_CONFIG_DIR` has its own record. The model-facing guard on the file matches its name in a path or command text. A glob, an encoded string or a copy made some other way gets past it. Anything that can edit your config folder can edit the record.
- **Owner identity is local only.** `/standing set` from Remote Control or any messaging channel is refused; v0.1 does not guess who the owner is there. Verified: refused under `claude -p`, accepted from the interactive prompt.
- **A settings change needs a reload.** And a plugin-side change does not apply to an already-running session.

## Tests

```
node --test claude-plugin/tests/
```

Runs the logic against real temp files, a loopback HTTP server and short commands. Only Claude Code's own API is replaced with a small stand-in. It also checks the properties above: the gate never returns `allow`, the level can't be set by anything but the owner's keystroke, proofs can't reach L3, seeds never undo a demotion, and no target or expected text is stored.

```
cd claude-plugin/standing && claude plugin test
```

Loads the real hooks module and fires events through it with Claude Code's test kit (session start, the gate, the completion check, the record guard, the owner-only command). Check what Claude Code reads from the mod with `claude plugin validate --strict claude-plugin/standing`.

## Undo

`claude plugin uninstall axiom-standing` and, if you added the marketplace, `claude plugin marketplace remove deploy-axiom-standing`. The record stays in `<config dir>/plugins/store/` until you delete it. Standing never rewrites or deletes it on its own.

## How it was made

I designed it, set the rules for what it may do and record, and tested it on my own system. AI agents wrote the code under my spec and review. I read JavaScript; I don't claim to write it by hand.

Percy Keith, Deploy Axiom. MIT license.
