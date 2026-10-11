import { afterEach, describe, expect, it } from 'vitest'

import { setRuntimeI18nLocale } from '@/i18n'

import { summarizeToolRun, type ToolCallLike, toolPresentVerb } from './run-summary'

function tool(toolName: string, args: Record<string, unknown> = {}, result?: unknown): ToolCallLike {
  return { args, result, toolCallId: `${toolName}-${Math.random()}`, toolName }
}

const read = (path: string) => tool('read_file', { path }, { content: '' })
const searched = (query: string) => tool('search_files', { query }, { hits: [] })
const ran = (command: string) => tool('terminal', { command }, { exit_code: 0 })

const settled = (tools: ToolCallLike[]) => summarizeToolRun(tools, false)
const running = (tools: ToolCallLike[]) => summarizeToolRun(tools, true)

// A run only ever holds ephemeral activity: reads, searches, commands. File
// edits and other cards are split out before a run is summarized, so there is
// no "Edited …" clause to test here — that work shows as its own diff card.
describe('summarizeToolRun', () => {
  it('names a lone target and counts the rest', () => {
    expect(settled([searched('toolRuns'), read('a.ts'), read('b.ts'), read('c.ts')])).toBe('Explored 4 files')
  })

  it('orders clauses explore then run regardless of call order', () => {
    expect(settled([ran('ls'), read('a.ts'), read('b.ts'), ran('pwd'), ran('id')])).toBe(
      'Explored 2 files, ran 3 commands'
    )
  })

  it('counts commands rather than naming them once they have run', () => {
    expect(settled([ran('git status')])).toBe('Ran 1 command')
    expect(settled([read('status.ts'), ran('a'), ran('b'), ran('c'), ran('d'), ran('e')])).toBe(
      'Explored status.ts, ran 5 commands'
    )
  })

  it('puts the running category in the present tense and leaves the rest past', () => {
    expect(running([read('a.ts'), tool('read_file', { path: 'b.ts' }), ran('x'), ran('y')])).toBe(
      'Exploring 2 files, ran 2 commands'
    )
  })

  it('names the command that is still running', () => {
    expect(running([tool('terminal', { command: 'npm run typecheck' })])).toMatch(/^Running /)
  })

  // Sequential calls leave a gap where the run is still going but nothing is
  // pending. Falling back to past tense there contradicted the ticker still
  // scrolling underneath, so the most recent call carries the present tense.
  it('stays in the present tense between two sequential calls', () => {
    expect(running([read('a.ts'), ran('x'), ran('y')])).toBe('Explored a.ts, running 2 commands')
  })

  // A turn can end — or the agent can simply move on — with a call that never
  // got a result. The run is history at that point and has to read as history,
  // or it narrates work that stopped happening and never offers its toggle.
  it('reads a run the turn left unresolved as finished', () => {
    expect(settled([read('a.ts'), tool('search_files', { query: 'toolRuns' })])).toBe('Explored 2 files')
  })
})

describe('summarizeToolRun copy by category', () => {
  it('keeps the English copy for the categories no other test reads', () => {
    expect(settled([tool('delegate_task'), tool('delegate_task')])).toBe('Delegated 2 tasks')
    expect(settled([tool('write_file'), tool('patch')])).toBe('Edited 2 files')
    expect(settled([tool('todo'), tool('todo')])).toBe('Used 2 tools')
    expect(running([tool('todo')])).toBe('Using 1 tool')
  })
})

// The header also renders on zh screens, so its copy must come from the active
// locale. Each block sets its language and puts English back afterwards.
describe('summarizeToolRun in zh', () => {
  afterEach(() => setRuntimeI18nLocale('en'))

  it('keeps English words out of a zh run summary', () => {
    setRuntimeI18nLocale('zh')

    const summaries = [
      settled([read('a.ts'), read('b.ts'), ran('ls'), ran('pwd')]),
      running([read('a.ts'), read('b.ts'), ran('x'), ran('y')]),
      settled([tool('delegate_task'), tool('delegate_task')]),
      settled([tool('write_file'), tool('patch')]),
      settled([tool('todo'), { ...tool('todo'), isError: true }]),
      running([tool('todo'), tool('todo')])
    ]

    for (const summary of summaries) {
      expect(summary).not.toMatch(/[A-Za-z]/)
    }
  })

  it('reads a settled mixed run in zh', () => {
    setRuntimeI18nLocale('zh')

    expect(settled([read('a.ts'), read('b.ts'), ran('ls'), ran('pwd')])).toBe('已探索 2 个文件，已运行 2 个命令')
  })

  it('reads the present-tense verb for the status line in zh', () => {
    setRuntimeI18nLocale('zh')

    expect(toolPresentVerb('read_file')).toBe('正在探索')
  })
})
