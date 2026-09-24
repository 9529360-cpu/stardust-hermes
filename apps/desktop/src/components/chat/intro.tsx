import { openCommandPalette } from '@/store/command-palette'
import { requestComposerFocus } from '@/app/chat/composer/focus'
import { useI18n } from '@/i18n'

export type IntroProps = {
  personality?: string
  seed?: number
}

const ASSISTANT_COPY = {
  ar: {
    headline: 'بماذا نبدأ؟',
    body: 'اسأل مباشرة، أو أعطني مهمة. يظهر سياق المشروع أو الملفات أو المعاينة بجانب المحادثة فقط عند الحاجة.',
    start: 'ابدأ برسالة',
    actions: 'استكشف الأوامر'
  },
  en: {
    headline: 'What should we work on?',
    body: 'Ask directly or hand me a task. Project, file, or preview context appears beside the conversation only when it is useful.',
    start: 'Start with a message',
    actions: 'Explore commands'
  },
  ja: {
    headline: '今日は何を進めますか？',
    body: 'そのまま質問するか、仕事を任せてください。必要なときだけ、プロジェクト・ファイル・プレビューの文脈を会話の横に表示します。',
    start: 'メッセージを始める',
    actions: 'コマンドを見る'
  },
  ru: {
    headline: 'С чего начнём?',
    body: 'Задайте вопрос или поручите задачу. Контекст проекта, файлов или предпросмотра появится рядом с диалогом только при необходимости.',
    start: 'Начать с сообщения',
    actions: 'Открыть команды'
  },
  zh: {
    headline: '今天想做什么？',
    body: '直接问我，或者把一件事交给我。需要项目、文件或预览时，相关上下文会出现在对话右侧。',
    start: '从一条消息开始',
    actions: '浏览命令'
  },
  'zh-hant': {
    headline: '今天想做什麼？',
    body: '直接問我，或者把一件事交給我。需要專案、檔案或預覽時，相關上下文會出現在對話右側。',
    start: '從一則訊息開始',
    actions: '瀏覽命令'
  }
} as const

export function Intro({ personality: _personality, seed: _seed }: IntroProps) {
  const { locale } = useI18n()
  const assistant = ASSISTANT_COPY[locale]

  return (
    <div className="assistant-home" data-slot="aui_intro">
      <div className="assistant-home-copy">
        <span className="assistant-home-eyebrow">Stardust</span>
        <h1>{assistant.headline}</h1>
        <p>{assistant.body}</p>
      </div>
      <div className="assistant-home-actions" data-testid="assistant-quick-actions">
        <button className="assistant-home-action assistant-home-action-primary" onClick={() => requestComposerFocus()} type="button">
          {assistant.start}
        </button>
        <button className="assistant-home-action" onClick={openCommandPalette} type="button">
          {assistant.actions}
        </button>
      </div>
    </div>
  )
}
