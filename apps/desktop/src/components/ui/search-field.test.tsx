import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SearchField } from './search-field'

afterEach(cleanup)

describe('SearchField', () => {
  it('forwards autoFocus to the actual input', async () => {
    render(<SearchField autoFocus onChange={vi.fn()} placeholder="Search" value="" />)

    const input = screen.getByRole('textbox')

    await waitFor(() => expect(input.ownerDocument.activeElement).toBe(input))
  })
})
