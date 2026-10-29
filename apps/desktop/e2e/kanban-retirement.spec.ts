import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
})

// eslint-disable-next-line no-empty-pattern -- Playwright needs the object pattern here.
test('retiring the board preserves scheduled-job navigation and a usable conversation', async ({}, testInfo) => {
  const page = fixture!.page
  const nav = page.locator('[data-personal-product-nav]')
  const scheduledJobs = nav.getByRole('button', { name: '定时任务', exact: true })
  const newChat = nav.getByRole('button', { name: '新建对话', exact: true })

  await scheduledJobs.click()
  await expect(page.locator('[data-cron-page]')).toBeVisible()
  await expect(scheduledJobs).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('button', { name: /kanban|看板/i })).toHaveCount(0)

  await newChat.click()
  await expect(page.locator('[contenteditable="true"]').first()).toBeVisible()

  for (const [width, height] of [
    [1220, 800],
    [960, 700]
  ]) {
    await fixture!.app.evaluate(
      ({ BrowserWindow }, size) => {
        BrowserWindow.getAllWindows()[0].setSize(size[0], size[1])
      },
      [width, height]
    )
    await expect(nav).toBeVisible()
    await expect(newChat).toBeVisible()
    const bounds = await nav.boundingBox()
    const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }))
    expect(bounds).not.toBeNull()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.y).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width)
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height)
    await page.screenshot({ path: testInfo.outputPath(`board-removed-${width}.png`) })
  }
})
