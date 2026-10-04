import { describe, expect, it } from 'vitest'

import { en } from '@/i18n/en'
import { ja } from '@/i18n/ja'
import { zh } from '@/i18n/zh'
import { zhHant } from '@/i18n/zh-hant'

import {
  ERROR_CODE_KEYS,
  ERROR_SURFACE_LAYERS,
  errorRecoveryPlan,
  type ErrorSurface,
  formatErrorDiagnostics,
  parseErrorSurface
} from './error-surface'
import { errorCardText } from './error-surface-copy'

describe('parseErrorSurface', () => {
  it('accepts a valid descriptor', () => {
    expect(parseErrorSurface({ layer: 'streaming', code: 'stream_drop', retryable: true })).toEqual({
      layer: 'streaming',
      code: 'stream_drop',
      retryable: true
    })
  })

  it('accepts every documented layer', () => {
    for (const layer of ['provider', 'endpoint', 'streaming', 'auth', 'billing', 'gateway', 'runtime', 'disk']) {
      expect(parseErrorSurface({ layer, code: 'x', retryable: false })?.layer).toBe(layer)
    }
  })

  it('rejects unknown layers and non-objects', () => {
    expect(parseErrorSurface({ layer: 'blockchain', code: 'x', retryable: true })).toBeNull()
    expect(parseErrorSurface('provider')).toBeNull()
    expect(parseErrorSurface(null)).toBeNull()
    expect(parseErrorSurface(undefined)).toBeNull()
    expect(parseErrorSurface(7)).toBeNull()
  })

  it('defaults code and retryable when missing', () => {
    expect(parseErrorSurface({ layer: 'gateway' })).toEqual({ layer: 'gateway', code: 'unknown', retryable: true })
  })

  it('honors retryable=false', () => {
    expect(parseErrorSurface({ layer: 'auth', code: 'auth_permanent', retryable: false })?.retryable).toBe(false)
  })

  it('carries the failing session identity when present', () => {
    const surface = parseErrorSurface({
      layer: 'provider',
      code: 'rate_limit',
      retryable: true,
      provider: 'openrouter',
      model: 'test/m1'
    })

    expect(surface?.provider).toBe('openrouter')
    expect(surface?.model).toBe('test/m1')
    // Absent identity yields no keys, not empty strings.
    expect(parseErrorSurface({ layer: 'provider', code: 'x', retryable: true })?.provider).toBeUndefined()
  })
})

describe('formatErrorDiagnostics', () => {
  it('includes layer, code, model and error', () => {
    const text = formatErrorDiagnostics({
      errorText: 'boom',
      model: 'anthropic/claude-opus-4.6',
      surface: { layer: 'provider', code: 'rate_limit', retryable: true }
    })

    expect(text).toContain('layer: provider')
    expect(text).toContain('code: rate_limit')
    expect(text).toContain('model: anthropic/claude-opus-4.6')
    expect(text).toContain('error: boom')
  })

  it('prefers the descriptor identity over the caller fallback', () => {
    const text = formatErrorDiagnostics({
      errorText: 'boom',
      // Foreground composer atom — potentially stale by click time.
      model: 'some/other-model',
      surface: { layer: 'provider', code: 'rate_limit', retryable: true, provider: 'openrouter', model: 'failed/model' }
    })

    expect(text).toContain('provider: openrouter')
    expect(text).toContain('model: failed/model')
    expect(text).not.toContain('some/other-model')
  })

  it('omits absent fields without leaving blank lines', () => {
    const text = formatErrorDiagnostics({ errorText: 'boom' })

    expect(text).not.toContain('layer:')
    expect(text).not.toContain('model:')
    expect(text.split('\n').every(line => line.trim().length > 0)).toBe(true)
  })
})

// The card body and the buttons under it must agree: a body that says "retry"
// while the plan hides the Retry button leaves the user with an instruction
// they cannot follow. Walks every code the backend can send (plus the layer
// fallbacks) with the non-retryable verdict the classifier stamps for it, in
// every locale that words the card itself.
describe.each([
  ['en', en.assistant.thread, /\bretry\b|\btry again\b/i],
  ['zh', zh.assistant.thread, /重试/],
  ['zh-hant', zhHant.assistant.thread, /重試/],
  ['ja', ja.assistant.thread, /再試行/]
] as const)('error copy never names a hidden Retry (%s)', (_locale, thread, RETRY_WORDS) => {
  // Verdicts the classifier stamps as deterministic (agent/error_surface.py
  // `_NON_RETRYABLE_REASONS`); everything else arrives retryable.
  const NON_RETRYABLE = new Set([
    'auth',
    'auth_permanent',
    'billing',
    'content_policy_blocked',
    'provider_policy_blocked',
    'model_not_found',
    'format_error',
    'ssl_cert_verification',
    'context_overflow',
    'interpreter_shutdown'
  ])

  const surfaces: ErrorSurface[] = [
    ...ERROR_CODE_KEYS.map(code => ({ code, layer: 'provider' as const, retryable: !NON_RETRYABLE.has(code) })),
    { code: 'auth', layer: 'auth', retryable: false },
    { code: 'auth_permanent', layer: 'auth', retryable: false },
    { code: 'ssl_cert_verification', layer: 'endpoint', retryable: false },
    { code: 'interpreter_shutdown', layer: 'gateway', retryable: false },
    { code: 'interpreter_shutdown', layer: 'runtime', retryable: false },
    { code: 'unknown', layer: 'endpoint', retryable: false }
  ]

  it.each(surfaces.map(surface => [surface.code, surface.layer, surface] as const))(
    '%s on %s',
    (_code, _layer, surface) => {
      const plan = errorRecoveryPlan(surface)
      const { body } = errorCardText(thread, surface)

      if (!plan.retry) {
        expect(body).not.toMatch(RETRY_WORDS)
      }
    }
  )

  it('a credential rejection keeps Retry, so its body may still say retry', () => {
    const surface: ErrorSurface = { authKind: 'api_key', code: 'auth', layer: 'auth', provider: 'openai', retryable: false }
    expect(errorRecoveryPlan(surface).retry).toBe(true)
  })
})

// zh / zh-hant / ja used to fall back to English for every per-code title and
// body, the layer bodies and the card's buttons — half-English cards at the
// moment the user has to decide what to do.
describe.each([
  ['zh', zh.assistant.thread],
  ['zh-hant', zhHant.assistant.thread],
  ['ja', ja.assistant.thread]
] as const)('the %s error card is worded in its own language', (_locale, thread) => {
  const english = en.assistant.thread

  const surfaces: ErrorSurface[] = [
    ...ERROR_CODE_KEYS.map(code => ({ code, layer: 'provider' as const, provider: 'Acme', retryable: true })),
    ...ERROR_SURFACE_LAYERS.map(layer => ({ code: 'unknown', layer, provider: 'Acme', retryable: true }))
  ]

  it.each(surfaces.map(surface => [surface.code, surface.layer, surface] as const))(
    '%s on %s',
    (_code, _layer, surface) => {
      const ours = errorCardText(thread, surface)
      const base = errorCardText(english, surface)

      expect(ours.title).not.toBe(base.title)
      expect(ours.body).not.toBe(base.body)
    }
  )

  it('names its buttons', () => {
    for (const key of [
      'errorChooseModel',
      'errorCompressConversation',
      'errorUpdateApiKey',
      'errorOpenHermesFolder',
      'errorDetails',
      'errorToastTitle'
    ] as const) {
      expect(thread[key], key).not.toBe(english[key])
    }
  })
})
