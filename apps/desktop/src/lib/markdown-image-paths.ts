import { mediaMarkdownHref, mediaName } from '@/lib/media'

// Absolute image paths in assistant prose become media links, so the picture
// renders through the same `#media:` route a `[label](path)` link takes. Only
// paths that end in an image extension qualify; any other file stays text.

// A bare path: `D:\报告\结果.png`, `D:/out/a.jpg`, `/tmp/chart.svg`. The character
// before it must not be a path or URL character, so `https://host/a.png`,
// `file:///a.png` and `and/or.png` never match. The extension must end the
// token, so `a.png.txt` and `a.pngx` never match.
const BARE_IMAGE_PATH_RE =
  /(?<![A-Za-z0-9_./:\\~-])(?:[A-Za-z]:[\\/]|\/)[\p{L}\p{M}\p{N}_.+@%&=~\\/-]*\.(?:png|jpe?g|gif|webp|svg)(?![\p{L}\p{M}\p{N}_\\/+@%&=~-]|\.[\p{L}\p{M}\p{N}_])/giu

// Image syntax with a drive-letter source: `![示例](D:\out\chart.png)`. The
// sanitizer drops that source, so the picture is routed as a media link.
// POSIX image sources keep their inline pipeline.
const DRIVE_IMAGE_SYNTAX_RE = /!\[([^[\]\n]*)\]\(([A-Za-z]:[\\/][^)\s]*\.(?:png|jpe?g|gif|webp|svg))\)/gi

// Markdown link and image syntax, plus autolinks. Text inside these is a link
// target or label, not prose, so bare paths are left alone there.
const LINK_OR_AUTOLINK_RE = /(!?\[[^\]\n]*\]\([^)\n]*\)|<[^>\n]*>)/g

function mediaLink(target: string, label: string): string {
  return `[${label}](${mediaMarkdownHref(target)})`
}

export function linkBareImagePaths(text: string): string {
  return text
    .replace(DRIVE_IMAGE_SYNTAX_RE, (_match: string, alt: string, target: string) =>
      mediaLink(target, alt.trim() || mediaName(target))
    )
    .split(LINK_OR_AUTOLINK_RE)
    .map((part, index) =>
      index % 2 === 1 ? part : part.replace(BARE_IMAGE_PATH_RE, target => mediaLink(target, mediaName(target)))
    )
    .join('')
}
