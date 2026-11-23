// Standing's behaviour, written against a small injected bundle of mods API calls (`io`) so it
// can be tested without Claude Code. register.js wires these methods to events.
import { actorName } from './config.mjs'
import { Ledger } from './ledger.mjs'
import { classify, isInside, needsApproval, parseLevel } from './policy.mjs'
import { ProofRunner, receiptLabel, sha256 } from './proofs.mjs'

export const PROOF_TOOL = 'mcp__axiom-standing__standing_proof'

export const MISSING = 'Standing needs a passing check before this task can be marked done. ' +
  'Run standing_proof for this task in this session, passing its task id, then try again.'
export const UNAVAILABLE = 'Standing cannot check this task’s proof right now, so it has not been marked done. ' +
  'Ask the owner to check the Standing settings, then try again.'
export const OWNER_ONLY = 'Only the owner typing at a local Claude Code prompt can change Standing levels.'

const HELP = 'Use /standing, /standing log <actor>, or /standing set <actor> <L0-L3> [reason].'

export class Standing {
  /**
   * @param {{settings: object|null, configError?: boolean}} parts
   * Every method that touches the outside world takes `io`: a small bundle of the mods API calls
   * it needs, built inside the hook that is running (see register.js).
   */
  constructor({ settings, configError = false }) {
    this.settings = settings
    this.configError = configError
    this.receipts = new Map() // `${actor}\n${session}\n${task}` -> safe label, memory only
    this.noted = new Set()
  }

  ledger(io) {
    const s = this.settings
    return new Ledger(io.store, { defaultLevel: s.defaultLevel, promoteAfter: s.promoteAfter, maxAutoLevel: s.maxAutoLevel, seed: s.seed })
  }

  // The actor is "main" for the main conversation and the subagent type for a subagent.
  async actorOf(io, agentId) {
    if (!agentId) return 'main'
    try {
      const found = (await io.agentList()).find((a) => a.id === agentId)
      return actorName(found?.type ?? 'subagent')
    } catch {
      return 'subagent'
    }
  }

  async sessionId(io) {
    try { return String((await io.sessionId()) ?? '') } catch { return '' }
  }

  // tool.check: Standing can only tighten. It returns Claude Code's own decision unchanged, or
  // an `ask`. It never returns `allow`, so it cannot approve anything Claude Code would not.
  async check(io, e, decided) {
    if (this.configError || !decided || decided.decision === 'deny') return decided
    if (e.tool === PROOF_TOOL) return decided // earning standing is open at every level
    try {
      const actor = await this.actorOf(io, e.agentId)
      const { level } = await this.ledger(io).actor(actor)
      if (level >= 3) return decided
      const { categories, writePath } = classify({ tool: e.tool, input: e.input, isReadOnly: e.isReadOnly })
      const local = writePath !== null && (await this.isLocalWrite(io, writePath))
      if (!needsApproval(level, categories, local, this.settings.levels)) return decided
      if (decided.decision === 'ask') return decided // already goes to a person
      return {
        decision: 'ask',
        reason: `Standing: ${actor} is at L${level}. This action needs the owner’s approval. Other Claude Code permission checks still apply.`,
      }
    } catch {
      return decided // an internal error never blocks ordinary work
    }
  }

  async isLocalWrite(io, path) {
    const workspace = this.settings.workspace
    if (!workspace || typeof path !== 'string' || !path || path.includes('\0') || path.startsWith('~')) return false
    try {
      const root = (await io.stat(workspace, { resolve: true }))?.realPath
      if (!root) return false
      const own = await io.stat(path, { resolve: true }).catch(() => undefined)
      if (own) return own.realPath ? isInside(own.realPath, root) : false
      // A new file: its folder must exist, resolve inside the workspace, and the name be plain.
      const cut = path.lastIndexOf('/')
      const name = path.slice(cut + 1)
      if (!name || name === '.' || name === '..') return false
      const folder = await io.stat(cut < 0 ? '.' : path.slice(0, cut + 1), { resolve: true }).catch(() => undefined)
      return Boolean(folder?.realPath) && isInside(folder.realPath, root)
    } catch {
      return false
    }
  }

  isCompletion(e) {
    return e.tool === 'TaskUpdate' && e.status === 'completed'
  }

  protectedFor(actor) {
    return this.settings ? this.settings.requireProofOnComplete && !this.settings.exemptActors.includes(actor) : true
  }

  // Returns a refusal message, or undefined when the completion may go ahead.
  async completionGate(io, e) {
    if (!this.settings) return UNAVAILABLE // a broken configuration fails closed here, and only here
    const actor = await this.actorOf(io, e.agentId)
    if (!this.protectedFor(actor)) return undefined
    try {
      await this.ledger(io).actor(actor) // a storage failure must also fail closed
      const session = await this.sessionId(io)
      const task = String(e.taskId ?? '').trim()
      return session && task && this.receipts.has(this.key(actor, session, task)) ? undefined : MISSING
    } catch {
      return UNAVAILABLE
    }
  }

  key(actor, session, task) { return `${actor}\n${session}\n${task}` }

  // Called after a completion actually went through.
  async note(io, e) {
    try {
      if (!this.settings?.transcriptLine) return
      const actor = await this.actorOf(io, e.agentId)
      const session = await this.sessionId(io)
      const task = String(e.taskId ?? '').trim()
      const key = this.key(actor, session, task)
      if (!this.receipts.has(key) || this.noted.has(key)) return
      this.noted.add(key)
      const row = await this.ledger(io).actor(actor)
      await io.log(`Standing: ${actor} L${row.level} · check passed (${this.receipts.get(key)}) · ` +
        `${row.passes} passes this window / ${row.fails} fails all time.`)
    } catch { /* best effort: never change the completion */ }
  }

  async prove(io, e) {
    try {
      if (this.configError) throw new Error('settings')
      const actor = await this.actorOf(io, e.agentId)
      const session = await this.sessionId(io)
      const task = typeof e.task === 'string' ? e.task.trim() : ''
      const kind = e.kind
      const detail = await sha256(JSON.stringify([kind, e.target, e.expect]))
      // Run the check ourselves. A model-supplied result is never accepted.
      const outcome = await new ProofRunner(io, this.settings).run(kind, e.target, e.expect)
      const eventKind = ['http', 'file', 'command'].includes(kind) ? kind : 'invalid'
      const state = await this.ledger(io).proof(actor, task, eventKind, detail, outcome.passed, outcome.reason)
      if (outcome.passed && session && task) this.receipts.set(this.key(actor, session, task), receiptLabel(kind, e.target))
      if (outcome.passed === false && state.level < state.previousLevel && this.settings.transcriptLine) {
        await Promise.resolve(io.log(`Standing: ${actor} L${state.previousLevel} -> L${state.level} · a check failed.`)).catch(() => {})
      }
      return JSON.stringify({
        result: outcome.passed === null ? 'unavailable' : outcome.passed ? 'pass' : 'fail',
        passed: outcome.passed, message: outcome.reason,
        level: 'L' + state.level, passes: state.passes, fails: state.fails,
      })
    } catch {
      return JSON.stringify({ error: 'Standing could not record this check. It has not counted toward completion.' })
    }
  }

  clearSession(session) {
    for (const key of [...this.receipts.keys()]) if (key.split('\n')[1] === session) this.receipts.delete(key)
    for (const key of [...this.noted]) if (key.split('\n')[1] === session) this.noted.delete(key)
  }

  async command(io, e) {
    try {
      if (this.configError) throw new Error('settings')
      const parts = String(e.args ?? '').trim().split(/\s+/).filter(Boolean)
      if (!parts.length) {
        const rows = ['Actor | Level | Passes (this window) | Fails (all time) | Last event']
        for (const a of await this.ledger(io).listing()) rows.push(`${a.name} | L${a.level} | ${a.passes} | ${a.fails} | ${a.last || 'No checks yet.'}`)
        if (rows.length === 1) rows.push('No actors recorded yet.')
        return rows.join('\n')
      }
      if (parts[0] === 'log' && parts.length === 2) {
        const events = await this.ledger(io).log(actorName(parts[1]))
        return events.map((x) => `${x.at} | ${x.kind} | ${x.result} | ${x.reason}`).join('\n') || 'No events yet.'
      }
      if (parts[0] === 'set' && parts.length >= 3) {
        // Only the owner's own keystroke at the terminal counts. Anything else is refused:
        // a plugin, the SDK, a bridge, a schedule or another session. There is no tool for it.
        if (e.origin?.kind !== 'composer') return OWNER_ONLY
        const name = actorName(parts[1])
        const level = parseLevel(parts[2])
        const reasonHash = parts.length > 3 ? await sha256(parts.slice(3).join(' ')) : ''
        await this.ledger(io).setLevel(name, level, reasonHash)
        return `Standing: ${name} is now at L${level}. Other Claude Code permission checks still apply.`
      }
      return HELP
    } catch {
      return 'Standing could not read or change its record. Check the settings, then try again.'
    }
  }
}
