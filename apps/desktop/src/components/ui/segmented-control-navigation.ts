/** Navigation is local to the focused control; modified keys belong to the host. */
export function segmentedControlNextIndex(index: number, count: number, key: string, rtl = false): number | null {
  if (count <= 0) {
    return null
  }

  if (key === 'Home') {
    return 0
  }

  if (key === 'End') {
    return count - 1
  }

  if (key !== 'ArrowLeft' && key !== 'ArrowRight') {
    return null
  }

  const direction = (key === 'ArrowRight' ? 1 : -1) * (rtl ? -1 : 1)

  return (Math.max(0, index) + direction + count) % count
}
