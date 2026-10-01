import { ConfirmDialog } from '@hermes/plugin-sdk'

import { useKanban } from './ui'

export function TaskDeleteConfirm({
  count,
  name,
  onClose,
  onConfirm,
  open
}: {
  count: number
  name?: string
  onClose: () => void
  onConfirm: () => Promise<void> | void
  open: boolean
}) {
  const k = useKanban()
  const namedSingle = typeof name === 'string' && name.length > 0

  return (
    <ConfirmDialog
      confirmLabel={k.delete}
      description={namedSingle ? k.deleteTaskConfirm : k.deleteTasksConfirm}
      destructive
      onClose={onClose}
      onConfirm={onConfirm}
      open={open}
      title={namedSingle ? k.deleteTaskTitle(name) : k.deleteTasksTitle(count)}
    />
  )
}
