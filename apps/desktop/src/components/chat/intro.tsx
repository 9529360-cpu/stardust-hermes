import { requestComposerInsert } from '@/app/chat/composer/focus'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Sun } from '@/lib/icons'

export type IntroProps = {
  cwd?: string | null
  personality?: string
  seed?: number
}

const ASSISTANT_COPY = {
  ar: {
    eyebrow: 'مساعدك الشخصي',
    headline: 'ما الذي تريد أن أعتني به؟',
    body: 'أخبرني بالنتيجة التي تريدها. سأتولى التفاصيل وأطلعك على المهم فقط.',
    actions: [
      ['رتّب يومي', 'ساعدني في ترتيب أهم مهامي اليوم ووضع خطة بسيطة قابلة للتنفيذ.'],
      ['اكتب لي', 'ساعدني في كتابة رسالة واضحة وطبيعية. اسألني فقط عن المعلومات الضرورية.'],
      ['حلّ مشكلة', 'لدي مشكلة أريد حلها. ساعدني في تشخيصها ثم نفّذ أفضل خطوة تالية.']
    ]
  },
  en: {
    eyebrow: 'Stardust assistant',
    headline: 'What can I help with?',
    body: 'Ask a question, hand me everyday work, or choose a project when you want to code. In a project, Stardust can inspect the repo, edit code, run tests, and verify the result.',
    actions: [
      ['Handle some work', 'Help me handle a work task. Clarify only what you need, then carry it through as far as you can.'],
      ['Ask a question', 'I have a question. Give me a clear, useful answer and explain the important tradeoffs.'],
      ['Code with me', 'I want to work on code. Help me choose the right project if needed, then inspect it and work through the task with strong engineering rigor.']
    ]
  },
  ja: {
    eyebrow: 'あなたのパーソナルアシスタント',
    headline: '今日は何をお手伝いしましょう？',
    body: '望む結果を教えてください。細部はこちらで整理し、判断が必要な時だけ確認します。',
    actions: [
      ['今日を整理', '今日の優先事項を整理して、無理のない簡潔な計画を作ってください。'],
      ['文章を作る', '自然で分かりやすい文章を作るのを手伝ってください。必要なことだけ質問してください。'],
      ['問題を解決', '解決したい問題があります。原因を整理し、最善の次の一歩まで進めてください。']
    ]
  },
  ru: {
    eyebrow: 'Ваш личный помощник',
    headline: 'О чём мне позаботиться?',
    body: 'Скажите, какой результат вам нужен. Я возьму детали на себя и обращусь только за важным решением.',
    actions: [
      ['Спланировать день', 'Помоги расставить приоритеты на сегодня и составить простой реалистичный план.'],
      ['Написать текст', 'Помоги написать ясное и естественное сообщение. Спроси только действительно нужные детали.'],
      ['Решить проблему', 'У меня есть проблема. Помоги разобраться в причине и выполнить лучший следующий шаг.']
    ]
  },
  zh: {
    eyebrow: 'Stardust 助理',
    headline: '今天想让我帮你做什么？',
    body: '可以直接问问题、交给我日常工作，也可以选择一个项目进入开发模式。需要编码时，Stardust 会读取代码、修改、运行测试并检查真实结果。',
    actions: [
      ['处理工作', '帮我处理一件工作上的事情。只确认必要信息，然后尽可能把事情推进到可交付的结果。'],
      ['问个问题', '我有一个问题。请给我清楚、实用的回答，并说明真正重要的取舍。'],
      ['开始开发', '我想做一个编码任务。如果还没选项目，先帮我进入合适的项目；然后读取代码、完成修改、运行测试并验证真实结果。']
    ]
  },
  'zh-hant': {
    eyebrow: 'Stardust 助理',
    headline: '今天想讓我幫你做什麼？',
    body: '可以直接問問題、交給我日常工作，也可以選擇一個專案進入開發模式。需要寫程式時，Stardust 會讀取程式碼、修改、執行測試並檢查真實結果。',
    actions: [
      ['處理工作', '幫我處理一件工作上的事情。只確認必要資訊，然後盡可能把事情推進到可交付的結果。'],
      ['問個問題', '我有一個問題。請給我清楚、實用的回答，並說明真正重要的取捨。'],
      ['開始開發', '我想做一個程式開發任務。如果還沒選專案，先幫我進入合適的專案；然後讀取程式碼、完成修改、執行測試並驗證真實結果。']
    ]
  }
} as const

const PROJECT_SELECTED_COPY = {
  ar: {
    headline: 'ما المهمة التالية في هذا المشروع؟',
    body: 'صف التغيير الذي تريده. سيعمل Stardust داخل هذا المشروع، ويعدّل الشيفرة، ويشغّل الاختبارات، ويتحقق من النتيجة.'
  },
  en: {
    headline: 'What should we change in this project?',
    body: 'Describe a coding task. Stardust will work in this project, edit the code, run tests, and verify the result.'
  },
  ja: {
    headline: 'このプロジェクトで次に何を変更しますか？',
    body: 'コーディングタスクを説明してください。Stardust がこのプロジェクト内でコードを編集し、テストを実行して結果を確認します。'
  },
  ru: {
    headline: 'Что изменить в этом проекте?',
    body: 'Опишите задачу разработки. Stardust будет работать в этом проекте, изменит код, запустит тесты и проверит результат.'
  },
  zh: {
    headline: '这个项目要做什么？',
    body: '直接描述一个编码任务。Stardust 会在当前项目中读取代码、修改、运行测试并检查真实结果。'
  },
  'zh-hant': {
    headline: '這個專案要做什麼？',
    body: '直接描述一個程式開發任務。Stardust 會在目前專案中讀取程式碼、修改、執行測試並檢查真實結果。'
  }
} as const

export function Intro({ cwd, personality: _personality, seed: _seed }: IntroProps) {
  const { locale } = useI18n()
  const assistant = ASSISTANT_COPY[locale]
  const projectSelected = Boolean(cwd?.trim())
  const selectedCopy = PROJECT_SELECTED_COPY[locale]

  return (
    <div className="assistant-home mx-auto flex w-full max-w-2xl flex-col items-center px-5 py-8 text-center" data-slot="aui_intro">
      <div aria-hidden className="assistant-home__mark">
        <Sun className="size-5" />
      </div>
      <p className="mt-4 text-[0.6875rem] font-semibold uppercase tracking-[0.18em] text-(--ui-text-quaternary)">
        {assistant.eyebrow}
      </p>
      <h1 className="mt-2 text-balance text-2xl font-semibold tracking-[-0.035em] text-(--ui-text-primary) sm:text-[1.75rem]">
        {projectSelected ? selectedCopy.headline : assistant.headline}
      </h1>
      <p className="mt-2 max-w-xl text-pretty text-sm leading-6 text-(--ui-text-tertiary)">
        {projectSelected ? selectedCopy.body : assistant.body}
      </p>
      <div className="mt-7 grid w-full gap-2.5 sm:grid-cols-3" data-testid="assistant-quick-actions">
        {assistant.actions.map(([label, prompt]) => (
          <Button
            className="assistant-home__action"
            key={label}
            onClick={() => requestComposerInsert(prompt, { mode: 'block', target: 'active' })}
            size="sm"
            type="button"
            variant="outline"
          >
            {label}
          </Button>
        ))}
      </div>
    </div>
  )
}
