import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

import { BATCH_CLARIFY_QUESTIONS, BATCH_CLARIFY_TRIGGER } from '../../../tests-js/scripts/mock-server'
import { buildAppEnv, launchDesktop, setupMockBackend, waitForAppReady, type MockBackendFixture } from './fixtures'
import { collectErrorBanners, expect, test } from './test'

const evidence = process.env.TASK_CENTER_SCREENSHOT_DIR!
const python = process.env.STARDUST_DESKTOP_PYTHON!
let fixture: MockBackendFixture | undefined

function seed(sandbox: { hermesHome: string }) {
  process.env.HERMES_HOME = sandbox.hermesHome
  process.env.HERMES_SHARED_AUTH_DIR = path.join(sandbox.hermesHome, 'shared')
  const output = execFileSync(python, ['-c', `
import json,time
from hermes_state import SessionDB
from hermes_cli.goals import GoalState,save_goal
from cron.jobs import create_job
from cron.executions import create_execution,finish_execution
from tools.async_delegation import _persist_dispatch
now=time.time()-60
db=SessionDB()
for label,status,verdict in [('running','active','continue'),('done','done','done'),('paused','paused',None),('blocked','paused','blocked')]:
    sid='acceptance-'+label
    db.create_session(sid,'desktop')
    db.set_session_title(sid,'验收会话-'+label)
    db.append_message(sid,'user','隔离验收持久化会话-'+label)
    db.append_message(sid,'assistant','隔离验收历史内容-'+label)
    save_goal(sid,GoalState(goal='持久化目标-'+label,status=status,last_verdict=verdict,created_at=now,last_turn_at=now))
for label,success in [('完成',True),('失败',False)]:
    job=create_job(prompt='隔离验收任务',schedule='every 7d',name='持久化自动化-'+label,paused=True)
    execution=create_execution(job['id'],source='manual')
    assert finish_execution(execution['id'],success=success,error=None if success else '隔离验收失败证据')
_persist_dispatch({'delegation_id':'acceptance-owner-lost','goal':'持久化委派-owner-lost','dispatched_at':now,'parent_session_id':'acceptance-running'})
db.close()
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
  fs.writeFileSync(path.join(evidence, 'seed-output.txt'), output)
  baseline('backend-before-launch')
}

function baseline(name: string) {
  const output = execFileSync(python, ['-c', 'import json; from tools.task_projection import list_task_rows; print(json.dumps(list_task_rows(limit=50),ensure_ascii=False))'], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
  fs.writeFileSync(path.join(evidence, name + '.json'), output)
  return JSON.parse(output)
}

test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus && fixture) {
    await fixture.page.screenshot({ path: path.join(evidence, 'failure.png') }).catch(() => undefined)
    const text = await fixture.page.locator('body').innerText().catch(() => '')
    fs.writeFileSync(path.join(evidence, 'failure-body.txt'), text)
  }
})

test.afterAll(async () => {
  await fixture?.app.close().catch(() => undefined)
  await fixture?.mock.close().catch(() => undefined)
  fixture?.sandbox.cleanup()
})

test('持久化任务在真实桌面交互及两次冷重启后恢复', async () => {
  test.setTimeout(240_000)
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: zh', seedSandbox: seed })
  const initial = baseline('backend-baseline')
  expect(initial.degraded).toEqual([])
  expect(initial.tasks.find((row: any) => row.title === '持久化目标-running')?.state).toBe('running')
  expect(initial.tasks.find((row: any) => row.title === '持久化目标-blocked')?.state).toBe('blocked')
  expect(initial.tasks.find((row: any) => row.title === '持久化委派-owner-lost')?.state).toBe('interrupted')

  for (let round = 0; round < 3; round++) {
    await waitForAppReady(fixture, 100_000)
    const { page } = fixture
    await page.getByRole('button', { name: '任务', exact: true }).click()
    const center = page.locator('[data-task-center]')
    for (const [group, title] of [['running', '持久化目标-running'], ['attention', '持久化目标-blocked'], ['upcoming', '持久化目标-paused'], ['recent', '持久化目标-done'], ['attention', '持久化委派-owner-lost']] as const) {
      await expect(page.locator(`[data-task-group="${group}"]`).getByRole('button').filter({ hasText: title })).toBeVisible({ timeout: 20_000 })
    }
    for (const status of ['执行中', '受阻', '已暂停', '已完成', '已中断', '失败']) {
      await expect(center.getByRole('button').filter({ hasText: status }).first()).toBeVisible()
    }
    await expect(page.locator('[data-task-group="attention"]').getByRole('button').filter({ hasText: '持久化自动化-失败' }).first()).toBeVisible()
    await page.screenshot({ path: path.join(evidence, `persistent-round-${round}.png`) })
    await page.locator('[data-task-group="running"]').getByRole('button').filter({ hasText: '持久化目标-running' }).click()
    await expect(center).toHaveCount(0)
    await expect(page.getByText('验收会话-running', { exact: true }).first()).toBeVisible()
    if (round === 0) {
      await page.locator('[data-personal-product-nav]').getByRole('button', { name: '新建对话', exact: true }).click()
      const composer = page.locator('[contenteditable="true"]').first()
      await composer.fill(BATCH_CLARIFY_TRIGGER)
      await composer.press('Enter')
      const form = page.locator('form[data-clarify-batch]').first()
      await expect(form).toBeVisible({ timeout: 60_000 })
      await page.getByRole('button', { name: '任务', exact: true }).click()
      const clarify = page.locator('[data-task-group="attention"]').getByRole('button').filter({ hasText: BATCH_CLARIFY_QUESTIONS[0].question }).first()
      await expect(clarify).toBeVisible()
      await page.screenshot({ path: path.join(evidence, 'persistent-live-clarify.png') })
      await clarify.click()
      await form.getByRole('button', { name: /Coffee/ }).click()
      await form.getByRole('button', { name: /Morning/ }).click()
      await form.locator('button[type="submit"]').click()
      await expect(page.locator('[data-clarify-settled]').first()).toBeVisible({ timeout: 30_000 })
      await page.getByRole('button', { name: '任务', exact: true }).click()
      await expect(clarify).toHaveCount(0)
    }
    expect(await collectErrorBanners(page)).toEqual([])
    if (round < 2) {
      await fixture.app.close()
      const restarted = await launchDesktop(buildAppEnv(fixture.sandbox))
      fixture = { ...fixture, ...restarted }
    }
  }
  baseline('backend-after-two-restarts')
  await fixture.app.close()
})
