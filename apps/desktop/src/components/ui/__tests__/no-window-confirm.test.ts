import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

function collectSourceFiles(dir: string): string[] {
  const results: string[] = []

  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === 'dist' || entry === '__tests__') {
      continue
    }

    const fullPath = join(dir, entry)
    const stat = statSync(fullPath)

    if (stat.isDirectory()) {
      results.push(...collectSourceFiles(fullPath))
    } else if (entry.endsWith('.ts') || entry.endsWith('.tsx')) {
      results.push(fullPath)
    }
  }

  return results
}

describe('desktop confirmation contract', () => {
  it('uses the themed confirm owner instead of blocking window.confirm', () => {
    const violations: string[] = []
    const srcDir = resolve(__dirname, '../..')

    for (const filePath of collectSourceFiles(srcDir)) {
      const content = readFileSync(filePath, 'utf8')

      if (!content.includes('window.confirm(')) {
        continue
      }

      const relativePath = filePath.replace(srcDir + '/', '')
      const line = content.slice(0, content.indexOf('window.confirm(')).split('\n').length
      violations.push(`${relativePath}:${line} uses window.confirm — call confirm() from @/store/confirm`)
    }

    expect(violations, violations.join('\n')).toEqual([])
  })
})
