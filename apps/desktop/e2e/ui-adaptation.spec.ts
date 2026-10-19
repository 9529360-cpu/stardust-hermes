import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

const TASK_ROOT = process.env.STARDUST_UI_ADAPTATION_TASK_ROOT ?? path.join(
  os.tmpdir(),
  `stardust-ui-adaptation-${process.pid}-${Date.now()}`,
)
fs.mkdirSync(TASK_ROOT, { recursive: true })

let fixture: MockBackendFixture | null = null

function appPage() {
  if (!fixture) throw new Error('Electron fixture is not ready')
  return fixture.page
}

async function paneState(id: string) {
  return appPage().evaluate(key => {
    const raw = localStorage.getItem('hermes.desktop.paneStates.v1')
    return raw ? (JSON.parse(raw) as Record<string, unknown>)[key] : undefined
  }, id)
}

test.beforeAll(async () => {
  test.setTimeout(150_000)
  const sandboxRoot = path.join(TASK_ROOT, 'sandbox')
  fs.mkdirSync(sandboxRoot, { recursive: true })
  process.env.TEMP = sandboxRoot
  process.env.TMP = sandboxRoot
  // Use the explicit Windows interpreter while keeping the backend root and
  // PYTHONPATH on this candidate checkout (venv is a read-only junction).
  process.env.STARDUST_DESKTOP_PYTHON = 'D:/Stardust/hermes-agent/.venv/Scripts/python.exe'
  process.env.STARDUST_PYTHON = process.env.STARDUST_DESKTOP_PYTHON
  delete process.env.ELECTRON_RUN_AS_NODE
  delete process.env.NODE_ENV
  delete process.env.STARDUST_REMOTE_URL
  delete process.env.STARDUST_REMOTE_TOKEN
  delete process.env.HERMES_HOME
  delete process.env.HERMES_SHARED_AUTH_DIR
  fixture = await setupMockBackend({
    seedSandbox(sandbox) {
      process.env.HERMES_HOME = sandbox.hermesHome
      process.env.HERMES_SHARED_AUTH_DIR = path.join(sandbox.hermesHome, 'shared')
    },
  })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('MasterDetail sash commits, resets, cancels, and clears on a real window resize', async () => {
  const page = appPage()
  const app = fixture!.app

  await page.evaluate(() => { window.location.hash = '#/skills' })
  const sash = page.locator('.stardust-master-detail-sash').first()
  await expect(sash).toBeVisible({ timeout: 30_000 })
  const grid = page.locator('.stardust-master-detail-grid').first()
  const rect = await grid.locator(':scope > *').first().boundingBox()
  expect(rect).not.toBeNull()
  const initialWidth = Math.round(rect!.width)
  const sashBox = await sash.boundingBox()
  expect(sashBox).not.toBeNull()
  const sx = sashBox!.x + sashBox!.width / 2
  const sy = sashBox!.y + sashBox!.height / 2

  await page.mouse.move(sx, sy)
  await page.mouse.down()
  await page.mouse.move(sx + 76, sy, { steps: 5 })
  await expect.poll(async () => grid.evaluate(el => getComputedStyle(el).gridTemplateColumns)).not.toBe('')
  expect(await paneState('capabilities-split')).toBeUndefined()
  await page.mouse.up()
  await expect.poll(async () => (await paneState('capabilities-split') as { widthOverride?: number } | undefined)?.widthOverride).toBeGreaterThan(initialWidth)
  const committed = await paneState('capabilities-split') as { widthOverride?: number }
  expect(committed.widthOverride).toBeGreaterThan(initialWidth)
  await page.getByRole('button', { name: 'MCP', exact: true }).click()
  await expect(page).toHaveURL(/tab=mcp/)
  await page.locator('[data-tour="tab-skills"]').click()
  await expect(page).toHaveURL(/#\/skills$/)
  await expect.poll(async () => (await paneState('capabilities-split') as { widthOverride?: number } | undefined)?.widthOverride).toBe(committed.widthOverride)
  expect(await page.evaluate(() => document.querySelectorAll('.stardust-resize-shield').length)).toBe(0)

  const committedSash = await sash.boundingBox()
  expect(committedSash).not.toBeNull()
  await page.mouse.dblclick(committedSash!.x + committedSash!.width / 2, committedSash!.y + committedSash!.height / 2)
  await expect.poll(async () => (await paneState('capabilities-split') as { widthOverride?: number } | undefined)?.widthOverride).toBeUndefined()

  const beforeCancel = await paneState('capabilities-split')
  const boxAfterReset = await sash.boundingBox()
  expect(boxAfterReset).not.toBeNull()
  const cx = boxAfterReset!.x + boxAfterReset!.width / 2
  const cy = boxAfterReset!.y + boxAfterReset!.height / 2
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  await page.mouse.move(cx + 64, cy, { steps: 4 })
  await page.keyboard.press('Escape')
  await page.mouse.up()
  expect(await paneState('capabilities-split')).toEqual(beforeCancel)
  await expect.poll(() => page.evaluate(() => document.querySelectorAll('.stardust-resize-shield').length)).toBe(0)

  await page.mouse.move(cx, cy)
  await page.mouse.down()
  await page.mouse.move(cx + 52, cy, { steps: 3 })
  const widthBeforeResize = await page.locator('.stardust-master-detail-grid').evaluate(el => el.getBoundingClientRect().width)
  const windowSize = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getSize())
  await app.evaluate(({ BrowserWindow }, height) => {
    const w = BrowserWindow.getAllWindows()[0]
    w.setSize(w.getSize()[0] - 120, height)
  }, windowSize[1])
  await expect.poll(() => page.locator('.stardust-master-detail-grid').evaluate(el => el.getBoundingClientRect().width)).toBeLessThan(widthBeforeResize)
  await expect.poll(async () => (await paneState('capabilities-split') as { widthOverride?: number } | undefined)?.widthOverride).toBeUndefined()
  await page.mouse.up()
  expect((await paneState('capabilities-split') as { widthOverride?: number } | undefined)?.widthOverride).toBeUndefined()
  expect(await page.evaluate(() => document.querySelectorAll('.stardust-resize-shield').length)).toBe(0)

  const paneResize = async () => {
    const page = appPage()
    await page.getByRole('button', { name: 'MCP', exact: true }).click()
    await expect(page).toHaveURL(/tab=mcp/)
    const pane = page.locator('section').filter({ has: page.locator('.cursor-row-resize') }).last()
    await expect(pane.locator('.cursor-row-resize')).toBeVisible({ timeout: 30_000 })
    return { page, pane }
  }
  const { page: mcpPage, pane } = await paneResize()
  const paneBody = pane.locator(':scope > div').last()
  const paneBodyHeight = await paneBody.evaluate(el => el.getBoundingClientRect().height)
  const paneSash = pane.locator('.cursor-row-resize')
  const paneSashBox = await paneSash.boundingBox()
  expect(paneSashBox).not.toBeNull()
  await page.mouse.move(paneSashBox!.x + 15, paneSashBox!.y + 3)
  await page.mouse.down()
  await page.mouse.move(paneSashBox!.x + 15, paneSashBox!.y - 64, { steps: 4 })
  await page.mouse.up()
  await expect.poll(() => paneBody.evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThan(paneBodyHeight)
  const resizedBodyHeight = await paneBody.evaluate(el => el.getBoundingClientRect().height)
  await expect.poll(async () => (await paneState('mcp-logs') as { heightOverride?: number } | undefined)?.heightOverride).toBeGreaterThan(paneBodyHeight)
  expect(Math.abs(resizedBodyHeight - (await paneState('mcp-logs') as { heightOverride: number }).heightOverride)).toBeLessThanOrEqual(2)
  await mcpPage.locator('[data-tour="tab-skills"]').click()
  await expect(mcpPage).toHaveURL(/#\/skills$/)
  const { page: remountedPage, pane: remountedPane } = await paneResize()
  const remountedBody = remountedPane.locator(':scope > div').last()
  await expect.poll(() => remountedBody.evaluate(el => el.getBoundingClientRect().height)).toBeCloseTo(resizedBodyHeight, 0)
  await expect.poll(async () => (await paneState('mcp-logs') as { heightOverride?: number } | undefined)?.heightOverride).toBeGreaterThan(paneBodyHeight)

  // Exercise a production SegmentedControl in Appearance settings using keyboard, then RTL.
  await remountedPage.evaluate(() => { window.location.hash = '#/settings?tab=config:appearance' })
  const segment = remountedPage.locator('[data-slot="segmented-control"]').first()
  await expect(segment).toBeVisible({ timeout: 30_000 })
  const selected = segment.locator('[data-slot="segmented-control-option"][aria-pressed="true"]')
  const selectedName = await selected.innerText()
  const allOptions = segment.locator('[data-slot="segmented-control-option"]')
  const selectedIndex = await selected.evaluate(el => Array.from(el.parentElement!.querySelectorAll('[data-slot="segmented-control-option"]')).indexOf(el))
  const nextIndex = (selectedIndex + 1) % await allOptions.count()
  const nextOption = allOptions.nth(nextIndex)
  const nextOptionName = await nextOption.innerText()
  await selected.focus()
  await page.keyboard.press('ArrowRight')
  await expect(nextOption).toBeFocused()
  await expect(nextOption).toHaveAttribute('aria-pressed', 'true')
  expect(nextOptionName.trim()).not.toBe(selectedName.trim())
  const rtlPreviousIndex = (nextIndex - 1 + await allOptions.count()) % await allOptions.count()
  const rtlPreviousName = await allOptions.nth(rtlPreviousIndex).innerText()
  await segment.evaluate(el => { el.setAttribute('dir', 'rtl') })
  await nextOption.focus()
  await page.keyboard.press('ArrowRight')
  await expect(allOptions.nth(rtlPreviousIndex)).toBeFocused()
  expect(rtlPreviousName.trim()).not.toBe(nextOptionName.trim())
})

test('scroll fades track top, middle, and bottom on real list content', async () => {
  const page = appPage()
  await page.evaluate(() => { window.location.hash = '#/skills' })
  const viewport = page.locator('.stardust-master-detail-grid [data-scroll-fade]').first()
  await expect(viewport).toBeVisible({ timeout: 30_000 })
  const fadeState = async () => viewport.evaluate(el => ({
    top: el.scrollTop,
    max: el.scrollHeight - el.clientHeight,
    fadeTop: getComputedStyle(el).getPropertyValue('--scroll-fade-top'),
    fadeBottom: getComputedStyle(el).getPropertyValue('--scroll-fade-bottom'),
    mask: getComputedStyle(el).maskImage || getComputedStyle(el).webkitMaskImage,
  }))
  const initialFade = await fadeState()
  expect(initialFade.max).toBeGreaterThan(0)
  expect(initialFade.fadeTop).toBe('0px')
  expect(parseFloat(initialFade.fadeBottom)).toBeGreaterThan(0)
  expect(initialFade.mask).not.toBe('none')
  await viewport.evaluate(el => { el.scrollTop = Math.floor((el.scrollHeight - el.clientHeight) / 2) })
  await expect.poll(async () => (await fadeState()).top).toBeGreaterThan(0)
  const middleFade = await fadeState()
  expect(middleFade.top).toBeLessThan(middleFade.max)
  expect(parseFloat(middleFade.fadeTop)).toBeGreaterThan(0)
  expect(parseFloat(middleFade.fadeBottom)).toBeGreaterThan(0)
  await viewport.evaluate(el => { el.scrollTop = el.scrollHeight })
  await expect.poll(async () => (await fadeState()).top).toBeGreaterThan(middleFade.top)
  await expect.poll(async () => (await fadeState()).fadeBottom).toBe('0px')
  const bottomFade = await fadeState()
  expect(Math.abs(bottomFade.max - bottomFade.top)).toBeLessThanOrEqual(1)
  expect(parseFloat(bottomFade.fadeTop)).toBeGreaterThan(0)
  const evidencePath = path.join(TASK_ROOT, 'ui-adaptation-native.png')
  await page.screenshot({ path: evidencePath, fullPage: false })
  expect(fs.existsSync(evidencePath)).toBe(true)
})
