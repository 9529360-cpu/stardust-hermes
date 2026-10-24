import { describe, expect, it, vi } from 'vitest'

import { activePromptIndex, countMessagesBelow, firstAfter } from './thread-scroll-geometry'
import { activeTimelineIndex } from './timeline-data'

it('keeps timeline selection identical across sticky prompt boundaries', () => {
  const viewport = document.createElement('div')
  vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue(box(0, 600))
  const ids = Array.from({ length: 200 }, (_, index) => `prompt-${index}`)
  const offsets: number[] = []

  ids.forEach((id, index) => {
    const node = document.createElement('div')
    node.dataset.messageId = id
    node.dataset.slot = 'aui_user-message-root'
    viewport.append(node)
    vi.spyOn(node, 'getBoundingClientRect').mockImplementation(() => box(offsets[index]!, offsets[index]! + 40))
  })

  for (const scrollTop of [0, 8, 100, 10000, 19900, 25000]) {
    offsets.splice(0, offsets.length, ...ids.map((_, index) => Math.max(-40, index * 100 - scrollTop)))
    expect(activePromptIndex(viewport, ids)).toBe(activeTimelineIndex(offsets))
  }
})

it('uses the first mounted entry while visibility delivery is pending', () => {
  const viewport = document.createElement('div')
  vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue(box(0, 600))
  const node = document.createElement('div')
  node.dataset.slot = 'aui_user-message-root'
  node.dataset.messageId = 'second'
  viewport.append(node)
  expect(activePromptIndex(viewport, ['first', 'second'], new Set())).toBe(1)
})

function box(top: number, bottom: number): DOMRect {
  return { top, bottom, height: bottom - top } as DOMRect
}

it('locates a fold in logarithmic layout reads and counts newly appended messages', () => {
  const viewport = document.createElement('div')
  const content = document.createElement('div')
  vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue(box(0, 600))
  const reads = vi.fn((index: number) => box(index * 100, (index + 1) * 100))

  for (let index = 0; index < 400; index++) {
    const group = document.createElement('div')
    group.dataset.slot = 'aui_message-group'
    vi.spyOn(group, 'getBoundingClientRect').mockImplementation(() => reads(index))
    const message = document.createElement('div')
    message.dataset.slot = 'aui_assistant-message-root'
    group.append(message)
    content.append(group)
  }

  expect(countMessagesBelow(viewport, content)).toBe(394)
  const visible = new Set([content.children[5] as HTMLElement, content.children[6] as HTMLElement])
  reads.mockClear()
  expect(countMessagesBelow(viewport, content, visible)).toBe(394)
  expect(reads.mock.calls.length).toBeLessThanOrEqual(3)
  const appended = document.createElement('div')
  appended.dataset.slot = 'aui_assistant-message-root'
  content.lastElementChild!.append(appended)
  expect(countMessagesBelow(viewport, content)).toBe(395)
})

describe('ordered fold search', () => {
  it('handles empty, before-first, after-last and exact boundaries', () => {
    expect(firstAfter(0, () => true)).toBe(0)
    expect(firstAfter(400, () => true)).toBe(0)
    expect(firstAfter(400, () => false)).toBe(400)
    expect(firstAfter(400, index => index > 199)).toBe(200)
  })
})
