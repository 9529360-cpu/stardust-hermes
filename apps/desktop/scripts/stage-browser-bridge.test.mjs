import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'vitest'

import { stageBrowserBridge } from './stage-browser-bridge.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const bridgeSource = path.resolve(here, '../../../experiments/playwright-mcp-host-bridge')
const pins = JSON.parse(fs.readFileSync(path.join(bridgeSource, 'package.json'), 'utf8')).dependencies

/** A stand-in for `npm ci` that installs each pinned package (optionally at another version). */
function fakeNpm({ status = 0, versions = {} } = {}) {
  const calls = []

  return {
    calls,
    run(command, args, options) {
      calls.push({ args, cwd: options.cwd })

      for (const [name, version] of Object.entries(pins)) {
        const dir = path.join(options.cwd, 'node_modules', ...name.split('/'))
        fs.mkdirSync(dir, { recursive: true })
        fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ name, version: versions[name] ?? version }))
      }

      return { status }
    }
  }
}

function withTemp(fn) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'stardust-bridge-stage-'))

  try {
    return fn(path.join(root, 'browser-bridge'))
  } finally {
    fs.rmSync(root, { force: true, recursive: true })
  }
}

test('stages the bridge sources beside its pinned packages from the bridge lockfile', () => {
  withTemp(dest => {
    const npm = fakeNpm()

    assert.equal(stageBrowserBridge({ dest, run: npm.run }), dest)

    for (const name of ['main.mjs', 'mcp-driver.mjs', 'broker-client.mjs', 'protocol.mjs']) {
      assert.ok(fs.existsSync(path.join(dest, 'src', name)), name)
    }

    assert.ok(fs.existsSync(path.join(dest, 'package-lock.json')))
    assert.equal(npm.calls.length, 1)
    assert.equal(npm.calls[0].cwd, dest)
    assert.ok(npm.calls[0].args.includes('ci') && npm.calls[0].args.includes('--omit=dev'))
    assert.ok(npm.calls[0].args.includes('--ignore-scripts'), 'no browser downloads at build time')
  })
})

test('refuses a staged package that drifted from the pin', () => {
  withTemp(dest => {
    const npm = fakeNpm({ versions: { '@playwright/mcp': '0.0.1' } })

    assert.throws(() => stageBrowserBridge({ dest, run: npm.run }), /@playwright\/mcp staged at 0\.0\.1/)
  })
})

test('fails the build when npm ci fails', () => {
  withTemp(dest => {
    assert.throws(() => stageBrowserBridge({ dest, run: fakeNpm({ status: 1 }).run }), /npm ci for the browser bridge failed/)
  })
})
