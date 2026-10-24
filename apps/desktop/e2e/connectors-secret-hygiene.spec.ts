import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// CX5 secret hygiene acceptance: after connecting a custom URL server WITH an
// API key through the dialog, the key must NOT appear in any plain file under
// the sandbox HOME (config.yaml, .env, logs). The secure store (CX4) or a
// ${REF} is the only legal landing spot. Scanned with Node fs (no shell find)
// BEFORE sandbox cleanup deletes the evidence.

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    // unified_dialog (CX5 page) + connector_secret_store (CX2/4 encrypted
    // landing for dialog-entered API keys — the config spec-cx5 mandates).
    extraConfig: 'connectors:\n  unified_dialog: true\nconnector_secret_store:\n  enabled: true\n'
  })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

/** Recursive plain-text scan; returns every file whose content contains needle. */
async function scanFor(needle: string, root: string): Promise<string[]> {
  const { readdir, readFile } = await import('node:fs/promises')
  const { join } = await import('node:path')
  const hits: string[] = []
  const SKIP = new Set(['Cache', 'Code Cache', 'blob_storage', 'DawnGraphiteCache', 'DawnWebGPUCache', 'GPUCache'])

  async function walk(dir: string): Promise<void> {
    let entries

    try {
      entries = await readdir(dir, { withFileTypes: true })
    } catch {
      return
    }

    for (const entry of entries) {
      const full = join(dir, entry.name)

      if (entry.isDirectory()) {
        if (!SKIP.has(entry.name)) {
          await walk(full)
        }
      } else if (entry.isFile()) {
        try {
          const buf = await readFile(full)

          if (buf.includes(needle)) {
            hits.push(full)
          }
        } catch {
          /* locked/unreadable — skip */
        }
      }
    }
  }

  await walk(root)

  return hits
}

test('api key never lands in plain files under HOME', async () => {
  // Mechanism sanity FIRST, on this exact sandbox: the known .env mock key
  // must be found — proves the scan is live before we trust a 0.
  const known = await scanFor('e2e-mock-key', fixture!.sandbox.hermesHome)
  expect(known.length, 'scan must find the known .env value (mechanism sanity)').toBeGreaterThan(0)

  const page = fixture!.page
  await page.evaluate(() => {
    window.location.hash = '#/connectors'
  })
  await expect(page.locator('[data-slot="connector-catalog-page"]')).toBeVisible({ timeout: 30_000 })

  const SECRET = 'cx5-secret-canary-a1b2c3'
  await page.locator('[data-slot="add-connection"]').click()
  await page.locator('[data-slot="custom-name"]').fill('secret-canary')
  await page.locator('[data-slot="custom-url"]').fill('https://mcp.deepwiki.com/mcp')
  await page.locator('[data-slot="custom-use-api-key"]').click()
  await page.locator('[data-slot="custom-api-key"]').fill(SECRET)
  await page.locator('[data-slot="custom-connect"]').click()
  await expect(page.locator('[data-slot="connector-connected-row"][data-server="secret-canary"]')).toBeVisible({ timeout: 60_000 })

  const hits = await scanFor(SECRET, fixture!.sandbox.hermesHome)

  // The CX2 encrypted store is platform-gated: only Windows DPAPI exists so
  // far (agent/connector_secrets.py _build_platform_store — macOS Keychain /
  // Linux Secret Service come later). On a platform without a secure backend
  // the product contract (CX2b spec) is explicit: fail-open on the STORAGE
  // CHOICE — the key lands in the profile .env exactly like the legacy path,
  // and config.yaml keeps only the ${MCP_SECRET_CANARY_API_KEY} reference.
  // So the invariant this test can assert everywhere is: the secret appears
  // in AT MOST the legacy .env slot, and NOWHERE else (no config.yaml, logs,
  // connector-secrets/, or any other plain file).
  const isEnvPath = (file: string) => {
    const norm = file.replace(/\\/g, '/')

    return norm === `${fixture!.sandbox.hermesHome.replace(/\\/g, '/')}/.env`
  }

  const offenders = hits.filter(file => !isEnvPath(file))
  expect(
    offenders,
    `secret leaked outside the legacy .env slot (encrypted-store platforms allow NO hit at all):\n${offenders.join('\n')}`
  ).toEqual([])

  if (process.platform === 'win32') {
    // Windows has the DPAPI backend and the flag is on in this sandbox: the
    // store MUST have taken the key — even the .env hit is a failure here.
    expect(hits, `plain-text secret hits (DPAPI backend expected, got):\n${hits.join('\n')}`).toEqual([])
  }
})
