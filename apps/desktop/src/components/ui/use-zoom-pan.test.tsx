import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useZoomPan } from './use-zoom-pan'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function Harness({ onRender }: { onRender: () => void }) {
  const pan = useZoomPan()
  const renders = useRef(0)
  renders.current += 1
  onRender()

  return (
    <>
      <button aria-label="Zoom in" onClick={pan.zoomIn} type="button" />
      <button aria-label="Reset zoom" onClick={pan.reset} type="button" />
      <div data-panning={pan.panning} data-testid="stage" {...pan.stageProps}>
        <div data-testid="content" ref={pan.transformElementRef} style={pan.style} />
      </div>
      <output data-testid="render-count">{renders.current}</output>
    </>
  )
}

function installRaf() {
  let nextId = 0
  const frames = new Map<number, FrameRequestCallback>()
  const request = vi.fn((callback: FrameRequestCallback) => {
    const id = ++nextId
    frames.set(id, callback)

    return id
  })
  const cancel = vi.fn((id: number) => frames.delete(id))

  vi.stubGlobal('requestAnimationFrame', request)
  vi.stubGlobal('cancelAnimationFrame', cancel)

  return {
    cancel,
    flush() {
      const pending = [...frames.values()]
      frames.clear()
      pending.forEach(callback => callback(performance.now()))
    },
    pending: () => frames.size,
    request
  }
}

function installPointerCapture() {
  const set = vi.fn()
  const release = vi.fn()
  const captured = new WeakMap<Element, Set<number>>()

  vi.stubGlobal('PointerEvent', class extends MouseEvent {
    isPrimary: boolean
    pointerId: number
    pointerType: string

    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init)
      this.isPrimary = init.isPrimary ?? true
      this.pointerId = init.pointerId ?? 0
      this.pointerType = init.pointerType ?? 'mouse'
    }
  })
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
    configurable: true,
    value(this: HTMLElement, pointerId: number) {
      set(pointerId)
      const ids = captured.get(this) ?? new Set<number>()
      ids.add(pointerId)
      captured.set(this, ids)
    }
  })
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
    configurable: true,
    value(this: HTMLElement, pointerId: number) { return captured.get(this)?.has(pointerId) ?? false }
  })
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
    configurable: true,
    value(this: HTMLElement, pointerId: number) {
      release(pointerId)
      captured.get(this)?.delete(pointerId)
    }
  })

  return { release, set }
}

function startZoomedPan() {
  const onRender = vi.fn()
  render(<Harness onRender={onRender} />)
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
  onRender.mockClear()

  const stage = screen.getByTestId('stage')
  const content = screen.getByTestId('content')

  fireEvent.pointerDown(stage, { button: 0, clientX: 100, clientY: 80, isPrimary: true, pointerId: 7 })
  onRender.mockClear()

  return { content, onRender, stage }
}

describe('useZoomPan pointer gesture', () => {
  beforeEach(() => {
    installPointerCapture()
  })

  it('coalesces pointer movement to one frame without re-rendering the diagram', () => {
    const raf = installRaf()
    const { content, onRender, stage } = startZoomedPan()
    const rendersAtStart = screen.getByTestId('render-count').textContent

    fireEvent.pointerMove(stage, { clientX: 112, clientY: 91, pointerId: 7 })
    fireEvent.pointerMove(stage, { clientX: 126, clientY: 104, pointerId: 7 })

    expect(raf.request).toHaveBeenCalledTimes(1)
    expect(raf.pending()).toBe(1)
    expect(onRender).not.toHaveBeenCalled()

    act(() => raf.flush())

    expect(content.style.transform).toBe('translate(26px, 24px) scale(1.25)')
    expect(screen.getByTestId('render-count').textContent).toBe(rendersAtStart)
    expect(onRender).not.toHaveBeenCalled()

    fireEvent.pointerUp(stage, { clientX: 126, clientY: 104, pointerId: 7 })

    expect(stage.getAttribute('data-panning')).toBe('false')
    expect(content.style.transform).toBe('translate(26px, 24px) scale(1.25)')
  })

  it('cancels a pending frame and restores the gesture origin on pointercancel', () => {
    const raf = installRaf()
    const { content, stage } = startZoomedPan()

    fireEvent.pointerMove(stage, { clientX: 140, clientY: 120, pointerId: 7 })
    expect(raf.pending()).toBe(1)

    fireEvent.pointerCancel(stage, { pointerId: 7 })

    expect(raf.cancel).toHaveBeenCalledTimes(1)
    expect(raf.pending()).toBe(0)
    expect(content.style.transform).toBe('translate(0px, 0px) scale(1.25)')
    expect(stage.getAttribute('data-panning')).toBe('false')
  })

  it('restores the gesture origin when the window blurs', () => {
    installRaf()
    const { content, stage } = startZoomedPan()

    fireEvent.pointerMove(stage, { clientX: 135, clientY: 95, pointerId: 7 })
    act(() => window.dispatchEvent(new Event('blur')))

    expect(content.style.transform).toBe('translate(0px, 0px) scale(1.25)')
    expect(stage.getAttribute('data-panning')).toBe('false')
  })

  it('does not let a secondary pointer steal or end the active pan', () => {
    const capture = installPointerCapture()
    installRaf()
    const { content, stage } = startZoomedPan()

    fireEvent.pointerDown(stage, { button: 0, clientX: 200, clientY: 200, isPrimary: false, pointerId: 8 })
    fireEvent.pointerMove(stage, { clientX: 300, clientY: 300, pointerId: 8 })
    fireEvent.pointerUp(stage, { pointerId: 8 })

    expect(capture.set).toHaveBeenCalledTimes(1)
    expect(content.style.transform).toBe('translate(0px, 0px) scale(1.25)')
    expect(stage.getAttribute('data-panning')).toBe('true')

    fireEvent.pointerCancel(stage, { pointerId: 7 })
    expect(stage.getAttribute('data-panning')).toBe('false')
  })
})
