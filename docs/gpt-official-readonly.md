# GPT官方（gpt-official）

独立于 SIWC / openai-codex 的官方 Codex Responses API 账户路由。

- 默认 `gpt-6-luna`，在目录内排首位；不替换为 Astra。
- 只读 `CODEX_HOME/models_cache.json`（默认 `~/.codex/models_cache.json`），保留原客户端 `visibility=list` 的全部候选和 `display_name`。不以 `supported_in_api` 过滤 Codex 候选；不使用固定 0.0.0 live URL 遮盖原客户端目录，不硬编码候选或伪造客户端版本。
- 目录来源、`fetched_at`、原客户端版本进入 picker payload 和提示；超过24小时提示更新。缺失、空或无效目录明确报错，无静态 fallback。
- 缓存 `identity` 的账户映射未经验证，不宣称已授权；真实请求决定模型权限。隐藏 Reserve / Legacy / Auto Review 不作为聊天选项。
- 每次请求重读原 `auth.json` 的 access/account，不读取 refresh/id token，不刷新、复制或写回凭据；错误不跨 provider 回退。
- 请求身份保持真实 `hermes-agent` / `HermesAgent`，不伪装原客户端。

## 此次最终目录修正证据

专项 pytest 15 passed；model-picker UI unit 10 passed；desktop build 成功。
单元测试目录为 fixture，不代表真实授权。

真实本地目录读取到7个可见候选：GPT-6.1-Sol、GPT-6-Astra、GPT-6-Sol、GPT-6-Luna、GPT-5.6-Sol、GPT-5.6-Terra、GPT-5.6-Luna。缓存时间 `2026-10-12T06:21:55.249793500Z`，原客户端版本 `0.162.0`。

独立 runtime 用本 worktree 的 AIAgent / ResponsesApiTransport / run_codex_stream，仅请求 `gpt-6-luna`：文本返回 `LUNA_OK`；无副作用固定字符串工具调用及回传闭环返回 `TOOL_OK`。auth SHA256 前后不变。没有 Astra 推理。

证据：`D:/Stardust/test-temp/gpt-catalog-final/evidence/` 的 runtime.log、auth-check.json、ui.log、ui-click.log、model-picker.png。独立 Electron 使用独立 home/userdata，真实菜单显示全部7项。通用 ModelPickerDialog 已接原显示名/来源提示；聊天快捷菜单仍使用自己的 slug 格式化名称，尚需接入原显示名和来源提示，不能声称所有UI面已完成。

## 部署

仅 feature worktree；未合并、覆盖或重启用户运行实例。父审后完成快捷菜单显示名/来源验收再发布。
