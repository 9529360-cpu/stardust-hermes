import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

// Radix Select calls scrollIntoView on its items when the content opens; jsdom
// doesn't implement it (nor hasPointerCapture / releasePointerCapture), so stub
// them to let the dropdown open in tests.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelInfo = vi.fn()
const getGlobalModelOptions = vi.fn()
const getAuxiliaryModels = vi.fn()
const getMoaModels = vi.fn()
const setModelAssignment = vi.fn()
const getRecommendedDefaultModel = vi.fn()
const saveMoaModels = vi.fn()
const setEnvVar = vi.fn()
const getHermesConfigRecord = vi.fn()
const saveHermesConfig = vi.fn()
const startManualLocalEndpoint = vi.fn()
const startManualOnboarding = vi.fn()
const startManualProviderOAuth = vi.fn()
const desktopOnboarding = atom({ manual: false })
let profileSwitchHandler: (() => void) | null = null

vi.mock('@/hermes', () => ({
  getGlobalModelInfo: (profile?: null | string) => getGlobalModelInfo(profile),
  getGlobalModelOptions: (opts?: unknown, profile?: null | string) => getGlobalModelOptions(opts, profile),
  getAuxiliaryModels: (profile?: null | string) => getAuxiliaryModels(profile),
  getApiRequestProfile: () => 'default',
  getMoaModels: (profile?: null | string) => getMoaModels(profile),
  profileScopeKey: (scope?: null | string) => (scope ?? '').trim() || 'default',
  setModelAssignment: (body: unknown) => setModelAssignment(body),
  getRecommendedDefaultModel: (slug: string) => getRecommendedDefaultModel(slug),
  saveMoaModels: (body: unknown) => saveMoaModels(body),
  setEnvVar: (key: string, value: string) => setEnvVar(key, value),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  saveHermesConfig: (config: unknown) => saveHermesConfig(config),
  setApiRequestProfile: () => {}
}))

vi.mock('@/store/onboarding', () => ({
  $desktopOnboarding: desktopOnboarding,
  startManualLocalEndpoint: (reason?: null | string, profile?: string) => startManualLocalEndpoint(reason, profile),
  startManualOnboarding: (reason?: null | string, profile?: string) => startManualOnboarding(reason, profile),
  startManualProviderOAuth: (slug: string, profile?: string) => startManualProviderOAuth(slug, profile)
}))

vi.mock('../hooks/use-on-profile-switch', () => ({
  useOnProfileSwitch: (handler: () => void) => {
    profileSwitchHandler = handler
  }
}))

beforeEach(() => {
  getGlobalModelInfo.mockResolvedValue({ provider: 'nous', model: 'hermes-4' })
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        name: 'Nous',
        slug: 'nous',
        models: ['hermes-4', 'hermes-4-mini'],
        authenticated: true,
        capabilities: { 'hermes-4': { reasoning: true, fast: true } }
      }
    ]
  })
  getAuxiliaryModels.mockResolvedValue({
    main: { provider: 'nous', model: 'hermes-4' },
    tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
  })
  getMoaModels.mockResolvedValue(null)
  setModelAssignment.mockResolvedValue({ ok: true, provider: 'nous', model: 'hermes-4', gateway_tools: [] })
  getRecommendedDefaultModel.mockResolvedValue({ provider: 'nous', model: 'hermes-4', free_tier: null })
  setEnvVar.mockResolvedValue({ ok: true })
  getHermesConfigRecord.mockResolvedValue({ agent: { reasoning_effort: 'medium', service_tier: 'normal' } })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  desktopOnboarding.set({ manual: false })
  profileSwitchHandler = null
})

async function renderModelSettings(scopeProfile?: string, initialEntries: string[] = ['/']) {
  const { ModelSettings } = await import('./model-settings')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    // The aux-task deep-link highlight reads useSearchParams, so the page
    // needs a router context in tests (the app provides HashRouter at root).
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={client}>
        <ModelSettings scopeProfile={scopeProfile} />
      </QueryClientProvider>
    </MemoryRouter>
  )
}

async function openAdvancedModelSettings(label = 'Advanced model settings') {
  fireEvent.click(await screen.findByRole('button', { name: label }))
}

async function renderChineseModelSettings() {
  const { ModelSettings } = await import('./model-settings')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <I18nProvider configClient={null} initialLocale="zh">
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ModelSettings />
        </QueryClientProvider>
      </MemoryRouter>
    </I18nProvider>
  )
}

describe('ModelSettings profile scope', () => {
  // #90549: the API helpers treat `null` as "deliberately target the
  // primary/default profile". A page following the active profile must pass
  // `undefined`, or every read repaints the primary's model and the user's
  // change looks reverted.
  it('follows the active profile (undefined, never null) when unscoped', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalledWith(undefined))
    expect(getGlobalModelOptions).toHaveBeenCalledWith(undefined, undefined)
    expect(getAuxiliaryModels).toHaveBeenCalledWith(undefined)
    expect(getMoaModels).toHaveBeenCalledWith(undefined)
  })

  it('reads through the explicit scope override when one is set', async () => {
    await renderModelSettings('research')

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalledWith('research'))
    expect(getGlobalModelOptions).toHaveBeenCalledWith(undefined, 'research')
    expect(getAuxiliaryModels).toHaveBeenCalledWith('research')
    expect(getMoaModels).toHaveBeenCalledWith('research')
  })
})

describe('ModelSettings', () => {
  it('shows configured model services as rows instead of a provider dropdown', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    expect(await screen.findByRole('button', { name: /Nous/ })).toBeTruthy()
    expect(screen.getByText('Default')).toBeTruthy()
    expect(screen.queryByText(/DeepSeek/)).toBeNull()
  })

  it.each(['custom', 'local', 'custom:lab'])(
    'opens local endpoint setup when %s has no inventory row',
    async provider => {
      getGlobalModelInfo.mockResolvedValueOnce({ provider, model: '' })
      getGlobalModelOptions.mockResolvedValueOnce({ providers: [] })

      await renderModelSettings()

      expect(await screen.findByText(provider)).toBeTruthy()
      expect(screen.queryByText(/undefined/)).toBeNull()
      expect(screen.queryByText(/signs in through your browser/)).toBeNull()

      fireEvent.click(await screen.findByRole('button', { name: 'Connect AI service' }))

      expect(startManualLocalEndpoint).toHaveBeenCalledOnce()
      expect(startManualOnboarding).not.toHaveBeenCalled()
      expect(startManualProviderOAuth).not.toHaveBeenCalled()
    }
  )

  it('opens the generic provider picker for an unknown provider with no inventory row', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'retired-provider', model: '' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [] })

    await renderModelSettings()

    fireEvent.click(await screen.findByRole('button', { name: 'Connect AI service' }))

    expect(startManualOnboarding).toHaveBeenCalledOnce()
    expect(startManualLocalEndpoint).not.toHaveBeenCalled()
    expect(startManualProviderOAuth).not.toHaveBeenCalled()
  })

  it('deep-links a known OAuth provider row into its setup flow', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'anthropic', model: '' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Anthropic',
          slug: 'anthropic',
          models: [],
          authenticated: false,
          auth_type: 'oauth'
        }
      ]
    })

    await renderModelSettings()

    fireEvent.click(await screen.findByRole('button', { name: 'Connect Anthropic' }))

    expect(startManualProviderOAuth).toHaveBeenCalledWith('anthropic', undefined)
    expect(startManualLocalEndpoint).not.toHaveBeenCalled()
    expect(startManualOnboarding).not.toHaveBeenCalled()
  })

  it('replaces the selected provider and model when the active profile changes', async () => {
    getGlobalModelInfo
      .mockResolvedValueOnce({ provider: 'custom', model: 'local-a' })
      .mockResolvedValueOnce({ provider: 'nous', model: 'hermes-4' })
    getGlobalModelOptions
      .mockResolvedValueOnce({
        providers: [
          {
            name: 'Custom A',
            slug: 'custom',
            models: ['local-a'],
            authenticated: true
          }
        ]
      })
      .mockResolvedValueOnce({
        providers: [
          {
            name: 'Nous',
            slug: 'nous',
            models: ['hermes-4'],
            authenticated: true,
            capabilities: { 'hermes-4': { reasoning: true, fast: true } }
          }
        ]
      })

    await renderModelSettings()
    expect(await screen.findByRole('button', { name: /Custom A/ })).toBeTruthy()

    await act(async () => {
      profileSwitchHandler?.()
    })

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByRole('button', { name: /Nous/ })).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Connect AI service' })).toBeNull()
  })

  it('preserves a user-defined provider endpoint when applying the main model', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true
        },
        {
          name: 'Ollama',
          slug: 'local-ollama',
          models: ['qwen3:latest'],
          authenticated: true,
          is_user_defined: true,
          api_url: 'http://localhost:11434/v1'
        }
      ]
    })
    setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: 'local-ollama',
      model: 'qwen3:latest',
      gateway_tools: []
    })

    await renderModelSettings()

    fireEvent.click(await screen.findByRole('button', { name: /Ollama/ }))
    expect(await screen.findByText('qwen3:latest')).toBeTruthy()

    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'qwen3:latest',
        provider: 'local-ollama',
        scope: 'main',
        base_url: 'http://localhost:11434/v1'
      })
    )
  })

  it('rotates a saved API key from the provider editor before applying its model', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'openai', model: 'gpt-5.4' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'OpenAI',
          slug: 'openai',
          models: ['gpt-5.4'],
          authenticated: true,
          auth_type: 'api_key',
          key_env: 'OPENAI_API_KEY'
        }
      ]
    })
    setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: 'openai',
      model: 'gpt-5.4',
      gateway_tools: []
    })

    await renderModelSettings()

    const key = await screen.findByPlaceholderText('Leave blank to keep the saved key')
    fireEvent.change(key, { target: { value: 'sk-replacement' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }))

    await waitFor(() => expect(setEnvVar).toHaveBeenCalledWith('OPENAI_API_KEY', 'sk-replacement'))
    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'gpt-5.4',
        provider: 'openai',
        scope: 'main'
      })
    )
    expect(setEnvVar.mock.invocationCallOrder[0]).toBeLessThan(setModelAssignment.mock.invocationCallOrder[0])
  })

  it('updates a saved API key without changing the selected model', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'openai', model: 'gpt-5.4' })
    getGlobalModelOptions.mockResolvedValue({
      providers: [
        {
          name: 'OpenAI',
          slug: 'openai',
          models: ['gpt-5.4', 'gpt-5.4-mini'],
          authenticated: true,
          auth_type: 'api_key',
          key_env: 'OPENAI_API_KEY'
        }
      ]
    })

    await renderModelSettings()

    const key = await screen.findByPlaceholderText('Leave blank to keep the saved key')
    fireEvent.change(key, { target: { value: 'sk-rotated' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Update key' }))

    await waitFor(() => expect(setEnvVar).toHaveBeenCalledWith('OPENAI_API_KEY', 'sk-rotated'))
    await waitFor(() => expect((key as HTMLInputElement).value).toBe(''))
    expect(getRecommendedDefaultModel).not.toHaveBeenCalled()
    expect(setModelAssignment).not.toHaveBeenCalled()
    expect(screen.getByText('Model: gpt-5.4')).toBeTruthy()
  })

  it('opens the shared provider and custom-service flows from the model page', async () => {
    await renderModelSettings('research')

    fireEvent.click(await screen.findByRole('button', { name: 'Add model service' }))
    expect(startManualOnboarding).toHaveBeenCalledWith(null, 'research')

    fireEvent.click(await screen.findByRole('button', { name: 'Add custom service' }))
    expect(startManualLocalEndpoint).toHaveBeenCalledWith(null, 'research')
  })

  it('refreshes the model-service list after the shared setup flow closes', async () => {
    await renderModelSettings()
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalledTimes(1))

    await act(async () => {
      desktopOnboarding.set({ manual: true })
    })
    await act(async () => {
      desktopOnboarding.set({ manual: false })
    })

    await waitFor(() => expect(getGlobalModelOptions.mock.calls.length).toBeGreaterThan(1))
  })

  it('writes the profile default speed (service_tier) as a sparse patch, never the cached snapshot', async () => {
    // The cached record is a default-expanded snapshot; a CLI pin made after it
    // loaded is not in it. Echoing the whole record back would reset that
    // auxiliary slot to auto/'' (#95460) — only the edited key may be sent.
    getHermesConfigRecord.mockResolvedValue({
      agent: { reasoning_effort: 'medium', service_tier: 'normal' },
      auxiliary: { curator: { provider: 'auto', model: '', reasoning_effort: 'high' } }
    })
    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    const fastSwitch = await screen.findByRole('switch')
    fireEvent.click(fastSwitch)

    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledWith({ agent: { service_tier: 'fast' } }))
  })

  it('hides the reasoning/speed defaults when the main model reports no capabilities', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: false, fast: false } }
        }
      ]
    })

    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('keeps auxiliary and MoA controls behind advanced settings by default', async () => {
    await renderModelSettings()

    expect(await screen.findByRole('button', { name: 'Advanced model settings' })).toBeTruthy()
    const advanced = screen.getByText('Auxiliary models').closest('[data-slot="advanced-model-settings"]')
    expect((advanced as HTMLElement | null)?.hidden).toBe(true)
    expect(screen.queryByRole('button', { name: 'Set to main' })).toBeNull()
  })

  it('reveals advanced model settings for an auxiliary-task deep link', async () => {
    await renderModelSettings(undefined, ['/?tab=config:model&aux=vision'])

    expect(await screen.findByRole('button', { name: 'Hide advanced model settings' })).toBeTruthy()
    const advanced = screen.getByText('Auxiliary models').closest('[data-slot="advanced-model-settings"]')
    expect((advanced as HTMLElement | null)?.hidden).toBe(false)
  })

  it('renders the auxiliary task rows after advanced settings are opened', async () => {
    await renderModelSettings()
    await openAdvancedModelSettings()

    expect(await screen.findByText('Vision')).toBeTruthy()
    // #97297 — the three canonical slots the backend serves must have rows too.
    expect(screen.getByText('Triage specifier')).toBeTruthy()
    expect(screen.getByText('Kanban decomposer')).toBeTruthy()
    expect(screen.getByText('Profile describer')).toBeTruthy()
    expect(screen.getAllByText('auto · use main model').length).toBeGreaterThan(0)
  })

  it('edits auxiliary reasoning effort below the selected model and applies it with the assignment', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [{ task: 'vision', provider: 'nous', model: 'hermes-4', base_url: '', reasoning_effort: null }]
    })

    await renderModelSettings()
    await openAdvancedModelSettings()

    expect(screen.queryByRole('combobox', { name: 'Vision reasoning effort' })).toBeNull()

    fireEvent.click((await screen.findAllByRole('button', { name: 'Change' }))[0])

    const reasoningSelect = await screen.findByRole('combobox', { name: 'Vision reasoning effort' })
    expect(reasoningSelect.compareDocumentPosition(await screen.findByRole('combobox', { name: 'Vision model' }))).toBe(
      Node.DOCUMENT_POSITION_PRECEDING
    )

    fireEvent.click(reasoningSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'High' }))

    const applyButtons = await screen.findAllByRole('button', { name: 'Apply' })
    fireEvent.click(applyButtons.at(-1)!)

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'hermes-4',
        provider: 'nous',
        scope: 'auxiliary',
        task: 'vision',
        reasoning_effort: 'high'
      })
    )
  })

  it('assigns an auxiliary task to the main model via setModelAssignment', async () => {
    await renderModelSettings()
    await openAdvancedModelSettings()

    // One "Set to main" button per task slot; the first is Vision.
    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'hermes-4',
        provider: 'nous',
        scope: 'auxiliary',
        task: 'vision'
      })
    )
  })

  it('carries the user-defined endpoint when an aux slot is set to a local main model', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Ollama',
          slug: 'local-ollama',
          models: ['qwen3:latest'],
          authenticated: true,
          is_user_defined: true,
          api_url: 'http://localhost:11434/v1'
        }
      ]
    })
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'local-ollama', model: 'qwen3:latest' })
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'local-ollama', model: 'qwen3:latest' },
      tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
    })

    await renderModelSettings()
    await openAdvancedModelSettings()

    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'qwen3:latest',
        provider: 'local-ollama',
        scope: 'auxiliary',
        task: 'vision',
        base_url: 'http://localhost:11434/v1'
      })
    )
  })

  it('warns when a main switch leaves auxiliary tasks pinned to another provider', async () => {
    setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: 'openrouter',
      model: 'anthropic/claude-opus-4.7',
      gateway_tools: [],
      stale_aux: [{ task: 'compression', provider: 'nous', model: 'hermes-4' }]
    })

    await renderModelSettings()
    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())

    const applyButton = await screen.findByRole('button', { name: 'Apply' })
    fireEvent.click(applyButton)

    // The switch-time notice names the pinned provider and offers a reset.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
    expect(screen.getByText('nous')).toBeTruthy()
  })

  it('shows a persistent banner when a loaded aux slot mismatches the main provider', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [{ task: 'curator', provider: 'openrouter', model: 'anthropic/claude-opus-4.7', base_url: '' }]
    })

    await renderModelSettings()

    // Banner present on load, no switch required.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
  })

  it('does not warn when an aux slot uses the main alias', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [{ task: 'vision', provider: 'main', model: 'kimi-k3', base_url: '' }]
    })

    await renderModelSettings()
    await openAdvancedModelSettings()
    await screen.findAllByRole('button', { name: 'Set to main' })

    // 'main' is a backend-supported alias that tracks the active main provider
    // (auxiliary_client._normalize_aux_provider) — it can never be a stale pin. #97310
    expect(screen.queryByText(/still run on/)).toBeNull()
  })

  it('does not flag an aux slot pinned to a local/LAN endpoint and shows its base_url', async () => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'ollama-cloud', model: 'glm-5.3-flash' },
      tasks: [
        {
          task: 'title_generation',
          provider: 'openai',
          model: 'llama3.2:3b',
          base_url: 'http://byron.local:11434/v1',
          local_endpoint: true
        },
        {
          task: 'vision',
          provider: 'openai',
          model: 'gpt-4o-mini',
          base_url: 'https://api.example.com/v1',
          local_endpoint: false
        }
      ]
    })

    await renderModelSettings()

    // The public custom endpoint still bills a provider, so the banner stays —
    // but it names only that one task, not the free LAN pin.
    expect(await screen.findByText(/1 auxiliary task \(/)).toBeTruthy()
    await openAdvancedModelSettings()
    // The row shows where the pinned task actually points.
    expect(screen.getByText(/http:\/\/byron\.local:11434\/v1/)).toBeTruthy()
  })
})

describe('ModelSettings MoA preset editor', () => {
  const moaConfig = () => ({
    default_preset: 'default',
    active_preset: '',
    presets: {
      default: {
        reference_models: [
          { provider: 'nous', model: 'hermes-4' },
          { provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' }
        ],
        aggregator: { provider: 'openrouter', model: 'anthropic/claude-opus-4.8' },
        reference_temperature: 0,
        aggregator_temperature: 0,

        enabled: true
      }
    },
    reference_models: [
      { provider: 'nous', model: 'hermes-4' },
      { provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' }
    ],
    aggregator: { provider: 'openrouter', model: 'anthropic/claude-opus-4.8' },
    reference_temperature: 0,
    aggregator_temperature: 0,

    enabled: true
  })

  beforeEach(() => {
    getGlobalModelOptions.mockResolvedValue({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4', 'hermes-4-mini'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: true, fast: true } }
        },
        {
          name: 'OpenRouter',
          slug: 'openrouter',
          models: ['deepseek/deepseek-v4-pro', 'anthropic/claude-opus-4.8'],
          authenticated: true
        }
      ]
    })
    getMoaModels.mockResolvedValue(moaConfig())
    saveMoaModels.mockImplementation((body: unknown) => Promise.resolve(body))
  })

  async function openReferenceEditor() {
    await renderModelSettings()
    await openAdvancedModelSettings()
    expect(await screen.findByText('Reference 1')).toBeTruthy()
  }

  function slotSelects() {
    // Combobox order in the MoA section (last 7 on the page): preset select,
    // then provider+model per reference (2 refs), then aggregator
    // provider+model. Reference 1's pair is therefore at -6 / -5.
    const all = screen.getAllByRole('combobox')

    return { ref1Provider: all.at(-6)!, ref1Model: all.at(-5)! }
  }

  it('renders the model-service and MoA chrome in the active Chinese locale', async () => {
    await renderChineseModelSettings()

    expect(await screen.findByText('模型服务')).toBeTruthy()
    expect(screen.getByText(/选一个服务，需要时填 API Key 或登录/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '高级模型设置' })).toBeTruthy()
    await openAdvancedModelSettings('高级模型设置')
    expect(await screen.findByText('参考模型 1')).toBeTruthy()
    expect(screen.getByText(/配置具名预设/)).toBeTruthy()
    expect(screen.getByText('已启用')).toBeTruthy()
    expect(screen.getByRole('button', { name: '设为默认' })).toBeTruthy()
    expect(screen.getByPlaceholderText('新预设')).toBeTruthy()
    expect(screen.getByRole('button', { name: '添加预设' })).toBeTruthy()
    expect(screen.getByText(/默认预设:/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '添加参考模型' })).toBeTruthy()
    expect(screen.queryByText('Enabled')).toBeNull()
    expect(screen.queryByText('Set default')).toBeNull()
    expect(screen.queryByText('Reference 1')).toBeNull()
  })

  it('holds the autosave while a slot is half-filled (provider changed, model pending)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'OpenRouter' }))

      // Model was cleared by the provider change → config incomplete → the
      // debounced autosave must NOT fire, even well past the 600ms window.
      await vi.advanceTimersByTimeAsync(2000)
      expect(saveMoaModels).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves once the model pick completes the slot', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'OpenRouter' }))
      await vi.advanceTimersByTimeAsync(700)

      fireEvent.click(slotSelects().ref1Model)
      fireEvent.click(await screen.findByRole('option', { name: 'anthropic/claude-opus-4.8' }))
      await vi.advanceTimersByTimeAsync(700)

      expect(saveMoaModels).toHaveBeenCalledTimes(1)
      const sent = saveMoaModels.mock.calls[0][0] as ReturnType<typeof moaConfig>
      expect(sent.presets.default.reference_models[0]).toEqual({
        provider: 'openrouter',
        model: 'anthropic/claude-opus-4.8'
      })
      // The untouched slots ride along unchanged — nothing reverts to defaults.
      expect(sent.presets.default.reference_models[1]).toEqual({
        provider: 'openrouter',
        model: 'deepseek/deepseek-v4-pro'
      })
      expect(sent.presets.default.aggregator).toEqual({
        provider: 'openrouter',
        model: 'anthropic/claude-opus-4.8'
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not clear the model or save when the same provider is re-selected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'Nous' }))
      await vi.advanceTimersByTimeAsync(700)

      // Radix treats re-picking the current value as a no-op (no
      // onValueChange), so nothing changes: no save, model still shown.
      expect(saveMoaModels).not.toHaveBeenCalled()
      expect(screen.getByText('nous · hermes-4')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('autosaves the selected preset when its enabled switch is toggled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(screen.getByRole('switch', { name: 'Enabled' }))
      await vi.advanceTimersByTimeAsync(700)

      expect(saveMoaModels).toHaveBeenCalledWith(
        expect.objectContaining({
          presets: expect.objectContaining({
            default: expect.objectContaining({ enabled: false })
          })
        })
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves a disabled reference model without removing it (per-slot enabled toggle)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(screen.getByRole('switch', { name: 'Disable reference 1' }))
      await vi.advanceTimersByTimeAsync(700)

      expect(saveMoaModels).toHaveBeenCalledWith(
        expect.objectContaining({
          presets: expect.objectContaining({
            default: expect.objectContaining({
              reference_models: [
                expect.objectContaining({ provider: 'nous', model: 'hermes-4', enabled: false }),
                expect.objectContaining({ provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' })
              ]
            })
          })
        })
      )
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('ModelSettings code-skew 503', () => {
  const skewError = new Error(
    'Error invoking remote method \'hermes:api\': Error: 503: {"detail":"Restart required: This process is running code from 08b4875f4a but the checkout on disk is now 48d2528066. The model picker would risk a stale-module crash — restart the Desktop-owned backend to load the new code (use Restart backend in Hermes Desktop, or quit and reopen the app)"}'
  )

  afterEach(() => {
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  it('unwraps the stale-backend 503 instead of dumping IPC JSON', async () => {
    getGlobalModelOptions.mockRejectedValueOnce(skewError)

    await renderModelSettings()

    await waitFor(() => {
      expect(screen.getByText(/running old code after an update/i)).toBeTruthy()
    })
    expect(screen.getByRole('button', { name: 'Restart backend' })).toBeTruthy()
    expect(screen.queryByText(/hermes:api/)).toBeNull()
    expect(screen.queryByText(/systemctl/)).toBeNull()
  })

  it('recycles the Desktop-owned backend and reloads the catalog', async () => {
    const recycleBackend = vi.fn().mockResolvedValue({ ok: true })

    ;(window as unknown as { hermesDesktop: { recycleBackend: typeof recycleBackend } }).hermesDesktop = {
      recycleBackend
    }

    getGlobalModelOptions.mockRejectedValueOnce(skewError)

    await renderModelSettings()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restart backend' })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Restart backend' }))

    await waitFor(() => expect(recycleBackend).toHaveBeenCalledWith(undefined))
    await waitFor(() => expect(getGlobalModelOptions.mock.calls.length).toBeGreaterThan(1))
  })
})