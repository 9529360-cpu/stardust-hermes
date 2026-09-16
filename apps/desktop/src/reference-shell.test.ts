import { describe, expect, it } from 'vitest'

import { REFERENCE_SHELL, applyReferenceShell, shouldEnableReferenceShell } from './reference-shell'

describe('reference shell activation', () => {
  it('enables the aurora shell for the primary workspace and secondary session windows', () => {
    expect(shouldEnableReferenceShell(null)).toBe(true)
    expect(shouldEnableReferenceShell('secondary')).toBe(true)
  })

  it.each(['browser', 'hud', 'overlay', 'quick', 'wake', 'intro'])('leaves %s utility windows unchanged', winParam => {
    expect(shouldEnableReferenceShell(winParam)).toBe(false)
  })

  it('writes the shell marker only when the window is eligible', () => {
    const primary = document.createElement('html')
    applyReferenceShell(null, primary)
    expect(primary.dataset.hermesReferenceShell).toBe(REFERENCE_SHELL)

    const utility = document.createElement('html')
    applyReferenceShell('hud', utility)
    expect(utility.dataset.hermesReferenceShell).toBeUndefined()
  })
})
