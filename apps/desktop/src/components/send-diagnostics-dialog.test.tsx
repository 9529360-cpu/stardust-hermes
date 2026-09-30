import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $sendDiagnostics, requestSendDiagnostics } from '@/store/send-diagnostics'

import { SendDiagnosticsHost } from './send-diagnostics-dialog'

afterEach(() => {
  cleanup()
  $sendDiagnostics.set(null)
})

// Stardust owns the support handoff: the default Desktop flow prepares a
// force-redacted local ZIP and lets the user save it. It must neither present
// Nous/Portal/Discord as the support authority nor claim that logs were
// uploaded to a support service.
describe('SendDiagnosticsHost', () => {
  it('describes a local redacted save with no support-service upload', () => {
    requestSendDiagnostics()
    render(<SendDiagnosticsHost />)

    const dialog = screen.getByRole('dialog')

    expect(dialog).toBeTruthy()
    expect(dialog.textContent).toMatch(/save diagnostics/i)
    expect(dialog.textContent).toMatch(/nothing is uploaded/i)
    expect(dialog.textContent).not.toMatch(/nous|portal|discord/i)
  })

  it('shows the saved local path and only Stardust GitHub as the support handoff', () => {
    requestSendDiagnostics()
    $sendDiagnostics.set({
      phase: 'done',
      result: { savedPath: '/Users/me/Downloads/stardust-diagnostics.zip' }
    })
    render(<SendDiagnosticsHost />)

    expect(screen.getByText('/Users/me/Downloads/stardust-diagnostics.zip')).toBeTruthy()

    const buttons = screen.getAllByRole('button').map(button => button.textContent ?? '')

    expect(buttons.some(text => /github/i.test(text))).toBe(true)
    expect(buttons.some(text => /nous portal|discord/i.test(text))).toBe(false)
  })

  it('surfaces backend cleanup uncertainty after a successful local save', () => {
    $sendDiagnostics.set({
      phase: 'done',
      result: {
        savedPath: '/tmp/stardust-diagnostics.zip',
        cleanupWarning: 'backend cleanup unavailable'
      }
    })
    render(<SendDiagnosticsHost />)

    expect(screen.getByText(/backend cleanup unavailable/i)).toBeTruthy()
  })
})
