# Stardust

> 为我自己长期使用和维护的个人 AI 助理。

Stardust 是一个独立维护的个人助理工程。项目以 **Hermes Agent** 的开源代码作为技术底座，但从这里开始，产品方向、桌面体验、模型路由、更新策略和后续维护都以本仓库为准。

它不是 Hermes 的镜像站，也不是跟随上游发布节奏的发行版。

## 项目定位

这个仓库只服务一个明确目标：把现有底座持续改造成真正属于自己的个人助理。

- **独立维护**：`9529360-cpu/stardust-hermes` 是本项目唯一的源码与发布权威。
- **不跟踪上游更新**：不做定期 upstream merge / rebase，不自动同步，也不从上游安装更新。
- **个人助理优先**：后续功能、交互、界面与工作流优先满足自己的长期使用需求，而不是保持上游产品形态。
- **本地与隐私优先**：源码仓库不保存真实凭据、对话、记忆、日志、数据库或本地运行状态。
- **保留必要兼容层**：部分内部包名、命令名和目录仍可能保留 `hermes`，这是底层兼容与历史继承，不代表当前产品品牌定位。

## 当前改造方向

### Personal Assistant Desktop

桌面端正在从原始通用 Agent 界面改造成个人助理工作台，重点包括：

- 中文优先的首页与导航结构；
- 更适合长期日常使用的桌面交互；
- 独立的视觉体系与布局；
- 后续围绕个人工作流继续演进，而不是复刻上游 UI。

### Model Routing

为长期稳定使用，项目已经加入并继续完善：

- 多模型路由的持久化健康状态；
- 路由级故障切换与熔断 / 冷却机制；
- 凭据池轮换；
- 本仓库自己的更新与恢复来源。

## 安装

新安装使用 Stardust 自己的入口，不从上游安装：

### Linux / macOS / WSL

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

### Windows PowerShell

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

## 仓库边界

这个 Git 仓库只放源码、测试与项目文档。以下内容不应提交：

- `.env`、API Key、Token、私钥；
- 本地用户配置；
- 对话、记忆、画像、日志、缓存；
- SQLite / 数据库文件；
- 打包后的桌面程序与个人运行数据。

更完整的项目边界和维护约定见 [`STARDUST.md`](STARDUST.md)。

## 技术底座与许可证

Stardust 最初基于 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的开源代码构建，当前分支保留原项目的 Git 历史、版权声明与 MIT License。

**Hermes Agent 是技术底座与代码来源；Stardust 是从该底座继续独立改造、独立维护的个人助理项目。**

本项目不是 Nous Research 官方发行版。后续 Stardust 自有改动继续遵循仓库中的 [`LICENSE`](LICENSE)。
