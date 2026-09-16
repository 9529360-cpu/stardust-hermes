import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect } from 'react'

import { $activePresetId } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { registry } from '@/contrib/registry'
import { NEW_SESSION_TITLE, sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { readKey, writeKey } from '@/lib/storage'
import { $repoStatus, registerRepoStatusCwd } from '@/store/coding-status'
import { applyDesktopLayoutPreset, revealDesktopPane } from '@/store/pane-focus'
import { openReviewForPath } from '@/store/review'
import { $currentCwd, $selectedStoredSessionId, $sessions, sessionMatchesStoredId } from '@/store/session'
import { $workingSessionIds } from '@/store/session-states'
import { isAuxiliaryWindow } from '@/store/windows'

export const WORKSPACE_OVERVIEW_PANE_ID = 'workspace-overview'

const PERSONAL_LAYOUT_VERSION = 2
const PERSONAL_LAYOUT_VERSION_KEY = 'hermes.desktop.personalLayoutVersion'

function Card({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section data-workspace-card="">
      <div data-workspace-card-title="">{title}</div>
      {children}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 text-[0.67rem]" data-workspace-metric="">
      <span className="text-(--ui-text-tertiary)">{label}</span>
      <span className="min-w-0 truncate text-right font-medium text-(--ui-text-secondary)">{value}</span>
    </div>
  )
}

function ToolButton({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <Button aria-label={label} onClick={onClick} size="icon-xs" title={label} variant="ghost">
      <Codicon name={icon} size="0.78rem" />
    </Button>
  )
}

export function WorkspaceOverview() {
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
  const sessionLabel = session ? storedSessionTitle(session) : NEW_SESSION_TITLE
  const normalizedCwd = cwd.replace(/[/\\]+$/, '')
  const projectName = normalizedCwd.split(/[/\\]/).filter(Boolean).at(-1) ?? 'No project selected'
  const changedFiles = repoStatus?.files ?? []
  const changedCount = changedFiles.length
  const branch = repoStatus?.branch || 'No repository'
  const staged = repoStatus?.staged ?? changedFiles.filter(file => file.staged).length
  const unstaged = repoStatus?.unstaged ?? changedFiles.filter(file => file.unstaged).length
  const untracked = repoStatus?.untracked ?? changedFiles.filter(file => file.untracked).length
  const summary = !cwd
    ? 'Choose a project to give this task a persistent workspace.'
    : working
      ? changedCount > 0
        ? `The agent is working with ${changedCount} changed file${changedCount === 1 ? '' : 's'} in this workspace.`
        : 'The task is in progress. File changes will appear here as they land.'
      : changedCount > 0
        ? `${changedCount} changed file${changedCount === 1 ? ' is' : 's are'} ready to inspect.`
        : 'The workspace is clean and ready for the next task.'

  return (
    <aside aria-label="Workspace overview" className="h-full min-h-0 overflow-y-auto" data-personal-overview="">
      <div className="flex flex-col" data-workspace-inspector="">
        <Card title="Workspace">
          <div className="flex items-start gap-2.5">
            <span className="grid size-8 shrink-0 place-items-center rounded-xl" data-workspace-project-icon="">
              <Codicon name="repo" size="0.9rem" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[0.78rem] font-semibold text-(--ui-text-primary)">{projectName}</div>
              <div className="mt-0.5 truncate font-mono text-[0.62rem] text-(--ui-text-quaternary)">{branch}</div>
            </div>
            <div className="flex shrink-0 items-center gap-0.5" data-workspace-tools="">
              <ToolButton icon="files" label="Files" onClick={() => revealDesktopPane('files')} />
              <ToolButton icon="diff" label="Review" onClick={() => revealDesktopPane('review')} />
              <ToolButton icon="terminal" label="Terminal" onClick={() => revealDesktopPane('terminal')} />
            </div>
          </div>

          {repoStatus && (
            <div className="mt-3 grid grid-cols-2 gap-2" data-workspace-sync-grid="">
              <Metric label="Ahead" value={repoStatus.ahead} />
              <Metric label="Behind" value={repoStatus.behind} />
            </div>
          )}
        </Card>

        <Card title="Current result">
          <div className="flex items-start gap-2.5">
            <span
              aria-hidden="true"
              className={working ? 'mt-1.5 size-2 shrink-0 rounded-full bg-(--theme-midground)' : 'mt-1.5 size-2 shrink-0 rounded-full bg-(--ui-success)'}
              data-result-status-dot=""
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[0.78rem] font-semibold text-(--ui-text-primary)">{sessionLabel}</div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[0.62rem] text-(--ui-text-tertiary)">
                <span className="rounded-full border border-(--ui-stroke-tertiary) bg-(--ui-control-active-background) px-1.5 py-0.5 font-medium text-(--ui-text-secondary)">
                  {working ? 'In progress' : changedCount > 0 ? 'Ready to review' : 'Ready'}
                </span>
                {session?.model && <span className="max-w-full truncate font-mono text-(--ui-text-quaternary)">{session.model}</span>}
              </div>
            </div>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2" data-result-summary-grid="">
            <div data-result-mini-card="">
              <span className="text-[0.58rem] uppercase tracking-[0.1em] text-(--ui-text-quaternary)">Files</span>
              <strong className="mt-1 block text-base font-semibold text-(--ui-text-primary)">{changedCount}</strong>
            </div>
            <div data-result-mini-card="">
              <span className="text-[0.58rem] uppercase tracking-[0.1em] text-(--ui-text-quaternary)">Staged</span>
              <strong className="mt-1 block text-base font-semibold text-(--ui-text-primary)">{staged}</strong>
            </div>
          </div>
        </Card>

        <Card title="Files touched">
          {changedFiles.length > 0 ? (
            <div className="flex flex-col" data-workspace-file-list="">
              {changedFiles.slice(0, 6).map(file => (
                <button key={file.path} onClick={() => void openReviewForPath(file.path)} type="button" data-workspace-file-row="">
                  <span className="min-w-0 flex-1 truncate font-mono text-[0.64rem] text-(--ui-text-secondary)">{file.path}</span>
                  <span className="shrink-0 text-[0.58rem] font-semibold text-(--ui-text-quaternary)">
                    {file.conflicted ? '!' : file.staged ? 'S' : file.untracked ? 'U' : 'M'}
                  </span>
                </button>
              ))}
              {changedFiles.length > 6 && (
                <button className="mt-1 text-left text-[0.62rem] text-(--theme-midground)" onClick={() => revealDesktopPane('review')} type="button">
                  +{changedFiles.length - 6} more files
                </button>
              )}
            </div>
          ) : (
            <div className="text-[0.68rem] leading-5 text-(--ui-text-tertiary)">No local edits yet.</div>
          )}

          {repoStatus && changedCount > 0 && (
            <div className="mt-3 grid grid-cols-3 gap-1.5" data-workspace-change-grid="">
              <Metric label="Staged" value={staged} />
              <Metric label="Edited" value={unstaged} />
              <Metric label="New" value={untracked} />
            </div>
          )}
        </Card>

        <Card title="Summary">
          <p className="text-[0.7rem] leading-5 text-(--ui-text-secondary)">{summary}</p>
          {cwd && (
            <div className="mt-3 flex flex-col gap-1.5 border-t border-(--ui-stroke-quaternary) pt-3">
              <Metric label="Task" value={working ? 'Working' : 'Idle'} />
              <Metric label="Working tree" value={changedCount === 0 ? 'Clean' : `${changedCount} changed`} />
              <Metric label="Review" value={staged > 0 ? `${staged} staged` : changedCount > 0 ? 'Pending' : 'Clear'} />
            </div>
          )}
        </Card>
      </div>
    </aside>
  )
}

export function schedulePersonalLayoutMigration(): void {
  if (isAuxiliaryWindow()) {
    return
  }

  const current = Number(readKey(PERSONAL_LAYOUT_VERSION_KEY) ?? 0)

  if (Number.isFinite(current) && current >= PERSONAL_LAYOUT_VERSION) {
    return
  }

  queueMicrotask(() => {
    if ($activePresetId.get() === 'default') {
      applyDesktopLayoutPreset('default')
    }

    writeKey(PERSONAL_LAYOUT_VERSION_KEY, String(PERSONAL_LAYOUT_VERSION))
  })
}

export function registerWorkspaceOverviewPane(): () => void {
  return registry.register({
    id: WORKSPACE_OVERVIEW_PANE_ID,
    area: 'panes',
    source: 'core',
    title: 'overview',
    data: {
      placement: 'right',
      collapsible: true,
      uncloseable: true,
      revealAliases: ['overview', 'workspace-overview'],
      width: '310px',
      minWidth: '280px',
      maxWidth: '390px'
    },
    render: () => <WorkspaceOverview />
  })
}