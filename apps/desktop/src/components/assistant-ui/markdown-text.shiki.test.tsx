import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

import { MarkdownTextContent } from './markdown-text'

afterEach(cleanup)

it('keeps streaming fences readable and highlights only after completion', async () => {
  const text = '```typescript\nconst answer: number = 42;\n'
  const { container, rerender } = render(<MarkdownTextContent isRunning text={text} />)

  await waitFor(() => expect(container.textContent).toContain('const answer: number = 42;'))
  expect(container.querySelector('[data-testid="shiki-container"]')).toBeNull()
  rerender(<MarkdownTextContent isRunning text={text + 'console.log(answer);\n'} />)
  await waitFor(() => expect(container.textContent).toContain('console.log(answer);'))
  expect(container.querySelector('[data-testid="shiki-container"]')).toBeNull()
  rerender(<MarkdownTextContent isRunning={false} text={text + 'console.log(answer);\n```'} />)
  await waitFor(() => expect(container.querySelector('[data-testid="shiki-container"] .line span')).toBeTruthy())
  expect(container.textContent).toContain('const answer: number = 42;')
  expect(container.textContent).toContain('console.log(answer);')
})
