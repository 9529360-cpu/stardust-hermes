import { describe, expect, it } from 'vitest'

import { movedSettingsTabRedirect } from './moved-tabs'

describe('movedSettingsTabRedirect', () => {
  it('sends the retired Settings plugin/MCP tabs to Capabilities, keeping the row selector', () => {
    expect(movedSettingsTabRedirect('?tab=plugins')).toBe('/skills?tab=plugins')
    expect(movedSettingsTabRedirect('?tab=plugins&plugin=demo%2Fplugin')).toBe(
      '/skills?tab=plugins&plugin=demo%2Fplugin'
    )
    expect(movedSettingsTabRedirect('?tab=mcp&server=github')).toBe('/skills?tab=mcp&server=github')
  })

  it('moves retired provider/account settings to direct model services', () => {
    expect(movedSettingsTabRedirect('?tab=billing')).toBe('/settings?tab=config%3Amodel')
    expect(movedSettingsTabRedirect('?tab=providers&pview=accounts')).toBe('/settings?tab=config%3Amodel')
    expect(movedSettingsTabRedirect('?tab=providers&pview=keys')).toBe('/settings?tab=config%3Amodel')
    expect(movedSettingsTabRedirect('?tab=providers&pview=custom-endpoints')).toBe('/settings?tab=config%3Amodel')
    expect(movedSettingsTabRedirect('?tab=providers&pview=local')).toBe('/settings?tab=config%3Amodel')
  })

  it('leaves live Settings tabs alone', () => {
    expect(movedSettingsTabRedirect('?tab=config%3Amodel')).toBeNull()
    expect(movedSettingsTabRedirect('')).toBeNull()
  })
})
