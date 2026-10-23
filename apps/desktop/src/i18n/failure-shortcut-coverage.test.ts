import { describe, expect, it } from 'vitest'

import { KEYBIND_ACTIONS, KEYBIND_READONLY } from '@/lib/keybinds/actions'
import { comboTokens, formatCombo } from '@/lib/keybinds/combo'
import { providerWaitText } from '@/store/provider-wait'

import { en } from './en'
import { setRuntimeI18nLocale } from './runtime'
import { zh } from './zh'

describe('failure and shortcut locale coverage', () => {
  it('provides Chinese action names without inherited English fallback', () => {
    for (const { id } of [...KEYBIND_ACTIONS, ...KEYBIND_READONLY]) {
      expect(en.keybinds.actions[id], id).toBeTruthy()
      expect(zh.keybinds.actions[id], id).toMatch(/\p{Script=Han}/u)
    }
  })
  it('localizes key names without changing English formatting', () => {
    expect(comboTokens('mod+shift+escape', zh.keybinds.keys)).toEqual(['控制', '上档', '退出'])
    expect(formatCombo('mod+shift+escape', en.keybinds.keys)).toBe(formatCombo('mod+shift+escape'))
  })
  it('localizes structured retry frames and older backend text', () => {
    setRuntimeI18nLocale('zh')
    const expected = zh.boot.providerRetry(6, 2, 3)
    expect(providerWaitText('raw technical message', 'provider_retry', { seconds: 6, attempt: 2, max_attempts: 3 })).toBe(expected)
    expect(providerWaitText('⏳ waiting on provider — retrying in 6s (attempt 2/3)')).toBe(expected)
    setRuntimeI18nLocale('en')
    expect(providerWaitText('raw', 'provider_retry', { seconds: 6, attempt: 2, max_attempts: 3 })).toBe(en.boot.providerRetry(6, 2, 3))
  })
  it('covers every failure, recovery and shortcut panel string in both locales', () => {
    const check = (english: unknown, chinese: unknown, path: string) => {
      expect(chinese, path).toBeDefined()
      if (typeof english === 'string' || typeof english === 'function') {
        const enText = typeof english === 'function' ? english('测试', 2, 3) : english
        const zhText = typeof chinese === 'function' ? chinese('测试', 2, 3) : chinese

        expect(enText, path).toBeTruthy()
        expect(zhText, path).toMatch(/\p{Script=Han}/u)
      } else if (english && typeof english === 'object') {
        for (const [key, value] of Object.entries(english)) {
          check(value, (chinese as Record<string, unknown>)[key], `${path}.${key}`)
        }
      }
    }

    check(en.boot, zh.boot, 'boot')
    check(en.keybinds, zh.keybinds, 'keybinds')
    check(en.notifications.actions, zh.notifications.actions, 'notifications.actions')
  })
  it('provides translated backend recovery actions', () => {
    expect(zh.boot.errors.restartHermes).toMatch(/\p{Script=Han}/u)
    expect(zh.boot.errors.openLogs).toMatch(/\p{Script=Han}/u)
    expect(zh.notifications.actions.restartHermes).toMatch(/\p{Script=Han}/u)
    expect(zh.notifications.actions.openGateways).toMatch(/\p{Script=Han}/u)
    expect(zh.boot.errors.gatewayConnectionLostDetail).toMatch(/\p{Script=Han}/u)
  })
})
