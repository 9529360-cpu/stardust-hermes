import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { saveCustomEndpoint, validateCustomEndpoint } from '@/hermes'
import { I18nProvider } from '@/i18n'

const getCustomEndpoints = vi.fn()
let profileSwitchHandler: (() => void) | null = null

vi.mock('@/hermes', () => ({
  activateCustomEndpoint: vi.fn(),
  deleteCustomEndpoint: vi.fn(),
  getCustomEndpoints: (...args: unknown[]) => getCustomEndpoints(...args),
  saveCustomEndpoint: vi.fn(),
  validateCustomEndpoint: vi.fn()
}))

vi.mock('../hooks/use-on-profile-switch', () => ({
  useOnProfileSwitch: (handler: () => void) => {
    profileSwitchHandler = handler
  }
}))

beforeEach(() => {
  profileSwitchHandler = null
  getCustomEndpoints.mockResolvedValue({ endpoints: [] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('CustomEndpointsSettings', () => {
  it('validates HTTP URLs without accepting arbitrary schemes', async () => {
    const { isEndpointUrl } = await import('./custom-endpoints-settings')
    expect(isEndpointUrl('https://relay.example/v1')).toBe(true)
    expect(isEndpointUrl('http://127.0.0.1:8000/v1')).toBe(true)
    expect(isEndpointUrl('file:///etc/passwd')).toBe(false)
    expect(isEndpointUrl('https://user:pass@relay.example/v1')).toBe(false)
    expect(isEndpointUrl('relay.example/v1')).toBe(false)
  })

  it('drops stale responses when the requested profile changes', async () => {
    let resolveA!: (value: unknown) => void
    let resolveB!: (value: unknown) => void
    const requestA = new Promise(resolve => {resolveA = resolve})
    const requestB = new Promise(resolve => {resolveB = resolve})
    getCustomEndpoints.mockImplementation((profile?: string) => (profile === 'profile-a' ? requestA : requestB))
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    const view = render(<CustomEndpointsSettings scopeProfile="profile-a" />)

    await waitFor(() => expect(getCustomEndpoints).toHaveBeenCalledWith('profile-a'))
    view.rerender(<CustomEndpointsSettings scopeProfile="profile-b" />)
    await waitFor(() => expect(getCustomEndpoints).toHaveBeenCalledWith('profile-b'))

    await act(async () => {
      resolveB({
        endpoints: [{
          id: 'b', name: 'Profile B Relay', base_url: 'https://b.example/v1', model: 'b-model',
          models: ['b-model'], discover_models: true, is_current: true, has_api_key: false, source: 'providers'
        }]
      })
      await requestB
    })
    expect(await screen.findByText('Profile B Relay')).toBeTruthy()

    await act(async () => {
      resolveA({
        endpoints: [{
          id: 'a', name: 'Profile A Relay', base_url: 'https://a.example/v1', model: 'a-model',
          models: ['a-model'], discover_models: true, is_current: true, has_api_key: false, source: 'providers'
        }]
      })
      await requestA
    })

    expect(screen.queryByText('Profile A Relay')).toBeNull()
    expect(screen.getByText('Profile B Relay')).toBeTruthy()
  })

  it('reloads and clears old relay state on a live active-profile switch', async () => {
    getCustomEndpoints
      .mockResolvedValueOnce({
        endpoints: [{
          id: 'a', name: 'Active A Relay', base_url: 'https://a.example/v1', model: 'a-model',
          models: ['a-model'], discover_models: true, is_current: true, has_api_key: false, source: 'providers'
        }]
      })
      .mockResolvedValueOnce({ endpoints: [] })
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(<CustomEndpointsSettings />)

    expect(await screen.findByText('Active A Relay')).toBeTruthy()
    expect(profileSwitchHandler).toBeTypeOf('function')
    act(() => profileSwitchHandler?.())

    await waitFor(() => expect(getCustomEndpoints).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText('Active A Relay')).toBeNull())
    expect(await screen.findByRole('button', { name: 'Add model service' })).toBeTruthy()
    expect(screen.queryByPlaceholderText('My model service')).toBeNull()
  })
  it('discovers candidates from the unsaved form without silently choosing a default', async () => {
    vi.mocked(validateCustomEndpoint).mockResolvedValue({
      ok: true,
      reachable: true,
      message: '',
      models: ['relay-model']
    })
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <CustomEndpointsSettings />
      </I18nProvider>
    )
    fireEvent.click(await screen.findByRole('button', { name: '添加模型服务' }))
    fireEvent.change(screen.getByPlaceholderText('http://127.0.0.1:8081/v1'), {
      target: { value: 'https://relay.example/v1' }
    })
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    await waitFor(() => expect(validateCustomEndpoint).toHaveBeenCalled())
    await waitFor(() => expect(document.querySelector('datalist option[value="relay-model"]')).toBeTruthy())
    expect(screen.getByPlaceholderText('gpt-5.4')).toHaveProperty('value', '')
    fireEvent.click(screen.getByRole('button', { name: 'relay-model' }))
    expect(screen.getByPlaceholderText('gpt-5.4')).toHaveProperty('value', 'relay-model')
    fireEvent.change(screen.getByPlaceholderText('http://127.0.0.1:8081/v1'), {
      target: { value: 'https://other.example/v1' }
    })
    expect(screen.queryByRole('button', { name: 'relay-model' })).toBeNull()
  })

  it('allows another connection test after editing during a pending probe', async () => {
    let resolveProbe!: (result: { ok: boolean; reachable: boolean; message: string; models: string[] }) => void
    vi.mocked(validateCustomEndpoint).mockImplementationOnce(() => new Promise(resolve => {resolveProbe = resolve}))
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(<CustomEndpointsSettings />)
    await screen.findByText('Add model service')
    fireEvent.change(screen.getByPlaceholderText('http://127.0.0.1:8081/v1'), {
      target: { value: 'https://relay.example/v1' }
    })
    const testButton = screen.getByRole('button', { name: 'Test connection' })
    fireEvent.click(testButton)
    await waitFor(() => expect(validateCustomEndpoint).toHaveBeenCalledTimes(1))
    expect(testButton).toHaveProperty('disabled', true)

    fireEvent.change(screen.getByPlaceholderText('My model service'), { target: { value: 'Renamed relay' } })
    expect(testButton).toHaveProperty('disabled', false)
    await act(async () => {
      resolveProbe({ ok: true, reachable: true, message: '', models: ['old-model'] })
    })
    expect(testButton).toHaveProperty('disabled', false)
    expect(screen.queryByRole('button', { name: 'old-model' })).toBeNull()
    await act(async () => { fireEvent.click(testButton) })
    expect(validateCustomEndpoint).toHaveBeenCalledTimes(2)
  })

  it('shows a plain-language Chinese setup first and keeps technical fields behind advanced options', async () => {
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')

    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <CustomEndpointsSettings />
      </I18nProvider>
    )
    await waitFor(() => expect(getCustomEndpoints).toHaveBeenCalled())

    fireEvent.click(await screen.findByRole('button', { name: '添加模型服务' }))
    expect(await screen.findByText('服务名称')).toBeTruthy()
    expect(screen.getByText('服务地址')).toBeTruthy()
    expect(screen.getByText('默认模型')).toBeTruthy()
    expect(screen.getByText('API 密钥')).toBeTruthy()
    expect(screen.getByText('设为新对话默认服务')).toBeTruthy()
    expect(screen.getByRole('button', { name: '测试连接' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '保存服务' })).toBeTruthy()

    expect(screen.queryByText('服务标识')).toBeNull()
    expect(screen.queryByText('上下文长度')).toBeNull()
    expect(screen.queryByText('Provider ID')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '高级选项' }))

    expect(screen.getByText('服务标识')).toBeTruthy()
    expect(screen.getByText('上下文长度')).toBeTruthy()
    expect(screen.getByText('自动读取模型列表')).toBeTruthy()
    expect(screen.getByText('仅用于内部识别，通常可以留空。')).toBeTruthy()
    expect(screen.getByRole('button', { name: '收起高级选项' })).toBeTruthy()
  })

  it('adds a second service without turning the existing service into an edit target', async () => {
    const existing = {
      id: 'alpha',
      name: 'Alpha',
      base_url: 'https://alpha.example/v1',
      model: 'alpha-model',
      models: ['alpha-model'],
      discover_models: true,
      is_current: true,
      has_api_key: false,
      source: 'providers'
    }

    const added = {
      id: 'beta',
      name: 'Beta',
      base_url: 'https://beta.example/v1',
      model: 'beta-model',
      models: ['beta-model'],
      discover_models: true,
      is_current: false,
      has_api_key: false,
      source: 'providers'
    }

    getCustomEndpoints.mockResolvedValueOnce({ endpoints: [existing] })
    vi.mocked(saveCustomEndpoint).mockResolvedValue({
      id: 'beta',
      current: { provider: 'alpha', model: 'alpha-model', base_url: 'https://alpha.example/v1' },
      endpoints: [existing, added]
    })

    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(<CustomEndpointsSettings />)

    expect(await screen.findByText('Alpha')).toBeTruthy()
    expect(screen.queryByPlaceholderText('My model service')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Add model service' }))
    fireEvent.change(screen.getByPlaceholderText('My model service'), { target: { value: 'Beta' } })
    fireEvent.change(screen.getByPlaceholderText('http://127.0.0.1:8081/v1'), {
      target: { value: 'https://beta.example/v1' }
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-5.4'), { target: { value: 'beta-model' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save service' }))

    await waitFor(() =>
      expect(saveCustomEndpoint).toHaveBeenCalledWith(
        expect.objectContaining({
          id: undefined,
          name: 'Beta',
          base_url: 'https://beta.example/v1',
          model: 'beta-model',
          create_only: true,
          make_default: false
        }),
        undefined
      )
    )
    await waitFor(() => expect(screen.queryByPlaceholderText('My model service')).toBeNull())
    expect(screen.getByText('Alpha')).toBeTruthy()
    expect(screen.getByText('Beta')).toBeTruthy()
  })

  it('makes the first service the default when the profile has no main model yet', async () => {
    // First run: onboarding sends the user here to "add a model API". If that
    // first service did not become the default, chat would still have no model.
    getCustomEndpoints.mockResolvedValueOnce({ current: { provider: '', model: '', base_url: '' }, endpoints: [] })
    vi.mocked(saveCustomEndpoint).mockResolvedValue({
      id: 'relay',
      current: { provider: 'relay', model: 'relay-model', base_url: 'https://relay.example/v1' },
      endpoints: [
        {
          id: 'relay',
          name: 'Relay',
          base_url: 'https://relay.example/v1',
          model: 'relay-model',
          models: ['relay-model'],
          discover_models: true,
          is_current: true,
          has_api_key: false,
          source: 'providers'
        }
      ]
    })

    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <CustomEndpointsSettings />
      </I18nProvider>
    )

    fireEvent.click(await screen.findByRole('button', { name: '添加模型服务' }))
    expect(screen.getByRole('checkbox', { name: '设为新对话默认服务' }).getAttribute('aria-checked')).toBe('true')

    fireEvent.change(screen.getByPlaceholderText('我的模型服务'), { target: { value: 'Relay' } })
    fireEvent.change(screen.getByPlaceholderText('http://127.0.0.1:8081/v1'), {
      target: { value: 'https://relay.example/v1' }
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-5.4'), { target: { value: 'relay-model' } })
    fireEvent.click(screen.getByRole('button', { name: '保存服务' }))

    await waitFor(() =>
      expect(saveCustomEndpoint).toHaveBeenCalledWith(
        expect.objectContaining({ create_only: true, make_default: true, name: 'Relay' }),
        undefined
      )
    )
  })

  it('explains a name collision on Add in place, without a raw server error', async () => {
    const existing = {
      id: 'relay',
      name: 'Relay',
      base_url: 'https://a.example/v1',
      model: 'model-a',
      models: ['model-a'],
      discover_models: true,
      is_current: true,
      has_api_key: false,
      source: 'providers'
    }

    getCustomEndpoints.mockResolvedValueOnce({
      current: { provider: 'relay', model: 'model-a', base_url: 'https://a.example/v1' },
      endpoints: [existing]
    })
    vi.mocked(saveCustomEndpoint).mockRejectedValue(
      new Error('409: {"detail":"model service already exists; edit the existing service instead"}')
    )

    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <CustomEndpointsSettings />
      </I18nProvider>
    )

    fireEvent.click(await screen.findByRole('button', { name: '添加模型服务' }))
    fireEvent.change(screen.getByPlaceholderText('我的模型服务'), { target: { value: 'Relay' } })
    fireEvent.change(screen.getByPlaceholderText('http://127.0.0.1:8081/v1'), {
      target: { value: 'https://b.example/v1' }
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-5.4'), { target: { value: 'model-b' } })
    fireEvent.click(screen.getByRole('button', { name: '保存服务' }))

    expect(await screen.findByText('已经有同名的模型服务。请在列表里编辑它，或换一个名称。')).toBeTruthy()
    // The editor stays open with the user's input so they can rename.
    expect(screen.getByPlaceholderText('我的模型服务')).toHaveProperty('value', 'Relay')
    expect(screen.queryByText(/model service already exists/)).toBeNull()
  })

  it('edits only the service explicitly opened for editing', async () => {
    const existing = {
      id: 'alpha',
      name: 'Alpha',
      base_url: 'https://alpha.example/v1',
      model: 'alpha-model',
      models: ['alpha-model'],
      discover_models: true,
      is_current: false,
      has_api_key: false,
      source: 'providers'
    }

    const updated = { ...existing, model: 'alpha-model-2', models: ['alpha-model', 'alpha-model-2'] }
    getCustomEndpoints.mockResolvedValueOnce({ endpoints: [existing] })
    vi.mocked(saveCustomEndpoint).mockResolvedValue({
      id: 'alpha',
      current: { provider: '', model: '', base_url: '' },
      endpoints: [updated]
    })

    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(<CustomEndpointsSettings />)

    expect(await screen.findByText('Alpha')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Edit model service' }))
    fireEvent.change(screen.getByPlaceholderText('gpt-5.4'), { target: { value: 'alpha-model-2' } })

    fireEvent.click(screen.getByRole('button', { name: 'Advanced options' }))
    expect(screen.getByDisplayValue('alpha')).toHaveProperty('disabled', true)

    fireEvent.click(screen.getByRole('button', { name: 'Save service' }))

    await waitFor(() =>
      expect(saveCustomEndpoint).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'alpha',
          model: 'alpha-model-2',
          create_only: false
        }),
        undefined
      )
    )
  })

  it('saves the selected discovered model and returns to the service inventory', async () => {
    vi.mocked(validateCustomEndpoint).mockResolvedValue({
      ok: true,
      reachable: true,
      message: '',
      models: ['relay-model']
    })
    vi.mocked(saveCustomEndpoint).mockResolvedValue({
      id: 'custom:relay',
      current: { provider: '', model: '', base_url: '' },
      endpoints: [
        {
          id: 'custom:relay',
          name: 'Relay',
          base_url: 'https://relay.example/v1',
          model: 'relay-model',
          models: ['relay-model'],
          discover_models: true,
          is_current: false,
          has_api_key: true,
          source: 'managed'
        }
      ]
    })
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(<CustomEndpointsSettings />)
    fireEvent.click(await screen.findByRole('button', { name: 'Add model service' }))
    fireEvent.change(screen.getByPlaceholderText('My model service'), { target: { value: 'Relay' } })
    fireEvent.change(screen.getByPlaceholderText('http://127.0.0.1:8081/v1'), {
      target: { value: 'https://relay.example/v1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    fireEvent.click(await screen.findByRole('button', { name: 'relay-model' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save service' }))
    await waitFor(() =>
      expect(saveCustomEndpoint).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Relay',
          base_url: 'https://relay.example/v1',
          model: 'relay-model',
          models: ['relay-model'],
          create_only: true
        }),
        undefined
      )
    )
    await waitFor(() => expect(screen.queryByPlaceholderText('Leave blank to keep the current key')).toBeNull())
    expect(screen.getByText('Relay')).toBeTruthy()
  })
})
