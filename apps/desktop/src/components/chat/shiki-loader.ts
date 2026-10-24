import type { HighlighterCore } from 'shiki/core'
import { bundledLanguages } from 'shiki/langs'
import { bundledThemes } from 'shiki/themes'

import { SHIKI_THEME } from './shiki-config'

let highlighterPromise: Promise<HighlighterCore> | undefined
const languages = new Map<string, Promise<void>>()
const themes = new Map<string, Promise<void>>()

export function supportsLanguage(language: string): boolean {
  return Object.hasOwn(bundledLanguages, language)
}

function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    highlighterPromise = Promise.all([import('shiki/core'), import('shiki/engine/oniguruma')])
      .then(([{ createHighlighterCore }, { createOnigurumaEngine }]) =>
        createHighlighterCore({ langs: [], themes: [], engine: createOnigurumaEngine(import('shiki/wasm')) })
      )
      .catch(error => {
        highlighterPromise = undefined
        throw error
      })
  }

  return highlighterPromise
}

/** Registry entries are dynamic imports; neither the core nor grammars load until requested. */
export async function loadHighlighter(language: string, theme = SHIKI_THEME as { dark: string; light: string }) {
  const highlighter = await getHighlighter()
  const pending: Promise<void>[] = []

  if (supportsLanguage(language)) {
    if (!languages.has(language)) {
      const load = bundledLanguages[language as keyof typeof bundledLanguages]()
        .then(grammar => highlighter.loadLanguage(grammar.default))
        .catch(error => {
          languages.delete(language)
          throw error
        })

      languages.set(language, load)
    }

    pending.push(languages.get(language)!)
  }

  for (const name of new Set([theme.dark, theme.light])) {
    if (!themes.has(name)) {
      if (!Object.hasOwn(bundledThemes, name)) {
        throw new Error(`Unknown Shiki theme: ${name}`)
      }

      const load = bundledThemes[name as keyof typeof bundledThemes]()
        .then(theme => highlighter.loadTheme(theme.default))
        .catch(error => {
          themes.delete(name)
          throw error
        })

      themes.set(name, load)
    }

    pending.push(themes.get(name)!)
  }

  await Promise.all(pending)

  return highlighter
}

export async function highlightToHtml(
  language: string,
  code: string,
  theme: { dark: string; light: string },
  colorReplacements: Record<string, Record<string, string>>
): Promise<string> {
  const highlighter = await loadHighlighter(language, theme)

  return highlighter.codeToHtml(code, {
    lang: supportsLanguage(language) ? language : 'text',
    themes: theme,
    defaultColor: 'light-dark()',
    colorReplacements
  })
}
