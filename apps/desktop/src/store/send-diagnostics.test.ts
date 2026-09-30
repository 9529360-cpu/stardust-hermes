import { afterEach, describe, expect, it, vi } from 'vitest'

import { $gateway } from '@/store/gateway'
import {
  $sendDiagnostics,
  confirmSendDiagnostics,
  dismissSendDiagnostics,
  requestSendDiagnostics
} from '@/store/send-diagnostics'

function stubGateway(
  request: (method: string, params?: Record<string, unknown>, timeout?: number) => Promise<unknown>
) {
  const original = $gateway.get()

  $gateway.set({ request } as never)

  return () => $gateway.set(original)
}

function stubDesktop(options?: {
  lines?: string[]
  save?: (payload: Record<string, unknown>) => Promise<{ canceled?: boolean; path?: string; saved: boolean }>
}) {
  const original = window.hermesDesktop
  const saveGatewayFile =
    options?.save ??
    vi.fn(async () => ({
      path: '/Users/me/Downloads/stardust-diagnostics.zip',
      saved: true
    }))

  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: {
      getRecentLogs: async () => ({
        lines: options?.lines ?? [],
        path: '/tmp/desktop.log'
      }),
      saveGatewayFile
    }
  })

  return {
    saveGatewayFile,
    restore: () =>
      Object.defineProperty(window, 'hermesDesktop', {
        configurable: true,
        value: original
      })
  }
}

function localBundleRequest(overrides?: {
  discard?: unknown
  prepare?: unknown
}) {
  return vi.fn(async (method: string) => {
    if (method === 'diagnostics.prepare_bundle') {
      return (
        overrides?.prepare ?? {
          ok: true,
          path: '/srv/.hermes/cache/diagnostics/stardust-diagnostics-x.zip',
          filename: 'stardust-diagnostics-x.zip',
          byte_size: 123
        }
      )
    }

    if (method === 'diagnostics.discard_bundle') {
      return overrides?.discard ?? { ok: true, removed: true }
    }

    throw new Error(`unexpected diagnostics method: ${method}`)
  })
}

describe('send-diagnostics store', () => {
  afterEach(() => {
    $sendDiagnostics.set(null)
    vi.restoreAllMocks()
  })

  it('opens in consent phase without any backend or file I/O', () => {
    const request = vi.fn()
    const restore = stubGateway(request)

    try {
      requestSendDiagnostics('layer: provider')

      expect($sendDiagnostics.get()).toEqual({ errorContext: 'layer: provider', phase: 'consent' })
      expect(request).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('prepares locally, saves through the desktop bridge, then discards the backend copy', async () => {
    const request = localBundleRequest()
    const restoreGateway = stubGateway(request)
    const desktop = stubDesktop({ lines: ['boot ok', 'ws connected'] })

    try {
      requestSendDiagnostics('layer: streaming\ncode: stream_drop')
      await confirmSendDiagnostics()

      expect(request.mock.calls.map(call => call[0])).toEqual([
        'diagnostics.prepare_bundle',
        'diagnostics.discard_bundle'
      ])
      expect(request.mock.calls.some(call => call[0] === 'diagnostics.share_nous')).toBe(false)

      const prepareParams = request.mock.calls[0][1] as Record<string, unknown>

      expect(prepareParams.error_context).toContain('stream_drop')
      expect((prepareParams.extra_files as Record<string, string>)['desktop.log']).toContain('ws connected')

      expect(desktop.saveGatewayFile).toHaveBeenCalledWith(
        expect.objectContaining({
          path: '/srv/.hermes/cache/diagnostics/stardust-diagnostics-x.zip',
          suggestedName: 'stardust-diagnostics-x.zip'
        })
      )

      const state = $sendDiagnostics.get()

      expect(state?.phase).toBe('done')
      expect(state?.result?.savedPath).toBe('/Users/me/Downloads/stardust-diagnostics.zip')
      expect(state?.result?.byteSize).toBe(123)
    } finally {
      desktop.restore()
      restoreGateway()
    }
  })

  it('fails closed when the desktop save bridge is unavailable and still discards the temp bundle', async () => {
    const request = localBundleRequest()
    const restoreGateway = stubGateway(request)
    const originalDesktop = window.hermesDesktop

    Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: undefined })

    try {
      requestSendDiagnostics()
      await confirmSendDiagnostics()

      expect(request.mock.calls.map(call => call[0])).toEqual([
        'diagnostics.prepare_bundle',
        'diagnostics.discard_bundle'
      ])
      expect($sendDiagnostics.get()?.phase).toBe('error')
      expect($sendDiagnostics.get()?.error).toContain('save bridge')
    } finally {
      Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: originalDesktop })
      restoreGateway()
    }
  })

  it('surfaces preparation failures without falling back to the legacy upload RPC', async () => {
    const request = localBundleRequest({ prepare: { ok: false, error: 'bundle unavailable' } })
    const restoreGateway = stubGateway(request)
    const desktop = stubDesktop()

    try {
      requestSendDiagnostics()
      await confirmSendDiagnostics()

      expect(request).toHaveBeenCalledTimes(1)
      expect(request.mock.calls[0][0]).toBe('diagnostics.prepare_bundle')
      expect(request.mock.calls.some(call => call[0] === 'diagnostics.share_nous')).toBe(false)
      expect(desktop.saveGatewayFile).not.toHaveBeenCalled()
      expect($sendDiagnostics.get()?.phase).toBe('error')
      expect($sendDiagnostics.get()?.error).toContain('bundle unavailable')
    } finally {
      desktop.restore()
      restoreGateway()
    }
  })

  it('discards the backend copy when the local file save fails', async () => {
    const request = localBundleRequest()
    const restoreGateway = stubGateway(request)
    const desktop = stubDesktop({
      save: vi.fn(async () => {
        throw new Error('disk full')
      })
    })

    try {
      requestSendDiagnostics()
      await confirmSendDiagnostics()

      expect(request.mock.calls.map(call => call[0])).toEqual([
        'diagnostics.prepare_bundle',
        'diagnostics.discard_bundle'
      ])
      expect($sendDiagnostics.get()?.phase).toBe('error')
      expect($sendDiagnostics.get()?.error).toContain('disk full')
    } finally {
      desktop.restore()
      restoreGateway()
    }
  })

  it('treats native save cancellation as cancellation and cleans the backend copy', async () => {
    const request = localBundleRequest()
    const restoreGateway = stubGateway(request)
    const desktop = stubDesktop({
      save: vi.fn(async () => ({ canceled: true, saved: false }))
    })

    try {
      requestSendDiagnostics()
      await confirmSendDiagnostics()

      expect(request.mock.calls.map(call => call[0])).toEqual([
        'diagnostics.prepare_bundle',
        'diagnostics.discard_bundle'
      ])
      expect($sendDiagnostics.get()).toBeNull()
    } finally {
      desktop.restore()
      restoreGateway()
    }
  })

  it('reports cleanup uncertainty without hiding a successful local save', async () => {
    const request = localBundleRequest({
      discard: { ok: false, removed: false, error: 'backend cleanup unavailable' }
    })
    const restoreGateway = stubGateway(request)
    const desktop = stubDesktop()

    try {
      requestSendDiagnostics()
      await confirmSendDiagnostics()

      const state = $sendDiagnostics.get()

      expect(state?.phase).toBe('done')
      expect(state?.result?.cleanupWarning).toContain('backend cleanup unavailable')
      expect(state?.result?.savedPath).toContain('stardust-diagnostics.zip')
    } finally {
      desktop.restore()
      restoreGateway()
    }
  })

  it('confirm is a no-op outside the consent phase (no double prepare/save)', async () => {
    const request = localBundleRequest()
    const restoreGateway = stubGateway(request)
    const desktop = stubDesktop()

    try {
      requestSendDiagnostics()
      await confirmSendDiagnostics()
      await confirmSendDiagnostics()

      expect(request.mock.calls.filter(call => call[0] === 'diagnostics.prepare_bundle')).toHaveLength(1)
      expect(desktop.saveGatewayFile).toHaveBeenCalledTimes(1)
    } finally {
      desktop.restore()
      restoreGateway()
    }
  })

  it('dismiss clears the dialog state', () => {
    requestSendDiagnostics()
    dismissSendDiagnostics()

    expect($sendDiagnostics.get()).toBeNull()
  })

  it('dismissal mid-prepare cannot resurrect the dialog and the late temp bundle is discarded', async () => {
    let resolvePrepare: (value: unknown) => void = () => {}

    const request = vi.fn((method: string) => {
      if (method === 'diagnostics.prepare_bundle') {
        return new Promise(resolve => (resolvePrepare = resolve))
      }
      if (method === 'diagnostics.discard_bundle') {
        return Promise.resolve({ ok: true, removed: true })
      }

      throw new Error(`unexpected method ${method}`)
    })

    const restoreGateway = stubGateway(request as never)
    const desktop = stubDesktop()

    try {
      requestSendDiagnostics()
      const pending = confirmSendDiagnostics()

      await vi.waitFor(() =>
        expect(request.mock.calls.some(call => call[0] === 'diagnostics.prepare_bundle')).toBe(true)
      )
      dismissSendDiagnostics()
      expect($sendDiagnostics.get()).toBeNull()

      resolvePrepare({
        ok: true,
        path: '/srv/.hermes/cache/diagnostics/stardust-diagnostics-stale.zip',
        filename: 'stardust-diagnostics-stale.zip'
      })
      await pending

      expect($sendDiagnostics.get()).toBeNull()
      expect(desktop.saveGatewayFile).not.toHaveBeenCalled()
      expect(request.mock.calls.some(call => call[0] === 'diagnostics.discard_bundle')).toBe(true)

      requestSendDiagnostics('fresh')
      expect($sendDiagnostics.get()?.phase).toBe('consent')
    } finally {
      desktop.restore()
      restoreGateway()
    }
  })
})
