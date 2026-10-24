import { connectionScoped, profileScoped } from '@/api/client'
import { hermesApi } from '@/hermes'

const stages = [
  'listen_start', 'eos', 'stt_request', 'stt_result', 'llm_first_token',
  'tts_request', 'network_first_audio_byte', 'audio_context_scheduled', 'playback_start', 'turn_end'
] as const
export type VoiceStage = typeof stages[number]
type Observation = { timestamp_ms: number | null; reason: string | null }

/** One explicit owner per utterance; never contains audio, transcript or credentials. */
export class VoiceTurnTrace {
  readonly turnId = crypto.randomUUID()
  private observations: Partial<Record<VoiceStage, Observation>> = {}
  private ended = false
  private readonly origin = performance.now()

  private readonly enabled: Promise<boolean>

  private constructor(private scope: ReturnType<typeof profileScoped> & ReturnType<typeof connectionScoped>) {
    this.enabled = hermesApi<{ enabled: boolean }>({ ...scope, path: '/api/audio/trace-config' })
      .then(config => config.enabled === true).catch(() => false)
  }

  static begin(): VoiceTurnTrace {
    return new VoiceTurnTrace({ ...connectionScoped(), ...profileScoped() })
  }

  mark(stage: VoiceStage) {
    if (!this.ended && !this.observations[stage]) {
      this.observations[stage] = { timestamp_ms: performance.now() - this.origin, reason: null }
    }
  }

  finish(reason = 'not_observed') {
    if (this.ended) {
      return
    }
    this.mark('turn_end')
    this.ended = true
    const table = Object.fromEntries(stages.map(stage => [stage,
      this.observations[stage] ?? { timestamp_ms: null, reason }
    ]))
    void this.enabled.then(enabled => {
      if (!enabled) {
        return
      }
      return hermesApi({ ...this.scope, path: '/api/audio/trace', method: 'POST', body: {
        turn_id: this.turnId, stages: table
      } })
    }).catch(() => undefined)
  }
}
