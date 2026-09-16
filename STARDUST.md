# Stardust local edition / 星尘本地定制版

This repository is a public source-code fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), based on upstream revision `24fd22b94d`. The original project, copyright notice, MIT license, and Git history are retained.

本仓库是 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的公开源码分支，基于上游版本 `24fd22b94d`。原项目版权声明、MIT 许可证和 Git 历史均予以保留。

## What this edition changes / 主要改动

- A quieter, localized personal-assistant desktop home and navigation hierarchy.
- Persistent health tracking and circuit-breaker behavior for an arbitrary model-route fallback chain.
- Credential-pool rotation remains available before route-level fallback.
- Automatic upstream checks and update installation are disabled intentionally, keeping the local edition pinned.
- Focused regression tests for the assistant home and persistent route health.

- 更安静、中文优先的个人助理桌面首页与导航层级。
- 面向任意数量模型路由的持久化健康状态、冷却和半开探测。
- 路由降级前仍支持凭据池轮换。
- 主动关闭上游自动检查和更新安装，避免本地定制被覆盖。
- 为助理首页和持久化路由健康机制补充回归测试。

## Product source / 产品源码权威

`9529360-cpu/stardust-hermes` is the source authority for this edition. Runtime update checks, release links, package metadata, and the Stardust bootstrap installers must point to this repository rather than silently treating the upstream project as the product release source.

`9529360-cpu/stardust-hermes` 是本版本的源码权威。运行时更新检查、Release 链接、包元数据以及 Stardust 安装入口都必须指向本仓库，不能把上游项目默认为本产品的发布源。

For fresh installs, use the Stardust-owned bootstrap entrypoints. They reuse the mature Hermes installers while pinning clone/recovery URLs to this repository:

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

新安装请使用以上 Stardust 安装入口。它们继续复用成熟的 Hermes 安装逻辑，但会在执行前把源码和恢复地址固定到本仓库，避免重装时回到上游版本。

## Privacy boundary / 隐私边界

This Git repository contains source code and test fixtures only. Runtime/operator data belongs outside the checkout and must not be committed. In particular, do not add:

- `.env` files or real API keys/tokens;
- `%LOCALAPPDATA%\Hermes\config.yaml` or other operator configuration;
- conversations, memory/profile files, pairing state, logs, caches, or route-health state;
- SQLite/database files, credentials, private keys, backups, or packaged desktop builds.

本 Git 仓库只包含源码和测试夹具。运行时和使用者数据必须留在源码目录之外，禁止提交：

- `.env` 文件或真实 API Key、Token；
- `%LOCALAPPDATA%\Hermes\config.yaml` 等个人配置；
- 对话、记忆/画像、配对状态、日志、缓存和路由健康状态；
- SQLite/数据库、凭据、私钥、备份以及打包后的桌面程序。

The root `.gitignore` excludes these common runtime paths. Review staged files before every public push; ignore rules are a guardrail, not a substitute for review.

根目录 `.gitignore` 已排除常见运行时路径。每次公开推送前仍应检查暂存内容；忽略规则只是防线，不能代替人工审查。

## Update policy / 更新策略

This edition does not automatically contact or install from upstream. Passive CLI update checks are off by default and the desktop background poller is disabled for the local edition. Explicit desktop update checks, when used, resolve against the Stardust repository rather than NousResearch. Upstream changes should be reviewed and ported deliberately rather than merged into an operator installation automatically.

本版本不会自动联系或安装上游更新。CLI 被动更新检查默认关闭，桌面端后台轮询在本地定制版中也保持关闭。用户主动触发桌面更新检查时，更新源解析为 Stardust 仓库，而不是 NousResearch。上游改动应经过审查后有选择地移植，而不是自动合并进正在使用的版本。

## License and attribution / 许可证与归属

The project remains under the repository's [`LICENSE`](LICENSE) (MIT). Hermes Agent and the original code are by Nous Research and upstream contributors. Stardust-specific modifications are provided under the same license. This fork is not presented as an official Nous Research release.

本项目继续使用仓库中的 [`LICENSE`](LICENSE)（MIT）。Hermes Agent 及原始代码归 Nous Research 和上游贡献者；星尘定制改动以相同许可证提供。本分支不是 Nous Research 官方发行版。
