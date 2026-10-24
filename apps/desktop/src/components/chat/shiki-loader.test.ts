import { createHighlighter } from 'shiki'
// @vitest-environment node
import { describe, expect, it } from 'vitest'

import { SHIKI_COLOR_REPLACEMENTS, SHIKI_THEME } from './shiki-config'
import { highlightToHtml, loadHighlighter, supportsLanguage } from './shiki-loader'

const examples = {
  typescript: 'const answer: number = 42;\nconsole.log(answer);',
  python: 'def greet(name: str):\n    return f"Hello {name}"',
  bash: '#!/bin/bash\necho "$HOME" # comment'
}

describe('on-demand Shiki', () => {
  it('loads requested grammars and preserves the full-entry HTML exactly', async () => {
    const baseline = await createHighlighter({ langs: ['typescript', 'python', 'bash'], themes: Object.values(SHIKI_THEME) })

    for (const [language, code] of Object.entries(examples)) {
      const expected = baseline.codeToHtml(code, {
        lang: language,
        themes: SHIKI_THEME,
        defaultColor: 'light-dark()',
        colorReplacements: SHIKI_COLOR_REPLACEMENTS
      })

      const actual = await highlightToHtml(language, code, SHIKI_THEME, SHIKI_COLOR_REPLACEMENTS)
      expect(actual).toBe(expected)
      expect(actual).toMatchSnapshot(language)
    }

    baseline.dispose()
  })

  it('shares concurrent grammar loads, resolves aliases, and rejects prototype names', async () => {
    const [a, b] = await Promise.all([loadHighlighter('ts'), loadHighlighter('typescript')])
    expect(a).toBe(b)
    expect(a.getLoadedLanguages()).toContain('typescript')
    expect(supportsLanguage('foo')).toBe(false)
    expect(supportsLanguage('constructor')).toBe(false)
    expect(a.getLoadedThemes().sort()).toEqual(Object.values(SHIKI_THEME).sort())
    expect(a.getLoadedLanguages()).toEqual(expect.arrayContaining(['typescript', 'python', 'bash']))
    expect(a.getLoadedLanguages()).not.toContain('javascript')
  })
})
