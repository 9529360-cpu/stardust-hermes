/** Pointer dead-zone / cancellation adapted from open-claude-ui's split-pane
 * design (MIT). This implementation keeps preview local and commits only once.
 * See apps/desktop/THIRD_PARTY_NOTICES/open-claude-ui.txt. */
export interface PointerResizeOptions {
  axis: 'x' | 'y'
  onPreview: (delta: number) => void
  onCommit: () => void
  onCancel: () => void
  onActiveChange: (active: boolean) => void
}

const RESIZE_DEAD_ZONE = 4

export function startPointerResize(
  handle: HTMLElement,
  start: PointerEvent,
  { axis, onPreview, onCommit, onCancel, onActiveChange }: PointerResizeOptions
): () => void {
  const document = handle.ownerDocument
  const view = document.defaultView

  if (!view || !handle.isConnected || start.button !== 0 || !start.isPrimary) {
    return () => {}
  }

  start.preventDefault()
  handle.setPointerCapture(start.pointerId)
  const coordinate = (event: PointerEvent) => (axis === 'x' ? event.clientX : event.clientY)
  const origin = coordinate(start)
  let delta = 0
  let frame: number | null = null
  let active = false
  let settled = false
  let shield: HTMLDivElement | null = null

  const preview = () => {
    frame = null

    if (!settled && active) {
      onPreview(delta)
    }
  }

  const moveTo = (event: PointerEvent) => {
    delta = coordinate(event) - origin

    if (!active && Math.abs(delta) < RESIZE_DEAD_ZONE) {
      return
    }

    if (!active) {
      active = true
      shield = document.createElement('div')
      shield.className = 'stardust-resize-shield'
      shield.dataset.axis = axis
      shield.setAttribute('aria-hidden', 'true')
      document.body.append(shield)
      onActiveChange(true)
    }

    if (frame === null) {
      frame = view.requestAnimationFrame(preview)
    }
  }

  const finish = (commit: boolean) => {
    if (settled) {
      return
    }

    if (frame !== null) {
      view.cancelAnimationFrame(frame)
    }
    frame = null

    // Flush the final pointer position even when pointerup beat the next frame.
    if (commit && active) {
      onPreview(delta)
    }
    settled = true
    view.removeEventListener('pointermove', onMove, true)
    view.removeEventListener('pointerup', onUp, true)
    view.removeEventListener('pointercancel', onPointerCancel, true)
    handle.removeEventListener('lostpointercapture', onPointerCancel)
    view.removeEventListener('keydown', onKey, true)
    view.removeEventListener('blur', cancel)
    view.removeEventListener('resize', cancel)
    document.removeEventListener('visibilitychange', onVisibility)
    shield?.remove()

    if (handle.hasPointerCapture(start.pointerId)) {
      handle.releasePointerCapture(start.pointerId)
    }

    if (active) {
      onActiveChange(false)
    }

    if (commit && active) {
      onCommit()
    } else {
      onCancel()
    }
  }

  const cancel = () => finish(false)

  const onMove = (event: PointerEvent) => {
    if (event.pointerId !== start.pointerId) {
      return
    }

    // A release outside the window may arrive without pointerup.
    if (event.buttons === 0) {
      cancel()

      return
    }

    moveTo(event)
  }

  const onUp = (event: PointerEvent) => {
    if (event.pointerId !== start.pointerId) {
      return
    }
    moveTo(event)
    finish(true)
  }

  const onPointerCancel = (event: PointerEvent) => {
    if (event.pointerId === start.pointerId) {
      cancel()
    }
  }

  const onKey = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') {
      return
    }
    event.preventDefault()
    event.stopImmediatePropagation()
    cancel()
  }

  const onVisibility = () => {
    if (document.hidden) {
      cancel()
    }
  }

  view.addEventListener('pointermove', onMove, true)
  view.addEventListener('pointerup', onUp, true)
  view.addEventListener('pointercancel', onPointerCancel, true)
  handle.addEventListener('lostpointercapture', onPointerCancel)
  // Temporary gesture cancellation, not a permanent global shortcut.
  view.addEventListener('keydown', onKey, true)
  view.addEventListener('blur', cancel)
  view.addEventListener('resize', cancel)
  document.addEventListener('visibilitychange', onVisibility)

  return cancel
}
