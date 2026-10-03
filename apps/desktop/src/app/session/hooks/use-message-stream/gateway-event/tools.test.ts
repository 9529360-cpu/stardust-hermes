import { afterEach, expect, it, vi } from 'vitest'

import * as tree from '@/components/pane-shell/tree/store'
import * as context from '@/store/right-context'
import { $subagentsBySession, upsertSubagent } from '@/store/subagents'

import { handleToolEvent } from './tools'
import type { GatewayEventContext } from './types'

afterEach(() => {
  $subagentsBySession.set({})
  vi.restoreAllMocks()
})

const spawn = (isActiveEvent = true, fromActiveSource = true, subagentId = 'worker'): GatewayEventContext =>
  ({
    event: { type: 'subagent.spawn_requested' },
    payload: { subagent_id: subagentId, goal: 'Develop the project', status: 'queued' },
    sessionId: 'parent',
    isActiveEvent,
    fromActiveSource: () => fromActiveSource,
    deps: { nativeSubagentSessionsRef: { current: new Set() }, sessionInterrupted: () => false }
  }) as unknown as GatewayEventContext

it('reveals the active team once and does not reopen it for subsequent members', () => {
  const reveal = vi.spyOn(context, 'setRightContextOpen').mockImplementation(() => {})
  const activate = vi.spyOn(tree, 'revealTreePane').mockImplementation(() => {})
  handleToolEvent(spawn())
  expect(reveal).toHaveBeenCalledWith(true)
  expect(activate).toHaveBeenCalledWith('workspace-overview')
  handleToolEvent(spawn(true, true, 'second'))
  expect(reveal).toHaveBeenCalledTimes(1)
})

it('records background members without revealing another conversation or connection', () => {
  const reveal = vi.spyOn(context, 'setRightContextOpen').mockImplementation(() => {})
  handleToolEvent(spawn(false))
  expect($subagentsBySession.get().parent).toHaveLength(1)
  $subagentsBySession.set({})
  handleToolEvent(spawn(true, false))
  expect(reveal).not.toHaveBeenCalled()
})

it('reveals a native start when it replaces a provisional tool-call member', () => {
  const reveal = vi.spyOn(context, 'setRightContextOpen').mockImplementation(() => {})
  vi.spyOn(tree, 'revealTreePane').mockImplementation(() => {})
  upsertSubagent('parent', { subagent_id: 'delegate-tool:call:0', goal: 'Provisional member', status: 'running' })
  const event = spawn()
  event.event = { ...event.event, type: 'subagent.start' }
  handleToolEvent(event)
  expect(reveal).toHaveBeenCalledWith(true)
  expect($subagentsBySession.get().parent?.map(member => member.id)).toEqual(['worker'])
})
