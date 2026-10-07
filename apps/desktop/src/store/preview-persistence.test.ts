import { describe, expect, it } from 'vitest'

import { decodePreviewTabs } from './preview'

describe('persisted preview migration', () => {
  it('restores optional stored-session ownership while preserving legacy tabs', () => {
    const [legacy, owned] = decodePreviewTabs(
      JSON.stringify([
        {
          id: 'file:file:///work/legacy.html',
          target: {
            kind: 'file',
            label: 'legacy.html',
            source: '/work/legacy.html',
            url: 'file:///work/legacy.html'
          }
        },
        {
          id: 'file:file:///work/owned.html',
          storedSessionId: '  task-two  ',
          target: {
            kind: 'file',
            label: 'owned.html',
            source: '/work/owned.html',
            url: 'file:///work/owned.html'
          }
        }
      ])
    )

    expect(legacy?.storedSessionId).toBeUndefined()
    expect(owned?.storedSessionId).toBe('task-two')
  })

  it('does not restore transient previews into persistent tabs', () => {
    expect(
      decodePreviewTabs(
        JSON.stringify([
          {
            id: 'file:file:///remote/report.html',
            storedSessionId: 'task-one',
            target: {
              kind: 'file',
              label: 'report.html',
              source: '/remote/report.html',
              transient: true,
              url: 'file:///remote/report.html'
            }
          }
        ])
      )
    ).toEqual([])
  })

  it('upgrades a pre-PDF remote tab from binary to pdf', () => {
    const source = '/remote/.hermes/desktop-attachments/spec.pdf'

    const [restored] = decodePreviewTabs(
      JSON.stringify([
        {
          id: `file:file://${source}`,
          target: {
            binary: true,
            kind: 'file',
            label: 'spec.pdf',
            large: true,
            path: source,
            previewKind: 'binary',
            source,
            url: `file://${source}`
          }
        }
      ])
    )

    expect(restored?.target.previewKind).toBe('pdf')
  })

  it('leaves a persisted non-PDF binary tab unchanged', () => {
    const source = '/work/archive.zip'

    const [restored] = decodePreviewTabs(
      JSON.stringify([
        {
          id: `file:file://${source}`,
          target: {
            binary: true,
            kind: 'file',
            label: 'archive.zip',
            path: source,
            previewKind: 'binary',
            source,
            url: `file://${source}`
          }
        }
      ])
    )

    expect(restored?.target.previewKind).toBe('binary')
  })

  it.each(['report.pdf#notes', 'report.pdf?draft'])('treats %s as a literal filesystem path', sourceName => {
    const source = `/work/${sourceName}`

    const [restored] = decodePreviewTabs(
      JSON.stringify([
        {
          id: `file:file://${encodeURI(source)}`,
          target: {
            binary: true,
            kind: 'file',
            label: sourceName,
            path: source,
            previewKind: 'binary',
            source,
            url: `file:///work/${encodeURIComponent(sourceName)}`
          }
        }
      ])
    )

    expect(restored?.target.previewKind).toBe('binary')
  })

  it('does not overwrite a non-binary PDF preview kind', () => {
    const source = '/work/spec.pdf'

    const [restored] = decodePreviewTabs(
      JSON.stringify([
        {
          id: `file:file://${source}`,
          target: {
            kind: 'file',
            label: 'spec.pdf',
            path: source,
            previewKind: 'text',
            source,
            url: `file://${source}`
          }
        }
      ])
    )

    expect(restored?.target.previewKind).toBe('text')
  })
})
