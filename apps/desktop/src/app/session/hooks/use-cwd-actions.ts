import { type MutableRefObject, useCallback, useRef } from 'react'

import { useI18n } from '@/i18n'
import { notifyError } from '@/store/notifications'
import {
  $currentCwd,
  $newChatWorkspaceTargetGeneration,
  setCurrentBranch,
  setCurrentCwd,
  setSelectedProjectCwd,
  setWorkspaceCwdOwner
} from '@/store/session'
import type { SessionRuntimeInfo } from '@/types/hermes'

interface CwdActionsOptions {
  activeSessionIdRef: MutableRefObject<string | null>
  selectedStoredSessionIdRef: MutableRefObject<string | null>
  onSessionRuntimeInfo?: (info: Pick<SessionRuntimeInfo, 'branch' | 'cwd'>) => void
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function useCwdActions({
  activeSessionIdRef,
  selectedStoredSessionIdRef,
  onSessionRuntimeInfo,
  requestGateway
}: CwdActionsOptions) {
  const { t } = useI18n()
  const copy = t.desktop
  const workspaceChangeRequestRef = useRef(0)

  const refreshProjectBranch = useCallback(
    async (cwd: string) => {
      const target = cwd.trim()

      if (!target || activeSessionIdRef.current) {
        return
      }

      try {
        const info = await requestGateway<{ branch?: string; cwd?: string }>('config.get', {
          key: 'project',
          cwd: target
        })

        if (!activeSessionIdRef.current && ($currentCwd.get() || target) === (info.cwd || target)) {
          setCurrentBranch(info.branch || '')
        }
      } catch {
        setCurrentBranch('')
      }
    },
    [activeSessionIdRef, requestGateway]
  )

  const changeSessionCwd = useCallback(
    async (cwd: string): Promise<boolean> => {
      const trimmed = cwd.trim()

      if (!trimmed) {
        return false
      }

      // Ref, not the closure-captured prop: this hook's consumers are memoized
      // on a stable actions object, so the prop can still name the previously
      // focused chat. Re-anchoring the wrong session's workspace would point
      // that agent's terminal/file tools at another conversation's project.
      const sessionId = activeSessionIdRef.current
      const selectedStoredSessionId = selectedStoredSessionIdRef.current

      const requestId = ++workspaceChangeRequestRef.current

      if (Boolean(sessionId) !== Boolean(selectedStoredSessionId)) {
        notifyError(new Error(copy.cwdChangePending), copy.cwdChangeFailed)

        return false
      }

      if (!sessionId) {
        setSelectedProjectCwd(trimmed)
        setWorkspaceCwdOwner(null)

        const workspaceGeneration = $newChatWorkspaceTargetGeneration.get()

        try {
          const info = await requestGateway<{ branch?: string; cwd?: string }>('config.get', {
            key: 'project',
            cwd: trimmed
          })

          if (
            workspaceChangeRequestRef.current !== requestId ||
            $newChatWorkspaceTargetGeneration.get() !== workspaceGeneration ||
            activeSessionIdRef.current
          ) {
            return false
          }

          // Adopt the backend's normalized cwd so the persisted workspace and
          // branch stay consistent with what the agent will use.
          if (info.cwd) {
            setSelectedProjectCwd(info.cwd)
          }

          setCurrentBranch(info.branch || '')

          return true
        } catch {
          if ($newChatWorkspaceTargetGeneration.get() === workspaceGeneration && !activeSessionIdRef.current) {
            setCurrentBranch('')
          }

          return false
        }
      }

      try {
        const info = await requestGateway<SessionRuntimeInfo>('session.cwd.set', {
          session_id: sessionId,
          cwd: trimmed
        })

        if (
          activeSessionIdRef.current !== sessionId ||
          selectedStoredSessionIdRef.current !== selectedStoredSessionId ||
          workspaceChangeRequestRef.current !== requestId
        ) {
          return false
        }

        const resolvedCwd = info.cwd || trimmed

        setCurrentCwd(resolvedCwd)
        setWorkspaceCwdOwner(selectedStoredSessionId)

        setCurrentBranch(info.branch || '')
        onSessionRuntimeInfo?.({ branch: info.branch || '', cwd: resolvedCwd })

        return true
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        const isBusy = /session busy|4009/.test(message)

        if (workspaceChangeRequestRef.current === requestId) {
          notifyError(err, isBusy ? copy.cwdChangeBusy : copy.cwdChangeFailed)
        }

        return false
      }
    },
    [
      activeSessionIdRef,
      copy,
      onSessionRuntimeInfo,
      requestGateway,
      selectedStoredSessionIdRef,
      workspaceChangeRequestRef
    ]
  )

  return { changeSessionCwd, refreshProjectBranch }
}
