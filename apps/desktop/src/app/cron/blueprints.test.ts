import { describe, expect, it } from 'vitest'

import type { AutomationBlueprint } from '@/hermes'
import { TRANSLATIONS } from '@/i18n/catalog'

import {
  blueprintDisplayDescription,
  blueprintDisplayFieldLabel,
  blueprintDisplayOption,
  blueprintDisplayTitle,
  initialBlueprintValues
} from './blueprints'

function blueprint(fields: AutomationBlueprint['fields']): AutomationBlueprint {
  return {
    key: 'test',
    title: 'Test',
    description: '',
    category: 'general',
    tags: [],
    command: '',
    appUrl: '',
    fields
  }
}

describe('blueprint display copy', () => {
  const c = TRANSLATIONS.zh.cron

  it('localizes official blueprints and their field labels by stable key', () => {
    const official = {
      ...blueprint([{ name: 'time', type: 'time', label: 'Time', default: '08:00', options: [], optional: false, help: '' }]),
      key: 'morning-brief',
      title: 'Morning briefing',
      description: "A short daily briefing: today's calendar, weather, and anything urgent waiting on you."
    }

    expect(blueprintDisplayTitle(official, c)).toBe('晨间简报')
    expect(blueprintDisplayDescription(official, c)).toContain('今日日程')
    expect(blueprintDisplayFieldLabel(official.key, official.fields[0], c)).toBe('时间')
    expect(blueprintDisplayOption('sunday', c)).toBe('周日')
  })

  it('leaves third-party blueprint copy untouched when no display override exists', () => {
    const thirdParty = { ...blueprint([]), key: 'vendor-custom', title: 'Vendor workflow', description: 'Vendor copy' }

    expect(blueprintDisplayTitle(thirdParty, c)).toBe('Vendor workflow')
    expect(blueprintDisplayDescription(thirdParty, c)).toBe('Vendor copy')
  })
})

describe('initialBlueprintValues', () => {
  it('seeds each field from its default', () => {
    const values = initialBlueprintValues(
      blueprint([
        { name: 'time', type: 'time', label: 'Time', default: '08:00', options: [], optional: false, help: '' },
        {
          name: 'topic',
          type: 'enum',
          label: 'Topic',
          default: 'news',
          options: ['news', 'sports'],
          optional: false,
          help: ''
        }
      ])
    )

    expect(values).toEqual({ time: '08:00', topic: 'news' })
  })

  it('falls back to an empty string when a field has no default', () => {
    const values = initialBlueprintValues(
      blueprint([{ name: 'topic', type: 'text', label: 'Topic', default: null, options: [], optional: true, help: '' }])
    )

    expect(values).toEqual({ topic: '' })
  })

  it('returns an empty object for a blueprint with no fields', () => {
    expect(initialBlueprintValues(blueprint([]))).toEqual({})
  })

  it("seeds the deliver slot to 'local' when its default is the dashboard-only 'origin'", () => {
    const values = initialBlueprintValues(
      blueprint([
        {
          name: 'deliver',
          type: 'enum',
          label: 'Deliver',
          default: 'origin',
          options: ['origin', 'local', 'telegram'],
          optional: false,
          help: ''
        }
      ])
    )

    expect(values).toEqual({ deliver: 'local' })
  })

  it("seeds the deliver slot to 'local' when it has no default", () => {
    const values = initialBlueprintValues(
      blueprint([
        {
          name: 'deliver',
          type: 'enum',
          label: 'Deliver',
          default: null,
          options: ['origin', 'local'],
          optional: false,
          help: ''
        }
      ])
    )

    expect(values).toEqual({ deliver: 'local' })
  })

  it('leaves a non-origin deliver default untouched', () => {
    const values = initialBlueprintValues(
      blueprint([
        {
          name: 'deliver',
          type: 'enum',
          label: 'Deliver',
          default: 'telegram',
          options: ['origin', 'local', 'telegram'],
          optional: false,
          help: ''
        }
      ])
    )

    expect(values).toEqual({ deliver: 'telegram' })
  })
})
