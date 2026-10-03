import { beforeEach, describe, expect, it } from 'vitest'

import {
  $cronJobs,
  $cronJobsScope,
  beginCronJobsRequest,
  commitCronJobsRequest,
  invalidateCronJobsRequestIfCurrent,
  setCronJobs,
  updateCronJobs
} from './cron'

const oldJob = { id: 'old' } as never
const newJob = { id: 'new' } as never

describe('cron jobs request fencing', () => {
  beforeEach(() => {
    setCronJobs([])
  })

  it('rejects an older refresh after a newer refresh commits', () => {
    const older = beginCronJobsRequest('all')
    const newer = beginCronJobsRequest('all')

    expect(commitCronJobsRequest(newer, [newJob])).toBe(true)
    expect(commitCronJobsRequest(older, [oldJob])).toBe(false)
    expect($cronJobs.get()).toEqual([newJob])
  })

  it('rejects a refresh from the previous profile scope', () => {
    const work = beginCronJobsRequest('work')

    beginCronJobsRequest('personal')

    expect(commitCronJobsRequest(work, [oldJob])).toBe(false)
    expect($cronJobs.get()).toEqual([])
  })

  it('keeps cache ownership after a failed read and updates it on another scope’s success', () => {
    const first = beginCronJobsRequest('connection-a\u0000all')
    commitCronJobsRequest(first, [oldJob])
    expect($cronJobsScope.get()).toBe('connection-a\u0000all')

    beginCronJobsRequest('connection-b\u0000work')
    expect($cronJobsScope.get()).toBe('connection-a\u0000all')
    commitCronJobsRequest(beginCronJobsRequest('connection-b\u0000work'), [newJob])
    expect($cronJobsScope.get()).toBe('connection-b\u0000work')
  })

  it('closing an older owner does not invalidate a newer read', () => {
    const overlay = beginCronJobsRequest('all')
    const sidebar = beginCronJobsRequest('all')

    expect(invalidateCronJobsRequestIfCurrent(overlay)).toBe(false)
    expect(commitCronJobsRequest(sidebar, [newJob])).toBe(true)
    expect($cronJobs.get()).toEqual([newJob])
  })

  it('rejects an in-flight poll after a local mutation', () => {
    const poll = beginCronJobsRequest('all')

    updateCronJobs(() => [newJob])

    expect(commitCronJobsRequest(poll, [oldJob])).toBe(false)
    expect($cronJobs.get()).toEqual([newJob])
  })
})
