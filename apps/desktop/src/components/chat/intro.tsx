import { useStore } from '@nanostores/react'

import { requestComposerInsert } from '@/app/chat/composer/focus'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Sun } from '@/lib/icons'
import { $currentCwd } from '@/store/session'

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
      ['Написать текст', 'Помоги написать ясный и естественный текст. Спроси только действительно нужные детали.'],
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

const PROJECT_COPY = {
  ar: {
    eyebrow: 'مساحة عمل المشروع',
    headline: 'ما الذي نغيّره؟',
    body: 'صف الميزة أو الخطأ أو إعادة الهيكلة أو النتيجة المطلوبة. سأفحص المشروع، أنفّذ التعديلات، أشغّل الفحوص المناسبة، ثم أعرض لك ما تغيّر.',
    actions: [
      ['ابنِ ميزة', 'نفّذ ميزة مفيدة في هذا المشروع. افحص البنية الحالية أولاً واتبع الأنماط الموجودة ثم اختبر النتيجة.'],
      ['أصلح خطأ', 'اعثر على سبب الخطأ في هذا المشروع وأصلحه من المالك الصحيح، ثم شغّل فحوصاً تثبت أن المشكلة انتهت.'],
      ['راجع الكود', 'راجع هذا المشروع وحدد أهم مشاكل الصحة والواجهة وقابلية الصيانة، مع إصلاحات عملية مرتبة حسب الأثر.']
    ]
  },
  en: {
    eyebrow: 'Project workspace',
    headline: 'What should we change?',
    body: 'Describe the feature, bug, refactor, or outcome you want. I’ll inspect the project, make the edits, run the relevant checks, and show you what changed.',
    actions: [
      ['Build a feature', 'Implement a useful feature in this project. Inspect the existing architecture first, follow its patterns, and validate the result.'],
      ['Fix a bug', 'Find the root cause of a bug in this project, fix the correct owner, and run checks that prove the regression is resolved.'],
      ['Review the code', 'Review this project for the highest-impact correctness, UX, and maintainability issues, then propose concrete fixes in priority order.']
    ]
  },
  ja: {
    eyebrow: 'プロジェクト ワークスペース',
    headline: '何を変更しますか？',
    body: '追加したい機能、直したい不具合、リファクタリング、または実現したい結果を説明してください。プロジェクトを調べ、変更し、必要なチェックを実行して差分を示します。',
    actions: [
      ['機能を作る', 'このプロジェクトに有用な機能を実装してください。まず既存の構成を確認し、現在のパターンに従って検証まで行ってください。'],
      ['不具合を直す', 'このプロジェクトの不具合の根本原因を特定し、正しい責務の箇所で修正して、再発が解消したことを検証してください。'],
      ['コードをレビュー', 'このプロジェクトをレビューし、正しさ、UX、保守性の観点で影響の大きい問題と具体的な修正案を優先順に示してください。']
    ]
  },
  ru: {
    eyebrow: 'Рабочая область проекта',
    headline: 'Что будем менять?',
    body: 'Опишите нужную функцию, ошибку, рефакторинг или результат. Я изучу проект, внесу изменения, запущу подходящие проверки и покажу diff.',
    actions: [
      ['Сделать функцию', 'Реализуй полезную функцию в этом проекте. Сначала изучи текущую архитектуру, следуй её паттернам и проверь результат.'],
      ['Исправить ошибку', 'Найди первопричину ошибки в этом проекте, исправь её в правильном владельце и запусти проверки, подтверждающие исправление.'],
      ['Проверить код', 'Проведи ревью проекта и выдели наиболее важные проблемы корректности, UX и поддерживаемости с конкретными исправлениями по приоритету.']
    ]
  },
  zh: {
    eyebrow: '项目工作区',
    headline: '这次要改什么？',
    body: '告诉我你要做的功能、要修的 Bug、要重构的部分或最终结果。我会先读项目，再修改代码、跑必要检查，并把真实差异给你看。',
    actions: [
      ['实现一个功能', '在这个项目里实现一个有用的功能。先理解现有架构和约定，再完成实现并验证结果。'],
      ['修复一个 Bug', '找出这个项目中 Bug 的根因，在真正负责的代码层修复它，并运行能证明回归已解决的检查。'],
      ['审查代码', '审查这个项目，找出对正确性、体验和可维护性影响最大的几个问题，并按优先级给出可执行的修复。']
    ]
  },
  'zh-hant': {
    eyebrow: '專案工作區',
    headline: '這次要改什麼？',
    body: '告訴我你要做的功能、要修的 Bug、要重構的部分或最終結果。我會先讀專案，再修改程式、跑必要檢查，並把真實差異給你看。',
    actions: [
      ['實作一個功能', '在這個專案裡實作一個有用的功能。先理解現有架構與慣例，再完成實作並驗證結果。'],
      ['修復一個 Bug', '找出這個專案中 Bug 的根因，在真正負責的程式層修復它，並執行能證明回歸已解決的檢查。'],
      ['審查程式碼', '審查這個專案，找出對正確性、體驗與可維護性影響最大的問題，並依優先順序提出可執行的修復。']
    ]
  }
} as const

export function Intro({ personality: _personality, seed: _seed }: IntroProps) {
  const { locale } = useI18n()
  const cwd = useStore($currentCwd)
  const inProject = cwd.trim().length > 0
  const assistant = inProject ? PROJECT_COPY[locale] : ASSISTANT_COPY[locale]

  if (inProject) {
    return (
      <div
        className="mx-auto flex w-full max-w-xl flex-col items-start px-6 pb-5 pt-10 text-left"
        data-project-thread-intro=""
        data-slot="aui_intro"
      >
        <p className="text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-(--ui-text-quaternary)">
          {assistant.eyebrow}
        </p>
        <h1 className="mt-1.5 text-balance text-xl font-semibold tracking-[-0.03em] text-(--ui-text-primary)">
          {assistant.headline}
        </h1>
        <p className="mt-1.5 max-w-lg text-pretty text-[0.78rem] leading-5 text-(--ui-text-tertiary)">
          {assistant.body}
        </p>
        <div className="mt-4 flex w-full flex-wrap gap-1.5" data-testid="assistant-quick-actions">
          {assistant.actions.map(([label, prompt]) => (
            <Button
              className="h-7 rounded-md px-2.5 text-[0.66rem] font-medium"
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

  return (
    <div className="assistant-home mx-auto flex w-full max-w-2xl flex-col items-center px-5 py-8 text-center" data-slot="aui_intro">
      <div aria-hidden className="assistant-home__mark">
        <Sun className="size-5" />
      </div>
      <p className="mt-4 text-[0.6875rem] font-semibold uppercase tracking-[0.18em] text-(--ui-text-quaternary)">
        {assistant.eyebrow}
      </p>
      <h1 className="mt-2 text-balance text-2xl font-semibold tracking-[-0.035em] text-(--ui-text-primary) sm:text-[1.75rem]">
        {assistant.headline}
      </h1>
      <p className="mt-2 max-w-xl text-pretty text-sm leading-6 text-(--ui-text-tertiary)">
        {assistant.body}
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
