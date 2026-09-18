import type { Locale } from '@/i18n'

export interface WorkspaceOverviewCopy {
  workspace: string
  noProject: string
  currentResult: string
  projectContext: string
  filesTouched: string
  summary: string
  quickAccess: string
  newSession: string
  continueTask: string
  chooseProjectTools: string
  chooseProjectAction: string
  chooseProjectSummary: string
  working: string
  ready: string
  branch: string
  sync: string
  noRepository: string
  workingTreeClean: string
  review: string
  files: string
  terminal: string
  localEdits: string
  hiddenChanges: string
  staged: string
  added: string
  removed: string
  session: string
  workingTree: string
  reviewState: string
  inProgress: string
  idle: string
  clean: string
  notStaged: string
  nothingPending: string
  summaryWorkingClean: string
  summaryReadyClean: string
  syncValue: (ahead: number, behind: number) => string
  changedFiles: (count: number) => string
  more: (count: number) => string
  changed: (count: number) => string
  stagedCount: (count: number) => string
  summaryWorkingChanged: (count: number) => string
  summaryReadyChanged: (count: number) => string
}

const en: WorkspaceOverviewCopy = {
  workspace: 'Context',
  noProject: 'No project selected',
  currentResult: 'Current goal',
  projectContext: 'Project information',
  filesTouched: 'File changes',
  summary: 'System status',
  quickAccess: 'Quick actions',
  newSession: 'New session',
  continueTask: 'Continue task',
  chooseProjectTools: 'Choose a project to enable workspace tools.',
  chooseProjectAction: 'Choose project',
  chooseProjectSummary: 'Choose a project to give this workspace persistent file and review context.',
  working: 'Working',
  ready: 'Ready',
  branch: 'Branch',
  sync: 'Sync',
  noRepository: 'No repository',
  workingTreeClean: 'Working tree clean',
  review: 'Review',
  files: 'Files',
  terminal: 'Terminal',
  localEdits: 'Local edits will appear here while you work.',
  hiddenChanges: 'The repository reports changes outside the capped file preview. Open Review to inspect them.',
  staged: 'staged',
  added: 'added',
  removed: 'removed',
  session: 'Session',
  workingTree: 'Working tree',
  reviewState: 'Review state',
  inProgress: 'In progress',
  idle: 'Idle',
  clean: 'Clean',
  notStaged: 'Not staged',
  nothingPending: 'Nothing pending',
  summaryWorkingClean: 'Work is in progress. File changes will appear here as soon as they land.',
  summaryReadyClean: 'The workspace is clean and ready for the next task.',
  syncValue: (ahead, behind) => `${ahead} ahead · ${behind} behind`,
  changedFiles: count => `${count} file${count === 1 ? '' : 's'} changed`,
  more: count => `+${count} more`,
  changed: count => `${count} changed`,
  stagedCount: count => `${count} staged`,
  summaryWorkingChanged: count =>
    `Work is in progress with ${count} changed file${count === 1 ? '' : 's'} in the current workspace.`,
  summaryReadyChanged: count => `${count} changed file${count === 1 ? ' is' : 's are'} ready for review.`
}

const zh: WorkspaceOverviewCopy = {
  workspace: '上下文',
  noProject: '未选择项目',
  currentResult: '当前目标',
  projectContext: '项目信息',
  filesTouched: '文件改动',
  summary: '系统状态',
  quickAccess: '快捷操作',
  newSession: '新会话',
  continueTask: '继续处理任务',
  chooseProjectTools: '选择项目后可使用工作区工具。',
  chooseProjectAction: '选择项目',
  chooseProjectSummary: '选择项目，为当前工作区提供持续的文件与审查上下文。',
  working: '进行中',
  ready: '就绪',
  branch: '分支',
  sync: '同步',
  noRepository: '非 Git 仓库',
  workingTreeClean: '工作区干净',
  review: '审查',
  files: '文件',
  terminal: '终端',
  localEdits: '你工作时，本地改动会显示在这里。',
  hiddenChanges: '仓库还有未显示的改动，请打开“审查”查看。',
  staged: '已暂存',
  added: '新增',
  removed: '删除',
  session: '会话',
  workingTree: '工作区',
  reviewState: '审查状态',
  inProgress: '进行中',
  idle: '空闲',
  clean: '干净',
  notStaged: '未暂存',
  nothingPending: '无待处理项',
  summaryWorkingClean: '任务正在进行，文件改动落地后会显示在这里。',
  summaryReadyClean: '工作区干净，可以开始下一个任务。',
  syncValue: (ahead, behind) => `超前 ${ahead} · 落后 ${behind}`,
  changedFiles: count => `${count} 个文件有改动`,
  more: count => `另有 ${count} 个`,
  changed: count => `${count} 个改动`,
  stagedCount: count => `${count} 个已暂存`,
  summaryWorkingChanged: count => `任务正在进行，当前工作区有 ${count} 个文件发生改动。`,
  summaryReadyChanged: count => `${count} 个文件的改动已可审查。`
}

const zhHant: WorkspaceOverviewCopy = {
  workspace: '上下文',
  noProject: '未選擇專案',
  currentResult: '目前目標',
  projectContext: '專案資訊',
  filesTouched: '檔案變更',
  summary: '系統狀態',
  quickAccess: '快速操作',
  newSession: '新對話',
  continueTask: '繼續處理任務',
  chooseProjectTools: '選擇專案後可使用工作區工具。',
  chooseProjectAction: '選擇專案',
  chooseProjectSummary: '選擇專案，讓目前工作區保有檔案與審查內容。',
  working: '進行中',
  ready: '就緒',
  branch: '分支',
  sync: '同步',
  noRepository: '非 Git 儲存庫',
  workingTreeClean: '工作區乾淨',
  review: '審查',
  files: '檔案',
  terminal: '終端機',
  localEdits: '工作時，本機變更會顯示在這裡。',
  hiddenChanges: '儲存庫還有未顯示的變更，請開啟「審查」查看。',
  staged: '已暫存',
  added: '新增',
  removed: '刪除',
  session: '對話',
  workingTree: '工作區',
  reviewState: '審查狀態',
  inProgress: '進行中',
  idle: '閒置',
  clean: '乾淨',
  notStaged: '未暫存',
  nothingPending: '沒有待處理項目',
  summaryWorkingClean: '工作正在進行，檔案變更出現後會顯示在這裡。',
  summaryReadyClean: '工作區乾淨，可以開始下一個任務。',
  syncValue: (ahead, behind) => `超前 ${ahead} · 落後 ${behind}`,
  changedFiles: count => `${count} 個檔案有變更`,
  more: count => `另有 ${count} 個`,
  changed: count => `${count} 個變更`,
  stagedCount: count => `${count} 個已暫存`,
  summaryWorkingChanged: count => `工作正在進行，目前工作區有 ${count} 個檔案發生變更。`,
  summaryReadyChanged: count => `${count} 個檔案的變更已可審查。`
}

const ja: WorkspaceOverviewCopy = {
  workspace: 'コンテキスト',
  noProject: 'プロジェクト未選択',
  currentResult: '現在の目標',
  projectContext: 'プロジェクト情報',
  filesTouched: 'ファイル変更',
  summary: 'システム状態',
  quickAccess: 'クイック操作',
  newSession: '新しいセッション',
  continueTask: 'タスクを続ける',
  chooseProjectTools: 'プロジェクトを選択するとワークスペースツールを利用できます。',
  chooseProjectAction: 'プロジェクトを選択',
  chooseProjectSummary: 'プロジェクトを選択して、ファイルとレビューのコンテキストをこのワークスペースに保持します。',
  working: '作業中',
  ready: '準備完了',
  branch: 'ブランチ',
  sync: '同期',
  noRepository: 'Git リポジトリではありません',
  workingTreeClean: '作業ツリーはクリーンです',
  review: 'レビュー',
  files: 'ファイル',
  terminal: 'ターミナル',
  localEdits: 'ローカルの変更は作業中にここへ表示されます。',
  hiddenChanges: 'プレビュー外にも変更があります。レビューを開いて確認してください。',
  staged: 'ステージ済み',
  added: '追加',
  removed: '削除',
  session: 'セッション',
  workingTree: '作業ツリー',
  reviewState: 'レビュー状態',
  inProgress: '進行中',
  idle: '待機中',
  clean: 'クリーン',
  notStaged: '未ステージ',
  nothingPending: '保留なし',
  summaryWorkingClean: '作業中です。ファイル変更は反映され次第ここに表示されます。',
  summaryReadyClean: 'ワークスペースはクリーンで、次のタスクを開始できます。',
  syncValue: (ahead, behind) => `${ahead} ahead · ${behind} behind`,
  changedFiles: count => `${count} 件のファイルを変更`,
  more: count => `ほか ${count} 件`,
  changed: count => `${count} 件変更`,
  stagedCount: count => `${count} 件ステージ済み`,
  summaryWorkingChanged: count => `作業中です。現在のワークスペースで ${count} 件のファイルが変更されています。`,
  summaryReadyChanged: count => `${count} 件のファイル変更をレビューできます。`
}

const ru: WorkspaceOverviewCopy = {
  workspace: 'Контекст',
  noProject: 'Проект не выбран',
  currentResult: 'Текущая цель',
  projectContext: 'Сведения о проекте',
  filesTouched: 'Изменения файлов',
  summary: 'Состояние системы',
  quickAccess: 'Быстрые действия',
  newSession: 'Новая сессия',
  continueTask: 'Продолжить задачу',
  chooseProjectTools: 'Выберите проект, чтобы использовать инструменты рабочей области.',
  chooseProjectAction: 'Выбрать проект',
  chooseProjectSummary: 'Выберите проект, чтобы сохранить здесь контекст файлов и проверки.',
  working: 'В работе',
  ready: 'Готово',
  branch: 'Ветка',
  sync: 'Синхронизация',
  noRepository: 'Не Git-репозиторий',
  workingTreeClean: 'Рабочее дерево чистое',
  review: 'Проверка',
  files: 'Файлы',
  terminal: 'Терминал',
  localEdits: 'Локальные изменения появятся здесь во время работы.',
  hiddenChanges: 'Есть изменения вне краткого списка. Откройте проверку, чтобы увидеть их.',
  staged: 'подготовлено',
  added: 'добавлено',
  removed: 'удалено',
  session: 'Сессия',
  workingTree: 'Рабочее дерево',
  reviewState: 'Статус проверки',
  inProgress: 'В процессе',
  idle: 'Ожидание',
  clean: 'Чисто',
  notStaged: 'Не подготовлено',
  nothingPending: 'Нет ожидающих изменений',
  summaryWorkingClean: 'Работа продолжается. Изменения файлов появятся здесь сразу после записи.',
  summaryReadyClean: 'Рабочая область чистая и готова к следующей задаче.',
  syncValue: (ahead, behind) => `впереди ${ahead} · позади ${behind}`,
  changedFiles: count => `Изменено файлов: ${count}`,
  more: count => `ещё ${count}`,
  changed: count => `изменено: ${count}`,
  stagedCount: count => `подготовлено: ${count}`,
  summaryWorkingChanged: count => `Работа продолжается; в текущей области изменено файлов: ${count}.`,
  summaryReadyChanged: count => `Файлов с изменениями для проверки: ${count}.`
}

const ar: WorkspaceOverviewCopy = {
  workspace: 'السياق',
  noProject: 'لم يتم اختيار مشروع',
  currentResult: 'الهدف الحالي',
  projectContext: 'معلومات المشروع',
  filesTouched: 'تغييرات الملفات',
  summary: 'حالة النظام',
  quickAccess: 'إجراءات سريعة',
  newSession: 'جلسة جديدة',
  continueTask: 'متابعة المهمة',
  chooseProjectTools: 'اختر مشروعاً لتفعيل أدوات مساحة العمل.',
  chooseProjectAction: 'اختيار مشروع',
  chooseProjectSummary: 'اختر مشروعاً للاحتفاظ بسياق الملفات والمراجعة في مساحة العمل.',
  working: 'قيد العمل',
  ready: 'جاهز',
  branch: 'الفرع',
  sync: 'المزامنة',
  noRepository: 'ليس مستودع Git',
  workingTreeClean: 'شجرة العمل نظيفة',
  review: 'مراجعة',
  files: 'الملفات',
  terminal: 'الطرفية',
  localEdits: 'ستظهر التعديلات المحلية هنا أثناء العمل.',
  hiddenChanges: 'توجد تغييرات أخرى خارج المعاينة. افتح المراجعة لفحصها.',
  staged: 'مجهز',
  added: 'مضاف',
  removed: 'محذوف',
  session: 'الجلسة',
  workingTree: 'شجرة العمل',
  reviewState: 'حالة المراجعة',
  inProgress: 'قيد التنفيذ',
  idle: 'خامل',
  clean: 'نظيف',
  notStaged: 'غير مجهز',
  nothingPending: 'لا شيء معلق',
  summaryWorkingClean: 'العمل جارٍ. ستظهر تغييرات الملفات هنا فور تسجيلها.',
  summaryReadyClean: 'مساحة العمل نظيفة وجاهزة للمهمة التالية.',
  syncValue: (ahead, behind) => `${ahead} متقدم · ${behind} متأخر`,
  changedFiles: count => `${count} ملفاً معدلاً`,
  more: count => `${count} إضافية`,
  changed: count => `${count} تغيير`,
  stagedCount: count => `${count} مجهز`,
  summaryWorkingChanged: count => `العمل جارٍ مع ${count} ملفاً معدلاً في مساحة العمل الحالية.`,
  summaryReadyChanged: count => `${count} ملفاً معدلاً جاهزاً للمراجعة.`
}

export const WORKSPACE_OVERVIEW_COPY = {
  ar,
  en,
  ja,
  ru,
  zh,
  'zh-hant': zhHant
} satisfies Record<Locale, WorkspaceOverviewCopy>
