import { describe, expect, it } from 'vitest'

import {
  isInternalSessionTitle,
  oneLineExcerpt,
  SESSION_TITLE_EXCERPT_CHARS,
  sessionDisplayTitle
} from './session-title'

const UNTITLED = 'Untitled session'

describe('sessionDisplayTitle', () => {
  it('keeps the real title, trimmed, instead of the first message', () => {
    expect(sessionDisplayTitle({ preview: '请帮我整理周报', title: '  周报整理  ' }, UNTITLED)).toBe('周报整理')
  })

  it('falls back to the start of the first message on one line when there is no title', () => {
    const preview = '请帮我把这周的进展、风险和下周计划整理成一份可以直接发给团队的周报，并列出每个负责人的待办\n第二行'
    const shown = sessionDisplayTitle({ preview, title: '' }, UNTITLED)

    expect(shown).not.toContain('\n')
    expect(Array.from(shown).length).toBeLessThanOrEqual(SESSION_TITLE_EXCERPT_CHARS)
    expect(shown.endsWith('…')).toBe(true)
  })

  it('never shows a plumbing title; the first message stands in for it', () => {
    expect(sessionDisplayTitle({ preview: '你好', title: 'Bot Chat' }, UNTITLED)).toBe('你好')
    expect(sessionDisplayTitle({ preview: '', title: 'handoff-20260101' }, UNTITLED)).toBe(UNTITLED)
  })

  it('uses the untitled label only when there is neither a title nor a message', () => {
    expect(sessionDisplayTitle({ preview: null, title: null }, UNTITLED)).toBe(UNTITLED)
    expect(sessionDisplayTitle({ preview: '  \n  ', title: '   ' }, UNTITLED)).toBe(UNTITLED)
  })
})

describe('oneLineExcerpt', () => {
  it('collapses line breaks and runs of whitespace into single spaces', () => {
    expect(oneLineExcerpt('first line\n\n  second\tline ')).toBe('first line second line')
  })

  it('leaves text that already fits untouched', () => {
    expect(oneLineExcerpt('short', 40)).toBe('short')
  })

  it('cuts to the limit with an ellipsis, counting characters rather than bytes', () => {
    const shown = oneLineExcerpt('这是一个很长的中文标题用于测试截断是否按字符计算而不是按字节计算', 10)

    expect(Array.from(shown)).toHaveLength(10)
    expect(shown.endsWith('…')).toBe(true)
  })
})

describe('isInternalSessionTitle', () => {
  it('flags the bot registry title and handoff rows, not names that only look similar', () => {
    expect(isInternalSessionTitle('Bot Chat')).toBe(true)
    expect(isInternalSessionTitle('handoff-20260101')).toBe(true)
    expect(isInternalSessionTitle('Bot Chat notes')).toBe(false)
    expect(isInternalSessionTitle('handoff-notes')).toBe(false)
  })
})
