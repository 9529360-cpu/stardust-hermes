import type { MemoryEntry, MemoryListResult, MemoryMutationResult } from '@hermes/shared'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'

export type MemoryTarget = 'memory' | 'user'
export type MemoryNotesStatus = 'loading' | 'ready' | 'error'
/** `changed`: the backend refused a forget because the displayed note moved. `saveFailed`: a write did not land. */
export type MemoryNotice = 'changed' | 'saveFailed'

/**
 * Remembered notes for both memory targets, with forget and remember writes.
 *
 * Forget always sends the displayed text as `expected_text` next to the index, so a stale list
 * cannot delete a different note. Every write is followed by an authoritative reload.
 */
export function useMemoryNotes() {
  const { requestGateway } = useGatewayRequest()
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [targets, setTargets] = useState<MemoryListResult['targets']>({})
  const [status, setStatus] = useState<MemoryNotesStatus>('loading')
  const [notice, setNotice] = useState<MemoryNotice | null>(null)
  const loadGeneration = useRef(0)

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current

    try {
      const result = await requestGateway<MemoryListResult>('memory.list', { target: 'both' })

      if (generation !== loadGeneration.current) {
        return
      }

      setEntries(result.entries ?? [])
      setTargets(result.targets ?? {})
      setStatus('ready')
    } catch {
      if (generation === loadGeneration.current) {
        setStatus('error')
      }
    }
  }, [requestGateway])

  /** Resolves true only when the backend accepted the note, so the caller keeps the draft on failure. */
  const remember = useCallback(
    async (target: MemoryTarget, content: string): Promise<boolean> => {
      setNotice(null)
      let saved = false

      try {
        const result = await requestGateway<MemoryMutationResult>('memory.remember', { target, content })

        saved = result.success
      } catch {
        saved = false
      }

      if (!saved) {
        setNotice('saveFailed')
      }

      await load()

      return saved
    },
    [load, requestGateway]
  )

  const forget = useCallback(
    async (entry: MemoryEntry) => {
      setNotice(null)

      try {
        const result = await requestGateway<MemoryMutationResult>('memory.forget', {
          target: entry.target,
          index: entry.index,
          expected_text: entry.text
        })

        if (!result.success) {
          setNotice('changed')
        }
      } catch {
        setNotice('saveFailed')
      }

      await load()
    },
    [load, requestGateway]
  )

  useEffect(() => {
    void load()
  }, [load])

  return { entries, targets, status, notice, reload: load, remember, forget }
}
