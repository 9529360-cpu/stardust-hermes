import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { MarkdownTextContent } from './markdown-text'

// A Windows image path in image syntax used to be dropped by the sanitizer and
// shown as a blocked image. It now takes the media route, so the picture renders
// and is named after its file.
describe('MarkdownTextContent local image paths', () => {
  afterEach(cleanup)

  it('routes a Windows image path in image syntax to the media preview, named by its file', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text="![示例图表](D:\\out\\chart.png)" />)

    await waitFor(() => expect(container.querySelector('img')?.getAttribute('alt')).toBe('chart.png'))
    expect(container.textContent).not.toMatch(/Image blocked/)
  })

  it('renders a bare Windows image path in prose as a media preview named by its file', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text="已保存到 D:\\out\\结果.png" />)

    await waitFor(() => expect(container.querySelector('img')?.getAttribute('alt')).toBe('结果.png'))
    expect(container.textContent).toContain('已保存到')
    expect(container.textContent).not.toContain('D:\\out')
  })
})
