import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { $subagentsBySession, upsertSubagent } from '@/store/subagents'

import { SubagentSection } from './subagent-section'

vi.stubGlobal(
  'ResizeObserver',
  class {
    disconnect() {}
    observe() {}
    unobserve() {}
  }
)
Element.prototype.animate = vi.fn(() => ({ cancel() {} }) as Animation)
afterEach(() => {
  cleanup()
  $subagentsBySession.set({})
  vi.restoreAllMocks()
})

it('keeps each worker draft while inspecting siblings and removes settled selection', () => {
  upsertSubagent('parent', { subagent_id: 'a', goal: 'Worker A' })
  upsertSubagent('parent', { subagent_id: 'b', goal: 'Worker B' })
  render(<SubagentSection sessionId="parent" />)
  fireEvent.click(screen.getByRole('button', { name: /2 Subagents/ }))
  fireEvent.click(screen.getByRole('button', { name: /Worker A/ }))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Preserve my instruction' } })
  fireEvent.click(screen.getByRole('button', { name: /Worker B/ }))
  expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('')
  fireEvent.click(screen.getByRole('button', { name: /Worker A/ }))
  expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('Preserve my instruction')
  act(() => upsertSubagent('parent', { subagent_id: 'a', status: 'completed' }, false, 'subagent.complete'))
  expect(screen.queryByText('Worker A')).toBeNull()
  expect(screen.queryByRole('textbox')).toBeNull()
})

it('expands sidebar members initially and respects a manual collapse during progress', () => {
  upsertSubagent('parent', { subagent_id: 'worker', goal: 'Project development' })
  upsertSubagent('foreign', { subagent_id: 'foreign', goal: 'Foreign private work' })
  render(<SubagentSection defaultCollapsed={false} sessionId="parent" />)
  expect(screen.getByText('Project development')).toBeTruthy()
  expect(screen.queryByText('Foreign private work')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /1 Subagent/ }))
  act(() => upsertSubagent('parent', { subagent_id: 'worker', text: 'Checking source' }, false, 'subagent.progress'))
  expect(screen.queryByText('Project development')).toBeNull()
  expect(screen.getByRole('button', { name: /1 Subagent/ }).getAttribute('aria-expanded')).toBe('false')
})

it('measures detail elapsed from worker start rather than first inspection', () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(100000)
  upsertSubagent('parent', { subagent_id: 'timed', goal: 'Timed worker' })
  now.mockReturnValue(117000)
  const { container } = render(<SubagentSection sessionId="parent" />)
  fireEvent.click(screen.getByRole('button', { name: /1 Subagent/ }))
  fireEvent.click(screen.getByRole('button', { name: /Timed worker/ }))
  expect(
    container.querySelector(
      '[data-slot="composer-subagent-detail"] [data-slot="tool-block"] > button > span:last-child'
    )?.textContent
  ).toBe('17s')
})
