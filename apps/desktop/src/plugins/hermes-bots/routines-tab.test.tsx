// @vitest-environment jsdom
/**
 * The Routines tab carries its name in both shapes a pane tab takes. Expanded,
 * the name is the tab's text. In the collapsed right-edge rail the same name is
 * the icon's accessible label and its tooltip. The rail hides the text and the
 * icon swaps in through CSS (the shell's `data-vertical` marker), so both
 * shapes must always name the same pane.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { RoutinesTabLabel } from './routines-tab'

afterEach(() => {
  cleanup()
})

describe('RoutinesTabLabel', () => {
  it('gives the rail icon the same name as the expanded tab text', () => {
    const { container } = render(<RoutinesTabLabel />)
    const icon = screen.getByRole('img')
    const name = icon.getAttribute('aria-label') ?? ''

    expect(name).not.toBe('')
    expect(container.textContent).toContain(name)
  })
})
