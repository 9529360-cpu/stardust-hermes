import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('context rail preview keeps the conversation mounted and uses the real preview target', async () => {
  const page = fixture!.page

  await page.evaluate(() => {
    window.localStorage.setItem('hermes.desktop.personalLayoutVersion', '3')
    window.localStorage.setItem('hermes.desktop.rightContextOpen.v1', 'true')
    window.localStorage.setItem(
      'hermes.desktop.previewTabs.v2',
      JSON.stringify([
        {
          id: 'url:workspace-e2e-preview',
          target: {
            kind: 'url',
            label: 'Workspace preview E2E',
            source: 'https://preview.example.test/',
            url: 'https://preview.example.test/'
          }
        }
      ])
    )
  })

  await page.reload()
  await waitForAppReady(fixture!, 120_000)

  const browserProfilePrompt = page.getByRole('dialog').filter({ hasText: '让网站保持登录状态' })

  if (await browserProfilePrompt.isVisible().catch(() => false)) {
    await browserProfilePrompt.getByRole('button', { name: '暂不' }).click()
  }

  const productNav = page.locator('[data-personal-product-nav]')
  const composer = page.getByRole('textbox', { name: '消息' })

  await expect(productNav.getByRole('button', { name: '对话', exact: true })).toBeVisible()
  await expect(productNav.getByRole('button', { name: '工作空间' })).toHaveCount(0)
  await expect(composer).toBeVisible()
  await expect(page.locator('[data-jarvis-workspace]')).toHaveCount(0)

  // Preview tabs occupy the contextual right rail while the center conversation
  // remains mounted; they never depend on a competing Workspace page.
  const previewTab = page.getByRole('tab', { name: /preview\.example\.test/ })
  await expect(previewTab).toBeVisible()
  await previewTab.click()

  if (await browserProfilePrompt.isVisible().catch(() => false)) {
    await browserProfilePrompt.getByRole('button', { name: '暂不' }).click()
  }

  await expect(composer).toBeVisible()
  await expect(page.locator('[data-personal-overview]')).not.toBeVisible()
  await expect(page.getByRole('textbox', { name: '地址' })).toHaveValue('https://preview.example.test/')
})
