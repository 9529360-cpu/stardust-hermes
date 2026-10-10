import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { fmtDayTime } from '@/lib/time'

vi.mock('@assistant-ui/react', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuiState: (select: (state: unknown) => unknown) =>
    select({ message: { id: 'msg-1', status: { type: 'complete' } }, thread: { isRunning: false } })
}))

const { ToolFallback } = await import('./fallback')

const NEXT_RUNS = ['2026-10-12T09:00:00+08:00', '2026-10-13T09:00:00+08:00']

const dryRunCreate = {
  success: true,
  dry_run: true,
  saved: false,
  preview: {
    name: 'Morning digest',
    schedule: 'every day 09:00',
    next_runs: NEXT_RUNS,
    deliver: 'origin',
    repeat: null,
    paused: false
  },
  message: 'Preview only, nothing was saved.'
}

function renderCronRow(result: unknown) {
  const props = {
    args: { action: 'create', name: 'Morning digest', schedule: 'every day 09:00' },
    result,
    toolCallId: 'call-cron',
    toolName: 'cronjob_manage'
  } as unknown as ComponentProps<typeof ToolFallback>

  return render(<ToolFallback {...props} />)
}

afterEach(() => {
  cleanup()
})

describe('cron tool card', () => {
  it('shows a dry run as a preview in the collapsed row and lists its runs when opened', () => {
    const { container } = renderCronRow(dryRunCreate)

    const title = screen.getByText('Cron job preview · every day 09:00')

    expect(container.textContent).not.toContain('Not saved')

    fireEvent.click(title)

    const items = Array.from(container.querySelectorAll('li'), item => item.textContent?.trim())
    const paragraphs = Array.from(container.querySelectorAll('p'), paragraph => paragraph.textContent?.trim())

    // Each run is its own list item; delivery and the not-saved line are their own paragraphs.
    expect(items).toEqual(NEXT_RUNS.map(iso => fmtDayTime.format(Date.parse(iso))))
    expect(paragraphs).toContain('Delivery: Current chat')
    expect(paragraphs).toContain('Not saved')
    expect(container.textContent).not.toContain('undefined')
  })

  it('keeps a saved create shown as a cron job, not as a preview', () => {
    const { container } = renderCronRow({
      success: true,
      job_id: 'job_1',
      name: 'Morning digest',
      schedule: 'every day 09:00',
      repeat: 'forever',
      deliver: 'origin',
      next_run_at: '2026-10-12T09:00:00+08:00',
      job: { id: 'job_1', name: 'Morning digest' },
      message: "Cron job 'Morning digest' created."
    })

    expect(screen.getByText('Cron job')).toBeTruthy()
    expect(container.textContent).not.toContain('preview')
  })
})
