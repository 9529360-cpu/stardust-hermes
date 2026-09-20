import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  connectionScoped: vi.fn(() => ({ connectionId: 'remote-a' })),
  hermesApi: vi.fn(),
  profileScoped: vi.fn(() => ({ profile: 'worker' })),
  STARTUP_REQUEST_TIMEOUT_MS: 30_000
}))

const client = await import('./client')
const { acceptCronSuggestion, dismissCronSuggestion, getCronSuggestions } = await import('./cron')

const hermesApi = vi.mocked(client.hermesApi)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('cron suggestion review API', () => {
  it('lists pending suggestions on the selected profile and connection', async () => {
    hermesApi.mockResolvedValue({
      suggestions: [
        {
          id: 's-1',
          title: 'Important-mail monitor',
          description: 'Only urgent mail',
          source: 'integration',
          blueprint_key: 'important-mail',
          job_spec: { schedule: '*/30 * * * *' }
        }
      ]
    } as never)

    const result = await getCronSuggestions('work profile')

    expect(result).toHaveLength(1)
    expect(hermesApi.mock.calls[0][0]).toMatchObject({
      connectionId: 'remote-a',
      profile: 'worker',
      path: '/api/cron/suggestions?profile=work%20profile'
    })
  })

  it('accepts with only the durable conversation id in the body', async () => {
    hermesApi.mockResolvedValue({ id: 'job-1', enabled: true } as never)

    await acceptCronSuggestion('s/1', 'worker', 'desktop-stored-session')

    expect(hermesApi.mock.calls[0][0]).toMatchObject({
      connectionId: 'remote-a',
      method: 'POST',
      path: '/api/cron/suggestions/s%2F1/accept?profile=worker',
      body: { session_id: 'desktop-stored-session' }
    })
    expect(hermesApi.mock.calls[0][0]).not.toHaveProperty('body.local_session_origin')
  })

  it('dismisses in the same profile scope without creating work', async () => {
    hermesApi.mockResolvedValue({ id: 's-2', ok: true } as never)

    await dismissCronSuggestion('s-2', 'worker')

    expect(hermesApi.mock.calls[0][0]).toMatchObject({
      connectionId: 'remote-a',
      method: 'POST',
      path: '/api/cron/suggestions/s-2/dismiss?profile=worker'
    })
    expect(hermesApi.mock.calls[0][0]).not.toHaveProperty('body')
  })
})
