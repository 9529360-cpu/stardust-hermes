import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
  useCallback,
  useEffect,
  useRef,
  useState
} from 'react'

import { isSmartZoomWheel } from '@/lib/trackpad-gestures'

interface Transform {
  scale: number
  x: number
  y: number
}

interface PanGesture {
  current: Transform
  origin: Transform
  onBlur: () => void
  pointerId: number
  startX: number
  startY: number
  captureTarget: HTMLDivElement
  transformTarget: HTMLDivElement
}

const MIN_SCALE = 0.25
const MAX_SCALE = 8
const WHEEL_STEP = 1.1
const BUTTON_STEP = 1.25

const clamp = (scale: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale))
const INITIAL_TRANSFORM: Transform = { scale: 1, x: 0, y: 0 }
const transformCss = ({ scale, x, y }: Transform) => `translate(${x}px, ${y}px) scale(${scale})`

/**
 * Headless pan/zoom transform. Wheel zooms toward the cursor, drag pans, buttons
 * zoom toward centre. Pointer movement writes once per animation frame without
 * re-rendering the content being panned.
 */
export function useZoomPan() {
  const [transform, setTransform] = useState<Transform>(INITIAL_TRANSFORM)
  const transformRef = useRef(INITIAL_TRANSFORM)
  const transformElementRef = useRef<HTMLDivElement>(null)
  const drag = useRef<null | PanGesture>(null)
  const frame = useRef<number | null>(null)
  const [panning, setPanning] = useState(false)

  const commitTransform = useCallback((next: Transform) => {
    transformRef.current = next
    setTransform(next)
  }, [])

  const cancelFrame = useCallback(() => {
    if (frame.current !== null) {
      window.cancelAnimationFrame(frame.current)
      frame.current = null
    }
  }, [])

  const finishPan = useCallback(
    (commit: boolean, expectedPointerId?: number) => {
      const gesture = drag.current

      if (!gesture || (expectedPointerId !== undefined && gesture.pointerId !== expectedPointerId)) {
        return
      }

      // Clear ownership first: releasePointerCapture may synchronously dispatch
      // lostpointercapture, which is another valid gesture-ending path.
      drag.current = null
      window.removeEventListener('blur', gesture.onBlur)
      cancelFrame()
      setPanning(false)

      const next = commit ? gesture.current : gesture.origin
      gesture.transformTarget.style.transform = transformCss(next)
      commitTransform(next)

      try {
        if (gesture.captureTarget.hasPointerCapture(gesture.pointerId)) {
          gesture.captureTarget.releasePointerCapture(gesture.pointerId)
        }
      } catch {
        // The pointer may already have been released by the browser.
      }
    },
    [cancelFrame, commitTransform]
  )

  // Keep a lost renderer/dialog from retaining an active pointer capture or a
  // queued animation frame after its stage has unmounted.
  useEffect(
    () => () => {
      cancelFrame()
      const gesture = drag.current
      drag.current = null

      if (gesture) {
        window.removeEventListener('blur', gesture.onBlur)
        const origin = transformCss(gesture.origin)

        // The RAF writes the transform directly to avoid a render per move. A
        // cancelled viewer must put React's last style back before it unmounts.
        if (gesture.transformTarget.style.transform !== origin) {
          gesture.transformTarget.style.transform = origin
        }

        try {
          if (gesture.captureTarget.hasPointerCapture(gesture.pointerId)) {
            gesture.captureTarget.releasePointerCapture(gesture.pointerId)
          }
        } catch {
          // The target may already be detached.
        }
      }
    },
    [cancelFrame]
  )

  // Zoom toward (cx, cy), measured from the surface centre, keeping that point fixed.
  const zoomAt = useCallback(
    (factor: number, cx = 0, cy = 0) => {
      if (drag.current) {
        finishPan(true)
      }

      const prev = transformRef.current
      const scale = clamp(prev.scale * factor)
      const k = scale / prev.scale

      commitTransform({ scale, x: cx - k * (cx - prev.x), y: cy - k * (cy - prev.y) })
    },
    [commitTransform, finishPan]
  )

  const onWheel = useCallback(
    (event: ReactWheelEvent) => {
      event.preventDefault()

      // macOS smart zoom (two-finger double-tap) → reset, not zoom-in.
      if (isSmartZoomWheel(event)) {
        finishPan(false)
        commitTransform(INITIAL_TRANSFORM)

        return
      }

      const rect = event.currentTarget.getBoundingClientRect()
      const cx = event.clientX - rect.left - rect.width / 2
      const cy = event.clientY - rect.top - rect.height / 2

      zoomAt(event.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP, cx, cy)
    },
    [commitTransform, finishPan, zoomAt]
  )

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const origin = transformRef.current

    // The natural-size diagram already fits its stage; dragging it at 1× would
    // move content out of view without creating any additional useful detail.
    if (event.button !== 0 || event.isPrimary === false || origin.scale <= 1 || drag.current) {
      return
    }

    event.preventDefault()
    const transformTarget = transformElementRef.current

    if (!transformTarget) {
      return
    }

    const captureTarget = event.currentTarget
    const onBlur = () => finishPan(false, event.pointerId)
    drag.current = {
      current: origin,
      origin,
      onBlur,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      captureTarget,
      transformTarget
    }

    try {
      captureTarget.setPointerCapture(event.pointerId)
    } catch {
      // Synthetic events and detached surfaces have no active pointer capture.
    }

    window.addEventListener('blur', onBlur)
    setPanning(true)
  }, [finishPan])

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const gesture = drag.current

      if (!gesture || gesture.pointerId !== event.pointerId) {
        return
      }

      gesture.current = {
        ...gesture.origin,
        x: gesture.origin.x + event.clientX - gesture.startX,
        y: gesture.origin.y + event.clientY - gesture.startY
      }

      if (frame.current !== null) {
        return
      }

      frame.current = window.requestAnimationFrame(() => {
        frame.current = null
        const latest = drag.current

        if (latest?.pointerId === event.pointerId) {
          latest.transformTarget.style.transform = transformCss(latest.current)
        }
      })
    },
    []
  )

  const onPointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) {
      finishPan(true, event.pointerId)
    }
  }, [finishPan])

  const onPointerCancel = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) {
      finishPan(false, event.pointerId)
    }
  }, [finishPan])

  const onLostPointerCapture = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) {
      finishPan(false, event.pointerId)
    }
  }, [finishPan])

  const reset = useCallback(() => {
    finishPan(false)
    commitTransform(INITIAL_TRANSFORM)
  }, [commitTransform, finishPan])
  const zoomIn = useCallback(() => zoomAt(BUTTON_STEP), [zoomAt])
  const zoomOut = useCallback(() => zoomAt(1 / BUTTON_STEP), [zoomAt])

  const style: CSSProperties = {
    transform: transformCss(transform)
  }

  return {
    panning,
    reset,
    scale: transform.scale,
    stageProps: { onLostPointerCapture, onPointerCancel, onPointerDown, onPointerMove, onPointerUp, onWheel },
    style,
    transformElementRef,
    zoomIn,
    zoomOut
  }
}
