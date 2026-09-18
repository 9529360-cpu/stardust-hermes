交接日期：2026-09-18。用户要求停止当前前端开发，在新对话继续。
这是“真实工作树续接”交接，不是发布说明，也不是要求清理工作树。
本文件优先于 HANDOFF_FRONTEND_2026-09-17.md 中已经过时的能力页断点。

## 1. 产品方向与并行分工

Stardust 的目标是长期个人助理，不是纯开发代理工作台。

产品原则：
- 普通问答、日常工作处理、资料整理、自动任务、外部消息平台与强编码能力并存。
- 不要把普通提问自动变成编码任务。
- 首页和新对话默认保持助理导向。
- 项目 / 文件 / Git Review / Terminal 是按需展开的工作区能力，不应该强迫用户先选项目。
- 用户明确要求移除 Hermes / Nous 内置账户、匿名账户、免费额度与充值引导。
- 这不等于删除用户自己配置的模型 API Key、第三方服务授权或远程网关认证。

当前项目同时有另一个后端工程对话在工作。
本对话负责前端 / Electron 桌面 UI。
不要覆盖、回退、整理或提交不属于本前端工作的后端改动。

## 2. 真实开发环境与保护边界

- 使用 Remote Desktop Commander。
- Windows 设备：豹
- deviceId：8d61dc42-f2a2-42f0-8bac-9fc9b4170939
- 仓库：D:\远程工作区\stardust-hermes
- 前端目录：D:\远程工作区\stardust-hermes\apps\desktop
- 当前分支：dev/stardust-assistant-ui
- 当前 HEAD：0c71846e37873c6144f93dda91377dd7413909dd
- 当前工作树仍然非常脏，含前端、后端和测试的并行未提交修改。
- 本对话没有 commit、push、reset、clean 或覆盖工作树。

接手后先读取：
- 根 AGENTS.md
- apps/desktop/AGENTS.md
- apps/desktop/src/AGENTS.md
- 本文件

然后重新执行 git status，以接手时真实工作树为准。

禁止：
- git reset --hard
- git clean
- 整体覆盖文件
- 为了“干净”而撤销不明来源修改
- 对 Hermes 兼容名做机械全局改名

保留这些兼容性/协议名称，除非有明确 owner 证据：
- CLI 命令 hermes ...
- HERMES_HOME
- window.hermesDesktop
- 后端 RPC / gateway 字段
- 插件 canonical key / stable ID
- 目录、app ID、协议字段

## 3. 本轮已完成：能力页不再是 Hermes 仓库管理界面

### 3.1 Skills 详情信息层级

文件：
- apps/desktop/src/app/skills/index.tsx
- apps/desktop/src/app/skills/index.test.tsx

已将 Skill 详情从“默认直接铺完整 SKILL.md/frontmatter”调整为两层。

普通用户默认看到：
- 技能描述
- “它能做什么”
- 运行要求
- 支持平台

高级信息仍完整保留，但默认折叠：
- 原始说明（完整 SKILL.md body）
- 技术信息（frontmatter metadata）

重要：
- 没有删除原始技能内容。
- 已安装技能和官方未安装技能预览走同一信息层级。
- 动态/agent skill 的编辑、归档能力未删除。
- 原始说明展开/收起已在 Electron E2E 中验证。

新增 data hooks：
- data-skill-overview
- data-skill-raw-instructions
- data-skill-technical-metadata

### 3.2 Skills 分类展示本地化

已知内置 category 在中文界面转为产品展示名，例如：
- Productivity -> 效率
- Creative -> 创意
- Research -> 研究
- Autonomous-Ai-Agents -> 自主智能体
- Software-Development -> 软件开发

实现原则：
- 只本地化已知 Stardust 分类。
- 不修改 skill.category 原始协议值。
- 未知 / 第三方自定义分类继续 prettyName 原值回退。

i18n 新增：
- t.skills.skillCategoryLabels

### 3.3 Toolsets 产品化展示

当前能力页的工具集展示已经不是直接把 backend 英文 description 当产品说明：
- 已知工具集有中文展示名。
- 已知工具集有中文产品级说明。
- 长技术说明降到“技术说明”折叠区域。
- 工具数量使用本地化 toolCount。
- 默认配置 / 当前连接使用 i18n，不再显示 Hermes (default)。

相关 i18n：
- t.skills.toolsetLabels
- t.skills.toolsetDescriptions
- t.skills.technicalDetails
- t.skills.toolCount
- t.skills.defaultProfile
- t.skills.currentConnection

### 3.4 Plugins 展示名与 Agent 术语已收口

文件：
- apps/desktop/src/app/skills/plugins-tab.tsx
- apps/desktop/src/i18n/en.ts
- apps/desktop/src/i18n/zh.ts
- apps/desktop/src/i18n/zh-hant.ts

内置插件的“真实 identity”和“用户展示名”已经分开。

界面展示：
- hermes-bots -> 智能体
- kanban -> 任务看板
- radio -> 电台

底层仍保持：
- hermes-bots
- kanban
- radio
以及原 canonical key / package id / 开关 RPC / deep-link identity。

中文插件页把可见的 Agent 改为“智能体”：
- 默认配置 中的智能体
- 智能体 + 桌面
- 智能体插件
- 相关空状态和安装提示

新增：
- t.skills.plugins.bundledNames
- 现有 bundledDescriptions 已有中文覆盖

注意：
- 英文 locale 仍使用 Agent/Agents，属于英文产品文案，不是协议问题。
- 插件 catalog iframe 仍是外部 Hermes docs/catalog，当前没有重做 catalog 服务本身。

## 4. 能力页 E2E 已从失败断点修到通过

文件：
apps/desktop/e2e/personal-assistant-capabilities.spec.ts

旧交接中的失败断点已经过时：
- 不再查找不存在的“Agent 插件”旧标题。
- 改为断言真实统一插件表格的 columnheader。
- 增加 Skills 加载完成后的正向断言，不再只用“英文不存在”作为假阳性。
- 验证技能“它能做什么”存在。
- 验证分类“效率”存在。
- 验证原始说明 / 技术信息默认折叠。
- 实际点击“原始说明”并验证 pre 可见，再收起。
- Toolsets 验证“浏览器自动化”，并验证技术说明折叠。
- Plugins 验证“默认配置 中的智能体”“智能体 / 任务看板 / 电台”和中文描述。

当前该 E2E 已通过。

## 5. 首页工作区右栏：确认已有正确行为，不要误改

本轮曾怀疑首页右侧“未选择项目”工作区占宽度会破坏普通聊天体验。

检查真实 owner 后确认当前代码已经正确：
- src/store/right-context.ts 的默认值是 false。
- 首页普通新对话默认不显示 WorkspaceOverview。
- 只有用户点击“工作区”或主动打开 Files/Review 时才展开右侧 context rail。
- personal-assistant-home.spec.ts 明确断言默认 overview 不可见，点击“工作区”后才出现。

之前看到带右栏的截图，是 E2E 主动点击“工作区”之后截的，不是默认首屏。

因此这里做了 evidence-backed no-change。
不要为了旧截图把工作区能力删掉或改变默认行为。

另外，旧交接里“未选项目时显示工作区干净”的问题现在已经收掉：
- E2E 断言未选项目时不会出现“工作区干净”。
- WorkspaceOverview 会显示“未选择项目 / 选择项目后可使用工作区工具”。

## 6. 当前准确断点：下一步做“消息平台”的展示层收口

文件：
apps/desktop/src/app/messaging/index.tsx
apps/desktop/src/app/messaging/index.test.tsx
apps/desktop/src/i18n/types.ts
apps/desktop/src/i18n/en.ts
apps/desktop/src/i18n/zh.ts
apps/desktop/src/i18n/zh-hant.ts

本轮只完成了审查，没有留下消息平台产品改动。

已经确认消息平台结构本身是好的，不需要重构：
- 必填 / 推荐 / 高级字段已经分层。
- pairing pending/approved 有独立交互。
- Telegram QR setup 有独立流程。
- profile scope 和 gateway restart 状态已有 owner。
- FIELD_COPY 已能把很多 env key 转成可读 label/help。

真正需要处理的是 presentation。

### 问题 A：详情标题下仍直接显示 backend platform.description

PlatformDetail 目前直接渲染 platform.description。
中文 UI 如果 backend 返回英文 description，会直接露英文。

建议下一步：
- 增加 t.messaging.platformDescriptions 显示层映射。
- 用 platform.id 查已知 Stardust 平台的产品级中文描述。
- 未知 / 插件平台继续 fallback 到 backend platform.description。
- 不修改后端 payload 或 platform.id。

本轮曾临时加过 platformDescriptions 类型/英文空 map 作为探索，但在交接前已经完整撤回，避免留下半成品。
当前 source 已回到可 typecheck 状态。
新对话不要以为 platformDescriptions 已经存在。

### 问题 B：中文 messaging 文案仍混有 Hermes 产品名

src/i18n/zh.ts 的 messaging 区里可继续收：
- Telegram QR subtitle：“凭据仅保存在此 Hermes 安装中”
- quickHelp：“Hermes 会自动创建机器人…”
- 一些 platformIntro：“让 Hermes 指向… / Hermes 自带… / Hermes 会通过…”
- 其他普通用户可见产品说明

处理原则：
- 普通产品文案改成“本应用 / Stardust / 助理”等合适表达。
- 不要改合法技术命令，例如 hermes gateway setup。
- 不要改后端字段名、URL、token key、CLI 兼容名。
- PLATFORM_INTRO 英文 fallback 可以继续作为未知 locale fallback，但中文已知平台优先用 t.messaging.platformIntro。

### 问题 C：PlatformRow / 搜索是否需要展示名本地化

目前平台 name 来自 backend，例如 Telegram / Discord / Microsoft Teams，这些品牌名可以保留。
不要把品牌名硬翻译。
重点是描述和 setup copy，不是平台 identity。

## 7. 当前验证台账

### TypeScript

2026-09-18 交接前重新执行：
npm run typecheck

结果：exit code 0。
包含：
- renderer tsconfig
- electron tsconfig
- e2e tsconfig

### diff check

交接前重新执行：
git diff --check -- apps/desktop

结果：DIFF_CHECK_EXIT=0。

### Skills / Plugins UI tests

此前联合执行：
npm run test:ui -- src/app/skills/index.test.tsx src/app/skills/plugins-tab.test.tsx

结果：29 passed。

之后插件展示名有新增修改，再单独执行：
npm run test:ui -- src/app/skills/plugins-tab.test.tsx

结果：16 passed。

插件测试仍有既存 React act(...) warnings；不是本轮失败。
不要为了 warning 扩大当前产品范围。

### Production build

插件展示名收口后执行：
npm run build

结果：exit code 0。
包括：
- Vite renderer build
- electron-main.mjs
- electron-preload.js
- native deps staging
- assert-dist-built

仍有既存 warning：
- Vite native config / __dirname
- advancedChunks deprecated
- ineffective dynamic imports
- dirty working tree build stamp

这些不是当前产品验收阻塞。

### Electron E2E

插件展示名和 Skills 信息层级完成后执行：

npx playwright test e2e/personal-assistant-capabilities.spec.ts e2e/personal-assistant-home.spec.ts e2e/personal-assistant-settings.spec.ts e2e/personal-assistant-tasks.spec.ts --workers=1

结果：4 passed。

具体：
- capabilities：通过
- home：通过
- settings：通过
- tasks：通过

E2E 使用 setupMockBackend()，证明真实 Electron UI / 路由 / 交互，不证明真实模型调用或第三方平台 API 已通。

## 8. 当前仍未覆盖的产品边界

优先级从高到低：

1. 消息平台展示层中文收口，见第 6 节。
2. 继续审查其他一级入口是否把 backend / Hermes 管理台术语直接暴露给普通用户。
3. 设置与 onboarding 的内置账户移除仍要和后端并行结果一起做最终整体验收。
4. Aurora 原生 time input 的系统时钟图标对比度此前观察到偏暗，本轮未处理。
5. 没有验证真实模型请求、真实 Telegram/Discord/Slack 连接、真实 cron 投递。
6. 没有做安装器打包、发布、自动更新或用户机器升级。
7. 没有执行全量 Vitest / 全量 Playwright / lint。

## 9. 重要文件索引

能力页：
- apps/desktop/src/app/skills/index.tsx
- apps/desktop/src/app/skills/index.test.tsx
- apps/desktop/src/app/skills/plugins-tab.tsx
- apps/desktop/src/app/skills/plugins-tab.test.tsx
- apps/desktop/src/app/skills/embedded-hub-picker.tsx
- apps/desktop/e2e/personal-assistant-capabilities.spec.ts

消息平台下一断点：
- apps/desktop/src/app/messaging/index.tsx
- apps/desktop/src/app/messaging/index.test.tsx
- apps/desktop/src/app/messaging/telegram-qr-setup.tsx
- apps/desktop/src/i18n/types.ts
- apps/desktop/src/i18n/en.ts
- apps/desktop/src/i18n/zh.ts
- apps/desktop/src/i18n/zh-hant.ts

首页 / 工作区：
- apps/desktop/src/app/contrib/workspace-overview.tsx
- apps/desktop/src/app/contrib/workspace-overview-copy.ts
- apps/desktop/src/app/contrib/personal-product-nav.tsx
- apps/desktop/src/store/right-context.ts
- apps/desktop/src/app/contrib/controller.tsx
- apps/desktop/e2e/personal-assistant-home.spec.ts

设置 / 账户方向：
- apps/desktop/src/app/settings/index.tsx
- apps/desktop/src/app/settings/model-settings.tsx
- apps/desktop/src/app/settings/moved-tabs.ts
- apps/desktop/src/app/settings/uninstall-section.tsx
- apps/desktop/e2e/personal-assistant-settings.spec.ts

任务：
- apps/desktop/src/app/cron/index.tsx
- apps/desktop/src/app/cron/blueprints.tsx
- apps/desktop/e2e/personal-assistant-tasks.spec.ts

## 10. 工作树归属提醒

git status 中仍有大量后端修改，包括：
- agent/
- hermes_cli/
- tui_gateway/
- gateway/
- tools/
- plugins/
- tests/
- SOUL.md
等。

这些主要来自并行后端工程工作。
不要把整个 status 当作前端成果。
不要批量 stage。

前端也有很多在本轮之前就存在的修改。
接手时先看具体 diff 和 owner，不要用“这个文件被修改了”推断“本轮负责全部内容”。

当前仍有 untracked：
- BACKEND_HANDOFF_20260917_ACCOUNT_DECOUPLING.md
- HANDOFF_FRONTEND_2026-09-17.md
- 本文件 HANDOFF_FRONTEND_2026-09-18.md
- 4 个 personal-assistant E2E spec
- workspace-overview-copy.ts
- composer placeholder test
- 若干后端测试

不要 clean。

## 11. 新对话恢复顺序

建议新对话直接这样恢复：

1. 使用 Veteran Full-Stack Engineer（runtime-regression-debugger）和 Remote Desktop Commander。
2. 连接 Windows 设备“豹”。
3. 读取本文件。
4. 读取根 AGENTS.md、apps/desktop/AGENTS.md、apps/desktop/src/AGENTS.md。
5. 检查实际 git status / diff / branch / HEAD。
6. 不重新 clone，不重新做全仓架构调研。
7. 从 messaging 展示层继续。
8. 每一刀保持“产品展示名 != 协议 identity”的原则。
9. 改完先跑 messaging 单元/组件测试，再 typecheck/build，再加入 4 个 personal-assistant E2E。
10. 最终看真实 Electron 终态，不以 build green 代替 UI 验收。

可用命令（PowerShell，apps/desktop）：

Set-Location -LiteralPath 'D:\远程工作区\stardust-hermes\apps\desktop'

npm run test:ui -- src/app/messaging/index.test.tsx
npm run test:ui -- src/app/skills/index.test.tsx src/app/skills/plugins-tab.test.tsx
npm run typecheck
npm run build

npx playwright test e2e/personal-assistant-capabilities.spec.ts e2e/personal-assistant-home.spec.ts e2e/personal-assistant-settings.spec.ts e2e/personal-assistant-tasks.spec.ts --workers=1

不要并行跑多个 build；prebuild 会清共用产物。

## 12. 给下一位助手的一句话

从“消息平台详情直接显示 backend 英文 description，以及中文 setup/intro 仍混有 Hermes 产品名”继续；只改展示层，保留品牌名、CLI 命令和协议 identity。能力页已经完成信息层级、本地化分类和插件展示名收口，并且 4 个 Electron 主路径 E2E 全绿。不要重新做这些，也不要误把点击后展开的工作区右栏当成默认首屏问题。


---

# 13. 最新补充交接：工作空间视觉重构 / 真实 Electron 验证

> 本节晚于上面的第 11、12 节；**下一对话以本节为准**。
> 上面“从 messaging 展示层继续”的断点已经过时。当前最新断点是：**继续打磨 Stardust 工作空间首页，并逐步把设计预览数据换成真实运行数据。**

## 13.1 本轮实际完成

本轮没有重做架构，也没有重新 clone。直接在真实 Windows 开发机“豹”的现有工作树继续。

当前分支：

`dev/stardust-assistant-ui`

本轮把原先偏 Hermes/聊天软件的工作区继续往“日常助理 + 强编码能力 + 实时开发工作台”方向推进，主要完成：

- 新的 `#/workspace` 工作空间主视觉与信息层级。
- 左侧产品导航继续收口为 Stardust 产品入口，不再像传统聊天侧边栏。
- 中央工作区加入：
  - 当前任务 / 项目头部
  - 任务执行进度时间线
  - Agent 实时输出 / terminal-like 状态
  - Git Diff 文件列表与简化 diff 预览
  - 实时预览卡片
  - 底部自然语言命令输入 / 快捷动作
- 右侧 Overview 改成“项目 / 工作环境 / 最近活动 / 当前目标”的工作台信息架构。
- 进入 workspace 时自动打开右侧 Overview：
  - `setRightContextOpen(true)`
  - `revealTreePane('workspace-overview')`
- 为设计对照增加 **仅 DEV 生效** 的 workspace design-preview 数据层：
  - localStorage key: `stardust.ui.workspaceDesignPreview`
  - 只有 `import.meta.env.DEV` 且 key 为 `1` 时启用。
  - 生产构建不会因为 localStorage key 启用这些 mock 数据。
- 右栏宽度从 255px 调成 270px（min 242 / max 274），更接近当前视觉方案。
- Aurora 下左栏出现“内层 202px、外层仍占 260px”的死空列问题已处理：
  - workspace 路由下把 `spl-root` 的 sessions 外层 wrapper 固定为 202px。
  - 这是 route-scoped CSS，不是全局改 layout model。
- 去掉了中央 workspace 底部重复的大地球图，保留更克制的背景层；导航和预览内仍有视觉资产。

## 13.2 本轮重点文件

本轮直接重点修改：

- `apps/desktop/src/app/workspace/index.tsx`
- `apps/desktop/src/app/workspace/design-preview.ts`
- `apps/desktop/src/app/workspace/workspace.css`
- `apps/desktop/src/app/contrib/workspace-overview.tsx`
- `apps/desktop/src/app/contrib/personal-product-nav.tsx`
- `apps/desktop/src/app/contrib/personal-product-nav.css`

相关既有接线仍依赖：

- `apps/desktop/src/app/contrib/surfaces.tsx`
- `apps/desktop/src/app/contrib/layout-presets.ts`
- `apps/desktop/src/app/routes.ts`
- `apps/desktop/src/app/contrib/controller.tsx`
- `apps/desktop/src/store/right-context.ts`

注意：

- `apps/desktop/src/app/workspace/` 当前是 **untracked 目录**，因此普通 `git diff --stat` 不会显示其内容。
- 不要因为 diff stat 没出现 workspace 目录就误以为它没改。

## 13.3 Design Preview 数据说明

文件：

`apps/desktop/src/app/workspace/design-preview.ts`

当前设计预览数据包括：

- 项目：`Stardust / Web App`
- branch：`feature/ui-refactor`
- 当前任务：`继续开发 Stardust`
- 任务步骤 / 运行时长
- terminal 输出
- 4 个文件的 Git Diff 摘要
- 最近活动
- 当前目标
- fake preview target: `http://localhost:5173`

这些数据只用于 DEV 下视觉验证，不应直接当成最终业务数据源。

下一步应逐项替换为真实 store / gateway / repo status / task thread / terminal / preview target 数据，但不要在没有真实数据时破坏当前 UI 骨架。

## 13.4 当前真实 Electron 运行状态

交接时真实 Electron renderer 仍可通过：

- CDP: `http://127.0.0.1:9222`
- renderer: `http://127.0.0.1:5174/#/workspace`

交接时 viewport 实测：

- 1564 × 1111

最终测得主布局：

- 左 sessions/product nav：202px
- 中间 workspace：约 1092px
- 右 overview：270px

中央卡片在该尺寸下无主滚动溢出，主 workspace scrollTop = 0。

设计预览 key 已在这个开发 renderer 的 localStorage 中打开。

如果新对话 Electron 进程还活着，可直接继续 CDP 实时看终态；如果进程重启，不要重新做架构调研，只恢复 dev 启动和这个 localStorage key 即可。

临时 CDP 截图脚本不在仓库，在：

`D:\GPTcodex\Temp\stardust-hermes-ui-dev\live-capture.mjs`

最近的真实截图在：

`D:\GPTcodex\Temp\stardust-hermes-ui-dev\live-workspace.png`

不要把这些 Temp 文件提交进仓库。

## 13.5 本轮验证结果

已通过：

1. `npm run typecheck`
   - exit code 0

2. `npm run build`
   - exit code 0
   - `assert-dist-built` 通过
   - 仅有已知 warning：
     - Vite config native loader / `__dirname`
     - `advancedChunks` deprecated
     - ineffective dynamic import
     - working tree dirty build stamp warning

3. Playwright：
   - `e2e/personal-assistant-home.spec.ts`
     - 1 passed
   - `e2e/personal-assistant-capabilities.spec.ts`
   - `e2e/personal-assistant-settings.spec.ts`
   - `e2e/personal-assistant-tasks.spec.ts`
     - 3 passed

4. `git diff --check -- apps/desktop`
   - exit code 0
   - 只有 `workspace-overview.tsx` 的 CRLF/LF warning，无 whitespace error。

## 13.6 当前已知技术点 / 不要误修

### 左栏 202px 的 CSS

`personal-product-nav.css` 里有：

`:root:has([data-personal-sidebar-shell][data-personal-sidebar-view='workspace']) [data-tree-split='spl-root'] > div:has(> [data-tree-group='grp-sessions'])`

并强制：

- flex: 0 0 202px
- min-width: 202px
- max-width: 202px

原因：pane model 本身仍把 sessions pane 的 declared width/minWidth 设为 237px，而 Aurora CSS 只把内层 group max-width 压到 202px，导致外层还保留 260px / 237px 空间，中央左边出现明显死空列。

这个修复当前只针对 workspace 产品视图。

后续若准备彻底修 layout model，可以从 pane sizing / `SIDEBAR_DEFAULT_WIDTH` 方向做结构性修复；但不要一上来删除这段 CSS，否则视觉会立即退化。

### 右栏自动打开

`workspace/index.tsx` mount 时主动：

- `setRightContextOpen(true)`
- `revealTreePane('workspace-overview')`

这是当前 workspace 产品定义的一部分，不是意外副作用。

### Design Preview

`workspaceDesignPreviewEnabled()` 必须继续保持 DEV gate。不要把 mock 数据变成生产默认。

## 13.7 下一步建议断点

下一对话直接从这里继续，不要回到 messaging，也不要重做架构调研。

优先顺序：

1. **先重新看真实 Electron workspace 终态**，确认当前视觉没有热更新 / 重启后的回归。
2. 对照当前工作空间 UI，继续做一轮细节 QA：
   - 左栏内容密度与滚动
   - 右栏卡片信息密度
   - 中央 Git Diff / Preview 卡片比例
   - 底部输入框和快捷动作的交互层级
   - 小尺寸窗口响应式
3. 开始把 `design-preview.ts` 的 mock 数据逐项换成真实数据：
   - cwd / project / branch / repo status：已有 store
   - preview target：已有 coding-status
   - task / agent progress：应接真实 session/task/agent 状态
   - terminal stream：不要假装有；需要真实来源再接
4. workspace 的“日常助理”和“开发工作台”继续保持同一产品，不要做成两个割裂模式。
5. 若要动 pane layout 内核，先证明能替掉 202px workaround，再删除 workaround。
6. 最后再回到 messaging 展示层的剩余产品文案清理。

## 13.8 工作树安全规则（再次强调）

当前 worktree 有大量并行后端工程改动，约 200+ modified files，包含：

- `agent/`
- `hermes_cli/`
- `tui_gateway/`
- `gateway/`
- `tools/`
- `tests/`
- `SOUL.md`
- shared gateway contract
- 多个 backend handoff

**绝对不要：**

- `git reset --hard`
- `git checkout .`
- `git restore .`
- `git clean`
- 整仓 stash
- 整仓 format
- 整仓 stage / commit

不要覆盖后端并行修改，也不要把不属于前端这轮的改动一起提交。

## 13.9 新对话恢复指令（最新版）

新对话可以直接贴：

> 继续前端开发。使用 Remote Desktop Commander，连接设备“豹”，工作目录 `D:\远程工作区\stardust-hermes`。先完整读取 `HANDOFF_FRONTEND_2026-09-18.md`，**以文件最后的“最新补充交接：工作空间视觉重构 / 真实 Electron 验证”为最新状态**；再读取 AGENTS 规则和当前 git status，从真实工作树继续。不重新 clone、不重新做架构调研、不覆盖后端并行修改。先恢复并检查真实 Electron `#/workspace` 终态，再继续工作空间 UI/真实数据接线。


# 14. 最新补充交接：助理优先 Workspace / 真实状态接线 / E2E 收口

> **这一节是当前最新状态。与前面第 13 节冲突的地方，以本节为准。**
> 时间：2026-09-18 13:17 左右（本轮结束）
> 前端设备：**豹**
> 工作目录：`D:\远程工作区\stardust-hermes`
> 分支：`dev/stardust-assistant-ui`

## 14.1 双机分工 / 后端留言已经看过

已经读取并按以下文件执行：
- `DEV_COORDINATION_20260918_MACHINE_SPLIT.md`
- `BACKEND_HANDOFF_20260918_JARVIS_LIFECYCLE.md`
- `BACKEND_HANDOFF_20260918_COMPUTE_HOST_RESTART.md`

当前分工：
- **豹：只负责前端 / Electron / UI / 真实窗口调试。**
- **另一台开发机 DESKTOP-KKER56V：后端。**
- 前端不覆盖后端并行工作树，不做整仓清理或回滚。
- 跨层协议必须先对齐，不在前端另造 Task 生命周期 owner。

## 14.2 Workspace 产品方向已经改成“助理优先 + 开发能力随时接入”

当前无项目态不再展示 Git Diff / Preview 两个空壳卡，而是：
- 主 Hero：`Stardust 助理 / 助理模式`
- `工作准备状态`
- `当前状态`
- `助理快捷入口`
- `开发工作台`

无项目时：
- 可以直接开始对话。
- 可以查看任务。
- 可以打开终端。
- 可以搜索命令 / 页面 / 功能。
- “选择项目”是增强入口，不是使用 Stardust 的前置条件。
- 右栏改成“助理上下文”，不再反复催项目 / Git。

选择项目后：
- 同一 Workspace 自动切回完整开发能力。
- Git Diff / 文件 / branch / repo status / Preview / Terminal 都进入同一工作空间。
- 没有拆成两个独立模式或两个产品。

底部入口已经去掉原来的假 DEV 快捷动作，改为真实：
- 继续/开始对话
- 选择项目
- 查看任务
- 打开终端
- 更多操作 / Command Palette

## 14.3 已接的真实状态

Workspace 当前使用：
- cwd：`$currentCwd`
- 项目上下文：`$projectScope + $projectTree + projectRootCwd()`
- cwd 优先级：**session cwd -> 明确 projectScope root -> 空**
- 不再用 activeProject/defaultDir 猜项目。
- Git：`repoStatusForCwd(effectiveCwd)` + `registerRepoStatusCwd(effectiveCwd)`
- Preview：真实 `$previewTarget`
- Agent busy：真实 `$workingSessionIds`
- 当前 Todo/进度：`$statusItemsBySession` + `$activeSessionId`

任务进度有真实 Todo 时显示真实 title/status；没有 Todo 时显示 workspace readiness，不伪造步骤。
中央“当前状态”优先展示真实 running status item。
如果别的会话仍在后台执行，会列出真实 running session 标题，可点击回到该会话。

右栏状态语义已经拆开：
- `selectedWorking`：当前会话是否在执行
- `anyWorking`：系统是否存在任意执行中的会话

因此当前目标不会被别的后台会话误标成“正在执行”。
有 Todo 时右栏显示真实完成数、百分比和当前步骤。

## 14.4 已移除 / 不应恢复的假数据

正常 Workspace 渲染现在不再依赖 design-preview 的：
- fake branch `feature/ui-refactor`
- fake 4 个 Git 文件
- fake terminal / Vite / lint / typecheck 输出
- fake 最近活动
- fake 当前目标
- fake Preview target

`apps/desktop/src/app/workspace/design-preview.ts` 可能仍作为旧 DEV scaffold 留在仓库，
但主 Workspace 不应再把它当生产数据源。确认完全无引用后再决定是否删除。

Terminal stream 当前仍没有伪造实时日志。
要显示 live terminal，必须先找到可靠数据 owner / 数据源再接。

## 14.5 真实 Electron / 响应式状态

真实开发 renderer：
- CDP：`http://127.0.0.1:9222`
- Vite renderer：`http://127.0.0.1:5174`

本轮 E2E 跑完后，当前 renderer 路由被切回了 `#/`，不是 `#/workspace`。
新对话恢复时直接运行：
`D:\GPTcodex\Temp\stardust-hermes-ui-dev\reload-workspace.mjs`
再检查 `#/workspace`，不要误以为 Workspace 消失。

大窗口实测主布局仍约：
- 左栏：202px
- 中央 Workspace：1092px
- 右栏：270px

最小窗口模拟：
- 请求 900x800 时实际 viewport 钳到约 1000x889
- 左栏约 201px
- 中央约 528px
- 右栏约 246px
- document 无横向溢出
- 中央只做纵向滚动

小窗口左栏死空列已修：
`personal-product-nav.css` 在 workspace 路由下同时对
`[data-tree-group='grp-sessions']` 和它的直接 wrapper 强制 202px。

Workspace 根节点增加 `overflow-x-hidden`。
装饰性背景仍可能让元素自身 scrollWidth 大于 clientWidth，
但用户可见 document 不再出现横向滚动。

另外修复了重复的可访问名称：
- 顶部主搜索保留“搜索命令、页面和功能…”
- 底部省略号按钮改成“更多操作”

## 14.6 本轮验证结果

已通过：
1. `npm run typecheck`
   - exit code 0

2. `npm run build`
   - exit code 0
   - `assert-dist-built` 通过
   - warning 仅为已知 dirty build stamp / Vite config / deprecated chunk / dynamic import 提示

3. Playwright 首页：
   - `personal-assistant-home.spec.ts`
   - 1 passed

4. 个人助理前端回归并行跑：
   - `personal-assistant-home.spec.ts`
   - `personal-assistant-capabilities.spec.ts`
   - `personal-assistant-messaging.spec.ts`
   - `personal-assistant-settings.spec.ts`
   - `personal-assistant-tasks.spec.ts`
   - **5 passed (39.4s)**

能力页最初并行跑出现一次 loading 时序抖动，单跑通过。
测试已改成等待真实 skill overview + switch 稳定出现，再确认 loading 消失；
随后 5 条并行回归全部通过。

5. `git diff --check`（本轮前端相关范围）
   - exit code 0
   - 只有 `workspace-overview.tsx` CRLF/LF warning
   - 无 whitespace error

## 14.7 本轮主要前端改动范围

重点文件：
- `apps/desktop/src/app/workspace/index.tsx`
- `apps/desktop/src/app/workspace/workspace.css`
- `apps/desktop/src/app/contrib/workspace-overview.tsx`
- `apps/desktop/src/app/contrib/workspace-overview-copy.ts`
- `apps/desktop/src/app/contrib/personal-product-nav.css`
- `apps/desktop/e2e/personal-assistant-home.spec.ts`
- `apps/desktop/e2e/personal-assistant-capabilities.spec.ts`

当前这些文件仍在未提交工作树中。
不要把整仓后端改动一起 stage / commit。

局部 status 交接时：
- modified：`personal-product-nav.css`
- modified：`workspace-overview.tsx`
- untracked：`workspace-overview-copy.ts`
- untracked：`workspace/`
- untracked：两个 personal-assistant E2E spec

仓库整体仍有大量并行后端 modified/untracked 文件，这是正常现状。

## 14.8 下一对话最重要的断点

优先做：
1. 用 `reload-workspace.mjs` 恢复真实 Electron `#/workspace`。
2. 再跑 `inspect-workspace.mjs` / layout probe，确认 hot reload 后终态没漂。
3. **验证真实项目态**：实际进入一个明确 projectScope / session cwd 后，确认：
   - project name
   - real branch
   - changed files
   - additions/removals
   - Git Diff 卡
   - right rail file changes
   - Preview target
   都能从真实 store 自动出现。
4. 之前尝试过用 CDP 临时 `setCurrentCwdTransient` 验证真实 repo，
   但异步 Runtime.evaluate Promise 被 Chromium 回收；状态已恢复，没有污染用户持久配置。
   下一轮改用更稳的分步 CDP，或直接通过真实项目 UI 进入。
5. 继续完善 Task Thread / Agent execution 的真实展示，但只消费现有 lifecycle owner。
6. Terminal live stream 仍待可靠数据源，不要造假日志。
7. 最后再做项目态视觉 QA：卡片比例、长文件名、多 changed files、多 running session 密度。

## 14.9 新对话恢复指令（请用这一版）

> 继续前端开发。使用 Remote Desktop Commander，连接设备“豹”，工作目录 `D:\远程工作区\stardust-hermes`。先完整读取 `HANDOFF_FRONTEND_2026-09-18.md`，**以文件最后的“最新补充交接：助理优先 Workspace / 真实状态接线 / E2E 收口”为最新状态**；再读取 `DEV_COORDINATION_20260918_MACHINE_SPLIT.md`、AGENTS 规则和当前 git status。不要重新 clone，不重新做架构调研，不覆盖另一台开发机的后端并行修改。先用已有 CDP 脚本恢复并检查真实 Electron `#/workspace`，然后优先验证真实项目态的 Git / Preview / Task 状态，再继续完善工作空间前端。


# 15. 2026-09-18 最新补充交接：对话/项目导航收口、真实项目态与品牌验证

> 本节晚于第 14 节。恢复时仍以“当前源码 + git diff > handoff”为原则，不要撤销本节之后可能存在的后续修改。

## 15.1 本轮实际完成

1. **左侧产品导航继续收口为“对话优先”**
   - 顶级导航保持：`对话 / 任务 / 项目 / 知识库 / 工具 / 设置`。
   - `项目` 不再直接触发 native folder picker，也不再强制打开右侧 Context Rail。
   - 点击 `项目` 现在切换左侧为 project grouping；CENTER 仍是同一个 chat surface。
   - 点击 `对话` 恢复 conversation grouping；CENTER 不被替换。
   - 已在真实 dev Electron 中验证 `对话 → 项目 → 对话` 高亮、左栏内容和中央聊天都正常。
   - 已避免在已经处于 chat surface 时重复 navigate 到同一路由；只有从其它产品页返回 chat 时才导航。

2. **用户可见 Stardust 品牌继续收口**
   - onboarding / guided greeting / setup / restart / notification 等 presentation 文案中的产品身份改为 Stardust（6 个 locale：en/zh/zh-hant/ja/ru/ar）。
   - Git Review 中“让 Hermes 提交并开 PR / Ask Hermes…”等用户可见文案改为 Stardust。
   - 没有机械替换 `HERMES_HOME`、`window.hermesDesktop`、Hermes CLI、RPC/app/protocol identity、Hermes gateway 等兼容/技术身份。
   - wake phrase `hey hermes` **未改**：当前 `wake.status / wake.start` 的 `phrase` 是 backend authoritative 的真实可说关键词；UI 改成 `hey stardust` 会与识别器不一致。把它视为兼容身份，不是 presentation 漏改。

3. **真实项目态已通过真实 UI 验证，不再依赖 mock 注入**
   - 真实项目列表可见：`闲聊/veteran-engineer`、`Projects/veteran-engineer`、`WPE项目`、`ZNagent`。
   - 实际点击 `ZNagent` 后，CENTER 仍保持 chat。
   - 真实 UI 显示：
     - project: `ZNagent`
     - cwd: `C:/Users/bz977/ZNagent`
     - branch: `main`
     - PR: `#272`
     - changed: `1 处更改`
   - Electron main-process `window.hermesDesktop.git.repoStatus('C:/Users/bz977/ZNagent')` 返回：
     - branch `main`
     - ahead/behind `0/0`
     - changed `1`
     - untracked `1`
     - file `.local-run/`
   - 点击 `1 处更改` 后，真实 REVIEW 列表显示 `.local-run`；与 repoStatus 一致，没有 fake changed file。
   - 直接探测 stardust-hermes 仓库也返回真实当前 Git 状态（分支 `dev/stardust-assistant-ui`，大量真实 dirty paths），证明 Git owner 是 Electron/backend 数据链，不是 renderer mock。

4. **右侧 Context Rail 真实行为**
   - 普通聊天默认关闭。
   - 进入 ZNagent 后 rail 仍默认关闭，不因“项目”顶级导航强制打开。
   - 手动打开后显示真实项目信息：project/cwd/branch/ahead/behind。
   - `WorkspaceOverview` 当前只从真实 `$currentCwd / $projectScope / repoStatusForCwd / $previewTarget / $workingSessionIds / $statusItemsBySession / $activeSessionId` 投影状态。
   - 当前 sandbox 没有真实 working session / todo / preview target，因此正确 UI 是不伪造 progress / preview / task。

5. **响应式真实 OS 窗口 QA**
   - 不是只用截图/Emulation；用 Win32 `MoveWindow` 实际调整了 Electron BrowserWindow。
   - 大窗口外框约 1220×800（当前 app zoom 约 90%，renderer 1355×889）：
     - 左侧约 219px
     - Context Rail 打开约 420px
     - 无横向滚动
   - 中等外框 900×720（renderer 1000×800）：
     - 左侧约 187px
     - rail 约 360px
     - 无横向滚动
   - BrowserWindow 最小外框 400×620（renderer 444×689）：
     - 左侧与右侧自动隐藏
     - CENTER 保留
     - 项目状态条仍可见
     - `document.scrollWidth === clientWidth`
   - 因此当前响应式宽度策略合理，不要为了旧“202px”数字硬改。

## 15.2 本轮修改文件

本轮明确触碰的前端文件：
- `apps/desktop/src/app/contrib/personal-product-nav.tsx`
- `apps/desktop/e2e/personal-assistant-home.spec.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/i18n/zh-hant.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/ru.ts`
- `apps/desktop/src/i18n/ar.ts`

以及本 handoff 文件。

注意：这些文件本轮开始前部分已经 dirty；不要把“整文件 diff”都归因于本轮，也不要覆盖来源不明的历史修改。

调试脚本继续只放在：
`D:\GPTcodex\Temp\stardust-hermes-ui-dev`
不要提交 Temp 文件。

## 15.3 验证结果

最终验证：
- `npm run typecheck` → exit 0
- Vitest：
  - `src/i18n/context.test.tsx`
  - `src/app/routes.workspace-reveal.test.ts`
  - `src/app/contrib/personal-layout.test.ts`
  - `src/app/workspace/task-session.test.ts`
  - 共 4 files / 61 tests → passed
- `npm run build` → exit 0
- `npx playwright test e2e/personal-assistant-home.spec.ts --workers=1` → 1 passed
- `npx playwright test e2e/workspace-preview-state.spec.ts --workers=1` → 1 passed
- `git diff --check -- apps/desktop` → exit 0
  - 仅 `workspace-overview.tsx` CRLF/LF warning
  - 无 whitespace error

重要：personal-assistant-home E2E 在重建 dist 前曾两次失败，因为 fixture 明确 `electron .` 加载已有 `dist`；重建 `npm run build` 后当前源码版本通过。不要把那两次旧 dist 失败当作当前产品回归。

## 15.4 当前真实运行现场

- Vite `127.0.0.1:5174` 仍在监听。
- CDP `127.0.0.1:9222` 仍在监听。
- dev Electron renderer 仍使用：
  `D:\远程工作区\stardust-hermes\.hermes-sandbox\desktop-dev\user-data`
- 仍是 sandbox 开发实例；没有改回用户日常 Hermes 数据目录。
- 本轮结束前已恢复到顶级 `对话` 左栏模式；项目上下文若由当前草稿/会话持有会继续保留，不会因“对话”导航人为伪造/清除 runtime state。

## 15.5 Backend authoritative state 与 Contract Gap

当前可直接消费的真实 owner：
- cwd / project scope / project tree
- Git repo status、branch、changed files、ahead/behind、diff/review
- preview target
- working / attention session ids
- per-session status items / todo
- active session id
- backend wake status / wake phrase

仍不要前端自造：
- TaskManager / AgentStateMachine
- fake progress timer
- fake terminal logs
- fake Git / Preview / branch
- 点击 Stop 后的“已停止”乐观状态

当前明确 gap / 未完成点：
1. **Task Thread / Agent Execution**：sandbox 本轮没有真实 running session，因此只验证了 owner/wiring 和“无真实数据不显示”；要验证多 running sessions / lifecycle settle 仍需一次真实后端执行场景。
2. **Terminal live stream**：仍没有本轮可证明的可靠 live stream owner；保留入口/容器，不伪造输出。
3. **Wake phrase 产品化**：后端 authoritative phrase 仍为 `hey hermes`。如果产品要改成 `hey stardust`，必须后端/配置/模型共同迁移，不能只改前端。
4. **多 changed files / 超长文件名密度**：当前可安全进入的真实项目只有 0 或 1 个 change，未通过真实数据覆盖“很多 changed files/超长名”的极端密度；不要用 production fake state 补这个验证。

## 15.6 下一轮第一步

不要做大型重构。

建议下一步只做第一次正式前后端 integration milestone：
1. 启动一个真实可控的 backend session / task，让 `$workingSessionIds / $statusItemsBySession / $activeSessionId` 有真实数据。
2. 验证右侧 Task Thread / Agent Execution 从 running → interrupt/settle → closed 的 projection，严格遵守 backend authority。
3. 若 terminal backend 已提供稳定 owner，再接 live stream；否则继续只保留入口。
4. 如需把 wake phrase 产品名改成 Stardust，先与后端确认 contract/config migration。
5. 最后再对“多 changed files / 多 running sessions / 长文件名”做真实数据密度 QA。

仍然不要 commit / push / merge；也不要 stage 整仓后端历史修改。


# 16. 2026-09-18 收口交接：冻结前端范围，等待第一次正式前后端 integration

> 本节晚于第 15 节，覆盖第 15 节中仍建议继续扩前端验证/清理的部分。当前源码与 git diff 仍高于 handoff。

## 16.1 用户最新范围冻结

本轮已明确进入收口阶段。此后不要再新增：branding 清理、voice 文案清理、新页面、新导航结构、新视觉 polish、新功能、新 mock、全局 Hermes -> Stardust 替换，也不要继续顺手搜索“还有哪里能改”。

ZNagent 是用户独立开发的外部项目，只曾被旧轮次拿来做只读 Git 项目态样本。**从本节开始严禁再对 ZNagent 做任何测试、启动、修改或依赖。** 后续验证只使用 `stardust-hermes` 自己或隔离临时 fixture。第 15 节涉及 ZNagent 的记录仅保留为历史，不再是有效验证方案或 integration 依赖。

本轮没有 commit / push / merge，也没有 stage 整仓。

## 16.2 收口时实际处理

- 撤回了刚刚超出范围的 3 处 voice `Hermes -> Stardust` 文案改动；`use-voice-live-conversation.ts` 最终无 diff。
- 没有继续做新的 branding、voice、页面、导航、视觉或功能修改。
- 当前轮明确触碰仍是：`personal-product-nav.tsx`、`personal-assistant-home.spec.ts`、6 个 locale（en/zh/zh-hant/ja/ru/ar）以及本 handoff；这些文件在本轮开始前部分已 dirty，不能把整文件 diff 都归因于本轮。
- `apps/desktop` 当前 tracked diff 规模为 61 files / 2891 insertions / 859 deletions，另有既存 untracked 前端文件；这是累计 dirty worktree，不是本轮新增 61 个文件。

## 16.3 最终自动化验证（收口态）

在 `apps/desktop` 最终收口态重新执行：

- `npm run typecheck` -> exit 0。
- Vitest：`src/i18n/context.test.tsx`、`src/app/routes.workspace-reveal.test.ts`、`src/app/contrib/personal-layout.test.ts`、`src/app/workspace/task-session.test.ts`、`src/store/layout-sidebar-view.test.ts`、`src/store/projects.test.ts` -> 6 files / 112 tests passed。
- Playwright Electron E2E：`personal-assistant-home.spec.ts` + `workspace-preview-state.spec.ts` -> 2 passed。
- `git diff --check -- apps/desktop` -> exit 0；只有 `workspace-overview.tsx` 的 CRLF/LF warning，无 whitespace error。
- 收口前曾单独再跑 `personal-assistant-home.spec.ts`，同样 1 passed；之前一次 aria-current 失败未在最终态复现。

## 16.4 真实 Electron 最后检查

真实 dev Electron 仍是 `npm run dev -> Vite 127.0.0.1:5174 -> Electron`，并继续使用 `.hermes-sandbox/desktop-dev/user-data`，没有切回用户日常 Hermes 数据目录。

最终普通聊天检查通过：
- route `#/`
- 左导航固定为 `对话 / 任务 / 项目 / 知识库 / 工具 / 设置`
- `对话` active
- composer 保持可见
- Context Rail 默认不可见
- `documentElement.scrollWidth === clientWidth`，无横向滚动

使用 `stardust-hermes` 自身作为唯一真实 repo 样本时，Electron `git.repoStatus` 返回真实分支 `dev/stardust-assistant-ui`、默认分支 `main`、ahead/behind `0/0`，并返回当前巨大 dirty worktree 的真实 changed/staged/unstaged/untracked/conflicted 统计；没有 renderer fake Git 数据。

真实项目 scope 也已只针对 `stardust-hermes` 验证：`$projectScope` 与 `projectIdForCwd(stardust-hermes)` 指向同一真实 project owner，项目顶级导航保持 active，CENTER composer 不被替换。

Context Rail 的窗口级开合与 CENTER 常驻已通过；现有 `workspace-preview-state.spec.ts` 也证明 Preview 在真实 Electron renderer 中使用真实 preview target 且不卸载 conversation。

但收口时的 dev sandbox 仍保留旧会话留下的 `$currentCwd`，且当前草稿没有 selected stored session。即使 project scope 已切到 `stardust-hermes`，最终 Overview 在干净重开 rail 后仍显示通用 `Stardust 助理` 上下文，而不是 `stardust-hermes` 项目指标。不要为了本轮收口再新增前端状态修补；将其留到第一次正式前后端 integration，验证“project scope / draft / selected session / current cwd”的 authoritative handoff。

## 16.5 Integration 前明确保留的 gap

1. **Project-context handoff**：需要在正式 integration 中证明进入 project 后，backend/session owner 能让新草稿/选中会话的 cwd 与 project scope 一致，Context Rail 才投影项目指标。当前不要在 Renderer 造一个平行 cwd owner。
2. **Task Thread / Agent Execution**：本轮仍没有真实 running session 端到端样本；继续只消费 `$workingSessionIds / $statusItemsBySession / $activeSessionId` 等现有 owner，不做 fake progress。
3. **Terminal live stream**：没有新增数据源，不伪造日志。
4. **Wake phrase**：`hey hermes` 仍是 backend-authoritative compatibility phrase；本轮不再做 voice/branding 清理。
5. **Preview**：E2E 已覆盖已有真实 preview target 投影；dev sandbox 最终没有额外制造 Preview 数据。

## 16.6 下一步唯一建议

停止前端扩展，等待第一次正式前后端 integration。Integration 时优先验证：真实 session/task lifecycle、project scope -> session cwd handoff、Context Rail 项目投影、Stop/settle authority，以及已有 Terminal/Preview owner 是否有稳定后端数据。若 contract 缺失，记录 `Frontend Integration Contract Gap`，不要用前端 mock/fake state 补齐。

仍然不要 commit / push / merge；不要修改 `agent/**`、`gateway/**`、`tui_gateway/**`、`hermes_cli/**`、`tools/**`、`run_agent.py` 或 shared contract。
