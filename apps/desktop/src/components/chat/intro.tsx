import { requestComposerInsert } from '@/app/chat/composer/focus'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Sun } from '@/lib/icons'

export type IntroProps = {
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
    eyebrow: 'Your personal assistant',
    headline: 'What can I take care of?',
    body: 'Tell me the outcome you want. I’ll handle the details and only interrupt when your decision matters.',
    actions: [
      ['Plan my day', 'Help me sort today’s priorities and make a simple, realistic plan.'],
      ['Write something', 'Help me write a clear, natural message. Ask only for the details you truly need.'],
      ['Solve a problem', 'I have a problem to solve. Help me diagnose it and carry out the best next step.']
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
    eyebrow: '你的个人助理',
    headline: '今天想让我帮你做什么？',
    body: '直接告诉我你想要的结果。细节交给我，只有真正需要你决定时才会打扰你。',
    actions: [
      ['整理今天', '帮我梳理今天最重要的事情，并安排一个简单、现实的行动计划。'],
      ['帮我写点东西', '帮我写一段清楚、自然的内容。只询问真正缺少的必要信息。'],
      ['解决一个问题', '我有一个问题需要解决。请先帮我判断原因，然后推进最合适的下一步。']
    ]
  },
  'zh-hant': {
    eyebrow: '你的個人助理',
    headline: '今天想讓我幫你做什麼？',
    body: '直接告訴我你想要的結果。細節交給我，只有真正需要你決定時才會打擾你。',
    actions: [
      ['整理今天', '幫我整理今天最重要的事情，並安排一個簡單、實際的行動計畫。'],
      ['幫我寫點東西', '幫我寫一段清楚、自然的內容。只詢問真正缺少的必要資訊。'],
      ['解決一個問題', '我有一個問題需要解決。請先幫我判斷原因，再推進最合適的下一步。']
    ]
  }
} as const

export function Intro({ personality: _personality, seed: _seed }: IntroProps) {
  const { locale } = useI18n()
  const assistant = ASSISTANT_COPY[locale]

  return (
    <div
      className="assistant-home mx-auto flex w-full max-w-2xl flex-col items-center px-5 py-8 text-center"
      data-slot="aui_intro"
    >
      <div aria-hidden className="assistant-home__mark">
        <Sun className="size-5" />
      </div>
      <p className="mt-4 text-[0.6875rem] font-semibold uppercase tracking-[0.18em] text-(--ui-text-quaternary)">
        {assistant.eyebrow}
      </p>
      <h1 className="mt-2 text-balance text-2xl font-semibold tracking-[-0.035em] text-(--ui-text-primary) sm:text-[1.75rem]">
        {assistant.headline}
      </h1>
      <p className="mt-2 max-w-xl text-pretty text-sm leading-6 text-(--ui-text-tertiary)">{assistant.body}</p>
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
