// Level policy: which category of action each level may take without an extra approval.
// Pure functions only. Nothing here reads a file, runs a process or touches Claude Code.

export const CATEGORIES = ['read', 'local_write', 'shell', 'send', 'spend', 'spin', 'scrub', 'write', 'unknown']

export const DEFAULT_LEVELS = {
  L0: ['read'],
  L1: ['read', 'local_write'],
  L2: ['read', 'local_write', 'shell', 'send', 'spin', 'unknown'],
  L3: ['*'],
}

// Tools that only look. SubagentHandback and EndConversation are here on purpose: a worker
// must always be able to hand its report back, and nothing should stop a session from ending.
const READ_TOOLS = new Set([
  'Read', 'Grep', 'Glob', 'LSP', 'WebSearch', 'WebFetch', 'ListMcpResourcesTool', 'ReadMcpResourceTool',
  'TaskGet', 'TaskList', 'CronList', 'ListAgents', 'ListPlugins', 'ListSkills', 'SearchPlugins',
  'SearchSkills', 'ToolSearch', 'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode', 'WaitForMcpServers',
  'Skill', 'SubagentHandback', 'ReportFindings', 'EndConversation', 'propose_skills', 'SuggestSkills',
  'SuggestPluginInstall',
  // The session task list is a worker reporting on its own work, so it is open at every level.
  // Completing a task is still held by the proof-before-done rule, not by the level.
  'TaskCreate', 'TaskUpdate', 'TodoWrite',
])
// Edits to files. Local only when the target resolves inside the configured workspace.
const WRITE_TOOLS = new Set(['Edit', 'Write', 'NotebookEdit', 'MultiEdit'])
// Worktree switches change where edits land, so they count as local writes.
const LOCAL_TOOLS = new Set(['EnterWorktree', 'ExitWorktree'])
const SHELL_TOOLS = new Set(['Bash', 'PowerShell', 'Monitor'])
const FIXED = {
  Agent: 'spin', Workflow: 'spin', CronCreate: 'spin', RemoteTrigger: 'spin', ScheduleWakeup: 'spin',
  CronDelete: 'scrub', TaskStop: 'scrub',
  SendMessage: 'send', PushNotification: 'send', SendUserFile: 'send', Artifact: 'send',
  SendFeedback: 'send', ShareOnboardingGuide: 'send',
}

// Own small table, in the spirit of Axioma's public categories. Matched against the whole
// lower-cased tool name, so an MCP tool's server name counts too.
const RULES = {
  send: ['*send_email*', '*send_message*', '*create_post*', '*post_message*', '*post_tweet*', '*publish*'],
  spend: ['*create_payment*', '*confirm_payment*', '*capture_payment*', '*create_checkout*',
    '*create_purchase*', '*create_charge*', '*create_refund*', '*buy_*', '*purchase*'],
  spin: ['*deploy*', '*create_job*', '*job_create*', '*cron*create*'],
  scrub: ['*delete*', '*remove*', '*unlink*', '*rmdir*', '*move*', '*destroy*', '*scrub*'],
}

export function globMatch(pattern, text) {
  const source = pattern.split('*').map((part) => part.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*')
  return new RegExp('^' + source + '$', 's').test(text)
}

export function parseLevel(value, maximum = 3) {
  let n = value
  if (typeof n === 'string' && /^L[0-3]$/.test(n.trim())) n = Number(n.trim()[1])
  if (typeof n !== 'number' || !Number.isInteger(n) || n < 0 || n > maximum) {
    throw new Error('Use a level from L0 through L' + maximum + '.')
  }
  return n
}

export function shellRisks(text) {
  const plain = String(text ?? '').replace(/\\\n/g, '').replace(/['"\\]/g, '')
  const found = new Set()
  if (/\b(?:rm|mv|unlink|rmdir|truncate|Remove-Item|Move-Item)\b|git\s+(?:reset|clean)\b/.test(plain)) found.add('scrub')
  if (/\bgit\s+push\b|\bgh\s+(?:pr\s+(?:create|merge)|release\s+create)\b/.test(plain)) found.add('send')
  if (/\b(?:deploy|terraform\s+apply|kubectl\s+apply|crontab)\b/.test(plain)) found.add('spin')
  return found
}

/**
 * Classify one tool call.
 * @param {{tool: string, input: object, isReadOnly?: boolean}} call
 * @returns {{categories: Set<string>, writePath: string|null}}
 */
export function classify({ tool, input, isReadOnly }) {
  const args = input && typeof input === 'object' ? input : {}
  const name = String(tool ?? '')
  const lower = name.toLowerCase()
  const found = new Set()
  let writePath = null
  if (SHELL_TOOLS.has(name)) {
    const risks = shellRisks(args.command)
    for (const item of risks) found.add(item)
    // Claude Code's own read-only check is used only to relax the label from shell to read.
    // Standing never approves a call, so this can only mean "no extra Standing prompt".
    if (!found.size) found.add(isReadOnly === true && name !== 'Monitor' ? 'read' : 'shell')
  } else if (WRITE_TOOLS.has(name)) {
    found.add('write')
    const path = args.file_path ?? args.notebook_path ?? args.path
    writePath = typeof path === 'string' ? path : null
  } else if (LOCAL_TOOLS.has(name)) {
    found.add('local_write')
  } else if (name in FIXED) {
    found.add(FIXED[name])
  } else if (READ_TOOLS.has(name)) {
    found.add('read')
  } else {
    for (const [category, patterns] of Object.entries(RULES)) {
      if (patterns.some((pattern) => globMatch(pattern, lower))) found.add(category)
    }
    const leaf = lower.split('__').pop()
    if (!found.size && /^(?:read|search|list|get|describe|fetch|find|query)(?:_|$)/.test(leaf)) found.add('read')
    if (!found.size && isReadOnly === true && name.startsWith('mcp__')) found.add('read')
  }
  if (!found.size) found.add('unknown')
  return { categories: found, writePath }
}

export function needsApproval(level, categories, local = false, levels = DEFAULT_LEVELS) {
  if (level >= 3) return false
  const allowed = new Set(levels['L' + level] ?? [])
  if (allowed.has('*')) return false
  for (const item of categories) {
    const effective = item === 'write' && local ? 'local_write' : item
    if (!allowed.has(effective)) return true
  }
  return false
}

// True only when the real path of a target provably sits inside the real workspace root.
export function isInside(real, root) {
  if (typeof real !== 'string' || typeof root !== 'string' || !real || !root) return false
  const base = root.endsWith('/') ? root : root + '/'
  return real === root || real.startsWith(base)
}

// Best effort: Standing's own record file must not be read or edited by the model. This matches
// the file's name in a path or command text. It cannot catch every spelling (a glob, an
// encoded string, a hard link), so it raises the bar and is not a sandbox.
const RECORD = /store\/+axiom-standing/i
export function touchesRecord(tool, input) {
  const args = input && typeof input === 'object' ? input : {}
  const parts = [args.file_path, args.notebook_path, args.path, args.command, args.pattern, args.glob]
  return parts.some((x) => typeof x === 'string' && RECORD.test(x.split(String.fromCharCode(92)).join('/')))
}
