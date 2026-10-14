import { describe, expect, it } from 'vitest'

import {
  appendLiveSessionProjection,
  preserveLocalPendingTurnMessages,
  reconcileResumeMessages
} from '@/app/session/hooks/use-session-actions/utils'

import { toChatMessages } from './hydration'
import { chatMessageText, textPart } from './parts'
import type { ChatMessage } from './types'

describe('steer transcript hydration', () => {
  it("shows the user's plain correction when resume returns its model-facing marker", () => {
    const correction = 'Use the corrected path and continue.'
    const [message] = toChatMessages([
      {
        role: 'user',
        display_kind: 'steer',
        content: `[OUT-OF-BAND USER MESSAGE — a direct message from the user]\n${correction}\n[/OUT-OF-BAND USER MESSAGE]`
      }
    ])

    expect(message?.role).toBe('user')
    expect(chatMessageText(message!)).toBe(correction)
  })

  it('keeps already-projected steer content unchanged', () => {
    const correction = 'Use the corrected path and continue.'
    const [message] = toChatMessages([{ role: 'user', display_kind: 'steer', content: correction }])

    expect(chatMessageText(message!)).toBe(correction)
  })

  it('does not append a second correction when resume also returns the live correction projection', () => {
    const correction = 'Use the corrected path and continue.'
    const history = toChatMessages([
      {
        role: 'user',
        display_kind: 'steer',
        content: `[OUT-OF-BAND USER MESSAGE — a direct message from the user]\n${correction}\n[/OUT-OF-BAND USER MESSAGE]`
      }
    ])
    const optimistic: ChatMessage = {
      id: 'user-optimistic-correction',
      role: 'user',
      parts: [textPart(correction)]
    }

    const projected = appendLiveSessionProjection(history, {
      session_id: 'session-1',
      inflight: { corrections: [correction] }
    })
    const reconciled = preserveLocalPendingTurnMessages(
      reconcileResumeMessages(projected, [optimistic]),
      [optimistic]
    )

    expect(reconciled.filter(message => message.role === 'user').map(chatMessageText)).toEqual([correction])
  })
})
