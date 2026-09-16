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

export const WORKSPACE_OVERVIEW_PANE_ID = 'workspace-overview'

const PERSONAL_LAYOUT_VERSION = 1
const PERSONAL_LAYOUT_VERSION_KEY = 'hermes.desktop.personalLayoutVersion'

function Card({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-widget-surface-background) px-3 py-3 shadow-[inset_0_1px_0_color-mix(in_srgb,white_5%,transparent)]">
      <div className="mb-2 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-(--ui-text-tertiary)">{title}</div>
      {children}
    </section>
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

  return (
    <aside
      aria-label="Workspace overview"
      className="flex h-full min-h-0 flex-col overflow-y-auto bg-(--ui-sidebar-surface-background) px-3 pb-3 pt-[calc(var(--titlebar-height)+0.5rem)] text-(--ui-text-secondary)"
      data-personal-overview=""
    >
      <div className="mb-3 px-1">
        <div className="text-[0.72rem] font-semibold tracking-[0.12em] text-(--ui-text-primary)">WORKSPACE</div>
        <div className="mt-1 truncate text-[0.68rem] text-(--ui-text-tertiary)">{projectName}</div>
      </div>

      <div className="flex flex-col gap-3">
        <Card title="Current result">
          <div className="flex items-start gap-2.5">
            <span
              aria-hidden="true"
              className={
                working
                  ? 'mt-1.5 size-2 shrink-0 rounded-full bg-(--theme-midground) shadow-[0_0_10px_var(--theme-midground)]'
                  : 'mt-1.5 size-2 shrink-0 rounded-full bg-(--ui-success)'
              }
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-(--ui-text-primary)">{sessionLabel}</div>
              <div className="mt-0.5 text-[0.68rem] text-(--ui-text-tertiary)">{working ? 'Working' : 'Ready'}</div>
            </div>
          </div>
        </Card>

        <Card title="Project">
          <div className="flex items-start gap-2">
            <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name="folder" size="0.9rem" />
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-(--ui-text-primary)">{projectName}</div>
              <div className="mt-1 break-all font-mono text-[0.62rem] leading-relaxed text-(--ui-text-quaternary)">
                {cwd || 'Choose a project to enable workspace tools.'}
              </div>
            </div>
          </div>
        </Card>

        <Card title="Changes">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-sm text-(--ui-text-primary)">
              {changedFiles.length === 0 ? 'Working tree clean' : `${changedFiles.length} file${changedFiles.length === 1 ? '' : 's'} changed`}
            </span>
            {changedFiles.length > 0 && (
              <button
                className="shrink-0 text-[0.68rem] text-(--theme-midground) hover:underline"
                onClick={() => revealDesktopPane('review')}
                type="button"
              >
                Review
              </button>
            )}
          </div>

          {changedFiles.length > 0 ? (
            <div className="flex flex-col gap-0.5">
              {changedFiles.slice(0, 5).map(file => (
                <button
                  className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-(--ui-control-hover-background)"
                  key={file.path}
                  onClick={() => void openReviewForPath(file.path)}
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={
                      file.staged
                        ? 'size-1.5 shrink-0 rounded-full bg-(--ui-success)'
                        : 'size-1.5 shrink-0 rounded-full bg-(--theme-midground)'
                    }
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-[0.66rem] text-(--ui-text-secondary)">
                    {file.path}
                  </span>
                </button>
              ))}
              {changedFiles.length > 5 && (
                <div className="px-1.5 pt-1 text-[0.64rem] text-(--ui-text-quaternary)">+{changedFiles.length - 5} more</div>
              )}
            </div>
          ) : (
            <div className="text-[0.68rem] leading-relaxed text-(--ui-text-tertiary)">
              Local edits will appear here while you work with Hermes.
            </div>
          )}
        </Card>

        <Card title="Quick access">
          <div className="grid grid-cols-3 gap-1.5">
            <Button disabled={!cwd} onClick={() => revealDesktopPane('files')} size="sm" variant="outline">
              Files
            </Button>
            <Button disabled={!cwd} onClick={() => revealDesktopPane('review')} size="sm" variant="outline">
              Review
            </Button>
            <Button onClick={() => revealDesktopPane('terminal')} size="sm" variant="outline">
              Terminal
            </Button>
          </div>
        </Card>
      </div>
    </aside>
  )
}

function migrateStockLayoutOnce(): void {
  const current = Number(readKey(PERSONAL_LAYOUT_VERSION_KEY) ?? 0)

  if (Number.isFinite(current) && current >= PERSONAL_LAYOUT_VERSION) {
    return
  }

  queueMicrotask(() => {
    // Re-check after module initialization: the layout registry and default tree
    // are wired synchronously by controller.tsx before this microtask runs.
    if ($activePresetId.get() === 'default') {
      applyDesktopLayoutPreset('default')
    }

    writeKey(PERSONAL_LAYOUT_VERSION_KEY, String(PERSONAL_LAYOUT_VERSION))
  })
}

export function registerWorkspaceOverviewPane(): () => void {
  const dispose = registry.register({
    id: WORKSPACE_OVERVIEW_PANE_ID,
    area: 'panes',
    source: 'core',
    title: 'overview',
    data: {
      placement: 'right',
      collapsible: true,
      uncloseable: true,
      revealAliases: ['overview', 'workspace-overview'],
      width: '300px',
      minWidth: '260px',
      maxWidth: '390px'
    },
    render: () => <WorkspaceOverview />
  })

  migrateStockLayoutOnce()

  return dispose
}
