import { execFileSync } from 'node:child_process'
import * as path from 'node:path'

import { type MockBackendFixture, type Sandbox, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const DESKTOP_ROOT = path.resolve(import.meta.dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')
const PYTHON = path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')

// Two jobs, each with one finished run, written through the backend's own cron API. The desktop then
// reads exactly what scheduled runs leave behind. A run that is still going needs a live owner process,
// so that state is covered by the unit tests instead.
const SEED = `
from cron import executions
from cron.jobs import create_job

morning = create_job(prompt='Summarize my inbox', schedule='weekdays at 8am', name='Morning summary')
weekly = create_job(prompt='Write the weekly report', schedule='every monday 9am', name='Weekly report')
done = executions.create_execution(morning['id'], source='scheduler')
executions.finish_execution(done['id'], success=True, delivery_outcome='delivered')
broken = executions.create_execution(weekly['id'], source='scheduler')
executions.finish_execution(broken['id'], success=False, error='provider unavailable', delivery_outcome='failed')
`

function seedRuns(sandbox: Sandbox): void {
  execFileSync(PYTHON, ['-c', SEED], {
    cwd: REPO_ROOT,
    env: { ...process.env, HERMES_HOME: sandbox.hermesHome, PYTHONIOENCODING: 'utf-8' },
    stdio: 'pipe',
  })
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({ seedSandbox: seedRuns })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

// eslint-disable-next-line no-empty-pattern
test('recent scheduled runs show what finished and open the job they belong to', async ({}, testInfo) => {
  const page = fixture!.page

  await page.evaluate(() => {
    window.location.hash = '#/cron'
  })

  await expect(page.getByText('最近运行', { exact: true })).toBeVisible({ timeout: 30_000 })

  const failedRun = page.locator('[data-panel-row^="run-"]').filter({ hasText: 'Weekly report' })

  await failedRun.click()

  await expect(page.getByText('provider unavailable')).toBeVisible()
  await expect(page.getByText('出错原因', { exact: true })).toBeVisible()
  await expect(page.getByText('没能送达', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('tasks-failed-run.png') })

  await page.getByRole('button', { name: '查看这个任务', exact: true }).click()

  await expect(page.getByText('provider unavailable')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('tasks-job-after-open.png') })
})
