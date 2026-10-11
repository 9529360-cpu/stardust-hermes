import { describe, expect, it } from 'vitest'

import { mediaName } from '@/lib/media'

// `new URL('D:\\报告\\结果.png')` parses as a URL with scheme `d:`, so a Windows
// drive path used to come back as a percent-encoded fragment with backslashes
// in it. That string is the label and alt text of a media preview.
describe('mediaName', () => {
  it('returns the file name for a Windows drive path with backslashes', () => {
    expect(mediaName('D:\\报告\\结果.png')).toBe('结果.png')
    expect(mediaName('C:\\x\\report.png')).toBe('report.png')
  })

  it('returns the file name for forward-slash drive and POSIX paths', () => {
    expect(mediaName('D:/out/a.jpg')).toBe('a.jpg')
    expect(mediaName('/tmp/chart.svg')).toBe('chart.svg')
  })

  it('returns the file name for a file URL', () => {
    expect(mediaName('file:///tmp/clip.mp4')).toBe('clip.mp4')
  })
})
