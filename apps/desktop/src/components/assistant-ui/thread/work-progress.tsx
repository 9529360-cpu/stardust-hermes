import { ThreadPrimitive, useAuiState } from '@assistant-ui/react'
import { type ComponentProps, type FC, useMemo } from 'react'

import { SCAFFOLD_LABEL_CLASS } from '@/components/chat/scaffold-row'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { useI18n } from '@/i18n'

type MessageComponents = ComponentProps<typeof ThreadPrimitive.MessageByIndex>['components']
type WorkItem = { indices: number[]; kind: 'progress' } | { index: number; kind: 'message' }

/** Only sealed, text-only commentary from a turn that actually used tools is
 * scaffolding. Never tuck away an unanswered question, a tool result, a failure,
 * the live tail (which owns the wait indicator), or the final answer. */
export function workProgressItems(
  indices: readonly number[],
  eligible: ReadonlySet<number>
): WorkItem[] {
  const items: WorkItem[] = []

  for (const index of indices) {
    if (eligible.has(index)) {
      const last = items.at(-1)

      if (last?.kind === 'progress') {
        last.indices.push(index)
      } else {
        items.push({ indices: [index], kind: 'progress' })
      }
    } else {
      items.push({ index, kind: 'message' })
    }
  }

  return items
}

export const WorkProgressMessages: FC<{ components: MessageComponents; indices: readonly number[] }> = ({
  components,
  indices
}) => {
  const { t } = useI18n()
  // Return a primitive signature: streamed tokens cannot rebuild the turn's
  // rows or remount a disclosure the user has already opened.

  const eligibleKey = useAuiState(state => {
    const messages = indices.map(index => state.thread.messages[index])

    const usedTools = messages.some(
      message => message?.role === 'assistant' && message.content.some(part => part.type === 'tool-call')
    )

    if (!usedTools) {
      return ''
    }

    const lastIndex = indices.at(-1)

    return indices
      .filter(index => {
        if (index === lastIndex) {
          return false
        }

        const message = state.thread.messages[index]

        return (
          message?.role === 'assistant' &&
          message.metadata?.custom?.interim === true &&
          message.status?.type === 'complete' &&
          message.content.length > 0 &&
          message.content.every(part => part.type === 'text')
        )
      })
      .join(',')
  })

  const items = useMemo(
    () => workProgressItems(indices, new Set(eligibleKey ? eligibleKey.split(',').map(Number) : [])),
    [indices, eligibleKey]
  )

  return items.map(item =>
    item.kind === 'message' ? (
      <ThreadPrimitive.MessageByIndex components={components} index={item.index} key={item.index} />
    ) : (
      <details
        className="min-w-0 text-(--ui-text-tertiary)"
        data-conversation-scaffold=""
        data-slot="aui_work-progress"
        key={item.indices[0]}
      >
        <summary className="group/progress flex w-fit cursor-pointer list-none items-center gap-1.5 rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 [&::-webkit-details-marker]:hidden">
          <span className={SCAFFOLD_LABEL_CLASS}>{t.assistant.thread.workProgress(item.indices.length)}</span>
          <DisclosureCaret className="text-(--ui-text-tertiary) transition-transform group-open/progress:rotate-90" open={false} />
        </summary>
        <div className="grid min-w-0 gap-2 border-l border-(--ui-stroke-tertiary) pl-3">
          {item.indices.map(index => (
            <ThreadPrimitive.MessageByIndex components={components} index={index} key={index} />
          ))}
        </div>
      </details>
    )
  )
}
