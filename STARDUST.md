# Stardust local edition / 星尘本地定制版

This repository is a public source-code fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), based on upstream revision `24fd22b94d`. The original project, copyright notice, MIT license, and Git history are retained.

本仓库是 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的公开源码分支，基于上游版本 `24fd22b94d`。原项目版权声明、MIT 许可证和 Git 历史均予以保留。

## What this edition changes / 主要改动

- A quieter, localized personal-assistant desktop home and navigation hierarchy.
- Persistent health tracking and circuit-breaker behavior for an arbitrary model-route fallback chain.
- Credential-pool rotation remains available before route-level fallback.
- Stardust product updates are owned by this repository; Hermes upstream is never an automatic product-update source.
- Passive/background update traffic stays quiet by default, while explicit Stardust update actions target this repository.
- Focused regression tests for the assistant home, route health, and product update authority.

- 更安静、中文优先的个人助理桌面首页与导航层级。
- 面向任意数量模型路由的持久化健康状态、冷却和半开探测。
- 路由降级前仍支持凭据池轮换。
- Stardust 产品更新由本仓库负责，Hermes 上游绝不会成为自动产品更新源。
- 后台/被动更新流量默认保持安静，但用户主动执行 Stardust 更新时只访问本仓库。
- 为助理首页、路由健康和产品更新权威机制补充回归测试。

## Product source / 产品源码权威

`9529360-cpu/stardust-hermes` is the source, update, and release authority for this edition. Runtime product-update checks, update apply operations, GitHub Releases, release tags, release notes, package metadata, and the Stardust bootstrap installers must resolve to this repository.

`9529360-cpu/stardust-hermes` 是本版本唯一的源码、更新和发布权威。运行时产品更新检查、更新安装、GitHub Release、发布 Tag、Release Notes、包元数据以及 Stardust 安装入口都必须指向本仓库。

`NousResearch/hermes-agent` is retained for attribution and deliberate maintainer review only. Bringing upstream work into Stardust is an explicit engineering operation: review the upstream change, port or merge it on a development branch, run Stardust validation, and then merge it into Stardust `main`. An installed Stardust copy must never fetch/merge/push upstream automatically as part of a product update.

`NousResearch/hermes-agent` 只用于版权归属和维护者人工参考。吸收上游改动必须是明确的工程操作：先审查上游变更，在开发分支移植或合并，运行 Stardust 验证，再进入 Stardust `main`。已安装的 Stardust 不得在产品更新流程里自动 fetch、merge 或 push Hermes 上游。

For fresh installs, use the Stardust-owned bootstrap entrypoints. They reuse the mature Hermes installers while pinning clone/recovery URLs to this repository:

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

新安装请使用以上 Stardust 安装入口。它们继续复用成熟的 Hermes 安装逻辑，但会在执行前把源码和恢复地址固定到本仓库，避免重装时回到上游版本。

## Update policy / 更新策略

For normal product installs, `origin` must resolve to `9529360-cpu/stardust-hermes`. `hermes update --check` and `hermes update` use the Stardust-owned command boundary. A check never probes a configured `upstream` remote. A third-party or missing origin is refused rather than silently rewritten.

普通产品安装的 `origin` 必须解析到 `9529360-cpu/stardust-hermes`。`hermes update --check` 与 `hermes update` 经过 Stardust 自己的更新权威边界。检查更新不会探测本地配置的 `upstream` remote；第三方或缺失的 origin 会被明确拒绝，而不是被偷偷改写。

Older installations may still have the historical `NousResearch/hermes-agent` URL as `origin`. `hermes update --check` treats that case read-only and checks Stardust directly. An explicit `hermes update` may migrate only that exact historical origin to the Stardust repository before applying the update. Arbitrary forks are never migrated automatically.

较老的安装可能仍把历史 `NousResearch/hermes-agent` 地址保存为 `origin`。`hermes update --check` 在这种情况下保持只读，并直接检查 Stardust。真正执行 `hermes update` 时，只允许把这一种精确的历史来源迁移到 Stardust 后再应用更新；任意第三方 fork 都不会被自动迁移。

Dashboard/System page loading is passive and does not contact an update source. The explicit **Check now** action queries Stardust and the apply button is enabled only for a Stardust origin or the exact migratable historical origin. This keeps background traffic quiet without pinning the product forever.

Dashboard/System 页面加载属于被动路径，不会主动联系更新源。用户明确点击 **Check now** 时才查询 Stardust；只有 Stardust origin 或可迁移的精确历史 origin 才允许执行更新。这样既保持后台安静，也不会把产品永久锁死在旧版本。

Non-git/image-managed installs continue to follow their deployment manager. An unknown non-git install is not allowed to fall back to the legacy NousResearch ZIP updater; repair/reinstall it from a Stardust Release or Stardust bootstrap installer instead.

非 Git / 镜像托管安装仍由对应部署管理器负责。来源不明的 non-git 安装不得回退到旧 NousResearch ZIP 更新器；应从 Stardust Release 或 Stardust 安装入口修复/重装。

## Release policy / 发布策略

GitHub Releases are published only in `9529360-cpu/stardust-hermes`. The repository workflow `.github/workflows/stardust-release.yml` owns release publication. A release may be created by pushing an approved CalVer tag or by manually dispatching the workflow from a commit contained in Stardust `main`.

GitHub Release 只能发布在 `9529360-cpu/stardust-hermes`。发布流程由 `.github/workflows/stardust-release.yml` 负责。发布可以通过推送审核后的 CalVer Tag 触发，也可以从已经进入 Stardust `main` 的提交手动触发工作流。

Release tags use the existing CalVer shape:

- `vYYYY.M.D`
- `vYYYY.M.D.N` when more than one release is needed on the same date

The release workflow has `contents: write` only for tag/Release publication, verifies the release commit is contained in `origin/main`, and verifies the resulting Release URL still belongs to the Stardust repository. Merging ordinary feature PRs does not itself publish a release.

发布 Tag 继续使用现有 CalVer 形式：`vYYYY.M.D`；同一天多次发布时使用 `vYYYY.M.D.N`。发布工作流只为 Tag/Release 写入申请 `contents: write`，会确认发布提交已属于 `origin/main`，并在完成后验证 Release URL 仍属于 Stardust 仓库。普通功能 PR 合并不会自动发布版本。

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

## License and attribution / 许可证与归属

The project remains under the repository's [`LICENSE`](LICENSE) (MIT). Hermes Agent and the original code are by Nous Research and upstream contributors. Stardust-specific modifications are provided under the same license. This fork is not presented as an official Nous Research release.

本项目继续使用仓库中的 [`LICENSE`](LICENSE)（MIT）。Hermes Agent 及原始代码归 Nous Research 和上游贡献者；星尘定制改动以相同许可证提供。本分支不是 Nous Research 官方发行版。
