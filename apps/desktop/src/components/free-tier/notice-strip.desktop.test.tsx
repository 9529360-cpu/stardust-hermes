import { cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { useFreeTierNoticeOwner } from './notice-strip'

const originalDesktopBridge = window.hermesDesktop

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: originalDesktopBridge
  })
})

describe('useFreeTierNoticeOwner', () => {
  it('never claims the retired free-tier notice in Stardust Desktop', () => {
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {}
    })

    const { result } = renderHook(() => useFreeTierNoticeOwner())

    expect(result.current).toBe(false)
  })
})
