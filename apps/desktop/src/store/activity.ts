import { atom } from 'nanostores'

import { sessionTitle } from '@/lib/chat-runtime'
import type { PreviewServerRestart } from '@/store/preview'
import { sessionMatchesStoredId } from '@/store/session'
import type { ActionStatusResponse, CronJob, SessionInfo } from '@/types/hermes'

import type { ComposerStatusItem } from './composer-status'
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
export type TaskCenterRail = 'action' | 'cron' | 'preview' | 'process' | 'session' | 'subagent'
export type TaskCenterAction = 'manage-cron' | 'open-session' | 'stop-process'

export interface TaskCenterTask extends Omit<RailTask, 'status'> {
  action?: TaskCenterAction
  artifactRefs?: string[]
  depth?: number
  durability?: TaskDurability
  ownerSessionId?: string
  processId?: string
  rail: TaskCenterRail
  sessionId?: string
  status: TaskCenterStatus
}

export interface TaskCenterSources {
  actionTasks: Record<string, DesktopActionTask>
  attentionSessionIds: readonly string[]
  backgroundBySession: Record<string, ComposerStatusItem[]>
  cronJobs: readonly CronJob[]
  previewRestart: PreviewServerRestart | null
  sessions: readonly SessionInfo[]
  subagentsBySession: Record<string, SubagentProgress[]>
  workingSessionIds: readonly string[]
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
  const state = String(job.state ?? '').trim().toLowerCase()

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

const flattenSubagents = (
  runtimeSessionId: string,
  nodes: readonly SubagentNode[],
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
      sessionId: node.sessionId,
      status: subagentStatus(node.status),
      detail,
      updatedAt: node.updatedAt
    }

    return [task, ...flattenSubagents(runtimeSessionId, node.children, depth + 1)]
  })

/**
 * Read-only Task Center projection.
 *
 * Every row is derived from an existing authoritative owner cache: session
 * state, subagents, process.list, cron jobs, preview restart, or desktop
 * actions. This store never writes lifecycle state back into those owners.
 */
export function buildTaskCenterTasks(sources: TaskCenterSources): TaskCenterTask[] {
  const base = buildRailTasks(
    sources.workingSessionIds,
    sources.attentionSessionIds,
    sources.sessions,
    sources.previewRestart,
    sources.actionTasks
  ).map<TaskCenterTask>(task => {
    if (task.id.startsWith('session:')) {
      const sessionId = task.id.slice('session:'.length)

      return {
        ...task,
        action: 'open-session',
        durability: 'turn',
        rail: 'session',
        sessionId
      }
    }

    return {
      ...task,
      durability: 'process-local',
      rail: task.id.startsWith('preview:') ? 'preview' : 'action'
    }
  })

  const subagents = Object.entries(sources.subagentsBySession).flatMap(([runtimeSessionId, items]) =>
    flattenSubagents(runtimeSessionId, buildSubagentTree(items))
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
        rail: 'process',
        status: item.state === 'running' ? 'running' : item.state === 'failed' ? 'error' : 'success',
        updatedAt: 0
      }))
  )

  const cron = sources.cronJobs.map<TaskCenterTask>(job => ({
    action: 'manage-cron',
    detail:
      job.last_error ||
      job.schedule_display ||
      job.schedule?.display ||
      job.next_run_at ||
      'Scheduled task',
    durability: 'restart-durable',
    id: `cron:${job.id}`,
    label: job.name || job.prompt || job.script || 'Scheduled task',
    rail: 'cron',
    status: cronStatus(job),
    updatedAt: parseTimestamp(job.last_run_at)
  }))

  return [...base, ...subagents, ...processes, ...cron].sort(
    (left, right) =>
      TASK_STATUS_PRIORITY[left.status] - TASK_STATUS_PRIORITY[right.status] ||
      right.updatedAt - left.updatedAt ||
      left.id.localeCompare(right.id)
  )
}

