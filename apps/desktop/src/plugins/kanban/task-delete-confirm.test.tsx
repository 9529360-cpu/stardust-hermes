import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Test harness supplies the host's locale registration, as plugin loading does.
// eslint-disable-next-line no-restricted-imports
import { registerPluginLocales } from '@/i18n/plugin-i18n'

import { en, KANBAN_LOCALES } from './i18n'
import { TaskDeleteConfirm } from './task-delete-confirm'

let disposeLocales: () => void

beforeEach(() => {
  disposeLocales = registerPluginLocales('kanban', KANBAN_LOCALES)
})

afterEach(() => {
  cleanup()
  disposeLocales()
})

describe('task delete confirmation', () => {
  it('requires confirmation before permanently deleting one task', async () => {
    const onConfirm = vi.fn(async () => undefined)
    render(
      <TaskDeleteConfirm count={1} name="Example task" onClose={vi.fn()} onConfirm={onConfirm} open />
    )

    expect(screen.getByText(en.deleteTaskTitle('Example task'))).toBeTruthy()
    expect(screen.getByText(en.deleteTaskConfirm)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: en.delete }))
    await waitFor(() => expect(onConfirm).toHaveBeenCalledOnce())
  })

  it.each([1, 3])('makes bulk deletion explicit for %s selected task(s)', count => {
    render(<TaskDeleteConfirm count={count} onClose={vi.fn()} onConfirm={vi.fn()} open />)

    expect(screen.getByText(en.deleteTasksTitle(count))).toBeTruthy()
    expect(screen.getByText(en.deleteTasksConfirm)).toBeTruthy()
  })
})
