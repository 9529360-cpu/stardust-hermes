import type { CodeHighlighterPlugin } from '@streamdown/code'
import { bundledLanguages } from 'shiki/langs'

import { SHIKI_THEME } from './shiki-config'
import { loadHighlighter, supportsLanguage } from './shiki-loader'

/** Streamdown's code-card contract, sharing the chat's on-demand Shiki core. */
export const code: CodeHighlighterPlugin = {
  name: 'shiki',
  type: 'code-highlighter',
  getSupportedLanguages: () => Object.keys(bundledLanguages) as ReturnType<CodeHighlighterPlugin['getSupportedLanguages']>,
  getThemes: () => [SHIKI_THEME.light, SHIKI_THEME.dark],
  supportsLanguage,
  highlight: ({ code, language, themes }, callback) => {
    void loadHighlighter(language)
      .then(highlighter => {
        const result = highlighter.codeToTokens(code, {
          lang: supportsLanguage(language) ? language : 'text',
          themes: { light: themes[0], dark: themes[1] }
        })

        callback?.(result)
      })
      .catch(() => {
        // The visible SyntaxHighlighter override retains the plain code fallback.
      })

    return null
  }
}
