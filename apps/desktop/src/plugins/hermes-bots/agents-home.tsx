import { Button, cn, Codicon, ConfirmDialog, host, useValue } from '@stardust/plugin-sdk'
import { useState } from 'react'

import { avatarColor, botAppearance, BotFace } from './avatar'
import { CreateAgentDialog, CreateGroupChatDialog } from './create-dialog'
import {
  $botMeta,
  $lastRoster,
  botSourceStatus,
  isDefaultBot,
  partitionAgentHomeRoster,
  useRoster
} from './data'
import { EditProfileDialog } from './edit-profile-dialog'
import { $groupChats } from './group-chat'
import { openGroupChat } from './group-chat-view'
import { groupChatMemberBots, groupChatNames } from './group-membership'
import { $showHiddenBots, isBotHidden } from './hidden-bots'
import { useBots } from './i18n'
import { displayName } from './labels'
import { deleteBot } from './profile-ops'
import { openRosterBot } from './roster-actions'
import { usePublishRosterSnapshot } from './roster-pane-lifecycle'
import { botRosterMeta } from './routing'
import type { BotMeta, RosterRow } from './types'

interface AgentGroup {
  members: RosterRow[]
  name: string
}

interface AgentsHomeProps {
  agents: RosterRow[]
  allMeta: Record<string, BotMeta>
  canCreateGroup?: boolean
  error?: boolean
  groups?: AgentGroup[]
  initialLoading: boolean
  onConfigureAgent: (agent: RosterRow) => void
  onCreateAgent: () => void
  onCreateGroup: () => void
  onManageAgents?: () => void
  onToggleHidden?: () => void
  manageLabel?: string
  onOpenAgent: (agent: RosterRow) => void
  onOpenMainAssistant?: () => void
  mainAssistant?: RosterRow | null
  onOpenGroup: (name: string) => void
  onDeleteProfile?: (agent: RosterRow) => Promise<void>
  onRetry?: () => void
}

export function AgentsHome({
  agents,
  allMeta,
  canCreateGroup = partitionAgentHomeRoster(agents).partners.length > 1,
  error = false,
  groups = [],
  initialLoading,
  onConfigureAgent,
  onCreateAgent,
  onCreateGroup,
  onManageAgents,
  onToggleHidden,
  manageLabel,
  onOpenAgent,
  onOpenMainAssistant,
  mainAssistant: providedMainAssistant,
  onDeleteProfile,
  onOpenGroup,
  onRetry
}: AgentsHomeProps) {
  const b = useBots()
  const [deleting, setDeleting] = useState<RosterRow | null>(null)
  const partition = partitionAgentHomeRoster(agents)
  const mainAssistant = providedMainAssistant ?? partition.mainAssistant ?? null
  const visibleAgents = partition.partners

  return (
    <main className="min-h-0 flex-1 overflow-y-auto" data-slot="bots-roster" data-slot-page="agents-home">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-8 md:px-10 md:py-10">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl">
            <p className="mb-2 text-xs font-medium uppercase tracking-[0.14em] text-(--ui-text-tertiary)">
              {b.home.eyebrow}
            </p>
            <h1 className="text-2xl font-semibold tracking-tight text-(--ui-text-primary) md:text-3xl">
              {b.home.title}
            </h1>
            <p className="mt-2 text-sm leading-6 text-(--ui-text-secondary)">{b.home.description}</p>
          </div>
          <div className="flex items-center gap-2">
            {canCreateGroup ? (
              <Button onClick={onCreateGroup} variant="secondary">
                <Codicon className="mr-1.5" name="organization" />
                {b.home.createGroup}
              </Button>
            ) : null}
            {manageLabel ? (
              <Button onClick={onToggleHidden} variant="ghost">
                {manageLabel}
              </Button>
            ) : null}
            <Button onClick={onCreateAgent}>
              <Codicon className="mr-1.5" name="add" />
              {b.home.create}
            </Button>
          </div>
        </header>

        {!error && !initialLoading && mainAssistant ? (
          <section aria-label={b.home.mainAssistantLabel} data-main-assistant-card="">
            <div className="flex flex-col gap-4 rounded-2xl border border-(--ui-stroke-primary) bg-(--ui-sidebar-surface-background) p-5 md:flex-row md:items-center md:justify-between md:p-6">
              <div className="flex min-w-0 items-start gap-4">
                {(() => {
                  const appearance = botAppearance(mainAssistant.name, botRosterMeta(mainAssistant, allMeta))

                  return (
                    <BotFace
                      color={avatarColor(appearance.color, mainAssistant.name)}
                      image={appearance.image}
                      name={mainAssistant.name}
                      shape={appearance.shape}
                      size={56}
                    />
                  )
                })()}
                <div className="min-w-0">
                  <p className="text-xs font-medium text-(--ui-text-tertiary)">{b.home.mainAssistantLabel}</p>
                  <h2 className="mt-1 text-lg font-semibold text-(--ui-text-primary)">
                    {displayName(mainAssistant, botRosterMeta(mainAssistant, allMeta))}
                  </h2>
                  <p className="mt-1 max-w-2xl text-sm leading-5 text-(--ui-text-secondary)">
                    {b.home.mainAssistantDescription}
                  </p>
                </div>
              </div>
              {onOpenMainAssistant ? (
                <Button className="shrink-0" onClick={onOpenMainAssistant}>
                  <Codicon className="mr-1.5" name="comment-discussion" />
                  {b.home.openMainChat}
                </Button>
              ) : null}
            </div>
          </section>
        ) : null}

        {error ? (
          <section className="rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) p-5">
            <h2 className="text-sm font-medium text-(--ui-text-primary)">{b.home.loadFailedTitle}</h2>
            <p className="mt-1 text-sm text-(--ui-text-secondary)">{b.home.loadFailedDescription}</p>
            {onRetry ? (
              <Button className="mt-3" onClick={onRetry} size="sm" variant="secondary">
                {b.home.retry}
              </Button>
            ) : null}
          </section>
        ) : initialLoading ? (
          <div aria-live="polite" className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3" role="status">
            <span className="sr-only">{b.home.loading}</span>
            {Array.from({ length: 3 }, (_, index) => (
              <div
                className="h-52 animate-pulse rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background)"
                key={index}
              />
            ))}
          </div>
        ) : visibleAgents.length === 0 && !mainAssistant ? (
          <section className="flex min-h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-(--ui-stroke-secondary) px-6 py-10 text-center">
            <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-(--chrome-action-hover) text-(--ui-text-secondary)">
              <Codicon name="hubot" size="1.5rem" />
            </div>
            <h2 className="text-lg font-semibold text-(--ui-text-primary)">{b.home.emptyTitle}</h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-(--ui-text-secondary)">{b.home.emptyDescription}</p>
            <Button className="mt-5" onClick={onCreateAgent}>
              {b.home.create}
            </Button>
          </section>
        ) : (
          <section aria-label={b.home.partners}>
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h2 className="text-sm font-medium text-(--ui-text-primary)">{b.home.partners}</h2>
              <div className="flex items-center gap-3">
                <span className="text-xs text-(--ui-text-tertiary)">{b.home.partnerCount(visibleAgents.length)}</span>
                {onManageAgents ? (
                  <Button onClick={onManageAgents} size="sm" variant="ghost">
                    {b.home.managePartners}
                  </Button>
                ) : null}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {visibleAgents.map(agent => {
                const meta = botRosterMeta(agent, allMeta)
                const name = displayName(agent, meta)
                const { shape, color, image } = botAppearance(agent.name, meta)
                const status = botSourceStatus(agent)

                const statusLabel =
                  status.key === 'unknown'
                    ? b.home.statusUnknown
                    : status.key === 'on-demand'
                      ? b.home.connectsWhenChatStarts
                      : status.available
                        ? b.home.readyToChat
                        : b.home.cannotReach

                const description = agent.description?.trim() || b.home.noDescription

                return (
                  <article
                    className="group flex min-h-56 flex-col rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) p-4 transition-colors hover:border-(--ui-stroke-primary)"
                    data-agent-card={agent.name}
                    key={`${agent.connectionId || 'local'}:${agent.name}`}
                  >
                    <div className="flex items-start gap-3">
                      <BotFace
                        color={avatarColor(color, agent.name)}
                        image={image}
                        name={agent.name}
                        shape={shape}
                        size={48}
                      />
                      <div className="min-w-0 flex-1">
                        <h3 className="truncate text-base font-semibold text-(--ui-text-primary)" title={name}>
                          {name}
                        </h3>
                        <p
                          aria-live="polite"
                          className="mt-1 flex items-center gap-1.5 text-xs text-(--ui-text-tertiary)"
                          role="status"
                        >
                          <span
                            aria-hidden="true"
                            className={cn(
                              'size-1.5 rounded-full',
                              status.key === 'unknown'
                                ? 'bg-(--ui-text-quaternary)'
                                : status.available
                                  ? 'bg-(--ui-bg-green)'
                                  : 'bg-(--ui-bg-amber)'
                            )}
                          />
                          {statusLabel}
                        </p>
                      </div>
                    </div>
                    <p className="mt-4 line-clamp-3 min-h-12 text-sm leading-5 text-(--ui-text-secondary)">
                      {description}
                    </p>
                    {agent.connectionLabel ? (
                      <p className="mt-2 truncate text-xs text-(--ui-text-tertiary)" title={agent.connectionLabel}>
                        {b.home.availableOn(agent.connectionLabel)}
                      </p>
                    ) : null}
                    <div className="mt-auto flex items-center gap-2 pt-4">
                      <Button
                        aria-label={status.available ? b.home.startChatWith(name) : b.home.retryChatWith(name)}
                        className="flex-1"
                        onClick={() => onOpenAgent(agent)}
                        size="sm"
                      >
                        <Codicon className="mr-1.5" name="comment-discussion" />
                        {status.available ? b.home.startChat : b.home.tryAgain}
                      </Button>
                      <Button
                        aria-label={b.home.configureAgent(name)}
                        onClick={() => onConfigureAgent(agent)}
                        size="icon-sm"
                        title={b.home.configure}
                        variant="ghost"
                      >
                        <Codicon name="settings-gear" />
                      </Button>
                      {!isDefaultBot(agent) ? (
                        <Button
                          aria-label={b.home.deletePartnerFor(name)}
                          onClick={() => setDeleting(agent)}
                          size="icon-sm"
                          title={b.home.deletePartner}
                          variant="ghost"
                        >
                          <Codicon name="trash" />
                        </Button>
                      ) : null}
                    </div>
                  </article>
                )
              })}
            </div>
          </section>
        )}

        {!error && !initialLoading && groups.length ? (
          <section aria-label={b.home.groupChats}>
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h2 className="text-sm font-medium text-(--ui-text-primary)">{b.home.groupChats}</h2>
              <div className="flex items-center gap-3">
                <span className="text-xs text-(--ui-text-tertiary)">{b.home.groupDescription}</span>
                {canCreateGroup ? (
                  <Button onClick={onCreateGroup} size="sm" variant="secondary">
                    <Codicon className="mr-1.5" name="add" />
                    {b.home.createGroup}
                  </Button>
                ) : null}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {groups.map(group => (
                <button
                  className="flex min-h-24 items-center gap-3 rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) p-4 text-left transition-colors hover:bg-(--chrome-action-hover)"
                  key={group.name}
                  onClick={() => onOpenGroup(group.name)}
                  type="button"
                >
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-(--chrome-action-hover) text-(--ui-text-secondary)">
                    <Codicon name="organization" size="1.25rem" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-(--ui-text-primary)">{group.name}</span>
                    <span className="mt-1 block truncate text-xs text-(--ui-text-tertiary)">
                      {b.home.groupMemberCount(group.members.length)}
                    </span>
                  </span>
                  <Codicon className="text-(--ui-text-tertiary)" name="chevron-right" />
                </button>
              ))}
            </div>
          </section>
        ) : null}
      </div>
      <ConfirmDialog
        busyLabel={b.home.deletingPartner}
        confirmLabel={b.home.deletePartner}
        description={
          deleting
            ? b.home.deletePartnerDescription(displayName(deleting, botRosterMeta(deleting, allMeta)))
            : null
        }
        destructive
        doneLabel={b.home.deletePartnerDone}
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (deleting) {
            await onDeleteProfile?.(deleting)
          }
        }}
        open={Boolean(deleting)}
        title={deleting ? b.home.deletePartnerTitle(displayName(deleting, botRosterMeta(deleting, allMeta))) : b.home.deletePartner}
      />
    </main>
  )
}

/** Full-page, user-facing home. The sidebar remains the compact management roster. */
export function AgentsHomePage({ onManageAgents }: { onManageAgents: () => void }) {
  const b = useBots()
  const { data, error, isLoading, refetch } = useRoster()
  const allMeta = useValue($botMeta)
  const cachedRoster = useValue($lastRoster)
  const roster = Array.isArray(data?.profiles) ? data.profiles.filter(agent => !agent.ghost) : cachedRoster
  usePublishRosterSnapshot({
    data,
    live: Array.isArray(data?.profiles) ? data.profiles : null,
    roster,
    allMeta,
    activeSourceRoster: Array.isArray(data?.profiles) ? data.profiles : []
  })
  const groupsState = useValue($groupChats)
  const [createOpen, setCreateOpen] = useState(false)
  const [createGroupOpen, setCreateGroupOpen] = useState(false)
  const [editing, setEditing] = useState<RosterRow | null>(null)
  const showHiddenBots = useValue($showHiddenBots)
  const partition = partitionAgentHomeRoster(roster)
  const mainAssistant = partition.mainAssistant
  const hiddenAgents = partition.partners.filter(agent => isBotHidden(agent, allMeta))
  const agents = partition.partners.filter(agent => showHiddenBots || !isBotHidden(agent, allMeta))

  if (mainAssistant) {
    agents.unshift(mainAssistant)
  }

  const canCreateGroup = partition.partners.length >= 2

  const groups = groupChatNames(allMeta, groupsState)
    .filter(name => !groupsState[name]?.tombstone)
    .map(name => ({ name, members: groupChatMemberBots(name, roster, allMeta) }))

  return (
    <>
      <AgentsHome
        agents={agents}
        allMeta={allMeta}
        canCreateGroup={canCreateGroup}
        error={Boolean(error && !data && !cachedRoster.length)}
        groups={groups}
        initialLoading={isLoading && !data}
        mainAssistant={mainAssistant}
        manageLabel={hiddenAgents.length ? b.home.hiddenPartners(hiddenAgents.length, showHiddenBots) : undefined}
        onConfigureAgent={setEditing}
        onCreateAgent={() => setCreateOpen(true)}
        onCreateGroup={() => setCreateGroupOpen(true)}
        onDeleteProfile={async agent => {
          const name = displayName(agent, botRosterMeta(agent, allMeta))
          await deleteBot(agent)
          await refetch()
          host.notify({ kind: 'success', message: b.home.partnerDeleted(name) })
        }}
        onManageAgents={onManageAgents}
        onOpenAgent={agent => void openRosterBot(agent)}
        onOpenGroup={openGroupChat}
        onOpenMainAssistant={() => host.newChat('default')}
        onRetry={() => void refetch()}
        onToggleHidden={() => $showHiddenBots.set(!showHiddenBots)}
      />
      <CreateAgentDialog onClose={() => setCreateOpen(false)} open={createOpen} roster={roster} userFriendly />
      <CreateGroupChatDialog
        onClose={() => setCreateGroupOpen(false)}
        onCreated={openGroupChat}
        open={createGroupOpen}
        roster={roster}
      />
      <EditProfileDialog bot={editing} onClose={() => setEditing(null)} open={Boolean(editing)} />
    </>
  )
}
