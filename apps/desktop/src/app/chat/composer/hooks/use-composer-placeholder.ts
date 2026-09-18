import { useEffect, useRef, useState } from 'react'

import { useI18n } from '@/i18n'
import { resetBrowseState } from '@/store/composer-input-history'

import { pickPlaceholder } from '../composer-utils'

interface UseComposerPlaceholderOptions {
  disabled: boolean
  reconnecting: boolean
  sessionId: null | string | undefined
}

/**
 * The composer's placeholder text. A resting starter (new session) / continuation
 * (existing session) is picked once and only re-rolled when we genuinely move to
 * a *different* conversation — the null→id persist of a freshly-started session
 * keeps its starter so the text doesn't flip mid-stream. While the transport is
 * down, it swaps to a reconnecting / starting message instead.
 */
export function useComposerPlaceholder({ disabled, reconnecting, sessionId }: UseComposerPlaceholderOptions): string {
  const { locale, t } = useI18n()
  const newSessionPlaceholders = t.composer.newSessionPlaceholders
  const followUpPlaceholders = t.composer.followUpPlaceholders

  const [restingPlaceholder, setRestingPlaceholder] = useState(() =>
    pickPlaceholder(sessionId ? followUpPlaceholders : newSessionPlaceholders)
  )

  const prevSessionIdRef = useRef(sessionId)
  const prevLocaleRef = useRef(locale)

  // eslint-disable-next-line no-restricted-syntax -- legitimate non-atom ref write (see eslint rule comment)
  useEffect(() => {
    const prev = prevSessionIdRef.current
    const localeChanged = prevLocaleRef.current !== locale
    const persistedFreshSession = prev == null && Boolean(sessionId)
    prevSessionIdRef.current = sessionId
    prevLocaleRef.current = locale

    if (prev === sessionId && !localeChanged) {
      return
    }

    // null → id means the new session we were already composing in was just
    // persisted. Keep a starter placeholder; a locale change may translate it
    // in place, but persistence alone must not flip it to a follow-up.
    if (persistedFreshSession && !localeChanged) {
      return
    }

    if (prev !== sessionId && !persistedFreshSession) {
      resetBrowseState(prev)
    }

    const pool = persistedFreshSession
      ? newSessionPlaceholders
      : sessionId
        ? followUpPlaceholders
        : newSessionPlaceholders
    setRestingPlaceholder(pickPlaceholder(pool))
  }, [followUpPlaceholders, locale, newSessionPlaceholders, sessionId])

  // When the transport is disabled it's because the gateway isn't open.
  // Distinguish a cold start from a dropped connection
  // we're trying to restore. During reconnect, keep the textbox editable so a
  // flaky network doesn't block drafting; only submit/backend actions stay
  // disabled until the gateway is open again.
  return disabled
    ? reconnecting
      ? t.composer.placeholderReconnecting
      : t.composer.placeholderStarting
    : restingPlaceholder
}
