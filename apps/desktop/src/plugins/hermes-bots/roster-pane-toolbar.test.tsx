// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { botsText } from './i18n'
import { renderRosterToolbar } from './roster-pane-toolbar'

afterEach(() => {
  cleanup()
})

describe('renderRosterToolbar header', () => {
  it('labels the roster with the locale pane title, never a hardcoded word', () => {
    const b = botsText()

    render(
      <>
        {renderRosterToolbar({
          activeFilterCount: 0,
          activeSourceRoster: [],
          activityFilter: 'all',
          activityToasts: true,
          b,
          gatewayFilter: 'all',
          gatewayOptions: [],
          query: '',
          rowKindFilter: 'all',
          setActivityFilter: () => undefined,
          setCreateOpen: () => undefined,
          setGatewayFilter: () => undefined,
          setGroupCreateOpen: () => undefined,
          setQuery: () => undefined,
          setRowKindFilter: () => undefined,
          setSectionDialog: () => undefined,
          showRosterFilters: false,
          showRosterSearch: false,
          showRosterTools: false
        })}
      </>
    )

    expect(screen.getByText(b.paneTitle)).toBeTruthy()
    expect(screen.queryByText('Bots')).toBeNull()
  })
})
