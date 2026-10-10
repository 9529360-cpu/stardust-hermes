import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'
import type { Locale } from './types'

// Every key read by the screens fixed in the zh copy sweep. A locale bundle is
// partial and silently falls back to English, so each key must resolve to its
// own text in every bundle, and the Chinese text must actually be Chinese.
const SWEPT_KEYS: Record<string, string[]> = {
  'sidebar.filterMenu': [
    'grouping',
    'ordering',
    'show',
    'status',
    'pullRequest',
    'profile',
    'project',
    'resetDefaults',
    'updated',
    'created',
    'tokens',
    'cost',
    'manual',
    'preview',
    'prOpen',
    'draft',
    'merged',
    'closed',
    'noPr',
    'needsInput',
    'working',
    'unread',
    'idle',
    'inboxStyle',
    'archived',
    'expandAll',
    'collapseAll'
  ],
  'settings.quickEntry': [
    'askPlaceholder',
    'disconnectedPlaceholder',
    'sendTo',
    'targetLabel',
    'currentChat',
    'newSession'
  ],
  'assistant.thread': [
    'messageFrom',
    'showMessage',
    'repliedTo',
    'showReply',
    'conversationTimeline',
    'hermesWorking',
    'toolPayload',
    'toolSearch',
    'emojiSearch',
    'loadingEmoji',
    'noEmoji',
    'moreEmoji',
    'reactedByHermes',
    'deliveryPending',
    'deliveryDone',
    'removeReaction',
    'summarizingThread',
    'searchResults'
  ],
  'assistant.media': [
    'fetchFailed',
    'openMediaFile',
    'couldntLoad',
    'openImage',
    'loadingName',
    'generatedImage',
    'openNamed'
  ],
  'assistant.embeds': ['failed', 'openDiagram', 'holdToZoom', 'spotifyTitle', 'youtubeTitle'],
  connectors: ['skipThis', 'setupUnavailable', 'nothingYet', 'nothingYetBody', 'noneOfThese', 'continueWithCount'],
  onboarding: ['skipSetup', 'retryFirstBuild', 'firstBuildFailed', 'buildStarted', 'buildOpening', 'workingOnIt'],
  'ui.zoomable': ['zoomOut', 'zoomIn', 'reset'],
  ui: ['moreActions', 'hatchingProgress'],
  'messaging.telegramQr': ['qrAlt'],
  'commandCenter.generatePet': [
    'unavailableTitle',
    'unavailableBody',
    'setupImageGeneration',
    'grabKeyFrom',
    'addReference',
    'removeReference',
    'referenceFallback'
  ],
  'settings.computerUse': [
    'checking',
    'notSupported',
    'installBackend',
    'grantHint',
    'grantIdentity',
    'recheck',
    'ready',
    'platformNoteLinux',
    'platformNoteWin32',
    'granted',
    'notGranted',
    'unknownState',
    'pillReady',
    'pillNotReady',
    'approveTitle',
    'approveMessage',
    'waitingApproval',
    'grantPermissions',
    'hintAccessibility',
    'hintScreenRecording',
    'requestFailed',
    'readFailed'
  ],
  'settings.appearance': ['vscodeMarketplace', 'noThemeMatch', 'imageFilterName'],
  'settings.memoryProvider': [
    'waitingForConsent',
    'loadFailed',
    'loadingLabel',
    'settingsTitle',
    'setLabel',
    'notSetLabel',
    'fullConfig',
    'modalTitle',
    'modalDescription',
    'docsLink',
    'saveChanges',
    'keepCurrentValue',
    'apiKeySet',
    'oauthSet',
    'saved',
    'savedTitle',
    'aboutField',
    'startFailedDetail',
    'startFailedToast',
    'timedOut',
    'connectionFailed',
    'connectViaOauth',
    'reconnect',
    'loadFailedPlain'
  ],
  'settings.model': ['auxiliaryRunOn', 'otherProviders', 'slotProviderAria', 'slotModelAria', 'slotReasoningAria'],
  'settings.poolLimits': ['warmBotBackendsDesc', 'backendIdleTimeoutDesc'],
  'settings.mcp': ['apiKeyTag'],
  starmap: ['legendCoreOuter', 'legendMemory', 'badShareCode'],
  'starmap.nodeMenu': [
    'editMemory',
    'editSkill',
    'editTitle',
    'archiveSkill',
    'deleteMemory',
    'removedForever',
    'deleteTitle'
  ],
  'starmap.timeline': ['pause', 'playTimeline', 'scrubber'],
  'shell.modelMenu': ['moaPresets', 'moaPrefix'],
  'shell.tiles': ['sessionOpenFailed', 'noPageAt', 'resumeStillAvailable', 'resumeUnavailable']
}

// Existing keys the swept screens now read; they must be translated too.
const REUSED_KEYS = [
  'sidebar.filters',
  'sidebar.markAllRead',
  'sidebar.nav.artifacts',
  'sidebar.nav.messaging',
  'sidebar.nav.skills',
  'settings.quickEntry.enabledTitle',
  'connectors.search',
  'composer.stop',
  'settings.model.resetAllToMain',
  'common.cancel',
  'common.close',
  'common.copy',
  'common.copied',
  'common.retry',
  'common.save',
  'common.saving',
  'common.connect'
]

// Acronym-led labels that stay Latin in some bundles (the MoA prefix is a product term).
const ACRONYM_KEYS = new Set(['shell.modelMenu.moaPrefix'])

// Sample arguments for the keys whose values are functions.
const SAMPLE_ARGS: Record<string, unknown[]> = {
  'assistant.thread.messageFrom': ['Ada'],
  'assistant.thread.repliedTo': ['Ada'],
  'assistant.thread.removeReaction': ['👍'],
  'assistant.media.fetchFailed': ['clip.mp4'],
  'assistant.media.openMediaFile': ['audio'],
  'assistant.media.couldntLoad': ['diagram.png'],
  'assistant.media.loadingName': ['diagram.png'],
  'assistant.media.openNamed': ['clip.mp4'],
  'assistant.embeds.failed': ['YouTube'],
  'connectors.continueWithCount': [2],
  'onboarding.buildStarted': ['Build'],
  'onboarding.buildOpening': ['Build'],
  'settings.computerUse.notSupported': ['freebsd'],
  'settings.appearance.noThemeMatch': ['dark'],
  'settings.memoryProvider.loadFailed': ['timeout'],
  'settings.memoryProvider.settingsTitle': ['Mem0'],
  'settings.memoryProvider.setLabel': ['API key'],
  'settings.memoryProvider.notSetLabel': ['API key'],
  'settings.memoryProvider.modalTitle': ['Mem0'],
  'settings.memoryProvider.modalDescription': ['Mem0'],
  'settings.memoryProvider.docsLink': ['Mem0'],
  'settings.memoryProvider.savedTitle': ['Mem0'],
  'settings.memoryProvider.aboutField': ['Key'],
  'settings.model.auxiliaryRunOn': [2, 'vision, title'],
  'settings.model.slotProviderAria': ['Auxiliary'],
  'settings.model.slotModelAria': ['Auxiliary'],
  'settings.model.slotReasoningAria': ['Auxiliary'],
  'starmap.nodeMenu.editTitle': ['Ada'],
  'starmap.nodeMenu.deleteTitle': ['Ada'],
  'shell.tiles.noPageAt': ['/projects']
}

const LOCALES: Locale[] = ['zh', 'zh-hant', 'ja', 'ru', 'ar']

const ALL_KEYS = [
  ...Object.entries(SWEPT_KEYS).flatMap(([namespace, keys]) => keys.map(key => `${namespace}.${key}`)),
  ...REUSED_KEYS
]

function lookup(locale: Locale, path: string): unknown {
  return path
    .split('.')
    .reduce<unknown>(
      (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
      TRANSLATIONS[locale]
    )
}

// Renders a bundle entry the way the screen does, so the comparison is on visible text.
function render(locale: Locale, path: string): string {
  const value = lookup(locale, path)

  const resolved =
    typeof value === 'function' ? (value as (...args: unknown[]) => unknown)(...(SAMPLE_ARGS[path] ?? [])) : value

  if (typeof resolved === 'string') {
    return resolved
  }

  // Split sentences around an inline element return { before, after }.
  const parts = resolved as { after: string; before: string }

  return `${parts.before}[profile]${parts.after}`
}

describe('desktop copy sweep', () => {
  it('covers the keys the swept screens read', () => {
    expect(ALL_KEYS.length).toBeGreaterThan(100)
  })

  it.each(ALL_KEYS)('%s resolves to its own text in every locale bundle', key => {
    const english = render('en', key)

    for (const locale of LOCALES) {
      expect(lookup(locale, key), `${locale} ${key}`).toBeDefined()

      if (!ACRONYM_KEYS.has(key)) {
        expect(render(locale, key), `${locale} ${key} falls back to English`).not.toBe(english)
      }
    }
  })

  it.each(ALL_KEYS.filter(key => !ACRONYM_KEYS.has(key)))('%s has Chinese text in zh and zh-hant', key => {
    expect(render('zh', key), `zh ${key}`).toMatch(/[一-鿿]/)
    expect(render('zh-hant', key), `zh-hant ${key}`).toMatch(/[一-鿿]/)
  })
})
