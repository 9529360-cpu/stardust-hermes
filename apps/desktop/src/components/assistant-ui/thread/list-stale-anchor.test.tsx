import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { act, render } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { stubThreadEnvironment, stubThreadViewportSize } from '../test-utils'

import { Thread } from '.'

stubThreadEnvironment()
stubThreadViewportSize()

const CLIENT_H = 600
let scrollHeightValue = 20000

Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
  configurable: true,
  get: () => scrollHeightValue
})
Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get: () => CLIENT_H
})

beforeEach(() => {
  scrollHeightValue = 20000
  window.localStorage.clear()
})

async function settle(ticks = 20) {
  await act(async () => {
    for (let tick = 0; tick < ticks; tick += 1) {
      await new Promise<void>(resolve => window.setTimeout(resolve, 0))
    }
  })
}

// Heavy enough that the transcript outgrows RENDER_BUDGET and older turns sit
// behind "Show earlier" — the shape of a long session.
const heavyText = 'x'.repeat(5000)
const createdAt = new Date('2026-08-01T00:00:00.000Z')

function heavyTranscript(turns: number): ThreadMessage[] {
  return Array.from({ length: turns }, (_, index) => [
    {
      id: `u-${index}`,
      role: 'user',
      content: [{ type: 'text', text: heavyText }],
      attachments: [],
      createdAt,
      metadata: { custom: {} }
    } as ThreadMessage,
    {
      id: `a-${index}`,
      role: 'assistant',
      content: [{ type: 'text', text: heavyText }],
      status: { type: 'complete', reason: 'stop' },
      createdAt,
      metadata: { unstable_state: null, unstable_annotations: [], unstable_data: [], steps: [], custom: {} }
    } as ThreadMessage
  ]).flat()
}

function Harness({ messages }: { messages: ThreadMessage[] }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({ isRunning: false, messages, onNew: async () => {} })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread sessionKey="long" />
    </AssistantRuntimeProvider>
  )
}

const mountedGroups = (container: HTMLElement) => container.querySelectorAll('[data-slot="aui_message-group"]').length

async function settledGroupCount(container: HTMLElement): Promise<number> {
  let count = mountedGroups(container)

  for (let round = 0; round < 20; round += 1) {
    await settle(10)
    const next = mountedGroups(container)

    if (next === count) {
      return count
    }

    count = next
  }

  return count
}

describe('prepend anchor lifetime', () => {
  it('does not replay an anchor the commit it was taken for could not honor', async () => {
    const turns = 60
    const { container, rerender } = render(<Harness messages={heavyTranscript(turns)} />)
    const viewport = container.querySelector('[data-slot="aui_thread-viewport"]') as HTMLElement
    const windowed = await settledGroupCount(container)

    expect(windowed).toBeGreaterThan(0)
    expect(windowed).toBeLessThan(turns)

    // Leave the bottom lock the way a reader does, then reach the clamped top.
    viewport.scrollTop = scrollHeightValue - CLIENT_H
    act(() => viewport.dispatchEvent(new Event('scroll')))
    viewport.scrollTop = 0
    act(() => viewport.dispatchEvent(new Event('scroll')))
    await settle(5)

    // The wheel at the top pages older turns in, which anchors the view at the
    // 20000px the transcript measured at that moment. Before that page's
    // commit lands the rows re-measure shorter (intrinsic sizes resolving), so
    // the commit finds a transcript that is no longer as tall as the anchor.
    // Registered after the thread's own listener, so it runs between the
    // anchor and the commit.
    viewport.addEventListener(
      'wheel',
      () => {
        scrollHeightValue = 12000
      },
      { once: true }
    )
    act(() => viewport.dispatchEvent(new WheelEvent('wheel', { deltaY: -120 })))
    await settle(5)

    const paged = mountedGroups(container)

    expect(paged).toBeGreaterThan(windowed)
    expect(viewport.scrollTop).toBe(0)

    // Much later a new turn starts and the transcript is taller than that old
    // anchor. The anchor belonged to the paging commit; this commit must not
    // pull the view to `scrollHeight - anchor`.
    scrollHeightValue = 30000
    rerender(<Harness messages={[...heavyTranscript(turns), ...heavyTranscript(turns + 1).slice(-2)]} />)
    await settle(5)

    expect(viewport.scrollTop).toBe(0)
  })

  it('keeps paging older turns after an anchor that could not be honored', async () => {
    const turns = 60
    const { container } = render(<Harness messages={heavyTranscript(turns)} />)
    const viewport = container.querySelector('[data-slot="aui_thread-viewport"]') as HTMLElement
    const windowed = await settledGroupCount(container)

    viewport.scrollTop = scrollHeightValue - CLIENT_H
    act(() => viewport.dispatchEvent(new Event('scroll')))
    viewport.scrollTop = 0
    act(() => viewport.dispatchEvent(new Event('scroll')))
    await settle(5)

    viewport.addEventListener(
      'wheel',
      () => {
        scrollHeightValue = 12000
      },
      { once: true }
    )
    act(() => viewport.dispatchEvent(new WheelEvent('wheel', { deltaY: -120 })))
    await settle(5)

    const paged = mountedGroups(container)

    expect(paged).toBeGreaterThan(windowed)

    // The reader is back at the top and wheels up again. A leftover anchor
    // reads as "a restore is still pending" and swallows the page.
    viewport.scrollTop = 0
    act(() => viewport.dispatchEvent(new Event('scroll')))
    await settle(5)
    act(() => viewport.dispatchEvent(new WheelEvent('wheel', { deltaY: -120 })))
    await settle()

    expect(mountedGroups(container)).toBeGreaterThan(paged)
  })
})
