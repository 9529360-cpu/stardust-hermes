import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  connectionScoped: vi.fn(() => ({ connectionId: 'remote-a' })),
  getApiRequestConnection: vi.fn(() => 'remote-a'),
  hermesApi: vi.fn(),
  profileScoped: vi.fn(() => ({ profile: 'worker' })),
  STARTUP_REQUEST_TIMEOUT_MS: 30_000
}))

const client = await import('./client')

const {
  acceptCronSuggestion,
  deleteCronJob,
  dismissCronSuggestion,
  getCronJob,
  getCronJobRuns,
  getCronSuggestions,
  pauseCronJob,
  resumeCronJob,
  triggerCronJob,
  updateCronJob
} = await import('./cron')

const hermesApi = vi.mocked(client.hermesApi)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('cron job owner-scoped API', () => {
  const jobId = 'duplicate/job?key=1'
  const owner = 'owner /east&west'
  const jobPath = '/api/cron/jobs/duplicate%2Fjob%3Fkey%3D1'
  const ownerQuery = '?profile=owner%20%2Feast%26west'

  it('routes detail and run history through the explicit owner, not the active profile', async () => {
    hermesApi.mockResolvedValueOnce({ id: jobId } as never).mockResolvedValueOnce({ runs: [] } as never)

    await getCronJob(jobId, owner)
    await getCronJobRuns(jobId, 7, owner)

    expect(hermesApi.mock.calls[0][0]).toMatchObject({
      connectionId: 'remote-a',
      profile: 'worker',
      path: `${jobPath}${ownerQuery}`
    })
    expect(hermesApi.mock.calls[1][0]).toMatchObject({
      connectionId: 'remote-a',
      profile: 'worker',
      path: `${jobPath}/runs?limit=7&profile=owner%20%2Feast%26west`
    })
  })

  it('does not conflate duplicate job IDs across two explicit owners', async () => {
    hermesApi.mockResolvedValue({ id: jobId } as never)

    await getCronJob(jobId, 'worker')
    await getCronJob(jobId, owner)
    await deleteCronJob(jobId, owner)

    expect(hermesApi.mock.calls.map(([request]) => request.path)).toEqual([
      `${jobPath}?profile=worker`,
      `${jobPath}${ownerQuery}`,
      `${jobPath}${ownerQuery}`
    ])
    expect(hermesApi.mock.calls[2][0]).toMatchObject({ method: 'DELETE', profile: 'worker' })
  })

  it('keeps every mutation pinned to the owner even when another profile has the same job ID', async () => {
    hermesApi.mockResolvedValue({ id: jobId } as never)
    const updates = { name: 'Updated duplicate' }

    await updateCronJob(jobId, updates, owner)
    await pauseCronJob(jobId, owner)
    await resumeCronJob(jobId, owner)
    await triggerCronJob(jobId, owner)
    await deleteCronJob(jobId, owner)

    expect(hermesApi.mock.calls).toHaveLength(5)
    expect(hermesApi.mock.calls.map(([request]) => [request.method, request.path])).toEqual([
      ['PUT', `${jobPath}${ownerQuery}`],
      ['POST', `${jobPath}/pause${ownerQuery}`],
      ['POST', `${jobPath}/resume${ownerQuery}`],
      ['POST', `${jobPath}/trigger${ownerQuery}`],
      ['DELETE', `${jobPath}${ownerQuery}`]
    ])

    for (const [request] of hermesApi.mock.calls) {
      expect(request).toMatchObject({ connectionId: 'remote-a', profile: 'worker' })
    }

    expect(hermesApi.mock.calls[0][0]).toMatchObject({ body: { updates } })
  })
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
