import { expect, test } from './test'

import * as fs from 'node:fs'
import * as path from 'node:path'

import { type MockBackendFixture, type Sandbox, setupMockBackend, waitForAppReady } from './fixtures'

const LONG_TOOLSET_DESCRIPTION =
  'This retained test capability documents its stable tool contract, input schema, expected output, error behavior, compatibility boundaries, profile ownership, resource lifecycle, and safe execution rules. The complete description is intentionally longer than one hundred eighty characters so the desktop presents it in the secondary technical-details disclosure rather than the concise capability summary.'

function seedLongDescriptionPlugin(sandbox: Sandbox): void {
  const pluginDir = path.join(sandbox.hermesHome, 'plugins', 'e2e-technical-details')
  fs.mkdirSync(pluginDir, { recursive: true })
  fs.writeFileSync(
    path.join(pluginDir, 'plugin.yaml'),
    [
      'name: e2e-technical-details',
      'version: 1.0.0',
      `description: ${JSON.stringify(LONG_TOOLSET_DESCRIPTION)}`,
      'provides_tools:',
      '  - e2e_technical_details_probe',
      ''
    ].join('\n'),
    'utf8'
  )
  fs.writeFileSync(
    path.join(pluginDir, '__init__.py'),
    [
      'def register(ctx):',
      '    ctx.register_tool(',
      '        name="e2e_technical_details_probe",',
      '        toolset="e2e_technical_details",',
      '        schema={"name": "e2e_technical_details_probe", "description": "E2E fixture probe", "parameters": {"type": "object", "properties": {}}},',
      '        handler=lambda **_: {"ok": True},',
      '    )',
      ''
    ].join('\n'),
    'utf8'
  )
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    extraConfig: 'plugins:\n  enabled:\n    - e2e-technical-details',
    seedSandbox: seedLongDescriptionPlugin
  })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('tools is a direct primary navigation destination, and plugins open from it', async ({}, testInfo) => {
  expect(LONG_TOOLSET_DESCRIPTION.length).toBeGreaterThan(180)
  const page = fixture!.page
  const productNav = page.locator('[data-personal-product-nav]')

  await productNav.getByRole('button', { name: '工具', exact: true }).click()
  await expect(productNav.getByRole('button', { name: '工具', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('textbox', { name: '搜索工具集…' })).toBeVisible()
  await expect(page.getByText('正在加载能力…', { exact: true })).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText(/^\d+ 个工具$/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText(/^\d+ tools$/)).toHaveCount(0)
  await expect(page.getByText('Hermes (default)', { exact: true })).toHaveCount(0)
  const browser = page.getByText('浏览器自动化', { exact: true }).first()
  await expect(browser).toBeVisible()
  await browser.click()
  await expect(page.getByRole('heading', { name: '浏览器自动化', exact: true })).toBeVisible()
  await expect(page.getByText('Browser Automation', { exact: true })).toHaveCount(0)
  await expect(page.locator('iframe')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-toolsets.png') })

  // Browser Automation's actual description is concise, so ToolsetDetail's
  // component does not render a technical-details disclosure here.
  await expect(page.locator('[data-toolset-technical-details]')).toHaveCount(0)

  // The isolated fixture plugin supplies a retained toolset with a real long
  // description through the backend API; it never invokes the probe tool.
  const longDescriptionRow = page.locator('#toolset-row-e2e_technical_details')
  await expect(longDescriptionRow).toBeVisible()
  await longDescriptionRow.click()
  const technicalDetails = page.locator('[data-toolset-technical-details]')
  await expect(technicalDetails).toBeVisible()
  await expect(technicalDetails).not.toHaveAttribute('open', '')
  const technicalSummary = technicalDetails.locator('summary')
  const technicalDescription = technicalDetails.locator('p')
  await expect(technicalSummary).toBeVisible()
  await expect(technicalDescription).toHaveText(LONG_TOOLSET_DESCRIPTION)
  await expect(technicalDescription).not.toBeVisible()
  await technicalSummary.press('Enter')
  await expect(technicalDetails).toHaveAttribute('open', '')
  await expect(technicalDescription).toBeVisible()
  await technicalSummary.click()
  await expect(technicalDetails).not.toHaveAttribute('open', '')
  await expect(technicalDescription).not.toBeVisible()

  // Skills remain available inside the capability surface without occupying a
  // permanent primary-nav row.
  await page.getByRole('button', { name: /^技能 \d+$/ }).click()
  await expect(page.locator('[data-skill-overview]').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('switch').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('正在加载能力…', { exact: true })).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText('Hermes (default)', { exact: true })).toHaveCount(0)
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect(page.getByText('它能做什么', { exact: true })).toBeVisible()
  await expect(page.getByText('效率', { exact: true }).first()).toBeVisible()
  const rawInstructions = page.locator('[data-skill-raw-instructions]').first()
  const skillMetadata = page.locator('[data-skill-technical-metadata]').first()
  await expect(rawInstructions).toBeVisible()
  await expect(skillMetadata).toBeVisible()
  await expect(rawInstructions).not.toHaveAttribute('open', '')
  await expect(skillMetadata).not.toHaveAttribute('open', '')

  // Advanced users can still inspect the exact source without making it the
  // default product surface.
  await page.getByText('原始说明', { exact: true }).click()
  await expect(rawInstructions).toHaveAttribute('open', '')
  await expect(rawInstructions.locator('pre')).toBeVisible()
  await page.getByText('原始说明', { exact: true }).click()
  await expect(rawInstructions).not.toHaveAttribute('open', '')
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-skills.png') })

  // Plugins is the Plugins tab of the Tools page; the nav carries only Tools.
  await page.evaluate(() => {
    window.location.hash = '#/skills?tab=plugins'
  })
  await expect(productNav.getByRole('button', { name: '工具', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('columnheader', { name: '桌面', exact: true })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '默认配置 中的智能体', exact: true })).toBeVisible()
  await expect(page.getByText('智能体', { exact: true }).first()).toBeVisible()
  // The Kanban board is retired, so its plugin row is not listed.
  await expect(page.getByText('任务看板', { exact: true })).toHaveCount(0)
  await expect(page.getByText('电台', { exact: true })).toHaveCount(0)
  await expect(
    page.getByText('智能体名册，为每个智能体提供独立对话、定时任务、群聊和智能体间消息。', { exact: true })
  ).toBeVisible()
  await expect(page.getByText('Hermes（默认）', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Hermes (default)', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '打开插件文件夹', exact: true })).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-plugins.png') })
})
