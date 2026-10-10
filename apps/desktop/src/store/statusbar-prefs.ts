import { Codecs, persistentAtom } from '@/lib/persisted'
import { readKey } from '@/lib/storage'

// v1 (`hermes.desktop.statusbarHidden`) hid the approval pill by default;
// v2 corrected that. v3 exposes the bottom usage entry while retaining other
// v2 customizations; an explicit v3 hide remains persistent.
const STATUSBAR_HIDDEN_STORAGE_KEY = 'hermes.desktop.statusbarHidden.v3'
const PREVIOUS_HIDDEN_STORAGE_KEY = 'hermes.desktop.statusbarHidden.v2'
const LEGACY_HIDDEN_STORAGE_KEY = 'hermes.desktop.statusbarHidden'
// Keep the existing key so an explicit user choice survives this product skin.
const STATUSBAR_VISIBLE_STORAGE_KEY = 'hermes.desktop.statusbarVisible.v2'

// Surface the bottom usage entry on new installs while preserving an existing
// explicit choice to hide the whole bar.
export const $statusbarVisible = persistentAtom(STATUSBAR_VISIBLE_STORAGE_KEY, true, Codecs.bool)

export function toggleStatusbarVisible() {
  $statusbarVisible.set(!$statusbarVisible.get())
}

// Items the bar hides until the user turns them on from its context menu. The
// bar's job is to answer "is the backend healthy, where am I, what's it doing" —
// route shortcuts (cron/webhooks/agents) and the terminal toggle are
// navigation, not status, so they start out of the way. The approval pill
// (the yolo zap) stays: whether dangerous commands run unasked is state the
// user should see at a glance. The per-turn
// live diagnostics (running/session timers, cache hit rate, tokens/sec) stay
// optional; the context/usage menu is now the default bottom entry.
export const STATUSBAR_HIDDEN_BY_DEFAULT: readonly string[] = [
  'agents',
  'cache-hit-rate',
  'cron',
  'running-timer',
  'session-timer',
  'system-resources',
  'terminal',
  'tokens-per-second',
  'webhooks'
]

// Stored as the explicit hidden set (not the visible one) so an item added to
// the bar in a later version shows up for existing users instead of silently
// staying off. An empty array is a real value — the user turned everything on —
// so this uses a sanitizing json codec rather than Codecs.stringArray, which
// drops the key when empty and would resurrect the defaults on next launch.
const sanitizeHiddenIds = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string' && id.length > 0) : []

function hiddenSeed(): string[] {
  const previous = readKey(PREVIOUS_HIDDEN_STORAGE_KEY)
  const raw = previous ?? readKey(LEGACY_HIDDEN_STORAGE_KEY)

  if (raw === null) {
    return [...STATUSBAR_HIDDEN_BY_DEFAULT]
  }

  try {
    const hidden = sanitizeHiddenIds(JSON.parse(raw)).filter(id => id !== 'approval-mode')
    // v2 shipped the context meter hidden by default. Unhide it for the new
    // bottom token/cache entry while retaining every other prior preference.

    return hidden.filter(id => id !== 'context-usage')
  } catch {
    return [...STATUSBAR_HIDDEN_BY_DEFAULT]
  }
}

export const $statusbarHiddenIds = persistentAtom<string[]>(
  STATUSBAR_HIDDEN_STORAGE_KEY,
  hiddenSeed(),
  Codecs.json<string[]>(sanitizeHiddenIds)
)

export function setStatusbarItemVisible(id: string, visible: boolean) {
  const hidden = $statusbarHiddenIds.get()

  if (visible === !hidden.includes(id)) {
    return
  }

  $statusbarHiddenIds.set(visible ? hidden.filter(entry => entry !== id) : [...hidden, id])
}

/** Pure so the menu can derive its reset row's disabled state from the hidden
 *  list it already subscribes to, rather than reading the atom out of band.
 *  Set-compared: order is incidental (items are appended as they're hidden) and
 *  a duplicated id shouldn't read as a customization. */
export function isStatusbarLayoutDefault(hidden: readonly string[]) {
  const ids = new Set(hidden)

  return ids.size === STATUSBAR_HIDDEN_BY_DEFAULT.length && STATUSBAR_HIDDEN_BY_DEFAULT.every(id => ids.has(id))
}

/** Put the show/hide set back to what ships. Only touches item layout — whole-bar
 *  visibility is a separate preference, and resetting from the bar's own menu
 *  shouldn't make the bar the user is right-clicking disappear. */
export function resetStatusbarLayout() {
  $statusbarHiddenIds.set([...STATUSBAR_HIDDEN_BY_DEFAULT])
}
