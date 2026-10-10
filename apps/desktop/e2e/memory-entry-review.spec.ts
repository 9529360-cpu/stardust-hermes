import * as fs from 'node:fs'
import * as path from 'node:path'

import { startMockServer } from '../../../tests-js/scripts/mock-server'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { expect, test } from './test'

test('reviews and removes one built-in memory entry in an isolated profile', async (_fixtures, testInfo) => {
  const taskTemp = process.env.STARDUST_TASK_TEMP

  if (!taskTemp) {throw new Error('STARDUST_TASK_TEMP must point to the D-drive task temp directory')}
  process.env.TEMP = taskTemp
  process.env.TMP = taskTemp
  process.env.TMPDIR = taskTemp

  const sandbox = createSandbox('memory-entry-review')
  const memoryPath = path.join(sandbox.hermesHome, 'memories', 'MEMORY.md')
  fs.mkdirSync(path.dirname(memoryPath), { recursive: true })
  fs.writeFileSync(memoryPath, 'Likes tea\n§\nLives in Berlin', 'utf8')
  const mock = await startMockServer()
  let app: Awaited<ReturnType<typeof launchDesktop>>['app'] | null = null

  try {
    writeMockProviderConfig(sandbox.hermesHome, mock.url)
    writeEnvFile(sandbox.hermesHome)
    const launched = await launchDesktop(buildAppEnv(sandbox))
    app = launched.app
    const { page } = launched
    await waitForAppReady({ app, page, mock, mockUrl: mock.url, sandbox, cleanup: async () => undefined }, 120_000)

    await page.evaluate(() => {
      window.location.hash = '/command-center?section=maintenance'
    })
    await page
      .getByRole('button', { name: /View entries|查看条目/ })
      .first()
      .click()
    await expect(page.getByText('Likes tea', { exact: true })).toBeVisible()
    await expect(page.getByText('Lives in Berlin', { exact: true })).toBeVisible()

    const removeButtons = page.getByRole('button', { name: /Remove entry|删除条目/ })
    await removeButtons.first().click()
    const confirmDialog = page.getByRole('dialog')
    await expect(confirmDialog).toContainText('Likes tea')
    await confirmDialog.getByRole('button', { name: /Cancel|キャンセル|取消|继续/ }).click()
    await expect.poll(() => fs.readFileSync(memoryPath, 'utf8')).toBe('Likes tea\n§\nLives in Berlin')

    await removeButtons.first().click()
    const removeDialog = page.getByRole('dialog')
    await expect(removeDialog).toContainText('Likes tea')
    await removeDialog.getByRole('button', { name: /Confirm|Delete|确认|削除|移除/ }).click()
    await expect.poll(() => fs.readFileSync(memoryPath, 'utf8')).toBe('Lives in Berlin')
    await expect(page.getByText('Likes tea', { exact: true })).toHaveCount(0)
    await expect(page.getByText('Lives in Berlin', { exact: true })).toBeVisible()

    const freshDialog = page.getByRole('dialog')
    await expect(freshDialog).toContainText(/fresh chat|新对话/)
    await freshDialog.getByRole('button', { name: /Keep current chat|继续当前聊天|继续/ }).click()
    await app.close()
    app = null
    const relaunched = await launchDesktop(buildAppEnv(sandbox))
    app = relaunched.app
    const reopenedPage = relaunched.page
    await waitForAppReady(
      { app, page: reopenedPage, mock, mockUrl: mock.url, sandbox, cleanup: async () => undefined },
      120_000
    )
    await reopenedPage.evaluate(() => {
      window.location.hash = '/command-center?section=maintenance'
    })
    await reopenedPage
      .getByRole('button', { name: /View entries|查看条目/ })
      .first()
      .click()
    await expect(reopenedPage.getByText('Likes tea', { exact: true })).toHaveCount(0)
    await expect(reopenedPage.getByText('Lives in Berlin', { exact: true })).toBeVisible()
    await reopenedPage.screenshot({ path: testInfo.outputPath('memory-entry-review.png') })
  } finally {
    await app?.close().catch(() => undefined)
    await mock.close()
    sandbox.cleanup()
  }
})
