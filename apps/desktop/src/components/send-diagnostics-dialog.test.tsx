import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $sendDiagnostics, requestSendDiagnostics } from '@/store/send-diagnostics'

import { SendDiagnosticsHost } from './send-diagnostics-dialog'

afterEach(() => {
  cleanup()
  $sendDiagnostics.set(null)
})

// Stardust is independently maintained: the only default upload transport for
// "Send diagnostics" is still the legacy, explicit-consent Nous bundle upload
// (diagnostics.share_nous — see send-diagnostics.ts), but it must never be
// presented as Stardust's own support channel. The dialog must (a) label the
// upload honestly as going to Nous, so the user isn't misled about who can
// see it, and (b) hand off support to Stardust's own issue tracker, never the
// upstream Nous Portal/Discord links a first-party "Hermes Cloud" support
// surface would have offered.
describe('SendDiagnosticsHost', () => {
  it('labels the upload as going to Nous, not implying it is a Stardust-run channel', () => {
    requestSendDiagnostics()
    render(<SendDiagnosticsHost />)

    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getAllByText(/nous/i).length).toBeGreaterThan(0)
  })

  it('only offers the Stardust issue tracker as support, never Nous Portal or Discord', () => {
    requestSendDiagnostics()
    $sendDiagnostics.set({ phase: 'done', result: { viewUrl: 'https://example.com/view/x1' } })
    render(<SendDiagnosticsHost />)

    const links = screen.getAllByRole('button').map(button => button.textContent ?? '')

    expect(links.some(text => /github/i.test(text))).toBe(true)
    expect(links.some(text => /nous portal|discord/i.test(text))).toBe(false)
  })
})
