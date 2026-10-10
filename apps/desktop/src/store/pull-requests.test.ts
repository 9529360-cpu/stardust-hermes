import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesReviewChecks } from '@/global'

import {
  $pullRequestChecksByPr,
  numberPrKey,
  pullRequestChecksKey,
  pullRequestChecksState,
  refreshPullRequestChecks
} from './pull-requests'

const checksResult = (overrides: Partial<HermesReviewChecks> = {}): HermesReviewChecks => ({
  checks: [],
  conclusion: null,
  status: 'pending',
  workflowRuns: [],
  ...overrides
})

function installBridge(checks: ReturnType<typeof vi.fn>) {
  ;(window as unknown as { hermesDesktop?: unknown }).hermesDesktop = {
    git: { review: { checks } }
  }
}

describe('pull request checks state', () => {
  beforeEach(() => {
    $pullRequestChecksByPr.set({})
  })

  afterEach(() => {
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  it.each([
    ['a missing response', null, 'unavailable'],
    ['an unavailable response', checksResult({ status: 'unavailable' }), 'unavailable'],
    ['a pending rollup', checksResult({ status: 'pending' }), 'pending'],
    ['a successful rollup', checksResult({ conclusion: 'success', status: 'completed' }), 'passed'],
    [
      'a failed check in an otherwise completed rollup',
      checksResult({
        checks: [{ conclusion: 'failure', name: 'lint', status: 'completed', url: '' }],
        status: 'completed'
      }),
      'failed'
    ],
    ['a completed response without a conclusion', checksResult({ status: 'completed' }), 'pending']
  ])('maps %s to the bounded renderer state', (_name, result, expected) => {
    expect(pullRequestChecksState(result)).toBe(expected)
  })

  it('publishes loading immediately, coalesces in-flight reads, and stores the terminal state', async () => {
    let resolveChecks: (value: HermesReviewChecks) => void = () => undefined

    const checks = vi.fn(
      () =>
        new Promise<HermesReviewChecks>(resolve => {
          resolveChecks = resolve
        })
    )

    installBridge(checks)

    const first = refreshPullRequestChecks('/repo', 42, true)

    expect($pullRequestChecksByPr.get()[numberPrKey('/repo', 42)]).toBe('loading')

    const second = refreshPullRequestChecks('/repo', 42, true)

    expect(checks).toHaveBeenCalledTimes(1)

    resolveChecks(checksResult({ conclusion: 'success', status: 'completed' }))
    await Promise.all([first, second])

    expect($pullRequestChecksByPr.get()[numberPrKey('/repo', 42)]).toBe('passed')
  })

  it('marks the state unavailable when the bridge is absent', async () => {
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop

    await refreshPullRequestChecks('/repo', 7, true)

    expect($pullRequestChecksByPr.get()[numberPrKey('/repo', 7)]).toBe('unavailable')
  })

  it('derives a stable number key from a branch lookup', () => {
    expect(pullRequestChecksKey('/repo\nfeature', 9)).toBe(numberPrKey('/repo', 9))
    expect(pullRequestChecksKey(null, 9)).toBeNull()
  })
})
