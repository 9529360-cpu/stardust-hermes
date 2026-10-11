// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { PageSearchShell } from './page-search-shell'

afterEach(() => {
  cleanup()
})

const shellProps = {
  onSearchChange: () => undefined,
  searchPlaceholder: 'Search',
  searchValue: ''
}

describe('PageSearchShell page title', () => {
  it('renders the title as the Tasks-style page heading above the search and tabs', () => {
    render(
      <PageSearchShell {...shellProps} tabs={[{ id: 'a', label: 'A' }]} title="Tools">
        <div />
      </PageSearchShell>
    )

    expect(screen.getByRole('heading', { name: 'Tools' })).toBeTruthy()
  })

  it('does not leak the title onto the section as a native tooltip', () => {
    const { container } = render(
      <PageSearchShell {...shellProps} tabs={[{ id: 'a', label: 'A' }]} title="Tools">
        <div />
      </PageSearchShell>
    )

    expect(container.querySelector('section')?.getAttribute('title')).toBeNull()
  })

  it('renders no heading when the page gives no title, as the artifacts and messaging pages do', () => {
    render(
      <PageSearchShell {...shellProps} tabs={[{ id: 'a', label: 'A' }]}>
        <div />
      </PageSearchShell>
    )

    expect(screen.queryByRole('heading')).toBeNull()
  })
})
