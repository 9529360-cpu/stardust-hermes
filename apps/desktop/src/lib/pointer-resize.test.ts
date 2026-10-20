// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { startPointerResize } from './pointer-resize'

describe('pointer resize transaction', () => {
  let handle: HTMLDivElement
  let cancel: () => void
  let frames: Map<number, FrameRequestCallback>
  let serial: number
  let captured: Set<number>

  const callbacks = () => ({
    onPreview: vi.fn(),
    onCommit: vi.fn(),
    onCancel: vi.fn(),
    onActiveChange: vi.fn()
  })

  const pointer = (type: string, x: number, overrides: Record<string, number | boolean> = {}) => {
    const event = new MouseEvent(type, {
      clientX: x,
      clientY: x,
      button: 0,
      buttons: 1,
      bubbles: true,
      cancelable: true
    })
    Object.defineProperties(event, {
      pointerId: { value: 7 },
      isPrimary: { value: true },
      ...Object.fromEntries(Object.entries(overrides).map(([key, value]) => [key, { value }]))
    })

    return event as PointerEvent
  }

  const move = (x: number) => window.dispatchEvent(pointer('pointermove', x))
  const up = (x: number) => window.dispatchEvent(pointer('pointerup', x, { buttons: 0 }))

  const flush = () => {
    const pending = [...frames.values()]
    frames.clear()
    pending.forEach(callback => callback(0))
  }

  beforeEach(() => {
    handle = document.createElement('div')
    document.body.append(handle)
    frames = new Map()
    serial = 0
    captured = new Set()

    cancel = () => {}

    handle.setPointerCapture = id => {
      captured.add(id)
    }
    handle.hasPointerCapture = id => captured.has(id)

    handle.releasePointerCapture = id => {
      captured.delete(id)
    }
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => {
      frames.set(++serial, callback)

      return serial
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => {
      frames.delete(id)
    })
  })

  afterEach(() => {
    cancel()
    handle.remove()
    expect(document.querySelector('.stardust-resize-shield')).toBeNull()
    expect(frames.size).toBe(0)
    vi.restoreAllMocks()
  })

  it('ignores secondary input and does not create a shield for sub-threshold wobble', () => {
    const cb = callbacks()
    cancel = startPointerResize(handle, pointer('pointerdown', 100, { button: 2 }), { axis: 'x', ...cb })
    move(170)
    up(170)
    expect(cb.onPreview).not.toHaveBeenCalled()
    cancel = startPointerResize(handle, pointer('pointerdown', 100), { axis: 'x', ...cb })
    move(102)
    up(102)
    expect(cb.onPreview).not.toHaveBeenCalled()
    expect(cb.onCommit).not.toHaveBeenCalled()
    expect(cb.onActiveChange).not.toHaveBeenCalled()
  })

  it('coalesces pointer work and flushes the final position before one commit', () => {
    const cb = callbacks()
    cancel = startPointerResize(handle, pointer('pointerdown', 100), { axis: 'x', ...cb })
    move(110)
    move(120)
    expect(cb.onPreview).not.toHaveBeenCalled()
    expect(frames.size).toBe(1)
    flush()
    expect(cb.onPreview).toHaveBeenLastCalledWith(20)
    expect(cb.onCommit).not.toHaveBeenCalled()
    up(150)
    expect(cb.onPreview).toHaveBeenLastCalledWith(50)
    expect(cb.onCommit).toHaveBeenCalledTimes(1)
    expect(cb.onCancel).not.toHaveBeenCalled()
    expect(captured.size).toBe(0)
    cancel()
    expect(cb.onCancel).not.toHaveBeenCalled()
  })

  it('consumes Escape only for the gesture and removes all its listeners', () => {
    const cb = callbacks()
    cancel = startPointerResize(handle, pointer('pointerdown', 100), { axis: 'x', ...cb })
    move(150)
    flush()
    const first = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true, bubbles: true })
    window.dispatchEvent(first)
    expect(first.defaultPrevented).toBe(true)
    expect(cb.onCancel).toHaveBeenCalledTimes(1)
    expect(cb.onActiveChange).toHaveBeenLastCalledWith(false)
    up(180)
    expect(cb.onCommit).not.toHaveBeenCalled()
    const second = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true, bubbles: true })
    window.dispatchEvent(second)
    expect(second.defaultPrevented).toBe(false)
  })

  it.each(['pointercancel', 'lostpointercapture', 'blur', 'resize', 'dispose'])(
    'cancels at %s without a preference write',
    boundary => {
      const cb = callbacks()
      cancel = startPointerResize(handle, pointer('pointerdown', 100), { axis: 'y', ...cb })
      move(70)
      flush()
      expect(cb.onPreview).toHaveBeenLastCalledWith(-30)

      if (boundary === 'dispose') {
        cancel()
      } else if (boundary === 'lostpointercapture') {
        handle.dispatchEvent(pointer(boundary, 70))
      } else if (boundary === 'pointercancel') {
        window.dispatchEvent(pointer(boundary, 70))
      } else {
        window.dispatchEvent(new Event(boundary))
      }

      expect(cb.onCancel).toHaveBeenCalledTimes(1)
      expect(cb.onCommit).not.toHaveBeenCalled()
      if (boundary === 'resize') {
        expect(cb.onActiveChange).toHaveBeenLastCalledWith(false)
      }
    }
  )

  it('ignores another pointer and cancels when a release was lost', () => {
    const cb = callbacks()
    cancel = startPointerResize(handle, pointer('pointerdown', 100), { axis: 'x', ...cb })
    window.dispatchEvent(pointer('pointermove', 170, { pointerId: 8 }))
    expect(frames.size).toBe(0)
    move(120)
    window.dispatchEvent(pointer('pointermove', 130, { buttons: 0 }))
    expect(cb.onCancel).toHaveBeenCalledTimes(1)
    expect(cb.onCommit).not.toHaveBeenCalled()
  })
})
