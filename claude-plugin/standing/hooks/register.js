// Standing for Claude Code: earned trust for agent workers.
//
// - tool.check   level gate. Can only tighten (ask); never returns allow.
// - tool.call    standing_proof (runs the check itself) and the proof-before-done gate on TaskUpdate.
// - command.run  /standing: the record, and the owner-only level change.
// - session.*    registers the tool and the command; clears in-memory receipts.
//
// Claude Code's static check requires `$` to be spelled $.noun.method(...) at every call site, so
// each hook builds its own small `io` bundle of exactly the calls it needs and hands that to the
// logic in ./lib. The logic never sees `$`.
import { parseSettings } from './lib/config.mjs'
import { touchesRecord } from './lib/policy.mjs'
import { Standing, UNAVAILABLE } from './lib/service.mjs'

export function register(on, options) {
  let settings = null
  let configError = false
  try {
    settings = parseSettings(options)
  } catch {
    configError = true // reported by /standing and by the completion gate; never grants a fallback level
  }
  const standing = new Standing({ settings, configError })

  on('session.start', async ($, e, next) => {
    try {
      await $.tool.register({
        name: 'standing_proof',
        description: 'Run an independent check and record whether it held. Standing runs the check itself; ' +
          'it never accepts a claim that a check passed. A task cannot be marked completed until a check ' +
          'for that task id has passed in this session. kind is "file" (target: a path; expect: text or a ' +
          'sha256 digest), "http" (target: an owner-approved URL; expect: text) or "command" (target: an ' +
          'owner-approved read-only command; expect: text in its output). Output is not returned.',
        inputSchema: {
          type: 'object',
          properties: {
            kind: { type: 'string', enum: ['file', 'http', 'command'] },
            target: { oneOf: [{ type: 'string' }, { type: 'array', items: { type: 'string' } }] },
            expect: { type: 'string' },
            task: { type: 'string', description: 'The task id this check is for, so it can authorize completing that task.' },
          },
          required: ['kind', 'target', 'expect'],
        },
        isDeferred: false,
      })
    } catch { /* the gate does not depend on the tool; the command below still registers */ }
    try {
      await $.command.register({
        name: 'standing',
        description: 'Show the trust record, or set a level (owner only)',
        argumentHint: '[log <actor> | set <actor> <L0-L3> [reason]]',
      })
    } catch { /* a taken name must not stop the rest of the mod */ }
    return next(e)
  })

  // The level gate. Deliberately no .catch: if it throws, Claude Code's own decision stands.
  on('tool.check', async ($, e, next) => {
    const decided = await next(e)
    const io = {
      agentList: () => $.agent.list(),
      stat: (path, init) => $.fs.stat(path, init),
      store: { get: (k) => $.store.get(k), set: (k, v) => $.store.set(k, v), keys: () => $.store.keys() },
    }
    return standing.check(io, e, decided)
  })

  on('tool.call', { tool: 'mcp__axiom-standing__standing_proof' }, async ($, e) => {
    const io = {
      agentList: () => $.agent.list(),
      sessionId: () => $.session.id(),
      cwd: () => $.session.cwd(),
      stat: (path, init) => $.fs.stat(path, init),
      read: (path, init) => $.fs.read(path, init),
      home: () => $.env.get('HOME'),
      configDir: () => $.env.get('CLAUDE_CONFIG_DIR'),
      fetch: (url, init) => $.http.fetch(url, init),
      run: (words, init) => $.process.run(words, init),
      after: (ms, fn) => $.clock.after(ms, fn),
      log: (text) => $.ui.log(text),
      store: { get: (k) => $.store.get(k), set: (k, v) => $.store.set(k, v), keys: () => $.store.keys() },
    }
    return { result: await standing.prove(io, e) }
  }).catch(() => ({ result: JSON.stringify({ error: 'Standing could not record this check. It has not counted toward completion.' }) }))

  // Keeps the model away from Standing's own record file. Best effort, not a sandbox.
  on('tool.call', { tool: ['Read', 'Edit', 'Write', 'NotebookEdit', 'MultiEdit', 'Grep', 'Glob', 'Bash', 'PowerShell'] }, async ($, e, next) => {
    if (touchesRecord(e.tool, e)) return { deny: 'Standing keeps its own record. It is not for the model to read or change.' }
    return next(e)
  })

  // Proof before done. This one fails closed: an error in the gate refuses a completion.
  on('tool.call', { tool: 'TaskUpdate' }, async ($, e, next) => {
    if (!standing.isCompletion(e)) return next(e)
    const io = {
      agentList: () => $.agent.list(),
      sessionId: () => $.session.id(),
      log: (text) => $.ui.log(text),
      store: { get: (k) => $.store.get(k), set: (k, v) => $.store.set(k, v), keys: () => $.store.keys() },
    }
    const refusal = await standing.completionGate(io, e)
    if (refusal) return { deny: refusal }
    const result = await next(e)
    if (!result?.deny && !result?.isError) await standing.note(io, e)
    return result
  }).catch(($, e) => (e?.status === 'completed' ? { deny: UNAVAILABLE } : undefined))

  on('command.run', { command: 'standing' }, async ($, e) => {
    const io = { store: { get: (k) => $.store.get(k), set: (k, v) => $.store.set(k, v), keys: () => $.store.keys() } }
    // Text for the owner only: the record is not put in front of the model.
    return { text: await standing.command(io, e), isTextDisplayOnly: true }
  })

  on('session.end', async ($, e, next) => {
    standing.receipts.clear()
    standing.noted.clear()
    return next(e)
  })
}
