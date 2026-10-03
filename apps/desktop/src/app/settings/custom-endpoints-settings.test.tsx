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
    expect(await screen.findByText('Add model service')).toBeTruthy()
    expect(screen.getByPlaceholderText('My model service')).toHaveProperty('value', '')
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
    await screen.findByText('添加模型服务')
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

  it('shows a plain-language Chinese setup first and keeps technical fields behind advanced options', async () => {
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')

    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <CustomEndpointsSettings />
      </I18nProvider>
    )
    await waitFor(() => expect(getCustomEndpoints).toHaveBeenCalled())

    expect(await screen.findByText('添加模型服务')).toBeTruthy()
    expect(screen.getByText('服务名称')).toBeTruthy()
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

  it('saves the selected discovered model and clears the key field on edit', async () => {
    vi.mocked(validateCustomEndpoint).mockResolvedValue({
      ok: true,
      reachable: true,
      message: '',
      models: ['relay-model']
    })
    vi.mocked(saveCustomEndpoint).mockResolvedValue({
      id: 'custom:relay',
      current: { provider: 'custom:relay', model: 'relay-model', base_url: 'https://relay.example/v1' },
      endpoints: [
        {
          id: 'custom:relay',
          name: 'Relay',
          base_url: 'https://relay.example/v1',
          model: 'relay-model',
          models: ['relay-model'],
          discover_models: true,
          is_current: true,
          has_api_key: true,
          source: 'managed'
        }
      ]
    })
    const { CustomEndpointsSettings } = await import('./custom-endpoints-settings')
    render(<CustomEndpointsSettings />)
    await screen.findByText('Add model service')
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
          models: ['relay-model']
        }),
        undefined
      )
    )
    await waitFor(() =>
      expect(screen.getByPlaceholderText('Leave blank to keep the current key')).toHaveProperty('value', '')
    )
  })
})
