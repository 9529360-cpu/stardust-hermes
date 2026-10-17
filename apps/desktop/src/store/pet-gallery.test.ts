import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as gatewayStore from './gateway'
import { $petInfo, type PetInfo, setPetInfo } from './pet'
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
import { $activeGatewayProfile } from './profile'

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
    $activeGatewayProfile.set('default')
  })

  afterEach(() => {
    resetPetGallery()
    $activeGatewayProfile.set('default')
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

    fullCatalog.resolve({ ...localGallery(), catalogComplete: true })
    await loading

    expect($petGallery.get()).toEqual({ ...localGallery(), catalogComplete: true })
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
    full.resolve({ ...complete, catalogComplete: true })
    await loading

    expect($petGallery.get()).toEqual({ ...complete, catalogComplete: true })
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })

  it('keeps installed pets when a successful full-catalog RPC reports the offline fallback', async () => {
    const local = localGallery()
    const fallback = deferred<PetGallery>()
    const { fullCatalogStarted, request } = requestWithDeferredFullCatalog(local, fallback.promise)
    const loading = loadPetGallery(request)

    await fullCatalogStarted
    fallback.resolve({ ...local, catalogComplete: false })
    await loading

    expect($petGallery.get()).toEqual({ ...local, catalogComplete: false })
    expect($petGalleryFullCatalogStatus.get()).toBe('error')
  })

  it('retries a degraded catalog after the next open and recovers when it becomes available', async () => {
    let fullCatalogCalls = 0
    const local = localGallery()

    const request: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.info.meta') {
        return { enabled: false } as T
      }

      if (method === 'pet.gallery' && params.localOnly) {
        return local as T
      }

      if (method === 'pet.gallery') {
        fullCatalogCalls += 1

        return {
          ...local,
          catalogComplete: fullCatalogCalls > 1
        } as T
      }

      throw new Error('Unexpected RPC ' + method)
    }

    await loadPetGallery(request)
    expect($petGalleryFullCatalogStatus.get()).toBe('error')

    await loadPetGallery(request)

    expect(fullCatalogCalls).toBe(2)
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })

  it('ignores a late full-catalog reply after reset and a new profile load', async () => {
    const staleFull = deferred<PetGallery>()

    const staleStarted = requestWithDeferredFullCatalog(
      { active: 'alpha', enabled: true, pets: [{ displayName: 'Alpha', installed: true, slug: 'alpha' }] },
      staleFull.promise
    )

    $activeGatewayProfile.set('alpha')

    const staleLoad = loadPetGallery(staleStarted.request)

    await staleStarted.fullCatalogStarted

    resetPetGallery()
    $activeGatewayProfile.set('beta')
    setPetInfo({ enabled: false })

    const betaGallery: PetGallery = {
      active: 'beta',
      enabled: true,
      catalogComplete: true,
      pets: [{ displayName: 'Beta', installed: true, slug: 'beta' }]
    }

    const betaRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.info.meta') {
        return { enabled: false } as T
      }

      if (method === 'pet.gallery' && params.localOnly) {
        return betaGallery as T
      }

      if (method === 'pet.gallery') {
        return betaGallery as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    await loadPetGallery(betaRequest)
    expect($petGallery.get()).toEqual(betaGallery)
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')

    staleFull.resolve({
      active: 'alpha',
      enabled: true,
      catalogComplete: false,
      pets: [{ displayName: 'Alpha', installed: true, slug: 'alpha' }]
    })
    await staleLoad

    expect($petGallery.get()).toEqual(betaGallery)
    expect($petGalleryStatus.get()).toBe('ready')
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })

  it('reloads the gallery when the active source changes but the profile name does not', async () => {
    let connectionId: null | string = 'source-a'
    vi.spyOn(gatewayStore, 'activeGatewayConnectionId').mockImplementation(() => connectionId)
    $activeGatewayProfile.set('shared-profile')

    const sourceAFull = deferred<PetGallery>()

    const sourceAStarted = requestWithDeferredFullCatalog(
      {
        active: 'source-a-pet',
        enabled: true,
        pets: [{ displayName: 'Source A', installed: true, slug: 'source-a-pet' }]
      },
      sourceAFull.promise
    )

    const sourceALoad = loadPetGallery(sourceAStarted.request)

    await sourceAStarted.fullCatalogStarted
    setPetInfo({
      enabled: true,
      slug: 'shared-pet',
      displayName: 'Shared Pet',
      scale: 0.5,
      spritesheetBase64: 'source-a-spritesheet',
      spritesheetRevision: 'shared-revision'
    })

    connectionId = 'source-b'

    const sourceBGallery: PetGallery = {
      active: 'source-b-pet',
      enabled: true,
      catalogComplete: true,
      pets: [{ displayName: 'Source B', installed: true, slug: 'source-b-pet' }]
    }

    let sourceBRequests = 0
    let sourceBInfoCalls = 0

    const sourceBRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.info.meta') {
        return {
          enabled: true,
          slug: 'shared-pet',
          displayName: 'Shared Pet',
          scale: 0.5,
          spritesheetRevision: 'shared-revision'
        } as T
      }

      if (method === 'pet.info') {
        sourceBInfoCalls += 1

        return {
          enabled: true,
          slug: 'shared-pet',
          displayName: 'Shared Pet',
          scale: 0.5,
          spritesheetBase64: 'source-b-spritesheet',
          spritesheetRevision: 'shared-revision'
        } as T
      }

      if (method === 'pet.gallery') {
        sourceBRequests += 1

        return sourceBGallery as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    await loadPetGallery(sourceBRequest)
    expect(sourceBRequests).toBe(2)
    expect(sourceBInfoCalls).toBe(1)
    expect($petInfo.get().spritesheetBase64).toBe('source-b-spritesheet')
    expect($petGallery.get()).toEqual(sourceBGallery)
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')

    sourceAFull.resolve({
      active: 'source-a-pet',
      enabled: true,
      catalogComplete: false,
      pets: [{ displayName: 'Source A', installed: true, slug: 'source-a-pet' }]
    })
    await sourceALoad

    expect($petGallery.get()).toEqual(sourceBGallery)
    expect($petGalleryStatus.get()).toBe('ready')
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })

  it('does not apply stale pet.info metadata after reset while the old local phase is pending', async () => {
    const oldMeta = deferred<{ enabled: boolean }>()
    let notifyOldMetaStarted!: () => void

    const oldMetaStarted = new Promise<void>(resolve => {
      notifyOldMetaStarted = resolve
    })

    const alphaLocal: PetGallery = {
      active: 'alpha',
      enabled: true,
      pets: [{ displayName: 'Alpha', installed: true, slug: 'alpha' }]
    }

    const alphaRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.gallery' && params.localOnly) {
        return alphaLocal as T
      }

      if (method === 'pet.info.meta') {
        notifyOldMetaStarted()

        return oldMeta.promise as T
      }

      if (method === 'pet.gallery') {
        return alphaLocal as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    $activeGatewayProfile.set('alpha')
    const alphaLoad = loadPetGallery(alphaRequest)

    await oldMetaStarted

    resetPetGallery()
    $activeGatewayProfile.set('beta')
    setPetInfo({ enabled: false })

    const betaGallery: PetGallery = {
      active: 'beta',
      enabled: true,
      catalogComplete: true,
      pets: [{ displayName: 'Beta', installed: true, slug: 'beta' }]
    }

    const betaPetInfo = {
      enabled: true,
      slug: 'beta',
      displayName: 'Beta',
      scale: 0.5,
      spritesheetBase64: 'beta-spritesheet',
      spritesheetRevision: 'beta-revision'
    }

    const betaRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.info.meta') {
        return {
          enabled: true,
          slug: 'beta',
          displayName: 'Beta',
          scale: 0.5,
          spritesheetRevision: 'beta-revision'
        } as T
      }

      if (method === 'pet.info') {
        return betaPetInfo as T
      }

      if (method === 'pet.gallery' && params.localOnly) {
        return betaGallery as T
      }

      if (method === 'pet.gallery') {
        return betaGallery as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    await loadPetGallery(betaRequest)
    expect($petGallery.get()).toEqual(betaGallery)
    expect($petInfo.get()).toMatchObject(betaPetInfo)

    oldMeta.resolve({ enabled: false })
    await alphaLoad

    expect($petGallery.get()).toEqual(betaGallery)
    expect($petGalleryStatus.get()).toBe('ready')
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
    expect($petInfo.get()).toMatchObject(betaPetInfo)
  })

  it('ignores a late legacy pet.info payload after reset to a new profile', async () => {
    const staleInfo = deferred<PetInfo>()
    let notifyStaleInfoStarted!: () => void

    const staleInfoStarted = new Promise<void>(resolve => {
      notifyStaleInfoStarted = resolve
    })

    const alphaGallery: PetGallery = {
      active: 'alpha',
      enabled: true,
      pets: [{ displayName: 'Alpha', installed: true, slug: 'alpha' }]
    }

    const alphaRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.gallery' && params.localOnly) {
        return alphaGallery as T
      }

      if (method === 'pet.info.meta') {
        throw new Error('JSON-RPC -32601: Method not found')
      }

      if (method === 'pet.info') {
        notifyStaleInfoStarted()

        return staleInfo.promise as T
      }

      if (method === 'pet.gallery') {
        return alphaGallery as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    $activeGatewayProfile.set('alpha')
    const alphaLoad = loadPetGallery(alphaRequest)

    await staleInfoStarted

    resetPetGallery()
    $activeGatewayProfile.set('beta')
    setPetInfo({ enabled: false })

    const betaGallery: PetGallery = {
      active: 'beta',
      enabled: true,
      catalogComplete: true,
      pets: [{ displayName: 'Beta', installed: true, slug: 'beta' }]
    }

    const betaPetInfo: PetInfo = {
      enabled: true,
      slug: 'beta',
      displayName: 'Beta',
      scale: 0.5,
      spritesheetBase64: 'beta-spritesheet',
      spritesheetRevision: 'beta-revision'
    }

    const betaRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.info.meta') {
        return {
          enabled: true,
          slug: 'beta',
          displayName: 'Beta',
          scale: 0.5,
          spritesheetRevision: 'beta-revision'
        } as T
      }

      if (method === 'pet.info') {
        return betaPetInfo as T
      }

      if (method === 'pet.gallery' && params.localOnly) {
        return betaGallery as T
      }

      if (method === 'pet.gallery') {
        return betaGallery as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    await loadPetGallery(betaRequest)
    expect($petInfo.get()).toMatchObject(betaPetInfo)

    staleInfo.resolve({
      enabled: true,
      slug: 'alpha',
      displayName: 'Alpha',
      scale: 0.25,
      spritesheetBase64: 'alpha-spritesheet',
      spritesheetRevision: 'alpha-revision'
    })
    await alphaLoad

    expect($petGallery.get()).toEqual(betaGallery)
    expect($petGalleryStatus.get()).toBe('ready')
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
    expect($petInfo.get()).toMatchObject(betaPetInfo)
  })

  it('does not let an old phase-one finally detach a replacement profile load', async () => {
    const alphaLocal = deferred<PetGallery>()
    let notifyAlphaLocalStarted!: () => void

    const alphaLocalStarted = new Promise<void>(resolve => {
      notifyAlphaLocalStarted = resolve
    })

    const alphaGallery: PetGallery = {
      active: 'alpha',
      enabled: true,
      pets: [{ displayName: 'Alpha', installed: true, slug: 'alpha' }]
    }

    const alphaRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.gallery' && params.localOnly) {
        notifyAlphaLocalStarted()

        return alphaLocal.promise as T
      }

      if (method === 'pet.info.meta') {
        return { enabled: false } as T
      }

      if (method === 'pet.gallery') {
        return alphaGallery as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    $activeGatewayProfile.set('alpha')
    const alphaLoad = loadPetGallery(alphaRequest)

    await alphaLocalStarted

    resetPetGallery()
    $activeGatewayProfile.set('beta')
    const betaLocal = deferred<PetGallery>()
    let betaLocalCalls = 0
    let notifyBetaLocalStarted!: () => void

    const betaLocalStarted = new Promise<void>(resolve => {
      notifyBetaLocalStarted = resolve
    })

    const betaGallery: PetGallery = {
      active: 'beta',
      enabled: true,
      catalogComplete: true,
      pets: [{ displayName: 'Beta', installed: true, slug: 'beta' }]
    }

    const betaRequest: GatewayRequest = async <T>(method: string, params: Record<string, unknown> = {}) => {
      if (method === 'pet.gallery' && params.localOnly) {
        betaLocalCalls += 1
        notifyBetaLocalStarted()

        return betaLocal.promise as T
      }

      if (method === 'pet.info.meta') {
        return { enabled: false } as T
      }

      if (method === 'pet.gallery') {
        return betaGallery as T
      }

      throw new Error(`Unexpected RPC ${method}`)
    }

    const betaLoad = loadPetGallery(betaRequest)

    await betaLocalStarted
    alphaLocal.reject(new Error('late alpha local-gallery failure'))
    await alphaLoad

    const joinedBetaLoad = loadPetGallery(betaRequest)
    expect(betaLocalCalls).toBe(1)
    expect($petGallery.get()).toBeNull()
    expect($petGalleryStatus.get()).toBe('loading')

    betaLocal.resolve(betaGallery)
    await Promise.all([betaLoad, joinedBetaLoad])

    expect($petGallery.get()).toEqual(betaGallery)
    expect($petGalleryStatus.get()).toBe('ready')
    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })

  it('keeps legacy successful catalog responses compatible when the completeness field is absent', async () => {
    const { fullCatalogStarted, request } = requestWithDeferredFullCatalog(
      localGallery(),
      Promise.resolve(localGallery())
    )

    await loadPetGallery(request)
    await fullCatalogStarted

    expect($petGalleryFullCatalogStatus.get()).toBe('ready')
  })
})
