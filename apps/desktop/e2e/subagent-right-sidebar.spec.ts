import { expect, test } from './test'
import { createSandbox, setupMockBackend, waitForAppReady } from './fixtures'

test('delegated work opens the right sidebar with scoped progress and controls', async () => {
  test.setTimeout(180_000)
  const project = createSandbox('team-workspace')
  const fixture = await setupMockBackend({
    mockServer: { holdFirstStreamForPrompt: 'Summarize the test results' },
    extraConfig: `terminal:\n  cwd: ${JSON.stringify(project.hermesHome)}`,
  })
  try {
    const { page, app } = fixture
    await waitForAppReady(fixture, 120_000)
    const composer = page.locator('[contenteditable="true"]').first()
    await composer.fill('E2E_SIDEBAR_TRIGGER: delegate the research work.')
    await composer.press('Enter')
    const roster = page.locator('aside [data-slot="composer-subagents"]')
    await expect(roster.getByText('Summarize the test results', { exact: true })).toBeVisible({ timeout: 45_000 })
    await roster.getByRole('button', { name: /Summarize the test results/ }).click()
    await expect(roster.getByRole('textbox')).toBeVisible()
    await expect(roster.getByRole('button', { name: /Stop|停止/ })).toBeVisible()
    await page.screenshot({ path: 'test-results/project-agent-sidebar-wide.png' })
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(950, 700))
    await expect(roster.getByRole('textbox')).toBeVisible()
    await page.screenshot({ path: 'test-results/project-agent-sidebar-compact.png' })
  } finally {
    fixture.mock.releaseHeldStream()
    await fixture.cleanup()
    project.cleanup()
  }
})
