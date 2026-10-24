// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProfileScope } from '@/hermes'
import type { McpCatalogEntry } from '@/types/hermes'

const addMcpServer = vi.fn()
const installMcpCatalogEntry = vi.fn()
const getMcpCatalogEntry = vi.fn()
const getActionStatus = vi.fn()
const testMcpServer = vi.fn()
const completeMcpDesktopOAuth = vi.fn()

vi.mock('@/api/mcp', () => ({
  addMcpServer: (...args: unknown[]) => addMcpServer(...args),
  getMcpCatalogEntry: (...args: unknown[]) => getMcpCatalogEntry(...args),
  installMcpCatalogEntry: (...args: unknown[]) => installMcpCatalogEntry(...args)
}))
vi.mock('@/api/system', () => ({ getActionStatus: (...args: unknown[]) => getActionStatus(...args) }))
vi.mock('@/hermes', () => ({
  profileScopeKey: () => 'default',
  testMcpServer: (...args: unknown[]) => testMcpServer(...args)
}))
vi.mock('@/lib/mcp-dashboard-oauth', () => ({
  McpOAuthCancelled: class extends Error {},
  completeMcpDesktopOAuth: (...args: unknown[]) => completeMcpDesktopOAuth(...args)
}))
vi.mock('@/lib/mcp-probe-cache', () => ({
  probeCache: { get: vi.fn(() => undefined), set: vi.fn() },
  probeKey: () => 'probe'
}))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))
const useHermesConfigRecord = vi.fn((profile?: ProfileScope) => ({ data: profile ? undefined : { mcp_servers: {}, connectors: { unified_dialog: true } } }))
vi.mock('../hooks/use-config-record', () => ({
  useHermesConfigRecord: (profile?: ProfileScope) => useHermesConfigRecord(profile)
}))

import { I18nProvider } from '@/i18n/context'

import { ConnectorAddDialog, CustomConnectorAddDialog } from './connector-add-dialog'

const baseEntry = (overrides: Partial<McpCatalogEntry> = {}): McpCatalogEntry =>
  ({
    name: 'filesystem',
    description: 'Official filesystem server',
    source: 'https://github.com/modelcontextprotocol/servers',
    transport: 'stdio',
    auth_type: 'none',
    publisher: 'Model Context Protocol (official)',
    permissions: ['Read files'],
    runtime: 'local',
    required_env: [],
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem@2026.7.10', 'D:/tmp'],
    url: null,
    install_url: null,
    install_ref: null,
    bootstrap: [],
    default_enabled: null,
    post_install: '',
    suggest: null,
    needs_install: false,
    installed: false,
    enabled: false,
    ...overrides
  }) as McpCatalogEntry

beforeEach(() => {
  vi.clearAllMocks()
  useHermesConfigRecord.mockReturnValue({ data: { mcp_servers: {}, connectors: { unified_dialog: true } } })
  getMcpCatalogEntry.mockResolvedValue(baseEntry())
  installMcpCatalogEntry.mockResolvedValue({ ok: true, background: false })
  testMcpServer.mockResolvedValue({ ok: true, tools: [{ name: 'read_file', description: '' }] })
})

afterEach(cleanup)

// The dialog copy asserted below is the product's Chinese labels (spec), so
// wrap mounts in the zh locale like the other copy-pinned specs.
const zh = (ui: React.ReactElement) => <I18nProvider configClient={null} initialLocale="zh-CN">{ui}</I18nProvider>

const mountCatalogDialog = (entry: McpCatalogEntry | null) =>
  render(zh(<ConnectorAddDialog entry={entry} onClose={() => undefined} onConnected={() => undefined} />))

describe('ConnectorAddDialog (CX5)', () => {
  it('shows publisher, permissions and runtime location in Chinese', async () => {
    mountCatalogDialog(baseEntry())
    await waitFor(() => expect(screen.getByText('发布者')).toBeTruthy())
    expect(screen.getByText('Model Context Protocol (official)')).toBeTruthy()
    expect(screen.getByText('所需权限')).toBeTruthy()
    expect(screen.getByText('运行位置（本地/远程）')).toBeTruthy()
    expect(screen.getAllByText(/本地/).length).toBeGreaterThan(0)
    expect(screen.getByText('Read files')).toBeTruthy()
  })

  it('connects a no-credential catalog entry through the install path and probes', async () => {
    mountCatalogDialog(baseEntry())
    await waitFor(() => expect(screen.getByText('Read files')).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '连接' }))
    })
    await waitFor(() => expect(installMcpCatalogEntry).toHaveBeenCalledWith('filesystem', {}, undefined))
    await waitFor(() => expect(testMcpServer).toHaveBeenCalledWith('filesystem', undefined))
  })

  it('requires the API key before connecting an api_key entry and never echoes it', async () => {
    const entry = baseEntry({
      name: 'n8n',
      transport: 'http',
      url: 'https://mcp.example.com/mcp',
      auth_type: 'api_key',
      runtime: 'remote',
      required_env: [{ name: 'N8N_API_KEY', prompt: 'n8n API key', required: true }]
    })

    getMcpCatalogEntry.mockResolvedValue(entry)
    mountCatalogDialog(entry)
    await waitFor(() => expect(screen.getByText('n8n API key')).toBeTruthy())
    const field = document.querySelector('[data-slot="connector-secret-N8N_API_KEY"]') as HTMLInputElement
    expect(field?.type).toBe('password')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '连接' }))
    })
    // Chinese error, and the error text never contains a value.
    await waitFor(() => expect(screen.getByText('请输入此服务的 API 密钥。')).toBeTruthy())
  })

  it('shows the duplicate-name state for an already-configured entry and disables connect', async () => {
    const entry = baseEntry({ name: 'deepwiki', transport: 'http', url: 'https://mcp.deepwiki.com/mcp', runtime: 'remote' })
    getMcpCatalogEntry.mockResolvedValue(entry)
    useHermesConfigRecord.mockReturnValue({
      data: { mcp_servers: { deepwiki: { url: 'https://mcp.deepwiki.com/mcp' } } }
    } as never)
    mountCatalogDialog(entry)
    await waitFor(() => expect(screen.getByText(/已存在同名连接 deepwiki/)).toBeTruthy())
    expect((screen.getByRole('button', { name: '连接' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('CustomConnectorAddDialog (CX5)', () => {
  const mountCustom = () =>
    render(zh(<CustomConnectorAddDialog onClose={() => undefined} onConnected={() => undefined} open />))

  it('demands a name, then a URL or command (missing-command edge case)', async () => {
    mountCustom()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '连接' }))
    })
    await waitFor(() => expect(screen.getByText('请输入连接名称。')).toBeTruthy())

    await act(async () => {
      fireEvent.change(document.querySelector('[data-slot="custom-name"]')!, { target: { value: 'my-mcp' } })
      fireEvent.click(screen.getByRole('button', { name: '连接' }))
    })
    await waitFor(() => expect(screen.getByText('请输入服务 URL 或命令。')).toBeTruthy())
  })

  it('adds a custom URL service via the CX3 bearer path without echoing the key', async () => {
    addMcpServer.mockResolvedValue({ name: 'my-mcp' })
    mountCustom()
    fireEvent.change(document.querySelector('[data-slot="custom-name"]')!, { target: { value: 'my-mcp' } })
    fireEvent.change(document.querySelector('[data-slot="custom-url"]')!, {
      target: { value: 'https://example.com/mcp' }
    })
    fireEvent.click(document.querySelector('[data-slot="custom-use-api-key"]')!)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '连接' }))
    })
    // API key required — Chinese error, no value echoed anywhere.
    await waitFor(() => expect(screen.getByText('请输入此服务的 API 密钥。')).toBeTruthy())

    const keyField = document.querySelector('[data-slot="custom-api-key"]') as HTMLInputElement
    expect(keyField.type).toBe('password')
    fireEvent.change(keyField, { target: { value: 'sk-secret-value-123' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '连接' }))
    })
    await waitFor(() =>
      expect(addMcpServer).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'my-mcp', url: 'https://example.com/mcp', auth: 'header', bearer_token: 'sk-secret-value-123' }),
        undefined
      )
    )
  })

  it('surfaces a backend duplicate-name rejection as-is (no secret in the message)', async () => {
    addMcpServer.mockRejectedValue(new Error("Server 'my-mcp' already exists"))
    mountCustom()
    fireEvent.change(document.querySelector('[data-slot="custom-name"]')!, { target: { value: 'my-mcp' } })
    fireEvent.change(document.querySelector('[data-slot="custom-url"]')!, { target: { value: 'https://example.com/mcp' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '连接' }))
    })
    await waitFor(() => expect(screen.getByText("Server 'my-mcp' already exists")).toBeTruthy())
  })
})
