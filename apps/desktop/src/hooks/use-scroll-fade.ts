import '@/components/ui/scroll-fade.css'

import { type RefObject, useLayoutEffect } from 'react'

import { observeScrollFade } from '@/lib/scroll-fade'

/** Enhance the existing scrollport, never replace its scroll/focus owner. */
export function useScrollFade(
  viewportRef: RefObject<HTMLElement | null>,
  contentRef?: RefObject<HTMLElement | null>,
  enabled = true
) {
  useLayoutEffect(() => {
    const viewport = viewportRef.current

    if (!enabled || !viewport) {
      return
    }

    return observeScrollFade(viewport, contentRef?.current ?? viewport.firstElementChild)
  }, [contentRef, enabled, viewportRef])
}
