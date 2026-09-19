import { atom } from 'nanostores'

import { sessionTitle } from '@/lib/chat-runtime'
import type { PreviewServerRestart } from '@/store/preview'
import { sessionMatchesStoredId } from '@/store/session'
import type { ActionStatusResponse, SessionInfo } from '@/types/hermes'

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
