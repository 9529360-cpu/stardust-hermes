export const REFERENCE_SHELL = 'aurora' as const

export function shouldEnableReferenceShell(winParam: string | null): boolean {
  return winParam === null || winParam === 'secondary'
}

export function applyReferenceShell(winParam: string | null, root: HTMLElement = document.documentElement): void {
  if (shouldEnableReferenceShell(winParam)) {
    root.dataset.hermesReferenceShell = REFERENCE_SHELL
  }
}
