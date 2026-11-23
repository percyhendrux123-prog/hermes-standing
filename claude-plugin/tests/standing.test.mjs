// Tests for Standing's logic (node:test). The checks run for real: temp files, a loopback HTTP
// server and short commands. Only Claude Code's mods API is replaced, by a small `io` bundle.
// Run: node --test claude-plugin/tests/
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { after, before, describe, test } from 'node:test'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'

const run = promisify(execFile)
const LIB = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'standing', 'hooks', 'lib')
const { classify, needsApproval, DEFAULT_LEVELS, parseLevel, touchesRecord } = await import(path.join(LIB, 'policy.mjs'))
const { Ledger } = await import(path.join(LIB, 'ledger.mjs'))
const { parseSettings } = await import(path.join(LIB, 'config.mjs'))
const { ProofRunner, argv, proofUrl, urlAllowed, credentialPath, receiptLabel } = await import(path.join(LIB, 'proofs.mjs'))
const { Standing, MISSING, UNAVAILABLE, OWNER_ONLY, PROOF_TOOL } = await import(path.join(LIB, 'service.mjs'))

const SCRATCH = fs.realpathSync(fs.existsSync(path.join(os.homedir(), '.hermes/cache/scratch'))
  ? path.join(os.homedir(), '.hermes/cache/scratch') : os.tmpdir())
const SECRET = 'CANARY-5d41402a'

function tmp() {
  return fs.realpathSync(fs.mkdtempSync(path.join(SCRATCH, 'standing-test-')))
}

function memoryStore() {
  const map = new Map()
  return {
    map,
    get: async (k) => (map.has(k) ? structuredClone(map.get(k)) : undefined),
    set: async (k, v) => { map.set(k, structuredClone(v)) },
    keys: async () => [...map.keys()],
  }
}

// A real-file-system io bundle; the session is fixed to one actor/session id.
function makeIo({ cwd, agents = [], session = 'sess-1', store = memoryStore(), logs = [], env = {}, fetchImpl } = {}) {
  return {
    store,
    logs,
    agentList: async () => agents,
    sessionId: async () => session,
    cwd: async () => cwd,
    stat: async (p, init) => {
      const abs = path.resolve(cwd, p)
      const st = fs.statSync(abs)
      const out = { kind: st.isFile() ? 'file' : st.isDirectory() ? 'dir' : 'other', size: st.size, mtimeMs: st.mtimeMs, isLink: fs.lstatSync(abs).isSymbolicLink() }
      if (init?.resolve) out.realPath = fs.realpathSync(abs)
      return out
    },
    read: async (p, init) => {
      const buf = fs.readFileSync(path.resolve(cwd, p))
      if (init?.as === 'bytes') return { base64: buf.toString('base64') }
      return buf.subarray(0, init?.length ?? buf.length).toString('utf8')
    },
    home: async () => env.HOME,
    configDir: async () => env.CLAUDE_CONFIG_DIR,
    fetch: fetchImpl ?? (async (url) => {
      const res = await fetch(url, { redirect: 'follow' })
      return { status: res.status, ok: res.ok, headers: {}, text: await res.text() }
    }),
    run: async (words, init) => {
      try {
        const { stdout } = await run(words[0], words.slice(1), { cwd, timeout: init.timeoutMs, env: { ...process.env, ...init.env } })
        return { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
      } catch (error) {
        if (error.killed) throw error
        return { exitCode: error.code ?? 1, stdout: error.stdout ?? '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
      }
    },
    after: (ms, fn) => { const t = setTimeout(fn, ms); return { cancel: () => clearTimeout(t) } },
    log: async (text) => { logs.push(text) },
  }
}

function standing(overrides = {}) {
  const settings = parseSettings(overrides)
  return new Standing({ settings })
}

const allow = { decision: 'allow' }

describe('classification and level gating', () => {
  const cases = [
    ['Read', { file_path: 'x' }, 'read'],
    ['Grep', { pattern: 'x' }, 'read'],
    ['Edit', { file_path: 'x' }, 'write'],
    ['Bash', { command: 'make build' }, 'shell'],
    ['Bash', { command: 'git push origin main' }, 'send'],
    ['Bash', { command: 'rm -rf build' }, 'scrub'],
    ['Bash', { command: 'terraform apply' }, 'spin'],
    ['Agent', { prompt: 'x' }, 'spin'],
    ['SendMessage', {}, 'send'],
    ['mcp__pay__create_payment', {}, 'spend'],
    ['mcp__docs__list_pages', {}, 'read'],
    ['mcp__thing__do_something', {}, 'unknown'],
  ]
  for (const [tool, input, expected] of cases) {
    test(`${tool} ${JSON.stringify(input)} is ${expected}`, () => {
      assert.ok(classify({ tool, input }).categories.has(expected))
    })
  }

  test('a Claude Code read-only Bash call is read, but a risky one stays risky', () => {
    assert.ok(classify({ tool: 'Bash', input: { command: 'date' }, isReadOnly: true }).categories.has('read'))
    assert.ok(classify({ tool: 'Bash', input: { command: 'rm x' }, isReadOnly: true }).categories.has('scrub'))
    assert.ok(classify({ tool: 'Bash', input: { command: 'make' } }).categories.has('shell'))
  })

  test('default level table: what each level needs approval for', () => {
    const need = (level, tool, input, local = false) =>
      needsApproval(level, classify({ tool, input }).categories, local, DEFAULT_LEVELS)
    assert.equal(need(0, 'Read', {}), false)
    assert.equal(need(0, 'Edit', { file_path: 'a' }, true), true) // L0 never writes
    assert.equal(need(1, 'Edit', { file_path: 'a' }, true), false)
    assert.equal(need(1, 'Edit', { file_path: 'a' }, false), true) // non-local write asks
    assert.equal(need(1, 'Bash', { command: 'make' }), true)
    assert.equal(need(2, 'Bash', { command: 'make' }), false)
    assert.equal(need(2, 'Bash', { command: 'git push' }), false) // send is allowed at L2
    assert.equal(need(2, 'Bash', { command: 'rm x' }), true) // scrub asks below L3
    assert.equal(need(2, 'mcp__pay__create_payment', {}), true) // spend asks below L3
    assert.equal(need(3, 'Bash', { command: 'rm x' }), false)
  })

  test('a call in several categories needs every one of them', () => {
    const cats = classify({ tool: 'Bash', input: { command: 'git push && rm x' } }).categories
    assert.ok(cats.has('send') && cats.has('scrub'))
    assert.equal(needsApproval(2, cats), true)
  })

  test('SubagentHandback and the task list are never gated by level', () => {
    for (const tool of ['SubagentHandback', 'TaskCreate', 'TaskUpdate', 'TodoWrite']) {
      assert.equal(needsApproval(0, classify({ tool, input: {} }).categories), false, tool)
    }
    assert.equal(needsApproval(1, classify({ tool: 'ScheduleWakeup', input: {} }).categories), true)
  })

  test('the record file is recognised in paths and commands', () => {
    assert.equal(touchesRecord('Read', { file_path: '/h/.claude/plugins/store/axiom-standing_x.json' }), true)
    assert.equal(touchesRecord('Bash', { command: 'sed -i s/1/3/ ~/.claude/plugins/store/axiom-standing_x.json' }), true)
    assert.equal(touchesRecord('Bash', { command: 'ls' }), false)
  })
})

describe('settings', () => {
  test('defaults', () => {
    const s = parseSettings({})
    assert.equal(s.defaultLevel, 1)
    assert.equal(s.maxAutoLevel, 1)
    assert.equal(s.promoteAfter, 10)
    assert.equal(s.requireProofOnComplete, true)
  })
  test('L3 is never a seed, a default or an automatic ceiling', () => {
    assert.throws(() => parseSettings({ seed: ['worker=L3'] }))
    assert.throws(() => parseSettings({ default_level: 'L3' }))
    assert.throws(() => parseSettings({ max_auto_level: 'L3' }))
    assert.throws(() => parseLevel('L4'))
  })
  test('only L3 may use the all-actions category', () => {
    assert.throws(() => parseSettings({ levels: ['L2=*'] }))
    assert.equal(parseSettings({ levels: ['L3=*'] }).levels.L3[0], '*')
    assert.throws(() => parseSettings({ levels: ['L1=read,nonsense'] }))
  })
  test('values saved as strings by `claude plugin configure` are accepted', () => {
    const s = parseSettings({ promote_after: '5', require_proof_on_complete: 'false', seed: 'main=L2;worker=L0' })
    assert.equal(s.promoteAfter, 5)
    assert.equal(s.requireProofOnComplete, false)
    assert.deepEqual(s.seed, { main: 2, worker: 0 })
  })
  test('invalid settings throw, never silently fall back', () => {
    assert.throws(() => parseSettings({ workspace: 'relative/path' }))
    assert.throws(() => parseSettings({ proof_timeout: 99 }))
    assert.throws(() => parseSettings({ command_allowlist: ['git status; rm x'] }))
    assert.throws(() => parseSettings({ http_proof_targets: ['http://user:pw@example.com/'] }))
  })
})

describe('ledger', () => {
  test('earning: promotes after N passes, capped at the ceiling', async () => {
    const ledger = new Ledger(memoryStore(), { defaultLevel: 0, promoteAfter: 3, maxAutoLevel: 1 })
    let state
    for (let i = 0; i < 3; i++) state = await ledger.proof('w', 't', 'file', 'h', true, 'ok')
    assert.equal(state.level, 1)
    assert.equal(state.passes, 0)
    for (let i = 0; i < 9; i++) state = await ledger.proof('w', 't', 'file', 'h', true, 'ok')
    assert.equal(state.level, 1, 'the default ceiling never grants L2 from proofs alone')
    assert.equal(state.passes, 9)
  })
  test('proofs never reach L3, even with the highest allowed ceiling', async () => {
    const ledger = new Ledger(memoryStore(), { defaultLevel: 2, promoteAfter: 1, maxAutoLevel: 2 })
    for (let i = 0; i < 20; i++) await ledger.proof('w', 't', 'file', 'h', true, 'ok')
    assert.equal((await ledger.actor('w')).level, 2)
  })
  test('one failure demotes by one, counts, and resets the window', async () => {
    const ledger = new Ledger(memoryStore(), { defaultLevel: 2, promoteAfter: 10, maxAutoLevel: 2 })
    await ledger.proof('w', 't', 'file', 'h', true, 'ok')
    const state = await ledger.proof('w', 't', 'file', 'h', false, 'no')
    assert.deepEqual([state.level, state.previousLevel, state.passes, state.fails], [1, 2, 0, 1])
  })
  test('floor is L0', async () => {
    const ledger = new Ledger(memoryStore(), { defaultLevel: 0 })
    assert.equal((await ledger.proof('w', 't', 'file', 'h', false, 'no')).level, 0)
  })
  test('an unavailable check changes nothing but is logged', async () => {
    const ledger = new Ledger(memoryStore(), { defaultLevel: 1 })
    await ledger.proof('w', 't', 'file', 'h', true, 'ok')
    const state = await ledger.proof('w', 't', 'file', 'h', null, 'could not run')
    assert.deepEqual([state.level, state.passes, state.fails], [1, 1, 0])
    assert.equal((await ledger.log('w'))[0].result, 'unavailable')
  })
  test('seeds fill a missing row only: no lowering, no raising, restarts keep a demotion', async () => {
    const store = memoryStore()
    const first = new Ledger(store, { defaultLevel: 1, seed: { w: 2 } })
    assert.equal((await first.actor('w')).level, 2)
    await first.proof('w', 't', 'file', 'h', false, 'no') // demoted to L1
    const restarted = new Ledger(store, { defaultLevel: 1, seed: { w: 2 } })
    assert.equal((await restarted.actor('w')).level, 1, 'a restart does not undo a demotion')
    const reseeded = new Ledger(store, { defaultLevel: 1, seed: { w: 0 } })
    assert.equal((await reseeded.actor('w')).level, 1)
  })
  test('owner level change resets the window and keeps lifetime failures', async () => {
    const ledger = new Ledger(memoryStore(), { defaultLevel: 1 })
    await ledger.proof('w', 't', 'file', 'h', false, 'no')
    await ledger.proof('w', 't', 'file', 'h', true, 'ok')
    await ledger.setLevel('w', 3, 'abc')
    assert.deepEqual(await ledger.actor('w'), { ...(await ledger.actor('w')), level: 3, passes: 0, fails: 1 })
  })
  test('events are capped and hold no free text from the caller', async () => {
    const ledger = new Ledger(memoryStore(), { defaultLevel: 1 })
    for (let i = 0; i < 80; i++) await ledger.proof('w', 't', 'file', 'h', true, 'ok')
    assert.ok((await ledger.store.get('events:w')).length <= 50)
    assert.equal((await ledger.log('w')).length, 20)
  })
})

describe('proof checks run for real', () => {
  let ws, server, base
  before(async () => {
    ws = tmp()
    fs.writeFileSync(path.join(ws, 'artifact.txt'), 'build is ready for review\n')
    fs.mkdirSync(path.join(ws, 'sub'))
    server = http.createServer((req, res) => {
      if (req.url === '/health') { res.end('service ready') } else if (req.url === '/redirect') {
        res.statusCode = 302; res.setHeader('location', '/health'); res.end()
      } else if (req.url === '/missing') { res.statusCode = 404; res.end('ready') } else { res.end('other') }
    })
    await new Promise((r) => server.listen(0, '127.0.0.1', r))
    base = `http://127.0.0.1:${server.address().port}`
  })
  after(() => { server.close(); fs.rmSync(ws, { recursive: true, force: true }) })

  const runner = (extra = {}) => new ProofRunner(makeIo({ cwd: ws, env: { HOME: ws + '-nohome' } }), parseSettings({ workspace: ws, ...extra }))

  test('file: pass, fail, and sha256', async () => {
    const r = runner()
    assert.equal((await r.run('file', 'artifact.txt', 'ready for review')).passed, true)
    assert.equal((await r.run('file', 'artifact.txt', 'something else')).passed, false)
    const digest = (await run('shasum', ['-a', '256', path.join(ws, 'artifact.txt')])).stdout.split(' ')[0]
    assert.equal((await r.run('file', 'artifact.txt', 'sha256:' + digest)).passed, true)
    assert.equal((await r.run('file', 'artifact.txt', '0'.repeat(64))).passed, false)
  })
  test('file: missing, outside the roots, and credential names are unavailable, never failures', async () => {
    const r = runner()
    const other = tmp()
    fs.writeFileSync(path.join(other, 'x.txt'), 'ready')
    fs.writeFileSync(path.join(ws, '.env'), 'ready')
    for (const target of ['nope.txt', path.join(other, 'x.txt'), '../' + path.basename(other) + '/x.txt', '.env', '~/x', 'sub']) {
      assert.equal((await r.run('file', target, 'ready')).passed, null, target)
    }
    fs.rmSync(other, { recursive: true, force: true })
  })
  test('file: a symlink out of the workspace is refused, and with no roots nothing is read', async () => {
    const other = tmp()
    fs.writeFileSync(path.join(other, 'x.txt'), 'ready')
    fs.symlinkSync(path.join(other, 'x.txt'), path.join(ws, 'link.txt'))
    assert.equal((await runner().run('file', 'link.txt', 'ready')).passed, null)
    const none = new ProofRunner(makeIo({ cwd: ws }), parseSettings({}))
    assert.equal((await none.run('file', 'artifact.txt', 'ready')).passed, null)
    fs.rmSync(other, { recursive: true, force: true })
  })
  test('file: nothing inside the Claude Code config folder is read', async () => {
    const cfg = path.join(ws, 'cfg'); fs.mkdirSync(cfg)
    fs.writeFileSync(path.join(cfg, 'notes.txt'), 'ready')
    const r = new ProofRunner(makeIo({ cwd: ws, env: { HOME: ws, CLAUDE_CONFIG_DIR: cfg } }), parseSettings({ file_proof_roots: [ws] }))
    assert.equal((await r.run('file', 'cfg/notes.txt', 'ready')).passed, null)
  })
  test('http: disabled until the owner lists a target; out-of-scope is refused before any request', async () => {
    let calls = 0
    const io = makeIo({ cwd: ws, fetchImpl: async () => { calls++; return { status: 200, text: 'ready' } } })
    assert.equal((await new ProofRunner(io, parseSettings({})).run('http', base + '/health', 'ready')).passed, null)
    const scoped = new ProofRunner(io, parseSettings({ http_proof_targets: [base + '/health'] }))
    assert.equal((await scoped.run('http', base + '/other', 'ready')).passed, null)
    assert.equal((await scoped.run('http', base + '/health?x=1', 'ready')).passed, null)
    assert.equal((await scoped.run('http', 'http://127.0.0.1:1/health', 'ready')).passed, null)
    assert.equal(calls, 0)
  })
  test('http: pass, fail on content, fail on status', async () => {
    const r = runner({ http_proof_targets: [base + '/'] })
    assert.equal((await r.run('http', base + '/health', 'service ready')).passed, true)
    assert.equal((await r.run('http', base + '/health', 'nope')).passed, false)
    assert.equal((await r.run('http', base + '/missing', 'ready')).passed, false)
  })
  test('http: URL scope rules', () => {
    const prefixes = [proofUrl('http://example.com/proofs/'), proofUrl('http://example.com/health')]
    const ok = (u) => urlAllowed(proofUrl(u), prefixes)
    assert.equal(ok('http://example.com/proofs/result'), true)
    assert.equal(ok('http://example.com/delete'), false)
    assert.equal(ok('http://example.com/health'), true)
    assert.equal(ok('http://example.com/health-other'), false)
    for (const bad of ['http://example.com/proofs/../x', 'http://example.com/%2e%2e/b', 'http://u:p@example.com/', 'http://example.com/#f', 'ftp://example.com/', 'http://example.com/a;b', 'http://example.com/%252e']) {
      assert.throws(() => proofUrl(bad), undefined, bad)
    }
  })
  test('command: exact allowlisted argv passes and fails on output; others are unavailable', async () => {
    const r = runner({ command_allowlist: ['git --version'] })
    assert.equal((await r.run('command', 'git --version', 'git version')).passed, true)
    assert.equal((await r.run('command', ['git', '--version'], 'no such text')).passed, false)
    assert.equal((await r.run('command', 'git --help', 'usage')).passed, null)
    assert.equal((await r.run('command', 'git --version extra', 'git')).passed, null)
    assert.equal((await r.run('command', 'date', '2026')).passed, null)
  })
  test('command: shell metacharacters are rejected in strings and lists', () => {
    for (const bad of ['git status; rm x', 'echo $(id)', 'cat a | sh', 'ls *', 'echo `id`', "echo 'x'", 'a > b']) assert.throws(() => argv(bad), undefined, bad)
    assert.throws(() => argv(['git', 'status && ls']))
    assert.throws(() => argv(''))
  })
  test('command: a non-zero exit is a failure, and a timeout is unavailable', async () => {
    const r = runner({ command_allowlist: ['git definitely-not-a-subcommand', 'sleep 5'], proof_timeout: 1 })
    assert.equal((await r.run('command', 'git definitely-not-a-subcommand', 'x')).passed, false)
    assert.equal((await r.run('command', 'sleep 5', 'x')).passed, null)
  })
  test('empty expectations and unknown kinds are unavailable', async () => {
    const r = runner()
    assert.equal((await r.run('file', 'artifact.txt', '   ')).passed, null)
    assert.equal((await r.run('shell', 'ls', 'x')).passed, null)
  })
  test('an unavailable message never echoes the target or an error text', async () => {
    const out = await runner().run('file', '/etc/' + SECRET, 'x')
    assert.equal(out.passed, null)
    assert.ok(!out.reason.includes(SECRET))
  })
  test('credential path rules and labels', () => {
    for (const p of ['/a/.ssh/id_rsa', '/a/.env.local', '/a/x.pem', '/a/.aws/config', '/a/.config/gcloud/x', '/a/.docker/config.json', '/a/my-credentials.json']) assert.equal(credentialPath(p), true, p)
    assert.equal(credentialPath('/a/b/readme.md'), false)
    assert.equal(receiptLabel('http', 'http://Example.com:8080/x?y=1'), 'http example.com')
    assert.equal(receiptLabel('file', '/secret/path'), 'file')
  })
})

describe('the gate: never approves, only asks', () => {
  test('returns Claude Code’s decision unchanged when the level allows the call', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH })
    assert.deepEqual(await s.check(io, { tool: 'Read', input: { file_path: 'x' } }, allow), allow)
    const ask = { decision: 'ask', reason: 'core' }
    assert.deepEqual(await s.check(io, { tool: 'Read', input: {} }, ask), ask)
  })
  test('never returns allow for a call Claude Code asked or denied about', async () => {
    const s = standing({ seed: ['main=L2'] }); const io = makeIo({ cwd: SCRATCH })
    for (const decided of [{ decision: 'ask' }, { decision: 'deny', reason: 'rule' }]) {
      const out = await s.check(io, { tool: 'Bash', input: { command: 'make' } }, decided)
      assert.equal(out.decision, decided.decision)
    }
  })
  test('asks when the actor’s level is below what the call needs, with a plain reason', async () => {
    const s = standing({ seed: ['main=L1'] }); const io = makeIo({ cwd: SCRATCH })
    const out = await s.check(io, { tool: 'Bash', input: { command: 'make' } }, allow)
    assert.equal(out.decision, 'ask')
    assert.match(out.reason, /Standing: main is at L1\./)
  })
  test('an actor is the subagent type, looked up from the agent id', async () => {
    const s = standing({ seed: 'main=L2;Explore=L0' })
    const io = makeIo({ cwd: SCRATCH, agents: [{ id: 'a1', type: 'Explore' }] })
    assert.equal((await s.check(io, { tool: 'Bash', input: { command: 'make' }, agentId: 'a1' }, allow)).decision, 'ask')
    assert.deepEqual(await s.check(io, { tool: 'Bash', input: { command: 'make' } }, allow), allow)
    assert.equal((await s.check(io, { tool: 'Bash', input: { command: 'make' }, agentId: 'gone' }, allow)).decision, 'ask') // unknown id -> "subagent", default L1
  })
  test('local writes: inside the workspace passes at L1, outside or unresolvable asks', async () => {
    const ws = tmp(); const other = tmp()
    const s = standing({ workspace: ws }); const io = makeIo({ cwd: ws })
    const edit = (file_path) => s.check(io, { tool: 'Write', input: { file_path } }, allow)
    assert.deepEqual(await edit('new.txt'), allow)
    assert.deepEqual(await edit(path.join(ws, 'sub-not-there-yet.txt')), allow)
    assert.equal((await edit(path.join(other, 'x.txt'))).decision, 'ask')
    assert.equal((await edit('../' + path.basename(other) + '/x.txt')).decision, 'ask')
    assert.equal((await edit('~/x.txt')).decision, 'ask')
    assert.equal((await edit('')).decision, 'ask')
    fs.symlinkSync(other, path.join(ws, 'out'))
    assert.equal((await edit('out/x.txt')).decision, 'ask', 'a symlinked folder out of the workspace is not local')
    const bare = standing({})
    assert.equal((await bare.check(io, { tool: 'Write', input: { file_path: 'new.txt' } }, allow)).decision, 'ask')
    for (const d of [ws, other]) fs.rmSync(d, { recursive: true, force: true })
  })
  test('L3 skips the action gate', async () => {
    const s = standing(); const store = memoryStore(); const io = makeIo({ cwd: SCRATCH, store })
    await s.ledger(io).setLevel('main', 3)
    const rm = { tool: 'Bash', input: { command: 'rm -rf x' } }
    assert.deepEqual(await s.check(io, rm, allow), allow)
  })
  test('the proof tool is open at every level', async () => {
    const s = standing({ default_level: 'L0' }); const io = makeIo({ cwd: SCRATCH })
    assert.deepEqual(await s.check(io, { tool: PROOF_TOOL, input: {} }, allow), allow)
  })
  test('an internal error leaves Claude Code’s decision alone', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH }); io.store.get = async () => { throw new Error('boom') }
    assert.deepEqual(await s.check(io, { tool: 'Bash', input: { command: 'make' } }, allow), allow)
  })
  test('with invalid settings the level gate steps aside and the completion gate fails closed', async () => {
    let s
    try { parseSettings({ default_level: 'L9' }) } catch { s = new Standing({ settings: null, configError: true }) }
    const io = makeIo({ cwd: SCRATCH })
    assert.deepEqual(await s.check(io, { tool: 'Bash', input: { command: 'make' } }, allow), allow)
    assert.equal(await s.completionGate(io, { tool: 'TaskUpdate', status: 'completed', taskId: '1' }), UNAVAILABLE)
    assert.match(await s.command(io, { args: '', origin: { kind: 'composer' } }), /could not read or change/)
  })
})

describe('proof before done', () => {
  const complete = { tool: 'TaskUpdate', status: 'completed', taskId: '7' }
  test('a completion is detected only for TaskUpdate with status completed', () => {
    const s = standing()
    assert.equal(s.isCompletion(complete), true)
    assert.equal(s.isCompletion({ tool: 'TaskUpdate', status: 'in_progress', taskId: '7' }), false)
    assert.equal(s.isCompletion({ tool: 'TaskCreate', status: 'completed' }), false)
  })
  test('blocked without a proof, allowed after a passing one for the same task in the same session', async () => {
    const ws = tmp(); fs.writeFileSync(path.join(ws, 'a.txt'), 'ready')
    const s = standing({ workspace: ws }); const io = makeIo({ cwd: ws })
    assert.equal(await s.completionGate(io, complete), MISSING)
    const proof = JSON.parse(await s.prove(io, { kind: 'file', target: 'a.txt', expect: 'ready', task: '7' }))
    assert.equal(proof.passed, true)
    assert.equal(await s.completionGate(io, complete), undefined)
    assert.equal(await s.completionGate(io, { ...complete, taskId: '8' }), MISSING, 'another task’s proof does not count')
    assert.equal(await s.completionGate(makeIo({ cwd: ws, session: 'other' , store: io.store }), complete), MISSING, 'another session’s proof does not count')
    fs.rmSync(ws, { recursive: true, force: true })
  })
  test('a proof with no task id earns credit but cannot authorize a completion', async () => {
    const ws = tmp(); fs.writeFileSync(path.join(ws, 'a.txt'), 'ready')
    const s = standing({ workspace: ws }); const io = makeIo({ cwd: ws })
    const proof = JSON.parse(await s.prove(io, { kind: 'file', target: 'a.txt', expect: 'ready' }))
    assert.equal(proof.passes, 1)
    assert.equal(await s.completionGate(io, complete), MISSING)
    fs.rmSync(ws, { recursive: true, force: true })
  })
  test('a failing or unavailable proof does not authorize a completion', async () => {
    const ws = tmp(); fs.writeFileSync(path.join(ws, 'a.txt'), 'ready')
    const s = standing({ workspace: ws }); const io = makeIo({ cwd: ws })
    assert.equal(JSON.parse(await s.prove(io, { kind: 'file', target: 'a.txt', expect: 'nope', task: '7' })).result, 'fail')
    assert.equal(JSON.parse(await s.prove(io, { kind: 'file', target: 'gone.txt', expect: 'x', task: '7' })).result, 'unavailable')
    assert.equal(await s.completionGate(io, complete), MISSING)
    fs.rmSync(ws, { recursive: true, force: true })
  })
  test('the switch and the exemption list turn only this rule off', async () => {
    const io = makeIo({ cwd: SCRATCH })
    assert.equal(await standing({ require_proof_on_complete: false }).completionGate(io, complete), undefined)
    assert.equal(await standing({ exempt_actors: ['main'] }).completionGate(io, complete), undefined)
    const sub = makeIo({ cwd: SCRATCH, agents: [{ id: 'a1', type: 'worker' }] })
    assert.equal(await standing({ exempt_actors: ['main'] }).completionGate(sub, { ...complete, agentId: 'a1' }), MISSING)
  })
  test('L3 does not bypass the completion check', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH })
    await s.ledger(io).setLevel('main', 3)
    assert.equal(await s.completionGate(io, complete), MISSING)
  })
  test('a storage failure fails closed', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH }); io.store.get = async () => { throw new Error('disk') }
    assert.equal(await s.completionGate(io, complete), UNAVAILABLE)
  })
  test('a demotion and a completion write a transcript line only when a check backed it', async () => {
    const ws = tmp(); fs.writeFileSync(path.join(ws, 'a.txt'), 'ready')
    const s = standing({ workspace: ws, seed: ['main=L2'] }); const logs = []; const io = makeIo({ cwd: ws, logs })
    await s.note(io, complete)
    assert.equal(logs.length, 0)
    await s.prove(io, { kind: 'file', target: 'a.txt', expect: 'ready', task: '7' })
    await s.note(io, complete); await s.note(io, complete)
    assert.equal(logs.length, 1)
    assert.match(logs[0], /^Standing: main L2 · check passed \(file\) · 1 passes this window \/ 0 fails all time\.$/)
    await s.prove(io, { kind: 'file', target: 'a.txt', expect: 'wrong', task: '7' })
    assert.equal(logs.at(-1), 'Standing: main L2 -> L1 · a check failed.')
    assert.ok(!logs.join('\n').includes('a.txt'))
    fs.rmSync(ws, { recursive: true, force: true })
  })
  test('what the proof tool returns and what the ledger keeps hold no target or expected text', async () => {
    const ws = tmp(); fs.writeFileSync(path.join(ws, 'a.txt'), SECRET)
    const s = standing({ workspace: ws }); const io = makeIo({ cwd: ws })
    const out = await s.prove(io, { kind: 'file', target: 'a.txt', expect: SECRET, task: '7' })
    assert.ok(!out.includes(SECRET) && !out.includes('a.txt'))
    assert.ok(!JSON.stringify([...io.store.map.entries()]).includes(SECRET))
    assert.ok(!JSON.stringify([...io.store.map.entries()]).includes('a.txt'))
    fs.rmSync(ws, { recursive: true, force: true })
  })
  test('a proof only clears receipts of an ended session', () => {
    const s = standing(); s.receipts.set('main\nA\n1', 'file'); s.receipts.set('main\nB\n1', 'file')
    s.clearSession('A')
    assert.deepEqual([...s.receipts.keys()], ['main\nB\n1'])
  })
})

describe('never self-promote', () => {
  test('only a keystroke from the owner at the prompt can set a level', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH })
    const origins = [{ kind: 'sdk' }, { kind: 'bridge' }, { kind: 'plugin', name: 'x' }, { kind: 'peer' }, { kind: 'peer-send-message' },
      { kind: 'scheduled-trigger' }, { kind: 'task-notification' }, { kind: 'coordinator' }, { kind: 'channel', server: 's' }, undefined, {}]
    for (const origin of origins) {
      assert.equal(await s.command(io, { args: 'set main L3 please', origin }), OWNER_ONLY, JSON.stringify(origin))
    }
    assert.equal((await s.ledger(io).actor('main')).level, 1)
    assert.match(await s.command(io, { args: 'set main L3 my reason', origin: { kind: 'composer' } }), /main is now at L3/)
    assert.equal((await s.ledger(io).actor('main')).level, 3)
  })
  test('the reason is stored as a hash, not text', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH })
    await s.command(io, { args: 'set main L2 ' + SECRET, origin: { kind: 'composer' } })
    const stored = JSON.stringify([...io.store.map.entries()])
    assert.ok(!stored.includes(SECRET))
    assert.match((await s.ledger(io).log('main'))[0].hash, /^[0-9a-f]{64}$/)
  })
  test('bad level or name is refused, and the command text goes to the owner only', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH })
    assert.match(await s.command(io, { args: 'set main L9', origin: { kind: 'composer' } }), /could not read or change/)
    assert.match(await s.command(io, { args: 'set bad/name L1', origin: { kind: 'composer' } }), /could not read or change/)
    assert.match(await s.command(io, { args: 'frobnicate', origin: { kind: 'composer' } }), /^Use \/standing/)
  })
  test('the model-facing proof tool has no way to name a level', async () => {
    const source = fs.readFileSync(path.join(LIB, '..', 'register.js'), 'utf8')
    const schema = source.slice(source.indexOf('inputSchema'), source.indexOf('isDeferred'))
    assert.ok(!/level|promote|seed/i.test(schema))
    const s = standing(); const io = makeIo({ cwd: SCRATCH })
    await s.prove(io, { kind: 'file', target: 'x', expect: 'y', level: 'L3', set: 'L3' })
    assert.equal((await s.ledger(io).actor('main')).level, 1)
  })
  test('the table and the log are readable by the owner', async () => {
    const s = standing(); const io = makeIo({ cwd: SCRATCH })
    assert.match(await s.command(io, { args: '', origin: { kind: 'composer' } }), /No actors recorded yet\./)
    await s.ledger(io).proof('main', '', 'file', 'h', true, 'ok')
    assert.match(await s.command(io, { args: '', origin: { kind: 'composer' } }), /main \| L1 \| 1 \| 0 \|/)
    assert.match(await s.command(io, { args: 'log main', origin: { kind: 'composer' } }), /\| file \| pass \|/)
  })
})
