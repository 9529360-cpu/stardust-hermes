# tui_gateway：共享后端继续维护

**本目录不整体冻结。** 名称包含 `tui` 不代表它只是多余的终端界面。Stardust 的 Windows Electron Desktop 实际依赖这里的 JSON-RPC 后端；只暂停面向独立终端界面或独立 dashboard 的专属功能扩展，不冻结共享运行能力。

## 已核查的实际调用链

1. `apps/desktop/electron/backend-command.ts` 的 `serveBackendArgs()` 构造 `serve --host 127.0.0.1 --port 0`；旧运行时不支持 `serve` 时保留 `dashboard --no-open` 兼容回退。
2. `hermes_cli/subcommands/dashboard.py` 将 `serve` 注册到共享 `cmd_dashboard`，设置 `headless_backend=True`。
3. 桌面的 `apps/desktop/src/api/client.ts` 基于 `apps/shared/` 的 `JsonRpcGatewayClient`。
4. `hermes_cli/web_routers/chat_ws.py` 的 `/api/ws` 路由调用 `tui_gateway.ws.handle_ws()`。因此 `hermes_cli/web_server*`、相关 `web_routers` 和本目录共同组成桌面实际后端路径。

## 维护边界

- 继续维护桌面所需的 RPC、会话、模型调用、工具、流式事件、恢复、权限与授权交互，以及共享协议、安全和兼容性。
- `apps/shared/`、Python 协议声明及生成的 TypeScript 契约继续维护；不能因独立界面停止扩展而停止协议同步或适配。
- Telegram（TG）、WhatsApp、微信三个消息平台及其所需的共享核心、消息 gateway 能力继续维护。消息 `gateway/` 与本目录职责不同，不能用一个目录的界面冻结替代平台维护决策。
- 现有 TUI/dashboard 的安全、数据保护、阻断性缺陷和必要兼容适配仍可修复；新增这些独立界面的专属体验暂缓。
- 保留现有相关测试与 CI，包括后端、契约和桌面调用链检查；不得因本说明删除、跳过或降级质量门槛。

本文件用于纠正“冻结整个 tui_gateway”的范围误判，不是停用后端的指令。详见根目录 `ROADMAP.md` 与本目录 `AGENTS.md`。
