import { describe, expect, it } from 'vitest'

import { selectKanbanTaskCenterTasks } from './task-center-model'
import type { KanbanBoard, KanbanTask } from './types'

const task = (id: string, status: string): KanbanTask => ({
  id,
  status,
  title: id
})

const board = (...tasks: KanbanTask[]): KanbanBoard => ({
  assignees: [],
  columns: [
    { name: 'mixed', tasks }
  ],
  latest_event_id: 1,
  now: 1,
  tenants: []
})

describe('Kanban Task Center projection', () => {
  it('shows only actionable durable work and orders human attention first', () => {
    const tasks = selectKanbanTaskCenterTasks(
      board(
        task('done', 'done'),
        task('scheduled', 'scheduled'),
        task('todo', 'todo'),
        task('running', 'running'),
        task('blocked', 'blocked'),
        task('ready', 'ready'),
        task('review', 'review'),
        task('triage', 'triage'),
        task('archived', 'archived')
      ),
      10
    )

    expect(tasks.map(item => item.id)).toEqual(['blocked', 'running', 'review', 'ready', 'scheduled'])
  })

  it('keeps the surface compact without mutating or re-owning board state', () => {
    const source = board(
      task('blocked-b', 'blocked'),
      task('blocked-a', 'blocked'),
      task('running', 'running')
    )

    expect(selectKanbanTaskCenterTasks(source, 2).map(item => item.id)).toEqual(['blocked-a', 'blocked-b'])
    expect(source.columns[0].tasks.map(item => item.id)).toEqual(['blocked-b', 'blocked-a', 'running'])
  })

  it('returns no rows when the owner has no current durable work', () => {
    expect(selectKanbanTaskCenterTasks(board(task('todo', 'todo'), task('done', 'done')))).toEqual([])
    expect(selectKanbanTaskCenterTasks(undefined)).toEqual([])
  })
})
