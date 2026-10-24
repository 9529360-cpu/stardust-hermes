import { act, render, waitFor } from '@testing-library/react'
import { expect, test } from 'vitest'

import { AnimatedInt } from './diff-count'

test('animates to the next integer without replacing its text node', async () => {
  const { container, rerender } = render(<AnimatedInt value={12} />)
  const text = container.querySelector('span')!.firstChild
  expect(text?.nodeValue).toBe('12')
  act(() => rerender(<AnimatedInt value={24} />))
  await waitFor(() => expect(text?.nodeValue).toBe('24'), { timeout: 2000 })
  expect(container.querySelector('span')!.firstChild).toBe(text)
})
