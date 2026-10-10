// @vitest-environment jsdom
import { host } from '@hermes/plugin-sdk'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RosterBackButton } from './roster-back-button'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('RosterBackButton', () => {
  it('reveals the sessions pane, the only way back to the product nav from the roster', () => {
    const reveal = vi.spyOn(host, 'revealPane').mockImplementation(() => undefined)

    render(<RosterBackButton label="Back to conversations" />)
    fireEvent.click(screen.getByRole('button', { name: 'Back to conversations' }))

    expect(reveal).toHaveBeenCalledTimes(1)
    expect(reveal).toHaveBeenCalledWith('sessions')
  })
})
