import fs from 'node:fs'
import path from 'node:path'

import { expect, test } from './test'
import { createSandbox, buildAppEnv, launchDesktop } from './fixtures'

test.describe('flagged connector onboarding', () => {
  test('a fresh flagged session starts the guided chat without offering connectors', async () => {
    const sandbox = createSandbox('connectors-ui')
    // The copy asserted below is English. Pin the display language so it holds on
    // the zh-CN runner, as the other English-copy specs do. No provider is written,
    // so the guided onboarding still starts from its unconfigured state.
    fs.writeFileSync(path.join(sandbox.hermesHome, 'config.yaml'), 'display:\n  language: en\n', 'utf8')
    const { app, page } = await launchDesktop(buildAppEnv(sandbox, {
      STARDUST_GUEST_ONBOARDING: '1'
    }))

    try {
      // The connector offer now appears only once a first build asks for apps
      // (a manage_connections tool call), so a fresh launch lands in the guided chat.
      await expect(page.getByRole('heading', { name: 'What should we work on?' })).toBeVisible({ timeout: 90_000 })
      await expect(page.getByRole('button', { name: 'Start with a message', exact: true })).toBeVisible()
      await expect(page.locator('[data-connector-offer]')).toHaveCount(0)
    } finally {
      await app.close().catch(() => undefined)
      sandbox.cleanup()
    }
  })

  test('keeps the default surface free of connector authorization controls', async () => {
    const sandbox = createSandbox('connectors-off')
    const { app, page } = await launchDesktop(buildAppEnv(sandbox))

    try {
      await expect(page.locator('[data-connector-offer]')).toHaveCount(0)
    } finally {
      await app.close().catch(() => undefined)
      sandbox.cleanup()
    }
  })
})
