import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

const getCustomEndpoints = vi.fn()

vi.mock('@/hermes', () => ({
  activateCustomEndpoint: vi.fn(),
  deleteCustomEndpoint: vi.fn(),
  getCustomEndpoints: () => getCustomEndpoints(),
  saveCustomEndpoint: vi.fn(),
  validateCustomEndpoint: vi.fn()
}))

beforeEach(() => {
  getCustomEndpoints.mockResolvedValue({ endpoints: [] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('CustomEndpointsSettings', () => {
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
})
