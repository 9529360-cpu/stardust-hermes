import { getRuntimeI18nLocale } from './runtime'
import type { Locale } from './types'

/**
 * Slash palette copy. Command names stay in the registry; only descriptions
 * and section headers change with the UI locale. Missing keys keep the
 * English string the catalog or desktop spec already produced.
 */
interface SlashPaletteCopy {
  browseSessions: string
  commands: Record<string, string>
  sections: Record<string, string>
  skinCurrent: string
  skinList: string
  skinNext: string
}

const ZH_SECTIONS: Record<string, string> = {
  Commands: '命令',
  Configuration: '配置',
  Exit: '退出',
  Info: '信息',
  Options: '选项',
  'Plugin commands': '插件命令',
  Session: '会话',
  Sessions: '会话',
  Skills: '技能',
  Themes: '主题',
  'Tools & Skills': '工具与技能',
  TUI: '终端',
  'User commands': '用户命令'
}

const ZH_COMMANDS: Record<string, string> = {
  '/agents': '显示正在运行的智能体和任务',
  '/approvals': '查看或设置危险命令的批准模式',
  '/battery': '在状态栏开关电量指示',
  '/bg': '在单独的后台会话里运行一条提示词',
  '/blueprint': '用蓝图模板配置一项自动化',
  '/branch': '把最新消息分支出新对话',
  '/browser': '管理浏览器 CDP 连接（connect / disconnect / status，仅本地网关）',
  '/btw': '旁问一句，不打断当前对话',
  '/bundles': '列出技能包（用 /名字 一次启用多个技能）',
  '/codex-runtime': '为 OpenAI/Codex 模型切换 Codex 运行时',
  '/compress': '压缩当前对话上下文',
  '/context': '查看上下文窗口：用量、分类、压缩和吞吐',
  '/debug': '上传调试报告并得到可分享的链接',
  '/diff': '显示工作区的 git 改动',
  '/egress': '显示 Docker 出站代理状态',
  '/export': '把配置文件导出为可分享的归档',
  '/focus': '开关专注视图：只显示你的提示词和最终回复',
  '/goal': '设定一个跨回合持续推进、直到完成的目标',
  '/handoff': '把此会话转交到消息平台',
  '/hatch': '生成一只新宠物',
  '/heartbeat': '空闲时按周期把一条提示词送回此会话',
  '/help': '显示桌面斜杠命令',
  '/import': '从分享的归档导入为新配置文件',
  '/init': '扫描仓库，生成或更新 AGENTS.md',
  '/journey': '打开记忆图谱：技能与记忆随时间的变化',
  '/learn': '从目录、链接、这段对话或笔记里学一项可复用技能',
  '/loop': '按周期在此会话重复运行一条提示词',
  '/memory': '审阅待写入的记忆，或开关批准门',
  '/moa': '用默认的多智能体预设跑一次，然后恢复你的模型',
  '/model': '切换此会话的模型',
  '/new': '开始一段新的桌面对话',
  '/palette': '打开模糊搜索的命令面板',
  '/personality': '选用一套预设人格',
  '/pet': '开关或领养一只宠物（/pet、/pet list、/pet boba）',
  '/plan': '把实现计划写成 Markdown，先不执行',
  '/profile': '切换当前配置文件',
  '/prompt': '在编辑器里写好下一条提示词再发送',
  '/queue': '把提示词排到下一回合，或列出、编辑、删除、移动、清空队列',
  '/refine': '立刻回顾这段对话，并把经验写入记忆或技能',
  '/resume': '恢复已保存的会话',
  '/retry': '重试上一条消息（重新发给智能体）',
  '/review': '派一个独立子智能体复查刚讨论的工作',
  '/rollback': '列出或恢复文件系统检查点（保留你的手动修改）',
  '/save': '把当前对话保存为 JSON',
  '/sessions': '浏览并恢复以前的会话',
  '/skin': '切换桌面主题，或切到下一个',
  '/status': '显示当前会话状态',
  '/steer': '在下一次工具调用后插入消息，不打断当前回合',
  '/stop': '停止当前回合和后台进程',
  '/subgoal': '为当前目标添加或管理额外标准',
  '/suggestions': '审阅建议的自动化（接受或忽略）',
  '/timestamps': '在消息和 /history 上开关时间戳',
  '/title': '重命名当前会话',
  '/tools': '管理工具：列出、禁用或启用',
  '/undo': '回退 N 轮用户消息并重新提问（默认 1 轮）',
  '/usage': '显示 token 用量和速率限制',
  '/version': '显示版本',
  '/wake': '控制桌面唤醒词（on / off / status）',
  '/whoami': '显示你的斜杠命令权限（管理员 / 用户）',
  '/worktree': '查看、列出、创建或清理隔离的 git 工作树',
  '/yolo': '切换 YOLO：自动批准危险命令'
}

const ZH_HANT_SECTIONS: Record<string, string> = {
  Commands: '命令',
  Configuration: '設定',
  Exit: '離開',
  Info: '資訊',
  Options: '選項',
  'Plugin commands': '外掛命令',
  Session: '工作階段',
  Sessions: '工作階段',
  Skills: '技能',
  Themes: '主題',
  'Tools & Skills': '工具與技能',
  TUI: '終端',
  'User commands': '使用者命令'
}

const ZH_HANT_COMMANDS: Record<string, string> = {
  '/agents': '顯示正在執行的代理和任務',
  '/approvals': '查看或設定危險命令的核准模式',
  '/battery': '在狀態列開關電量指示',
  '/bg': '在單獨的背景工作階段裡執行一則提示',
  '/blueprint': '用藍圖範本設定一項自動化',
  '/branch': '把最新訊息分支成新對話',
  '/browser': '管理瀏覽器 CDP 連線（connect / disconnect / status，僅本機閘道）',
  '/btw': '旁問一句，不打斷目前對話',
  '/bundles': '列出技能包（用 /名字 一次啟用多個技能）',
  '/codex-runtime': '為 OpenAI/Codex 模型切換 Codex 執行環境',
  '/compress': '壓縮目前對話上下文',
  '/context': '查看上下文視窗：用量、分類、壓縮和吞吐',
  '/debug': '上傳除錯報告並取得可分享的連結',
  '/diff': '顯示工作區的 git 變更',
  '/egress': '顯示 Docker 對外代理狀態',
  '/export': '把設定檔匯出成可分享的封存',
  '/focus': '開關專注檢視：只顯示你的提示和最終回覆',
  '/goal': '設定一個跨回合持續推進、直到完成的目標',
  '/handoff': '把此工作階段轉交到訊息平台',
  '/hatch': '生成一隻新寵物',
  '/heartbeat': '閒置時按週期把一則提示送回此工作階段',
  '/help': '顯示桌面斜線命令',
  '/import': '從分享的封存匯入為新設定檔',
  '/init': '掃描存放庫，生成或更新 AGENTS.md',
  '/journey': '打開記憶圖譜：技能與記憶隨時間的變化',
  '/learn': '從目錄、連結、這段對話或筆記裡學一項可再用技能',
  '/loop': '按週期在此工作階段重複執行一則提示',
  '/memory': '審閱待寫入的記憶，或開關核准門',
  '/moa': '用預設的多代理組合跑一次，然後恢復你的模型',
  '/model': '切換此工作階段的模型',
  '/new': '開始一段新的桌面對話',
  '/palette': '打開模糊搜尋的命令面板',
  '/personality': '選用一套預設人格',
  '/pet': '開關或領養一隻寵物（/pet、/pet list、/pet boba）',
  '/plan': '把實作計畫寫成 Markdown，先不執行',
  '/profile': '切換目前的設定檔',
  '/prompt': '在編輯器裡寫好下一則提示再送出',
  '/queue': '把提示排到下一回合，或列出、編輯、刪除、移動、清空佇列',
  '/refine': '立刻回顧這段對話，並把經驗寫入記憶或技能',
  '/resume': '恢復已儲存的工作階段',
  '/retry': '重試上一則訊息（重新發給代理）',
  '/review': '派一個獨立子代理複查剛討論的工作',
  '/rollback': '列出或恢復檔案系統檢查點（保留你的手動修改）',
  '/save': '把目前對話儲存為 JSON',
  '/sessions': '瀏覽並恢復先前的工作階段',
  '/skin': '切換桌面主題，或切到下一個',
  '/status': '顯示目前工作階段狀態',
  '/steer': '在下一次工具呼叫後插入訊息，不打斷目前回合',
  '/stop': '停止目前回合和背景處理程序',
  '/subgoal': '為目前目標新增或管理額外標準',
  '/suggestions': '審閱建議的自動化（接受或忽略）',
  '/timestamps': '在訊息和 /history 上開關時間戳',
  '/title': '重新命名目前工作階段',
  '/tools': '管理工具：列出、停用或啟用',
  '/undo': '回退 N 輪使用者訊息並重新提問（預設 1 輪）',
  '/usage': '顯示 token 用量和速率限制',
  '/version': '顯示版本',
  '/wake': '控制桌面喚醒詞（on / off / status）',
  '/whoami': '顯示你的斜線命令權限（管理員 / 使用者）',
  '/worktree': '查看、列出、建立或清理隔離的 git 工作樹',
  '/yolo': '切換 YOLO：自動核准危險命令'
}

const COPY: Record<Locale, SlashPaletteCopy> = {
  en: { browseSessions: '', commands: {}, sections: {}, skinCurrent: '', skinList: '', skinNext: '' },
  ja: { browseSessions: '', commands: {}, sections: {}, skinCurrent: '', skinList: '', skinNext: '' },
  ar: { browseSessions: '', commands: {}, sections: {}, skinCurrent: '', skinList: '', skinNext: '' },
  ru: { browseSessions: '', commands: {}, sections: {}, skinCurrent: '', skinList: '', skinNext: '' },
  zh: {
    browseSessions: '浏览全部会话…',
    commands: ZH_COMMANDS,
    sections: ZH_SECTIONS,
    skinCurrent: '（当前）',
    skinList: '显示可用的桌面主题',
    skinNext: '切换到下一个桌面主题'
  },
  'zh-hant': {
    browseSessions: '瀏覽全部工作階段…',
    commands: ZH_HANT_COMMANDS,
    sections: ZH_HANT_SECTIONS,
    skinCurrent: '（目前）',
    skinList: '顯示可用的桌面主題',
    skinNext: '切換到下一個桌面主題'
  }
}

const ENGLISH = {
  browseSessions: 'Browse all sessions…',
  skinCurrent: ' (current)',
  skinList: 'Show available desktop themes',
  skinNext: 'Cycle to the next desktop theme'
} as const

function phrase(key: keyof typeof ENGLISH): string {
  return COPY[getRuntimeI18nLocale()][key] || ENGLISH[key]
}

/** Description shown beside a slash command. `english` is the desktop spec or catalog text. */
export function slashCommandDescription(command: string, english: string): string {
  return COPY[getRuntimeI18nLocale()].commands[command] || english
}

/** Section header in the slash palette. Unknown names stay as authored. */
export function slashSectionLabel(name: string): string {
  return COPY[getRuntimeI18nLocale()].sections[name] || name
}

export function slashBrowseSessionsLabel(): string {
  return phrase('browseSessions')
}

export function slashSkinListLabel(): string {
  return phrase('skinList')
}

export function slashSkinNextLabel(): string {
  return phrase('skinNext')
}

export function slashSkinCurrentMark(): string {
  return phrase('skinCurrent')
}

export const SLASH_PALETTE_LOCALES = ['zh', 'zh-hant'] as const
export const SLASH_PALETTE_COMMANDS: Record<(typeof SLASH_PALETTE_LOCALES)[number], Record<string, string>> = {
  zh: ZH_COMMANDS,
  'zh-hant': ZH_HANT_COMMANDS
}
export const SLASH_PALETTE_SECTIONS: Record<(typeof SLASH_PALETTE_LOCALES)[number], Record<string, string>> = {
  zh: ZH_SECTIONS,
  'zh-hant': ZH_HANT_SECTIONS
}
