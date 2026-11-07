# Standing: earned trust for Hermes agents (SPEC v0.1, 2026-11-06)

Status: original build spec, kept as written apart from removing internal references. Implementation changes since then are in `SPEC-NOTES.md`. Code MIT, spec CC0. Companion to [hermes-axioma](https://github.com/percyhendrux123-prog/hermes-axioma).

## One sentence
A worker earns what it may do: Standing keeps a per-worker record of claims that held up when re-checked and claims that didn't, and gates each tool call by the level that record has earned.

## Why (incidents from a real multi-agent operation)
- Workers report "done" when it isn't: a worker's "proven" result failed a model-driven re-run. The lead agent ended up re-checking every completion by hand.
- A worker's test touched live data outside its sandbox.
- Over-strict permissions pushed a user close to giving up on their agent (an endless "ask for approval" loop). Strictness must loosen as a record grows.
Prompts and skills can't do this: it needs a durable record, re-run checks and tool-call vetoes.

## Scope v0.1 (smallest useful version; nothing else)
1. **Actors.** An actor = the Hermes profile running the session (`HERMES_HOME` profile name; `default` for the main one). Kanban task id (`HERMES_KANBAN_TASK`) recorded as context, not as a separate actor.
2. **Levels** (configurable map, defaults):
   - L0 read: read/search tools only; any write, send, spend, spin or scrub → `approve` (human gate).
   - L1 draft: + local writes inside the configured workspace; send/spend/spin/scrub → `approve`.
   - L2 act: + send/spin inside existing gates; spend/scrub → `approve`.
   - L3 trusted: no Standing gate (other plugins/gates still apply).
   Category classification reuses the same send/spend/spin/scrub idea as Axioma (own copy of the rule table; no import dependency).
   Unattended runs: an `approve` under cron/gateway follows Hermes's own approval policy (default deny). Document it loudly.
3. **Seeding (no disruption on install).** `standing.seed: {profile: level}` in config. Unlisted profiles start at `standing.default_level` (default L1). Installing never lowers an existing worker below its seeded level.
4. **Proof before done.** Tool `standing_proof(kind, target, expect)` records a proof handle and the plugin re-runs it itself:
   - `http`: GET target, pass if status 200 and `expect` substring present.
   - `file`: pass if sha256(target) == expect (or file exists + contains expect).
   - `command`: allowlisted read-only command (configured list, e.g. `git rev-parse`, `test -f`), pass if stdout contains expect. No shell metacharacters.
   `pre_tool_call` on `kanban_complete`: block with a plain message unless this task has ≥1 passing proof in this session. Config switch `require_proof_on_complete` (default true) and an exempt list.
5. **Earning and losing.**
   - Each passing proof +1. Promotion when passes since last level change ≥ `promote_after` (default 10) and zero fails in that window → level +1 (cap at `max_auto_level`, default L2; L3 only by owner command).
   - A failing proof → demote one level immediately, recorded with the reason.
   - Owner can set any level: `/standing set <profile> <L0-L3> [reason]`.
6. **Visibility.** `/standing` → table: actor, level, passes, fails, last event. `/standing log <profile>` → last 20 events. Plain English.
7. **Storage.** `ctx.plugin_db()` SQLite: `actors(name, level, passes_window, fails_total, updated_at)`, `events(id, at, actor, task_id, kind, detail_hash, result, reason)`. No message bodies, no tool arguments stored in clear (hash targets; keep `kind` and result).
8. **Fail safe.** Any internal error in a hook → log and return no directive (don't brick the agent), EXCEPT the kanban_complete proof check, which fails closed with a clear message when `require_proof_on_complete` is on.

## Out of scope v0.1
Friction budget, per-delegated-subagent actors, cross-machine trust sync, desktop UI, LLM calls. (On the ideas list for later.)

## Acceptance (all must pass, shown with real output)
- `hermes plugins validate <path> --install-deps` and `hermes plugins doctor <path>` clean.
- Unit tests (pytest): level gating per category, seed/no-lowering, promotion after N passes, demotion on one fail, proof kinds pass/fail, command allowlist rejects metacharacters, kanban_complete blocked without proof / allowed with proof, hook error → no directive.
- Live scenario on a throwaway HERMES_HOME with a real `hermes chat -q` run (model-driven, not just unit tests): (a) L0 actor tries a write → approval prompt; (b) worker calls kanban_complete with no proof → blocked with the plain message; (c) passing `standing_proof` then complete → allowed and +1; (d) failing proof → demoted, `/standing` shows it.
- README: what it is, why, install, config, the unattended-run note, disclosure list (what it reads/stores/never does), demo GIF/video placeholder.
- No network calls except `http` proofs the user configures; no subprocess except the allowlisted proof commands.

## Demo (<90 s, for X)
Real terminal: `/standing` table → worker claims done without proof → blocked → proof passes → completes, +1 → a bad proof → demoted. Captions in plain English.
