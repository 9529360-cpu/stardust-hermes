import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setBackdrop, setBackdropImagePath } from '@/store/backdrop'

import { Backdrop } from './Backdrop'

const CUSTOM_IMAGE = 'data:image/jpeg;base64,Y3VzdG9t'

describe('Backdrop', () => {
  const originalDesktop = window.hermesDesktop

  beforeEach(() => {
    window.localStorage.clear()
    setBackdropImagePath(null)
    setBackdrop(false)
  })

  afterEach(() => {
    cleanup()
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: originalDesktop
    })
  })

  it('renders nothing while the backdrop is disabled', () => {
    const { container } = render(<Backdrop />)

    expect(container.querySelector('img')).toBeNull()
  })

  it('reads a custom background only through the local Electron file bridge', async () => {
    const readFileDataUrl = vi.fn(async () => CUSTOM_IMAGE)

    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { readFileDataUrl }
    })

    setBackdropImagePath('C:\\Pictures\\wallpaper.jpg')
    setBackdrop(true)

    const { container } = render(<Backdrop />)

    await waitFor(() => expect(readFileDataUrl).toHaveBeenCalledWith('C:\\Pictures\\wallpaper.jpg'))

    const image = container.querySelector('img')

    await waitFor(() => expect(image?.getAttribute('src')).toBe(CUSTOM_IMAGE))
    expect(container.querySelector('[data-custom-backdrop="true"]')).not.toBeNull()
  })

  it('falls back to the bundled backdrop when the local image cannot be read', async () => {
    const readFileDataUrl = vi.fn(async () => {
      throw new Error('missing')
    })

    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { readFileDataUrl }
    })

    setBackdropImagePath('/missing/wallpaper.jpg')
    setBackdrop(true)

    const { container } = render(<Backdrop />)

    await waitFor(() => expect(readFileDataUrl).toHaveBeenCalledWith('/missing/wallpaper.jpg'))

    const image = container.querySelector('img')

    expect(image?.getAttribute('src')).toContain('ds-assets/filler-bg0.jpg')
    expect(container.querySelector('[data-custom-backdrop="true"]')).toBeNull()
  })
})
