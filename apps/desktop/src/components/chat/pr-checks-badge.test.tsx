// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { PrChecksBadge } from './pr-checks-badge'

const states = [
  ['loading', 'Checks loading'],
  ['unavailable', 'Checks unavailable'],
  ['pending', 'Checks pending'],
  ['passed', 'Checks passed'],
  ['failed', 'Checks failed']
] as const

afterEach(() => {
  cleanup()
})

describe('PrChecksBadge', () => {
  it.each(states)('renders the explicit %s state', (state, label) => {
    const { container } = render(<PrChecksBadge state={state} />)

    expect(container.querySelector(`[data-pr-checks-state="${state}"]`)).not.toBeNull()
    expect(screen.getByTitle(label)).toBeTruthy()
    expect(screen.getByText(label)).toBeTruthy()
  })
})
