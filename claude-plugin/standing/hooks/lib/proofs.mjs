// Checks Standing runs itself. A model never supplies a result: it names a kind, a target and
// an expected value, and Standing executes the check through Claude Code's mods API and
// reports pass, fail or unavailable. Output is never returned to the model.
import { isInside } from './policy.mjs'

export const LIMIT = 2 * 1024 * 1024
const META = /[;&|<>`$\\\n\r\0*?[\]{}()!'"]/

export const PROOF_LOCATIONS = 'File proofs run on the machine Claude Code runs on. HTTP targets and exact read-only ' +
  'commands must be listed by the owner in the plugin settings.'

export const unavailable = (reason) => ({
  passed: null,
  reason: reason + ' No pass or demotion was recorded. ' + PROOF_LOCATIONS,
})

export function argv(target) {
  let words
  if (typeof target === 'string') {
    if (META.test(target)) throw new Error('Shell characters are not allowed.')
    words = target.trim().split(/\s+/).filter(Boolean)
  } else if (Array.isArray(target) && target.every((x) => typeof x === 'string')) {
    words = [...target]
  } else {
    throw new Error('Use a command string or a list of words.')
  }
  if (!words.length || words.some((x) => !x || META.test(x))) throw new Error('Shell characters and empty commands are not allowed.')
  return words
}

export function sameWords(a, b) { return a.length === b.length && a.every((x, i) => x === b[i]) }

export function credentialPath(path) {
  const parts = String(path).split('/').filter(Boolean).map((x) => x.toLowerCase())
  const named = new Set(['.ssh', '.aws', '.gnupg', 'auth.json', '.netrc', '.npmrc', '.pypirc'])
  if (parts.some((p) => named.has(p) || p.includes('credentials') || p.includes('keychain') ||
    p.startsWith('.env') || p.startsWith('id_') || p.endsWith('.pem') || p.endsWith('.key'))) return true
  return parts.some((p, i) => (p === '.config' && parts[i + 1] === 'gcloud') || (p === '.docker' && parts[i + 1] === 'config.json'))
}

// Lexical normalisation of an absolute path: resolves "." and ".." without touching the disk.
export function normalize(path) {
  const out = []
  for (const part of path.split('/')) {
    if (!part || part === '.') continue
    if (part === '..') out.pop()
    else out.push(part)
  }
  return '/' + out.join('/')
}

const URL_RE = /^(https?):\/\/([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?$/i

export function proofUrl(value) {
  if (typeof value !== 'string' || /[\s\x00-\x1f\x7f\\]/.test(value)) {
    throw new Error('Use an absolute HTTP proof URL without credentials or control characters.')
  }
  const m = URL_RE.exec(value)
  if (!m || m[5] || !m[2] || m[2].includes('@')) throw new Error('Use an absolute HTTP proof URL without credentials or fragments.')
  const hostPort = /^(\[[0-9a-fA-F:.]+\]|[^:[\]]+)(?::(\d{1,5}))?$/.exec(m[2])
  if (!hostPort) throw new Error('The HTTP proof URL has an invalid host or port.')
  const scheme = m[1].toLowerCase()
  const port = hostPort[2] ? Number(hostPort[2]) : scheme === 'https' ? 443 : 80
  if (port < 1 || port > 65535) throw new Error('The HTTP proof URL has an invalid port.')
  const path = m[3] || ''
  let decoded
  try { decoded = decodeURIComponent(path) } catch { throw new Error('HTTP proof paths cannot contain ambiguous escapes.') }
  if (/[\x00-\x1f\x7f\\%;]/.test(decoded) || [path, decoded].some((p) => p.split('/').some((s) => s === '.' || s === '..'))) {
    throw new Error('HTTP proof paths cannot contain traversal or ambiguous escapes.')
  }
  return { scheme, host: hostPort[1].toLowerCase(), port, path, query: m[4] ?? '' }
}

export function urlAllowed(url, prefixes) {
  const path = url.path || '/'
  return prefixes.some((p) => {
    if (p.scheme !== url.scheme || p.host !== url.host || p.port !== url.port || p.query !== url.query) return false
    const base = p.path || '/'
    return base.endsWith('/') ? path.startsWith(base) : path === base
  })
}

export function receiptLabel(kind, target) {
  if (kind === 'http') {
    try {
      const host = proofUrl(target).host
      if (/^[a-z0-9.-]{1,253}$/.test(host)) return 'http ' + host
    } catch { /* fall through */ }
    return 'http'
  }
  return kind === 'file' || kind === 'command' ? kind : 'check'
}

export async function sha256(text) {
  const bytes = new TextEncoder().encode(text)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

function within(io, promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = io.after(ms, () => reject(new Error('timeout')))
    promise.then((v) => { timer.cancel(); resolve(v) }, (e) => { timer.cancel(); reject(e) })
  })
}

export class ProofRunner {
  constructor(io, settings) { this.io = io; this.s = settings }

  async run(kind, target, expect) {
    if (typeof expect !== 'string' || !expect.trim()) return unavailable('The check needs a non-empty expected result.')
    try {
      let passed
      if (kind === 'file') passed = await this.file(target, expect)
      else if (kind === 'http') {
        if (!this.s.httpProofTargets.length) return unavailable('HTTP proofs are disabled until the owner sets http_proof_targets.')
        passed = await this.http(target, expect)
      } else if (kind === 'command') {
        const words = argv(target)
        if (!this.s.commandAllowlist.some((allowed) => sameWords(allowed, words))) {
          return unavailable('That exact command is not on the owner’s read-only list.')
        }
        passed = await this.command(words, expect)
      } else return unavailable('Choose a file, http, or command check.')
      return { passed: Boolean(passed), reason: passed ? 'The check passed.' : 'The result did not match the expected result.' }
    } catch {
      // An exception can carry a path, a URL, output or other private text. Say nothing of it.
      return unavailable('The check could not be run safely or the target was unavailable.')
    }
  }

  async file(target, expect) {
    const io = this.io
    if (typeof target !== 'string' || !target || target.includes('\0') || target.startsWith('~')) throw new Error('refused')
    const cwd = await io.cwd()
    const requested = normalize(target.startsWith('/') ? target : cwd + '/' + target)
    const roots = this.s.fileProofRoots.length ? this.s.fileProofRoots : this.s.workspace ? [this.s.workspace] : []
    if (!roots.length || credentialPath(requested)) throw new Error('refused')
    const realRoots = []
    for (const root of roots) {
      const stat = await io.stat(root, { resolve: true }).catch(() => undefined)
      if (stat?.realPath) realRoots.push(stat.realPath)
    }
    const info = await io.stat(requested, { resolve: true })
    const real = info.realPath
    if (info.kind !== 'file' || !real || credentialPath(real)) throw new Error('refused')
    if (!roots.some((r) => isInside(requested, normalize(r))) || !realRoots.some((r) => isInside(real, r))) throw new Error('refused')
    // Never read inside Claude Code's own config folder, unless it is the configured workspace.
    const home = await io.home()
    const config = (await io.configDir()) || (home ? home + '/.claude' : '')
    if (config) {
      const protectedRoot = (await io.stat(config, { resolve: true }).catch(() => undefined))?.realPath ?? normalize(config)
      const ws = this.s.workspace ? (await io.stat(this.s.workspace, { resolve: true }).catch(() => undefined))?.realPath : undefined
      if (isInside(real, protectedRoot) && !(ws && isInside(real, ws))) throw new Error('refused')
    }
    const digest = expect.startsWith('sha256:') ? expect.slice(7) : expect
    if (/^[0-9a-fA-F]{64}$/.test(digest)) {
      if (info.size > 4 * 1024 * 1024) throw new Error('too large to hash')
      const { base64 } = await io.read(real, { as: 'bytes' })
      const raw = atob(base64)
      const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0))
      const hash = await crypto.subtle.digest('SHA-256', bytes)
      return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, '0')).join('') === digest.toLowerCase()
    }
    return (await io.read(real, { length: LIMIT })).includes(expect)
  }

  async http(target, expect) {
    const url = proofUrl(target)
    // The owner's scope is checked before any request is made.
    if (!urlAllowed(url, this.s.httpProofTargets.map(proofUrl))) throw new Error('refused')
    const response = await within(this.io, this.io.fetch(target, { method: 'GET' }), this.s.proofTimeout * 1000)
    if (response.status !== 200) return false
    return String(response.text ?? '').slice(0, LIMIT).includes(expect)
  }

  async command(words, expect) {
    const result = await this.io.run(words, {
      stdin: '',
      timeoutMs: this.s.proofTimeout * 1000,
      env: { LANG: 'C', LC_ALL: 'C', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0' },
    })
    if (result.isStdoutTruncated) throw new Error('over budget')
    return result.exitCode === 0 && result.stdout.includes(expect)
  }
}
