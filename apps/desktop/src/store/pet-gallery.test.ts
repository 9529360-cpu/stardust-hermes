import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $petInfo, setPetInfo } from './pet'
import {
  $petGallery,
  $petGalleryFullCatalogStatus,
  $petGalleryStatus,
  adoptPet,
  type GatewayRequest,
  loadPetGallery,
  type PetGallery,
  resetPetGallery
} from './pet-gallery'

function localGallery() {
  return {
    enabled: true,
    active: 'boba',
    pets: [{ slug: 'boba', displayName: 'Boba', installed: true }]
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })

  return { promise, reject, resolve }
}

function requestWithDeferredFullCatalog(local: PetGallery, fullCatalog: Promise<PetGallery>) {
  let notifyFullCatalogStarted!: () => void

  const fullCatalogStarted = new Promise<void>(resolve => {
    notifyFullCatalogStarted = resolve
  })

  const request: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
    if (method === 'pet.info.meta') {
      return { enabled: false } as T
    }

    if (method === 'pet.gallery' && params.localOnly) {
      return local as T
    }

    if (method === 'pet.gallery') {
      notifyFullCatalogStarted()

      return fullCatalog as T
    }

    throw new Error(`Unexpected RPC ${method}`)
  }

  return { fullCatalogStarted, request }
}

describe('pet gallery pet.info sync', () => {
  beforeEach(() => {
    resetPetGallery()
    setPetInfo({ enabled: false })
  })

  afterEach(() => {
    resetPetGallery()
    setPetInfo({ enabled: false })
    vi.restoreAllMocks()
  })

  it('uses pet.info.meta and keeps the cached spritesheet when the revision is current', async () => {
    setPetInfo({
      enabled: true,
      slug: 'boba',
      displayName: 'Old Boba',
      scale: 0.33,
      spritesheetBase64: 'large-sprite-payload',
      spritesheetRevision: '100:2048',
      frameW: 192,
      frameH: 208
    })

    const requestMock = vi.fn(async (method: string) => {
      if (method === 'pet.gallery') {
        return localGallery()
      }

      if (method === 'pet.info.meta') {
        return {
          enabled: true,
          slug: 'boba',
          displayName: 'Boba',
          scale: 0.5,
          spritesheetRevision: '100:2048'
        }
      }

      if (method === 'pet.info') {
        throw new Error('full pet.info should not be called for an unchanged sprite')
      }

      throw new Error(`unexpected method: ${method}`)
    })

    const request = requestMock as unknown as GatewayRequest

    await loadPetGallery(request)

    const methods = requestMock.mock.calls.map(([method]) => method)
    expect(methods).toContain('pet.info.meta')
    expect(methods).not.toContain('pet.info')
    expect($petInfo.get()).toMatchObject({
      enabled: true,
      slug: 'boba',
      displayName: 'Boba',
      scale: 0.5,
      spritesheetBase64: 'large-sprite-payload',
      spritesheetRevision: '100:2048',
      frameW: 192,
      frameH: 208
    })
  })

  it('fetches full pet.info when metadata reports a new spritesheet revision', async () => {
    setPetInfo({
      enabled: true,
      slug: 'boba',
      displayName: 'Boba',
      scale: 0.33,
      spritesheetBase64: 'old-sprite-payload',
      spritesheetRevision: '100:2048'
    })

    const requestMock = vi.fn(async (method: string) => {
      if (method === 'pet.gallery') {
        return localGallery()
      }

      if (method === 'pet.info.meta') {
        return {
          enabled: true,
          slug: 'boba',
          displayName: 'Boba',
          scale: 0.33,
          spritesheetRevision: '101:4096'
        }
      }

      if (method === 'pet.info') {
        return {
          enabled: true,
          slug: 'boba',
          displayName: 'Boba',
          scale: 0.33,
          spritesheetBase64: 'new-sprite-payload',
          spritesheetRevision: '101:4096'
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    const request = requestMock as unknown as GatewayRequest

    await loadPetGallery(request)

    const methods = requestMock.mock.calls.map(([method]) => method)
    expect(methods).toContain('pet.info.meta')
    expect(methods).toContain('pet.info')
    expect($petInfo.get().spritesheetBase64).toBe('new-sprite-payload')
    expect($petInfo.get().spritesheetRevision).toBe('101:4096')
  })

  it('falls back to full pet.info when an older gateway lacks metadata', async () => {
    const requestMock = vi.fn(async (method: string) => {
      if (method === 'pet.gallery') {
        return localGallery()
      }

      if (method === 'pet.info.meta') {
        throw new Error('JSON-RPC -32601: Method not found')
      }

      if (method === 'pet.info') {
        return {
          enabled: true,
          slug: 'boba',
          displayName: 'Boba from legacy gateway',
          scale: 0.4,
          spritesheetBase64: 'legacy-full-payload',
          spritesheetRevision: '99:1024'
        }
      }

      throw new Error(`unexpected method: ${method}`)
    })

    const request = requestMock as unknown as GatewayRequest

    await loadPetGallery(request)

    const methods = requestMock.mock.calls.map(([method]) => method)
    expect(methods).toContain('pet.info.meta')
    expect(methods).toContain('pet.info')
    expect($petInfo.get()).toMatchObject({
      enabled: true,
      slug: 'boba',
      displayName: 'Boba from legacy gateway',
      spritesheetBase64: 'legacy-full-payload',
      spritesheetRevision: '99:1024'
    })
  })

  it('keeps mutation sync on metadata when the selected pet sprite is unchanged', async () => {
    $petGallery.set(localGallery())
    setPetInfo({
      enabled: true,
      slug: 'boba',
      displayName: 'Boba',
      scale: 0.33,
      spritesheetBase64: 'large-sprite-payload',
      spritesheetRevision: '100:2048'
    })

    const requestMock = vi.fn(async (method: string) => {
      if (method === 'pet.select') {
        return { ok: true, slug: 'boba', displayName: 'Boba' }
      }

      if (method === 'pet.info.meta') {
        return {
          enabled: true,
          slug: 'boba',
          displayName: 'Boba',
          scale: 0.33,
          spritesheetRevision: '100:2048'
        }
      }

      if (method === 'pet.info') {
        throw new Error('full pet.info should not be called after an unchanged select')
      }

      throw new Error(`unexpected method: ${method}`)
    })

    const request = requestMock as unknown as GatewayRequest

    await expect(adoptPet(request, 'boba', 'Could not adopt pet.')).resolves.toBe(true)

    const methods = requestMock.mock.calls.map(([method]) => method)
    expect(methods).toEqual(['pet.select', 'pet.info.meta'])
    expect($petInfo.get().spritesheetBase64).toBe('large-sprite-payload')
  })
})

describe('pet gallery full catalog loading', () => {
  beforeEach(() => {
    resetPetGallery()
  })

  afterEach(() => {
    resetPetGallery()
    vi.restoreAllMocks()
  })

  it('tracks a deferred full catalog separately while the local-only gallery is empty', async () => {
    const fullCatalog = deferred<PetGallery>()

    const { fullCatalogStarted, request } = requestWithDeferredFullCatalog(
      { active: '', enabled: false, pets: [] },
      fullCatalog.promise
    )

    const loading = loadPetGallery(request)

    await fullCatalogStarted

    expect($petGallery.get()).toEqual({ active: '', enabled: false, pets: [] })
    expect($petGalleryStatus.get()).toBe('ready')
    expect($petGalleryFullCatalogStatus.get()).toBe('loading')

    fullCatalog.resolve(localGallery())
    await loading

    expect($petGallery.get()).toEqual(localGallery())
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })

  it('keeps non-empty local pets ready while the deferred full catalog refreshes', async () => {
    const local = localGallery()
    const full = deferred<PetGallery>()
    const { fullCatalogStarted, request } = requestWithDeferredFullCatalog(local, full.promise)
    const loading = loadPetGallery(request)

    await fullCatalogStarted

    expect($petGallery.get()).toEqual(local)
    expect($petGalleryStatus.get()).toBe('ready')
    expect($petGalleryFullCatalogStatus.get()).toBe('loading')

    const complete = { ...local, pets: [...local.pets, { displayName: 'Orbit', installed: false, slug: 'orbit' }] }
    full.resolve(complete)
    await loading

    expect($petGallery.get()).toEqual(complete)
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })
})
