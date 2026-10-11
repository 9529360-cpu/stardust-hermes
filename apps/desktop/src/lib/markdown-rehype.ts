import type { Element, Parent, Root } from 'hast'
import { defaultRehypePlugins } from 'streamdown'
import type { PluggableList, Plugin } from 'unified'
import { visitParents } from 'unist-util-visit-parents'

// The rehype pipeline for assistant markdown. streamdown's default is
// raw -> sanitize -> harden. This keeps its parser, its sanitize rules and its
// harden plugin, and changes only what breaks the chat surface:
//
// - sanitize also allows `data:` in `src`. Its default http/https allowlist
//   removed every data URI before harden could judge it, so inline images never
//   rendered. harden still gates them: with allowDataImages it keeps only
//   `data:image/*`, and file:, javascript: and non-image data: stay blocked.
// - an image harden still refuses becomes a placeholder element that the React
//   layer renders with the localized `couldntLoad` copy, instead of harden's
//   English "[Image blocked: ...]" text.
// - a link harden refuses keeps its text only; harden's English " [blocked]"
//   marker and "Blocked URL" title are dropped.

export const BLOCKED_IMAGE_TAG = 'markdown-blocked-image'

interface HardenOptions {
  allowDataImages: boolean
  allowedImagePrefixes: string[]
  allowedLinkPrefixes: string[]
  allowedProtocols: string[]
  imageBlockPolicy: 'remove'
  linkBlockPolicy: 'text-only'
}

type SanitizeSchema = { protocols?: Record<string, string[] | undefined> } & Record<string, unknown>
type HardenFactory = (options: HardenOptions) => (tree: Root) => void

// streamdown exports the plugins it was built with, keyed by stage. The
// sanitize and harden entries are [plugin, options] pairs.
const [sanitizePlugin, sanitizeSchema] = defaultRehypePlugins.sanitize as unknown as [Plugin, SanitizeSchema]
const [hardenFactory] = defaultRehypePlugins.harden as unknown as [HardenFactory]

// Any origin and protocol is accepted, except what harden always refuses:
// javascript:, vbscript:, file:, and data: that is not an image.
const HARDEN_OPTIONS: HardenOptions = {
  allowDataImages: true,
  allowedImagePrefixes: ['*'],
  allowedLinkPrefixes: ['*'],
  allowedProtocols: ['*'],
  imageBlockPolicy: 'remove',
  linkBlockPolicy: 'text-only'
}

const hardenTransform = hardenFactory(HARDEN_OPTIONS)

// Asks harden what it would do with this image, on a copy of the node, so the
// allow decision stays in one place (harden) rather than being re-implemented.
function hardenBlocksImage(image: Element): boolean {
  const probe: Root = {
    type: 'root',
    children: [{ type: 'element', tagName: 'img', properties: { ...image.properties }, children: [] }]
  }

  hardenTransform(probe)

  const [first] = probe.children

  return !(first?.type === 'element' && first.tagName === 'img')
}

function blockedImagePlaceholder(image: Element): Element {
  const { alt, src } = image.properties

  return {
    type: 'element',
    tagName: BLOCKED_IMAGE_TAG,
    properties: {
      alt,
      // A data: source can be a whole document. The placeholder only needs a name.
      src: typeof src === 'string' && !src.startsWith('data:') ? src : undefined
    },
    children: []
  }
}

// Runs before harden: a refused image is swapped for the placeholder, so harden
// never sees it and never emits its English indicator.
const localizeBlockedImages: Plugin<[], Root> = () => tree => {
  const refused: { image: Element; parent: Parent }[] = []

  visitParents(tree, 'element', (image, ancestors) => {
    if (image.tagName === 'img' && hardenBlocksImage(image)) {
      refused.push({ image, parent: ancestors[ancestors.length - 1] as Parent })
    }
  })

  for (const { image, parent } of refused) {
    parent.children[parent.children.indexOf(image)] = blockedImagePlaceholder(image)
  }
}

export const markdownRehypePlugins: PluggableList = [
  defaultRehypePlugins.raw,
  [
    sanitizePlugin,
    {
      ...sanitizeSchema,
      protocols: {
        ...sanitizeSchema.protocols,
        src: [...(sanitizeSchema.protocols?.src ?? []), 'data']
      }
    }
  ],
  localizeBlockedImages,
  [hardenFactory, HARDEN_OPTIONS]
]
