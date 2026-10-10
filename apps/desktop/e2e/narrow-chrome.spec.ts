/**
 * Narrow chrome in the real window. Below the sidebar breakpoint the sessions
 * overlay opens over the layout, and it must clear the titlebar's own controls
 * (the sidebar toggle at the top left) at every narrow width: its zone tabs and
 * its brand title stay fully visible, and the toggle stays the thing a click
 * lands on. The status bar's approval item names what it controls.
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, type Page, test } from './test'

// Main window minimum is 400; the sidebar dock breakpoint is 640. The widest
// narrow step is 636, not 639: at the 1.5x scale this machine runs at, a 639
// request lands on 640 CSS px, which is docked.
const NARROW_WIDTHS = [400, 480, 560, 636]

// NARROW_CHROME_SCREENSHOT_DIR=<dir> saves captures of the key states for design
// review; never part of the assertions.
async function capture(page: Page, name: string, locator?: ReturnType<Page['locator']>): Promise<void> {
  const dir = process.env.NARROW_CHROME_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  const file = path.join(dir, `${name}.png`)

  if (locator) {
    await locator.screenshot({ path: file })
  } else {
    await page.screenshot({ path: file })
  }
}

interface Box {
  x: number
  y: number
  r: number
  b: number
}

interface OverlayGeometry {
  overlay: Box | null
  toggle: Box | null
  toggleHitsToggle: boolean
  brandText: Box | null
  brandHitIsBrand: boolean
  tabs: { id: string; box: Box; label: Box | null; hitIsTab: boolean }[]
}

/** Geometry of the overlay and the titlebar controls, read from the live DOM. */
async function measureOverlay(page: Page): Promise<OverlayGeometry> {
  return page.evaluate(() => {
    const boxOf = (el: Element | null): Box | null => {
      if (!el) {
        return null
      }

      const r = el.getBoundingClientRect()

      return { x: r.left, y: r.top, r: r.right, b: r.bottom }
    }

    // Text bounds, not the element's: a block element spans its whole row.
    const textBoxOf = (el: Element | null): Box | null => {
      if (!el) {
        return null
      }

      const range = document.createRange()

      range.selectNodeContents(el)
      const r = range.getBoundingClientRect()

      return { x: r.left, y: r.top, r: r.right, b: r.bottom }
    }

    const hitWithin = (box: Box | null, el: Element | null): boolean => {
      if (!box || !el) {
        return false
      }

      const hit = document.elementFromPoint((box.x + box.r) / 2, (box.y + box.b) / 2)

      return Boolean(hit && (el === hit || el.contains(hit)))
    }

    const toggleEl = document.querySelector<HTMLElement>('[data-titlebar-cluster="left"] button')
    const brandEl = document.querySelector<HTMLElement>('[data-personal-product-nav] > div')
    const overlayEl = document.querySelector<HTMLElement>('[data-narrow-overlay]')
    const tabEls = [...document.querySelectorAll<HTMLElement>('[data-narrow-overlay-tab]')]
    const toggle = boxOf(toggleEl)

    return {
      overlay: boxOf(overlayEl),
      toggle,
      toggleHitsToggle: hitWithin(toggle, toggleEl),
      brandText: textBoxOf(brandEl),
      brandHitIsBrand: hitWithin(textBoxOf(brandEl), brandEl),
      tabs: tabEls.map(tab => {
        const label = tab.querySelector<HTMLElement>('span.truncate')
        const box = boxOf(tab)

        return {
          id: tab.getAttribute('data-narrow-overlay-tab') ?? '',
          box: box ?? { x: 0, y: 0, r: 0, b: 0 },
          label: textBoxOf(label),
          hitIsTab: hitWithin(box, tab)
        }
      })
    }
  })
}

function intersects(a: Box | null, b: Box | null): boolean {
  if (!a || !b) {
    return false
  }

  // Half a pixel of tolerance: subpixel edges must not read as overlap.
  return a.x + 0.5 < b.r && b.x + 0.5 < a.r && a.y + 0.5 < b.b && b.y + 0.5 < a.b
}

function contains(outer: Box | null, inner: Box | null): boolean {
  if (!outer || !inner) {
    return false
  }

  return inner.x >= outer.x - 0.5 && inner.r <= outer.r + 0.5 && inner.y >= outer.y - 0.5 && inner.b <= outer.b + 0.5
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  // A workspace makes the review pane eligible to show, so the sessions zone can
  // stack a second collapsible pane beside sessions (the overlay's tab strip).
  const workspace = path.join(os.tmpdir(), 'narrow-chrome-workspace')
  fs.mkdirSync(workspace, { recursive: true })
  fixture = await setupMockBackend({ extraConfig: `terminal:\n  cwd: ${JSON.stringify(workspace)}` })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('the status bar approval item names what it controls and shows the mode beside it', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  const item = page.locator('[data-statusbar-item="approval-mode"]')
  const footer = page.locator('footer[data-slot="statusbar"]')
  await expect(item).toBeVisible({ timeout: 15_000 })
  await capture(page, 'statusbar-approval-smart', footer)

  await item.click()
  await page.getByRole('menuitemradio', { name: /关闭|Off/ }).click()
  await expect(item).toContainText(/关闭|Off/)
  await capture(page, 'statusbar-approval-off', footer)

  // The bare "Off" read as a close action; the subject says what is off.
  await expect(item).toContainText(/审批|Approvals/)
})

test('the narrow sessions overlay clears the titlebar controls at every narrow width', async () => {
  test.setTimeout(300_000)
  const page = fixture!.page
  const app = fixture!.app
  await waitForAppReady(fixture!, 120_000)

  // A zone with a second collapsible pane (review) stacks it beside sessions, so
  // the overlay shows the zone's tab strip. The layout persists across a reload.
  const stackedTree = {
    type: 'split',
    id: 'spl-root',
    orientation: 'row',
    weights: [0.82, 3.9, 1.45],
    children: [
      { type: 'group', id: 'grp-sessions', panes: ['sessions', 'review'], active: 'sessions' },
      { type: 'group', id: 'grp-main', panes: ['workspace'], active: 'workspace' },
      { type: 'group', id: 'grp-context', panes: ['files'], active: 'files' }
    ]
  }

  await page.evaluate(
    value => window.localStorage.setItem('hermes.desktop.layoutTree.v2', JSON.stringify(value)),
    stackedTree
  )
  await page.reload()
  await waitForAppReady(fixture!, 120_000)
  // The reload boots the backend again, and that boot card resizes the window.
  // Wait for the shell to settle before the window is sized for each width.
  await expect(page.locator('[data-personal-product-nav]')).toBeVisible({ timeout: 120_000 })
  await page.waitForTimeout(1_000)

  // The review pane starts hidden. Ctrl+G (view.toggleReview) shows it, which
  // puts the stacked review pane on screen beside sessions.
  await page.keyboard.press('Control+g')
  await page.waitForTimeout(500)

  const setWindowSize = async (width: number, height: number) => {
    await app.evaluate(({ BrowserWindow }, size) => {
      BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
    }, { width, height })
    // The breakpoint is a media query; give the layout a frame to re-evaluate.
    await page.waitForTimeout(700)
    // The step only means something if the window really is at that width.
    expect(await page.evaluate(() => window.innerWidth)).toBe(width)
  }

  const overlayTabs = page.locator('[data-narrow-overlay-tab]')

  for (const width of NARROW_WIDTHS) {
    await setWindowSize(width, 820)

    // Ctrl+B is view.toggleSidebar: at a narrow width it opens the sessions overlay.
    // The overlay stays open between widths, so open it only when no tab shows.
    if ((await overlayTabs.count()) === 0) {
      await page.keyboard.press('Control+b')
      await page.waitForTimeout(500)
    }

    await expect(page.locator('[data-narrow-overlay-tab]')).toHaveCount(2, { timeout: 10_000 })
    const geometry = await measureOverlay(page)
    await capture(page, `overlay-${width}`)

    expect.soft(geometry.tabs.map(tab => tab.id), `tabs at ${width}px`).toEqual(['sessions', 'review'])

    for (const tab of geometry.tabs) {
      expect.soft(intersects(tab.box, geometry.toggle), `tab ${tab.id} under the toggle at ${width}px`).toBe(false)
      expect.soft(contains(geometry.overlay, tab.box), `tab ${tab.id} inside the overlay at ${width}px`).toBe(true)
      expect.soft(contains(tab.box, tab.label), `tab ${tab.id} label fully visible at ${width}px`).toBe(true)
      expect.soft(tab.hitIsTab, `tab ${tab.id} takes its own clicks at ${width}px`).toBe(true)
    }

    expect.soft(intersects(geometry.brandText, geometry.toggle), `brand under the toggle at ${width}px`).toBe(false)
    expect.soft(geometry.brandHitIsBrand, `brand takes its own hits at ${width}px`).toBe(true)
    expect.soft(geometry.toggleHitsToggle, `toggle stays clickable at ${width}px`).toBe(true)

    // The status bar stays whole: its clusters must not clip any item.
    const clipped = await page.evaluate(() => {
      const footer = document.querySelector<HTMLElement>('footer[data-slot="statusbar"]')

      return footer
        ? [...footer.children].some(cluster => cluster.scrollWidth > cluster.clientWidth + 1)
        : true
    })

    expect.soft(clipped, `status bar clipped at ${width}px`).toBe(false)
  }

  // Docked at the default size no overlay is left open over the layout.
  await setWindowSize(1220, 800)
  await capture(page, 'docked-1220')
  await expect(page.locator('[data-narrow-overlay]')).toHaveCount(0)
})
