// @vitest-environment jsdom
/**
 * An empty roster is a real empty state with the one thing to do next: create a
 * bot from the page itself, not only from the header's "+" menu.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { botsText } from './i18n'
import { renderRosterContent } from './roster-pane-content'

afterEach(() => {
  cleanup()
})

function renderRoster(overrides: Partial<Parameters<typeof renderRosterContent>[0]> = {}) {
  const onCreateBot = vi.fn()

  const props = {
    allBotsHidden: false,
    b: botsText(),
    error: null,
    gatewayUp: true,
    gatewaySections: { sectioned: false, sections: [] },
    hasRosterConstraint: false,
    hiddenBots: [],
    hiddenGatewaySections: { sectioned: false, sections: [] },
    hiddenExpanded: false,
    hiddenSectionRef: { current: null },
    initialRosterLoading: false,
    isLoading: false,
    matchingHiddenBots: [],
    onCreateBot,
    query: '',
    refetch: vi.fn(),
    renderBotRow: () => null,
    renderGatewaySection: () => null,
    renderGroupChatSection: () => null,
    renderHiddenGatewaySection: () => null,
    renderUserSections: () => null,
    roster: [],
    rosterRows: [],
    selectedGateway: undefined,
    showGatewaySections: false,
    showHiddenRows: false,
    showHiddenSection: false,
    sortedGroupRows: [],
    staleNotice: null,
    ...overrides
  } as unknown as Parameters<typeof renderRosterContent>[0]

  render(<>{renderRosterContent(props)}</>)

  return { b: props.b, onCreateBot }
}

describe('empty roster', () => {
  it('says there are no bots and offers the create action on the page', () => {
    const { b, onCreateBot } = renderRoster()

    expect(screen.getByText(b.roster.emptyTitle)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: b.bot.newTitle }))

    expect(onCreateBot).toHaveBeenCalledTimes(1)
  })

  it('does not offer the create action while the roster is still loading', () => {
    const { b } = renderRoster({ initialRosterLoading: true, isLoading: true })

    expect(screen.queryByRole('button', { name: b.bot.newTitle })).toBeNull()
  })
})
