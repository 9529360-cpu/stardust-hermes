import { atom } from 'nanostores'

import { persistBoolean, storedBoolean } from '@/lib/storage'

// v2: the default flipped to collapsed. v1 was written on every launch, so a stored v1 `false` cannot
// be told apart from an untouched default — it is deliberately not carried over.
const REASONING_COLLAPSED_BY_DEFAULT_STORAGE_KEY = 'hermes.desktop.reasoning.collapsedByDefault.v2'

/** Desktop-local presentation preference; shared backend config must not be changed by a single window. */
export const $reasoningCollapsedByDefault = atom(storedBoolean(REASONING_COLLAPSED_BY_DEFAULT_STORAGE_KEY, true))

// `listen`, not `subscribe`: only an explicit change is persisted, so the stored value is always the
// user's choice and a future default change still reaches everyone who never made one.
$reasoningCollapsedByDefault.listen(value => persistBoolean(REASONING_COLLAPSED_BY_DEFAULT_STORAGE_KEY, value))

export function setReasoningCollapsedByDefault(value: boolean) {
  $reasoningCollapsedByDefault.set(value)
}
