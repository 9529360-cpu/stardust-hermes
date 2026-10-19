import '@/components/ui/pointer-resize.css'

import { type PointerEvent as ReactPointerEvent, useCallback, useLayoutEffect, useRef } from 'react'

import { type PointerResizeOptions, startPointerResize } from '@/lib/pointer-resize'

/** One gesture per owner; a context switch or unmount cancels its local preview. */
export function usePointerResize(scope: string | undefined) {
  const cancelRef = useRef<(() => void) | null>(null)
  useLayoutEffect(() => () => cancelRef.current?.(), [scope])

  return useCallback((event: ReactPointerEvent<HTMLElement>, options: PointerResizeOptions) => {
    if (event.button !== 0 || !event.isPrimary) {
      return
    }
    cancelRef.current?.()
    cancelRef.current = startPointerResize(event.currentTarget, event.nativeEvent, options)
  }, [])
}
