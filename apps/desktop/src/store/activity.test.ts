import { describe, expect, it } from 'vitest'

import type { CronJob, SessionInfo } from '@/types/hermes'

import { buildRailTasks, buildTaskCenterTasks } from './activity'
import type { ComposerStatusItem } from './composer-status'
import type { SubagentProgress } from './subagents'

const session = (overrides: Partial<SessionInfo> = {}): SessionInfo =>
  ({
    id: 'tip',
    _lineage_root_id: 'root',
    title: 'Research task',
    last_active: 123,
    started_at: 100,
    ...overrides
  }) as SessionInfo

describe('activity task projection', () => {
  it('deduplicates session lineage aliases and lets waiting-for-user outrank running', () => {
    const tasks = buildRailTasks(['root', 'tip'], ['root'], [session()], null, {})

    expect(tasks).toEqual([
      {
        id: 'session:tip',
        label: 'Research task',
        detail: 'Waiting for your input',
        status: 'waiting',
        updatedAt: 123_000
      }
    ])
  })

  it('normalizes backend session timestamps to milliseconds before sorting with renderer tasks', () => {
    const tasks = buildRailTasks(
      ['tip'],
      [],
      [session({ last_active: 10 })],
      null,
      {
        doctor: {
          status: { exit_code: null, lines: [], name: 'doctor', pid: 1, running: true },
          updatedAt: 10_500
        }
      }
    )

    expect(tasks.map(task => task.id)).toEqual(['action:doctor', 'session:tip'])
  })

  it('projects preview restart failures without creating a second task owner', () => {
    const tasks = buildRailTasks(
      [],
      [],
      [],
      { message: 'server failed', status: 'error', taskId: 'p1', url: 'http://x' },
      {}
    )

    expect(tasks).toHaveLength(1)
    expect(tasks[0]).toMatchObject({
      id: 'preview:p1',
      detail: 'server failed',
      status: 'error'
    })
  })
})


const subagent = (overrides: Partial<SubagentProgress> = {}): SubagentProgress => ({
  id: 'worker',
  parentId: null,
  goal: 'Inspect repository',
  status: 'running',
  taskCount: 1,
  taskIndex: 0,
  startedAt: 1_000,
  updatedAt: 2_000,
  filesRead: [],
  filesWritten: [],
  stream: [],
  ...overrides
})

const background = (overrides: Partial<ComposerStatusItem> = {}): ComposerStatusItem => ({
  id: 'proc-1',
  state: 'running',
  title: 'pytest -q',
  type: 'background',
  ...overrides
})

const cron = (overrides: Partial<CronJob> = {}): CronJob => ({
  enabled: true,
  id: 'cron-1',
  name: 'Morning check',
  state: 'scheduled',
  ...overrides
})

describe('task center projection', () => {
  it('projects owner-specific durability without becoming a second lifecycle owner', () => {
    const tasks = buildTaskCenterTasks({
      actionTasks: {},
      attentionSessionIds: ['tip'],
      backgroundBySession: { runtime: [background()] },
      cronJobs: [cron()],
      previewRestart: null,
      sessions: [session()],
      subagentsBySession: { runtime: [subagent({ sessionId: 'child-1' })] },
      workingSessionIds: []
    })

    expect(tasks.map(task => [task.id, task.status, task.durability])).toEqual([
      ['session:tip', 'waiting', 'turn'],
      ['subagent:runtime:worker', 'running', 'process-local'],
      ['process:proc-1', 'running', 'process-local'],
      ['cron:cron-1', 'queued', 'restart-durable']
    ])
  })

  it('preserves subagent hierarchy, child session links, summaries, and artifacts', () => {
    const tasks = buildTaskCenterTasks({
      actionTasks: {},
      attentionSessionIds: [],
      backgroundBySession: {},
      cronJobs: [],
      previewRestart: null,
      sessions: [],
      subagentsBySession: {
        runtime: [
          subagent({
            filesWritten: ['/workspace/report.md'],
            id: 'parent',
            sessionId: 'child-parent',
            status: 'completed',
            summary: 'parent report'
          }),
          subagent({
            id: 'nested',
            parentId: 'parent',
            goal: 'Verify report',
            sessionId: 'child-nested',
            status: 'interrupted',
            summary: 'backend restarted'
          })
        ]
      },
      workingSessionIds: []
    })

    expect(tasks).toEqual([
      expect.objectContaining({
        action: 'open-session',
        depth: 1,
        id: 'subagent:runtime:nested',
        sessionId: 'child-nested',
        status: 'interrupted'
      }),
      expect.objectContaining({
        artifactRefs: ['/workspace/report.md'],
        depth: 0,
        id: 'subagent:runtime:parent',
        sessionId: 'child-parent',
        status: 'success'
      })
    ])
  })

  it('keeps process stop and cron management as owner actions', () => {
    const tasks = buildTaskCenterTasks({
      actionTasks: {},
      attentionSessionIds: [],
      backgroundBySession: {
        runtime: [
          background(),
          background({ id: 'proc-2', state: 'failed', exitCode: 7, title: 'build' })
        ]
      },
      cronJobs: [
        cron({ enabled: false, id: 'paused', state: 'paused' }),
        cron({ id: 'failed', last_error: 'network down', state: 'error' })
      ],
      previewRestart: null,
      sessions: [],
      subagentsBySession: {},
      workingSessionIds: []
    })

    expect(tasks.find(task => task.id === 'process:proc-1')).toMatchObject({
      action: 'stop-process',
      ownerSessionId: 'runtime',
      processId: 'proc-1',
      status: 'running'
    })
    expect(tasks.find(task => task.id === 'process:proc-2')).toMatchObject({
      action: undefined,
      status: 'error'
    })
    expect(tasks.find(task => task.id === 'cron:paused')).toMatchObject({
      action: 'manage-cron',
      durability: 'restart-durable',
      status: 'paused'
    })
    expect(tasks.find(task => task.id === 'cron:failed')).toMatchObject({
      detail: 'network down',
      status: 'error'
    })
  })
})
