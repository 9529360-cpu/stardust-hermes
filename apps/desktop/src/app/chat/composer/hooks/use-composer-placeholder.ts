import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { useI18n } from '@/i18n'
import { resetBrowseState } from '@/store/composer-input-history'
import { $currentCwd } from '@/store/session'

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
 *
 * A new project-scoped thread is the exception to the randomized starter pool:
 * use the locale's first starter (the coding/build prompt) so the Codex-style
 * workspace never randomly falls back to generic or Hermes-branded chat copy.
 */
export function useComposerPlaceholder({ disabled, reconnecting, sessionId }: UseComposerPlaceholderOptions): string {
  const { t } = useI18n()
  const cwd = useStore($currentCwd)
  const projectScoped = Boolean(cwd.trim())
  const newSessionPlaceholders = t.composer.newSessionPlaceholders
  const followUpPlaceholders = t.composer.followUpPlaceholders
  const projectPlaceholder = newSessionPlaceholders[0] ?? t.composer.message
  const newPlaceholder = () => (projectScoped ? projectPlaceholder : pickPlaceholder(newSessionPlaceholders))

  const [restingPlaceholder, setRestingPlaceholder] = useState(() =>
    sessionId ? pickPlaceholder(followUpPlaceholders) : newPlaceholder()
  )

  const prevSessionIdRef = useRef(sessionId)

  // A draft can move from no workspace into a project without receiving a
  // stored session id yet. Keep the starter aligned with that visible context.
  useEffect(() => {
    if (!sessionId) {
      setRestingPlaceholder(projectScoped ? projectPlaceholder : pickPlaceholder(newSessionPlaceholders))
    }
  }, [newSessionPlaceholders, projectPlaceholder, projectScoped, sessionId])

  // eslint-disable-next-line no-restricted-syntax -- legitimate non-atom ref write (see eslint rule comment)
  useEffect(() => {
    const prev = prevSessionIdRef.current
    prevSessionIdRef.current = sessionId

    if (prev === sessionId) {
      return
    }

    // null → id: the new session we're already in just got persisted. Keep the
    // starter we showed instead of swapping to a follow-up under the user.
    if (prev == null && sessionId) {
      return
    }

    resetBrowseState(prev)
    setRestingPlaceholder(
      sessionId
        ? pickPlaceholder(followUpPlaceholders)
        : projectScoped
          ? projectPlaceholder
          : pickPlaceholder(newSessionPlaceholders)
    )
  }, [followUpPlaceholders, newSessionPlaceholders, projectPlaceholder, projectScoped, sessionId])

  // When the transport is disabled it's because the gateway isn't open.
  // Distinguish a cold start ("Starting Hermes...") from a dropped connection
  // we're trying to restore. During reconnect, keep the textbox editable so a
  // flaky network doesn't block drafting; only submit/backend actions stay
  // disabled until the gateway is open again.
  return disabled
    ? reconnecting
      ? t.composer.placeholderReconnecting
      : t.composer.placeholderStarting
    : restingPlaceholder
}
