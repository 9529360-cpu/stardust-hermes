import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, useI18n } from '@/i18n'

import { useComposerPlaceholder } from './use-composer-placeholder'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <I18nProvider configClient={null} initialLocale="en">
      {children}
    </I18nProvider>
  )
}

describe('useComposerPlaceholder', () => {
  it('translates the resting placeholder when the display language changes', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)

    const { result } = renderHook(() => {
      const i18n = useI18n()
      const placeholder = useComposerPlaceholder({ disabled: false, reconnecting: false, sessionId: null })

      return { i18n, placeholder }
    }, { wrapper })
    expect(result.current.placeholder).toBe('What are we building?')

    await act(async () => {
      await result.current.i18n.setLocale('zh')
    })

    expect(result.current.i18n.locale).toBe('zh')
    expect(result.current.placeholder).toBe('今天想做什么？')
  })

  it('keeps a starter when a new session is persisted', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)

    const { result, rerender } = renderHook(
      ({ sessionId }: { sessionId: null | string }) =>
        useComposerPlaceholder({ disabled: false, reconnecting: false, sessionId }),
      { initialProps: { sessionId: null as null | string }, wrapper }
    )

    expect(result.current).toBe('What are we building?')
    rerender({ sessionId: 'persisted-session' })
    expect(result.current).toBe('What are we building?')
  })
})
