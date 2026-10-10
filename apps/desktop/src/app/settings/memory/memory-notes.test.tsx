import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { MemoryNotes } from './memory-notes'

const requestGateway = vi.hoisted(() => vi.fn())

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

type Notes = { memory: string[]; user: string[] }

/** A small in-memory backend. Forget is refused unless the displayed text still matches, as in the RPC. */
function fakeMemory(notes: Notes, targets: Record<string, 'enabled' | 'disabled'> = {}) {
  requestGateway.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'memory.list') {
      return {
        entries: (['memory', 'user'] as const).flatMap(target =>
          notes[target].map((text, index) => ({ target, index, text }))
        ),
        targets: { memory: 'enabled', user: 'enabled', ...targets }
      }
    }

    if (method === 'memory.remember') {
      notes[params.target as keyof Notes].push(String(params.content))

      return { success: true }
    }

    if (method === 'memory.forget') {
      const bucket = notes[params.target as keyof Notes]
      const index = Number(params.index)

      if (bucket[index] !== params.expected_text) {
        return { success: false }
      }

      bucket.splice(index, 1)

      return { success: true }
    }

    throw new Error(`unexpected ${method}`)
  })
}

function renderPanel() {
  return render(
    <I18nProvider>
      <MemoryNotes />
    </I18nProvider>
  )
}

describe('MemoryNotes', () => {
  beforeEach(() => {
    requestGateway.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('lists notes under the assistant and the user targets', async () => {
    fakeMemory({ memory: ['Prefers short answers'], user: ['Lives in Shanghai'] })

    renderPanel()

    expect(await screen.findByText('Prefers short answers')).toBeTruthy()
    expect(screen.getByText('Lives in Shanghai')).toBeTruthy()
    // Group headings reuse the target names the toggle shows, so the labels can appear more than once.
    expect(screen.getAllByText('Assistant notes').length).toBeGreaterThan(0)
    expect(screen.getAllByText('About you').length).toBeGreaterThan(0)
  })

  it('remembers a trimmed note under the chosen target, then reloads', async () => {
    const notes: Notes = { memory: [], user: [] }

    fakeMemory(notes)
    renderPanel()

    await screen.findAllByText('Nothing remembered here yet.')

    const remember = screen.getByRole('button', { name: 'Remember' })
    const input = screen.getByPlaceholderText('Add a note the assistant should keep')

    expect((remember as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(input, { target: { value: '  Use metric units  ' } })
    fireEvent.click(remember)

    expect(await screen.findByText('Use metric units')).toBeTruthy()
    expect(requestGateway).toHaveBeenCalledWith('memory.remember', {
      target: 'memory',
      content: 'Use metric units'
    })
  })

  it('forgets a note only with its index and the text the user saw', async () => {
    const notes: Notes = { memory: ['Prefers short answers'], user: [] }

    fakeMemory(notes)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Forget' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Forget' }))

    await waitFor(() => expect(screen.queryByText('Prefers short answers')).toBeNull())
    expect(requestGateway).toHaveBeenCalledWith('memory.forget', {
      target: 'memory',
      index: 0,
      expected_text: 'Prefers short answers'
    })
  })

  it('says a note changed and refreshes when the backend refuses a stale forget', async () => {
    const notes: Notes = { memory: ['Prefers short answers'], user: [] }

    fakeMemory(notes)
    renderPanel()

    // The panel shows the old text; something else changes the note before the user's forget lands.
    expect(await screen.findByText('Prefers short answers')).toBeTruthy()
    notes.memory[0] = 'Prefers long answers'

    fireEvent.click(screen.getByRole('button', { name: 'Forget' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Forget' }))

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('That note changed before your request. The list was refreshed.')).toBeTruthy()
    expect(await screen.findByText('Prefers long answers')).toBeTruthy()
  })

  it('shows the disabled copy and blocks remembering for a target that is off', async () => {
    fakeMemory({ memory: ['Old note'], user: [] }, { memory: 'disabled' })

    renderPanel()

    expect(await screen.findByText('Memory is off for this target.')).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText('Add a note the assistant should keep'), {
      target: { value: 'Blocked' }
    })

    expect((screen.getByRole('button', { name: 'Remember' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps the typed note when the backend refuses it, so the user can retry', async () => {
    fakeMemory({ memory: [], user: [] })
    const backend = requestGateway.getMockImplementation()

    requestGateway.mockImplementation(async (method: string, params: Record<string, unknown>) =>
      method === 'memory.remember' ? { success: false } : backend?.(method, params)
    )

    renderPanel()
    await screen.findAllByText('Nothing remembered here yet.')

    const input = screen.getByPlaceholderText('Add a note the assistant should keep') as HTMLInputElement

    fireEvent.change(input, { target: { value: 'Keep me' } })
    fireEvent.click(screen.getByRole('button', { name: 'Remember' }))

    expect(await screen.findByText("Couldn't save that note.")).toBeTruthy()
    expect(input.value).toBe('Keep me')
  })
})
