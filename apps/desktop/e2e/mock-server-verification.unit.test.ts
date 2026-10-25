import { describe, expect, it } from 'vitest'

import {
  startMockServer,
  VERIFICATION_STOP_TEXT,
  VERIFICATION_STOP_TRIGGER,
} from '../../../tests-js/scripts/mock-server'

interface ChatMessage {
  content?: string
  role: string
  tool_calls?: { function?: { name?: string } }[]
}

async function complete(url: string, messages: ChatMessage[]): Promise<ChatMessage> {
  const response = await fetch(`${url}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'mock-model', stream: false, messages }),
  })
  const body = (await response.json()) as { choices: { message: ChatMessage }[] }

  return body.choices[0].message
}

describe('verify-on-stop mock script', () => {
  it('picks the step from this request, so a previous conversation cannot skip ahead', async () => {
    const first = await startMockServer({ verificationWritePath: '/tmp/e2e-verification-a.py' })
    const trigger: ChatMessage[] = [{ role: 'user', content: VERIFICATION_STOP_TRIGGER }]

    try {
      const opened = await complete(first.url, trigger)
      expect(opened.tool_calls?.[0]?.function?.name).toBe('write_file')

      const finished = await complete(first.url, [
        ...trigger,
        { role: 'assistant', content: 'I will make the requested code change.' },
        { role: 'tool', content: 'wrote' },
      ])
      expect(finished.content).toContain('The code edit is complete.')

      // An auxiliary echo of the same history must not consume the next step.
      const echo = await complete(first.url, trigger)
      expect(echo.tool_calls?.[0]?.function?.name).toBe('write_file')
    } finally {
      await first.close()
    }

    const second = await startMockServer({ verificationWritePath: '/tmp/e2e-verification-b.py' })

    try {
      const reopened = await complete(second.url, trigger)
      expect(reopened.tool_calls?.[0]?.function?.name).toBe('write_file')

      const nudged = await complete(second.url, [
        ...trigger,
        { role: 'assistant', content: 'I will make the requested code change.' },
        { role: 'tool', content: 'wrote' },
        { role: 'assistant', content: 'The code edit is complete.' },
        { role: 'user', content: '[System: You edited code in this turn, but evidence is missing.' },
      ])
      expect(nudged.content).toBe(VERIFICATION_STOP_TEXT)
      expect(
        second.receivedPrompts.some(prompt => prompt.includes('[System: You edited code in this turn')),
      ).toBe(true)
    } finally {
      await second.close()
    }
  })
})
