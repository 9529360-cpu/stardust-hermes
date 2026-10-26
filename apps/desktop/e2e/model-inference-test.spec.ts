import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'
import type { MockServerOptions } from '../../../tests-js/scripts/mock-server'

const PROBE_PROMPT = 'Reply with exactly: Stardust model test OK'
const FAKE_AUTH_ECHO = 'fake-e2e-provider-secret-401'
const FAKE_RATE_ECHO = 'fake-e2e-provider-secret-429'

let fixture: MockBackendFixture | null = null
const modelTestCancelStatuses: string[] = []
const modelTestResultStatuses: string[] = []
const modelTestResultKinds: Array<string | null> = []
const modelTestResultErrors: string[] = []
const modelTestCancelSupport: boolean[] = []
const modelTestInferenceOk: Array<boolean | null | undefined> = []
const setupRuntimeCheckParams: Array<Record<string, unknown>> = []
const setupRuntimeCheckResults: Array<Record<string, unknown>> = []

function attachModelTestRpcCapture(target: MockBackendFixture) {
  target.page.on('websocket', socket => {
    const runtimeCheckRequestIds = new Set<string>()

    socket.on('framesent', frame => {
      try {
        const parsed = JSON.parse(String(frame.payload)) as unknown
        const entries = Array.isArray(parsed) ? parsed : [parsed]
        for (const entry of entries) {
          if (!entry || typeof entry !== 'object' || !('method' in entry) ||
              entry.method !== 'setup.runtime_check' || !('id' in entry)) {
            continue
          }
          const id = String(entry.id)
          runtimeCheckRequestIds.add(id)
          setupRuntimeCheckParams.push(
            'params' in entry && entry.params && typeof entry.params === 'object'
              ? entry.params as Record<string, unknown>
              : {}
          )
        }
      } catch {
        // Ignore unrelated or non-JSON WebSocket frames.
      }
    })

    socket.on('framereceived', frame => {
      try {
        const parsed = JSON.parse(String(frame.payload)) as unknown
        const entries = Array.isArray(parsed) ? parsed : [parsed]
        for (const entry of entries) {
          if (!entry || typeof entry !== 'object' || !('result' in entry)) {
            continue
          }
          const result = entry.result
          if ('id' in entry && runtimeCheckRequestIds.delete(String(entry.id)) &&
              result && typeof result === 'object') {
            setupRuntimeCheckResults.push(result as Record<string, unknown>)
          }
          if (result && typeof result === 'object' && 'status' in result && 'inference_ok' in result &&
              typeof result.status === 'string' && 'cancel_supported' in result &&
              typeof result.cancel_supported === 'boolean') {
            modelTestResultStatuses.push(result.status)
            modelTestInferenceOk.push(
              typeof result.inference_ok === 'boolean' || result.inference_ok === null
                ? result.inference_ok as boolean | null
                : undefined
            )
            modelTestCancelSupport.push(result.cancel_supported)
            modelTestResultKinds.push(
              'error_kind' in result && typeof result.error_kind === 'string' ? result.error_kind : null
            )
            modelTestResultErrors.push('error' in result && typeof result.error === 'string' ? result.error : '')
          }
          if (result && typeof result === 'object' && 'status' in result && 'message' in result &&
              typeof result.status === 'string' && typeof result.message === 'string' &&
              result.message.toLowerCase().includes('model test')) {
            modelTestCancelStatuses.push(result.status)
          }
        }
      } catch {
        // Ignore unrelated or non-JSON WebSocket frames.
      }
    })
  })
}

async function startFixture(mockServer: MockServerOptions = {}): Promise<MockBackendFixture> {
  modelTestCancelStatuses.length = 0
  modelTestResultStatuses.length = 0
  modelTestResultKinds.length = 0
  modelTestResultErrors.length = 0
  modelTestCancelSupport.length = 0
  modelTestInferenceOk.length = 0
  setupRuntimeCheckParams.length = 0
  setupRuntimeCheckResults.length = 0
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: zh', mockServer })
  attachModelTestRpcCapture(fixture)
  await waitForAppReady(fixture, 120_000)
  return fixture
}

async function restartFixture(mockServer: MockServerOptions = {}): Promise<MockBackendFixture> {
  if (fixture) {
    fixture.mock.releaseHeldStream()
    await fixture.cleanup()
    fixture = null
  }
  return startFixture(mockServer)
}

async function openModelSettingsWithSession() {
  const { mock, page } = fixture!
  const productNav = page.locator('[data-personal-product-nav]')
  const composer = page.locator('[contenteditable="true"]').first()

  // Create a real live session first; its normal chat request establishes the baseline count.
  await productNav.getByRole('button', { name: '新建对话', exact: true }).click()
  await composer.click()
  await composer.type('为模型测试建立活动会话', { delay: 5 })
  await page.keyboard.press('Enter')
  await expect(page.getByText('mock inference server', { exact: false })).toBeVisible({ timeout: 60_000 })
  const baseline = mock.receivedCompletions.length
  expect(baseline).toBeGreaterThan(0)

  await page.getByRole('button', { name: '打开设置' }).click()
  await page.getByRole('complementary').getByRole('button', { name: '模型服务' }).click()
  const testButton = page.getByRole('button', { name: '测试所选模型', exact: true })
  await expect(testButton).toBeEnabled()
  await page.bringToFront()
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))

  return { baseline, testButton }
}

async function expectFakeSecretAbsent(secret: string) {
  const { page, sandbox } = fixture!
  await expect(page.locator('body')).not.toContainText(secret)
  expect(modelTestResultErrors.join('\n')).not.toContain(secret)

  const agentLog = join(sandbox.hermesHome, 'logs', 'agent.log')
  await expect.poll(() => existsSync(agentLog)).toBe(true)
  expect(readFileSync(agentLog, 'utf8')).not.toContain(secret)
}

test.beforeAll(async () => {
  await startFixture({ holdFirstCompletionContaining: PROBE_PROMPT })
})

test.afterEach(async () => {
  fixture?.mock.releaseHeldStream()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('only an explicit click sends one bounded inference request and confirmed stop is reported honestly', async () => {
  const { mock, page } = fixture!
  const { baseline, testButton } = await openModelSettingsWithSession()

  // Advance renderer timers through one status refresh interval. Readiness and model options
  // remain non-inference paths, so the provider sees no extra completion POST.
  await page.clock.install()
  await page.clock.runFor(60_000)
  expect(mock.receivedCompletions.length).toBe(baseline)

  await testButton.click()
  await mock.waitForHeldCompletion()
  await expect.poll(() => mock.receivedCompletions.length).toBe(baseline + 1)

  const probe = mock.receivedCompletions[baseline]
  expect(probe.path).toBe('/v1/chat/completions')
  expect(probe.body.model).toBe('mock-model')
  expect(probe.body.stream).toBe(false)
  expect(probe.body.max_tokens ?? probe.body.max_completion_tokens).toBeLessThanOrEqual(16)
  expect(probe.body.tools).toBeUndefined()
  expect(probe.body.messages).toHaveLength(2)
  expect((probe.body.messages as Array<{ role: string; content: string }>).map(message => message.role)).toEqual(['system', 'user'])
  expect((probe.body.messages as Array<{ role: string; content: string }>)[1].content).toBe(PROBE_PROMPT)

  await page.getByRole('button', { name: '停止等待', exact: true }).click()
  await expect.poll(() => modelTestCancelStatuses.length).toBeGreaterThan(0)
  expect(modelTestCancelStatuses.at(-1)).toBe('cancel_requested')
  await expect.poll(() => modelTestResultStatuses.at(-1)).toBe('canceled')
  expect(modelTestCancelSupport.at(-1)).toBe(true)
  await expect(page.getByRole('status')).toContainText('模型测试已停止；如果请求已经发出，提供方可能仍会完成处理。')
  expect(mock.receivedCompletions.length).toBe(baseline + 1)
})

test('models reachability stays ready when the selected inference request returns 401', async () => {
  const { mock, page } = await restartFixture({
    failCompletionsContaining: {
      prompt: PROBE_PROMPT,
      count: 1,
      status: 401,
      errorMessage: FAKE_AUTH_ECHO
    }
  })
  const { baseline, testButton } = await openModelSettingsWithSession()

  await expect.poll(() => setupRuntimeCheckResults.some(result => result.ok === true)).toBe(true)
  const runtimeCheck = setupRuntimeCheckResults.find(result => result.ok === true)!
  expect(setupRuntimeCheckParams.some(params => params.live === true)).toBe(false)
  expect(runtimeCheck.live_ok === undefined || runtimeCheck.live_ok === null).toBe(true)
  expect(runtimeCheck.inference_ok === undefined || runtimeCheck.inference_ok === null).toBe(true)

  await page.getByRole('button', { name: '添加模型服务', exact: true }).click()
  await page.getByLabel('服务地址').fill(`${fixture!.mockUrl}/v1`)
  const modelListCount = mock.receivedModelListResponses.length
  await page.getByRole('button', { name: '测试连接', exact: true }).click()
  await expect.poll(() => mock.receivedModelListResponses.length).toBe(modelListCount + 1)
  expect(mock.receivedModelListResponses.at(-1)).toBe(200)
  const reachableText = page.getByText(/连接正常.*找到.*模型/)
  await expect(reachableText).toHaveCount(2)
  await expect(reachableText.first()).toBeVisible()
  await expect(reachableText.last()).toBeVisible()

  await testButton.click()
  await expect.poll(() => mock.receivedCompletions.length).toBe(baseline + 1)
  await expect.poll(() => modelTestResultStatuses.at(-1)).toBe('failed')
  expect(modelTestResultKinds.at(-1)).toBe('auth')
  expect(modelTestInferenceOk.at(-1)).toBe(false)
  await expect(page.locator('[data-slot="model-inference-test"] [role="status"]')).toContainText(
    '提供方拒绝了身份验证。'
  )
  await expect(reachableText).toHaveCount(2)
  await expect(reachableText.first()).toBeVisible()
  await expect(reachableText.last()).toBeVisible()
  await expectFakeSecretAbsent(FAKE_AUTH_ECHO)
  expect(mock.receivedCompletions.length).toBe(baseline + 1)
})

test('429 failures stay classified and do not echo a response secret to UI, RPC or logs', async () => {
  const { mock, page } = await restartFixture({
    failCompletionsContaining: {
      prompt: PROBE_PROMPT,
      count: 1,
      status: 429,
      errorMessage: FAKE_RATE_ECHO
    }
  })
  const { baseline, testButton } = await openModelSettingsWithSession()

  await testButton.click()
  await expect.poll(() => mock.receivedCompletions.length).toBe(baseline + 1)
  await expect.poll(() => modelTestResultStatuses.at(-1)).toBe('failed')
  expect(modelTestResultKinds.at(-1)).toBe('rate_limit')
  await expect(page.getByRole('status')).toContainText('已达到提供方速率限制。')
  await expectFakeSecretAbsent(FAKE_RATE_ECHO)
  expect(mock.receivedCompletions.length).toBe(baseline + 1)
})

test('a held local completion reaches the bounded timeout without retry', async () => {
  const { mock, page } = await restartFixture({
    holdFirstCompletionContaining: PROBE_PROMPT
  })
  const { baseline, testButton } = await openModelSettingsWithSession()

  await testButton.click()
  await mock.waitForHeldCompletion()
  await expect.poll(() => mock.receivedCompletions.length).toBe(baseline + 1)
  await expect(page.getByRole('status')).toContainText('此面板已停止等待；服务端请求可能持续到超时。', {
    timeout: 30_000
  })
  await expect.poll(() => modelTestResultStatuses.at(-1)).toBe('timed_out')
  expect(modelTestResultKinds.at(-1)).toBe('timeout')
  expect(modelTestCancelSupport.at(-1)).toBe(false)
  expect(mock.receivedCompletions.length).toBe(baseline + 1)
})

test('applying another provider while a probe is held discards the old result', async () => {
  const { mock, page } = await restartFixture({
    holdFirstCompletionContaining: PROBE_PROMPT
  })
  const { baseline, testButton } = await openModelSettingsWithSession()

  await testButton.click()
  await mock.waitForHeldCompletion()
  await expect.poll(() => mock.receivedCompletions.length).toBe(baseline + 1)

  await page.getByRole('button', { name: '添加模型服务', exact: true }).click()
  await page.getByLabel('服务名称').fill('Second test service')
  await page.getByLabel('服务地址').fill(`${fixture!.mockUrl}/v1`)
  await page.getByLabel('默认模型').fill('mock-model-second')
  await page.getByRole('checkbox', { name: '设为新对话默认服务' }).check()
  await page.getByRole('button', { name: '保存服务', exact: true }).click()

  await expect(page.getByText('mock-model-second', { exact: true })).toBeVisible()
  await expect.poll(() => modelTestCancelStatuses.at(-1)).toBe('cancel_requested')
  await expect.poll(() => modelTestResultStatuses.at(-1)).toBe('canceled')
  await expect(page.locator('[data-slot="model-inference-test"] [role="status"]')).toHaveCount(0)

  mock.releaseHeldStream()
  await expect.poll(() => mock.receivedCompletions.length).toBe(baseline + 1)
  await expect(page.locator('[data-slot="model-inference-test"] [role="status"]')).toHaveCount(0)
})
