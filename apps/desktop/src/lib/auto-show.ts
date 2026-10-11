/**
 * Auto-show: which gateway events may open something in the right-rail preview
 * by themselves, and what that something is.
 *
 * Pure on purpose. It reads one gateway event and the session's workspace root
 * and answers two questions: is this safe to show unprompted, and what is the
 * normalized file path or URL? The routing hook owns everything else: the
 * on-screen check, the Settings switch, the per-turn ledger, and the tab itself.
 *
 * Two events count:
 * - `tool.complete` for a successful `write_file` or `patch`. The file must be a
 *   type the owner listed for automatic display (PDF, HTML, common images, SVG,
 *   Markdown). Reads never count, and neither does any other tool.
 * - `preview.open`, the assistant's explicit "show this" call. Any file type is
 *   allowed except programs, scripts, installers and archives, so an explicit
 *   request for a CSV still works. Http(s) and localhost URLs pass through as the
 *   assistant opening a page. URLs are read only from this payload, never from
 *   prose, tool output, or web content, so a link in text stays a link.
 *
 * Every file must resolve inside the session workspace. Relative paths anchor to
 * it; `~` and paths that climb out of it are refused. Windows paths compare
 * case-insensitively.
 */

export interface AutoShowEvent {
  payload?: unknown
  type: string
}

export type AutoShowTarget = { kind: 'file'; path: string } | { kind: 'url'; url: string }

export type AutoShowRefusal =
  | 'failed-tool'
  | 'no-path'
  | 'no-workspace'
  | 'not-in-workspace'
  | 'not-previewable'
  | 'blocked-type'
  | 'not-relevant'
  | 'unsupported-target'

export type AutoShowDecision =
  | { open: true; origin: 'agent-open' | 'written-file'; targets: AutoShowTarget[] }
  | { open: false; reason: AutoShowRefusal }

/** The owner's list: what a file the assistant writes may show by itself. */
const WRITTEN_FILE_EXTENSIONS: ReadonlySet<string> = new Set([
  'pdf',
  'html',
  'htm',
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'svg',
  'md'
])

/** What an explicit open may never show: programs, scripts, installers, archives, launchers. */
const BLOCKED_EXTENSIONS: ReadonlySet<string> = new Set([
  // programs, installers, disk images, and libraries
  'exe',
  'com',
  'scr',
  'msi',
  'msix',
  'msp',
  'appx',
  'appxbundle',
  'app',
  'apk',
  'dmg',
  'pkg',
  'deb',
  'rpm',
  'appimage',
  'bin',
  'run',
  'dll',
  'so',
  'dylib',
  'sys',
  'ocx',
  'cpl',
  'jar',
  'class',
  'iso',
  'img',
  // scripts and shortcuts that launch things
  'bat',
  'cmd',
  'ps1',
  'psm1',
  'psd1',
  'vbs',
  'vbe',
  'wsf',
  'wsh',
  'hta',
  'js',
  'jse',
  'mjs',
  'cjs',
  'sh',
  'bash',
  'zsh',
  'fish',
  'command',
  'py',
  'pyw',
  'rb',
  'pl',
  'php',
  'lua',
  'reg',
  'lnk',
  'url',
  'scf',
  'inf',
  // archives
  'zip',
  'tar',
  'gz',
  'tgz',
  'bz2',
  'tbz2',
  'xz',
  'txz',
  'zst',
  '7z',
  'rar',
  'cab',
  'lz',
  'lzma'
])

const WRITE_TOOLS: ReadonlySet<string> = new Set(['write_file', 'patch'])

const DRIVE = /^[a-z]:\//i

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const textOf = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')

const stringList = (value: unknown): string[] => (Array.isArray(value) ? value.map(textOf).filter(Boolean) : [])

const refuse = (reason: AutoShowRefusal): AutoShowDecision => ({ open: false, reason })

/** The root marker of a slash-normalized path: `//` for UNC, `D:/` for a drive, `/` for POSIX, else none. */
function rootPrefix(slashed: string): string {
  if (slashed.startsWith('//')) {
    return '//'
  }

  if (DRIVE.test(slashed)) {
    return slashed.slice(0, 3)
  }

  return slashed.startsWith('/') ? '/' : ''
}

/** Forward slashes, `.` and `..` folded away, no trailing slash. Null when `..` climbs above the root. */
function normalizePath(input: string): null | string {
  const slashed = input.replace(/\\/g, '/')
  const prefix = rootPrefix(slashed)
  const parts: string[] = []

  for (const part of slashed.slice(prefix.length).split('/')) {
    if (part === '' || part === '.') {
      continue
    }

    if (part === '..') {
      if (parts.length === 0) {
        return null
      }

      parts.pop()

      continue
    }

    parts.push(part)
  }

  return prefix + parts.join('/')
}

const isAbsolutePath = (slashed: string): boolean => slashed.startsWith('/') || DRIVE.test(slashed)

const joinOnto = (root: string, relative: string): string => `${root.endsWith('/') ? root : `${root}/`}${relative}`

// Windows drives and UNC shares are case-insensitive; POSIX paths are not.
const comparable = (path: string): string => (DRIVE.test(path) || path.startsWith('//') ? path.toLowerCase() : path)

function isInside(root: string, path: string): boolean {
  return comparable(path).startsWith(comparable(root.endsWith('/') ? root : `${root}/`))
}

function fromFileUrl(raw: string): null | string {
  try {
    const url = new URL(raw)
    const pathname = decodeURIComponent(url.pathname)

    if (url.hostname && url.hostname !== 'localhost') {
      return `//${url.hostname}${pathname}`
    }

    return DRIVE.test(pathname.slice(1)) ? pathname.slice(1) : pathname
  } catch {
    return null
  }
}

type Anchored = { path: string } | { refusal: AutoShowRefusal }

/** Resolve a raw path to a normalized absolute path that is provably inside the workspace. */
function anchor(raw: string, workspace: string): Anchored {
  const root = normalizePath(workspace.trim())

  if (!root || !isAbsolutePath(root)) {
    return { refusal: 'no-workspace' }
  }

  if (raw.startsWith('~')) {
    return { refusal: 'not-in-workspace' }
  }

  let candidate = raw

  if (/^file:/i.test(raw)) {
    const fromUrl = fromFileUrl(raw)

    if (fromUrl === null) {
      return { refusal: 'unsupported-target' }
    }

    candidate = fromUrl
  }

  const slashed = candidate.replace(/\\/g, '/')
  const path = normalizePath(isAbsolutePath(slashed) ? slashed : joinOnto(root, slashed))

  return path !== null && isInside(root, path) ? { path } : { refusal: 'not-in-workspace' }
}

function extensionOf(path: string): string {
  const name = path.slice(path.lastIndexOf('/') + 1)
  const dot = name.lastIndexOf('.')

  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** The tool's own receipt, or null when the call failed. A plain-text result is an error message. */
function receipt(payload: Record<string, unknown>): Record<string, unknown> | null {
  if (payload.error) {
    return null
  }

  const result = payload.result

  if (!isRecord(result) || result.error || result.success === false) {
    return null
  }

  return result
}

/** Absolute paths a successful write produced. Deleted paths never count. */
function writtenPaths(payload: Record<string, unknown>, result: Record<string, unknown>): string[] {
  const deleted = new Set(stringList(result.files_deleted))

  const produced = [...new Set([...stringList(result.files_created), ...stringList(result.files_modified)])].filter(
    path => !deleted.has(path)
  )

  if (produced.length > 0) {
    return produced
  }

  const resolved = textOf(result.resolved_path)

  if (resolved) {
    return [resolved]
  }

  const args = isRecord(payload.args) ? payload.args : {}
  const argPath = textOf(args.path)

  return argPath ? [argPath] : []
}

function decideWrittenFiles(payload: Record<string, unknown>, workspace: string): AutoShowDecision {
  if (!WRITE_TOOLS.has(textOf(payload.name))) {
    return refuse('not-relevant')
  }

  const result = receipt(payload)

  if (!result) {
    return refuse('failed-tool')
  }

  const candidates = writtenPaths(payload, result)

  if (candidates.length === 0) {
    return refuse('no-path')
  }

  const targets = new Map<string, AutoShowTarget>()
  let refusal: AutoShowRefusal = 'no-path'

  for (const raw of candidates) {
    const anchored = anchor(raw, workspace)

    if ('refusal' in anchored) {
      refusal = anchored.refusal

      continue
    }

    if (!WRITTEN_FILE_EXTENSIONS.has(extensionOf(anchored.path))) {
      refusal = 'not-previewable'

      continue
    }

    targets.set(anchored.path, { kind: 'file', path: anchored.path })
  }

  return targets.size > 0 ? { open: true, origin: 'written-file', targets: [...targets.values()] } : refuse(refusal)
}

function decideAgentOpen(payload: Record<string, unknown>, workspace: string): AutoShowDecision {
  const raw = textOf(payload.url).replace(/^`|`$/g, '').trim()

  if (!raw) {
    return refuse('no-path')
  }

  if (/^https?:\/\//i.test(raw)) {
    return { open: true, origin: 'agent-open', targets: [{ kind: 'url', url: raw }] }
  }

  // A scheme longer than one letter is a web or app scheme (localhost:3000,
  // javascript:, data:). A single letter is a Windows drive, and `file:` is a path.
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(raw)

  if (scheme && scheme[1].length > 1 && scheme[1].toLowerCase() !== 'file') {
    return refuse('unsupported-target')
  }

  const anchored = anchor(raw, workspace)

  if ('refusal' in anchored) {
    return refuse(anchored.refusal)
  }

  if (BLOCKED_EXTENSIONS.has(extensionOf(anchored.path))) {
    return refuse('blocked-type')
  }

  return { open: true, origin: 'agent-open', targets: [{ kind: 'file', path: anchored.path }] }
}

/**
 * Decide whether a gateway event may open a rail tab by itself, and what the
 * tab should show. `workspace` is the session's own cwd (never another
 * session's); an empty workspace refuses every file, because containment cannot
 * be proven.
 */
export function decideAutoShow(event: AutoShowEvent, workspace: string | null | undefined): AutoShowDecision {
  const payload = isRecord(event.payload) ? event.payload : {}
  const root = workspace ?? ''

  if (event.type === 'tool.complete') {
    return decideWrittenFiles(payload, root)
  }

  if (event.type === 'preview.open') {
    return decideAgentOpen(payload, root)
  }

  return refuse('not-relevant')
}

/**
 * Per-session bookkeeping for auto-show. A turn is live from `message.start` to
 * `message.complete`. A written file only auto-opens inside a live turn, so a
 * replayed completion from an old turn cannot yank the rail. Each file opens at
 * most once per turn.
 */
export interface AutoShowLedger {
  begin(sessionId: string): void
  end(sessionId: string): void
  isLive(sessionId: string): boolean
  /** True the first time `key` is claimed in this session's current turn. */
  claim(sessionId: string, key: string): boolean
}

export function createAutoShowLedger(): AutoShowLedger {
  const live = new Set<string>()
  const claimed = new Map<string, Set<string>>()

  return {
    begin(sessionId) {
      live.add(sessionId)
    },
    claim(sessionId, key) {
      const keys = claimed.get(sessionId) ?? new Set<string>()

      claimed.set(sessionId, keys)

      if (keys.has(key)) {
        return false
      }

      keys.add(key)

      return true
    },
    end(sessionId) {
      live.delete(sessionId)
      claimed.delete(sessionId)
    },
    isLive(sessionId) {
      return live.has(sessionId)
    }
  }
}
