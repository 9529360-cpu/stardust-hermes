const GROUP = '[data-slot="aui_message-group"]'

const MESSAGE_ROOTS =
  '[data-slot="aui_user-message-root"], [data-slot="aui_assistant-message-root"], [data-slot="aui_system-message-root"]'

/** Find the first ordered box past an edge without waking every skipped turn. */
export function firstAfter(length: number, isAfter: (index: number) => boolean): number {
  let low = 0
  let high = length

  while (low < high) {
    const middle = (low + high) >>> 1

    if (isAfter(middle)) {
      high = middle
    } else {
      low = middle + 1
    }
  }

  return low
}

/** Layout reads finish before callers publish state; offscreen descendants stay skipped. */
export function observeVisibleGroups(
  viewport: HTMLElement,
  content: HTMLElement,
  schedule: () => void
): { visible: Set<HTMLElement>; dispose: () => void } {
  const visible = new Set<HTMLElement>()

  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        visible.add(entry.target as HTMLElement)
      } else {
        visible.delete(entry.target as HTMLElement)
      }
    }

    schedule()
  }, { root: viewport })

  for (const group of content.querySelectorAll<HTMLElement>(GROUP)) {
    observer.observe(group)
  }

  return { visible, dispose: () => observer.disconnect() }
}

export function countMessagesBelow(viewport: HTMLElement, content: HTMLElement, visible?: Set<HTMLElement>): number {
  const bottom = viewport.getBoundingClientRect().bottom
  const groups = content.querySelectorAll<HTMLElement>(GROUP)

  const first = visible
    ? Array.from(groups).findIndex(group => visible.has(group))
    : firstAfter(groups.length, index => groups[index]!.getBoundingClientRect().bottom > bottom + 1)

  if (first < 0) {
    return 0
  }

  let count = 0

  for (let index = first; index < groups.length; index++) {
    const group = groups[index]!
    const messages = group.querySelectorAll<HTMLElement>(MESSAGE_ROOTS)

    if (visible?.has(group) || index === first) {
      const rect = group.getBoundingClientRect()

      if (rect.bottom <= bottom + 1) {
        continue
      }

      if (rect.top < bottom) {
        for (const message of messages) {
          const messageRect = message.getBoundingClientRect()

          if (messageRect.height > 0 && messageRect.bottom > bottom + 1) {
            count++
          }
        }

        continue
      }
    }

    count += messages.length
  }

  return count
}

/** Sticky prompts preserve document order; find the last prompt at the top edge. */
export function activePromptIndex(viewport: HTMLElement, ids: readonly string[], visible?: Set<HTMLElement>): number {
  const top = viewport.getBoundingClientRect().top
  const bottom = viewport.getBoundingClientRect().bottom

  const currentVisible = visible && new Set(Array.from(visible).filter(group => {
    const rect = group.getBoundingClientRect()

    return rect.bottom > top && rect.top < bottom
  }))

  const mounted: { index: number; node: HTMLElement }[] = []

  const prompts = new Map(
    Array.from(viewport.querySelectorAll<HTMLElement>('[data-slot="aui_user-message-root"][data-message-id]')).map(node => [
      node.dataset.messageId,
      node
    ])
  )

  for (let index = 0; index < ids.length; index++) {
    const node = prompts.get(ids[index]!)

    if (node) {
      mounted.push({ index, node })
    }
  }

  if (visible) {
    const candidates = mounted.filter(item => currentVisible!.has(item.node.closest<HTMLElement>(GROUP)!))
    let active = mounted[0]?.index ?? 0

    for (const item of candidates) {
      if (item.node.getBoundingClientRect().top <= top + 8) {
        active = item.index
      }
    }

    return active
  }

  const after = firstAfter(mounted.length, index => mounted[index]!.node.getBoundingClientRect().top > top + 8)

  return mounted[Math.max(0, after - 1)]?.index ?? 0
}
