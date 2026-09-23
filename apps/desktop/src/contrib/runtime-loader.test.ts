import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesReadDirResult } from '@/global'
import type * as HermesModule from '@/hermes'

import { $pluginDecisions, $pluginRecords, publishPlugin, setPluginEnabled } from './plugins-store'
import { discoverRuntimePlugins, loadRuntimePlugin, watchRuntimePlugins } from './runtime-loader'

// getStatus would supply the connected backend's hermes_home — a REMOTE path in
// remote mode. The disk scanner must NOT derive the plugin root from it (#66899).
const getStatus = vi.fn(async () => ({ hermes_home: '/remote/box/.hermes' }))

vi.mock('@/hermes', async importActual => ({
  ...(await importActual<typeof HermesModule>()),
  getStatus: () => getStatus()
}))

const desktopPluginsRoot = vi.fn<() => Promise<string>>()
const readDir = vi.fn<(path: string) => Promise<HermesReadDirResult>>()
const readFileText = vi.fn<(path: string) => Promise<{ text: string; truncated?: boolean }>>()
const readPluginSource = vi.fn<(path: string) => Promise<{ text: string; truncated?: boolean }>>()
const watchDirectory = vi.fn<(path: string) => Promise<{ id: string }>>()
const watchPreviewFile = vi.fn<(path: string) => Promise<{ id: string }>>()
const stopPreviewFileWatch = vi.fn<(id: string) => Promise<boolean>>()
const onPreviewFileChanged = vi.fn()

beforeEach(() => {
  $pluginDecisions.set({})
  $pluginRecords.set({})
  desktopPluginsRoot.mockReset()
  readDir.mockReset()
  readFileText.mockReset()
  readPluginSource.mockReset()
  watchDirectory.mockReset()
  watchPreviewFile.mockReset()
  stopPreviewFileWatch.mockReset()
  stopPreviewFileWatch.mockResolvedValue(true)
  onPreviewFileChanged.mockReset()
  getStatus.mockClear()
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    desktopPluginsRoot,
    onPreviewFileChanged,
    readDir,
    readFileText,
    stopPreviewFileWatch,
    watchDirectory,
    watchPreviewFile
  }
})

afterEach(() => {
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('scanDiskPlugins (#66899)', () => {
  it('scans the Electron-resolved local roots, never the backend hermes_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    readDir.mockResolvedValue({ entries: [] })

    await discoverRuntimePlugins()

    expect(desktopPluginsRoot).toHaveBeenCalled()
    expect(readDir).toHaveBeenCalledWith('/local/.hermes/desktop-plugins')
    // Unified halves are COPIED into the app root by Electron; the renderer
    // never scans the (profile-shaped) agent-plugins root itself.
    expect(readDir).not.toHaveBeenCalledWith('/local/.hermes/plugins')
    // The remote backend's hermes_home must never feed the local plugin scan.
    expect(getStatus).not.toHaveBeenCalled()
    expect(readDir).not.toHaveBeenCalledWith('/remote/box/.hermes/desktop-plugins')
  })

  it('no-ops when the resolvers yield no local root', async () => {
    desktopPluginsRoot.mockResolvedValue('')

    await discoverRuntimePlugins()

    expect(readDir).not.toHaveBeenCalled()
  })

  it('treats a folder without plugin.js as metadata, not a throwing file read', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    readDir.mockImplementation(async dir => {
      if (dir === '/local/.hermes/desktop-plugins') {
        return {
          entries: [{ isDirectory: true, name: 'my-feature', path: '/local/.hermes/desktop-plugins/my-feature' }]
        }
      }

      if (dir === '/local/.hermes/desktop-plugins/my-feature') {
        return {
          entries: [
            { isDirectory: false, name: 'README.md', path: '/local/.hermes/desktop-plugins/my-feature/README.md' }
          ]
        }
      }

      return { entries: [] }
    })

    await discoverRuntimePlugins()

    expect(readDir).toHaveBeenCalledWith('/local/.hermes/desktop-plugins/my-feature')
    expect(readFileText).not.toHaveBeenCalled()
  })

  it('a DIRECTORY named plugin.js is not a plugin entry (metadata walk rejects it)', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    readDir.mockImplementation(async dir => {
      if (dir === '/local/.hermes/desktop-plugins') {
        return { entries: [{ isDirectory: true, name: 'odd', path: '/local/.hermes/desktop-plugins/odd' }] }
      }

      if (dir === '/local/.hermes/desktop-plugins/odd') {
        // A folder literally named plugin.js — must resolve to "no entry".
        return {
          entries: [{ isDirectory: true, name: 'plugin.js', path: '/local/.hermes/desktop-plugins/odd/plugin.js' }]
        }
      }

      return { entries: [] }
    })

    await discoverRuntimePlugins()

    expect(readFileText).not.toHaveBeenCalled()
    expect($pluginRecords.get().odd).toBeUndefined()
  })

  it('loads a unified desktop half (app-root copy + package marker) OPT-IN and tags it with its package', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    let desktopEntryPresent = true
    const root = '/local/.hermes/desktop-plugins'

    readDir.mockImplementation(async dir => {
      if (dir === root) {
        return { entries: desktopEntryPresent ? [{ isDirectory: true, name: 'uni', path: `${root}/uni` }] : [] }
      }

      if (dir === `${root}/uni`) {
        return {
          entries: [
            { isDirectory: false, name: '.hermes-package.json', path: `${root}/uni/.hermes-package.json` },
            { isDirectory: false, name: 'plugin.js', path: `${root}/uni/plugin.js` }
          ]
        }
      }

      return { entries: [] }
    })

    const register = vi.fn()

    ;(globalThis as unknown as { __uniRegister: unknown }).__uniRegister = register
    ;(globalThis as unknown as { __uniTopLevelRuns: number }).__uniTopLevelRuns = 0
    readFileText.mockImplementation(async file =>
      file.endsWith('.hermes-package.json')
        ? { text: JSON.stringify({ package: 'uni-pkg', source: '/x/plugins/uni-pkg/desktop', sourceMtimeMs: 1 }) }
        : {
            text:
              'globalThis.__uniTopLevelRuns += 1; ' +
              'export default { id: "uni", register: globalThis.__uniRegister }'
          }
    )
    watchPreviewFile.mockResolvedValue({ id: 'w-uni' })

    // The loader evaluates plugins via blob-URL import(), which vite's module
    // runner can't resolve in tests — reroute to a data: URL, which node's
    // native ESM loader handles.
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockImplementation(
        blob =>
          `data:text/javascript;base64,${Buffer.from((blob as unknown as { parts: string[] }).parts.join('')).toString('base64')}`
      )

    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const RealBlob = globalThis.Blob
    vi.stubGlobal(
      'Blob',
      class {
        parts: string[]
        constructor(parts: string[]) {
          this.parts = parts
        }
      }
    )

    try {
      await discoverRuntimePlugins()

      // Inventoried for Capabilities → Plugins with its package identity, but
      // the unified posture wins: installed-but-inert until the user toggles.
      expect($pluginRecords.get().uni).toMatchObject({ kind: 'disk', status: 'disabled', packageName: 'uni-pkg' })
      expect(register).not.toHaveBeenCalled()
      expect((globalThis as unknown as { __uniTopLevelRuns: number }).__uniTopLevelRuns).toBe(0)

      // The user's explicit enable is the first point at which the module may
      // evaluate at all — top-level code has the same renderer authority as register().
      await setPluginEnabled('uni', true)
      expect((globalThis as unknown as { __uniTopLevelRuns: number }).__uniTopLevelRuns).toBe(1)
      expect(register).toHaveBeenCalledTimes(1)
      expect($pluginRecords.get().uni.status).toBe('loaded')

      // Electron removing the copy (package uninstalled) unloads the previous
      // Desktop registration instead of leaving a live ghost behind.
      desktopEntryPresent = false
      await discoverRuntimePlugins()
      expect($pluginRecords.get().uni).toBeUndefined()
      expect(stopPreviewFileWatch).toHaveBeenCalledWith('w-uni')
    } finally {
      createObjectURL.mockRestore()
      revokeObjectURL.mockRestore()
      vi.stubGlobal('Blob', RealBlob)
      delete (globalThis as unknown as { __uniRegister?: unknown }).__uniRegister
      delete (globalThis as unknown as { __uniTopLevelRuns?: unknown }).__uniTopLevelRuns
    }
  })
})

describe('watchRuntimePlugins dir watch (#66899)', () => {
  it('watches the Electron-resolved app root, never the backend hermes_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    readDir.mockResolvedValue({ entries: [] })
    watchDirectory.mockResolvedValue({ id: 'watch-1' })

    watchRuntimePlugins()
    // Drain the async scan + startDirWatches chains.
    await vi.waitFor(() => expect(watchDirectory).toHaveBeenCalledTimes(1))

    expect(watchDirectory).toHaveBeenCalledWith('/local/.hermes/desktop-plugins')
    expect(watchDirectory).not.toHaveBeenCalledWith('/remote/box/.hermes/desktop-plugins')
    expect(getStatus).not.toHaveBeenCalled()
  })
})

describe('plugin source reads (512 KiB preview-cap bug)', () => {
  const blobToDataUrl = () => {
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockImplementation(
        blob =>
          `data:text/javascript;base64,${Buffer.from((blob as unknown as { parts: string[] }).parts.join('')).toString('base64')}`
      )

    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const RealBlob = globalThis.Blob
    vi.stubGlobal(
      'Blob',
      class {
        parts: string[]
        constructor(parts: string[]) {
          this.parts = parts
        }
      }
    )

    return () => {
      createObjectURL.mockRestore()
      revokeObjectURL.mockRestore()
      vi.stubGlobal('Blob', RealBlob)
    }
  }

  /** Two-level standalone-root listing the metadata-walk probe needs:
   *  the root lists the package folder, the folder lists plugin.js. */
  const standaloneRootWith = (name: string) => {
    const folder = `/local/.hermes/desktop-plugins/${name}`

    readDir.mockImplementation(async dir => {
      if (dir === '/local/.hermes/desktop-plugins') {
        return { entries: [{ isDirectory: true, name, path: folder }] }
      }

      if (dir === folder) {
        return { entries: [{ isDirectory: false, name: 'plugin.js', path: `${folder}/plugin.js` }] }
      }

      return { entries: [] }
    })
  }

  it('inventories standalone full-source plugins inert until the user explicitly enables them', async () => {
    ;(window.hermesDesktop as unknown as { readPluginSource: unknown }).readPluginSource = readPluginSource
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    standaloneRootWith('big')
    // The preview read would truncate this source — it must never be used.
    readFileText.mockResolvedValue({ text: '// first 512 KiB only', truncated: true })

    const register = vi.fn()

    ;(globalThis as unknown as { __bigRegister: unknown }).__bigRegister = register
    ;(globalThis as unknown as { __bigTopLevelRuns: number }).__bigTopLevelRuns = 0
    readPluginSource.mockResolvedValue({
      text:
        'globalThis.__bigTopLevelRuns += 1; ' +
        'export default { id: "big-runtime", register: globalThis.__bigRegister }'
    })
    watchPreviewFile.mockResolvedValue({ id: 'w-big' })

    const restore = blobToDataUrl()

    try {
      await discoverRuntimePlugins()

      // Discovery is metadata-only: it must not even read/evaluate plugin.js.
      expect(readPluginSource).not.toHaveBeenCalled()
      expect(register).not.toHaveBeenCalled()
      expect((globalThis as unknown as { __bigTopLevelRuns: number }).__bigTopLevelRuns).toBe(0)
      expect($pluginRecords.get().big).toMatchObject({
        decisionId: 'big',
        kind: 'disk',
        status: 'disabled'
      })

      await setPluginEnabled('big', true)

      expect(readPluginSource).toHaveBeenCalledWith('/local/.hermes/desktop-plugins/big/plugin.js')
      expect((globalThis as unknown as { __bigTopLevelRuns: number }).__bigTopLevelRuns).toBe(1)
      expect(register).toHaveBeenCalledTimes(1)
      expect($pluginRecords.get()['big-runtime']).toMatchObject({
        decisionId: 'big',
        kind: 'disk',
        status: 'loaded'
      })
      expect($pluginRecords.get().big).toBeUndefined()

      // Toggling by the source-declared runtime id still persists trust against
      // the stable disk slot, so a rename inside plugin.js cannot mint trust.
      await setPluginEnabled('big-runtime', false)
      expect($pluginDecisions.get()).toMatchObject({ big: false })
      expect($pluginDecisions.get()['big-runtime']).toBeUndefined()
    } finally {
      restore()
      delete (globalThis as unknown as { __bigRegister?: unknown }).__bigRegister
      delete (globalThis as unknown as { __bigTopLevelRuns?: unknown }).__bigTopLevelRuns
    }
  })

  it('older shell without readPluginSource: a truncated preview read fails LOUDLY, never evaluates', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    standaloneRootWith('huge')
    // 512 KiB window of a larger file — parses fine, but is NOT the plugin.
    readFileText.mockResolvedValue({
      text: 'export default { id: "huge", register: () => { throw new Error("must never evaluate") } }',
      truncated: true
    })
    watchPreviewFile.mockResolvedValue({ id: 'w-huge' })

    const restore = blobToDataUrl()

    try {
      await discoverRuntimePlugins()

      // Discovery is safe metadata-only even on an older shell. The truncating
      // preview API is not touched until the user explicitly trusts execution.
      expect(readFileText).not.toHaveBeenCalled()
      expect($pluginRecords.get().huge).toMatchObject({
        decisionId: 'huge',
        kind: 'disk',
        status: 'disabled',
        file: '/local/.hermes/desktop-plugins/huge/plugin.js'
      })

      await setPluginEnabled('huge', true)

      expect($pluginRecords.get().huge).toMatchObject({
        kind: 'disk',
        status: 'error',
        file: '/local/.hermes/desktop-plugins/huge/plugin.js'
      })
      expect($pluginRecords.get().huge.error).toMatch(/512 KiB/)
    } finally {
      restore()
    }
  })

  it('older shell inventories a small standalone plugin inert, then explicit enable loads it', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.hermes/desktop-plugins')
    standaloneRootWith('small')

    const register = vi.fn()

    ;(globalThis as unknown as { __smallRegister: unknown }).__smallRegister = register
    ;(globalThis as unknown as { __smallTopLevelRuns: number }).__smallTopLevelRuns = 0
    readFileText.mockResolvedValue({
      text:
        'globalThis.__smallTopLevelRuns += 1; ' +
        'export default { id: "small", register: globalThis.__smallRegister }'
    })
    watchPreviewFile.mockResolvedValue({ id: 'w-small' })

    const restore = blobToDataUrl()

    try {
      await discoverRuntimePlugins()

      expect(readFileText).not.toHaveBeenCalled()
      expect(register).not.toHaveBeenCalled()
      expect((globalThis as unknown as { __smallTopLevelRuns: number }).__smallTopLevelRuns).toBe(0)
      expect($pluginRecords.get().small).toMatchObject({ kind: 'disk', status: 'disabled' })

      await setPluginEnabled('small', true)

      expect((globalThis as unknown as { __smallTopLevelRuns: number }).__smallTopLevelRuns).toBe(1)
      expect(register).toHaveBeenCalledTimes(1)
      expect($pluginRecords.get().small).toMatchObject({ kind: 'disk', status: 'loaded' })
    } finally {
      restore()
      delete (globalThis as unknown as { __smallRegister?: unknown }).__smallRegister
      delete (globalThis as unknown as { __smallTopLevelRuns?: unknown }).__smallTopLevelRuns
    }
  })
})

describe('runtime evaluation primitive', () => {
  it('defers register when its trusted caller supplies an opt-in default', async () => {
    const register = vi.fn()

    ;(globalThis as unknown as { __directRuntimeRegister: unknown }).__directRuntimeRegister = register

    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockImplementation(
        blob =>
          `data:text/javascript;base64,${Buffer.from((blob as unknown as { parts: string[] }).parts.join('')).toString('base64')}`
      )
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const RealBlob = globalThis.Blob
    vi.stubGlobal(
      'Blob',
      class {
        parts: string[]
        constructor(parts: string[]) {
          this.parts = parts
        }
      }
    )

    try {
      const id = await loadRuntimePlugin(
        'export default { id: "direct-runtime", register: globalThis.__directRuntimeRegister }',
        'direct-runtime'
      )

      expect(id).toBe('direct-runtime')
      expect(register).not.toHaveBeenCalled()
      expect($pluginRecords.get()['direct-runtime']).toMatchObject({ status: 'disabled' })

      await setPluginEnabled('direct-runtime', true)

      expect(register).toHaveBeenCalledTimes(1)
      expect($pluginRecords.get()['direct-runtime']).toMatchObject({ status: 'loaded' })
    } finally {
      createObjectURL.mockRestore()
      revokeObjectURL.mockRestore()
      vi.stubGlobal('Blob', RealBlob)
      delete (globalThis as unknown as { __directRuntimeRegister?: unknown }).__directRuntimeRegister
    }
  })
})

describe('bundled-shadowed disk copies', () => {
  it('skips a disk copy of a bundled plugin but publishes a visible inventory row', async () => {
    // The bundled twin is already registered (build-time glob).
    publishPlugin({ id: 'hermes-bots', name: 'Bot Mode', kind: 'bundled', status: 'loaded' })

    // Same blob→data: URL reroute as the opt-in test above.
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockImplementation(
        blob =>
          `data:text/javascript;base64,${Buffer.from((blob as unknown as { parts: string[] }).parts.join('')).toString('base64')}`
      )

    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const RealBlob = globalThis.Blob
    vi.stubGlobal(
      'Blob',
      class {
        parts: string[]
        constructor(parts: string[]) {
          this.parts = parts
        }
      }
    )

    try {
      const id = await loadRuntimePlugin(
        'export default { id: "hermes-bots", name: "Bot Mode", register() {} }',
        'hermes-bots',
        { file: '/local/.hermes/desktop-plugins/hermes-bots/plugin.js' }
      )

      // Skipped — the bundled copy stays the only live registration...
      expect(id).toBeNull()
      expect($pluginRecords.get()['hermes-bots']).toMatchObject({ kind: 'bundled', status: 'loaded' })

      // ...but the stale folder is DISCOVERABLE: an inventory row names it,
      // carries its path (reveal/delete affordance), and can never activate.
      expect($pluginRecords.get()['hermes-bots:disk-shadowed']).toMatchObject({
        kind: 'disk',
        status: 'disabled',
        file: '/local/.hermes/desktop-plugins/hermes-bots/plugin.js'
      })
    } finally {
      createObjectURL.mockRestore()
      revokeObjectURL.mockRestore()
      vi.stubGlobal('Blob', RealBlob)
    }
  })
})
