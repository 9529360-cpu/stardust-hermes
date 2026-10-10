/** Unit words for {@link formatDurationLabel}. The `assistant.thread` bundle satisfies this. */
export interface DurationUnits {
  durationSeconds: (seconds: number) => string
  durationMinutes: (minutes: number, seconds: number) => string
  durationHours: (hours: number, minutes: number) => string
}

/**
 * A finished duration in words for the reader: seconds alone under a minute,
 * then minutes with seconds, then hours with minutes. Fractions are floored, so
 * a block that ran 12.9 seconds reads 12, the same figure a timer showing it
 * would have stopped on. Negative and non-finite input reads as zero.
 */
export function formatDurationLabel(totalSeconds: number, units: DurationUnits): string {
  const whole = Number.isFinite(totalSeconds) ? Math.max(0, Math.floor(totalSeconds)) : 0

  if (whole < 60) {
    return units.durationSeconds(whole)
  }

  if (whole < 3600) {
    return units.durationMinutes(Math.floor(whole / 60), whole % 60)
  }

  return units.durationHours(Math.floor(whole / 3600), Math.floor((whole % 3600) / 60))
}
