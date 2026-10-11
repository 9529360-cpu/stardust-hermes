import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'

import { MarkdownTextContent } from './markdown-text'

const SVG_CHART = `data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="#2563eb"/></svg>'
)}`

const PIXEL_PNG =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

const couldntLoad = (name: string) => TRANSLATIONS.en.assistant.media.couldntLoad(name)

// Regression for the assistant reply that showed "[Image blocked: 示例图表]":
// the markdown sanitizer dropped every `data:` image source before the image
// policy ran, so safe inline images never reached <img>. Blocked sources now
// show a localized placeholder instead of rehype-harden's English indicator.
describe('MarkdownTextContent inline images', () => {
  afterEach(cleanup)

  it('renders an SVG data image as an <img>', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text={`![示例图表](${SVG_CHART})`} />)

    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())

    const image = container.querySelector('img')

    expect(image?.getAttribute('src')).toBe(SVG_CHART)
    expect(image?.getAttribute('alt')).toBe('示例图表')
    expect(container.textContent).not.toMatch(/Image blocked/)
  })

  it('renders a PNG data image as an <img>', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text={`![pixel](${PIXEL_PNG})`} />)

    await waitFor(() => expect(container.querySelector('img')?.getAttribute('src')).toBe(PIXEL_PNG))
  })

  it('keeps non-image data URLs blocked behind a localized placeholder', async () => {
    const { container } = render(
      <MarkdownTextContent isRunning={false} text="![嵌入页面](data:text/html,%3Cscript%3Ealert(1)%3C/script%3E)" />
    )

    await screen.findByText(couldntLoad('嵌入页面'))

    expect(container.querySelector('img')).toBeNull()
    expect(container.innerHTML).not.toContain('data:text/html')
  })

  it('keeps file: and javascript: image sources blocked behind a localized placeholder', async () => {
    const { container } = render(
      <MarkdownTextContent isRunning={false} text={'![本地图](file:///C:/x/y.png) ![脚本](javascript:alert(1))'} />
    )

    await screen.findByText(couldntLoad('本地图'))
    await screen.findByText(couldntLoad('脚本'))

    expect(container.querySelector('img')).toBeNull()
    expect(container.innerHTML).not.toContain('file:///')
    expect(container.innerHTML).not.toContain('javascript:')
  })

  it('names a blocked image that has no alt text after its file name', async () => {
    render(<MarkdownTextContent isRunning={false} text="![](clip.png)" />)

    await screen.findByText(couldntLoad('clip.png'))
  })

  it('drops the English blocked-link marker and keeps the link text', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text="[打开链接](javascript:alert(1))" />)

    await screen.findByText('打开链接')

    expect(container.textContent).not.toMatch(/\[blocked\]|Blocked URL/)
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull()
  })

  it('still renders an https image', async () => {
    const { container } = render(
      <MarkdownTextContent isRunning={false} text="![远程图](https://example.com/chart.png)" />
    )

    await waitFor(() =>
      expect(container.querySelector('img')?.getAttribute('src')).toBe('https://example.com/chart.png')
    )
  })
})
