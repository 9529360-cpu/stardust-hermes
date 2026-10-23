import { translateNow } from '@/i18n'
import { notify } from '@/store/notifications'
import { requestRoute } from '@/store/recovery-requests'

/** Configuration guidance is not a failed assistant reply. */
export function showModelSetupNotice(): void {
  notify({
    id: 'model-setup-required',
    kind: 'info',
    durationMs: 0,
    title: translateNow('assistant.thread.modelSetupTitle'),
    message: translateNow('assistant.thread.modelSetupBody'),
    action: {
      label: translateNow('assistant.thread.modelSetupAction'),
      onClick: () => requestRoute('/settings?tab=config%3Amodel')
    }
  })
}
