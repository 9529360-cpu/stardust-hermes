import { describe, expect, it, vi } from 'vitest'

vi.mock('@/hermes', () => ({
  getHermesConfigRecord: vi.fn(async () => ({})),
  saveHermesConfig: vi.fn(async () => undefined)
}))

import { saveHermesConfig } from '@/hermes'

import { $voiceStopPhrase, applyVoiceStopPhraseFromConfig } from './voice-prefs'

/**
 * A `Storage` double the test fully owns, installed in place of whichever
 * `localStorage` the ambient environment happens to provide.
 *
 * `vi.spyOn(localStorage, 'setItem')` looked right but silently didn't
 * intercept anything here: depending on the Node/jsdom combination running
 * the suite, the ambient `localStorage` can be a real, Proxy-backed `Storage`
 * (see the Node-26 note in vitest.setup.ts) whose `setItem` isn't an own,
 * spy-able property the way a plain object's is — the spy's mock was called
 * zero times while the real write still went through underneath it. Owning
 * the object end to end sidesteps that uncertainty instead of depending on
 * which Storage implementation happens to be active.
 */
function installFakeStorage(): { restore: () => void; setFailure: (error: null | Error) => void } {
  const store = new Map<string, string>()
  let failure: Error | null = null

  const storage: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: key => store.get(key) ?? null,
    key: index => [...store.keys()][index] ?? null,
    removeItem: key => void store.delete(key),
    setItem: (key, value) => {
      if (failure) {
        throw failure
      }

      store.set(key, value)
    }
  }

  const descriptors = {
    globalThis: Object.getOwnPropertyDescriptor(globalThis, 'localStorage'),
    window: Object.getOwnPropertyDescriptor(window, 'localStorage')
  }

  for (const target of [globalThis, window]) {
    Object.defineProperty(target, 'localStorage', { configurable: true, value: storage, writable: true })
  }

  return {
    restore: () => {
      for (const [target, descriptor] of [
        [globalThis, descriptors.globalThis],
        [window, descriptors.window]
      ] as const) {
        if (descriptor) {
          Object.defineProperty(target, 'localStorage', descriptor)
        }
      }
    },
    setFailure: error => {
      failure = error
    }
  }
}

it('keeps the desktop toggle local across config refreshes', async () => {
  for (const fails of [false, true]) {
    for (const enabled of [false, true]) {
      const fake = installFakeStorage()

      try {
        vi.resetModules()
        const prefs = await import('./voice-prefs')

        fake.setFailure(fails ? new DOMException('Full', 'QuotaExceededError') : null)
        vi.mocked(saveHermesConfig).mockClear()

        await prefs.setAutoSpeakReplies(enabled)
        prefs.applyAutoSpeakFromConfig({ voice: { auto_tts: !enabled } })
        expect(prefs.$autoSpeakReplies.get()).toBe(enabled)
        expect(saveHermesConfig).not.toHaveBeenCalled()
        expect(localStorage.getItem('hermes.desktop.autoSpeakReplies')).toBe(fails ? null : String(enabled))
      } finally {
        fake.restore()
      }
    }
  }
})

it('migrates the legacy preference once, not on every refresh', async () => {
  for (const fails of [false, true]) {
    for (const enabled of [false, true]) {
      const fake = installFakeStorage()

      try {
        vi.resetModules()
        const prefs = await import('./voice-prefs')

        fake.setFailure(fails ? new DOMException('Denied', 'SecurityError') : null)

        prefs.applyAutoSpeakFromConfig(null)
        expect(localStorage.getItem('hermes.desktop.autoSpeakReplies')).toBeNull()
        prefs.applyAutoSpeakFromConfig({ voice: { auto_tts: enabled } })
        prefs.applyAutoSpeakFromConfig({ voice: { auto_tts: !enabled } })
        expect(prefs.$autoSpeakReplies.get()).toBe(enabled)
        expect(localStorage.getItem('hermes.desktop.autoSpeakReplies')).toBe(fails ? null : String(enabled))
      } finally {
        fake.restore()
      }
    }
  }
})

describe('applyVoiceStopPhraseFromConfig', () => {
  it('defaults to "stop" when the key is absent (backend default applies)', () => {
    applyVoiceStopPhraseFromConfig({ voice: {} })
    expect($voiceStopPhrase.get()).toBe('stop')

    applyVoiceStopPhraseFromConfig(null)
    expect($voiceStopPhrase.get()).toBe('stop')
  })

  it('uses the first configured phrase so a custom phrase renders correctly', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: ['goodbye hermes', 'stop'] } })
    expect($voiceStopPhrase.get()).toBe('goodbye hermes')
  })

  it('coerces a bare string like the backend does', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: 'halt' } })
    expect($voiceStopPhrase.get()).toBe('halt')
  })

  it('null phrase when stop phrases are disabled — no notice is shown', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: [] } })
    expect($voiceStopPhrase.get()).toBeNull()
  })

  it('malformed entries are skipped; all-blank list disables', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: ['  ', ''] } })
    expect($voiceStopPhrase.get()).toBeNull()
  })
})
