# GPT官方（gpt-official）

独立于 SIWC / openai-codex 的官方 Codex Responses API 直连账户路由。

- 从 `CODEX_HOME/auth.json`（未设置时 `~/.codex/auth.json`）只读提取 access_token / account_id；不会登录、刷新、复制原 token pair 或写回文件。
- 原 ChatGPT/Codex 客户端负责登录和续期；缺失、过期或无权限时显示需原客户端处理的提示，不自动切换其他 provider / 模型。
- 模型选择器读取账户 Codex API 目录，不等同于 ChatGPT 网页全部模型，也不声称全球最新；仅展示服务端 `visibility=list` 且 `supported_in_api=true` 的常规模型。隐藏的 Reserve、Legacy、Auto Review 不作为普通聊天选项。
- 当前实现默认仍为 `gpt-5.6-luna`（旧代快速高效模型），不是最新 Luna 的承诺。用户截图的 GPT-6 Luna / GPT-6 Sol / GPT-6.1 Sol 不在本次账户目录中，最终默认版本需在来源/请求契约核实后确认；不允许凭静态推荐表补造 slug。目录中存在时排首位，不会替换为 Astra。
- 每次主会话直连请求前重新读取 access；与旧 OAuth refresh / credential pool 隔离。
- 请求身份保持 `originator=hermes-agent` / `HermesAgent`，不伪装官方客户端。沿用 ResponsesApiTransport 的 tools 和事件处理。

## 隔离验收

测试环境使用独立 `STARDUST_HOME`、`TEMP`、`TMP` 和 pytest `--basetemp`。专项测试 `tests/test_gpt_official.py` 采用 fake auth 与 httpx MockTransport，不调用真实推理。桌面测试覆盖 GPT官方名称、所有模型可选、登录错误无模型时仍显示。

真实只读目录校验已成功返回：gpt-5.6-luna、gpt-6-astra、gpt-reserve、gpt-5.6-sol、gpt-5.6-terra、gpt-5.5、codex-auto-review。目录随账户/服务变化，此列表不是产品静态 fallback；本次没有调用任何真实推理或启动 GPT agent。

相关回归：82 passed；UI：25 passed；桌面 typecheck 和 build 成功。扩大辅助路由回归：227 passed / 3 failed；同基线独立 worktree 复跑相同 3 失败（缺 anthropic 包一项、custom vision runtime 两项），非本改动新增。

## 本次独立复审实机证据

- 独立 Electron 40.10.2 窗口，`STARDUST_HOME=D:/Stardust/test-temp/gpt-real/home`、独立 userdata/TEMP，真实后端固定 feature worktree，未复制用户 config。桌面 IPC options 返回真实目录；main set Sol→Luna 后 info 与 config.yaml 均读回匹配值。
- 本分支真实 `AIAgent` + `ResponsesApiTransport` + `run_codex_stream`：Luna 完整返回 `LUNA_OK`；本地无副作用固定字符串 function call 返回后继续请求得到 `TOOL_OK`。原 auth.json SHA256 前后相同。没有 Astra 推理。
- 复审修复：缓存辅助 SDK 请求不重读 access/account；同步/异步辅助错误可能进入跨 provider fallback；主流物理重连不重读。新增回归后专项 11 passed。
- 目录证据：实际共享 URL 为 `models?client_version=0.0.0`，HTTP200，`cache-control: private, no-store`；省略 client_version 得到 HTTP400。原始 JSON 只有 models，未发现 GPT-6 Luna / GPT-6 Sol / GPT-6.1 Sol。尚未确认其他真实版本契约，不伪造版本。
- 证据位于 `D:/Stardust/test-temp/gpt-real/evidence/`：`runtime.log`、`auth-check.json`、`catalog-summary.json`、`model-picker.png`、`desktop-ready.png`。截图初版七项在后续代码中已改为尊重 visibility；需重新截图验收最终四项。UI 点击自动化仍未完成：菜单浮层截获点击；IPC 持久化已验证，不能将其称为 UI 点击通过。`display_name` 展示尚未接入，默认版本歧义尚待确认。因此此分支不应直接发布。

## 部署

当前成果仅在 feature worktree，不合并、不覆盖、不重启运行实例。父代理审查提交后，应先在独立 profile/实例验收模型目录、选择持久化和 Luna tools，再经明确发布流程部署并重启桌面及后端。不要只热替换运行中的 Python 文件。原客户端凭据文件无需迁移；不要导入 refresh_token。
