// The local trust record. One JSON value per actor and one capped event list per actor,
// kept through an injected store ({ get, set, keys }). In Claude Code that is the mod's own
// $.store. Metadata only: no tool arguments, targets, expected text or reasons are stored in clear.

export const MAX_EVENTS = 50

const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')

export function actorKey(name) { return 'actor:' + name }
export function eventsKey(name) { return 'events:' + name }

export class Ledger {
  constructor(store, { defaultLevel = 1, promoteAfter = 10, maxAutoLevel = 1, seed = {} } = {}) {
    this.store = store
    this.defaultLevel = defaultLevel
    this.promoteAfter = promoteAfter
    this.maxAutoLevel = maxAutoLevel
    this.seed = seed
  }

  // Seeds and the default only fill a missing row. They never raise or lower an existing one,
  // so a restart can never undo a demotion.
  async actor(name) {
    const saved = await this.store.get(actorKey(name))
    if (saved && Number.isInteger(saved.level)) return saved
    const level = Object.hasOwn(this.seed, name) ? this.seed[name] : this.defaultLevel
    const row = { level, passes: 0, fails: 0, at: nowIso() }
    await this.store.set(actorKey(name), row)
    return row
  }

  async addEvent(name, event) {
    const list = (await this.store.get(eventsKey(name))) ?? []
    list.push({ at: nowIso(), ...event })
    await this.store.set(eventsKey(name), list.slice(-MAX_EVENTS))
  }

  // passed: true (pass), false (fail), null (the check could not run: nothing changes).
  async proof(name, task, kind, detailHash, passed, reason) {
    const before = await this.actor(name)
    let { level, passes, fails } = before
    if (passed === true) {
      passes += 1
      if (passes >= this.promoteAfter && level < this.maxAutoLevel) { level += 1; passes = 0 }
    } else if (passed === false) {
      level = Math.max(0, level - 1)
      passes = 0 // a failure starts a new clean window, even at L0
      fails += 1
    }
    if (passed !== null) await this.store.set(actorKey(name), { level, passes, fails, at: nowIso() })
    const result = passed === null ? 'unavailable' : passed ? 'pass' : 'fail'
    await this.addEvent(name, { task: task || null, kind, hash: detailHash, result, reason })
    if (level !== before.level) {
      await this.addEvent(name, {
        task: task || null, kind: 'level', hash: '', result: 'L' + level,
        reason: passed ? 'Passing checks earned a higher level.' : 'A failed check lowered the level.',
      })
    }
    return { level, passes, fails, previousLevel: before.level }
  }

  async setLevel(name, level, reasonHash = '') {
    const before = await this.actor(name)
    await this.store.set(actorKey(name), { level, passes: 0, fails: before.fails, at: nowIso() })
    await this.addEvent(name, { task: null, kind: 'owner', hash: reasonHash, result: 'L' + level, reason: 'Owner set the level.' })
  }

  async listing() {
    const keys = (await this.store.keys()).filter((key) => key.startsWith('actor:')).sort()
    const rows = []
    for (const key of keys) {
      const name = key.slice('actor:'.length)
      const row = await this.store.get(key)
      if (!row || !Number.isInteger(row.level)) continue
      const events = (await this.store.get(eventsKey(name))) ?? []
      const last = events.at(-1)
      rows.push({ name, level: row.level, passes: row.passes, fails: row.fails, last: last ? last.result + ': ' + last.reason : '' })
    }
    return rows
  }

  async log(name) {
    return ((await this.store.get(eventsKey(name))) ?? []).slice(-20).reverse()
  }
}
