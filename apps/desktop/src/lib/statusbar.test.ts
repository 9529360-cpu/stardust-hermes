import { describe, expect, it } from 'vitest'

import { cacheHitLabel, tokensPerSecondLabel, usageContextPercentLabel, usageContextSummary } from '@/lib/statusbar'

const base = { calls: 0, input: 0, output: 0, total: 0 }
const measured = { ...base, total: 19500, context_max: 64000, context_percent: 30.4, context_used: 19500 }
const summary = (used: string, max: string) => `${used} / ${max} Tokens`

describe('context readout', () => {
  it('puts only the share of the window in use on the bar, marked when it is an estimate', () => {
    expect(usageContextPercentLabel(measured)).toBe('30%')
    expect(usageContextPercentLabel({ ...measured, context_estimated: true })).toBe('~30%')
    // No known window means no share to show, so the bar has nothing to paint.
    expect(usageContextPercentLabel({ ...base, total: 19500 })).toBe('')
  })

  it('keeps the exact counts for the tooltip, in the caller localized summary', () => {
    expect(usageContextSummary(measured, summary)).toBe('19.5k / 64k Tokens')
    expect(usageContextSummary({ ...measured, context_estimated: true }, summary)).toBe('~19.5k / 64k Tokens')
    expect(usageContextSummary({ ...base, total: 19500 }, summary)).toBe('')
  })
})

describe('statusbar usage readouts', () => {
  it('paints the backend cache-hit and throughput fields, and stays blank when they are absent', () => {
    // The backend omits both fields (rather than sending 0) when it has no data
    // — a provider with no cache reads, or a session before its first call.
    expect(cacheHitLabel(base)).toBe('')
    expect(tokensPerSecondLabel(base)).toBe('')

    expect(cacheHitLabel({ ...base, cache_hit_pct: 87 })).toBe('87%')
    expect(tokensPerSecondLabel({ ...base, avg_tps: 41.6 })).toBe('42 t/s')
  })
})
