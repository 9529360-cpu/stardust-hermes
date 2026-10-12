import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import { ensureVenvAlias, managedShimRefreshCommand, resolveVenvDir } from './venv-layout'

function makeRoot(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-venv-layout-'))
}

function interpreterPath(venvDir: string, isWindows: boolean): string {
  return path.join(venvDir, ...(isWindows ? ['Scripts', 'python.exe'] : ['bin', 'python']))
}

function writeInterpreter(venvDir: string, isWindows: boolean): string {
  const python = interpreterPath(venvDir, isWindows)
  fs.mkdirSync(path.dirname(python), { recursive: true })
  fs.writeFileSync(python, 'interpreter fixture')

  return python
}

/** Drops the alias link before recursive removal, so the cleanup can never walk into `.venv`. */
function removeRoot(root: string): void {
  const alias = path.join(root, 'venv')

  if (fs.existsSync(alias) && fs.lstatSync(alias).isSymbolicLink()) {
    if (process.platform === 'win32') {
      fs.rmdirSync(alias)
    } else {
      fs.unlinkSync(alias)
    }
  }

  fs.rmSync(root, { recursive: true, force: true })
}

test('resolveVenvDir prefers the venv that holds the interpreter, then .venv, then defaults to venv', () => {
  const root = makeRoot()

  try {
    assert.equal(resolveVenvDir(root, { isWindows: true }), path.join(root, 'venv'), 'neither: fresh-install default')

    writeInterpreter(path.join(root, '.venv'), true)
    assert.equal(resolveVenvDir(root, { isWindows: true }), path.join(root, '.venv'), '.venv only')

    writeInterpreter(path.join(root, 'venv'), true)
    assert.equal(resolveVenvDir(root, { isWindows: true }), path.join(root, 'venv'), 'both: venv wins')
  } finally {
    removeRoot(root)
  }
})

test('resolveVenvDir looks for the interpreter the host layout uses (bin/python off Windows)', () => {
  const root = makeRoot()

  try {
    writeInterpreter(path.join(root, '.venv'), false)
    writeInterpreter(path.join(root, 'venv'), true) // Windows layout: not an interpreter on POSIX

    assert.equal(resolveVenvDir(root, { isWindows: false }), path.join(root, '.venv'))
  } finally {
    removeRoot(root)
  }
})

test('ensureVenvAlias exposes a .venv-only install under venv and leaves .venv alone', () => {
  const root = makeRoot()

  try {
    const dotVenvPython = writeInterpreter(path.join(root, '.venv'), true)

    assert.equal(ensureVenvAlias(root, { isWindows: true }), 'created')

    assert.equal(resolveVenvDir(root, { isWindows: true }), path.join(root, 'venv'))
    assert.equal(fs.readFileSync(path.join(root, 'venv', 'Scripts', 'python.exe'), 'utf8'), 'interpreter fixture')
    assert.equal(fs.readFileSync(dotVenvPython, 'utf8'), 'interpreter fixture')
    assert.equal(ensureVenvAlias(root, { isWindows: true }), 'present', 'a second run changes nothing')
  } finally {
    removeRoot(root)
  }
})

test('ensureVenvAlias never replaces an existing venv directory or file', () => {
  const directoryRoot = makeRoot()
  const fileRoot = makeRoot()

  try {
    writeInterpreter(path.join(directoryRoot, '.venv'), true)
    fs.mkdirSync(path.join(directoryRoot, 'venv'))
    fs.writeFileSync(path.join(directoryRoot, 'venv', 'keep.txt'), 'real venv')

    assert.equal(ensureVenvAlias(directoryRoot, { isWindows: true }), 'present')
    assert.equal(fs.readFileSync(path.join(directoryRoot, 'venv', 'keep.txt'), 'utf8'), 'real venv')

    fs.mkdirSync(path.join(fileRoot, '.venv'))
    fs.writeFileSync(path.join(fileRoot, 'venv'), 'placeholder')

    assert.equal(ensureVenvAlias(fileRoot, { isWindows: true }), 'present')
    assert.equal(fs.readFileSync(path.join(fileRoot, 'venv'), 'utf8'), 'placeholder')
  } finally {
    removeRoot(directoryRoot)
    removeRoot(fileRoot)
  }
})

test('ensureVenvAlias does nothing without a .venv directory', () => {
  const root = makeRoot()

  try {
    assert.equal(ensureVenvAlias(root, { isWindows: true }), 'no-dotvenv')
    assert.equal(fs.existsSync(path.join(root, 'venv')), false)
  } finally {
    removeRoot(root)
  }
})

test('ensureVenvAlias reports a failed link with its reason and never throws', () => {
  const root = makeRoot()
  const logged: string[] = []

  try {
    fs.mkdirSync(path.join(root, '.venv'))

    const outcome = ensureVenvAlias(root, {
      isWindows: true,
      createLink: () => {
        throw new Error('EPERM: simulated refusal')
      },
      log: message => logged.push(message)
    })

    assert.equal(outcome, 'failed')
    assert.equal(fs.existsSync(path.join(root, 'venv')), false)
    assert.equal(logged.length, 1)
    assert.match(logged[0], /EPERM: simulated refusal/)
    assert.match(logged[0], /continuing with \.venv/)
  } finally {
    removeRoot(root)
  }
})

test('managedShimRefreshCommand runs the Python shim writer on the venv interpreter, Windows only', () => {
  const root = path.join('C:', 'Stardust', 'hermes-agent')

  assert.equal(managedShimRefreshCommand(root, { isWindows: false }), null, 'shims are Windows-only')

  const noPython = { isWindows: true, isFile: () => false }
  assert.equal(managedShimRefreshCommand(root, noPython), null, 'no interpreter, nothing to run it with')

  const dotVenvPython = path.join(root, '.venv', 'Scripts', 'python.exe')

  const refresh = managedShimRefreshCommand(root, {
    isWindows: true,
    isFile: candidate => candidate === dotVenvPython
  })

  assert.deepEqual(refresh, {
    command: dotVenvPython,
    args: ['-m', 'hermes_cli._install_repair', '--refresh-managed-shims', root],
    cwd: root
  })
})
