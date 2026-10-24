import { type ReactNode, type RefObject, useEffect } from 'react'

import { publishThreadMessagesBelow } from '@/store/thread-scroll'

import { countMessagesBelow, observeVisibleGroups } from './thread-scroll-geometry'

export { countMessagesBelow } from './thread-scroll-geometry'

interface MessagesBelowOptions {
  contentRef: RefObject<HTMLElement | null>
  scrollRef: RefObject<HTMLElement | null>
  isAtBottom: boolean
  paneVisible: boolean
  rows: ReactNode
  sessionKey: string | null | undefined
}

export function useMessagesBelow({
  contentRef,
  scrollRef,
  isAtBottom,
  paneVisible,
  rows,
  sessionKey
}: MessagesBelowOptions) {
  useEffect(() => {
    if (!paneVisible) {
      return
    }

    if (isAtBottom) {
      publishThreadMessagesBelow(0, { paneVisible })

      return
    }

    const viewport = scrollRef.current
    const content = contentRef.current

    if (!viewport || !content) {
      return
    }

    let frame = 0

    const measure = () => {
      frame = 0
      publishThreadMessagesBelow(countMessagesBelow(viewport, content, visibility.visible), { paneVisible })
    }

    const schedule = () => {
      if (!frame) {
        frame = requestAnimationFrame(measure)
      }
    }

    const visibility = observeVisibleGroups(viewport, content, schedule)
    schedule()
    viewport.addEventListener('scroll', schedule, { passive: true })
    const observer = new ResizeObserver(schedule)
    observer.observe(viewport)
    observer.observe(content)

    return () => {
      cancelAnimationFrame(frame)
      viewport.removeEventListener('scroll', schedule)
      observer.disconnect()
      visibility.dispose()
    }
  }, [contentRef, scrollRef, isAtBottom, paneVisible, rows, sessionKey])
}
