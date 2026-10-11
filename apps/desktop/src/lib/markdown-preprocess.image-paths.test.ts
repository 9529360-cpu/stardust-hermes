import { describe, expect, it } from 'vitest'

import { preprocessMarkdown } from '@/lib/markdown-preprocess'
import { mediaMarkdownHref } from '@/lib/media'

// A bare absolute image path in a reply ("已保存到 D:\报告\结果.png") becomes a
// media link through the same `#media:` route a `[label](path)` link takes, so
// the picture renders where the path was named. Other file types, code, URLs
// and relative paths keep their existing plain-text handling.

function mediaLink(path: string, label: string): string {
  return `[${label}](${mediaMarkdownHref(path)})`
}

describe('preprocessMarkdown bare image paths', () => {
  it('turns a Windows image path in prose into a media link', () => {
    expect(preprocessMarkdown('已保存到 D:\\报告\\结果.png')).toBe(
      `已保存到 ${mediaLink('D:\\报告\\结果.png', '结果.png')}`
    )
  })

  it.each([
    ['/home/me/chart.svg', 'chart.svg'],
    ['/home/me/photo.jpg', 'photo.jpg'],
    ['/home/me/photo.jpeg', 'photo.jpeg'],
    ['/home/me/anim.gif', 'anim.gif'],
    ['/home/me/banner.webp', 'banner.webp'],
    ['D:/out/shot.PNG', 'shot.PNG']
  ])('routes the image path %s', (path, label) => {
    expect(preprocessMarkdown(`结果见 ${path}`)).toBe(`结果见 ${mediaLink(path, label)}`)
  })

  it('leaves other file types as plain text', () => {
    const input = '报告在 D:\\out\\报告.docx，笔记在 /tmp/notes.md，数据在 /tmp/data.csv'

    expect(preprocessMarkdown(input)).toBe(input)
  })

  it('leaves code spans and fenced code untouched', () => {
    const input = ['用 `D:\\out\\a.png` 查看。', '', '```', 'cp /tmp/b.png /tmp/c.png', '```'].join('\n')

    expect(preprocessMarkdown(input)).toBe(input)
  })

  it('does not treat URLs or relative paths as local image paths', () => {
    const output = preprocessMarkdown('见 https://example.com/images/a.png 与 images/b.png，以及 file:///tmp/c.png')

    expect(output).not.toContain('#media:')
    expect(output).toContain('images/b.png')
    expect(output).toContain('file:///tmp/c.png')
  })

  it('routes a Windows image path used as a link or image target through the media link', () => {
    expect(preprocessMarkdown('[报告](D:\\out\\a.png)')).toBe(`[报告](${mediaMarkdownHref('D:\\out\\a.png')})`)
    // The sanitizer drops a drive-letter image source, so image syntax takes the media route too.
    expect(preprocessMarkdown('![示例图表](D:\\out\\b.png)')).toBe(`[示例图表](${mediaMarkdownHref('D:\\out\\b.png')})`)
  })

  it('leaves a POSIX image source on the inline image pipeline', () => {
    const input = '![示例](/out/b.png)'

    expect(preprocessMarkdown(input)).toBe(input)
  })

  it('does not treat a path inside a link label as prose', () => {
    expect(preprocessMarkdown('[D:\\out\\a.png 说明](https://example.com/docs)')).not.toContain('#media:')
  })

  it('keeps punctuation that follows the path outside the link', () => {
    expect(preprocessMarkdown('保存为 D:\\out\\a.png。')).toBe(`保存为 ${mediaLink('D:\\out\\a.png', 'a.png')}。`)
    expect(preprocessMarkdown('见 /tmp/a.png.')).toBe(`见 ${mediaLink('/tmp/a.png', 'a.png')}.`)
    expect(preprocessMarkdown('(/tmp/a.png)')).toBe(`(${mediaLink('/tmp/a.png', 'a.png')})`)
  })

  it('does not match a name that only starts with an image extension', () => {
    const input = '日志 /tmp/a.png.txt 与 /tmp/a.pngx'

    expect(preprocessMarkdown(input)).toBe(input)
  })

  it('matches an image path written directly after Chinese text', () => {
    expect(preprocessMarkdown('保存到/tmp/a.png')).toBe(`保存到${mediaLink('/tmp/a.png', 'a.png')}`)
  })
})
