import { describe, expect, it } from 'vitest'

import type { ClarifyRequest } from '@/store/clarify'
import type { ComposerStatusItem } from '@/store/composer-status'
import type { ApprovalRequest, SecretRequest, SudoRequest, VaultUnlockRequest } from '@/store/prompts'
import type { SubagentProgress } from '@/store/subagents'

import {
  ATTENTION_WINDOW_MS,
  buildSessionTaskRows,
  groupTaskRows,
  KNOWN_TASK_STATES,
  LINGER_MS,
  RECENT_LIMIT,
  RECENT_WINDOW_MS,
  type SessionTaskSources,
  stateMeta,
  summarizeTaskGroups,
  TASK_GROUPS,
  type TaskGroupId,
  type TrackedTaskRow
} from './task-model'

const NOW = 1_800_000_000_000

const emptySources = (): SessionTaskSources => ({
  approvals: {},
  background: {},
  clarifies: {},
  secrets: {},
  subagents: {},
  sudos: {},
  vaultCodes: {},
  vaultSaves: {},
  vaultUnlocks: {}
})

const resolve = (runtimeId: null | string) =>
  runtimeId === 'runtime-a' ? { sessionTitle: 'Plan the launch', storedSessionId: 'stored-a' } : {}

const subagent = (patch: Partial<SubagentProgress> & Pick<SubagentProgress, 'id'>): SubagentProgress => ({
  filesRead: [],
  filesWritten: [],
  goal: patch.id,
  parentId: null,
  startedAt: NOW - 120_000,
  status: 'running',
  stream: [],
  taskCount: 1,
  taskIndex: 0,
  updatedAt: NOW - 1_000,
  ...patch
})

const tracked = (patch: Partial<TrackedTaskRow> & Pick<TrackedTaskRow, 'id'>): TrackedTaskRow => ({
  observedAt: NOW - 1_000,
  phase: { kind: 'none' },
  rail: 'process',
  seq: 1,
  state: 'running',
  title: patch.id,
  ...patch
})

const idsOf = (groups: ReturnType<typeof groupTaskRows>, id: TaskGroupId) =>
  groups.find(group => group.id === id)?.rows.map(row => row.id) ?? []

describe('task model tables', () => {
  it('maps every known state to a rendered group and a position family', () => {
    for (const state of KNOWN_TASK_STATES) {
      const meta = stateMeta(state)
      const group = TASK_GROUPS.find(candidate => candidate.id === meta.group)

      expect(group, state).toBeDefined()
      expect(group?.reserved, state).toBeFalsy()
      expect(meta.known, state).toBe(true)
    }
  })

  it('keeps unknown states separate from running and upcoming', () => {
    const meta = stateMeta('needs_review')

    expect(meta.known).toBe(false)
    expect(meta.group).toBe('other')
    expect(meta.terminal).toBe(false)
    const groups = groupTaskRows([tracked({ id: 'unknown', state: 'needs_review' })], NOW)
    expect(idsOf(groups, 'other')).toEqual(['unknown'])
    expect(idsOf(groups, 'running')).toEqual([])
    expect(summarizeTaskGroups(groups)).toEqual({ attention: 0, running: 0 })
  })

  it('keeps the reserved agents slot between attention and running, and never fills it', () => {
    expect(TASK_GROUPS.map(group => group.id)).toEqual([
      'attention',
      'agents',
      'running',
      'upcoming',
      'other',
      'recent'
    ])
    expect(TASK_GROUPS.find(group => group.id === 'agents')?.reserved).toBe(true)

    const everyState = [...KNOWN_TASK_STATES, 'something-new'].map((state, index) =>
      tracked({ finishedAt: NOW - 2 * LINGER_MS, id: `row-${index}`, seq: index, state })
    )

    expect(groupTaskRows(everyState, NOW).some(group => group.id === 'agents')).toBe(false)
  })
})

describe('prompt rows', () => {
  it('turns an approval into a waiting row that shows the description line and never the command', () => {
    const approval: ApprovalRequest = {
      command: 'rm -rf /srv/data --no-preserve-root',
      description: 'Delete the data folder\nsecond line',
      requestId: 'req-1',
      sessionId: 'runtime-a'
    }

    const sources = { ...emptySources(), approvals: { 'runtime-a': approval } }
    const [row] = buildSessionTaskRows(sources, resolve)

    expect(row).toMatchObject({
      id: 'approval:runtime-a:req-1',
      phase: { kind: 'text', text: 'Delete the data folder' },
      rail: 'approval',
      sessionId: 'runtime-a',
      sessionTitle: 'Plan the launch',
      state: 'waiting_for_user',
      storedSessionId: 'stored-a'
    })
    expect(JSON.stringify(row)).not.toContain('rm -rf')
  })

  it('names the environment variable for a secret request and carries neither the prompt text nor a value', () => {
    const secret: SecretRequest = {
      envVar: 'OPENAI_API_KEY',
      prompt: 'Paste your key sk-live-1234',
      requestId: 'req-2',
      sessionId: 'runtime-a'
    }

    const [row] = buildSessionTaskRows({ ...emptySources(), secrets: { 'runtime-a': secret } }, resolve)

    expect(row?.phase).toEqual({ kind: 'needs-secret', name: 'OPENAI_API_KEY' })
    expect(JSON.stringify(row)).not.toContain('sk-live')
  })

  it('keeps one row per blocking request when a conversation is blocked on several at once', () => {
    const sudo: SudoRequest = { command: 'apt update', requestId: 'req-3', sessionId: 'runtime-a' }
    const vault: VaultUnlockRequest = {
      backend: 'op',
      displayName: '1Password',
      requestId: 'req-4',
      sessionId: 'runtime-a'
    }

    const clarify: ClarifyRequest = {
      choices: ['Yes', 'No'],
      multiSelect: false,
      question: 'Keep going?\nplease answer',
      requestId: 'req-5',
      sessionId: 'runtime-a'
    }

    const rows = buildSessionTaskRows(
      {
        ...emptySources(),
        clarifies: { 'runtime-a': clarify },
        sudos: { 'runtime-a': sudo },
        vaultUnlocks: { 'runtime-a': vault }
      },
      resolve
    )

    expect(rows.map(row => row.rail).sort()).toEqual(['clarify', 'sudo', 'vault-unlock'])
    expect(new Set(rows.map(row => row.id)).size).toBe(3)
    expect(rows.find(row => row.rail === 'vault-unlock')?.phase).toEqual({ kind: 'needs-vault', name: '1Password' })
    expect(rows.find(row => row.rail === 'clarify')?.phase).toEqual({ kind: 'text', text: 'Keep going?' })
    expect(JSON.stringify(rows)).not.toContain('apt update')
  })

  it('reads the first batch question when a clarify request has no top-level question', () => {
    const clarify: ClarifyRequest = {
      choices: null,
      multiSelect: false,
      question: '',
      questions: [{ choices: null, multiSelect: false, qid: 'q0', question: 'Pick a drink?' }],
      requestId: 'req-6',
      sessionId: 'runtime-a'
    }

    const [row] = buildSessionTaskRows({ ...emptySources(), clarifies: { 'runtime-a': clarify } }, resolve)

    expect(row?.phase).toEqual({ kind: 'text', text: 'Pick a drink?' })
  })

  it('leaves the stored conversation unset when the runtime session cannot be resolved', () => {
    const approval: ApprovalRequest = {
      command: 'ls',
      description: 'List files',
      requestId: 'req-7',
      sessionId: 'unknown'
    }
    const [row] = buildSessionTaskRows({ ...emptySources(), approvals: { unknown: approval } }, resolve)

    expect(row?.storedSessionId).toBeUndefined()
    expect(row?.sessionTitle).toBeUndefined()
  })
})

describe('background command rows', () => {
  const item = (patch: Partial<ComposerStatusItem> & Pick<ComposerStatusItem, 'id'>): ComposerStatusItem => ({
    state: 'running',
    title: 'npm run dev',
    type: 'background',
    ...patch
  })

  it('maps running, finished and failed commands and links the terminal by process id', () => {
    const rows = buildSessionTaskRows(
      {
        ...emptySources(),
        background: {
          'runtime-a': [
            item({ id: 'proc_1' }),
            item({ id: 'proc_2', state: 'done', title: 'npm test' }),
            item({ exitCode: 3, id: 'proc_3', state: 'failed', title: 'npm run build' })
          ]
        }
      },
      resolve
    )

    expect(rows.map(row => [row.id, row.state])).toEqual([
      ['process:proc_1', 'running'],
      ['process:proc_2', 'completed'],
      ['process:proc_3', 'failed']
    ])

    expect(rows[2]).toMatchObject({
      durability: 'process-local',
      phase: { code: 3, kind: 'exit-code' },
      rail: 'process',
      terminalProcId: 'proc_3',
      title: 'npm run build'
    })
    expect(rows[0]?.phase).toEqual({ kind: 'running' })
  })

  it('ignores items that are not background commands', () => {
    const rows = buildSessionTaskRows(
      { ...emptySources(), background: { 'runtime-a': [item({ id: 'todo:1', type: 'todo' })] } },
      resolve
    )

    expect(rows).toEqual([])
  })
})

describe('delegation rows', () => {
  it('groups root subagents that share a delegation id into one row with nested children', () => {
    const rows = buildSessionTaskRows(
      {
        ...emptySources(),
        subagents: {
          'runtime-a': [
            subagent({ currentTool: 'terminal', delegationId: 'd1', goal: 'Collect results', id: 's1', taskIndex: 0 }),
            subagent({ delegationId: 'd1', goal: 'Write summary', id: 's2', taskIndex: 1 }),
            subagent({ delegationId: 'd1', goal: 'Check links', id: 's3', parentId: 's1' }),
            subagent({ delegationId: 'd2', goal: 'Other batch', id: 's4' })
          ]
        }
      },
      resolve
    )

    expect(rows.map(row => row.id)).toEqual(['delegation:d1', 'delegation:d2'])
    expect(rows[0]).toMatchObject({
      durability: 'process-local',
      phase: { kind: 'working-on', tool: 'terminal' },
      rail: 'delegation',
      state: 'running',
      title: 'Collect results'
    })
    expect(rows[0]?.children?.map(child => [child.title, child.depth])).toEqual([
      ['Collect results', 0],
      ['Check links', 1],
      ['Write summary', 0]
    ])
  })

  it('gives subagents with no delegation id a row each instead of merging them', () => {
    const rows = buildSessionTaskRows(
      {
        ...emptySources(),
        subagents: { 'runtime-a': [subagent({ id: 'old-1' }), subagent({ id: 'old-2', startedAt: NOW })] }
      },
      resolve
    )

    expect(rows.map(row => row.id)).toEqual(['subagent:runtime-a:old-1', 'subagent:runtime-a:old-2'])
  })

  it('reports the group as running while any member runs, then failed before cancelled before completed', () => {
    const state = (statuses: SubagentProgress['status'][]) =>
      buildSessionTaskRows(
        {
          ...emptySources(),
          subagents: {
            'runtime-a': statuses.map((status, index) =>
              subagent({ delegationId: 'd', id: `s${index}`, status, taskIndex: index })
            )
          }
        },
        resolve
      )[0]?.state

    expect(state(['completed', 'running', 'failed'])).toBe('running')
    expect(state(['queued', 'completed'])).toBe('queued')
    expect(state(['completed', 'interrupted', 'failed'])).toBe('failed')
    expect(state(['completed', 'interrupted'])).toBe('cancelled')
    expect(state(['completed', 'completed'])).toBe('completed')
  })

  it('shows the elapsed phase when no tool is active and the failure line when it failed', () => {
    const running = buildSessionTaskRows(
      {
        ...emptySources(),
        subagents: { 'runtime-a': [subagent({ delegationId: 'd', id: 's1', startedAt: NOW - 90_000 })] }
      },
      resolve
    )[0]

    expect(running?.phase).toEqual({ kind: 'running' })
    expect(running?.startedAt).toBe(NOW - 90_000)

    const failed = buildSessionTaskRows(
      {
        ...emptySources(),
        subagents: {
          'runtime-a': [
            subagent({ delegationId: 'd', id: 's1', status: 'failed', summary: 'Timed out after 612.3s\ndetails' })
          ]
        }
      },
      resolve
    )[0]

    expect(failed?.phase).toEqual({ kind: 'text', text: 'Timed out after 612.3s' })
  })

  it('collects written files once, capped at eight', () => {
    const files = Array.from({ length: 12 }, (_, index) => `/work/file-${index}.md`)

    const row = buildSessionTaskRows(
      {
        ...emptySources(),
        subagents: {
          'runtime-a': [
            subagent({ delegationId: 'd', filesWritten: files.slice(0, 8), id: 's1' }),
            subagent({ delegationId: 'd', filesWritten: files, id: 's2' })
          ]
        }
      },
      resolve
    )[0]

    expect(row?.files).toHaveLength(8)
    expect(new Set(row?.files).size).toBe(8)
  })
})

describe('grouping', () => {
  it('puts failures and waiting rows in attention at once, running work in running, upcoming apart', () => {
    const groups = groupTaskRows(
      [
        tracked({ id: 'a', seq: 3, state: 'waiting_for_user' }),
        tracked({ id: 'b', seq: 1, state: 'running' }),
        tracked({ finishedAt: NOW - 1, id: 'c', seq: 2, state: 'failed' }),
        tracked({ id: 'd', seq: 4, state: 'paused' }),
        tracked({ id: 'e', seq: 5, state: 'blocked' })
      ],
      NOW
    )

    expect(groups.map(group => group.id)).toEqual(['attention', 'running', 'upcoming'])
    expect(idsOf(groups, 'attention')).toEqual(['c', 'a', 'e'])
    expect(idsOf(groups, 'running')).toEqual(['b'])
    expect(idsOf(groups, 'upcoming')).toEqual(['d'])
  })

  it('keeps a completed row in place for the linger window and then files it under recent', () => {
    const row = tracked({ finishedAt: NOW - (LINGER_MS - 1_000), id: 'done', state: 'completed' })

    expect(idsOf(groupTaskRows([row], NOW), 'running')).toEqual(['done'])
    expect(idsOf(groupTaskRows([row], NOW + 2_000), 'running')).toEqual([])
    expect(idsOf(groupTaskRows([row], NOW + 2_000), 'recent')).toEqual(['done'])
  })

  it('does not move a pinned row out of the group it was showing when the linger ends', () => {
    const row = tracked({ finishedAt: NOW - 2 * LINGER_MS, id: 'done', state: 'completed' })
    const previous = new Map<string, TaskGroupId>([['done', 'running']])
    const groups = groupTaskRows([row], NOW, { pinned: new Set(['done']), previous })

    expect(idsOf(groups, 'running')).toEqual(['done'])
    expect(idsOf(groupTaskRows([row], NOW, { previous }), 'recent')).toEqual(['done'])
  })

  it('still sends a pinned row to attention when it fails', () => {
    const row = tracked({ finishedAt: NOW, id: 'x', state: 'failed' })
    const previous = new Map<string, TaskGroupId>([['x', 'running']])

    expect(idsOf(groupTaskRows([row], NOW, { pinned: new Set(['x']), previous }), 'attention')).toEqual(['x'])
  })

  it('orders recent newest first, limits it, and reports how many more an expansion reveals', () => {
    const rows = Array.from({ length: RECENT_LIMIT + 3 }, (_, index) =>
      tracked({ finishedAt: NOW - 2 * LINGER_MS - index * 1_000, id: `r${index}`, seq: index, state: 'completed' })
    )

    const [collapsed] = groupTaskRows(rows, NOW)

    expect(collapsed?.id).toBe('recent')
    expect(collapsed?.rows.map(row => row.id)).toEqual(rows.slice(0, RECENT_LIMIT).map(row => row.id))
    expect(collapsed?.moreCount).toBe(3)

    const [expanded] = groupTaskRows(rows, NOW, { expandRecent: true })

    expect(expanded?.rows).toHaveLength(RECENT_LIMIT + 3)
    expect(expanded?.moreCount).toBe(0)
  })

  it('drops a completed row older than the recent window unless the list is expanded', () => {
    const old = tracked({ finishedAt: NOW - RECENT_WINDOW_MS - 1_000, id: 'old', state: 'completed' })

    expect(groupTaskRows([old], NOW)).toEqual([])
    expect(idsOf(groupTaskRows([old], NOW, { expandRecent: true }), 'recent')).toEqual(['old'])
  })

  it('drops a failure that has been unattended past the attention window', () => {
    const stale = tracked({ finishedAt: NOW - ATTENTION_WINDOW_MS - 1_000, id: 'stale', state: 'failed' })

    expect(groupTaskRows([stale], NOW)).toEqual([])
  })

  it('shows an unknown state separately instead of hiding it or inventing running work', () => {
    const groups = groupTaskRows([tracked({ id: 'odd', state: 'needs_review' })], NOW)

    expect(idsOf(groups, 'other')).toEqual(['odd'])
    expect(summarizeTaskGroups(groups).running).toBe(0)
  })
})

describe('summary', () => {
  it('counts running work and attention rows, not lingering completions', () => {
    const groups = groupTaskRows(
      [
        tracked({ id: 'a', seq: 1, state: 'running' }),
        tracked({ id: 'b', seq: 2, state: 'running' }),
        tracked({ finishedAt: NOW, id: 'c', seq: 3, state: 'completed' }),
        tracked({ id: 'd', seq: 4, state: 'waiting_for_user' })
      ],
      NOW
    )

    expect(summarizeTaskGroups(groups)).toEqual({ attention: 1, running: 2 })
  })
})
