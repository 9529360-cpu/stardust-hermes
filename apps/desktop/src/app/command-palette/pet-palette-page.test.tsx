import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $petGallery, $petGalleryFullCatalogStatus, $petGalleryStatus, resetPetGallery } from '@/store/pet-gallery'

import { PetPalettePage } from './pet-palette-page'

const { requestGateway } = vi.hoisted(() => ({ requestGateway: vi.fn(() => new Promise(() => {})) }))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

beforeEach(() => {
  resetPetGallery()
  requestGateway.mockClear()
})

afterEach(() => {
  cleanup()
  resetPetGallery()
})

function renderPalette(search = '') {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <PetPalettePage search={search} />
    </I18nProvider>
  )
}

describe('palette pet catalog availability', () => {
  it.each([
    { phase: 'loading' as const, message: 'Loading petdex gallery…' },
    { phase: 'error' as const, message: 'Could not reach the petdex gallery.' }
  ])('does not report no matches while the full catalog is $phase', ({ phase, message }) => {
    $petGallery.set({ active: '', enabled: false, pets: [], catalogComplete: false })
    $petGalleryStatus.set('ready')
    $petGalleryFullCatalogStatus.set(phase)

    renderPalette()

    expect(screen.queryByText('No matching pets.')).toBeNull()
    expect(screen.getByText(message)).toBeTruthy()
  })

  it('waits for the full catalog before declaring a query absent from installed pets', () => {
    $petGallery.set({
      active: 'mine',
      enabled: true,
      pets: [{ slug: 'mine', displayName: 'My companion', installed: true }],
      catalogComplete: false
    })
    $petGalleryStatus.set('ready')
    $petGalleryFullCatalogStatus.set('loading')

    renderPalette('not-installed')

    expect(screen.queryByText('No matching pets.')).toBeNull()
    expect(screen.getByText('Loading petdex gallery…')).toBeTruthy()
  })

  it('keeps installed pets usable while explaining an unavailable full catalog', () => {
    $petGallery.set({
      active: 'mine',
      enabled: true,
      pets: [{ slug: 'mine', displayName: 'My companion', installed: true }],
      catalogComplete: false
    })
    $petGalleryStatus.set('ready')
    $petGalleryFullCatalogStatus.set('error')

    renderPalette()

    expect(screen.getByRole('option', { name: /My companion/ })).toBeTruthy()
    expect(screen.getByText('Could not reach the petdex gallery.')).toBeTruthy()
  })
})
