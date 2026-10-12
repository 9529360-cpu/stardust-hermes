# GPT官方（gpt-official）

独立于 SIWC / openai-codex 的官方 Codex Responses API 直连账户路由。

- 从 `CODEX_HOME/auth.json`（未设置时 `~/.codex/auth.json`）只读提取 access_token / account_id；不会登录、刷新、复制原 token pair 或写回文件。
- 原 ChatGPT/Codex 客户端负责登录和续期；缺失、过期或无权限时显示需原客户端处理的提示，不自动切换其他 provider / 模型。
- 模型选择器从账户真实目录实时读取所有 slug，不使用静态权限模型或落盘凭据缓存。保留目录返回的隐藏项，因为它们仍是账户目录模型。
- 默认 `gpt-5.6-luna`，目录中存在时排首位；不会偷偷选择 Astra。所有目录模型均可在桌面选择器点击切换，使用既有 config 持久化，配置只保存选择而不保存凭据。
- 每次主会话直连请求前重新读取 access；与旧 OAuth refresh / credential pool 隔离。
- 请求身份保持 `originator=hermes-agent` / `HermesAgent`，不伪装官方客户端。沿用 ResponsesApiTransport 的 tools 和事件处理。

## 隔离验收

测试环境使用独立 `STARDUST_HOME`、`TEMP`、`TMP` 和 pytest `--basetemp`。专项测试 `tests/test_gpt_official.py` 采用 fake auth 与 httpx MockTransport，不调用真实推理。桌面测试覆盖 GPT官方名称、所有模型可选、登录错误无模型时仍显示。

真实只读目录校验已成功返回：gpt-5.6-luna、gpt-6-astra、gpt-reserve、gpt-5.6-sol、gpt-5.6-terra、gpt-5.5、codex-auto-review。目录随账户/服务变化，此列表不是产品静态 fallback；本次没有调用任何真实推理或启动 GPT agent。

相关回归：82 passed；UI：25 passed；桌面 typecheck 和 build 成功。扩大辅助路由回归：227 passed / 3 failed；同基线独立 worktree 复跑相同 3 失败（缺 anthropic 包一项、custom vision runtime 两项），非本改动新增。

## 部署

当前成果仅在 feature worktree，不合并、不覆盖、不重启运行实例。父代理审查提交后，应先在独立 profile/实例验收模型目录、选择持久化和 Luna tools，再经明确发布流程部署并重启桌面及后端。不要只热替换运行中的 Python 文件。原客户端凭据文件无需迁移；不要导入 refresh_token。
