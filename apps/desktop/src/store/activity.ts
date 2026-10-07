import { atom } from 'nanostores'

import { sessionTitle } from '@/lib/chat-runtime'
import type { PreviewServerRestart } from '@/store/preview'
import { sessionMatchesStoredId } from '@/store/session'
import type { ActionStatusResponse, CronJob, CronSuggestion, SessionInfo } from '@/types/hermes'

import type { ClarifyRequest } from './clarify'
import type { ComposerStatusItem } from './composer-status'
import type { ApprovalRequest } from './prompts'
import { buildSubagentTree, type SubagentNode, type SubagentProgress } from './subagents'

const HISTORY_LIMIT = 8
const COMPLETED_TTL_MS = 5 * 60 * 1000

export type RailTaskStatus = 'error' | 'running' | 'success' | 'waiting'

export interface RailTask {
  id: string
  label: string
  detail: string
  status: RailTaskStatus
  updatedAt: number
}

export interface DesktopActionTask {
  status: ActionStatusResponse
  updatedAt: number
}

export const $desktopActionTasks = atom<Record<string, DesktopActionTask>>({})

export function upsertDesktopActionTask(status: ActionStatusResponse): void {
  $desktopActionTasks.set(prune({ ...$desktopActionTasks.get(), [status.name]: { status, updatedAt: Date.now() } }))
}

export function buildRailTasks(
  workingSessionIds: readonly string[],
  attentionSessionIds: readonly string[],
  sessions: readonly SessionInfo[],
  previewRestart: PreviewServerRestart | null,
  actionTasks: Record<string, DesktopActionTask>
): RailTask[] {
  const sessionTasks = new Map<string, RailTask>()

  const upsertSessionTask = (id: string, status: Extract<RailTaskStatus, 'running' | 'waiting'>) => {
    const session = sessions.find(candidate => sessionMatchesStoredId(candidate, id))
    const canonicalId = session?.id ?? id
    const existing = sessionTasks.get(canonicalId)

    // A blocking prompt outranks ordinary running state for the same lineage.
    if (existing?.status === 'waiting' && status === 'running') {
      return
    }

    const activitySeconds = session?.last_active || session?.started_at || 0

    sessionTasks.set(canonicalId, {
      id: `session:${canonicalId}`,
      label: session ? sessionTitle(session) : 'Session task',
      detail: status === 'waiting' ? 'Waiting for your input' : 'Agent task running',
      status,
      updatedAt: activitySeconds > 0 ? activitySeconds * 1000 : Date.now()
    })
  }

  for (const id of workingSessionIds) {
    upsertSessionTask(id, 'running')
  }

  for (const id of attentionSessionIds) {
    upsertSessionTask(id, 'waiting')
  }

  const previewTasks: RailTask[] = previewRestart
    ? [
        {
          id: `preview:${previewRestart.taskId}`,
          label: 'Preview restart',
          detail: previewRestart.message || previewRestart.url,
          status:
            previewRestart.status === 'error' ? 'error' : previewRestart.status === 'running' ? 'running' : 'success',
          updatedAt: Date.now()
        }
      ]
    : []

  const actions: RailTask[] = Object.values(actionTasks).map(({ status, updatedAt }) => ({
    id: `action:${status.name}`,
    label: status.name,
    detail: actionDetail(status),
    status: actionStatus(status),
    updatedAt
  }))

  return [...sessionTasks.values(), ...previewTasks, ...actions].sort((left, right) => right.updatedAt - left.updatedAt)
}

function actionStatus(status: ActionStatusResponse): RailTaskStatus {
  if (status.running) {
    return 'running'
  }

  return status.exit_code === 0 ? 'success' : 'error'
}

function actionDetail(status: ActionStatusResponse): string {
  if (status.running) {
    return 'Running'
  }

  return status.exit_code === 0 ? 'Completed' : `Failed (${status.exit_code ?? 'unknown'})`
}

function prune(tasks: Record<string, DesktopActionTask>): Record<string, DesktopActionTask> {
  const now = Date.now()

  return Object.fromEntries(
    Object.entries(tasks)
      .filter(([, task]) => task.status.running || now - task.updatedAt <= COMPLETED_TTL_MS)
      .sort(([, left], [, right]) => right.updatedAt - left.updatedAt)
      .slice(0, HISTORY_LIMIT)
  )
}

export type TaskCenterStatus = RailTaskStatus | 'interrupted' | 'paused' | 'queued'
export type TaskDurability = 'process-local' | 'restart-durable' | 'turn'
export type TaskCenterRail = 'action' | 'approval' | 'cron' | 'preview' | 'process' | 'session' | 'subagent'
export type TaskCenterAction =
  | 'manage-cron'
  | 'open-session'
  | 'review-cron-suggestion'
  | 'stop-process'
export type TestResultStatus = 'running' | 'passed' | 'failed'

export interface TestResultCard {
  command: string
  exitCode?: number
  status: TestResultStatus
}

/** Optional workspace facts projected from existing session/project caches. */
export interface TaskWorkspaceContext {
  cwd?: string
  project?: string
  worktree?: string
  branch?: string
}

export interface TaskCenterTask extends Omit<RailTask, 'status'> {
  action?: TaskCenterAction
  approvalRef?: string
  approvalRequest?: ApprovalRequest
  artifactRefs?: string[]
  clarifyRequest?: ClarifyRequest
  cronSuggestion?: CronSuggestion
  depth?: number
  durability?: TaskDurability
  ownerSessionId?: string
  processId?: string
  workspace?: TaskWorkspaceContext
  rail: TaskCenterRail
  sessionId?: string
  status: TaskCenterStatus
  testResult?: TestResultCard
}

export type TaskCenterView = 'all' | 'needs-attention'
export type TaskCenterCheckStatus = 'failed' | 'loading' | 'passed' | 'pending' | 'unavailable'

/**
 * Return whether a projected task belongs in the user-actionable review queue.
 *
 * Pull-request checks are owned by the PR store rather than the task
 * projection, so callers provide the already-derived check state separately.
 * This keeps the queue a view over existing owners instead of adding a second
 * task lifecycle or duplicating CI state.
 */
export function isTaskCenterNeedsAttention(
  task: TaskCenterTask,
  pullRequestChecks: TaskCenterCheckStatus | undefined = undefined
): boolean {
  const needsInput =
    Boolean(task.approvalRequest || task.clarifyRequest) ||
    (task.status === 'waiting' && (task.rail === 'approval' || task.rail === 'session'))

  const failedTest = task.testResult?.status === 'failed'
  const failedOrPendingChecks = pullRequestChecks === 'failed' || pullRequestChecks === 'pending'
  const cronSuggestion = task.action === 'review-cron-suggestion'

  return needsInput || failedTest || failedOrPendingChecks || cronSuggestion
}

/** Filter the canonical projection without changing task ownership or order. */
export function filterTaskCenterTasks(
  tasks: TaskCenterTask[],
  view: TaskCenterView,
  pullRequestChecksByTask: Readonly<Record<string, TaskCenterCheckStatus | undefined>> = {}
): TaskCenterTask[] {
  if (view === 'all') {
    return tasks
  }

  return tasks.filter(task => isTaskCenterNeedsAttention(task, pullRequestChecksByTask[task.id]))
}

export interface TaskCenterSources {
  actionTasks: Record<string, DesktopActionTask>
  approvalRequests?: Record<string, ApprovalRequest>
  clarifyRequests?: Record<string, ClarifyRequest>
  attentionSessionIds: readonly string[]
  backgroundBySession: Record<string, ComposerStatusItem[]>
  cronJobs: readonly CronJob[]
  cronSuggestions?: readonly CronSuggestion[]
  previewRestart: PreviewServerRestart | null
  runtimeStoredSessionIds?: Record<string, null | string>
  sessions: readonly SessionInfo[]
  subagentsBySession: Record<string, SubagentProgress[]>
  workingSessionIds: readonly string[]
  projectTree?: readonly { id: string; label: string; path?: null | string }[]
}

function workspaceContext(
  session: SessionInfo | undefined,
  projects: readonly { id: string; label: string; path?: null | string }[] = []
): TaskWorkspaceContext | undefined {
  if (!session) {return undefined}
  const cwd = session.cwd?.trim() || undefined
  const repoRoot = session.git_repo_root?.trim() || undefined
  const normalizedCwd = cwd?.toLowerCase()

  const project = normalizedCwd
    ? [...projects]
        .filter(item => {
          const path = item.path
            ?.trim()
            .replace(/[\\/]+$/, '')
            .toLowerCase()

          return (
            path &&
            (normalizedCwd === path || normalizedCwd.startsWith(`${path}/`) || normalizedCwd.startsWith(`${path}\\`))
          )
        })
        .sort((a, b) => (b.path?.length ?? 0) - (a.path?.length ?? 0))[0]?.label
    : undefined

  const context: TaskWorkspaceContext = {
    cwd,
    project,
    branch: session.git_branch?.trim() || undefined,
    worktree: repoRoot && cwd && cwd.toLowerCase() !== repoRoot.toLowerCase() ? cwd : undefined
  }

  return Object.values(context).some(Boolean) ? context : undefined
}

const TASK_STATUS_PRIORITY: Record<TaskCenterStatus, number> = {
  waiting: 0,
  error: 1,
  interrupted: 2,
  running: 3,
  queued: 4,
  paused: 5,
  success: 6
}

const parseTimestamp = (value: null | string | undefined): number => {
  if (!value) {
    return 0
  }

  const parsed = Date.parse(value)

  return Number.isFinite(parsed) ? parsed : 0
}

const cronStatus = (job: CronJob): TaskCenterStatus => {
  const state = String(job.state ?? '')
    .trim()
    .toLowerCase()

  if (state === 'running' || state === 'active') {
    return 'running'
  }

  if (state === 'completed') {
    return 'success'
  }

  if (state === 'error' || state === 'failed') {
    return 'error'
  }

  if (!job.enabled || state === 'paused') {
    return 'paused'
  }

  return 'queued'
}

const subagentStatus = (status: SubagentProgress['status']): TaskCenterStatus => {
  if (status === 'completed') {
    return 'success'
  }

  if (status === 'failed') {
    return 'error'
  }

  if (status === 'interrupted') {
    return 'interrupted'
  }

  return status
}

// Only obvious test-runner commands become result cards. Counts are never
// inferred from arbitrary process output.
const testResultForProcess = (item: ComposerStatusItem): TestResultCard | undefined => {
  if (
    item.type !== 'background' ||
    !/^(?:\.?\/?(?:node_modules\/\.bin\/)?(?:pytest|vitest|jest|mocha|cargo\s+test|go\s+test|dotnet\s+test)\b)/i.test(
      item.title.trim()
    )
  ) {
    return undefined
  }

  return {
    command: item.title,
    exitCode: item.exitCode,
    status: item.state === 'running' ? 'running' : item.state === 'failed' ? 'failed' : 'passed'
  }
}

const flattenSubagents = (
  runtimeSessionId: string,
  nodes: readonly SubagentNode[],
  contextForSession: (id?: string) => TaskWorkspaceContext | undefined,
  depth = 0
): TaskCenterTask[] =>
  nodes.flatMap(node => {
    const detail = node.currentTool || node.summary || node.stream.at(-1)?.text || 'Delegated task'

    const task: TaskCenterTask = {
      action: node.sessionId ? 'open-session' : undefined,
      artifactRefs: node.filesWritten,
      depth,
      durability: 'process-local',
      id: `subagent:${runtimeSessionId}:${node.id}`,
      label: node.goal,
      rail: 'subagent',
      workspace: contextForSession(runtimeSessionId),
      sessionId: node.sessionId,
      status: subagentStatus(node.status),
      detail,
      updatedAt: node.updatedAt
    }

    return [task, ...flattenSubagents(runtimeSessionId, node.children, contextForSession, depth + 1)]
  })

/**
 * Read-only Task Center projection.
 *
 * Every row is derived from an existing authoritative owner cache: session
 * state, subagents, process.list, cron jobs, preview restart, or desktop
 * actions. This store never writes lifecycle state back into those owners.
 */
export function buildTaskCenterTasks(sources: TaskCenterSources): TaskCenterTask[] {
  const contextForSession = (id?: string) => {
    const session = id ? sources.sessions.find(candidate => sessionMatchesStoredId(candidate, id)) : undefined

    return workspaceContext(session, sources.projectTree)
  }

  const approvals = Object.entries(sources.approvalRequests ?? {}).map<TaskCenterTask>(([runtimeKey, request]) => {
    const runtimeSessionId = request.sessionId || runtimeKey
    const mappedStoredSessionId = sources.runtimeStoredSessionIds?.[runtimeSessionId] ?? null
    const storedSessionCandidate = mappedStoredSessionId ?? runtimeSessionId

    const storedSessionId =
      sources.sessions.find(session => sessionMatchesStoredId(session, storedSessionCandidate))?.id ??
      mappedStoredSessionId

    return {
      action: storedSessionId ? 'open-session' : undefined,
      approvalRef: request.requestId,
      approvalRequest: request,
      detail: request.command,
      durability: 'turn',
      id: `approval:${request.requestId || runtimeSessionId}`,
      label: request.description || 'Approval required',
      ownerSessionId: runtimeSessionId,
      workspace: contextForSession(storedSessionId ?? runtimeSessionId),
      rail: 'approval',
      sessionId: storedSessionId ?? undefined,
      status: 'waiting',
      updatedAt: 0
    }
  })

  const clarifications = Object.entries(sources.clarifyRequests ?? {}).map<TaskCenterTask>(([runtimeKey, request]) => {
    const runtimeSessionId = request.sessionId || runtimeKey
    const mappedStoredSessionId = sources.runtimeStoredSessionIds?.[runtimeSessionId] ?? null

    const storedSessionId =
      sources.sessions.find(session => sessionMatchesStoredId(session, mappedStoredSessionId ?? runtimeSessionId))
        ?.id ?? mappedStoredSessionId

    return {
      action: storedSessionId ? 'open-session' : undefined,
      clarifyRequest: request,
      detail: request.question,
      durability: 'turn',
      id: `clarify:${request.requestId || runtimeSessionId}`,
      label: 'Needs input',
      ownerSessionId: runtimeSessionId,
      workspace: contextForSession(storedSessionId ?? runtimeSessionId),
      rail: 'approval',
      sessionId: storedSessionId ?? undefined,
      status: 'waiting',
      updatedAt: 0
    }
  })

  const approvalSessionIds = new Set(
    [...approvals, ...clarifications].flatMap(task => (task.sessionId ? [task.sessionId] : []))
  )

  const base = buildRailTasks(
    sources.workingSessionIds,
    sources.attentionSessionIds,
    sources.sessions,
    sources.previewRestart,
    sources.actionTasks
  )
    .map<TaskCenterTask>(task => {
      if (task.id.startsWith('session:')) {
        const sessionId = task.id.slice('session:'.length)

        return {
          ...task,
          action: 'open-session',
          durability: 'turn',
          rail: 'session',
          sessionId,
          workspace: contextForSession(sessionId)
        }
      }

      return {
        ...task,
        durability: 'process-local',
        rail: task.id.startsWith('preview:') ? 'preview' : 'action'
      }
    })
    .filter(
      task =>
        !(
          task.rail === 'session' &&
          task.status === 'waiting' &&
          task.sessionId &&
          approvalSessionIds.has(task.sessionId)
        )
    )

  const subagents = Object.entries(sources.subagentsBySession).flatMap(([runtimeSessionId, items]) =>
    flattenSubagents(runtimeSessionId, buildSubagentTree(items), contextForSession)
  )

  const processes = Object.entries(sources.backgroundBySession).flatMap(([runtimeSessionId, items]) =>
    items
      .filter(item => item.type === 'background')
      .map<TaskCenterTask>(item => ({
        action: item.state === 'running' ? 'stop-process' : undefined,
        detail:
          item.state === 'running'
            ? 'Background process'
            : item.state === 'failed'
              ? `Failed (${item.exitCode ?? 'unknown'})`
              : 'Completed',
        durability: 'process-local',
        id: `process:${item.id}`,
        label: item.title,
        ownerSessionId: runtimeSessionId,
        processId: item.id,
        workspace: contextForSession(runtimeSessionId),
        rail: 'process',
        status: item.state === 'running' ? 'running' : item.state === 'failed' ? 'error' : 'success',
        testResult: testResultForProcess(item),
        updatedAt: 0
      }))
  )

  const cron = sources.cronJobs.map<TaskCenterTask>(job => ({
    action: 'manage-cron',
    detail: job.last_error || job.schedule_display || job.schedule?.display || job.next_run_at || 'Scheduled task',
    durability: 'restart-durable',
    id: `cron:${job.id}`,
    label: job.name || job.prompt || job.script || 'Scheduled task',
    rail: 'cron',
    status: cronStatus(job),
    updatedAt: parseTimestamp(job.last_run_at)
  }))

  const cronSuggestions = (sources.cronSuggestions ?? []).map<TaskCenterTask>(suggestion => ({
    action: 'review-cron-suggestion',
    cronSuggestion: suggestion,
    detail: suggestion.description || suggestion.job_spec.schedule || 'Suggested scheduled task',
    durability: 'restart-durable',
    id: `cron-suggestion:${suggestion.id}`,
    label: suggestion.title || suggestion.job_spec.name || 'Suggested scheduled task',
    rail: 'cron',
    status: 'waiting',
    updatedAt: parseTimestamp(suggestion.created_at)
  }))

  return [...approvals, ...clarifications, ...base, ...subagents, ...processes, ...cronSuggestions, ...cron].sort(
    (left, right) =>
      TASK_STATUS_PRIORITY[left.status] - TASK_STATUS_PRIORITY[right.status] ||
      right.updatedAt - left.updatedAt ||
      left.id.localeCompare(right.id)
  )
}
