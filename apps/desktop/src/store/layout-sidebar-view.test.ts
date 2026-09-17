import { beforeEach, describe, expect, it } from 'vitest'

import {
  $sidebarGrouping,
  $sidebarOrdering,
  $sidebarRowMeta,
  $sidebarShowAllSessions,
  $sidebarViewCustomized,
  migrateSidebarAssistantFirstDefault,
  resetSidebarView,
  setSidebarGrouping,
  setSidebarOrdering,
  setSidebarShowAllSessions,
  toggleSidebarRowMeta,
  toggleSidebarStatusFilter
} from './layout'
import { $showAllProfiles } from './profile'

beforeEach(() => {
  $showAllProfiles.set(false)
  resetSidebarView()
})

describe('the sidebar as it ships', () => {
  it('remembers expanded project previews across grouping changes and clears them on reset', () => {
    expect($sidebarShowAllSessions.get()).toBe(false)
    setSidebarGrouping('project')
    setSidebarShowAllSessions(true)
    setSidebarGrouping('date')

    expect($sidebarShowAllSessions.get()).toBe(true)
    expect($sidebarViewCustomized.get()).toBe(true)
    expect(window.localStorage.getItem('hermes.desktop.sidebarShowAllSessions')).toBe('true')

    resetSidebarView()

    expect($sidebarShowAllSessions.get()).toBe(false)
    expect($sidebarViewCustomized.get()).toBe(false)
    expect(window.localStorage.getItem('hermes.desktop.sidebarShowAllSessions')).toBe('false')
  })

  it('ships as a chronological task list while keeping recency metadata', () => {
    expect($sidebarGrouping.get()).toBe('date')
    expect($sidebarOrdering.get()).toBe('updated')
    expect($sidebarRowMeta.get()).toEqual(['preview', 'updated'])
  })

  it('offers no reset until something actually moves off the defaults', () => {
    expect($sidebarViewCustomized.get()).toBe(false)

    toggleSidebarRowMeta('tokens')

    expect($sidebarViewCustomized.get()).toBe(true)
  })

  it('is what reset puts back — every knob, not just the filters', () => {
    setSidebarGrouping('project')
    setSidebarOrdering('cost')
    toggleSidebarRowMeta('updated')
    toggleSidebarRowMeta('cost')
    toggleSidebarStatusFilter('working')

    resetSidebarView()

    expect($sidebarGrouping.get()).toBe('date')
    expect($sidebarOrdering.get()).toBe('updated')
    expect($sidebarRowMeta.get()).toEqual(['preview', 'updated'])
    expect($sidebarViewCustomized.get()).toBe(false)
  })

  it('ships chronologically in the all-profiles scope too, and resets back to it', () => {
    $showAllProfiles.set(true)
    setSidebarGrouping('profile')

    resetSidebarView()

    expect($sidebarGrouping.get()).toBe('date')
    expect($sidebarViewCustomized.get()).toBe(false)
  })

  it('resets the scope the user is not looking at, so flipping the rail cannot restore it', () => {
    setSidebarGrouping('status')
    $showAllProfiles.set(true)
    setSidebarGrouping('profile')

    resetSidebarView()
    $showAllProfiles.set(false)

    expect($sidebarGrouping.get()).toBe('date')
  })

  it('keeps Project available as an explicit developer view', () => {
    expect($sidebarGrouping.get()).toBe('date')

    setSidebarGrouping('project')
    expect($sidebarGrouping.get()).toBe('project')

    setSidebarGrouping('date')
    expect($sidebarGrouping.get()).toBe('date')
  })

  it('migrates only the untouched project-first shipped view to Tasks', () => {
    window.localStorage.removeItem('stardust.desktop.sidebarAssistantFirst.v1')
    window.localStorage.setItem('hermes.desktop.agentsGroupedByWorkspace', 'true')
    window.localStorage.setItem('hermes.desktop.sidebarAgentsGrouped.allProfiles', 'true')

    migrateSidebarAssistantFirstDefault()

    expect(window.localStorage.getItem('hermes.desktop.agentsGroupedByWorkspace')).toBe('false')
    expect(window.localStorage.getItem('hermes.desktop.sidebarAgentsGrouped.allProfiles')).toBe('false')
    expect(window.localStorage.getItem('stardust.desktop.sidebarAssistantFirst.v1')).toBe('1')
  })

  it('preserves an explicitly customized old project view during migration', () => {
    window.localStorage.removeItem('stardust.desktop.sidebarAssistantFirst.v1')
    window.localStorage.setItem('hermes.desktop.agentsGroupedByWorkspace', 'true')
    window.localStorage.setItem('hermes.desktop.sidebarAgentsGrouped.allProfiles', 'true')
    window.localStorage.setItem('hermes.desktop.sidebarSortKey', 'cost')

    migrateSidebarAssistantFirstDefault()

    expect(window.localStorage.getItem('hermes.desktop.agentsGroupedByWorkspace')).toBe('true')
    expect(window.localStorage.getItem('stardust.desktop.sidebarAssistantFirst.v1')).toBe('1')
  })

  it('turns all-profiles on when the user groups by profile, since that is the ask', () => {
    setSidebarGrouping('profile')

    expect($showAllProfiles.get()).toBe(true)
    expect($sidebarGrouping.get()).toBe('profile')
  })
})
