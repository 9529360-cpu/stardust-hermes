import { describe, expect, it, vi } from 'vitest'

import { JsonRpcGatewayError } from './json-rpc-channel'
import { retryWithoutRejectedParam } from './optional-param'

// Verbatim from a real gateway that predates `client_message_id` (captured from `hermes serve` on the base commit).
const OLD_BACKEND_REFUSAL =
  'invalid params for prompt.submit: client_message_id: Extra inputs are not permitted — the client and the Hermes backend are out of sync (different versions); run `stardust update` and restart both'

const params = { client_message_id: 'id-1', session_id: 's', text: 'hello' }

describe('retryWithoutRejectedParam', () => {
  it('sends once, untouched, when the backend accepts the param', async () => {
    const send = vi.fn(async () => 'ok')

    await expect(retryWithoutRejectedParam(send, params, 'client_message_id')).resolves.toBe('ok')
    expect(send.mock.calls).toEqual([[params]])
  })

  it('resends without the param when an older backend rejects it as unknown', async () => {
    const send = vi
      .fn<(p: typeof params) => Promise<string>>()
      .mockRejectedValueOnce(new JsonRpcGatewayError(OLD_BACKEND_REFUSAL, { code: 4000 }))
      .mockResolvedValueOnce('ok')

    await expect(retryWithoutRejectedParam(send, params, 'client_message_id')).resolves.toBe('ok')
    expect(send.mock.calls).toEqual([[params], [{ session_id: 's', text: 'hello' }]])
  })

  it.each([
    ['another failure code', new JsonRpcGatewayError('session busy', { code: 4009 })],
    [
      'a different rejected key',
      new JsonRpcGatewayError('invalid params for prompt.submit: surface: Extra inputs', { code: 4000 })
    ],
    ['an error without a code', new Error(OLD_BACKEND_REFUSAL)],
    ['a non-error rejection', 'nope']
  ])('does not retry %s', async (_name, failure) => {
    const send = vi.fn().mockRejectedValue(failure)

    await expect(retryWithoutRejectedParam(send, params, 'client_message_id')).rejects.toBe(failure)
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('does not retry a send that never carried the param', async () => {
    const failure = new JsonRpcGatewayError(OLD_BACKEND_REFUSAL, { code: 4000 })
    const send = vi.fn().mockRejectedValue(failure)
    const plain: { client_message_id?: string; session_id: string } = { session_id: 's' }

    await expect(retryWithoutRejectedParam(send, plain, 'client_message_id')).rejects.toBe(failure)
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('surfaces the second failure instead of looping', async () => {
    const second = new Error('boom')

    const send = vi
      .fn()
      .mockRejectedValueOnce(new JsonRpcGatewayError(OLD_BACKEND_REFUSAL, { code: 4000 }))
      .mockRejectedValueOnce(second)

    await expect(retryWithoutRejectedParam(send, params, 'client_message_id')).rejects.toBe(second)
    expect(send).toHaveBeenCalledTimes(2)
  })
})
