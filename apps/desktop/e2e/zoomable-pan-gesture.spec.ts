import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const TRIGGER = 'E2E_ZOOMABLE_PAN'
const MERMAID_REPLY = [
  '```mermaid',
  'flowchart LR',
  '  A[Composer] --> B[Gateway]',
  '  B --> C[Streaming response]',
  '  C --> D[Rendered Mermaid diagram]',
  '```'
].join('\n')

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    extraConfig: 'auxiliary:\n  title_generation:\n    enabled: false',
    extraDisplayConfig: '  language: en',
    mockServer: {
      replyForPrompt: prompt => prompt.includes(TRIGGER) ? MERMAID_REPLY : 'Ready.'
    }
  })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

// eslint-disable-next-line no-empty-pattern -- Playwright requires this object argument
test('Mermaid Zoomable commits a pan and rolls back a cancelled pointer gesture', async ({}, testInfo) => {
  const page = fixture!.page
  const composer = page.locator('[contenteditable="true"]').first()
  const viewport = page.locator('[data-slot="aui_thread-viewport"]')

  await expect(composer).toBeVisible()
  await composer.click()
  await composer.fill(`Draw a diagram for ${TRIGGER}`)
  await page.keyboard.press('Enter')
  await expect(viewport.getByRole('button', { name: 'Open diagram' })).toBeVisible({ timeout: 60_000 })

  await viewport.getByRole('button', { name: 'Open diagram' }).click()
  const dialog = page.getByRole('dialog')
  const stage = dialog.locator('[data-slot="zoomable-stage"]')
  const svg = dialog.locator('svg').first()
  const transformedContent = svg.locator('xpath=../..')

  await expect(dialog).toBeVisible()
  await expect(svg).toBeVisible()
  const initialTransform = await transformedContent.evaluate(element => element.style.transform)

  await stage.evaluate(element => {
    const rect = element.getBoundingClientRect()
    element.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width * 0.7,
      clientY: rect.top + rect.height * 0.6,
      ctrlKey: true,
      deltaY: -120
    }))
  })

  await expect.poll(() => transformedContent.evaluate(element => element.style.transform)).not.toBe(initialTransform)
  const zoomedTransform = await transformedContent.evaluate(element => element.style.transform)
  const bounds = await stage.boundingBox()
  expect(bounds).not.toBeNull()

  // Cancel the active pointer from its real move event before the queued RAF
  // paints. This exercises native pointer capture and the production cancel
  // handler without issuing a DevTools evaluation while the mouse is held.
  await stage.evaluate(element => {
    const cancelOnMove = (event: Event) => {
      const pointer = event as PointerEvent

      if (!pointer.buttons) {
        return
      }

      element.removeEventListener('pointermove', cancelOnMove)
      element.dispatchEvent(new PointerEvent('pointercancel', {
        bubbles: true,
        pointerId: pointer.pointerId,
        pointerType: pointer.pointerType
      }))
    }

    element.addEventListener('pointermove', cancelOnMove)
  })
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2)
  await page.mouse.down()
  await page.mouse.move(bounds!.x + bounds!.width / 2 + 48, bounds!.y + bounds!.height / 2 + 30, { steps: 2 })
  await expect.poll(() => transformedContent.evaluate(element => element.style.transform)).toBe(zoomedTransform)
  await expect(stage).not.toHaveClass(/cursor-grabbing/)
  await page.mouse.up()

  const stageAfterCancel = await stage.boundingBox()
  expect(stageAfterCancel).not.toBeNull()
  await page.mouse.move(stageAfterCancel!.x + stageAfterCancel!.width / 2, stageAfterCancel!.y + stageAfterCancel!.height / 2)
  await page.mouse.down()
  await page.mouse.move(stageAfterCancel!.x + stageAfterCancel!.width / 2 - 28, stageAfterCancel!.y + stageAfterCancel!.height / 2 - 16, { steps: 3 })
  const committedPan = await transformedContent.evaluate(element => element.style.transform)
  expect(committedPan).not.toBe(zoomedTransform)
  await page.mouse.up()
  await expect.poll(() => transformedContent.evaluate(element => element.style.transform)).toBe(committedPan)
  await expect(stage).not.toHaveClass(/cursor-grabbing/)

  await page.screenshot({ path: testInfo.outputPath('mermaid-zoomable-after-pan.png') })
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
})
