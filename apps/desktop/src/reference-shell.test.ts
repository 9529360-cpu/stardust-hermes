import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

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

  it('mounts the backdrop once at the desktop shell instead of inside the chat column', () => {
    const controller = readFileSync(resolve(process.cwd(), 'src/app/contrib/controller.tsx'), 'utf8')
    const chat = readFileSync(resolve(process.cwd(), 'src/app/chat/index.tsx'), 'utf8')

    expect(controller).toContain("import { Backdrop } from '@/components/Backdrop'")
    expect(controller).toContain('<Backdrop />')
    expect(chat).not.toContain("from '@/components/Backdrop'")
    expect(chat).not.toContain('<Backdrop />')
  })

  it('defines translucent Aurora field surfaces for a window backdrop', () => {
    const referenceShellCss = readFileSync(resolve(process.cwd(), 'src/reference-shell.css'), 'utf8')

    expect(referenceShellCss).toContain("[data-stardust-window-backdrop] [data-tree-group]")
    expect(referenceShellCss).toContain("[data-tree-group='grp-sessions']")
    expect(referenceShellCss).toContain("[data-tree-group='grp-context']")
    expect(referenceShellCss).toContain('backdrop-filter: blur(16px) saturate(1.08);')
  })

  it('leaves palette ownership to the active theme tokens', () => {
    const referenceShellCss = readFileSync(resolve(process.cwd(), 'src/reference-shell.css'), 'utf8')

    const colorBearingCss = referenceShellCss
      .split('\n')
      .filter(line => !line.includes('mask-image:'))
      .join('\n')

    expect(colorBearingCss).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i)
    expect(referenceShellCss).toContain('--aurora-blue: var(--ui-accent);')
    expect(referenceShellCss).toContain('--aurora-glass: var(--ui-bg-chrome);')
  })
})
