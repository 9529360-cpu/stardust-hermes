import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { buildAppEnv, launchDesktop, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { collectErrorBanners, expect, test } from './test'

let evidence: string
const python = process.env.STARDUST_DESKTOP_PYTHON!
let fixture: MockBackendFixture | undefined
let backendEnv: Record<string, string>

function seed(sandbox: Parameters<typeof buildAppEnv>[0]) {
  backendEnv = {
    ...buildAppEnv(sandbox),
    HERMES_HOME: sandbox.hermesHome,
    HERMES_SHARED_AUTH_DIR: path.join(sandbox.hermesHome, 'shared'),
    PYTHONIOENCODING: 'utf-8'
  }

  const output = execFileSync(
    python,
    [
      '-c',
      `
from cron.jobs import create_job
from cron.executions import create_execution,finish_execution
for label,success in [('完成',True),('失败',False)]:
    job=create_job(prompt='隔离验收任务',schedule='every 7d',name='持久化自动化-'+label,paused=True)
    execution=create_execution(job['id'],source='manual')
    assert finish_execution(execution['id'],success=success,error=None if success else '隔离验收失败证据')
print('Seeded two isolated scheduled jobs and runs')
`
    ],
    { encoding: 'utf8', env: backendEnv }
  )

  fs.writeFileSync(path.join(evidence, 'seed-output.txt'), output)
}

// Playwright requires destructuring even when this hook uses no fixtures.
// eslint-disable-next-line no-empty-pattern
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus && fixture) {
    await fixture.page.screenshot({ path: path.join(evidence, 'failure.png') }).catch(() => undefined)

    const text = await fixture.page
      .locator('body')
      .innerText()
      .catch(() => '')

    fs.writeFileSync(path.join(evidence, 'failure-body.txt'), text)
  }
})

test.afterAll(async () => {
  await fixture?.app.close().catch(() => undefined)
  await fixture?.mock.close().catch(() => undefined)
  fixture?.sandbox.cleanup()
})

// eslint-disable-next-line no-empty-pattern
test('scheduled jobs and their run history survive two cold desktop restarts', async ({}, testInfo) => {
  test.setTimeout(240_000)
  evidence = testInfo.outputPath('evidence')
  fs.mkdirSync(evidence, { recursive: true })
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: zh', seedSandbox: seed })

  for (let round = 0; round < 3; round++) {
    await waitForAppReady(fixture, 100_000)
    const { page } = fixture
    await page.getByRole('button', { name: '定时任务', exact: true }).click()

    const scheduledJobs = page.locator('[data-cron-page]')
    await expect(scheduledJobs).toBeVisible()

    for (const name of ['持久化自动化-完成', '持久化自动化-失败']) {
      await expect(scheduledJobs.locator('[data-panel-row]').filter({ hasText: name })).toBeVisible({ timeout: 20_000 })
    }

    await page.screenshot({ path: path.join(evidence, `scheduled-jobs-round-${round}.png`) })
    expect(await collectErrorBanners(page)).toEqual([])

    if (round < 2) {
      await fixture.app.close()
      const restarted = await launchDesktop(buildAppEnv(fixture.sandbox))
      fixture = { ...fixture, ...restarted }
    }
  }

  await fixture.app.close()
})
