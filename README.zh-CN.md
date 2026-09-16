# Stardust

> 为我自己长期使用和维护的个人 AI 助理。

Stardust 是一个独立维护的个人助理工程。项目以 **Hermes Agent** 的开源代码作为技术底座，但产品方向、桌面体验、模型路由、更新策略和后续维护都以本仓库为准。

它不是 Hermes 的镜像，也不跟随上游发布节奏。

## 项目原则

- **独立维护**：`9529360-cpu/stardust-hermes` 是本项目的源码与发布权威。
- **不跟踪上游更新**：不做定期 upstream merge / rebase，不自动同步，也不从上游安装更新。
- **个人助理优先**：功能、界面和工作流围绕自己的长期使用需求演进。
- **隐私优先**：仓库不保存真实凭据、对话、记忆、日志、数据库或个人运行状态。
- **兼容层可以保留**：内部包名、命令名和部分目录仍可能使用 `hermes`，这是历史兼容，不代表当前产品品牌。

## 当前改造方向

### 桌面个人助理

- 中文优先的首页与导航；
- 更适合长期日常使用的桌面交互；
- 独立视觉体系和布局；
- 后续围绕个人工作流持续演进。

### 模型路由

- 多模型路由健康状态持久化；
- 故障切换、熔断和冷却；
- 凭据池轮换；
- Stardust 自己的安装、恢复和更新来源。

## 安装

只使用 Stardust 自己的安装入口。

### Linux / macOS / WSL

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

### Windows PowerShell

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

不要使用原 Hermes 官方安装地址安装本项目，否则会得到上游版本而不是 Stardust。

## 仓库边界

这个 Git 仓库只放源码、测试和项目文档。不要提交 `.env`、API Key、Token、私钥、本地配置、对话、记忆、画像、日志、缓存、数据库或打包后的个人程序。

更多维护和隐私约定见 [`STARDUST.md`](STARDUST.md)，安全报告见 [`SECURITY.md`](SECURITY.md)。

## 技术底座与许可证

Stardust 最初基于 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的开源代码构建，并保留原项目的 Git 历史、版权声明与 MIT License。

**Hermes Agent 是技术底座与代码来源；Stardust 是在该底座上继续独立改造、独立维护的个人助理项目。**

本项目不是 Nous Research 官方发行版。
