import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { NEW_SESSION_TITLE, sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { $repoStatus, registerRepoStatusCwd } from '@/store/coding-status'
import { $currentCwd, $selectedStoredSessionId, $sessions, sessionMatchesStoredId } from '@/store/session'
import { $workingSessionIds } from '@/store/session-states'

const PHASES = [
  { icon: 'lightbulb', label: 'Understanding' },
  { icon: 'list-tree', label: 'Planning' },
  { icon: 'sparkle', label: 'Working' },
  { icon: 'checklist', label: 'Review' }
] as const

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
  const projectName = normalizedCwd.split(/[/\\]/).filter(Boolean).at(-1) ?? 'No workspace'
  const changedCount = repoStatus?.files.length ?? 0
  const stagedCount = repoStatus?.staged ?? (repoStatus ? repoStatus.files.filter(file => file.staged).length : 0)
  const activePhase = working ? 2 : changedCount > 0 ? 3 : session ? 1 : cwd ? 1 : 0

  return (
    <section className="flex h-full min-h-0 flex-col" data-task-workspace="">
      <header className="shrink-0" data-task-header="">
        <div className="flex min-w-0 items-start justify-between gap-5">
          <div className="min-w-0">
            <div className="mb-1 flex items-center gap-2 text-[0.68rem] font-medium text-(--ui-text-tertiary)">
              <span className="truncate">{projectName}</span>
              {repoStatus?.branch && (
                <>
                  <span aria-hidden="true" className="text-(--ui-text-quaternary)">/</span>
                  <span className="truncate font-mono text-(--ui-text-quaternary)">{repoStatus.branch}</span>
                </>
              )}
            </div>
            <h1 className="truncate text-[1.05rem] font-semibold tracking-[-0.015em] text-(--ui-text-primary)">{title}</h1>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {changedCount > 0 && (
              <span className="rounded-full border border-(--ui-stroke-tertiary) px-2 py-1 text-[0.64rem] text-(--ui-text-tertiary)">
                {changedCount} changed
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 rounded-full border border-(--ui-stroke-secondary) bg-(--ui-control-active-background) px-2.5 py-1 text-[0.66rem] font-medium text-(--ui-text-secondary)">
              <span
                aria-hidden="true"
                className={working ? 'size-1.5 rounded-full bg-(--theme-midground)' : 'size-1.5 rounded-full bg-(--ui-success)'}
              />
              {working ? 'In progress' : stagedCount > 0 ? 'Ready to review' : 'Ready'}
            </span>
          </div>
        </div>
      </header>

      <div className="shrink-0" data-task-progress="">
        {PHASES.map((phase, index) => {
          const complete = index < activePhase
          const active = index === activePhase

          return (
            <div
              className="flex min-w-0 flex-1 items-center gap-2"
              data-active={active ? '' : undefined}
              data-complete={complete ? '' : undefined}
              key={phase.label}
            >
              <span className="grid size-6 shrink-0 place-items-center rounded-full" data-task-phase-icon="">
                <Codicon name={complete ? 'check' : phase.icon} size="0.78rem" />
              </span>
              <span className="truncate text-[0.66rem] font-medium">{phase.label}</span>
              {index < PHASES.length - 1 && <span aria-hidden="true" className="h-px min-w-3 flex-1" data-task-phase-line="" />}
            </div>
          )
        })}
      </div>

      <div className="min-h-0 flex-1" data-task-transcript="">
        {children}
      </div>
    </section>
  )
}
