import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { describe, expect, it } from 'vitest'

import { readBootLanguage } from './boot-language'

describe('saved boot language', () => {
  it('reads YAML preferences from the selected home without merged defaults', () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'd6-locale-'))

    try {
      fs.writeFileSync(path.join(home, 'config.yaml'), 'display: {language: zh-CN}\n')
      expect(readBootLanguage(home, null)).toBe('zh-CN')
      fs.mkdirSync(path.join(home, 'profiles', 'work'), { recursive: true })
      fs.writeFileSync(path.join(home, 'profiles', 'work', 'config.yaml'), 'display:\n  language: en\n')
      expect(readBootLanguage(home, 'work')).toBe('en')
      expect(readBootLanguage(home, 'missing')).toBeUndefined()
      fs.writeFileSync(path.join(home, 'config.yaml'), '{}\n')
      expect(readBootLanguage(home, null)).toBeUndefined()
    } finally {
      fs.rmSync(home, { recursive: true, force: true })
    }
  })
})
