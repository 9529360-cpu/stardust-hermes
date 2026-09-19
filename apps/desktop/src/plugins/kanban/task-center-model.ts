import type { KanbanBoard, KanbanTask } from './types'

const TASK_CENTER_STATUS_ORDER = ['blocked', 'running', 'review', 'ready', 'scheduled'] as const
const TASK_CENTER_STATUSES: ReadonlySet<string> = new Set(TASK_CENTER_STATUS_ORDER)
const STATUS_RANK = new Map<string, number>(TASK_CENTER_STATUS_ORDER.map((status, index) => [status, index]))

/**
 * Durable Kanban work worth surfacing beside the conversation.
 *
 * Backlog and terminal board history stay on the board. Task Center only shows
 * work that is active, queued to run, scheduled, or explicitly waiting on the
 * user. Board/query state remains authoritative; this is a pure projection.
 */
export function selectKanbanTaskCenterTasks(
  board: KanbanBoard | null | undefined,
  limit = 5
): KanbanTask[] {
  if (!board || limit <= 0) {
    return []
  }

  return board.columns
    .flatMap(column => column.tasks)
    .filter(task => TASK_CENTER_STATUSES.has(task.status))
    .sort(
      (left, right) =>
        (STATUS_RANK.get(left.status) ?? Number.MAX_SAFE_INTEGER) -
          (STATUS_RANK.get(right.status) ?? Number.MAX_SAFE_INTEGER) ||
        left.id.localeCompare(right.id)
    )
    .slice(0, limit)
}
