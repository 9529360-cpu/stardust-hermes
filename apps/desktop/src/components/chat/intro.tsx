import { useI18n } from '@/i18n'

export type IntroProps = {
  personality?: string
  seed?: number
}

const ASSISTANT_COPY = {
  ar: {
    headline: 'بماذا نبدأ؟',
    body: 'اسأل مباشرة، أو أعطني مهمة. يظهر سياق المشروع أو الملفات أو المعاينة بجانب المحادثة فقط عند الحاجة.'
  },
  en: {
    headline: 'What should we work on?',
    body: 'Ask directly or hand me a task. Project, file, or preview context appears beside the conversation only when it is useful.'
  },
  ja: {
    headline: '今日は何を進めますか？',
    body: 'そのまま質問するか、仕事を任せてください。必要なときだけ、プロジェクト・ファイル・プレビューの文脈を会話の横に表示します。'
  },
  ru: {
    headline: 'С чего начнём?',
    body: 'Задайте вопрос или поручите задачу. Контекст проекта, файлов или предпросмотра появится рядом с диалогом только при необходимости.'
  },
  zh: {
    headline: '今天想做什么？',
    body: '直接问我，或者把一件事交给我。需要项目、文件或预览时，相关上下文会出现在对话右侧。'
  },
  'zh-hant': {
    headline: '今天想做什麼？',
    body: '直接問我，或者把一件事交給我。需要專案、檔案或預覽時，相關上下文會出現在對話右側。'
  }
} as const

export function Intro({ personality: _personality, seed: _seed }: IntroProps) {
  const { locale } = useI18n()
  const assistant = ASSISTANT_COPY[locale]

  return (
    <div
      className="assistant-home mx-auto flex min-h-[42vh] w-full max-w-3xl flex-col justify-end px-7 pb-8 text-left"
      data-slot="aui_intro"
    >
      <h1 className="max-w-xl text-balance text-[1.4rem] font-semibold leading-tight tracking-[-0.025em] text-(--ui-text-primary)">
        {assistant.headline}
      </h1>
      <p className="mt-2 max-w-xl text-pretty text-[0.78rem] leading-5 text-(--ui-text-tertiary)">{assistant.body}</p>
    </div>
  )
}
