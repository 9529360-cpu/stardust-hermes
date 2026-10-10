import { describe, expect, it } from 'vitest'

import { type DurationUnits, formatDurationLabel } from './duration-label'

const units: DurationUnits = {
  durationSeconds: seconds => `${seconds}s`,
  durationMinutes: (minutes, seconds) => `${minutes}m ${seconds}s`,
  durationHours: (hours, minutes) => `${hours}h ${minutes}m`
}

describe('formatDurationLabel', () => {
  it('reads whole seconds under a minute', () => {
    expect(formatDurationLabel(0, units)).toBe('0s')
    expect(formatDurationLabel(12, units)).toBe('12s')
    expect(formatDurationLabel(59, units)).toBe('59s')
  })

  it('floors fractions, matching a timer that stopped on the same moment', () => {
    expect(formatDurationLabel(12.9, units)).toBe('12s')
    expect(formatDurationLabel(59.99, units)).toBe('59s')
  })

  it('reads minutes with seconds from a minute up to an hour', () => {
    expect(formatDurationLabel(60, units)).toBe('1m 0s')
    expect(formatDurationLabel(65, units)).toBe('1m 5s')
    expect(formatDurationLabel(3_599, units)).toBe('59m 59s')
  })

  it('reads hours with minutes from an hour up, dropping the seconds', () => {
    expect(formatDurationLabel(3_600, units)).toBe('1h 0m')
    expect(formatDurationLabel(3_725, units)).toBe('1h 2m')
  })

  it('reads negative and non-finite input as zero rather than a bogus figure', () => {
    expect(formatDurationLabel(-4, units)).toBe('0s')
    expect(formatDurationLabel(Number.NaN, units)).toBe('0s')
    expect(formatDurationLabel(Number.POSITIVE_INFINITY, units)).toBe('0s')
  })
})
