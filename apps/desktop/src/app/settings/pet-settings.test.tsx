import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, TRANSLATIONS } from '@/i18n'
import { $petInfo, $petRoam } from '@/store/pet'
import {
  $petBusy,
  $petGallery,
  $petGalleryError,
  $petGalleryFullCatalogStatus,
  $petGalleryStatus,
  type PetGallery
} from '@/store/pet-gallery'
import { $gatewayState } from '@/store/session'

import { PetSettings } from './pet-settings'

const { requestGatewayMock } = vi.hoisted(() => ({ requestGatewayMock: vi.fn() }))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway: requestGatewayMock })
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })

  return { promise, reject, resolve }
}

function requestWithDeferredFullCatalog(
  fullCatalog: Promise<PetGallery>,
  localGallery: PetGallery = { active: '', enabled: false, pets: [] }
) {
  let notifyFullCatalogStarted!: () => void

  const fullCatalogStarted = new Promise<void>(resolve => {
    notifyFullCatalogStarted = resolve
  })

  requestGatewayMock.mockImplementation((method: string, params: Record<string, unknown> = {}) => {
    if (method === 'pet.gallery' && params.localOnly) {
      return Promise.resolve(localGallery)
    }

    if (method === 'pet.info.meta') {
      return Promise.resolve({ enabled: false })
    }

    if (method === 'pet.gallery') {
      notifyFullCatalogStarted()

      return fullCatalog
    }

    if (method === 'pet.thumb') {
      return Promise.resolve({ dataUri: 'data:image/png;base64,ok', ok: true })
    }

    return Promise.reject(new Error(`Unexpected RPC ${method}`))
  })

  return fullCatalogStarted
}

afterEach(() => {
  cleanup()
  $petGallery.set(null)
  $petGalleryError.set(null)
  $petGalleryFullCatalogStatus.set('idle')
  $petGalleryStatus.set('idle')
  $petBusy.set(null)
  $petInfo.set({ enabled: false })
  $petRoam.set(false)
  $gatewayState.set('idle')
})

beforeEach(() => {
  // Keep the component away from the live gateway path; these tests exercise
  // only the renderer's catalog/search state.
  $gatewayState.set('idle')
  $petGallery.set(null)
  $petGalleryError.set(null)
  $petGalleryFullCatalogStatus.set('idle')
  $petGalleryStatus.set('error')
  $petBusy.set(null)
  $petInfo.set({ enabled: false })
  $petRoam.set(false)
  requestGatewayMock.mockReset()
})

function renderPetSettings() {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <PetSettings />
    </I18nProvider>
  )
}

describe('Settings pet catalog search', () => {
  const copy = TRANSLATIONS.en.settings.appearance.pet

  it('hides search when the catalog is unavailable and keeps the unavailable message', () => {
    renderPetSettings()

    expect(screen.queryByRole('textbox', { name: copy.searchPlaceholder })).toBeNull()
    expect(screen.getByText(copy.unreachable)).toBeTruthy()
  })

  it('keeps search hidden and the loading skeleton visible before the first gallery arrives', () => {
    $petGalleryStatus.set('loading')

    const view = renderPetSettings()

    expect(screen.queryByRole('textbox', { name: copy.searchPlaceholder })).toBeNull()
    expect(view.container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
    expect(screen.queryByText(copy.unreachable)).toBeNull()
  })

  it('keeps an empty local gallery loading until the deferred full catalog succeeds', async () => {
    const fullCatalog = deferred<PetGallery>()
    const fullCatalogStarted = requestWithDeferredFullCatalog(fullCatalog.promise)
    $gatewayState.set('open')

    const view = renderPetSettings()

    await act(async () => {
      await fullCatalogStarted
    })
    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('loading'))

    expect($petGalleryStatus.get()).toBe('ready')
    expect(screen.queryByRole('textbox', { name: copy.searchPlaceholder })).toBeNull()
    expect(view.container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
    expect(screen.queryByText(copy.unreachable)).toBeNull()

    await act(async () => {
      fullCatalog.resolve({
        active: '',
        enabled: false,
        catalogComplete: true,
        pets: [{ displayName: 'Axolotl', installed: false, slug: 'axolotl' }]
      })
      await fullCatalog.promise
    })

    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('ready'))
    expect(screen.getByRole('textbox', { name: copy.searchPlaceholder })).toBeTruthy()
    expect(screen.queryByText(copy.unreachable)).toBeNull()
  })

  it('shows unavailable only after the deferred full catalog request fails', async () => {
    const fullCatalog = deferred<PetGallery>()
    const fullCatalogStarted = requestWithDeferredFullCatalog(fullCatalog.promise)
    $gatewayState.set('open')

    const view = renderPetSettings()

    await act(async () => {
      await fullCatalogStarted
    })
    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('loading'))
    expect(screen.queryByText(copy.unreachable)).toBeNull()

    await act(async () => {
      fullCatalog.reject(new Error('Petdex is offline'))
      await fullCatalog.promise.catch(() => undefined)
    })

    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('error'))
    expect($petGalleryStatus.get()).toBe('ready')
    expect(screen.queryByRole('textbox', { name: copy.searchPlaceholder })).toBeNull()
    expect(view.container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0)
    expect(screen.getByText(copy.unreachable)).toBeTruthy()
  })

  it('shows the installed-only warning when a successful RPC reports the offline fallback', async () => {
    const installedOnly: PetGallery = {
      active: 'axolotl',
      enabled: true,
      pets: [{ displayName: 'Axolotl', installed: true, slug: 'axolotl' }]
    }

    const fullCatalog = deferred<PetGallery>()
    const fullCatalogStarted = requestWithDeferredFullCatalog(fullCatalog.promise, installedOnly)
    $gatewayState.set('open')

    renderPetSettings()

    await act(async () => {
      await fullCatalogStarted
    })
    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('loading'))

    await act(async () => {
      fullCatalog.resolve({ ...installedOnly, catalogComplete: false })
      await fullCatalog.promise
    })

    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('error'))

    expect(screen.getByRole('textbox', { name: copy.searchPlaceholder })).toBeTruthy()
    expect(screen.getByText(copy.unreachable)).toBeTruthy()
    expect(screen.getByText('Axolotl')).toBeTruthy()
  })

  it('shows the unavailable message for an empty successful offline fallback', async () => {
    const fullCatalog = deferred<PetGallery>()
    const fullCatalogStarted = requestWithDeferredFullCatalog(fullCatalog.promise)
    $gatewayState.set('open')

    const view = renderPetSettings()

    await act(async () => {
      await fullCatalogStarted
    })
    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('loading'))

    await act(async () => {
      fullCatalog.resolve({ active: '', enabled: false, pets: [], catalogComplete: false })
      await fullCatalog.promise
    })

    await waitFor(() => expect($petGalleryFullCatalogStatus.get()).toBe('error'))
    expect(view.container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0)
    expect(screen.queryByRole('textbox', { name: copy.searchPlaceholder })).toBeNull()
    expect(screen.getByText(copy.unreachable)).toBeTruthy()
  })

  it('keeps search for an available catalog and reports a query with no matches separately', () => {
    $petGallery.set({
      active: '',
      enabled: false,
      catalogComplete: true,
      pets: [{ displayName: 'Axolotl', installed: false, slug: 'axolotl' }]
    })
    $petGalleryStatus.set('ready')

    renderPetSettings()

    const search = screen.getByRole('textbox', { name: copy.searchPlaceholder })
    fireEvent.change(search, { target: { value: 'not-in-catalog' } })

    expect(screen.getByText(copy.noMatch('not-in-catalog'))).toBeTruthy()
    expect(screen.queryByText(copy.unreachable)).toBeNull()
  })
})
