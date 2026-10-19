import { sessionSearchTextParts } from '@/lib/session-search'

/** Text children let React escape all HTML from user messages and the index. */
export function SessionSearchText({ text, query }: { text: string; query: string }) {
  return sessionSearchTextParts(text, query).map((part, index) =>
    part.highlighted ? (
      <mark className="bg-(--ui-bg-quaternary) font-medium text-(--ui-text-primary)" key={index}>
        {part.text}
      </mark>
    ) : (
      part.text
    )
  )
}
