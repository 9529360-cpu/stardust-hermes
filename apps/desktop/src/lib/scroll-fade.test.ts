import { describe, expect, it, vi } from 'vitest'

import { observeScrollFade, scrollFadeEdges } from './scroll-fade'

describe('scrollFadeEdges', () => {
  it.each([
    [0, 500, 100, 0, 28],
    [200, 500, 100, 28, 28],
    [400, 500, 100, 28, 0],
    [-40, 500, 100, 0, 28],
    [900, 500, 100, 28, 0],
    [0, 90, 100, 0, 0],
    [0, 0, 0, 0, 0],
    [2, 500, 100, 2, 28],
    [399.5, 500, 100, 28, 0],
    [40, 500, 20, 10, 10]
  ])(
    'uses actual edges at scrollTop=%s, scrollHeight=%s, clientHeight=%s',
    (scrollTop, scrollHeight, clientHeight, top, bottom) => {
      expect(scrollFadeEdges({ scrollTop, scrollHeight, clientHeight })).toEqual({ top, bottom })
    }
  )

  it('treats a disabled fade size as zero without changing scroll metrics', () => {
    const metrics = Object.freeze({ scrollTop: 40, scrollHeight: 500, clientHeight: 100 })
    expect(scrollFadeEdges(metrics, -1)).toEqual({ top: 0, bottom: 0 })
    expect(scrollFadeEdges(metrics, 12)).toEqual({ top: 12, bottom: 12 })
    expect(metrics.scrollTop).toBe(40)
  })

  it.each(['ResizeObserver', 'MutationObserver'] as const)(
    'measures and tracks scroll when %s is unavailable',
    missing => {
      const view = window as unknown as Record<string, unknown>
      const original = view[missing]
      Object.defineProperty(view, missing, { configurable: true, value: undefined })
      const viewport = document.createElement('div')
      Object.defineProperties(viewport, {
        clientHeight: { configurable: true, value: 100 },
        scrollHeight: { configurable: true, value: 300 },
        scrollTop: { configurable: true, writable: true, value: 100 }
      })
      document.body.append(viewport)
      const frames: FrameRequestCallback[] = []
      vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => {
        frames.push(callback)
        return frames.length
      })
      const update = vi.spyOn(viewport.style, 'setProperty')
      const dispose = observeScrollFade(viewport, document.createElement('div'))

      expect(viewport.getAttribute('data-scroll-fade-active')).not.toBeNull()
      expect(update).toHaveBeenCalledWith('--scroll-fade-top', '28px')
      expect(update).toHaveBeenCalledWith('--scroll-fade-bottom', '28px')

      viewport.scrollTop = 0
      viewport.dispatchEvent(new Event('scroll'))
      frames.splice(0).forEach(callback => callback(0))
      dispose()
      expect(viewport.hasAttribute('data-scroll-fade')).toBe(false)
      expect(viewport.hasAttribute('data-scroll-fade-active')).toBe(false)
      expect(update).toHaveBeenCalledWith('--scroll-fade-top', '0px')
      viewport.remove()
      Object.defineProperty(view, missing, { configurable: true, value: original })
      vi.restoreAllMocks()
    }
  )
})
