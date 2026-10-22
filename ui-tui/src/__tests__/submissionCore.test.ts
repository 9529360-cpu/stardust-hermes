import { JsonRpcGatewayError } from '@stardust/shared/json-rpc-channel'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { isSessionBusyError, markSubmitting, submitPrompt, type SubmitPromptDeps } from '../app/submissionCore.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import type { GatewayClient } from '../gatewayClient.js'

// A gateway double whose `input.detect_drop` resolution we control, so we can
// observe UI state DURING the async gap — the exact window the queue-mode race
// lived in.
function makeDeferredGateway() {
  let resolveDrop: (v: unknown) => void = () => {}

  const dropPromise = new Promise(res => {
    resolveDrop = res
  })

  const calls: string[] = []

  const gw = {
    request: vi.fn((method: string) => {
      calls.push(method)

      if (method === 'input.detect_drop') {
        return dropPromise
      }

      // prompt.submit et al: resolve immediately with a success shape.
      return Promise.resolve({ status: 'streaming' })
    })
  } as unknown as GatewayClient

  return { calls, gw, resolveDrop: (v: unknown = { matched: false }) => resolveDrop(v) }
}

function makeDeps(gw: GatewayClient, over: Partial<SubmitPromptDeps> = {}): SubmitPromptDeps {
  return {
    appendMessage: vi.fn(),
    enqueue: vi.fn(),
    expand: (t: string) => t,
    gw,
    setLastUserMsg: vi.fn(),
    sys: vi.fn(),
    ...over
  }
}

describe('submissionCore.submitPrompt — synchronous busy (queue-race fix)', () => {
  beforeEach(() => {
    resetUiState()
    patchUiState({ sid: 'sess-1' })
  })

  it('flips busy=true SYNCHRONOUSLY, before input.detect_drop resolves', () => {
    const { gw, resolveDrop } = makeDeferredGateway()

    expect(getUiState().busy).toBe(false)

    submitPrompt('hello', makeDeps(gw))

    // The critical invariant: busy is already true even though the
    // detect_drop RPC has NOT resolved yet. This is what makes a second,
    // rapid submit take the local-enqueue branch instead of racing a second
    // prompt.submit onto the backend.
    expect(getUiState().busy).toBe(true)
    expect(getUiState().status).toBe('running…')

    resolveDrop()
  })

  it('regression: two back-to-back sends — the SECOND sees busy=true in the gap', async () => {
    const { gw, resolveDrop } = makeDeferredGateway()

    // Emulate dispatchSubmission's routing decision: it sends only when
    // busy===false, otherwise it would enqueue. We assert the state the
    // router reads, which is the real regression.
    submitPrompt('first message', makeDeps(gw))

    // Before the fix, busy was still false here (set only inside detect_drop's
    // .then), so a second Enter would wrongly route into send() again.
    const busyWhenSecondArrives = getUiState().busy
    expect(busyWhenSecondArrives).toBe(true)

    resolveDrop()
    await Promise.resolve()
  })

  it('does not submit when there is no session, and does not mark busy', () => {
    resetUiState() // sid: null
    const { gw, calls } = makeDeferredGateway()
    const sys = vi.fn()

    submitPrompt('hello', makeDeps(gw, { sys }))

    expect(getUiState().busy).toBe(false)
    expect(sys).toHaveBeenCalledWith('session not ready yet')
    expect(calls).not.toContain('input.detect_drop')
  })

  it('after detect_drop resolves (no file), it issues prompt.submit', async () => {
    const { calls, gw, resolveDrop } = makeDeferredGateway()

    submitPrompt('hi there', makeDeps(gw))
    expect(calls).toEqual(['input.detect_drop'])

    resolveDrop({ matched: false })
    await Promise.resolve()
    await Promise.resolve()

    expect(calls).toContain('prompt.submit')
  })
})

describe('submissionCore.submitPrompt — literal submissions (startup -q queries)', () => {
  beforeEach(() => {
    resetUiState()
    patchUiState({ sid: 'sess-1' })
  })

  it('skipDetectDrop submits directly without the detect_drop round-trip', async () => {
    const { calls, gw } = makeDeferredGateway()

    submitPrompt('!echo not-a-shell-escape', makeDeps(gw), true, undefined, { skipDetectDrop: true })

    await Promise.resolve()
    await Promise.resolve()

    expect(calls).not.toContain('input.detect_drop')
    expect(calls).toContain('prompt.submit')
  })

  it('literal text reaches prompt.submit verbatim', async () => {
    const submitted: string[] = []

    const gw = {
      request: vi.fn((method: string, params?: { text?: string }) => {
        if (method === 'prompt.submit' && params?.text) {
          submitted.push(params.text)
        }

        return Promise.resolve({ status: 'streaming' })
      })
    } as unknown as GatewayClient

    submitPrompt('/model $(rm -rf ~)', makeDeps(gw), true, undefined, { skipDetectDrop: true })

    await Promise.resolve()
    await Promise.resolve()

    expect(submitted).toEqual(['/model $(rm -rf ~)'])
  })
})

describe('submissionCore.markSubmitting', () => {
  beforeEach(() => resetUiState())

  it('sets busy + running status', () => {
    markSubmitting()
    expect(getUiState().busy).toBe(true)
    expect(getUiState().status).toBe('running…')
  })
})

describe('submissionCore.isSessionBusyError', () => {
  it('matches the legacy busy rejections but not arbitrary errors', () => {
    expect(isSessionBusyError(new Error('session busy'))).toBe(true)
    expect(isSessionBusyError(new Error('waiting for model response'))).toBe(true)
    expect(isSessionBusyError(new Error('some other failure'))).toBe(false)
    expect(isSessionBusyError('not an error')).toBe(false)
  })
})

describe('submissionCore.submitPrompt — message identity', () => {
  beforeEach(() => {
    resetUiState()
    patchUiState({ sid: 'sess-1' })
  })

  const submittedParams = (gw: GatewayClient) =>
    vi
      .mocked(gw.request)
      .mock.calls.filter(([method]) => method === 'prompt.submit')
      .map(([, params]) => params as Record<string, unknown>)

  const submitted = (gw: GatewayClient) => submittedParams(gw).map(params => params.client_message_id)

  it('gives every deliberate send its own UUID, even for identical text', () => {
    const { gw } = makeDeferredGateway()
    const deps = makeDeps(gw)

    submitPrompt('same', deps, true, undefined, { skipDetectDrop: true })
    submitPrompt('same', deps, true, undefined, { skipDetectDrop: true })

    const [first, second] = submitted(gw)

    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(second).toMatch(/^[0-9a-f-]{36}$/)
    expect(second).not.toBe(first)
  })

  it('reuses the identity a retry supplies', () => {
    const { gw } = makeDeferredGateway()

    submitPrompt('same', makeDeps(gw), false, undefined, { clientMessageId: 'retry-id', skipDetectDrop: true })

    expect(submitted(gw)).toEqual(['retry-id'])
  })

  it('carries the same identity through file-drop detection', async () => {
    const { gw, resolveDrop } = makeDeferredGateway()

    submitPrompt('look at this', makeDeps(gw))
    resolveDrop()
    await vi.waitFor(() => expect(submitted(gw)).toHaveLength(1))

    expect(submitted(gw)[0]).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('re-queues a refused send under the identity it already used', async () => {
    const gw = {
      request: vi.fn((method: string) =>
        method === 'prompt.submit' ? Promise.reject(new Error('session busy')) : Promise.resolve({})
      )
    } as unknown as GatewayClient

    const deps = makeDeps(gw)

    submitPrompt('queued words', deps, true, undefined, { skipDetectDrop: true })
    await vi.waitFor(() => expect(deps.enqueue).toHaveBeenCalledTimes(1))

    const [id] = submitted(gw)

    expect(id).toMatch(/^[0-9a-f-]{36}$/)
    expect(deps.enqueue).toHaveBeenCalledWith('queued words', undefined, id)
  })

  it('resends without the identity when an older backend rejects the unknown param', async () => {
    // Verbatim refusal from a real `hermes serve` on the previous release.
    const refusal = new JsonRpcGatewayError(
      'invalid params for prompt.submit: client_message_id: Extra inputs are not permitted — the client and the Hermes backend are out of sync (different versions); run `stardust update` and restart both',
      { code: 4000 }
    )

    const gw = {
      request: vi.fn((method: string, params?: Record<string, unknown>) =>
        method === 'prompt.submit' && params && 'client_message_id' in params
          ? Promise.reject(refusal)
          : Promise.resolve({ status: 'streaming' })
      )
    } as unknown as GatewayClient

    const deps = makeDeps(gw)

    submitPrompt('to an older backend', deps, true, undefined, { skipDetectDrop: true })
    await vi.waitFor(() => expect(submittedParams(gw)).toHaveLength(2))

    const [first, second] = submittedParams(gw)

    expect(first?.client_message_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(second).toEqual({ session_id: 'sess-1', text: 'to an older backend' })
    expect(deps.sys).not.toHaveBeenCalled()
    expect(deps.enqueue).not.toHaveBeenCalled()
    expect(getUiState().busy).toBe(true)
  })
})
