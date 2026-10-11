/**
 * The presentation leaves every Bot Mode surface reads: the name a row shows,
 * the flattened one-line preview beside it, and the slug that identifies a
 * workspace tile.
 *
 * They sit below the surfaces rather than inside any one of them — the roster,
 * the bot row and the group-chat room all render the same identity, and none
 * of them can own it without the others importing a sibling surface.
 */

import { aliasIdentityFor } from './routing'
import type { BotMeta, RosterRow } from './types'

export function displayName(bot: Partial<RosterRow>, meta?: BotMeta | null): string {
  // A configured alias route claiming this row overrides source-derived
  // identity: the friendly alias name must survive hosted-session
  // activation and Cloud-only rosters (#89131).
  const alias = aliasIdentityFor(bot)

  // Only THIN rows from another source trade the friendly name for their
  // connection label — the active gateway's own default must keep reading
  // "Hermes". Annotated active rows carry sourceScoped too, and keying this
  // off sourceScoped renamed the user's main agent to an IP-derived label
  // (community report, Aug 17 2026).
  if (
    bot?.remoteSource &&
    (bot.name || '').trim().toLowerCase() === 'default' &&
    bot.connectionLabel &&
    !alias &&
    !meta?.title?.trim()
  ) {
    return bot.connectionLabel
  }

  if (meta?.title?.trim()) {
    return meta.title.trim()
  }

  // Core-profile display name (profile.yaml, set via `hermes profile rename
  // default <name>` or the dashboard) — the CLI-level equivalent of a Bot
  // Mode title. Rides the profiles.list row; presentation-only.
  if (typeof bot?.display_name === 'string' && bot.display_name.trim()) {
    return bot.display_name.trim()
  }

  // An untitled backend row claimed by an alias reads as the alias name —
  // never generic "Hermes" or a hostname-derived label.
  if (alias) {
    const raw = alias.name.replace(/[-_]+/g, ' ').trim()

    return raw.replace(/\b\w/g, ch => ch.toUpperCase())
  }

  // The primary profile is literally named "default" — as a bot identity
  // that reads like nobody bothered. Present it as Hermes (the agent it is)
  // unless the user gives it a real title.
  if ((bot.name || '').trim().toLowerCase() === 'default' && !bot.title) {
    return 'Hermes'
  }

  const raw = (bot.title || bot.name || '').replace(/[-_]+/g, ' ').trim()

  return raw.replace(/\b\w/g, ch => ch.toUpperCase())
}

export function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
}

// A preview is one truncated line, so a GFM table often arrives flattened onto
// it with its newlines gone. A table line has outer pipes, or sits beside a
// delimiter row (`---`, one or more hyphens per cell, with or without outer
// pipes). Its cells are joined with " · "; a newline or a delimiter row between
// two cells is a row break and joins them with "; ". An empty cell is a missing
// value, so it never starts a row. Prose lines pass through unchanged, and an
// escaped `\|` stays inside its cell.
const TABLE_DELIMITER_CELL_RE = /^:?-+:?$/
const TABLE_ESCAPED_PIPE = '\u0001'
const TABLE_CELL_JOIN = ' · '
const TABLE_ROW_JOIN = '; '

/** The trimmed cells of one line split on its unescaped pipes, or null when it has none. */
function splitTableCells(line: string): string[] | null {
  const shielded = line.replace(/\\\|/g, TABLE_ESCAPED_PIPE)

  if (!shielded.includes('|')) {
    return null
  }

  return shielded.split('|').map(cell => cell.trim().split(TABLE_ESCAPED_PIPE).join('|'))
}

/** Marks the delimiter run in one line. The run counts only when an empty cell or the line's
 *  edge sits on both sides, so a dash that is ordinary cell content never reads as a boundary. */
function delimiterCellFlags(cells: string[]): boolean[] {
  const flags = cells.map(() => false)
  let start = 0

  while (start < cells.length) {
    if (!TABLE_DELIMITER_CELL_RE.test(cells[start])) {
      start++

      continue
    }

    let end = start

    while (end + 1 < cells.length && TABLE_DELIMITER_CELL_RE.test(cells[end + 1])) {
      end++
    }

    const bounded = (start === 0 || cells[start - 1] === '') && (end === cells.length - 1 || cells[end + 1] === '')

    if (bounded) {
      flags.fill(true, start, end + 1)
    }

    start = end + 1
  }

  return flags
}

/** A delimiter row is a line whose non-empty cells are all delimiter cells. */
function isDelimiterRow(cells: string[]): boolean {
  const content = cells.filter(cell => cell !== '')

  return content.length > 0 && content.every(cell => TABLE_DELIMITER_CELL_RE.test(cell))
}

/** A line has outer pipes when it starts or ends with an empty cell. */
function hasOuterPipe(cells: string[]): boolean {
  return cells[0] === '' || cells[cells.length - 1] === ''
}

/** Which lines are table lines: a line with outer pipes or a delimiter run, the header above a
 *  delimiter row, and the pipe lines below it. A line without pipes ends the table. */
function tableLineFlags(rows: Array<string[] | null>): boolean[] {
  const flags = rows.map(cells => cells !== null && (hasOuterPipe(cells) || delimiterCellFlags(cells).includes(true)))

  rows.forEach((cells, index) => {
    if (cells === null || !isDelimiterRow(cells)) {
      return
    }

    flags[index] = true

    if (index > 0 && rows[index - 1] !== null) {
      flags[index - 1] = true
    }

    for (let next = index + 1; next < rows.length && rows[next] !== null; next++) {
      flags[next] = true
    }
  })

  return flags
}

/** One table block as a single line. A line break or a delimiter row is a row break; an empty
 *  cell is skipped and breaks nothing. */
function flattenTableRows(rows: string[][]): string {
  let flat = ''
  let rowBreak = false

  rows.forEach((cells, row) => {
    if (row > 0) {
      rowBreak = true
    }

    const delimiter = delimiterCellFlags(cells)

    cells.forEach((cell, column) => {
      if (delimiter[column]) {
        rowBreak = true
      } else if (cell) {
        flat = flat ? `${flat}${rowBreak ? TABLE_ROW_JOIN : TABLE_CELL_JOIN}${cell}` : cell
        rowBreak = false
      }
    })
  })

  return flat
}

function flattenTables(text: string): string {
  const lines = text.split('\n')
  const rows = lines.map(splitTableCells)
  const tableLine = tableLineFlags(rows)
  const pieces: string[] = []
  let start = 0

  while (start < lines.length) {
    if (!tableLine[start]) {
      pieces.push(lines[start])
      start++

      continue
    }

    let end = start

    while (end < lines.length && tableLine[end]) {
      end++
    }

    pieces.push(flattenTableRows(rows.slice(start, end).filter((cells): cells is string[] => cells !== null)))
    start = end
  }

  return pieces.join('\n')
}

/** Flatten markdown syntax out of a one-line roster preview so rows read
 *  like Discord's — no raw **bold**, `code`, > quotes, [link](url), table
 *  pipes, list markers or rules in the preview line. */
export function stripPreviewMarkdown(text: unknown) {
  const plain = String(text || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`\n]*)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(^|\s)[*_](\S(?:.*?\S)?)[*_](?=\s|$|[.,;:!?])/g, '$1$2')
    .replace(/~~(.*?)~~/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')

  return flattenTables(plain)
    .replace(/^\s*([-*_])(?:\s*\1){2,}\s*$/gm, '')
    .replace(/^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
}
