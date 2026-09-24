import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { applyReferenceShell, REFERENCE_SHELL, shouldEnableReferenceShell } from './reference-shell'

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

  it('leaves palette ownership to the active theme tokens', () => {
    const css = readFileSync(new URL('./reference-shell.css', import.meta.url), 'utf8')
    const colorBearingCss = css
      .split('\n')
      .filter(line => !line.includes('mask-image:'))
      .join('\n')

    expect(colorBearingCss).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i)
    expect(css).toContain('--aurora-blue: var(--ui-accent);')
    expect(css).toContain('--aurora-glass: var(--ui-bg-chrome);')
  })
})
