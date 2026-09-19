import { beforeEach, describe, expect, it } from 'vitest'

import {
  $pluginDecisions,
  $pluginRecords,
  pluginActive,
  type PluginKind,
  publishPlugin
} from './plugins-store'

beforeEach(() => {
  window.localStorage.clear()
  $pluginDecisions.set({})
  $pluginRecords.set({})
})

describe('desktop plugin trust defaults', () => {
  it('keeps non-bundled code disabled until the user explicitly enables it', () => {
    for (const kind of ['disk', 'runtime'] satisfies PluginKind[]) {
      const id = `external-${kind}`

      publishPlugin({ id, kind, name: id, status: 'disabled' })

      // Plugin metadata may ask to start enabled; the host trust boundary wins.
      expect(pluginActive(id, true)).toBe(false)

      $pluginDecisions.set({ [id]: true })
      expect(pluginActive(id, true)).toBe(true)

      $pluginDecisions.set({ [id]: false })
      expect(pluginActive(id, true)).toBe(false)
    }
  })

  it('preserves shipped defaults for bundled reviewed plugins', () => {
    publishPlugin({ id: 'bundled-on', kind: 'bundled', name: 'Bundled on', status: 'disabled' })
    publishPlugin({ id: 'bundled-off', kind: 'bundled', name: 'Bundled off', status: 'disabled' })

    expect(pluginActive('bundled-on', true)).toBe(true)
    expect(pluginActive('bundled-off', false)).toBe(false)
  })

  it('fails closed when a caller asks about code that is not inventoried yet', () => {
    expect(pluginActive('unknown-external', true)).toBe(false)
  })
})
