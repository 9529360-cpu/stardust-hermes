// @vitest-environment jsdom
import type * as HermesSdk from '@stardust/plugin-sdk'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { translateBots } from './i18n-test-helper'
import type { BotMeta, RosterRow } from './types'

vi.mock('@stardust/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()

  return {
    ...sdk,
    usePluginI18n: () => translateBots
  }
})

import { AgentsHome } from './agents-home'

const noMeta: Record<string, BotMeta> = {}

afterEach(() => {
  vi.clearAllMocks()
})

describe('AgentsHome', () => {
  it('introduces an agent in everyday language and offers a direct way to start', () => {
    const onOpenAgent = vi.fn()

    const agent: RosterRow = {
      connectionId: 'local',
      description: 'A calm partner who helps plan the day.',
      name: 'day-planner',
      sourceReachable: true,
      title: 'Daily planner'
    }

    render(
      <AgentsHome
        agents={[agent]}
        allMeta={noMeta}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={vi.fn()}
        onCreateGroup={vi.fn()}
        onOpenAgent={onOpenAgent}
        onOpenGroup={vi.fn()}
      />
    )

    expect(screen.getByText('Meet your AI partners')).toBeTruthy()
    expect(screen.getByText('A calm partner who helps plan the day.')).toBeTruthy()
    expect(screen.getByText('You can reach them now')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Start a chat with Daily Planner' }))
    expect(onOpenAgent).toHaveBeenCalledWith(agent)
  })

  it('explains an unavailable partner without exposing gateway terminology', () => {
    const onOpenAgent = vi.fn()
    const agent = { connectionId: 'studio', connectionLabel: 'Studio computer', name: 'writer', sourceReachable: false }

    render(
      <AgentsHome
        agents={[agent]}
        allMeta={noMeta}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={vi.fn()}
        onCreateGroup={vi.fn()}
        onOpenAgent={onOpenAgent}
        onOpenGroup={vi.fn()}
      />
    )

    expect(screen.getByText('Can’t reach this partner right now')).toBeTruthy()
    expect(screen.queryByText(/gateway/i)).toBeNull()
    const retry = screen.getByRole('button', { name: 'Try again, chat with Writer' })
    expect(retry.getAttribute('aria-label')).toBe('Try again, chat with Writer')
    fireEvent.click(retry)
    expect(onOpenAgent).toHaveBeenCalledWith(agent)
  })

  it('explains that on-demand partners connect when the user starts a chat', () => {
    render(
      <AgentsHome
        agents={[{ name: 'writer', sourceError: 'connect-on-demand', sourceReachable: true }]}
        allMeta={noMeta}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={vi.fn()}
        onCreateGroup={vi.fn()}
        onOpenAgent={vi.fn()}
        onOpenGroup={vi.fn()}
      />
    )

    expect(screen.getByText('They’ll connect when you start chatting')).toBeTruthy()
    expect(screen.queryByText('You can reach them now')).toBeNull()
  })

  it('offers a human-readable first step when there are no agents', () => {
    const onCreateAgent = vi.fn()

    render(
      <AgentsHome
        agents={[]}
        allMeta={noMeta}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={onCreateAgent}
        onCreateGroup={vi.fn()}
        onOpenAgent={vi.fn()}
        onOpenGroup={vi.fn()}
      />
    )

    fireEvent.click(screen.getAllByRole('button', { name: 'Create a partner' })[0])
    expect(onCreateAgent).toHaveBeenCalledOnce()
    expect(screen.getByText(/research, writing, planning, or anything/i)).toBeTruthy()
  })

  it('offers a separate way to enter full management', () => {
    const onManageAgents = vi.fn()

    render(
      <AgentsHome
        agents={[{ name: 'day-planner', title: 'Daily planner' }]}
        allMeta={noMeta}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={vi.fn()}
        onCreateGroup={vi.fn()}
        onManageAgents={onManageAgents}
        onOpenAgent={vi.fn()}
        onOpenGroup={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Manage' }))
    expect(onManageAgents).toHaveBeenCalledOnce()
  })

  it('does not claim a partner is ready when connection status is unknown', () => {
    render(
      <AgentsHome
        agents={[{ name: 'unknown' }]}
        allMeta={noMeta}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={vi.fn()}
        onCreateGroup={vi.fn()}
        onOpenAgent={vi.fn()}
        onOpenGroup={vi.fn()}
      />
    )

    expect(screen.getByText('We’ll check when you start chatting')).toBeTruthy()
    expect(screen.queryByText('You can reach them now')).toBeNull()
  })

  it('offers team creation only when at least two partners can be selected', () => {
    const onCreateGroup = vi.fn()

    const onePartner = render(
      <AgentsHome
        agents={[{ name: 'writer' }]}
        allMeta={noMeta}
        groups={[{ name: 'Existing team', members: [{ name: 'writer' }] }]}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={vi.fn()}
        onCreateGroup={onCreateGroup}
        onOpenAgent={vi.fn()}
        onOpenGroup={vi.fn()}
      />
    )

    expect(screen.queryByRole('button', { name: 'Create a team' })).toBeNull()
    onePartner.unmount()

    render(
      <AgentsHome
        agents={[{ name: 'writer' }, { name: 'planner' }]}
        allMeta={noMeta}
        groups={[{ name: 'Existing team', members: [{ name: 'writer' }] }]}
        initialLoading={false}
        onConfigureAgent={vi.fn()}
        onCreateAgent={vi.fn()}
        onCreateGroup={onCreateGroup}
        onOpenAgent={vi.fn()}
        onOpenGroup={vi.fn()}
      />
    )

    const createTeamButtons = screen.getAllByRole('button', { name: 'Create a team' })
    expect(createTeamButtons.length).toBeGreaterThan(0)
    fireEvent.click(createTeamButtons[0])
    expect(onCreateGroup).toHaveBeenCalledOnce()
  })
})
