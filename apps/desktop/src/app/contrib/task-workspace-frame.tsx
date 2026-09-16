import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { DiffCount } from '@/components/ui/diff-count'
import { Tip } from '@/components/ui/tooltip'
import { NEW_SESSION_TITLE, sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { $repoStatus, registerRepoStatusCwd } from '@/store/coding-status'
import { revealDesktopPane } from '@/store/pane-focus'
import { $currentCwd, $selectedStoredSessionId, $sessions, sessionMatchesStoredId } from '@/store/session'
import { $workingSessionIds } from '@/store/session-states'

export function TaskWorkspaceFrame({ children }: { children: ReactNode }) {
  const cwd = useStore($currentCwd)
  const repoStatus = useStore($repoStatus)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const sessions = useStore($sessions)
  const workingSessionIds = useStore($workingSessionIds)

  useEffect(() => registerRepoStatusCwd(cwd), [cwd])

  const session = selectedStoredSessionId
    ? sessions.find(candidate => sessionMatchesStoredId(candidate, selectedStoredSessionId))
    : undefined
  const working = session ? workingSessionIds.includes(session.id) : workingSessionIds.length > 0
  const title = session ? storedSessionTitle(session) : NEW_SESSION_TITLE
  const normalizedCwd = cwd.replace(/[/\\]+$/, '')
  const projectName = normalizedCwd.split(/[/\\]/).filter(Boolean).at(-1) ?? 'No project'
  const changedFiles = repoStatus?.files ?? []
  const added = changedFiles.reduce((sum, file) => sum + file.added, 0)
  const removed = changedFiles.reduce((sum, file) => sum + file.removed, 0)

  return (
    <section className="flex h-full min-h-0 flex-col" data-task-workspace="">
      <header className="shrink-0" data-task-header="">
        <div className="flex min-w-0 items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-1.5 text-[0.64rem] font-medium text-(--ui-text-quaternary)">
              <span className="truncate">{projectName}</span>
              {repoStatus?.branch && (
                <>
                  <span aria-hidden="true">/</span>
                  <span className="truncate font-mono">{repoStatus.branch}</span>
                </>
              )}
            </div>
            <div className="mt-0.5 flex min-w-0 items-center gap-2">
              {working && <span aria-label="Running" className="size-1.5 shrink-0 rounded-full bg-(--theme-midground)" />}
              <h1 className="truncate text-[0.9rem] font-semibold tracking-[-0.01em] text-(--ui-text-primary)">{title}</h1>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1" data-task-toolbar="">
            {changedFiles.length > 0 && (
              <button
                className="mr-1 inline-flex h-7 items-center gap-1.5 rounded-md px-1.5 text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background)"
                onClick={() => revealDesktopPane('review')}
                type="button"
              >
                <Codicon name="diff" size="0.76rem" />
                <span className="text-[0.63rem] font-medium">{changedFiles.length}</span>
                <DiffCount added={added} className="text-[0.61rem]" removed={removed} />
              </button>
            )}

            <Tip label="Review changes">
              <Button aria-label="Review changes" onClick={() => revealDesktopPane('review')} size="icon-xs" variant="ghost">
                <Codicon name="diff" size="0.82rem" />
              </Button>
            </Tip>
            <Tip label="Files">
              <Button aria-label="Files" disabled={!cwd} onClick={() => revealDesktopPane('files')} size="icon-xs" variant="ghost">
                <Codicon name="files" size="0.82rem" />
              </Button>
            </Tip>
            <Tip label="Terminal">
              <Button aria-label="Terminal" onClick={() => revealDesktopPane('terminal')} size="icon-xs" variant="ghost">
                <Codicon name="terminal" size="0.82rem" />
              </Button>
            </Tip>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1" data-task-transcript="">
        {children}
      </div>
    </section>
  )
}
