# Stardust / 星尘个人助理

Stardust is an independently maintained personal-assistant project built on the open-source Hermes Agent codebase. The repository keeps the original Git history, copyright notices, and MIT license, but Stardust now has its own product direction and maintenance policy.

Stardust 是一个以 Hermes Agent 开源代码为技术底座、但独立维护和持续改造的个人助理项目。仓库保留原始 Git 历史、版权声明与 MIT 许可证，但从现在开始，Stardust 拥有独立的产品方向和维护策略。

## Product direction / 产品方向

The goal is not to mirror Hermes or stay aligned with upstream releases. The goal is to keep reshaping the codebase into a personal assistant optimized for long-term individual use.

本项目的目标不是继续作为 Hermes 的镜像，也不是保持与上游版本同步，而是持续把现有底座改造成更适合个人长期使用的专属助理。

Current priorities include:

- a calmer, Chinese-first personal-assistant desktop and navigation hierarchy;
- persistent health tracking and circuit-breaker behavior for arbitrary model-route fallback chains;
- credential-pool rotation before route-level fallback;
- repository-owned install, recovery, update, and release paths;
- focused regression coverage for assistant UI and model-routing behavior.

当前重点包括：

- 更安静、中文优先的个人助理桌面首页与导航层级；
- 面向任意数量模型路由的持久化健康状态、冷却、半开探测和故障切换；
- 路由降级前的凭据池轮换；
- 由本仓库自行控制的安装、恢复、更新与发布路径；
- 面向助理界面和模型路由行为的回归测试。

## Source authority / 源码权威

`9529360-cpu/stardust-hermes` is the source and release authority for Stardust.

`9529360-cpu/stardust-hermes` 是 Stardust 的唯一源码与发布权威。

Runtime update checks, release links, package metadata, installers, recovery paths, and future distribution logic must point to this repository unless explicitly changed by the maintainer.

运行时更新检查、Release 链接、包元数据、安装入口、恢复地址以及后续分发逻辑，都应以本仓库为准，除非维护者明确决定修改。

## Upstream policy / 上游策略

Stardust does **not** track upstream Hermes releases as an ongoing maintenance strategy.

There is no planned periodic upstream merge, rebase, automated sync, or automatic upstream update installation. Future work is maintained directly in this repository. If a specific upstream implementation is ever useful, it may be referenced or manually adapted as an isolated engineering decision, but that is not the default maintenance path.

Stardust **不再把跟踪 Hermes 上游版本作为维护策略**。

后续不计划进行定期 upstream merge、rebase、自动同步，也不从上游自动安装更新。未来开发直接在本仓库继续维护。若某个上游实现确实有参考价值，可以针对单项能力进行人工研究或移植，但这属于独立的工程决策，不代表恢复上游跟踪关系。

## Installation / 安装

Fresh installs should use Stardust-owned bootstrap entrypoints:

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

新安装应使用 Stardust 自己的安装入口，源码与恢复地址固定指向本仓库。

## Compatibility naming / 兼容命名

Some internal paths, package names, commands, environment variables, and configuration locations still contain `hermes`. These names are inherited from the technical base and may remain where changing them would create unnecessary compatibility risk.

部分内部目录、包名、命令、环境变量和配置路径仍会保留 `hermes`。这些名称来自技术底座；如果重命名只会增加兼容性风险而没有实际收益，则可以继续保留。

This should not be interpreted as the product identity. The product maintained here is Stardust.

这些内部兼容命名不代表当前产品身份；本仓库维护的产品是 Stardust。

## Privacy boundary / 隐私边界

This Git repository contains source code, tests, and project documentation only. Runtime/operator data belongs outside the checkout and must not be committed. In particular, do not add:

- `.env` files or real API keys/tokens;
- local operator configuration;
- conversations, memories, profile files, pairing state, logs, caches, or route-health state;
- SQLite/database files, credentials, private keys, backups, or packaged desktop builds.

本 Git 仓库只包含源码、测试和项目文档。运行时与个人数据必须留在源码目录之外，禁止提交：

- `.env` 文件或真实 API Key、Token；
- 本地使用者配置；
- 对话、记忆、画像、配对状态、日志、缓存和路由健康状态；
- SQLite / 数据库、凭据、私钥、备份以及打包后的桌面程序。

The root `.gitignore` is only a guardrail. Public changes should still be reviewed before being pushed.

根目录 `.gitignore` 只是防线。公开推送前仍应检查变更内容。

## License and attribution / 许可证与归属

Stardust was originally built from the open-source [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) codebase. Hermes Agent and its original code are by Nous Research and upstream contributors. Stardust keeps the repository's MIT license and original attribution/history.

Stardust 最初基于 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的开源代码构建。Hermes Agent 及其原始代码归 Nous Research 和上游贡献者；Stardust 保留仓库中的 MIT License、原始归属与 Git 历史。

Stardust-specific modifications are independently maintained in this repository. This project is not an official Nous Research release.

Stardust 后续改造由本仓库独立维护。本项目不是 Nous Research 官方发行版。
