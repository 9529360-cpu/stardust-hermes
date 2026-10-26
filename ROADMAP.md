# Stardust ROADMAP

> **Stardust = 中文优先的 Windows 桌面个人助理。** 主界面是 Electron Desktop，保留 Telegram（TG）、WhatsApp、微信三个消息平台。产品名只有一个：Stardust。

本文是产品路线图权威，取代原 README.zh-CN 改造方向章节、STARDUST.md 产品方向章节与 JARVIS-PROGRAM.md（历史文档已删除）。工程约束仍以根和各目录 `AGENTS.md` 为准；依赖判断须核对代码，不能凭目录名称冻结共享后端。

## 原则

- **独立维护**：`9529360-cpu/stardust-hermes` 是源码与发布权威；不跟踪 Hermes 上游，不做自动同步。
- **Hermes 仅是底座**：内部包名、CLI、`HERMES_HOME` 等兼容命名可保留，不代表产品身份。
- **隐私优先**：仓库只放源码、测试和文档；对话、记忆、日志、密钥、路由健康状态一律不入库。
- **聚焦产品**：Windows Desktop 是界面建设主线；Telegram（TG）、WhatsApp、微信是保留的消息平台，不能因某次未配置或未启动就将其移除。
- **冻结独立界面扩展**：暂停 `ui-tui/` 终端界面与 `web/` dashboard 的专属功能扩展，保留现有行为及安全、数据保护、阻断性缺陷和必要兼容维护。冻结不等于删除，不覆盖共享后端。
- **维护共享运行能力**：agent core、消息 `gateway/`、`tui_gateway/`、`apps/shared/`，以及桌面使用的 `hermes_cli/web_server*`、`web_routers/` 等服务继续维护。
- **真实验收**：功能以真实运行证据验收，CI 绿灯不等于产品验收；冻结独立界面不能成为降低现有测试或 CI 门槛的理由。

## v0.1 验收主线（六条，全部通过即 v0.1）

1. **聊天**：流式输出、停止、错误恢复在 Desktop 上真实可用。
2. **模型接入**：官方/兼容 API 配置、健康检查、失败提示；不泄露密钥。
3. **记忆**：显式记住/忘记，重启后仍在，可查看可删除。
4. **Work**：长任务状态、取消、重启恢复、结果与证据。
5. **权限**：危险动作有授权边界与审计；授权未变时不重复确认。
6. **桌面工作台**：中文优先的简洁工作台真正可用，不只是换肤。

三个消息平台的现有能力与所需共享后端持续维护；相关改动按真实调用链验收，外部消息发送仍须符合明确授权边界。列为保留平台不代表未经实测即可声称接入或验收通过。

## 执行顺序

1. 清理残留（worktree 副本、散落证据、外部路径依赖），先核实归属并保留独有成果。
2. 按实际依赖收敛界面投入：只冻结独立 TUI 与 dashboard 的专属功能扩展，保持桌面共享后端、协议及 Telegram/WhatsApp/微信维护；不改 CI 降低门槛。
3. 名称清理：对外产品称 Stardust；内部兼容名称和既有路径仅在具备迁移与回退验证时调整。
4. 逐条做实 v0.1 六条主线，基础设施变更以这些主线和三个消息平台的实际需要为限。

## 已核查的界面与后端边界

- Desktop 的 `apps/desktop/electron/backend-command.ts` 构造 `serve` 调用；`hermes_cli/subcommands/dashboard.py` 注册该命令并设置 `headless_backend=True`。旧运行时不支持 `serve` 时，桌面保留 `dashboard --no-open` 兼容回退。
- 桌面 `apps/desktop/src/api/client.ts` 使用 `apps/shared/` 的 `JsonRpcGatewayClient`；共享服务的 `hermes_cli/web_routers/chat_ws.py` 在 `/api/ws` 调用 `tui_gateway.ws.handle_ws()`。**`tui_gateway/` 是桌面实际后端，不能整体冻结。**
- `hermes_cli/main_dashboard.py` 的 headless 路径跳过 `web/` 构建，设置当前代码使用的 `STARDUST_SERVE_HEADLESS=1`；`hermes_cli/web_server_dashboard.py` 据此不挂载 SPA，并保留桌面令牌握手。Desktop 不依赖 dashboard SPA，但依赖这些共享服务；旧指南中的 `HERMES_SERVE_HEADLESS` 名称不能替代代码核查。
- `apps/desktop/package.json`、`ui-tui/package.json` 和 `web/package.json` 分别定义独立客户端，均使用 `apps/shared/`；Desktop 不嵌入 Ink TUI，也没有对 `web/` 前端包的构建或运行依赖。

范围说明见 `ui-tui/FROZEN.md`、`tui_gateway/FROZEN.md`、`web/FROZEN.md`。安全、共享协议、兼容性与回归检查继续执行；这些说明不修改任何运行行为或 CI 配置。

## 当前验收状态

六条主线均有代码实现，不能把本路线图或模块存在视为 v0.1 已通过。2026-10-25 的隔离桌面与后端验收记录见本地 `Reports/Stardust/focus-validation/quality-audit.md`（忽略入库）。真实服务推理健康、持久任务恢复/取消闭环、授权复用范围与结果证据入口仍须逐项验收和整改；审计与授权记录的现有限制须明确展示。

## 边界

- 外部发送、交易、删除、发布等不可逆动作必须有明确授权边界。
- 模型负责理解与推理；本地系统拥有记忆、技能、任务状态、恢复与权限。
- 详见根目录 `AGENTS.md`（工程规范）与 `STARDUST.md`（维护与隐私政策）。
