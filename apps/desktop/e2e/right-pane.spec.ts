import { expect, test } from './test'
import { setupMockBackend, waitForAppReady } from './fixtures'

test.setTimeout(180_000)

test('persistent terminal overlay follows the pane after a real split drag', async () => {
  const fixture = await setupMockBackend()

  try {
    const { page } = fixture
    await waitForAppReady(fixture, 120_000)

    await page.keyboard.press('Control+`')

    const slot = page.locator('[data-terminal-slot]')
    const overlay = page.locator('[data-persistent-terminal]')
    await slot.waitFor({ state: 'visible', timeout: 30_000 })
    await overlay.locator('.xterm').waitFor({ state: 'visible', timeout: 30_000 })

    const pick = await page.evaluate(() => {
      const terminalSlot = document.querySelector<HTMLElement>('[data-terminal-slot]')
      if (!terminalSlot) return null

      const before = terminalSlot.getBoundingClientRect()
      const target = [...document.querySelectorAll<HTMLElement>('[role="separator"]')]
        .map(element => {
          const box = element.getBoundingClientRect()
          const horizontal = box.width > box.height
          const center = horizontal ? (box.top + box.bottom) / 2 : (box.left + box.right) / 2
          const sides = horizontal ? [before.top, before.bottom] : [before.left, before.right]

          return {
            box: { left: box.left, top: box.top, width: box.width, height: box.height },
            horizontal,
            score: Math.min(...sides.map(side => Math.abs(center - side))),
          }
        })
        .filter(item => item.horizontal && item.box.width > 0 && item.box.height > 0)
        .sort((a, b) => a.score - b.score)[0]

      if (!target) return null

      return {
        before: {
          left: before.left,
          top: before.top,
          width: before.width,
          height: before.height,
        },
        target,
      }
    })

    expect(pick).not.toBeNull()
    if (!pick) throw new Error('No terminal split separator was available')

    const { before, target } = pick
    const startX = target.box.left + target.box.width / 2
    const startY = target.box.top + target.box.height / 2
    const center = target.horizontal ? startY : startX
    const sides = target.horizontal
      ? [before.top, before.top + before.height]
      : [before.left, before.left + before.width]
    const direction = Math.abs(center - sides[0]) < Math.abs(center - sides[1]) ? -1 : 1
    const deltaX = target.horizontal ? 0 : direction * 80
    const deltaY = target.horizontal ? direction * 80 : 0

    await page.mouse.move(startX, startY)
    await page.mouse.down()
    await page.mouse.move(startX + deltaX, startY + deltaY, { steps: 12 })
    await page.mouse.up()
    await page.waitForTimeout(500)

    const result = await page.evaluate(() => {
      const terminalSlot = document.querySelector<HTMLElement>('[data-terminal-slot]')!
      const persistentOverlay = document.querySelector<HTMLElement>('[data-persistent-terminal]')!
      const next = terminalSlot.getBoundingClientRect()
      const fixed = persistentOverlay.getBoundingClientRect()

      return {
        slot: { left: next.left, top: next.top, width: next.width, height: next.height },
        drift: Math.max(
          Math.abs(next.left - fixed.left),
          Math.abs(next.top - fixed.top),
          Math.abs(next.width - fixed.width),
          Math.abs(next.height - fixed.height),
        ),
      }
    })

    const moved = Math.max(
      Math.abs(result.slot.left - before.left),
      Math.abs(result.slot.top - before.top),
      Math.abs(result.slot.width - before.width),
      Math.abs(result.slot.height - before.height),
    )

    expect(moved).toBeGreaterThan(10)
    expect(result.drift).toBeLessThanOrEqual(1)
  } finally {
    await fixture.cleanup()
  }
})
