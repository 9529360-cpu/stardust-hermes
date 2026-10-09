/**
 * Real desktop regression for approvals.mode: off. The mocked model asks the
 * real backend to write a protected instruction file in the E2E sandbox; the
 * write must finish without opening an approval card or leaving the turn stuck.
 */

import * as fs from 'node:fs'
import * as path from 'node:path'

import { startMockServer, VERIFICATION_STOP_TRIGGER } from '../../../tests-js/scripts/mock-server'

import { buildAppEnv, createSandbox, launchDesktop, waitForAppReady, writeEnvFile, writeMockProviderConfig } from './fixtures'
import { expect, test } from './test'

// Playwright requires its fixture argument to use object destructuring.
// eslint-disable-next-line no-empty-pattern
test('approval mode off completes protected writes without showing a prompt', async ({}, testInfo) => {
  const sandbox = createSandbox('approval-mode-off')
  const projectRoot = path.join(sandbox.root, 'project')
  const targetPath = path.join(projectRoot, 'AGENTS.md')
  fs.mkdirSync(projectRoot, { recursive: true })

  const mock = await startMockServer({ verificationWritePath: targetPath })
  let app: Awaited<ReturnType<typeof launchDesktop>>['app'] | null = null

  try {
    writeMockProviderConfig(
      sandbox.hermesHome,
      mock.url,
      undefined,
      'auxiliary:\n  title_generation:\n    enabled: false\napprovals:\n  mode: "manual"',
    )
    writeEnvFile(sandbox.hermesHome)

    const launched = await launchDesktop(buildAppEnv(sandbox))
    app = launched.app
    const { page } = launched
    await waitForAppReady({ app, page, mock, mockUrl: mock.url, sandbox, cleanup: async () => undefined }, 120_000)

    const approvalModeButton = page.locator('[data-statusbar-item="approval-mode"]')
    await approvalModeButton.waitFor({ state: 'visible', timeout: 15_000 })
    await approvalModeButton.click()
    await page.getByRole('menuitemradio', { name: /Off|关闭/ }).click()
    await expect(approvalModeButton).toContainText(/Off|关闭/)

    const pageErrors: string[] = []
    page.on('pageerror', error => pageErrors.push(error.message))
    const composer = page.locator('[contenteditable="true"]').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.click()
    await composer.fill(VERIFICATION_STOP_TRIGGER)
    await page.keyboard.press('Enter')

    const transcript = page.locator('[data-slot="aui_thread-viewport"]')
    await expect(transcript).toContainText('The code edit is complete.', { timeout: 60_000 })
    await expect.poll(() => fs.existsSync(targetPath), { timeout: 30_000 }).toBe(true)
    expect(fs.readFileSync(targetPath, 'utf8')).toContain('changed_by_e2e')
    await expect(
      page.locator(
        '[data-slot="tool-approval-inline"], [data-slot="tool-approval-actions"], [data-slot="tool-approval-fallback"]',
      ),
    ).toHaveCount(0)
    expect(pageErrors).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('approval-mode-off-protected-write.png') })
  } finally {
    await app?.close().catch(() => undefined)
    await mock.close()

    const logsDir = path.join(sandbox.hermesHome, 'logs')

    if (fs.existsSync(logsDir)) {
      const logFiles = fs.readdirSync(logsDir).filter(name => /\.log$/i.test(name))
      const relevantLogs = logFiles.map(name => `${name}\n${fs.readFileSync(path.join(logsDir, name), 'utf8')}`)
      await testInfo.attach('isolated-backend-logs.txt', {
        body: relevantLogs.join('\n\n') || 'No backend log files were emitted.',
        contentType: 'text/plain',
      })
    }

    sandbox.cleanup()
  }
})
