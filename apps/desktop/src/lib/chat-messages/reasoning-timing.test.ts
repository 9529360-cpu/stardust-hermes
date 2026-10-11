import { describe, expect, it } from 'vitest'

import { reasoningSeconds } from '@/components/chat/activity-timer'
import type { SessionMessage } from '@/types/hermes'

import { toChatMessages } from './hydration'
import { appendAssistantTextPart, appendReasoningPart } from './parts'
import { upsertToolPart } from './tool-parts'
import type { ChatMessagePart } from './types'

// Both ends of the live block are backend stamps: its first reasoning delta and its first answer delta.
const STARTED = 1000.25
const ANSWERED = 1012.5

const reasoningOf = (parts: ChatMessagePart[]) => parts.find(part => part.type === 'reasoning')

function reloadedReasoning(display_metadata: SessionMessage['display_metadata']) {
  const [message] = toChatMessages([
    { role: 'assistant', content: 'answer', reasoning: 'thinking', timestamp: ANSWERED, display_metadata }
  ])

  return reasoningOf(message?.parts ?? [])
}

describe('reasoning duration across a reload', () => {
  it('a reloaded block reports the same span the live stream showed', () => {
    const liveBlock = reasoningOf(appendAssistantTextPart(appendReasoningPart([], 'thinking', STARTED), 'answer', ANSWERED))
    const stored = reloadedReasoning({ reasoning_timing: { started_at: STARTED, completed_at: ANSWERED } })

    expect(reasoningSeconds(liveBlock?.timestamp, liveBlock?.completedAt)).toBe(12.25)
    expect(reasoningSeconds(stored?.timestamp, stored?.completedAt)).toBe(12.25)
  })

  it('a tool call with no answer text in between closes the block at the stamp its tool event carries', () => {
    const live = upsertToolPart(
      appendReasoningPart([], 'planning', STARTED),
      { name: 'read_file', tool_id: 'call-1' },
      'running',
      ANSWERED
    )

    const liveBlock = reasoningOf(live)
    const stored = reloadedReasoning({ reasoning_timing: { started_at: STARTED, completed_at: ANSWERED } })

    expect(reasoningSeconds(liveBlock?.timestamp, liveBlock?.completedAt)).toBe(12.25)
    expect(reasoningSeconds(stored?.timestamp, stored?.completedAt)).toBe(12.25)
  })

  it('a row with no stored span reads as finished, with no invented duration', () => {
    const stored = reloadedReasoning(undefined)

    expect(stored).toBeDefined()
    expect(reasoningSeconds(stored?.timestamp, stored?.completedAt)).toBeNull()
  })

  it('reads the span from display metadata an older remote backend serves as raw JSON text', () => {
    const stored = reloadedReasoning(JSON.stringify({ reasoning_timing: { started_at: STARTED, completed_at: ANSWERED } }))

    expect(reasoningSeconds(stored?.timestamp, stored?.completedAt)).toBe(12.25)
  })

  it('ignores a span that ends before it starts instead of reporting a negative duration', () => {
    const stored = reloadedReasoning({ reasoning_timing: { started_at: ANSWERED, completed_at: STARTED } })

    expect(stored?.completedAt).toBeUndefined()
  })
})
