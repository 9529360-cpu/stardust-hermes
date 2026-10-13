import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import { probeUpdateVenv, resolveUpdateVenv } from './update-venv'

function fixture(root: string, name: string) {
  const scripts = process.platform === 'win32' ? 'Scripts' : 'bin'
  const python = process.platform === 'win32' ? 'python.exe' : 'python'
  const dir = path.join(root, name, scripts)
  mkdirSync(dir, { recursive: true })
  const pythonPath = path.join(dir, python)
  writeFileSync(pythonPath, 'fixture')
  writeFileSync(path.join(dir, process.platform === 'win32' ? 'stardust.exe' : 'stardust'), 'fixture')

  return pythonPath
}

test('update target follows the selected backend interpreter with only .venv present', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'update-venv-'))

  try {
    const python = fixture(root, '.venv')

    const target = resolveUpdateVenv(root, {
      runtimePython: python,
      installationSource: 'development',
      runtimeMode: 'development'
    })

    assert.equal(target.root, path.join(root, '.venv'))
    assert.equal(target.python, python)
    assert.equal(target.error, null)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('development runtime interpreter disambiguates two virtual environments', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'update-venv-'))

  try {
    fixture(root, '.venv')
    const python = fixture(root, 'venv')

    const target = resolveUpdateVenv(root, {
      runtimePython: python,
      installationSource: 'development',
      runtimeMode: 'development'
    })

    assert.equal(target.root, path.join(root, 'venv'))
    assert.equal(target.python, python)
    assert.equal(target.error, null)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('unknown dual layout fails closed', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'update-venv-'))

  try {
    fixture(root, '.venv')
    fixture(root, 'venv')
    const target = resolveUpdateVenv(root, { installationSource: 'unknown', runtimeMode: 'unknown' })

    assert.equal(target.root, null)
    assert.match(target.error ?? '', /ambiguous/i)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('managed runtime mode selects venv when both candidates exist and source is unknown', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'update-venv-mode-'))

  try {
    fixture(root, '.venv')
    const python = fixture(root, 'venv')

    const target = resolveUpdateVenv(root, {
      installationSource: 'unknown',
      runtimeMode: 'managed'
    })

    assert.equal(target.root, path.join(root, 'venv'))
    assert.equal(target.python, python)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('Python probe accepts the selected environment prefix and imports the CLI', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'update-venv-probe-ok-'))

  try {
    const python = fixture(root, '.venv')

    const target = resolveUpdateVenv(root, {
      runtimePython: python,
      installationSource: 'development',
      runtimeMode: 'development'
    })

    const error = probeUpdateVenv(root, target, ((_python: string, args: string[]) => {
      assert.equal(args[0], '-c')
      assert.match(args[1], /sys\.argv = \['stardust', 'update'\]/)
      assert.ok(JSON.parse(args[2]).includes('hermes_cli.main'))

      return JSON.stringify({ prefix: target.root })
    }) as any)

    assert.equal(error, null)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('Python probe rejects a runnable interpreter from a different environment', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'update-venv-probe-'))

  try {
    const python = fixture(root, '.venv')

    const target = resolveUpdateVenv(root, {
      runtimePython: python,
      installationSource: 'development',
      runtimeMode: 'development'
    })

    const error = probeUpdateVenv(root, target, (() => JSON.stringify({ prefix: root })) as any)

    assert.match(error ?? '', /does not match/i)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('runtime health probe sets update argv before importing required modules', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'update-venv-probe-argv-'))

  try {
    const python = fixture(root, '.venv')
    const target = resolveUpdateVenv(root, {
      runtimePython: python,
      runtimeMode: 'development',
      platform: 'win32'
    })
    let script = ''
    const run = ((_python: string, args: string[]) => {
      script = args[1]
      return JSON.stringify({ prefix: target.root })
    }) as any

    const error = probeUpdateVenv(root, target, run, ['hermes_cli.main'])

    assert.equal(error, null)
    const argvSetup = script.indexOf("sys.argv = ['stardust', 'update']")
    const importCall = script.indexOf('importlib.import_module(name)')
    assert.ok(argvSetup >= 0)
    assert.ok(importCall > argvSetup)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
