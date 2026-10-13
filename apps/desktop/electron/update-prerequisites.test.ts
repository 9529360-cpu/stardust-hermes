import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import { developmentWorkspaceManualUpdateMessage, windowsUpdatePrerequisiteError } from './updater-process'

test('handoff prerequisites fail closed without requiring dependencies, shim or browser UI', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'hermes-prerequisites-'))

  try {
    const python = path.join(root, 'venv', 'Scripts', 'python.exe')
    const script = path.join(root, 'scripts', 'desktop-update', 'windows.ps1')
    assert.match(windowsUpdatePrerequisiteError(root)!, /python.exe/)
    mkdirSync(path.dirname(python), { recursive: true })
    writeFileSync(python, 'file-presence fixture')
    assert.equal(windowsUpdatePrerequisiteError(root), null) // legacy flat layout
    mkdirSync(path.dirname(script), { recursive: true })
    assert.match(windowsUpdatePrerequisiteError(root)!, /windows.ps1/)
    writeFileSync(script, 'file-presence fixture')
    assert.equal(windowsUpdatePrerequisiteError(root), null)
    unlinkSync(python)
    assert.match(windowsUpdatePrerequisiteError(root)!, /python.exe/)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('explicit .venv interpreter satisfies update prerequisites without requiring venv', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'hermes-prerequisites-dot-venv-'))

  try {
    const python = path.join(root, '.venv', 'Scripts', 'python.exe')
    const script = path.join(root, 'scripts', 'desktop-update', 'windows.ps1')
    mkdirSync(path.dirname(python), { recursive: true })
    mkdirSync(path.dirname(script), { recursive: true })
    writeFileSync(python, 'file-presence fixture')
    writeFileSync(script, 'script fixture')

    assert.match(windowsUpdatePrerequisiteError(root)!, /venv\\Scripts\\python\.exe/i)
    assert.equal(windowsUpdatePrerequisiteError(root, python), null)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('development workspace requires a manual update; packaged runtime may update automatically', () => {
  assert.equal(developmentWorkspaceManualUpdateMessage(true), null)

  const notice = developmentWorkspaceManualUpdateMessage(false)
  assert.ok(notice)
  assert.match(notice.message, /manual/i)
  assert.match(notice.message, /branch/i)
  assert.match(notice.message, /environment/i)
  assert.equal(notice.command, 'stardust update')
})
