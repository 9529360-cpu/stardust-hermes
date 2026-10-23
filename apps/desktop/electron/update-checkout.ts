import { execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

export function hasGitMetadata(root: string): boolean {
  try {
    const marker = path.join(root, '.git')
    const stat = fs.statSync(marker)

    if (stat.isDirectory()) {return true}

    if (!stat.isFile() || stat.size > 4096) {return false}
    const target = /^gitdir: (.+)\r?\n?$/.exec(fs.readFileSync(marker, 'utf8'))?.[1]?.trim()

    return Boolean(target && fs.statSync(path.resolve(root, target)).isDirectory())
  } catch {
    return false
  }
}

type CheckoutState =
  | { kind: 'branch' | 'detached-head'; currentBranch: string }
  | { kind: 'not-a-git-checkout' | 'git-missing' }
  | { kind: 'git-error'; message: string }

/** Local, read-only probe. Never waits for network or blocks Electron's event loop. */
export async function probeUpdateCheckout(root: string, gitBinary = 'git'): Promise<CheckoutState> {
  if (!hasGitMetadata(root)) {return { kind: 'not-a-git-checkout' }}

  return new Promise(resolve => {
    execFile(gitBinary, ['rev-parse', '--abbrev-ref', 'HEAD'], {
      cwd: root,
      env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
      timeout: 2000,
      windowsHide: true,
      maxBuffer: 64 * 1024
    }, (error, stdout) => {
      if (error) {
        resolve((error as NodeJS.ErrnoException).code === 'ENOENT'
          ? { kind: 'git-missing' }
          : { kind: 'git-error', message: error.message })

        return
      }

      const currentBranch = stdout.trim()
      resolve({ kind: currentBranch === 'HEAD' ? 'detached-head' : 'branch', currentBranch })
    })
  })
}
