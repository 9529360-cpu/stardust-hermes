import * as fs from 'node:fs'
import * as path from 'node:path'

// js-yaml is bundled into the main process; never parse config in the sandbox.
import { load } from 'js-yaml'

/** Read only the saved presentation preference, before the backend can serve config. */
export function readBootLanguage(home: string, profile: string | null): unknown {
  const configHome = profile && profile !== 'default' ? path.join(home, 'profiles', profile) : home

  try {
    const config = load(fs.readFileSync(path.join(configHome, 'config.yaml'), 'utf8')) as {
      display?: { language?: unknown }
    } | null

    return typeof config?.display?.language === 'string' ? config.display.language : undefined
  } catch {
    // Missing/unreadable config follows the established renderer default and async load.
    return undefined
  }
}
