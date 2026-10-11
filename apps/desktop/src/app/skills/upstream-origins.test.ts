import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

// Repository-policy invariant: the desktop renderer must not reach the upstream
// Hermes skills hub or plugin catalog. Both were embedded as iframes and
// postMessage pickers, so a source-level check on their origin and picker query
// is the practical boundary for this rule. Vitest runs from apps/desktop.
const SRC_ROOT = resolve(process.cwd(), 'src')
const UPSTREAM_MARKERS = ['hermes-agent.nousresearch.com', 'embed=picker']

function productionSources(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)

    if (statSync(path).isDirectory()) {
      return productionSources(path)
    }

    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : []
  })
}

describe('desktop renderer never requests the upstream hub or catalog', () => {
  it('no production source names the upstream hub origin or its picker query', () => {
    const offenders = productionSources(SRC_ROOT)
      .filter(file => UPSTREAM_MARKERS.some(marker => readFileSync(file, 'utf8').includes(marker)))
      .map(file => relative(SRC_ROOT, file))

    expect(offenders).toEqual([])
  }, 60_000)
})
