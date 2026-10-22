/** JSON-RPC "invalid params": the gateway answers an undeclared key with this code and the key's path
 *  (tui_gateway/contracts/registry.py::validate_params). */
const INVALID_PARAMS = 4000

/**
 * Send `params`; if an older backend rejects the optional `key` as an unknown param, send once more without it.
 *
 * A newer client meets an older backend whenever an app and its runtime update on separate clocks. The first attempt
 * failed contract validation before any handler ran, so the resend cannot duplicate work. Only that exact refusal
 * (4000 naming the key) is retried; every other failure surfaces unchanged.
 */
export async function retryWithoutRejectedParam<P extends object, R>(
  send: (params: P) => Promise<R>,
  params: P,
  key: keyof P & string
): Promise<R> {
  try {
    return await send(params)
  } catch (err) {
    const { code, message } = (err ?? {}) as { code?: unknown; message?: unknown }

    if (!(key in params) || code !== INVALID_PARAMS || typeof message !== 'string' || !message.includes(key)) {
      throw err
    }

    const { [key]: _rejected, ...rest } = params

    return send(rest as P)
  }
}
