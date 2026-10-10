import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test } from 'vitest'

const here = path.dirname(fileURLToPath(import.meta.url))
const mainSource = fs.readFileSync(path.join(here, 'main.ts'), 'utf8')

function extractFunction(source: string, name: string): string {
  const start = source.indexOf(`function ${name}(`)
  assert.notEqual(start, -1, `function ${name} not found in main.ts`)

  const rest = source.slice(start)
  const next = rest.slice(1).search(/\n(?:async )?function /)

  return next === -1 ? rest : rest.slice(0, next + 1)
}

test('local secondary windows surface bounded renderer load failures', () => {
  for (const name of ['spawnSecondaryWindow', 'spawnBrowserWindow', 'createInstanceWindow']) {
    const fn = extractFunction(mainSource, name)

    assert.match(fn, /reloadOnFailedLoad:\s*true/, `${name} must opt into failed-load recovery`)
    assert.match(
      fn,
      /onFailedLoadBudgetExhausted:\s*details\s*=>/,
      `${name} must replace a blank window with the renderer error page`
    )
    assert.match(fn, /loadRendererLoadErrorPage\(win/, `${name} must reuse the shared error page`)
  }
})
