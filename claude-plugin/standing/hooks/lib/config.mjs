// Owner settings. Invalid configuration is reported and never silently grants authority.
import { CATEGORIES, DEFAULT_LEVELS, parseLevel } from './policy.mjs'
import { argv, proofUrl } from './proofs.mjs'

export const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/

export function actorName(value) {
  if (typeof value !== 'string' || !NAME_RE.test(value)) {
    throw new Error('Use a short actor name without spaces or control characters.')
  }
  return value
}

const list = (value) => {
  if (value === undefined || value === null || value === '') return []
  if (Array.isArray(value)) return value
  // `claude plugin configure` saves one single-line string, so entries may be separated by ;
  if (typeof value === 'string') return value.split(';').map((x) => x.trim()).filter(Boolean)
  throw new Error('Expected a list.')
}

const absolute = (value) => typeof value === 'string' && value.startsWith('/')

/**
 * Turn the plugin's userConfig values into settings. Throws on anything invalid.
 * Lists arrive as arrays of strings; maps are written as "name=value" entries.
 */
export function parseSettings(options = {}) {
  const s = {
    seed: {}, defaultLevel: 1, levels: Object.fromEntries(Object.entries(DEFAULT_LEVELS).map(([k, v]) => [k, [...v]])),
    workspace: '', fileProofRoots: [], httpProofTargets: [], promoteAfter: 10, maxAutoLevel: 1,
    requireProofOnComplete: true, transcriptLine: true, exemptActors: [], commandAllowlist: [], proofTimeout: 10,
  }
  // `claude plugin configure` saves every value as a string, so accept "10" and "false" too.
  const o = Object.fromEntries(Object.entries(options ?? {}).map(([k, v]) => [k,
    v === 'true' ? true : v === 'false' ? false : typeof v === 'string' && /^\d+$/.test(v) && ['promote_after', 'proof_timeout'].includes(k) ? Number(v) : v]))
  if (o.default_level !== undefined && o.default_level !== '') s.defaultLevel = parseLevel(o.default_level, 2)
  if (o.max_auto_level !== undefined && o.max_auto_level !== '') s.maxAutoLevel = parseLevel(o.max_auto_level, 2)
  for (const entry of list(o.seed)) {
    const [name, level, ...rest] = String(entry).split('=')
    if (rest.length) throw new Error('Seeds are written name=L0, name=L1 or name=L2.')
    s.seed[actorName(name.trim())] = parseLevel(level, 2) // L3 is never a seed
  }
  if (o.promote_after !== undefined && o.promote_after !== '') {
    if (!Number.isInteger(o.promote_after) || o.promote_after < 1) throw new Error('promote_after must be a positive whole number.')
    s.promoteAfter = o.promote_after
  }
  if (o.proof_timeout !== undefined && o.proof_timeout !== '') {
    if (!Number.isInteger(o.proof_timeout) || o.proof_timeout < 1 || o.proof_timeout > 20) throw new Error('proof_timeout is 1 to 20 seconds.')
    s.proofTimeout = o.proof_timeout
  }
  if (typeof o.workspace === 'string' && o.workspace) {
    if (!absolute(o.workspace)) throw new Error('The workspace must be an absolute path.')
    s.workspace = o.workspace
  }
  for (const root of list(o.file_proof_roots)) {
    if (!absolute(root)) throw new Error('File proof roots must be absolute paths.')
    s.fileProofRoots.push(root)
  }
  for (const target of list(o.http_proof_targets)) { proofUrl(target); s.httpProofTargets.push(target) }
  for (const command of list(o.command_allowlist)) s.commandAllowlist.push(argv(command))
  for (const name of list(o.exempt_actors)) s.exemptActors.push(actorName(name))
  for (const key of ['require_proof_on_complete', 'transcript_line']) {
    if (o[key] !== undefined && typeof o[key] !== 'boolean') throw new Error(key + ' must be true or false.')
  }
  if (o.require_proof_on_complete !== undefined) s.requireProofOnComplete = o.require_proof_on_complete
  if (o.transcript_line !== undefined) s.transcriptLine = o.transcript_line
  for (const entry of list(o.levels)) {
    const [name, cats, ...rest] = String(entry).split('=')
    if (rest.length || !Object.hasOwn(DEFAULT_LEVELS, name.trim())) throw new Error('The level map uses L0=..., L1=..., L2=... entries.')
    const names = String(cats ?? '').split(',').map((x) => x.trim()).filter(Boolean)
    if (names.some((x) => !CATEGORIES.includes(x) && x !== '*')) throw new Error('The level map contains an unknown category.')
    if (name.trim() !== 'L3' && names.includes('*')) throw new Error('Only L3 may use the all-actions category.')
    s.levels[name.trim()] = names
  }
  return s
}
