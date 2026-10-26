# web：独立 dashboard 界面功能扩展冻结

Stardust 当前主界面是中文优先的 Windows Electron Desktop；消息平台保留 Telegram（TG）、WhatsApp、微信。`web/` 是独立浏览器 dashboard SPA，暂停新增页面、交互和独立产品功能。保留现有运行行为，以及安全、数据保护、阻断性缺陷和必要兼容修复。

## 冻结前端，不冻结共享服务

- `web/package.json` 与 `apps/desktop/package.json` 是各自独立的前端包，都依赖 `apps/shared/`；Desktop 没有对 `web/` 前端包的依赖。
- Desktop 启动 `serve`。`hermes_cli/subcommands/dashboard.py` 将其设为 `headless_backend=True`；`hermes_cli/main_dashboard.py` 的 `_resolve_dashboard_web_dist()` 在此路径跳过 `web/` 构建，并设置 `STARDUST_SERVE_HEADLESS=1`。
- `hermes_cli/web_server_dashboard.py` 的 `mount_spa()` 在 headless 模式下不挂载 dashboard SPA；它仍保留桌面令牌握手所需的根路径行为。这些服务代码不属于可随前端冻结而停用的内容。
- `hermes_cli/web_server*`、`hermes_cli/web_routers/` 的共享 HTTP/WebSocket/API、认证、配置与会话服务，以及 `tui_gateway/` 和 `apps/shared/`，继续随桌面和三个消息平台需求维护。尤其 `/api/ws` 调用 `tui_gateway.ws.handle_ws()`，是桌面实际运行链。
- 旧桌面运行时不支持 `serve` 时仍可能走 `dashboard --no-open` 兼容回退；保留该路径所需的兼容能力。

本说明只冻结独立界面的功能扩展，不授权删除前端、共享服务或构建入口。共享协议变化所需的前端适配仍需完成；现有相关测试与 CI 门槛保持，不得以冻结为由跳过或降级检查。

详见根目录 `ROADMAP.md`、本目录 `AGENTS.md` 与 `tui_gateway/FROZEN.md`。环境变量与调用链以上述代码为准；部分既有指南中的 `HERMES_SERVE_HEADLESS` 旧名称不代表当前代码使用的变量名。
