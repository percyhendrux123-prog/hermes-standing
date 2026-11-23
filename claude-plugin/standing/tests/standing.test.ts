import { expect, test } from 'claude-code/testing'

// These tests load the real hooks module and fire events through it the way Claude Code does.
// Claude Code's own answers (the store, the agent list, the session id) are stubs.

function memoryStore(on: any) {
  const saved = new Map<string, unknown>()
  on('store.get', ($: any, e: any) => ({ value: saved.get(e.key) }))
  on('store.set', ($: any, e: any) => { saved.set(e.key, e.value); return { value: undefined } })
  on('store.keys', () => ({ value: [...saved.keys()] }))
  return saved
}

test('session.start registers the proof tool and the /standing command', async ($, on) => {
  const registered: string[] = []
  on('session.start', () => ({ cwd: '/work' }))
  on('tool.register', ($: any, e: any) => { registered.push('tool:' + e.name); return { value: undefined } })
  on('command.register', ($: any, e: any) => { registered.push('command:' + e.name); return { value: undefined } })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  expect(registered).toEqual(['tool:standing_proof', 'command:standing'])
})

test('the gate asks below the level, and never allows', async ($, on) => {
  memoryStore(on)
  on('agent.list', () => ({ value: [] }))
  on('tool.check', () => ({ decision: 'allow' }))
  const shell = await $.tool.check({ tool: 'Bash', input: { command: 'make build' } })
  expect(shell.decision).toBe('ask')
  expect(shell.reason).toContain('Standing: main is at L1')
  const read = await $.tool.check({ tool: 'Read', input: { file_path: 'a.txt' } })
  expect(read.decision).toBe('allow')
  const denied = await $.tool.check({ tool: 'Bash', input: { command: 'make build' } })
  expect(denied.decision).not.toBe('allow')
})

test('a rule that denies a call stays denied', async ($, on) => {
  memoryStore(on)
  on('agent.list', () => ({ value: [] }))
  on('tool.check', () => ({ decision: 'deny', reason: 'rule' }))
  const out = await $.tool.check({ tool: 'Read', input: { file_path: 'a.txt' } })
  expect(out.decision).toBe('deny')
})

test('a task cannot be completed without a passing check', async ($, on) => {
  memoryStore(on)
  on('agent.list', () => ({ value: [] }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('tool.call', () => ({ result: 'ran' }))
  const refused = await $.tool.call({ tool: 'TaskUpdate', taskId: '1', status: 'completed' })
  expect(refused.deny).toContain('Standing needs a passing check')
  const other = await $.tool.call({ tool: 'TaskUpdate', taskId: '1', status: 'in_progress' })
  expect(other.result).toBe('ran')
})

test('the model cannot read or edit the record file', async ($, on) => {
  on('tool.call', () => ({ result: 'ran' }))
  const out = await $.tool.call({ tool: 'Bash', command: 'cat ~/.claude/plugins/store/axiom-standing_x.json' })
  expect(out.deny).toContain('Standing keeps its own record')
})

test('/standing set is refused when it does not come from the owner’s own keystroke', async ($, on) => {
  const saved = memoryStore(on)
  const out = await $.command.run({ command: 'standing', args: 'set main L3 please' })
  expect(out.text).toContain('Only the owner typing at a local Claude Code prompt')
  expect(saved.has('actor:main')).toBe(false)
})
