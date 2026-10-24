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

test('reply streaming preserves scroll follow and the messages-below badge', async () => {
  const page = fixture!.page
  const composer = page.locator('[data-slot="composer-rich-input"]')
  await composer.fill('Hello, can you hear me?')
  await composer.press('Enter')
  await expect(page.getByText('Hello from the mock inference server!', { exact: false })).toBeVisible({ timeout: 60_000 })
  const viewport = page.locator('[data-slot="aui_thread-viewport"]').first()
  await expect(viewport).toBeVisible()
  await expect.poll(() => viewport.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(8)
  await expect(page.locator('.thread-jump-button')).not.toHaveAttribute('aria-label', /[1-9]\d* 条消息/)
})
