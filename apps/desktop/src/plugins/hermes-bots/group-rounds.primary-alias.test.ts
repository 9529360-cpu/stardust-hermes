import { beforeEach, expect, it, vi } from 'vitest'

import { $groupChats } from './group-chat'
import { durableGroupChatMembers, groupMemberKey } from './group-membership'
import { parseGroupChatMentions, resolveGroupResponders, unaddressedGroupMentions } from './group-rounds'
import type { GroupMember, GroupMessage } from './types'

vi.mock('@stardust/plugin-sdk', async () => {
  const { atom } = await import('nanostores')

  return {
    atom,
    host: { request: vi.fn(), state: { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } } },
    queryClient: { invalidateQueries: vi.fn() },
    useQuery: vi.fn(),
    useValue: vi.fn()
  }
})
vi.mock('./shared', () => ({ getPluginCtx: () => null, ID: 'hermes-bots' }))

beforeEach(() => $groupChats.set({}))

it('keeps primary handoffs callable in both directions across persisted descriptor shapes', () => {
  for (const handle of [undefined, 'default', 'hermes', 'stardust']) {
    const original: GroupMember[] = [{ name: 'default', handle }, { name: 'code-farmer' }]

    const durable = durableGroupChatMembers(original)
    // Saved rooms carry the primary's Stardust handle, whichever shape they were saved from.
    expect(durable.map(member => member.handle)).toEqual(['stardust', 'code-farmer'])

    for (const members of [original, durable]) {
      for (const primaryTag of ['stardust', 'hermes']) {
        for (const [sender, target, senderTag, targetTag] of [
          ['code-farmer', 'default', 'code-farmer', primaryTag],
          ['default', 'code-farmer', primaryTag, 'code-farmer']
        ]) {
          const targetKey = groupMemberKey(members.find(member => member.name === target)!)

          const log: GroupMessage[] = [
            { at: 1, from: { kind: 'user', name: 'You' }, id: 'u', text: `@${senderTag} begin`, thread: 't' },
            { at: 2, from: { kind: 'member', name: sender }, id: 'm', text: `@${targetTag} continue`, thread: 't' }
          ]

          $groupChats.set({ g: { log, members, roomId: 'room', watermarks: {} } })
          expect([...parseGroupChatMentions(log[1].text, members).mentioned]).toEqual([targetKey])
          expect(resolveGroupResponders(log, members).map(member => member.name)).toContain(target)
          expect(unaddressedGroupMentions('g', members, 't')).toEqual([targetKey])
        }
      }
    }
  }
})

it('keeps qualified remote defaults distinct from the primary alias regardless of roster order', () => {
  const remotes: GroupMember[] = ['vera', 'spark'].map(device => ({
    name: 'default',
    handle: `default-${device}`,
    connectionId: device,
    remoteSource: true,
    sourceScoped: true
  }))

  for (const localHandle of ['hermes', 'stardust']) {
    const local: GroupMember = { name: 'default', handle: localHandle, connectionId: 'local', sourceScoped: true }

    for (const members of [[local, ...remotes], [...remotes].reverse().concat(local)]) {
      for (const descriptors of [members, durableGroupChatMembers(members)]) {
        for (const [tag, key] of [
          ['stardust', 'local::default'],
          ['hermes', 'local::default'],
          ['default-vera', 'vera::default'],
          ['default-spark', 'spark::default']
        ]) {
          expect([...parseGroupChatMentions(`@${tag} continue`, descriptors).mentioned]).toEqual([key])
        }
      }
    }
  }
})

it('keeps the primary alias on the primary when a user bot is literally named hermes, in either roster order', () => {
  const primary: GroupMember = { name: 'default', connectionId: 'local', sourceScoped: true }
  const shadow: GroupMember = { name: 'hermes', connectionId: 'local', sourceScoped: true }

  for (const members of [
    [primary, shadow],
    [shadow, primary]
  ]) {
    for (const tag of ['stardust', 'hermes']) {
      expect([...parseGroupChatMentions(`@${tag} continue`, members).mentioned]).toEqual(['local::default'])
    }
  }

  // The alias names the primary; a room without the primary has nobody to reach by it.
  expect(parseGroupChatMentions('@hermes continue', [shadow]).mentioned.size).toBe(0)
})
