# Stardust Desktop

> Stardust 的桌面个人助理工作台。Hermes Agent 是底层运行时来源，不是当前产品品牌。

Stardust Desktop 是本仓库面向日常长期使用的主要桌面界面。它保留 Hermes Agent 已经成熟的 CLI、Gateway、工具与运行时兼容层，同时把桌面端继续改造成独立维护的个人助理：中文优先、个人工作流优先、Stardust 自己的安装与更新来源优先。

本目录后续以 `9529360-cpu/stardust-hermes` 为唯一源码和发布权威，不跟随 Hermes 上游发布节奏。

## 当前方向

- **个人助理工作台**：桌面导航、首页、会话、项目与工作区围绕长期个人使用重构。
- **独立视觉体系**：继续推进 Codex 式信息结构与系统级液态玻璃视觉，不复刻上游桌面 UI。
- **稳定模型路由**：模型路由健康状态、故障切换、冷却与凭据轮换属于 Stardust 自己的长期能力。
- **本地与远程兼容**：可连接本地运行时或远程 Gateway；真正的执行边界始终由当前连接决定。
- **Stardust 更新权威**：桌面产品入口只更新 Stardust 客户端；不会从 UI 触发上游 `hermes update` 或替换后端源码。

## 安装与运行

推荐先使用仓库根目录 README 中的 Stardust 安装入口。底层仍保留 `hermes` CLI 名称以避免无意义的兼容性破坏，因此已有源码安装可以继续使用：

```bash
hermes desktop
```

开发模式：

```bash
npm install
cd apps/desktop
npm run dev
```

需要隔离真实配置时：

```bash
../scripts/dev-sandbox.sh npm run dev
HERMES_DESKTOP_HERMES_ROOT=/path/to/stardust-hermes npm run dev
HERMES_HOME=/tmp/stardust-dev npm run dev
npm run dev:fake-boot
```

## 构建桌面包

```bash
npm run dist:mac
npm run dist:win
npm run dist:linux
npm run pack
```

构建产物与正式发布应以本仓库的 GitHub Releases 为准。macOS / Windows 的签名和 notarization 仍使用现有 Electron Builder 凭据约定；没有对应签名凭据时，应把产物视为本地开发构建而不是正式发行包。

## 更新策略

Stardust 不再把 Hermes 上游作为自动更新源。

- 桌面客户端的检查与应用动作只针对 Stardust Desktop 自身。
- 远程后端的版本状态可以作为诊断信息显示，但不会变成“一键更新后端”的产品动作。
- `hermes update` 在 Stardust 中被有意禁用，避免把独立维护的安装重新同步回上游。
- 安装、恢复、bootstrap 与后续发布来源都应指向 `9529360-cpu/stardust-hermes`。

## 架构边界

Stardust Desktop 仍沿用成熟的三层边界：

- **Electron**：负责进程生命周期、原生文件系统 / Git / 窗口能力、安装更新和窄化后的 preload bridge。
- **React renderer**：负责导航、展示、面板、交互状态与桌面体验。
- **Agent backend**：负责会话、工具、模型调用和流式执行，通过 Gateway 协议向桌面提供能力。

内部仍可能出现 `Hermes`、`HERMES_HOME`、`hermes serve`、`tui_gateway` 等名称。这些属于底层兼容接口和历史包名，不应被当作产品品牌重新暴露到新的用户界面。

Backend resolution 继续按验证后的候选链工作：

1. `HERMES_DESKTOP_HERMES_ROOT`
2. 当前开发 checkout
3. 已完成的 Stardust managed install
4. `HERMES_DESKTOP_HERMES` 或 `hermes` on `PATH`
5. 可导入兼容运行时的 system Python
6. Stardust first-launch bootstrap installer

候选只在通过真实 probe 后才可使用；文件存在本身不是有效运行时的证明。

## Connections 与 Projects

Desktop 支持本地 backend 和显式远程 Gateway。远程模式下，Agent tools、终端命令和文件操作都发生在远端 Gateway 主机，而不是显示 Desktop UI 的电脑上。

Projects 是工作区抽象。一个 Project 可以承载多个目录、仓库、worktree 和会话；不要另外再造一套并行的 per-session folder picker。

切换 profile / connection 应优先作为 workspace switch 处理：前台 shell 保持挂载，只清理和重建属于当前 Gateway 的状态，避免把旧连接的会话、列表或缓存泄漏到新连接。

## 验证

修改 Desktop 后至少运行：

```bash
npm run fix
npm run typecheck
npm run lint
npm run test:ui
npm run test:desktop:platforms
```

涉及安装、启动、更新、打包或发布路径时，再运行：

```bash
npm run test:desktop:all
```

重要的视觉与交互改动不能只以 typecheck / unit test 作为完成证据，还需要在真实 Electron 窗口中检查代表性尺寸与状态。

## 维护指南

改动桌面前先读：

- [`AGENTS.md`](./AGENTS.md)：桌面架构、状态所有权、resolver / fallback、transport、性能与测试约束。
- [`DESIGN.md`](./DESIGN.md)：Stardust Desktop 的视觉与交互设计约束。
- [`../../STARDUST.md`](../../STARDUST.md)：独立维护、源码权威、隐私边界与上游策略。

## 故障排查

运行日志仍保存在兼容路径 `HERMES_HOME/logs/desktop.log`。现阶段不为了品牌统一强行迁移用户数据目录，因为这会带来不必要的安装和升级风险。

macOS / Linux：

```bash
rm "$HOME/.hermes/hermes-agent/.hermes-bootstrap-complete"
rm -rf "$HOME/.hermes/hermes-agent/venv"
```

Windows PowerShell：

```powershell
Remove-Item "$env:LOCALAPPDATA\hermes\hermes-agent\.hermes-bootstrap-complete"
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\hermes\hermes-agent\venv"
```

## 技术底座与许可证

Stardust Desktop 最初建立在 Hermes Agent 的开源桌面与运行时实现之上，并保留原项目的 Git 历史、版权声明与 MIT License。

Hermes Agent 是技术底座；当前由本仓库独立维护和继续改造的产品是 **Stardust Desktop**。

MIT — see [`../../LICENSE`](../../LICENSE).
