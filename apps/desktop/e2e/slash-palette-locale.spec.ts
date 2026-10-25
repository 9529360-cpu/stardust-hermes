/**
 * The slash palette keeps command names (`/new`) and translates descriptions
 * and section headers with the desktop locale.
 */

import { setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

test('Chinese UI shows slash descriptions and section headers in Chinese', async () => {
  const fixture = await setupMockBackend({ extraDisplayConfig: '  language: zh\n' })

  try {
    await waitForAppReady(fixture, 120_000)
    const composer = fixture.page.locator('[contenteditable="true"]').first()
    await composer.click()
    await composer.pressSequentially('/')

    const drawer = fixture.page.locator('[data-slot="composer-completion-drawer"]')
    await expect(drawer).toContainText('会话', { timeout: 30_000 })
    await expect(drawer).toContainText('/new')
    await expect(drawer).toContainText('开始一段新的桌面对话')
    await expect(drawer).toContainText('/handoff')
    await expect(drawer).toContainText('把此会话转交到消息平台')
    await expect(drawer.getByText('/new', { exact: true })).toBeVisible()
  } finally {
    await fixture.cleanup()
  }
})
