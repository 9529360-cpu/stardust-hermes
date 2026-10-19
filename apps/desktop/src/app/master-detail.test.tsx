// @vitest-environment jsdom
import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MasterDetail } from './master-detail'

vi.mock('@nanostores/react', () => ({ useStore: () => undefined }))
vi.mock('@/hooks/use-pointer-resize', () => ({
  usePointerResize: () => (_event: unknown, options: { onPreview: (delta: number) => void }) => {
    options.onPreview(20)
  }
}))
vi.mock('@/hooks/use-scroll-fade', () => ({ useScrollFade: () => undefined }))

afterEach(cleanup)

describe('MasterDetail sash', () => {
  it('disables touch gestures and reverses horizontal drag delta under RTL', () => {
    const { container } = render(
      <div dir="rtl">
        <MasterDetail resizeId="test">
          <aside>list</aside>
          <main>detail</main>
        </MasterDetail>
      </div>
    )
    const sash = container.querySelector('.stardust-master-detail-sash') as HTMLDivElement
    const grid = container.querySelector('.stardust-master-detail-grid') as HTMLDivElement
    Object.defineProperties(grid, {
      getBoundingClientRect: { value: () => ({ width: 800 }) }
    })
    Object.defineProperties(grid.children[0], {
      getBoundingClientRect: { value: () => ({ width: 300 }) }
    })
    expect(sash.className).toContain('touch-none')
    fireEvent.pointerDown(sash, { button: 0, isPrimary: true })
    expect(grid.style.getPropertyValue('--md-drag-split')).toBe('280px')
  })
})
