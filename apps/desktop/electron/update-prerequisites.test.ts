import assert from 'node:assert/strict'
import { existsSync, lstatSync, mkdirSync, mkdtempSync, rmdirSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import { windowsUpdatePrerequisiteError } from './updater-process'
import { ensureVenvAlias } from './venv-layout'

test('a uv-created .venv install passes the prerequisite, and neither folder keeps the original message', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'hermes-prerequisites-venv-'))

  try {
    const expected = `Update aborted: ${path.join(root, 'venv', 'Scripts', 'python.exe')} is missing or unreadable. Repair the installation and review antivirus quarantine before retrying.`
    assert.equal(windowsUpdatePrerequisiteError(root), expected)

    const dotVenvPython = path.join(root, '.venv', 'Scripts', 'python.exe')
    mkdirSync(path.dirname(dotVenvPython), { recursive: true })
    writeFileSync(dotVenvPython, 'file-presence fixture')
    assert.equal(ensureVenvAlias(root, { isWindows: true }), 'created')
    assert.equal(windowsUpdatePrerequisiteError(root), null)
  } finally {
    // Remove the alias link before the recursive delete, so cleanup can never walk into .venv.
    const alias = path.join(root, 'venv')

    if (existsSync(alias) && lstatSync(alias).isSymbolicLink()) {
      if (process.platform === 'win32') {
        rmdirSync(alias)
      } else {
        unlinkSync(alias)
      }
    }

    rmSync(root, { recursive: true, force: true })
  }
})

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
