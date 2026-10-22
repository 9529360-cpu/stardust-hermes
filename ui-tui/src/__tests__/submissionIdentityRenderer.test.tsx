import { PassThrough } from 'node:stream'

import { renderSync, Text } from '@hermes/ink'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { submitPrompt } from '../app/submissionCore.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'
import { useSubmission, type UseSubmissionOptions } from '../app/useSubmission.js'

vi.mock('../app/submissionCore.js', () => ({ submitPrompt: vi.fn() }))

afterEach(() => {
  resetUiState()
  vi.clearAllMocks()
})

describe('queued submission identity', () => {
  it('keeps an earlier attempt identity when double Enter sends the pending item', () => {
    patchUiState({ busy: false, sid: 'sid' })

    let submission!: ReturnType<typeof useSubmission>
    const item = { clientMessageId: 'earlier-attempt', display: 'pending', text: 'pending' }

    const opts = {
      appendMessage: vi.fn(),
      composerActions: {
        clearIn: vi.fn(),
        dequeue: vi.fn(() => item),
        enqueue: vi.fn(),
        pushHistory: vi.fn(),
        setQueueEdit: vi.fn()
      },
      composerRefs: {
        queueEditRef: { current: null },
        queueRef: { current: [item] },
        tokensRef: { current: [] }
      },
      composerState: { completions: [], input: '', inputBuf: [] },
      gw: { request: vi.fn() },
      setLastUserMsg: vi.fn(),
      slashRef: { current: vi.fn() },
      submitRef: { current: vi.fn() },
      sys: vi.fn()
    } as unknown as UseSubmissionOptions

    function Harness() {
      submission = useSubmission(opts)

      return <Text>ready</Text>
    }

    const stdout = new PassThrough()
    Object.assign(stdout, { columns: 80, isTTY: false, rows: 20 })
    stdout.resume()

    const instance = renderSync(<Harness />, {
      patchConsole: false,
      stdin: new PassThrough() as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream,
      stderr: new PassThrough() as unknown as NodeJS.WriteStream
    })

    try {
      submission.submit('')
      submission.submit('')
      expect(submitPrompt).toHaveBeenCalledTimes(1)
      expect(vi.mocked(submitPrompt).mock.calls[0]?.[4]).toEqual({ clientMessageId: 'earlier-attempt' })
    } finally {
      instance.unmount()
    }
  })
})
