import { describe, expect, it } from 'vitest'

import { skillDisplayDescription, skillDisplayName } from './skill-display'

describe('skill display translations', () => {
  it('shows curated Chinese copy without changing the source identifier or description', () => {
    const name = 'airtable'
    const description = 'Airtable REST API via curl. Records CRUD, filters, upserts.'

    expect(skillDisplayName(name, 'zh')).toBe('Airtable 数据管理')
    expect(skillDisplayDescription(name, description, 'zh')).toContain('Airtable REST API')
    expect(name).toBe('airtable')
    expect(description).toBe('Airtable REST API via curl. Records CRUD, filters, upserts.')
  })

  it('retains source metadata for other languages and unrecognized skills', () => {
    expect(skillDisplayName('airtable', 'en')).toBe('airtable')
    expect(skillDisplayName('airtable', 'zh-hant')).toBe('airtable')
    expect(skillDisplayDescription('airtable', 'Original', 'en')).toBe('Original')
    expect(skillDisplayName('user-created', 'zh')).toBe('user-created')
    expect(skillDisplayDescription('user-created', 'Personal description', 'zh')).toBe('Personal description')
    expect(skillDisplayName('airtable', 'zh', 'other')).toBe('airtable')
    expect(skillDisplayDescription('airtable', 'Custom skill', 'zh', 'other')).toBe('Custom skill')
    expect(skillDisplayName('baoyu-article-illustrator', 'zh', 'official')).toBe('宝玉文章配图')
    expect(skillDisplayDescription('baoyu-article-illustrator', 'Original', 'zh', 'official')).toContain('文章插图')
    expect(skillDisplayName('baoyu-article-illustrator', 'en', 'official')).toBe('baoyu-article-illustrator')
    expect(skillDisplayName('baoyu-article-illustrator', 'zh', 'other')).toBe('baoyu-article-illustrator')
  })
})
