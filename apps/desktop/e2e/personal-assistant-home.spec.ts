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

test('personal assistant workspace keeps conversation primary and navigation unclipped', async ({}, testInfo) => {
  const page = fixture!.page
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const noteConsole = (message: { text: () => string; type: () => string }) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  }
  const notePageError = (error: Error) => pageErrors.push(error.stack ?? error.message)
  page.on('console', noteConsole)
  page.on('pageerror', notePageError)

  const intro = page.locator('[data-slot="aui_intro"]')
  const productNav = page.locator('[data-personal-product-nav]')
  const conversationButton = productNav.getByRole('button', { name: '对话', exact: true })
  const projectButton = productNav.getByRole('button', { name: '项目', exact: true })
  const overview = page.locator('[data-personal-overview]')
  const composer = page.getByRole('textbox', { name: '消息' })
  const rightContextToggle = page.locator('[data-tour="right-pane-toggle"]')

  await expect(intro).toBeVisible()
  await expect(page.getByTestId('assistant-quick-actions')).toHaveCount(0)
  await expect(productNav).toBeVisible()
  await expect(page.getByRole('tab', { name: '会话' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: '智能体' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'SESSIONS' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'BOTS' })).toHaveCount(0)
  await expect(page.getByText('今天想做什么？', { exact: true })).toBeVisible()
  await expect(conversationButton).toBeVisible()
  await expect(conversationButton).toHaveAttribute('aria-current', 'page')
  await expect(productNav.getByRole('button', { name: '工作空间' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /新建会话/ })).toBeVisible()
  await expect(productNav.getByRole('button', { name: '任务' })).toBeVisible()
  await expect(productNav.getByRole('button', { name: '项目' })).toBeVisible()
  await expect(productNav.getByRole('button', { name: '知识库' })).toBeVisible()
  await expect(productNav.getByRole('button', { name: '工具' })).toBeVisible()
  await expect(productNav.getByRole('button', { name: '设置' })).toBeVisible()
  await expect(overview).not.toBeVisible()

  // Projects are a sidebar/context mode around the same conversation, not a
  // competing center page or an immediate native folder-picker action.
  await projectButton.click()
  await expect(projectButton).toHaveAttribute('aria-current', 'page')
  await expect(conversationButton).not.toHaveAttribute('aria-current', 'page')
  await expect(composer).toBeVisible()
  await expect(overview).not.toBeVisible()

  await conversationButton.click()
  await expect(conversationButton).toHaveAttribute('aria-current', 'page')
  await expect(projectButton).not.toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('tab', { name: /terminal/i })).toHaveCount(0)

  const placeholder = await composer.getAttribute('data-placeholder')
  expect([
    '今天想做什么？',
    '告诉我你需要什么帮助',
    '有什么想让我处理的？',
    '从一个问题或目标开始',
    '把麻烦的事情交给我',
    '想聊点什么？',
    '我能为你做什么？'
  ]).toContain(placeholder)
  expect(placeholder).not.toContain('Hermes')

  await composer.fill('保留这段未发送草稿')

  const viewport = await page.evaluate(() => ({ height: innerHeight, width: innerWidth }))

  await rightContextToggle.click()
  await expect(overview).toBeVisible()
  await expect(overview).toHaveAttribute('aria-label', '上下文')
  await expect(composer).toBeVisible()
  await expect.poll(async () => composer.evaluate(element => element.textContent ?? '')).toBe('保留这段未发送草稿')

  await rightContextToggle.click()
  await expect(overview).not.toBeVisible()
  await expect.poll(async () => composer.evaluate(element => element.textContent ?? '')).toBe('保留这段未发送草稿')
  await expect(page.locator('[data-jarvis-workspace]')).toHaveCount(0)

  // Legacy /workspace URLs now resolve to the same center conversation instead
  // of replacing it with a competing full-page workspace.
  await page.evaluate(() => {
    window.location.hash = '#/workspace'
  })
  await expect(composer).toBeVisible()
  await expect(page.locator('[data-jarvis-workspace]')).toHaveCount(0)
  await expect(overview).not.toBeVisible()
  await expect(conversationButton).toHaveAttribute('aria-current', 'page')
  await expect(productNav.getByRole('button', { name: '工作空间' })).toHaveCount(0)

  const navBox = await productNav.boundingBox()
  expect(navBox).not.toBeNull()
  expect(navBox!.x).toBeGreaterThanOrEqual(0)
  expect(navBox!.y).toBeGreaterThanOrEqual(0)
  expect(navBox!.x + navBox!.width).toBeLessThanOrEqual(viewport.width)
  expect(navBox!.y + navBox!.height).toBeLessThanOrEqual(viewport.height)

  await page.screenshot({ path: testInfo.outputPath('personal-assistant-home.png') })

  console.log('[diagnostic consoleErrors]', consoleErrors)
  expect(pageErrors).toEqual([])
  expect(consoleErrors).toEqual([])
  page.off('console', noteConsole)
  page.off('pageerror', notePageError)
})
