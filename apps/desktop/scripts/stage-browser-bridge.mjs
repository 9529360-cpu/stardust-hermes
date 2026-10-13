#!/usr/bin/env node
// stage-browser-bridge.mjs — stages the existing-Chrome bridge and its pinned runtime packages
//
// The bridge (experiments/playwright-mcp-host-bridge) runs as its own Electron-as-node child and imports
// @playwright/mcp and @modelcontextprotocol/sdk at runtime. electron-builder's node_modules collector is off
// (scripts/before-build.mjs), so a packaged app shipped the bridge's .mjs files without those packages and
// "Connect Chrome" could never start (owner field case 2026-10-12). This stages the bridge sources plus a
// `npm ci --omit=dev` of the bridge's own lockfile into dist/browser-bridge, which `dist/**` ships unpacked.
//
// Usage: node scripts/stage-browser-bridge.mjs

import { spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { isMain } from './utils.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const projectRoot = resolve(here, '..')
const bridgeSource = resolve(projectRoot, '../../experiments/playwright-mcp-host-bridge')

export const BRIDGE_STAGE_DIR = resolve(projectRoot, 'dist/browser-bridge')

/** How to run npm: the npm CLI script this build runs under (no shell needed), else `npm` from PATH. */
function npmInvocation(args) {
  const npmCli = process.env.npm_execpath || ''

  if (/\.c?js$/.test(npmCli)) {
    return { command: process.execPath, args: [npmCli, ...args], shell: false }
  }

  // Windows resolves npm.cmd only through a shell; the args are fixed flags, never user input.
  return process.platform === 'win32'
    ? { command: 'npm.cmd', args, shell: true }
    : { command: 'npm', args, shell: false }
}

/** The bridge's pinned package specs, e.g. `@playwright/mcp@0.0.83`, read from its package.json. */
function pinnedSpecs(manifest) {
  return Object.entries(manifest.dependencies || {}).map(([name, version]) => `${name}@${version}`)
}

export function stageBrowserBridge({ source = bridgeSource, dest = BRIDGE_STAGE_DIR, run = spawnSync } = {}) {
  const manifest = JSON.parse(readFileSync(join(source, 'package.json'), 'utf8'))

  rmSync(dest, { force: true, recursive: true })
  mkdirSync(join(dest, 'src'), { recursive: true })

  for (const name of ['package.json', 'package-lock.json']) {
    cpSync(join(source, name), join(dest, name))
  }

  for (const name of readdirSync(join(source, 'src'))) {
    if (name.endsWith('.mjs')) {
      cpSync(join(source, 'src', name), join(dest, 'src', name))
    }
  }

  // The bridge's own lockfile, production packages only, no install scripts (no browser downloads:
  // the bridge drives the user's Chrome through the official extension). --no-workspaces keeps npm
  // from attaching this folder to the repository's workspace root.
  const npm = npmInvocation(['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', '--prefer-offline', '--no-workspaces'])
  const result = run(npm.command, npm.args, { cwd: dest, shell: npm.shell, stdio: 'inherit' })

  if (result.status !== 0) {
    throw new Error(`npm ci for the browser bridge failed (exit ${result.status})`)
  }

  for (const spec of pinnedSpecs(manifest)) {
    const name = spec.slice(0, spec.lastIndexOf('@'))
    const version = spec.slice(spec.lastIndexOf('@') + 1)
    const manifestPath = join(dest, 'node_modules', ...name.split('/'), 'package.json')
    const staged = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, 'utf8')).version : undefined

    if (staged !== version) {
      throw new Error(`${name} staged at ${staged ?? 'nothing'}, but the bridge pins ${version}`)
    }
  }

  // The bridge locates Playwright MCP from its src/ directory (src/mcp-driver.mjs); prove that lands here.
  const resolved = createRequire(join(dest, 'src', 'mcp-driver.mjs')).resolve('@playwright/mcp/package.json')

  if (!resolved.startsWith(dest)) {
    throw new Error(`@playwright/mcp resolved outside the staged browser bridge (${resolved})`)
  }

  return dest
}

if (isMain(import.meta.url)) {
  const dest = stageBrowserBridge()
  console.log(`[stage-browser-bridge] staged ${dest}`)
}
