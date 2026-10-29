// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { PanelBody, PanelList, PanelRowMenu } from './panel'

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => undefined
  Element.prototype.setPointerCapture ??= () => undefined
  HTMLElement.prototype.scrollIntoView ??= () => undefined
})

afterEach(cleanup)

describe('PanelRowMenu', () => {
  it('opens its actions menu from the kebab without a tooltip', async () => {
    const onSelect = vi.fn()

    render(<PanelRowMenu items={[{ label: 'Rename', onSelect }]} />)

    const trigger = screen.getByRole('button', { name: 'Actions' })

    expect(trigger.closest('[data-slot="tooltip-trigger"]')).toBeNull()

    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: 'mouse' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename' }))

    expect(onSelect).toHaveBeenCalledOnce()
  })
})

describe('PanelBody responsive layout', () => {
  it('uses its own container width for the list/detail breakpoint', () => {
    const { container } = render(
      <PanelBody>
        <PanelList>Scheduled jobs</PanelList>
        <main>Details</main>
      </PanelBody>
    )

    const containerQuery = container.querySelector('[class*="@container/panel-body"]')
    const body = containerQuery?.firstElementChild
    const list = body?.firstElementChild

    expect(containerQuery?.className).toContain('@container/panel-body')
    expect(containerQuery?.className).toContain('min-w-0')
    expect(body?.className).toContain('@min-[47.5rem]/panel-body:flex-row')
    expect(list?.className).toContain('max-h-[40%]')
    expect(list?.className).toContain('@min-[47.5rem]/panel-body:max-h-none')
    expect(list?.className).toContain('@min-[47.5rem]/panel-body:w-52')
  })
})
