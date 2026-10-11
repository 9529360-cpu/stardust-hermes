import { useStore } from '@nanostores/react'
import { memo, type ReactNode, useEffect, useMemo, useState } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Tip } from '@/components/ui/tooltip'
import { $pluginRecords, type PluginRecord, setPluginEnabled } from '@/contrib/plugins-store'
import { discoverRuntimePlugins } from '@/contrib/runtime-loader'
import type { ProfileScope } from '@/hermes'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { AlertTriangle, FolderOpen, Loader2, Monitor, Package, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'
import {
  $agentPluginBusy,
  $agentPlugins,
  $agentPluginsError,
  $agentPluginsStatus,
  type AgentPluginRow,
  type GatewayRequest,
  isDesktopRelevantPlugin,
  loadAgentPlugins,
  toggleAgentPlugin,
  updateAgentPlugin
} from '@/store/agent-plugins'
import { notify, notifyError } from '@/store/notifications'
import { openPluginInstallRequest } from '@/store/plugin-install-request'

import { PanelEmpty } from '../overlays/panel'
import { Pill } from '../settings/primitives'
import { useDeepLinkHighlight } from '../settings/use-deep-link-highlight'

import { mergePluginPackages, type PackageKind, type PluginPackage } from './plugin-packages'

/** Deep-link anchor for a package row (`/skills?tab=plugins&plugin=<key>`).
 *  Accepts the agent key, the agent name, or the desktop record id. */
export const pluginElementId = (target: string) => `plugin-${target}`

/** Derive the bare profile name a `plugins.manage` call should target. */
function profileParam(scope: ProfileScope): null | string {
  if (!scope) {
    return null
  }

  return typeof scope === 'string' ? scope : (scope.profile ?? null)
}

function reveal(file: string) {
  void window.stardustDesktop?.revealPath?.(file)?.catch(() => undefined)
}

async function revealPluginsDir() {
  try {
    // Electron owns the app-level plugin root — deriving it from the backend's
    // hermes_home breaks against a remote backend (#66899).
    const dir = await window.stardustDesktop?.desktopPluginsRoot?.()

    if (!dir) {
      notifyError('Desktop plugins are unavailable', 'Could not resolve the plugins folder')

      return
    }

    const result = await window.stardustDesktop?.openDir?.(dir)

    if (result && !result.ok) {
      notifyError(result.error ?? 'unknown error', 'Could not open the plugins folder')
    }
  } catch (err) {
    notifyError(err, 'Could not resolve the plugins folder')
  }
}

/** Copy any changed unified desktop halves into the app root FIRST, then
 *  rescan the root — a concurrent scan would read the pre-copy state. */
async function rescanAll(requestGateway: GatewayRequest, scope: null | string) {
  await window.stardustDesktop?.reconcileDesktopPlugins?.().catch(() => undefined)
  await discoverRuntimePlugins()
  await loadAgentPlugins(requestGateway, scope)
}

/** Open the dual-target install modal pre-filled to install ONLY the agent
 *  half of a unified package into the scoped profile (the desktop half is
 *  already here). Provenance comes from the package marker Electron stamped
 *  when it copied the half out (catalog sidecar or git remote). */
function installAgentHalfHere(record: PluginRecord, profile: null | string) {
  const origin = record.packageOrigin

  if (!origin?.repo) {
    return
  }

  openPluginInstallRequest({
    catalogName: origin.catalogName,
    legacyHint: 'agent',
    profile,
    repo: origin.repo,
    sha: origin.sha
  })
}

function KindBadge({ kind }: { kind: PackageKind }) {
  const { t } = useI18n()
  const p = t.skills.plugins

  return (
    <span className="inline-flex items-center gap-1 rounded border border-(--ui-stroke-tertiary) px-1.5 py-px text-[0.65rem] text-(--ui-text-tertiary)">
      {kind !== 'desktop' && <Package aria-hidden className="size-3" />}
      {kind !== 'agent' && <Monitor aria-hidden className="size-3" />}
      {kind === 'both' ? p.kindBoth : kind === 'agent' ? p.kindAgent : p.kindDesktop}
    </span>
  )
}

/** Provenance pill: where the package came from. */
function ProvenancePill({ pkg }: { pkg: PluginPackage }) {
  const { t } = useI18n()
  const p = t.skills.plugins

  if (pkg.agent?.catalog_name) {
    return (
      <Tip label={p.catalogProvenance(pkg.agent.installed_sha?.slice(0, 8) ?? '')}>
        <span>
          <Pill>{pkg.agent.catalog_tier === 'official' ? p.tierOfficial : p.tierCommunity}</Pill>
        </span>
      </Tip>
    )
  }

  if (pkg.agent?.pinned_sha) {
    return (
      <Tip label={p.pinnedProvenance(pkg.agent.pinned_sha.slice(0, 8))}>
        <span>
          <Pill>
            <span className="font-mono">{p.pinnedBadge(pkg.agent.pinned_sha.slice(0, 8))}</span>
          </Pill>
        </span>
      </Tip>
    )
  }

  if (pkg.agent) {
    return <Pill>{pkg.agent.source}</Pill>
  }

  if (pkg.desktop?.packageOrigin?.repo) {
    const origin = pkg.desktop.packageOrigin
    const shortSha = origin.sha?.slice(0, 8)
    const label = origin.sha ? `${origin.repo} @ ${origin.sha}` : origin.repo

    return (
      <Tip label={label}>
        <span>
          <Pill>
            {shortSha ? (
              <span className="font-mono">{p.pinnedBadge(shortSha)}</span>
            ) : (
              t.settings.plugins.kinds[pkg.desktop.kind]
            )}
          </Pill>
        </span>
      </Tip>
    )
  }

  if (pkg.desktop) {
    return <Pill>{t.settings.plugins.kinds[pkg.desktop.kind]}</Pill>
  }

  return null
}

/** Column widths shared by the header and every row so the two control
 *  columns line up down the page like a table. */
const HALF_COL = 'flex w-36 shrink-0 items-center gap-1.5'

/** One control cell; `label` is the accessible name for screen readers only
 *  (the visible column label lives once, in the header). */
function HalfCell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div aria-label={label} className={HALF_COL} role="cell">
      {children}
    </div>
  )
}

function Dash() {
  return (
    <span aria-hidden className="w-9 text-center text-(--ui-text-quaternary)">
      —
    </span>
  )
}

function DesktopPluginTrustDialog({
  plugin,
  displayName,
  onClose
}: {
  plugin: PluginRecord
  displayName: string
  onClose: () => void
}) {
  const { t } = useI18n()
  const d = t.settings.plugins
  const copy = d.trustDialog
  const [enabling, setEnabling] = useState(false)
  const origin = plugin.packageOrigin
  const hasRecordedSource = Boolean(origin?.repo || origin?.sha || plugin.file)

  const enable = async () => {
    if (enabling) {
      return
    }

    setEnabling(true)

    try {
      await setPluginEnabled(plugin.id, true)
      onClose()
    } catch (error) {
      notifyError(error, `Could not enable ${displayName}`)
      setEnabling(false)
    }
  }

  return (
    <Dialog
      onOpenChange={open => {
        if (!open && !enabling) {
          onClose()
        }
      }}
      open
    >
      <DialogContent banner={copy.authorityBody} bannerTone="warn" onOpenAutoFocus={event => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle icon={AlertTriangle}>{copy.title(displayName)}</DialogTitle>
          <DialogDescription>{copy.description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-2 rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-3 py-2.5">
          <div className="text-[length:var(--conversation-caption-font-size)] font-medium text-foreground">
            {copy.sourceHeading}
          </div>

          {!hasRecordedSource && (
            <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
              {copy.sourceUnknown}
            </p>
          )}

          {origin?.repo && (
            <div className="grid gap-1 text-[length:var(--conversation-caption-font-size)] sm:grid-cols-[7rem_minmax(0,1fr)]">
              <span className="text-(--ui-text-tertiary)">{copy.repository}</span>
              <code className="break-all text-foreground">{origin.repo}</code>
            </div>
          )}

          {origin?.sha && (
            <div className="grid gap-1 text-[length:var(--conversation-caption-font-size)] sm:grid-cols-[7rem_minmax(0,1fr)]">
              <span className="text-(--ui-text-tertiary)">{copy.pinnedCommit}</span>
              <code className="break-all text-foreground">{origin.sha}</code>
            </div>
          )}

          {plugin.file && (
            <div className="grid gap-1 text-[length:var(--conversation-caption-font-size)] sm:grid-cols-[7rem_minmax(0,1fr)]">
              <span className="text-(--ui-text-tertiary)">{copy.localFile}</span>
              <code className="break-all text-foreground">{plugin.file}</code>
            </div>
          )}
        </div>

        <div className="space-y-1 text-[length:var(--conversation-caption-font-size)]">
          <div className="font-medium text-foreground">{copy.authorityHeading}</div>
          <p className="text-(--ui-text-secondary)">{copy.authorityBody}</p>
        </div>

        <DialogFooter>
          <Button disabled={enabling} onClick={onClose} variant="outline">
            {t.common.cancel}
          </Button>
          <Button disabled={enabling} onClick={() => void enable()}>
            {enabling ? t.common.loading : copy.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function PackageRow({
  pkg,
  scope,
  scopeLabel,
  busy,
  onAgentToggle,
  onAgentUpdate
}: {
  pkg: PluginPackage
  scope: null | string
  scopeLabel: string
  busy: boolean
  onAgentToggle: (row: AgentPluginRow, enable: boolean) => void
  onAgentUpdate: (row: AgentPluginRow) => void
}) {
  const { t } = useI18n()
  const p = t.skills.plugins
  const d = t.settings.plugins
  const desktop = pkg.desktop
  const agent = pkg.agent
  const desktopOn = desktop ? desktop.status !== 'disabled' : false
  const agentOn = agent?.status === 'enabled'
  const agentToggleable = Boolean(agent?.key)
  const [desktopTrustCandidate, setDesktopTrustCandidate] = useState<PluginRecord | null>(null)
  const displayName =
    desktop?.kind === 'bundled' ? (p.bundledNames[desktop.id as keyof typeof p.bundledNames] ?? pkg.name) : pkg.name
  const description =
    desktop?.kind === 'bundled'
      ? (p.bundledDescriptions[desktop.id as keyof typeof p.bundledDescriptions] ?? pkg.description)
      : pkg.description

  return (
    <div
      className="flex items-center gap-3 border-b border-(--ui-stroke-tertiary) px-3 py-2.5 last:border-b-0"
      data-testid={`plugin-row-${pkg.key}`}
      id={pluginElementId(agent?.key ?? agent?.name ?? desktop?.id ?? pkg.key)}
      role="row"
    >
      <div className="flex min-w-0 flex-1 items-start gap-2" role="cell">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[length:var(--conversation-text-font-size)] font-medium text-foreground">
            <span>{displayName}</span>
            {agent?.version && <span className="text-(--ui-text-quaternary)">v{agent.version}</span>}
            <KindBadge kind={pkg.kind} />
            <ProvenancePill pkg={pkg} />
            {agent?.portable && <Pill>{p.portableBadge}</Pill>}
            {desktop?.status === 'error' && <Pill tone="primary">{d.failed}</Pill>}
          </div>
          {(desktop?.status === 'error' ? desktop.error : description) && (
            <div
              className={cn(
                'mt-0.5 text-[length:var(--conversation-caption-font-size)] break-words',
                desktop?.status === 'error' ? 'text-(--ui-danger,#f87171)' : 'text-(--ui-text-tertiary)'
              )}
            >
              {desktop?.status === 'error' ? desktop.error : description}
            </div>
          )}
        </div>
        {/* Fixed slot so the switch column stays straight whether or not
            this row has a folder to reveal (bundled plugins have none). */}
        <span className="flex size-7 shrink-0 items-center justify-center">
          {desktop?.file && (
            <Tip label={d.reveal}>
              <Button onClick={() => reveal(desktop.file!)} size="icon" variant="ghost">
                <Codicon name="folder-opened" size="0.85rem" />
              </Button>
            </Tip>
          )}
        </span>
      </div>

      {/* The two halves. Desktop is app-level and reads the same whichever
          profile is selected; Agent follows the selector. A half the package
          lacks shows a dash; a half it has but which is missing on this side
          shows the install affordance. */}
      <HalfCell label={p.halfDesktop}>
        {desktop ? (
          <Switch
            aria-label={`${p.halfDesktop}: ${displayName}`}
            checked={desktopOn}
            onCheckedChange={on => {
              triggerHaptic('selection')

              if (on && desktop.kind !== 'bundled') {
                setDesktopTrustCandidate(desktop)

                return
              }

              void setPluginEnabled(desktop.id, on)
            }}
          />
        ) : pkg.desktopMissing ? (
          <Tip label={p.desktopHalfPendingTip}>
            <span className="text-[0.65rem] text-(--ui-text-tertiary)">{p.desktopHalfPending}</span>
          </Tip>
        ) : (
          <Dash />
        )}
      </HalfCell>

      <HalfCell label={p.halfAgentIn(scopeLabel)}>
        {agent ? (
          <>
            {agent.update_available && (
              <Button
                className="h-5 px-1.5 text-[0.65rem]"
                disabled={busy}
                onClick={() => onAgentUpdate(agent)}
                size="xs"
                variant="outline"
              >
                {p.updateToPin(agent.catalog_sha?.slice(0, 8) ?? '')}
              </Button>
            )}
            {busy && <Loader2 className="size-3.5 animate-spin text-(--ui-text-tertiary)" />}
            {agentToggleable ? (
              <Switch
                aria-label={`${p.halfAgent}: ${displayName}`}
                checked={agentOn}
                disabled={busy}
                onCheckedChange={on => onAgentToggle(agent, on)}
              />
            ) : (
              <Tip label={p.legacyBackend}>
                <span>
                  <Switch aria-label={`${p.halfAgent}: ${displayName}`} checked={agentOn} disabled />
                </span>
              </Tip>
            )}
          </>
        ) : pkg.agentMissingInProfile && desktop ? (
          <Tip label={desktop.packageOrigin?.repo ? p.installAgentHereTip(scopeLabel) : p.installAgentHereNoOrigin}>
            <span>
              <Button
                className="h-5 px-1.5 text-[0.65rem]"
                disabled={!desktop.packageOrigin?.repo}
                onClick={() => installAgentHalfHere(desktop, scope)}
                size="xs"
                variant="outline"
              >
                {p.installAgentHere}
              </Button>
            </span>
          </Tip>
        ) : (
          <Dash />
        )}
      </HalfCell>

      {desktopTrustCandidate && (
        <DesktopPluginTrustDialog
          displayName={displayName}
          onClose={() => setDesktopTrustCandidate(null)}
          plugin={desktopTrustCandidate}
        />
      )}
    </div>
  )
}

/** THE plugins surface: one row per package. Each row shows its Desktop half
 *  (this app — the same for every profile, gateway, or machine) and its Agent
 *  half (the selected profile's backend). Install from Git adds anything that
 *  is not already listed. */
export const PluginsTab = memo(function PluginsTab({
  profile,
  scopeSelector,
  scopeLabel
}: {
  profile: ProfileScope
  /** The Capabilities profile selector; rendered in the Agent column header so
   *  it visibly governs only that column. */
  scopeSelector?: ReactNode
  /** Display name of the selected profile for the Agent column label. */
  scopeLabel?: string
}) {
  const { t } = useI18n()
  const p = t.skills.plugins
  const d = t.settings.plugins
  const { requestGateway } = useGatewayRequest()

  const desktopRecords = useStore($pluginRecords)
  const agentRows = useStore($agentPlugins)
  const status = useStore($agentPluginsStatus)
  const error = useStore($agentPluginsError)
  const busyKey = useStore($agentPluginBusy)

  const scope = profileParam(profile)
  const label = scopeLabel ?? scope ?? t.skills.defaultProfile

  useEffect(() => {
    void loadAgentPlugins(requestGateway, scope)
  }, [requestGateway, scope])

  const packages = useMemo(
    () => mergePluginPackages(Object.values(desktopRecords), agentRows.filter(isDesktopRelevantPlugin)),
    [agentRows, desktopRecords]
  )

  useDeepLinkHighlight({ param: 'plugin', ready: () => true, elementId: pluginElementId })

  const agentBusy = (row: AgentPluginRow) => busyKey === (row.key ?? row.name) || busyKey === row.name

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-32 flex-1 overflow-y-auto">
        {/* Header: what the two columns mean, and the controls that act on
            the whole page (install, folder, rescan). */}
        <div className="flex flex-wrap items-start justify-between gap-3 px-3 pt-3 pb-2">
          <p className="min-w-0 flex-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
            {p.pageBlurb}
          </p>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              onClick={() => openPluginInstallRequest({ profile: scope, repo: '' })}
              size="sm"
              type="button"
              variant="secondary"
            >
              {d.installModal.installFromGit}
            </Button>
            <Tip label={d.openFolder}>
              <Button
                aria-label={d.openFolder}
                onClick={() => void revealPluginsDir()}
                size="icon"
                type="button"
                variant="ghost"
              >
                <FolderOpen className="size-3.5" />
              </Button>
            </Tip>
            <Tip label={d.rescan}>
              <Button
                aria-label={d.rescan}
                onClick={() => {
                  triggerHaptic('selection')
                  void rescanAll(requestGateway, scope)
                }}
                size="icon"
                type="button"
                variant="ghost"
              >
                <RefreshCw className="size-3.5" />
              </Button>
            </Tip>
          </div>
        </div>

        {status === 'error' ? (
          <PanelEmpty
            action={
              <Button onClick={() => void loadAgentPlugins(requestGateway, scope)} size="sm">
                {t.skills.refresh}
              </Button>
            }
            description={error ?? undefined}
            icon="error"
            title={p.loadFailed}
          />
        ) : packages.length === 0 && status === 'ready' ? (
          <p className="px-3 py-3 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
            {p.emptyAll} {p.emptyHint}
          </p>
        ) : (
          <div className="flex flex-col" role="table">
            {/* Column header: the visible labels for the two control columns,
                aligned with the cells below. The profile selector sits INSIDE
                the Agent header so it visibly governs only that column. */}
            <div
              className="flex items-center gap-3 border-y border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-3 py-1.5 text-[0.68rem] text-(--ui-text-tertiary)"
              role="row"
            >
              <div className="min-w-0 flex-1" role="columnheader" />
              <div className={HALF_COL} role="columnheader">
                <Monitor aria-hidden className="size-3.5 shrink-0" />
                <Tip label={p.halfDesktopHint}>
                  <span className="font-medium">{p.halfDesktop}</span>
                </Tip>
              </div>
              <div className={HALF_COL} role="columnheader">
                <Package aria-hidden className="size-3.5 shrink-0" />
                {scopeSelector ?? <span className="truncate font-medium">{p.halfAgentIn(label)}</span>}
              </div>
            </div>
            {packages.map(pkg => (
              <PackageRow
                busy={pkg.agent ? agentBusy(pkg.agent) : false}
                key={pkg.key}
                onAgentToggle={(row, enable) => {
                  if (!row.key) {
                    return
                  }

                  void toggleAgentPlugin(requestGateway, row.key, enable, p.toggleFailed(row.name), scope)
                }}
                onAgentUpdate={row => {
                  void updateAgentPlugin(requestGateway, row.name, p.updateFailed(row.name), scope).then(applied => {
                    if (applied) {
                      notify({ kind: 'success', message: p.updated(row.name) })
                      void rescanAll(requestGateway, scope)
                    }
                  })
                }}
                pkg={pkg}
                scope={scope}
                scopeLabel={label}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
})
