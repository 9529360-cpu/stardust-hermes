/**
 * The Task Center's row model: a pure projection of what the session-owned stores
 * already know (blocking prompts, background commands, subagents) into rows the
 * activity page can group and describe. No UI, no store writes.
 *
 * Every mapping here is a table with a default entry. A backend that grows a new
 * state, rail or phase reaches the page as a generic row (title + state + open),
 * never as a hidden row or a crash — the same lookup serves a newer backend talking
 * to an older desktop and the reverse.
 */
import type { ClarifyRequest } from '@/store/clarify'
import type { ComposerStatusItem } from '@/store/composer-status'
import type {
  ApprovalRequest,
  SecretRequest,
  SudoRequest,
  VaultCodeRequest,
  VaultSaveLoginRequest,
  VaultUnlockRequest
} from '@/store/prompts'
import { buildSubagentTree, type SubagentNode, type SubagentProgress } from '@/store/subagents'

/** A successful row stays where it was this long before it is filed under recent. */
export const LINGER_MS = 30_000
/** A failure that nobody dealt with leaves "needs you" after this long. */
export const ATTENTION_WINDOW_MS = 24 * 60 * 60 * 1000
export const RECENT_WINDOW_MS = 24 * 60 * 60 * 1000
export const RECENT_EXPANDED_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
export const RECENT_LIMIT = 10
export const RECENT_EXPANDED_LIMIT = 50

const FILES_LIMIT = 8
const LINE_MAX = 160

export const KNOWN_TASK_STATES = [
  'running',
  'waiting_for_user',
  'blocked',
  'failed',
  'interrupted',
  'queued',
  'paused',
  'completed',
  'cancelled'
] as const

export type KnownTaskState = (typeof KNOWN_TASK_STATES)[number]
/** Known states autocomplete; anything else is carried through as a generic state. */
export type TaskState = (string & {}) | KnownTaskState

export const KNOWN_TASK_RAILS = [
  'approval',
  'clarify',
  'sudo',
  'secret',
  'vault-unlock',
  'vault-save',
  'vault-code',
  'process',
  'delegation'
] as const

export type KnownTaskRail = (typeof KNOWN_TASK_RAILS)[number]
export type TaskRail = (string & {}) | KnownTaskRail

/** How long the work outlives the app. Only the in-process kind has a producer today. */
export type TaskDurability = (string & {}) | 'process-local'

export type TaskGroupId = 'agents' | 'attention' | 'other' | 'recent' | 'running' | 'upcoming'
export type RenderedTaskGroupId = Exclude<TaskGroupId, 'agents'>

export interface TaskGroupMeta {
  id: TaskGroupId
  /** A slot kept in the order but with no producer yet; it is never rendered. */
  reserved?: boolean
}

/**
 * Display order. `agents` is the place for a per-agent roll-up (who · mission · stage ·
 * next run): it appears only when the scope holds an agent profile. Nothing produces
 * it in this phase, so `groupTaskRows` skips it.
 */
export const TASK_GROUPS: readonly TaskGroupMeta[] = [
  { id: 'attention' },
  { id: 'agents', reserved: true },
  { id: 'running' },
  { id: 'upcoming' },
  { id: 'other' },
  { id: 'recent' }
]

export type TaskTone = 'active' | 'bad' | 'done' | 'muted' | 'warn'

export interface StateMeta {
  /** Rows in one family keep their position when they change state inside it. */
  family: 'active' | 'attention' | 'upcoming'
  /** The group the row settles in once any linger window is over. */
  group: RenderedTaskGroupId
  known: boolean
  terminal: boolean
  tone: TaskTone
}

export const STATE_META: Record<KnownTaskState, StateMeta> = {
  blocked: { family: 'attention', group: 'attention', known: true, terminal: false, tone: 'warn' },
  cancelled: { family: 'active', group: 'recent', known: true, terminal: true, tone: 'muted' },
  completed: { family: 'active', group: 'recent', known: true, terminal: true, tone: 'done' },
  failed: { family: 'attention', group: 'attention', known: true, terminal: true, tone: 'bad' },
  interrupted: { family: 'attention', group: 'attention', known: true, terminal: true, tone: 'warn' },
  paused: { family: 'upcoming', group: 'upcoming', known: true, terminal: false, tone: 'muted' },
  queued: { family: 'upcoming', group: 'upcoming', known: true, terminal: false, tone: 'muted' },
  running: { family: 'active', group: 'running', known: true, terminal: false, tone: 'active' },
  waiting_for_user: { family: 'attention', group: 'attention', known: true, terminal: false, tone: 'warn' }
}

const UNKNOWN_STATE_META: StateMeta = {
  family: 'upcoming',
  group: 'other',
  known: false,
  terminal: false,
  tone: 'muted'
}

export const stateMeta = (state: TaskState): StateMeta =>
  Object.hasOwn(STATE_META, state) ? STATE_META[state as KnownTaskState] : UNKNOWN_STATE_META

export const isKnownRail = (rail: TaskRail): rail is KnownTaskRail =>
  (KNOWN_TASK_RAILS as readonly string[]).includes(rail)

/** What the page says about a row's progress, as data: the page turns it into words in the reader's language. */
export type TaskPhase =
  | { kind: 'exit-code'; code: number }
  | { kind: 'needs-secret'; name: string }
  | { kind: 'needs-vault'; name: string }
  | { kind: 'none' }
  | { kind: 'running' }
  | { kind: 'text'; text: string }
  | { kind: 'working-on'; tool: string }

export interface TaskChild {
  depth: number
  files?: readonly string[]
  id: string
  sessionId?: string
  state: TaskState
  summary?: string
  title: string
  tool?: string
}

export interface TaskRow {
  children?: readonly TaskChild[]
  durability?: TaskDurability
  files?: readonly string[]
  id: string
  phase: TaskPhase
  /** The profile that owns the row. Set by profile-scoped sources; session rows leave it empty. */
  profile?: string
  rail: TaskRail
  /** Runtime id of the conversation the row belongs to, when it belongs to one. */
  sessionId?: string
  sessionTitle?: string
  startedAt?: number
  state: TaskState
  storedSessionId?: string
  summary?: string
  /** Set for a background command: the id its agent terminal tab is keyed by. */
  terminalProcId?: string
  /** Empty means "use the rail's default title". */
  title: string
  updatedAt?: number
}

/** A row once the tracker has seen it: stamped with arrival order and, once it ended, an end time. */
export interface TrackedTaskRow extends TaskRow {
  finishedAt?: number
  observedAt: number
  /** Position inside its group; a row that changes group takes a fresh one (appended at the end). */
  seq: number
}

export interface TaskGroup {
  id: TaskGroupId
  /** Rows an expansion would add (recent only). */
  moreCount: number
  rows: TrackedTaskRow[]
}

export interface GroupOptions {
  expandRecent?: boolean
  /** Rows the reader is hovering or has open: they stay in the group they were showing. */
  pinned?: ReadonlySet<string>
  previous?: ReadonlyMap<string, TaskGroupId>
}

/** First non-empty line, whitespace squeezed and clipped. Row text is always one line. */
export function firstLine(value: unknown, max = LINE_MAX): string {
  if (typeof value !== 'string') {
    return ''
  }

  const line = (value.split(/\r?\n/).find(part => part.trim()) ?? '').replace(/\s+/g, ' ').trim()

  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

const textPhase = (value: unknown): TaskPhase => {
  const text = firstLine(value)

  return text ? { kind: 'text', text } : { kind: 'none' }
}

export interface SessionTaskSources {
  approvals: Readonly<Record<string, ApprovalRequest>>
  background: Readonly<Record<string, readonly ComposerStatusItem[]>>
  clarifies: Readonly<Record<string, ClarifyRequest>>
  secrets: Readonly<Record<string, SecretRequest>>
  subagents: Readonly<Record<string, readonly SubagentProgress[]>>
  sudos: Readonly<Record<string, SudoRequest>>
  vaultCodes: Readonly<Record<string, VaultCodeRequest>>
  vaultSaves: Readonly<Record<string, VaultSaveLoginRequest>>
  vaultUnlocks: Readonly<Record<string, VaultUnlockRequest>>
}

export interface TaskSessionRef {
  sessionTitle?: string
  storedSessionId?: string
}

export type TaskSessionResolver = (runtimeSessionId: null | string) => TaskSessionRef

function sessionFields(sessionId: null | string | undefined, resolve: TaskSessionResolver): Partial<TaskRow> {
  if (!sessionId) {
    return {}
  }

  const { sessionTitle, storedSessionId } = resolve(sessionId)

  return { sessionId, sessionTitle, storedSessionId }
}

function promptRows<T extends { requestId?: string; sessionId: null | string }>(
  rail: KnownTaskRail,
  byKey: Readonly<Record<string, T>>,
  phaseOf: (request: T) => TaskPhase,
  resolve: TaskSessionResolver
): TaskRow[] {
  return Object.entries(byKey).map(([key, request]) => {
    const sessionId = request.sessionId ?? (key || null)

    return {
      id: `${rail}:${sessionId ?? ''}:${request.requestId ?? ''}`,
      phase: phaseOf(request),
      rail,
      state: 'waiting_for_user',
      title: '',
      ...sessionFields(sessionId, resolve)
    }
  })
}

const clarifyQuestion = (request: ClarifyRequest) => request.question || request.questions?.[0]?.question

const PROMPT_ROWS: ReadonlyArray<(sources: SessionTaskSources, resolve: TaskSessionResolver) => TaskRow[]> = [
  (s, r) => promptRows('approval', s.approvals, request => textPhase(request.description), r),
  (s, r) => promptRows('clarify', s.clarifies, request => textPhase(clarifyQuestion(request)), r),
  (s, r) => promptRows('sudo', s.sudos, () => ({ kind: 'none' }), r),
  (s, r) =>
    promptRows(
      'secret',
      s.secrets,
      request => (request.envVar ? { kind: 'needs-secret', name: request.envVar } : { kind: 'none' }),
      r
    ),
  (s, r) =>
    promptRows(
      'vault-unlock',
      s.vaultUnlocks,
      request =>
        request.displayName || request.backend
          ? { kind: 'needs-vault', name: request.displayName || request.backend }
          : { kind: 'none' },
      r
    ),
  (s, r) => promptRows('vault-save', s.vaultSaves, request => textPhase(request.site), r),
  (s, r) => promptRows('vault-code', s.vaultCodes, request => textPhase(request.site), r)
]

function processRows(background: SessionTaskSources['background'], resolve: TaskSessionResolver): TaskRow[] {
  const rows: TaskRow[] = []

  for (const [sessionId, items] of Object.entries(background)) {
    for (const item of items) {
      if (item.type !== 'background') {
        continue
      }

      const state: TaskState = item.state === 'done' ? 'completed' : item.state

      const phase: TaskPhase =
        state === 'running'
          ? { kind: 'running' }
          : state === 'failed' && typeof item.exitCode === 'number'
            ? { code: item.exitCode, kind: 'exit-code' }
            : { kind: 'none' }

      rows.push({
        durability: 'process-local',
        id: `process:${item.id}`,
        phase,
        rail: 'process',
        state,
        terminalProcId: item.id,
        title: firstLine(item.title, 200),
        ...sessionFields(sessionId, resolve)
      })
    }
  }

  return rows
}

function flattenSubagents(nodes: readonly SubagentNode[]): SubagentNode[] {
  return nodes.flatMap(node => [node, ...flattenSubagents(node.children)])
}

function withDepth(nodes: readonly SubagentNode[], depth = 0): TaskChild[] {
  return nodes.flatMap(node => [
    {
      depth,
      files: node.filesWritten.length ? node.filesWritten.slice(0, FILES_LIMIT) : undefined,
      id: node.id,
      sessionId: node.sessionId,
      state: subagentState(node.status),
      summary: firstLine(node.summary) || undefined,
      title: firstLine(node.goal),
      tool: node.currentTool
    },
    ...withDepth(node.children, depth + 1)
  ])
}

/** A subagent that was interrupted was stopped on purpose, so it reads as cancelled, not as a failure. */
function subagentState(status: SubagentProgress['status']): TaskState {
  switch (status) {
    case 'completed':
      return 'completed'

    case 'failed':
      return 'failed'

    case 'interrupted':
      return 'cancelled'

    default:
      return status
  }
}

function groupState(nodes: readonly SubagentNode[]): TaskState {
  const states = nodes.map(node => subagentState(node.status))

  for (const state of ['running', 'queued', 'failed', 'cancelled'] as const) {
    if (states.includes(state)) {
      return state
    }
  }

  return states.find(state => !stateMeta(state).known) ?? 'completed'
}

function delegationRows(subagents: SessionTaskSources['subagents'], resolve: TaskSessionResolver): TaskRow[] {
  const rows: TaskRow[] = []

  for (const [sessionId, list] of Object.entries(subagents)) {
    const groups = new Map<string, SubagentNode[]>()

    for (const root of buildSubagentTree(list)) {
      // A payload from an older emitter has no delegation id; merging those would fuse unrelated work.
      const key = root.delegationId ? `delegation:${root.delegationId}` : `subagent:${sessionId}:${root.id}`

      groups.set(key, [...(groups.get(key) ?? []), root])
    }

    for (const [id, roots] of groups) {
      const nodes = roots.flatMap(root => flattenSubagents([root]))
      const state = groupState(nodes)
      const live = nodes.filter(node => subagentState(node.status) === 'running')

      const active = [...live].sort((a, b) => b.updatedAt - a.updatedAt).find(node => node.currentTool)

      const failedNode = nodes.find(node => subagentState(node.status) === 'failed')

      const phase: TaskPhase =
        state === 'running'
          ? active?.currentTool
            ? { kind: 'working-on', tool: active.currentTool }
            : { kind: 'running' }
          : state === 'failed'
            ? textPhase(failedNode?.summary)
            : { kind: 'none' }

      const files = [...new Set(nodes.flatMap(node => node.filesWritten))].slice(0, FILES_LIMIT)

      const summary = firstLine((state === 'failed' ? failedNode : nodes.find(node => node.summary))?.summary)

      rows.push({
        children: withDepth(roots),
        durability: 'process-local',
        files: files.length ? files : undefined,
        id,
        phase,
        rail: 'delegation',
        startedAt: Math.min(...nodes.map(node => node.startedAt)),
        state,
        summary: summary || undefined,
        title: firstLine(roots[0]?.goal),
        updatedAt: Math.max(...nodes.map(node => node.updatedAt)),
        ...sessionFields(sessionId, resolve)
      })
    }
  }

  return rows
}

/** Every row the session-owned stores can account for right now, in a stable source order. */
export function buildSessionTaskRows(sources: SessionTaskSources, resolve: TaskSessionResolver): TaskRow[] {
  return [
    ...PROMPT_ROWS.flatMap(build => build(sources, resolve)),
    ...delegationRows(sources.subagents, resolve),
    ...processRows(sources.background, resolve)
  ]
}

/** Preserve arrival order and completion times while the source stores update. */
export function trackTaskRows(
  previous: readonly TrackedTaskRow[],
  incoming: readonly TaskRow[],
  now: number
): TrackedTaskRow[] {
  const byId = new Map(previous.map(row => [row.id, row]))
  let sequence = previous.reduce((max, row) => Math.max(max, row.seq), -1) + 1

  return incoming.map(row => {
    const before = byId.get(row.id)
    const wasTerminal = before && stateMeta(before.state).terminal
    const terminal = stateMeta(row.state).terminal

    return {
      ...row,
      finishedAt: terminal ? ((wasTerminal ? before.finishedAt : undefined) ?? row.updatedAt ?? now) : undefined,
      observedAt: before?.observedAt ?? row.startedAt ?? now,
      seq: before?.seq ?? sequence++
    }
  })
}

function placeRow(row: TrackedTaskRow, now: number): null | RenderedTaskGroupId {
  const meta = stateMeta(row.state)
  const age = now - (row.finishedAt ?? row.observedAt)

  if (meta.group === 'attention') {
    return meta.terminal && age > ATTENTION_WINDOW_MS ? null : 'attention'
  }

  if (meta.group === 'recent') {
    if (age < LINGER_MS) {
      return 'running'
    }

    return age <= RECENT_EXPANDED_WINDOW_MS ? 'recent' : null
  }

  return meta.group
}

const finishedOrObserved = (row: TrackedTaskRow) => row.finishedAt ?? row.observedAt

/**
 * Bucket rows into the fixed group order, drop the empty ones, and order each:
 * arrival order inside a group (new rows append), newest first for recent.
 */
export function groupTaskRows(rows: readonly TrackedTaskRow[], now: number, options: GroupOptions = {}): TaskGroup[] {
  const buckets = new Map<TaskGroupId, TrackedTaskRow[]>()

  for (const row of rows) {
    let target = placeRow(row, now)

    if (!target) {
      continue
    }

    const before = options.pinned?.has(row.id) ? options.previous?.get(row.id) : undefined

    // The reader is on this row: it does not walk away from the pointer. A failure still goes to attention.
    if (before && target !== 'attention' && before !== 'agents') {
      target = before
    }

    buckets.set(target, [...(buckets.get(target) ?? []), row])
  }

  const groups: TaskGroup[] = []

  for (const meta of TASK_GROUPS) {
    const list = meta.reserved ? undefined : buckets.get(meta.id)

    if (!list?.length) {
      continue
    }

    if (meta.id !== 'recent') {
      groups.push({
        id: meta.id,
        moreCount: 0,
        rows: [...list].sort((a, b) => a.seq - b.seq || a.id.localeCompare(b.id))
      })

      continue
    }

    const newestFirst = [...list].sort((a, b) => finishedOrObserved(b) - finishedOrObserved(a) || b.seq - a.seq)
    const dayCutoff = now - RECENT_WINDOW_MS
    const inDay = newestFirst.filter(row => finishedOrObserved(row) >= dayCutoff)

    if (options.expandRecent) {
      groups.push({ id: 'recent', moreCount: 0, rows: newestFirst.slice(0, RECENT_EXPANDED_LIMIT) })

      continue
    }

    if (inDay.length === 0) {
      continue
    }

    const shown = inDay.slice(0, RECENT_LIMIT)

    groups.push({
      id: 'recent',
      moreCount: Math.min(newestFirst.length, RECENT_EXPANDED_LIMIT) - shown.length,
      rows: shown
    })
  }

  return groups
}

/** The quiet counts for the page's summary line. A linger-window completion is not "in progress". */
export function summarizeTaskGroups(groups: readonly TaskGroup[]): { attention: number; running: number } {
  const attention = groups.find(group => group.id === 'attention')?.rows.length ?? 0

  const running =
    groups.find(group => group.id === 'running')?.rows.filter(row => !stateMeta(row.state).terminal).length ?? 0

  return { attention, running }
}
