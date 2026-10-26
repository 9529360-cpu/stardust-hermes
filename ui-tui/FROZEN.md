# ui-tui：独立终端界面功能扩展冻结

Stardust 当前聚焦中文优先的 Windows Electron Desktop，以及 Telegram（TG）、WhatsApp、微信三个消息平台。`ui-tui/` 是独立 Ink 终端界面；暂停为它新增界面、交互和产品功能，不将它作为新的产品主线。

冻结不等于删除或放弃维护。保留现有入口与可用行为；安全、数据保护、兼容性、阻断性缺陷和共享协议变更所必需的适配仍需维护，并执行现有相关测试与 CI。不得以冻结为理由降低检查门槛。

## 共享依赖边界

- Desktop 有自己的 React 界面，不嵌入 Ink TUI。`apps/desktop/package.json` 声明的是 `@stardust/shared` 依赖，未依赖 `hermes-tui`；`ui-tui/package.json` 则是独立的 `hermes-tui` 包。
- `tui_gateway/` 是 Desktop 也在使用的 JSON-RPC 后端，**不在整体冻结范围内**。它的会话、工具执行、事件、权限交互、传输和协议继续随桌面与消息平台需求维护。
- `apps/shared/` 由多个客户端共享，继续维护；共享协议演进时，现有 TUI 必要的兼容适配仍可修改。
- dashboard 的 PTY 聊天仍嵌入真实 TUI；冻结独立界面扩展不授权删除它们之间的现有运行路径。

详见根目录 `ROADMAP.md` 与 `tui_gateway/AGENTS.md`。本说明只限定产品功能扩展优先级，不修改代码、运行配置或 CI。
