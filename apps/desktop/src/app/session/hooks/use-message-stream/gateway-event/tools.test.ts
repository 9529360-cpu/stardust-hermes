import { afterEach, expect, it, vi } from 'vitest'

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

it('records active team members without reopening the retired overview rail', () => {
  const reveal = vi.spyOn(context, 'setRightContextOpen').mockImplementation(() => {})
  handleToolEvent(spawn())
  handleToolEvent(spawn(true, true, 'second'))
  expect($subagentsBySession.get().parent).toHaveLength(2)
  expect(reveal).not.toHaveBeenCalled()
})

it('records background members without revealing another conversation or connection', () => {
  const reveal = vi.spyOn(context, 'setRightContextOpen').mockImplementation(() => {})
  handleToolEvent(spawn(false))
  expect($subagentsBySession.get().parent).toHaveLength(1)
  $subagentsBySession.set({})
  handleToolEvent(spawn(true, false))
  expect(reveal).not.toHaveBeenCalled()
})

it('replaces a provisional tool-call member on native start without opening the rail', () => {
  const reveal = vi.spyOn(context, 'setRightContextOpen').mockImplementation(() => {})
  upsertSubagent('parent', { subagent_id: 'delegate-tool:call:0', goal: 'Provisional member', status: 'running' })
  const event = spawn()
  event.event = { ...event.event, type: 'subagent.start' }
  handleToolEvent(event)
  expect(reveal).not.toHaveBeenCalled()
  expect($subagentsBySession.get().parent?.map(member => member.id)).toEqual(['worker'])
})
