import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { MemoryNotes } from './memory-notes'

const requestGateway = vi.hoisted(() => vi.fn())

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

type Notes = { memory: string[]; user: string[] }

const ABOUT_YOU_PLACEHOLDER = 'For example: I prefer short answers'
const NOTES_PLACEHOLDER = 'For example: the tests run with pnpm test'

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

async function confirmForget() {
  const dialog = await screen.findByRole('dialog')

  fireEvent.click(within(dialog).getByRole('button', { name: 'Forget' }))
}

describe('MemoryNotes', () => {
  beforeEach(() => {
    requestGateway.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('lists what it knows about you and its own notes, each under a plain heading', async () => {
    fakeMemory({ memory: ['Tests run with pnpm test'], user: ['Prefers short answers'] })

    renderPanel()

    expect(await screen.findByText('Prefers short answers')).toBeTruthy()
    expect(screen.getByText('Tests run with pnpm test')).toBeTruthy()
    // The headings reuse the toggle's labels, so each label appears in two places.
    expect(screen.getAllByText('About you').length).toBeGreaterThan(0)
    expect(screen.getAllByText('My notes').length).toBeGreaterThan(0)
    expect(screen.getByText('Your preferences and background.')).toBeTruthy()
  })

  it('remembers a trimmed note under the chosen heading, then reloads', async () => {
    const notes: Notes = { memory: [], user: [] }

    fakeMemory(notes)
    renderPanel()

    await screen.findAllByText('Nothing here yet.')

    const remember = screen.getByRole('button', { name: 'Remember' })
    const input = screen.getByPlaceholderText(ABOUT_YOU_PLACEHOLDER)

    expect((remember as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(input, { target: { value: '  Use metric units  ' } })
    fireEvent.click(remember)

    expect(await screen.findByText('Use metric units')).toBeTruthy()
    expect(requestGateway).toHaveBeenCalledWith('memory.remember', {
      target: 'user',
      content: 'Use metric units'
    })
  })

  it('offers an example that matches the heading the note goes under', async () => {
    fakeMemory({ memory: [], user: [] })
    renderPanel()

    await screen.findAllByText('Nothing here yet.')

    expect(screen.getByPlaceholderText(ABOUT_YOU_PLACEHOLDER)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'My notes' }))

    expect(screen.getByPlaceholderText(NOTES_PLACEHOLDER)).toBeTruthy()
  })

  it('forgets a note only with its index and the text the user saw', async () => {
    const notes: Notes = { memory: [], user: ['Prefers short answers'] }

    fakeMemory(notes)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Forget' }))
    await confirmForget()

    await waitFor(() => expect(screen.queryByText('Prefers short answers')).toBeNull())
    expect(requestGateway).toHaveBeenCalledWith('memory.forget', {
      target: 'user',
      index: 0,
      expected_text: 'Prefers short answers'
    })
  })

  it('says the memory changed and refreshes when the backend refuses a stale forget', async () => {
    const notes: Notes = { memory: [], user: ['Prefers short answers'] }

    fakeMemory(notes)
    renderPanel()

    // The panel shows the old text; something else changes the note before the user's forget lands.
    expect(await screen.findByText('Prefers short answers')).toBeTruthy()
    notes.user[0] = 'Prefers long answers'

    fireEvent.click(screen.getByRole('button', { name: 'Forget' }))
    await confirmForget()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('That memory just changed, so the list is up to date.')).toBeTruthy()
    expect(await screen.findByText('Prefers long answers')).toBeTruthy()
  })

  it('explains a turned-off memory and blocks remembering under it', async () => {
    fakeMemory({ memory: ['Old note'], user: [] }, { memory: 'disabled' })

    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'My notes' }))

    expect(
      await screen.findByText('This kind of memory is off. Turn it on in Advanced settings below.')
    ).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText(NOTES_PLACEHOLDER), { target: { value: 'Blocked' } })

    expect((screen.getByRole('button', { name: 'Remember' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps the typed note when the backend refuses it, so the user can retry', async () => {
    fakeMemory({ memory: [], user: [] })
    const backend = requestGateway.getMockImplementation()

    requestGateway.mockImplementation(async (method: string, params: Record<string, unknown>) =>
      method === 'memory.remember' ? { success: false } : backend?.(method, params)
    )

    renderPanel()
    await screen.findAllByText('Nothing here yet.')

    const input = screen.getByPlaceholderText(ABOUT_YOU_PLACEHOLDER) as HTMLInputElement

    fireEvent.change(input, { target: { value: 'Keep me' } })
    fireEvent.click(screen.getByRole('button', { name: 'Remember' }))

    expect(await screen.findByText("Couldn't save that. Try again.")).toBeTruthy()
    expect(input.value).toBe('Keep me')
  })
})
