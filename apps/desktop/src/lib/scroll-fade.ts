/** Edge geometry adapted from open-claude-ui (MIT); no scroll ownership or React state.
 * See apps/desktop/THIRD_PARTY_NOTICES/open-claude-ui.txt. */
export interface ScrollFadeMetrics {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

export function scrollFadeEdges({ scrollTop, scrollHeight, clientHeight }: ScrollFadeMetrics, size = 28) {
  const height = Math.max(0, clientHeight)
  const extent = Math.max(0, scrollHeight - height)
  const top = Math.min(extent, Math.max(0, scrollTop))
  const fade = Math.max(0, Math.min(size, height / 2))

  return {
    top: top <= 1 ? 0 : Math.min(fade, top),
    bottom: extent - top <= 1 ? 0 : Math.min(fade, extent - top)
  }
}

/** Observe content as well as the viewport: streaming, disclosure, images and
 * font reflow can change scrollHeight without ever resizing the viewport.
 * A single RAF coalesces geometry reads. This never writes scrollTop, wraps a
 * scrollport, publishes to a store, or adds a per-frame React subscription. */
export function observeScrollFade(viewport: HTMLElement, content: Element | null, size = 28): () => void {
  const view = viewport.ownerDocument.defaultView
  if (!view) {
    return () => {}
  }
  const ResizeObserverImpl = view.ResizeObserver ?? globalThis.ResizeObserver
  const MutationObserverImpl = view.MutationObserver

  let frame: number | null = null
  let disposed = false
  let previous = ''

  const update = () => {
    frame = null

    if (disposed) {
      return
    }

    const edges = scrollFadeEdges(viewport, size)
    const key = `${edges.top}:${edges.bottom}`

    if (key === previous) {
      return
    }

    previous = key
    viewport.style.setProperty('--scroll-fade-top', `${edges.top}px`)
    viewport.style.setProperty('--scroll-fade-bottom', `${edges.bottom}px`)
    viewport.toggleAttribute('data-scroll-fade-active', edges.top > 0 || edges.bottom > 0)
  }

  const schedule = () => {
    if (!disposed && frame === null) {
      frame = view.requestAnimationFrame(update)
    }
  }

  viewport.setAttribute('data-scroll-fade', '')
  const resize = typeof ResizeObserverImpl === 'function' ? new ResizeObserverImpl(schedule) : null
  resize?.observe(viewport)

  if (content) {
    resize?.observe(content)
  }

  const mutations = typeof MutationObserverImpl === 'function' ? new MutationObserverImpl(schedule) : null

  if (content) {
    mutations?.observe(content, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['class', 'style', 'hidden', 'open']
    })
  }

  viewport.addEventListener('scroll', schedule, { passive: true })
  viewport.addEventListener('load', schedule, true)
  update()

  return () => {
    disposed = true

    if (frame !== null) {
      view.cancelAnimationFrame(frame)
    }

    resize?.disconnect()
    mutations?.disconnect()
    viewport.removeEventListener('scroll', schedule)
    viewport.removeEventListener('load', schedule, true)
    viewport.removeAttribute('data-scroll-fade')
    viewport.removeAttribute('data-scroll-fade-active')
    viewport.style.removeProperty('--scroll-fade-top')
    viewport.style.removeProperty('--scroll-fade-bottom')
  }
}
