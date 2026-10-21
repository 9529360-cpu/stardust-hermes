import { type DelegationRecoveryReceipt, JsonRpcGatewayError } from '@stardust/shared'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PaneVisibleContext } from '@/components/pane-shell/pane-visibility'
import { I18nProvider, TRANSLATIONS } from '@/i18n'
import * as gateway from '@/store/gateway'
import { $gatewayState, _resetSessionOwnerHintsForTests, setSessionOwnerHint } from '@/store/session'
import { $subagentsBySession } from '@/store/subagents'

import { useDelegationRecovery } from './use-delegation-recovery'

import { ComposerStatusStack } from './index'

vi.mock('@/lib/use-enter-animation', () => ({ useEnterAnimation: () => undefined }))

vi.stubGlobal(
  'ResizeObserver',
  class {
    disconnect() {}
    observe() {}
    unobserve() {}
  }
)

const OWNER = { connectionId: 'remote-owner', profile: 'research' }
const METHOD = 'delegation.recovery.list'

const receipt = (overrides: Partial<DelegationRecoveryReceipt> = {}): DelegationRecoveryReceipt => ({
  delegation_id: 'deleg-1',
  state: 'unknown',
  dispatched_at: 1_700_000_000,
  completed_at: null,
  delivery_state: 'pending',
  goal: 'Audit the billing exports',
  is_batch: false,
  goal_count: 1,
  ...overrides
})

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(r => {
    resolve = r
  })

  return { promise, resolve }
}

type Handlers = Record<string, (params: Record<string, unknown>) => unknown>

/** Answer owner-routed RPCs per method; anything unlisted gets an empty result, like a quiet backend. */
function serve(handlers: Handlers) {
  return vi.spyOn(gateway, 'requestGatewayForAgent').mockImplementation(async (_connection, _profile, method, params) => {
    const handler = handlers[method]

    return (handler ? await handler((params ?? {}) as Record<string, unknown>) : {}) as never
  })
}

const recoveryCalls = (request: ReturnType<typeof serve>) => request.mock.calls.filter(call => call[2] === METHOD)

const tick = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })

const stack = (sessionId: null | string, options: { onSubmit?: () => void; visible?: boolean } = {}) => (
  <MemoryRouter>
    <PaneVisibleContext.Provider value={options.visible ?? true}>
      <ComposerStatusStack onSubmit={options.onSubmit as never} queue={null} sessionId={sessionId} />
    </PaneVisibleContext.Provider>
  </MemoryRouter>
)

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  $subagentsBySession.set({})
  $gatewayState.set('idle')
  _resetSessionOwnerHintsForTests()
})

describe('unknown-outcome receipts in the composer status stack', () => {
  it('shows the goal summary and batch size, asked of the session owner with only session_id', async () => {
    const request = serve({
      [METHOD]: () => ({
        receipts: [
          receipt({ delegation_id: 'single', goal: 'Audit the billing exports' }),
          receipt({ delegation_id: 'batch', goal: 'Summarise the quarterly reports', is_batch: true, goal_count: 3 })
        ]
      })
    })

    setSessionOwnerHint('parent', OWNER)
    render(stack('parent'))

    fireEvent.click(await screen.findByRole('button', { name: /2 Unknown subagent results/ }))

    expect(screen.getByText('Audit the billing exports')).toBeTruthy()
    expect(screen.getByText('Summarise the quarterly reports')).toBeTruthy()
    expect(screen.getAllByText(/Result unknown/)).toHaveLength(2)
    expect(screen.getByText(/3 goals in batch/)).toBeTruthy()
    // Not a word that could be read as "completed" or "failed": the outcome is genuinely not known.
    expect(screen.getByText(/unknown whether they finished/)).toBeTruthy()
    expect(request).toHaveBeenCalledWith(OWNER.connectionId, OWNER.profile, METHOD, { session_id: 'parent' })
    expect(recoveryCalls(request)[0]).toHaveLength(4)
  })

  it('is display-only: no control to run it again, no submit, no write to the backend', async () => {
    const onSubmit = vi.fn()
    const request = serve({ [METHOD]: () => ({ receipts: [receipt()] }) })

    setSessionOwnerHint('parent', OWNER)
    const view = render(stack('parent', { onSubmit }))

    fireEvent.click(await screen.findByRole('button', { name: /1 Unknown subagent result/ }))

    const section = view.container.querySelector('[data-slot="composer-delegation-recovery"]')

    expect(section).not.toBeNull()
    // The disclosure header is the only interactive element.
    expect(section?.querySelectorAll('button, input, textarea, a')).toHaveLength(1)

    const methods = request.mock.calls.map(call => call[2])

    expect(onSubmit).not.toHaveBeenCalled()
    expect(methods).not.toContain('prompt.submit')
    expect(methods.filter(method => method.startsWith('delegation.'))).toEqual(methods.filter(method => method === METHOD))
  })

  it('shows only receipts the backend marked unknown', async () => {
    serve({
      [METHOD]: () => ({
        receipts: [
          receipt({ delegation_id: 'kept', goal: 'Genuinely unknown work' }),
          receipt({ delegation_id: 'newer-state', goal: 'A state this client has no copy for', state: 'completed' as never })
        ]
      })
    })

    setSessionOwnerHint('parent', OWNER)
    render(stack('parent'))

    fireEvent.click(await screen.findByRole('button', { name: /1 Unknown subagent result/ }))

    expect(screen.getByText('Genuinely unknown work')).toBeTruthy()
    expect(screen.queryByText('A state this client has no copy for')).toBeNull()
  })

  it('renders nothing for a session without receipts', async () => {
    const request = serve({ [METHOD]: () => ({ receipts: [] }) })

    setSessionOwnerHint('parent', OWNER)
    const view = render(stack('parent'))

    await waitFor(() => expect(recoveryCalls(request)).toHaveLength(1))

    expect(view.container.querySelector('[data-slot="composer-delegation-recovery"]')).toBeNull()
  })

  it('leaves the live subagent roster exactly as its own snapshot reported it', async () => {
    serve({
      'subagent.list': () => ({
        subagents: [{ subagent_id: 'worker', goal: 'Live work', status: 'running' }],
        delegations: []
      }),
      [METHOD]: () => ({ receipts: [receipt({ goal: 'Exited work' })] })
    })

    setSessionOwnerHint('parent', OWNER)
    render(stack('parent'))

    fireEvent.click(await screen.findByRole('button', { name: /1 Subagent/ }))
    expect(await screen.findByText('Live work')).toBeTruthy()

    fireEvent.click(await screen.findByRole('button', { name: /1 Unknown subagent result/ }))
    expect(screen.getByText('Exited work')).toBeTruthy()
    // A receipt is not a live child: it must never enter the roster the steer and stop controls read.
    expect($subagentsBySession.get().parent.map(item => item.goal)).toEqual(['Live work'])
  })

  it('keeps the live roster working when the backend has no recovery RPC', async () => {
    serve({
      'subagent.list': () => ({ subagents: [{ subagent_id: 'worker', goal: 'Live work', status: 'running' }] }),
      [METHOD]: () => {
        throw new JsonRpcGatewayError('unknown method: delegation.recovery.list', { code: -32601 })
      }
    })

    setSessionOwnerHint('parent', OWNER)
    const view = render(stack('parent'))

    fireEvent.click(await screen.findByRole('button', { name: /1 Subagent/ }))
    expect(await screen.findByText('Live work')).toBeTruthy()
    expect(view.container.querySelector('[data-slot="composer-delegation-recovery"]')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('session and owner scope', () => {
  it('never shows one session’s receipts under another, including a late answer', async () => {
    const late = deferred<{ receipts: DelegationRecoveryReceipt[] }>()

    const request = serve({
      [METHOD]: params => (params.session_id === 'a' ? late.promise : { receipts: [] })
    })

    setSessionOwnerHint('a', OWNER)
    setSessionOwnerHint('b', OWNER)
    const view = render(stack('a'))

    await waitFor(() => expect(recoveryCalls(request)).toHaveLength(1))
    view.rerender(stack('b'))
    await act(async () => late.resolve({ receipts: [receipt({ goal: 'Only session A knows this' })] }))

    expect(screen.queryByText('Only session A knows this')).toBeNull()
    expect(view.container.querySelector('[data-slot="composer-delegation-recovery"]')).toBeNull()
  })

  it('never renders one session’s receipts under another, not even for the frame before effects run', async () => {
    serve({
      [METHOD]: params =>
        params.session_id === 'a' ? { receipts: [receipt({ goal: 'A work' })] } : new Promise(() => undefined)
    })

    setSessionOwnerHint('a', OWNER)
    setSessionOwnerHint('b', OWNER)

    // Record every render, not just the settled one: an effect-time cleanup would hide a one-frame leak from the DOM.
    const rendered: { goals: string[]; id: string }[] = []

    const hook = renderHook(
      ({ id }) => {
        const receipts = useDelegationRecovery(id)

        rendered.push({ goals: receipts.map(item => item.goal), id })

        return receipts
      },
      { initialProps: { id: 'a' } }
    )

    await waitFor(() => expect(hook.result.current).toHaveLength(1))
    hook.rerender({ id: 'b' })

    const underB = rendered.filter(entry => entry.id === 'b')

    expect(underB.length).toBeGreaterThan(0)
    expect(underB.flatMap(entry => entry.goals)).toEqual([])
    expect(hook.result.current).toEqual([])
  })

  it('drops an answer whose owner changed while it was in flight, then asks the new owner', async () => {
    vi.useFakeTimers()
    const first = deferred<{ receipts: DelegationRecoveryReceipt[] }>()

    const request = vi.spyOn(gateway, 'requestGatewayForAgent').mockImplementation(async (connection, _profile, method) =>
      (method === METHOD
        ? connection === 'owner-1'
          ? await first.promise
          : { receipts: [receipt({ goal: 'Owner two work' })] }
        : {}) as never
    )

    setSessionOwnerHint('parent', { connectionId: 'owner-1', profile: 'research' })
    const view = render(stack('parent'))
    await tick(0)

    expect(recoveryCalls(request)[0][0]).toBe('owner-1')

    _resetSessionOwnerHintsForTests()
    setSessionOwnerHint('parent', { connectionId: 'owner-2', profile: 'research' })
    await act(async () => first.resolve({ receipts: [receipt({ goal: 'Owner one work' })] }))

    // The section is collapsed by default, so assert on the section itself, not on text hidden inside it.
    expect(view.container.querySelector('[data-slot="composer-delegation-recovery"]')).toBeNull()

    await tick(60_000)

    expect(request).toHaveBeenCalledWith('owner-2', 'research', METHOD, { session_id: 'parent' })
    fireEvent.click(screen.getByRole('button', { name: /1 Unknown subagent result/ }))
    expect(screen.getByText('Owner two work')).toBeTruthy()
    expect(screen.queryByText('Owner one work')).toBeNull()
  })

  it('forgets receipts that came from a previous owner as soon as it asks again', async () => {
    vi.useFakeTimers()

    serve({
      [METHOD]: () => ({ receipts: [receipt({ goal: 'Owner one work' })] })
    })

    setSessionOwnerHint('parent', { connectionId: 'owner-1', profile: 'research' })
    const hook = renderHook(() => useDelegationRecovery('parent'))
    await tick(0)

    expect(hook.result.current.map(item => item.goal)).toEqual(['Owner one work'])

    // The next owner never answers: whatever the previous one said must not linger as its truth.
    vi.spyOn(gateway, 'requestGatewayForAgent').mockImplementation(() => new Promise(() => undefined) as never)
    _resetSessionOwnerHintsForTests()
    setSessionOwnerHint('parent', { connectionId: 'owner-2', profile: 'research' })
    await tick(60_000)

    expect(hook.result.current).toEqual([])
  })
})

describe('polling discipline', () => {
  it('steps aside silently on a backend without the RPC and never asks again', async () => {
    vi.useFakeTimers()

    const request = serve({
      [METHOD]: () => {
        throw new JsonRpcGatewayError('unknown method: delegation.recovery.list', { code: -32601 })
      }
    })

    setSessionOwnerHint('parent', OWNER)
    const hook = renderHook(() => useDelegationRecovery('parent'))

    await tick(10 * 60_000)
    window.dispatchEvent(new Event('focus'))
    window.document.dispatchEvent(new Event('visibilitychange'))
    await tick(10 * 60_000)

    expect(recoveryCalls(request)).toHaveLength(1)
    expect(hook.result.current).toEqual([])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('also recognises a method-missing error that lost its code crossing the IPC bridge', async () => {
    vi.useFakeTimers()

    const request = serve({
      [METHOD]: () => {
        throw new Error('Method not found')
      }
    })

    setSessionOwnerHint('parent', OWNER)
    renderHook(() => useDelegationRecovery('parent'))
    await tick(10 * 60_000)

    expect(recoveryCalls(request)).toHaveLength(1)
  })

  it('probes again after the gateway reconnects, since the backend may have been upgraded', async () => {
    vi.useFakeTimers()
    let upgraded = false

    const request = serve({
      [METHOD]: () => {
        if (!upgraded) {
          throw new JsonRpcGatewayError('unknown method', { code: -32601 })
        }

        return { receipts: [receipt({ goal: 'Found after upgrade' })] }
      }
    })

    setSessionOwnerHint('parent', OWNER)
    const hook = renderHook(() => useDelegationRecovery('parent'))

    await tick(10 * 60_000)
    expect(recoveryCalls(request)).toHaveLength(1)

    upgraded = true
    await act(async () => $gatewayState.set('connecting'))
    await act(async () => $gatewayState.set('open'))
    await tick(0)

    expect(hook.result.current.map(item => item.goal)).toEqual(['Found after upgrade'])
  })

  it('retries a transient failure a bounded number of times, spaced out, then waits for a wake signal', async () => {
    vi.useFakeTimers()
    const at: number[] = []

    serve({
      [METHOD]: () => {
        at.push(Date.now())
        throw new Error('gateway unavailable')
      }
    })

    setSessionOwnerHint('parent', OWNER)
    renderHook(() => useDelegationRecovery('parent'))

    await tick(10 * 60_000)

    const settled = at.length

    expect(settled).toBeGreaterThan(1)
    expect(settled).toBeLessThanOrEqual(5)
    expect(Math.min(...at.slice(1).map((time, index) => time - at[index]))).toBeGreaterThanOrEqual(1_000)

    await tick(30 * 60_000)
    expect(at).toHaveLength(settled)

    window.dispatchEvent(new Event('focus'))
    await tick(0)
    expect(at).toHaveLength(settled + 1)
  })

  it('recovers on its own from a transient failure and keeps a slow safety-net cadence afterwards', async () => {
    vi.useFakeTimers()
    let failing = true

    const request = serve({
      [METHOD]: () => {
        if (failing) {
          throw new Error('gateway unavailable')
        }

        return { receipts: [receipt({ goal: 'Back again' })] }
      }
    })

    setSessionOwnerHint('parent', OWNER)
    const hook = renderHook(() => useDelegationRecovery('parent'))

    await tick(0)
    failing = false
    await tick(60_000)

    expect(hook.result.current.map(item => item.goal)).toEqual(['Back again'])

    const before = recoveryCalls(request).length

    await tick(10 * 60_000)

    const after = recoveryCalls(request).length

    expect(after).toBeGreaterThan(before)
    // Receipts only change when a background owner dies: a 10 minute window is a handful of reads, not a live feed.
    expect(after - before).toBeLessThanOrEqual(12)
  })

  it('makes no request while its pane is hidden, and asks as soon as it is revealed', async () => {
    vi.useFakeTimers()
    let visible = false

    const wrapper = ({ children }: { children: ReactNode }) => (
      <PaneVisibleContext.Provider value={visible}>{children}</PaneVisibleContext.Provider>
    )

    const request = serve({ [METHOD]: () => ({ receipts: [receipt()] }) })

    setSessionOwnerHint('parent', OWNER)
    const hook = renderHook(() => useDelegationRecovery('parent'), { wrapper })

    await tick(10 * 60_000)
    window.dispatchEvent(new Event('focus'))
    await tick(0)
    expect(recoveryCalls(request)).toHaveLength(0)

    visible = true
    hook.rerender()
    await tick(0)
    expect(recoveryCalls(request)).toHaveLength(1)
    expect(hook.result.current).toHaveLength(1)

    // Hidden again: the scheduled safety-net read is cancelled, not merely ignored.
    visible = false
    hook.rerender()

    const afterHide = recoveryCalls(request).length

    await tick(10 * 60_000)
    expect(recoveryCalls(request)).toHaveLength(afterHide)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('makes no request while the window itself is hidden, and resumes when it is shown', async () => {
    vi.useFakeTimers()
    const state = vi.spyOn(window.document, 'visibilityState', 'get').mockReturnValue('hidden')
    const request = serve({ [METHOD]: () => ({ receipts: [receipt()] }) })

    setSessionOwnerHint('parent', OWNER)
    renderHook(() => useDelegationRecovery('parent'))

    await tick(10 * 60_000)
    expect(recoveryCalls(request)).toHaveLength(0)

    state.mockReturnValue('visible')
    window.document.dispatchEvent(new Event('visibilitychange'))
    await tick(0)
    expect(recoveryCalls(request)).toHaveLength(1)
  })

  it('stops all work on unmount: no timer, no listener, no late state', async () => {
    vi.useFakeTimers()
    const late = deferred<{ receipts: DelegationRecoveryReceipt[] }>()
    const request = serve({ [METHOD]: () => late.promise })

    setSessionOwnerHint('parent', OWNER)
    const hook = renderHook(() => useDelegationRecovery('parent'))

    await tick(0)
    expect(recoveryCalls(request)).toHaveLength(1)

    hook.unmount()
    await act(async () => late.resolve({ receipts: [receipt()] }))
    window.dispatchEvent(new Event('focus'))
    window.document.dispatchEvent(new Event('visibilitychange'))
    await tick(10 * 60_000)

    expect(recoveryCalls(request)).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('asks nothing without a session', async () => {
    vi.useFakeTimers()
    const request = serve({ [METHOD]: () => ({ receipts: [receipt()] }) })

    const hook = renderHook(() => useDelegationRecovery(null))

    await tick(10 * 60_000)
    expect(request).not.toHaveBeenCalled()
    expect(hook.result.current).toEqual([])
  })

  it('keeps the same array identity when a re-read changes nothing', async () => {
    vi.useFakeTimers()
    serve({ [METHOD]: () => ({ receipts: [receipt()] }) })

    setSessionOwnerHint('parent', OWNER)
    const hook = renderHook(() => useDelegationRecovery('parent'))

    await tick(0)

    const first = hook.result.current

    await tick(2 * 60_000)
    expect(hook.result.current).toBe(first)
  })
})

describe('copy', () => {
  it('reads in Chinese when the interface is Chinese', async () => {
    serve({
      [METHOD]: () => ({
        receipts: [receipt({ goal: '核对账单导出' }), receipt({ delegation_id: 'batch', is_batch: true, goal_count: 3 })]
      })
    })

    setSessionOwnerHint('parent', OWNER)
    render(
      <MemoryRouter>
        <I18nProvider configClient={null} initialLocale="zh">
          <ComposerStatusStack queue={null} sessionId="parent" />
        </I18nProvider>
      </MemoryRouter>
    )

    fireEvent.click(await screen.findByRole('button', { name: /2 项子代理结果未知/ }))

    expect(screen.getByText('核对账单导出')).toBeTruthy()
    expect(screen.getAllByText(/结果未知/).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/批量 3 个目标/)).toBeTruthy()
    expect(screen.getByText(/无法确定是否已完成/)).toBeTruthy()
  })

  for (const locale of ['zh', 'zh-hant', 'ja'] as const) {
    it(`${locale} has its own text for every recovery string`, () => {
      const english = TRANSLATIONS.en.statusStack
      const copy = TRANSLATIONS[locale].statusStack

      expect(copy.recoveryUnknown(2)).not.toBe(english.recoveryUnknown(2))
      expect(copy.recoveryRowState).not.toBe(english.recoveryRowState)
      expect(copy.recoveryNote).not.toBe(english.recoveryNote)
      expect(copy.recoveryGoalCount(3)).not.toBe(english.recoveryGoalCount(3))
      expect(copy.recoveryGoalCount(3)).toContain('3')
      expect(copy.recoveryUnknown(2)).toContain('2')
    })
  }
})
