# Stardust Development Guide

Instructions for AI coding assistants and developers working on **Stardust**.

Stardust is an independently maintained personal AI assistant built on the open-source Hermes Agent codebase. `9529360-cpu/stardust-hermes` is the source and release authority for this project. Hermes Agent is the technical foundation and historical code origin; it is not the product identity or an upstream branch that this repository must continuously follow.

This root file contains repository-wide rules. Each major area has its own `AGENTS.md`; always read the nearest area guide before editing that area. When an area guide and this file differ, the more specific area rule wins unless it would violate the Stardust ownership, privacy, or source-authority rules below.

**Never give up on the right solution. Do not substitute a cosmetic patch for the structural behavior the request requires.**

## 强制实测门槛：未通过，不得提交或推送

**每次功能开发和 Bug 修复都必须真实测试。测试通过才允许 commit / push；禁止先提交、先推送，再让用户或 CI 替自己验收。此要求适用于主代理、子代理和所有开发者，子目录指南不得放宽。测试通过也不自动构成发布、部署或合并授权。**

- **前端必须打开真实前端。** Web 改动在真实浏览器中操作；桌面改动必须启动包含候选改动的原生 Windows Electron 隔离实例，实际点击、输入、切换并观察结果。只打开窗口不算验收；必须走完修改涉及的用户路径和关键边界场景。浏览器预览不能替代桌面 IPC、路由、持久化和系统集成验收；不得用用户正在使用的安装版代替开发实例。
- **后端必须走实际调用链。** 在隔离状态和必要的真实服务边界上执行相关 API、命令或集成流程，检查返回值、状态变更、错误路径及持久化结果。Mock、类型检查、构建和单测是补充证据，不能冒充真实集成验收；不得为测试擅自发送外部消息、付款或修改用户生产数据。
- **修复前复现，修复后回归。** 尽可能先以失败测试或可重现操作证明问题，再验证同一场景在修复后通过，并覆盖相邻功能、失败/取消路径及相关回归。间歇性、重复或竞态问题必须重复执行并注明次数；一次偶然通过不能宣告修复。
- **重复 Bug 必须按真实事件链切片定位。** 将最小复现场景拆为输入/事件产生、后端处理、持久化、传输、前端状态合并、最终渲染等边界，用时间戳、会话/轮次/消息 ID、事件来源、日志、DOM 和截图逐段对齐，查明首次重复或状态偏离发生在哪层。区分重复事件、重放、重复订阅、竞态和重复渲染；禁止仅加延迟、隐藏重复行或堆去重条件而不解释根因。切片指分层缩小问题，不是盲目拆文件。
- **证据必须对应最终候选代码。** 提交前记录代码差异/候选树身份、运行实例及数据隔离路径、测试步骤/命令、结果和小型证据位置；实测后若再修改代码，受影响的验收必须重跑。推送前确认提交内容就是已测试的内容，并运行相关质量检查。远程 CI 是第二道门槛，不能替代本地实测；CI 失败必须核查，不得只看聚合绿灯或归咎于 Mock/偶发。
- **阻塞就如实报告，不能降低门槛。** 窗口起不来、缺少必要授权、环境或真实服务不可用时，保留改动并明确尚未验证的场景，不得声称完成，不得提交/推送未经所需验收的功能代码。纯文档修改以内容审阅和差异检查验收，不为无关文档强行启动产品；行为、配置和脚本改动不能冒充纯文档。

## 强制开发纪律：禁止散落源码与开发垃圾

**这是必须执行的交付要求，不是建议。所有开发者、AI 代理和子代理均须遵守；子目录指南不得放宽本节的数据保护、运行隔离和收尾要求。不能以赶进度、测试方便或“以后再清”为理由绕过。**

1. **先定位置，再开工。** 开始任务前核实并记录唯一主仓库、当前分支/工作副本、任务专用临时目录和最终输出目录。不得边做边在桌面、下载、Documents、用户根目录或盘符根目录另起源码副本、测试环境和截图堆。
2. **复用现有环境，不准无限复制。** 本项目只有一个规范主仓库（以 origin 为准）；开发活动统一收在各自环境指定的开发根目录下。只有确有隔离需要才建立受 Git 管理的工作副本，至多同时保留两个活动任务工作副本；完成并集成后清理。不为一次试验再克隆整仓、安装整套工具链或复制依赖。不得为凑数量删除未完成或独有代码。
3. **开发版与运行版严格隔离。** 未获部署授权，不得覆盖、重启或清理正在使用的安装目录及其数据目录（具体路径由各自环境指定）。Windows 桌面、安装和更新行为必须在原生 Windows 的隔离实例验收；WSL 仅按需用于 Linux 后端、命令行和无界面兼容测试，禁止用 WSLg 反复启动 Electron 制造宿主崩溃转储。
4. **测试必须有边界。** 启动前明确超时、可用磁盘下限及单任务产物增长上限；测试前后核对磁盘和新增文件，长测试中途也要检查。达到约定阈值或发生重复崩溃时，停止自己拥有的测试进程、保存小型证据并排查；禁止原样循环重试、放任日志和转储增长。任务级 TEMP/TMP、测试数据和临时构建必须显式定向到任务专用目录，不得使用共享的系统临时目录；不能定向的宿主输出须单独跟踪。
5. **谁产生，谁收尾。** 交付前逐项核对本任务新增目录、后台子进程、工作副本、下载包、截图、测试虚拟环境和冗余构建。清除已确认可丢弃的任务产物；必要交付物与小型验收证据必须有明确位置。后台任务仍在运行时必须报告其用途、所属任务和停止方式，不得留下无人负责的进程。
6. **禁止以清理为名误删。** 账号、密钥、GPT/Codex 会话与历史、个人文件、真实项目成果及独有代码不是垃圾。不清空共享 TEMP 根目录，不按名称盲删，不沿链接删除目标，不强杀共享应用。归属不明或未提交成果先保留核实；系统自行生成且不支持定向的必要文件不能为了“零残留”强行删除。
7. **完成 = 功能真实验收通过 + 开发现场收尾核验完成。** 只有构建成功、单测通过、写完代码或写了这份规范，都不算完成。最终汇报必须给出真实验证结果、保留产物路径和残留清单；有占用、权限或归属问题就写明路径与原因，绝不虚报“全部清理干净”。这份规范是代理行为约束，不得冒充已经部署的自动拦截或操作系统沙箱。
8. **统一目录约定。** 开发活动收在各自环境指定的开发根目录下：新项目放 `Projects`；任务的实验、截图、测试 home、trace 和脚本放 `Temp/<项目>/<任务>`；必要的小型最终证据放 `Reports/<项目>/<任务>`。不得在桌面、下载目录、文档目录、用户根目录、磁盘根目录或相邻源码副本中产生临时产物。依赖、工具缓存和构建产物放在指定的开发磁盘上，复用已配置的共享缓存，不为每个任务重复复制或反复重装依赖。pytest 的 basetemp/cache 等工具输出应显式定向到任务目录。不得修改系统级 TEMP/TMP，也不得为满足本规则而引入目录联接（junction）。

## Stardust Ownership Rules

These rules are authoritative for every part of the repository:

- **This repository is independently maintained.** Do not add periodic upstream merge/rebase jobs, automatic upstream synchronization, or update paths that replace Stardust with an upstream Hermes checkout.
- **New product-facing ownership belongs to Stardust.** New install URLs, update URLs, release metadata, recovery paths, documentation links, product copy, and repository links must point to `9529360-cpu/stardust-hermes` unless the maintainer explicitly chooses another Stardust-owned endpoint.
- **Hermes naming may remain at compatibility boundaries.** Python packages, the `hermes` CLI, `HERMES_HOME`, existing environment variables, IPC/protocol names, install/data paths, executable names, app IDs, and other inherited interfaces may be load-bearing. Do not rename them merely to make search results cleaner. A compatibility rename requires a migration plan, fallback/rollback behavior, existing-install coverage, and data-preservation proof.
- **Attribution stays.** Keep the MIT license, original copyright notices, useful Git history, and accurate references to Hermes Agent / Nous Research where they identify the code foundation or a real external service.
- **Upstream code may be studied, not followed by default.** A specific implementation may be manually adapted when it solves a concrete Stardust problem. That is an isolated engineering decision, not restoration of an upstream tracking relationship.
- **Personal/runtime data never belongs in Git.** Do not commit `.env`, API keys, tokens, credentials, private keys, conversations, memories, profile exports, logs, caches, route-health state, local databases, backups, or packaged apps containing personal state.

See `STARDUST.md` and `CONTRIBUTING.md` for the public maintenance policy.

## What Stardust Is

Stardust runs the inherited agent core across a CLI, messaging gateway, TUI, web/dashboard surfaces, and an Electron desktop application. The current product direction prioritizes a long-lived personal assistant: a calmer Chinese-first desktop, personal workflows, reliable model routing/failover, local/remote operation, and Stardust-owned install/update/recovery behavior.

The inherited architecture is valuable and should be evolved rather than casually rewritten. Two invariants shape almost every core design decision:

- **Per-conversation prompt caching is sacred.** A long-lived conversation reuses a cached prefix every turn. Do not mutate past context, swap toolsets, reload memories, or rebuild the system prompt mid-conversation unless the design explicitly handles cache invalidation. Context compression is the exceptional path. State-mutating slash commands should normally defer prompt-affecting changes to the next session unless the user explicitly asks for immediate application.
- **The core is a narrow waist; capability lives at the edges.** Every model tool is sent on every API call. Prefer extending existing behavior, CLI commands + skills, service-gated tools, plugins, or MCP before adding a permanent core tool.

## Engineering Priorities

In order of importance:

1. Correctness, data safety, privacy, security, and recoverability.
2. Preserve user intent and existing working behavior unless the request explicitly changes it.
3. Fix the whole bug class and all materially equivalent call paths, not one visible symptom.
4. Keep Stardust independent of upstream release infrastructure and credentials.
5. Maintain compatibility for inherited runtime interfaces unless a deliberate migration replaces them.
6. Prefer simple, observable, reversible architecture over hidden automation.
7. Add regression proof at the strongest practical boundary.
8. Improve personal-assistant UX without weakening core runtime correctness.

Before changing behavior, trace the real production path and identify where the state is actually owned. Do not patch a renderer symptom when Electron/backend state is authoritative, or patch a backend helper when the caller has already discarded the information you need.

## Capability Footprint

Choose the least permanent surface that correctly solves the problem:

1. Extend existing code.
2. CLI command + skill.
3. Service-gated or session-scoped toolset.
4. Plugin.
5. MCP server/catalog integration.
6. New core model tool only when it is fundamental and cannot be expressed through existing terminal/file/browser/tool surfaces.

Do not add speculative hooks, managers, callbacks, or environment variables with no concrete consumer.

### Surface capability belongs to the session

A capability that exists because of the client on the other end (desktop panes, Projects, reactions, in-app browser surfaces) must resolve from the session/connection, not from a process-wide environment variable. Desktop and backend may run on different machines. `HERMES_DESKTOP=1` can legitimately describe how a backend process was launched; it does not prove a GUI is watching the current session.

Keep session-specific tools in named toolsets selected by the session/platform resolver. Use `check_fn` for reachability or opt-in, not for “which client is connected?” decisions.

## Code Shape Rules

- Keep facade modules narrow. Put substantial new behavior in topical sibling modules rather than growing god files.
- A file approaching ~2,000 lines, a function approaching ~300 lines, or high cyclomatic complexity is a signal to split by responsibility before adding more behavior.
- Prefer tables/maps to long `if/elif` ladders keyed on ids, names, routes, or kinds.
- No dead compatibility shims for internal moves. External compatibility belongs in the explicit compatibility layer.
- Do not add “defense-in-depth” wrappers, silent `try/except: pass`, flags nobody sets, or fallback branches with no demonstrated failure mode.
- Never infer process identity from argv substring matching. Use the canonical process/holder matchers for that subsystem.
- Never hardcode `~/.hermes` for runtime state. Use `get_hermes_home()` for code paths and `display_hermes_home()` for user-facing text.
- `.env` is for secrets. Non-secret behavioral configuration belongs in `config.yaml` or the owning structured configuration surface.
- Moving a public/internal symbol means updating its real call sites and relevant docs in the same change. Do not leave stale paths because a compatibility alias happened to mask them.

### TypeScript / Desktop style

- Shared or distant UI state belongs in small nanostores near the feature that owns it; rendering components subscribe with `useStore`, non-rendering actions use `$atom.get()`.
- Route roots compose surfaces; they should not become controllers.
- Prefer narrow hooks/actions over monolithic “do everything” hooks.
- Extend React primitive prop types instead of duplicating them.
- Keep Electron, renderer, and backend ownership boundaries explicit. Native filesystem/process/update behavior belongs in Electron; rendering and interaction state belong in React; agent execution belongs in the backend/Gateway.
- `window.hermesDesktop`, `hermes://`, executable names, and similar inherited identifiers are compatibility surfaces until a tested Stardust migration replaces them. User-visible copy around them should still say Stardust where safe.
- Desktop product update affordances update the Stardust client. They must not re-enable the inherited upstream `hermes update` synchronization path.

## Source Authority and Task Workspace Discipline

The remote `origin/main` branch is the **single authoritative product line**. Local clones, worktrees, task branches, CI checkouts, recovery bundles, and developer machines are disposable working copies; none of them may become a second source of truth.

For every feature, bug fix, refactor, CI change, documentation change, or other repository modification:

1. **Start from fresh main.** Fetch `origin`, verify the local base is clean, and fast-forward to the current `origin/main`. Do not start new work from a stale task branch, an old worktree, or a developer-machine snapshot.
2. **One task, one branch.** Create one descriptively named task branch from the current `origin/main`. Keep one authoritative remote implementation lineage for that task. Do not create replacement branches or PR stacks merely to get a cleaner history or a fresh CI run.
3. **One task, one working tree per machine.** A developer machine may create a temporary clone or worktree for the active task, but that workspace must track the task branch. Never keep long-lived alternate source trees such as `integration`, `validate`, `new-version`, or machine-specific “development copies” that can diverge from `main`.
4. **Parallel work stays isolated.** Concurrent developers use separate branches and worktrees. Never share one mutable working tree between tasks. If two tasks overlap, coordinate through the current `main` and explicit PR dependencies rather than copying files between workspaces.
5. **Avoid moving PR stacks.** If task B truly depends on task A, prefer finishing and merging A into `main`, refreshing from `main`, then continuing B. Do not leave a chain of PRs whose bases keep moving unless there is a concrete integration reason.
6. **Validate the exact candidate.** Run the relevant repository checks against the exact task head after refreshing its base. A green run on an older head or older base is not merge evidence.
7. **Merge back to online main.** Once the task satisfies its validation and review gates, merge it into `origin/main`. From that point, `main` is the product state; do not keep using the pre-merge task workspace as an alternate development line.
8. **Clean up immediately after integration.** Remove the local task worktree, delete the local task branch, delete the merged/closed remote task branch when safe, and prune stale worktree/remote-tracking metadata. A completed task must not leave an active development copy behind.
9. **Preserve before deleting uncertain work.** If an old workspace contains unique uncommitted or unpushed changes, inspect it before cleanup. Preserve genuinely unique material as a clearly named recovery artifact (for example a verified Git bundle) outside the active source tree, then remove the obsolete workspace. Recovery artifacts are cold backup only and must never be treated as a development source.
10. **Do not develop directly on `main` by default.** Use the task-branch flow even for small changes. Direct `main` maintenance is an explicit maintainer exception for a specific operation, not a standing shortcut.

Before consequential remote writes, refresh `origin/main` and overlapping PR state so another developer's merge cannot silently invalidate the base you verified. The desired steady state is always: one clean local `main`, zero or a small number of active task worktrees, and no unexplained duplicate Stardust source trees on developer machines.

## Development Environment

Activate the repository environment before Python work:

```bash
source .venv/bin/activate   # or: source venv/bin/activate
```

`scripts/run_tests.sh` probes `.venv`, then `venv`, then the managed compatibility location under `$HERMES_HOME`.

For JavaScript/TypeScript, install dependencies from the repository root. Workspace checks assume the root lockfile and workspace graph are authoritative.

## Project Structure

The filesystem is canonical; counts and exact module splits change over time. Important entry points include:

```text
run_agent.py          agent facade; turn loop implementation lives under agent/
model_tools.py        tool orchestration/discovery
 toolsets.py           named toolsets and core tool surface
cli.py                interactive CLI facade
hermes_state.py       session/state facade
hermes_constants.py   profile-aware home/path helpers
agent/                providers, turn phases, memory, compression, prompt assembly
hermes_cli/           CLI subcommands, config, setup, updater, dashboard backend
hermes_cli/web_routers/ dashboard API routers
tools/                tool implementations and environment backends
gateway/              messaging gateway and platform adapters
plugins/              plugin implementations and plugin contracts
skills/               built-in skills
ui-tui/               Ink terminal UI
tui_gateway/          JSON-RPC backend used by TUI/Desktop
apps/desktop/         Electron Stardust Desktop
apps/shared/          shared desktop/gateway protocol code
web/                  dashboard SPA
cron/                 scheduled jobs
scripts/              test/release/install/CI utilities
website/              Docusaurus documentation source
tests/                Python regression suite
```

User state is profile-aware under the compatibility `HERMES_HOME` hierarchy. Do not move or rename those directories without an explicit migration.

## Dependency Policy

Dependencies must remain bounded and reproducible.

- Python packages use an appropriate floor and upper bound; avoid bare unbounded `>=` constraints.
- Git dependencies pin immutable commit SHAs.
- GitHub Actions pin immutable action SHAs with a version comment.
- CI-only tools may use exact pins.
- Run `uv lock` after changing Python dependency declarations.
- Do not change dependency versions as collateral damage during unrelated refactors or branding work.

## Testing

For Python, use `scripts/run_tests.sh` rather than bare `pytest`. The wrapper enforces CI-like credential isolation, locale/timezone behavior, temporary `HERMES_HOME`, and file-level subprocess isolation.

```bash
scripts/run_tests.sh
scripts/run_tests.sh tests/gateway/
scripts/run_tests.sh tests/agent/test_foo.py -k test_x
```

General rules:

- A bug fix should normally add 1–2 invariant/behavior tests that fail on the broken behavior and pass on the fix.
- Test relationships and contracts, not volatile snapshots such as model counts, config version numbers, or catalog membership that is expected to change.
- Tests that depend on the real host OS run on that OS with the repository's `linux_only`, `macos_only`, or `windows_only` markers. Do not fake the interpreter's host platform to make process/filesystem behavior appear portable.
- Tests must not write personal state under the real home directory. Use the repository fixtures/temp `HERMES_HOME` patterns.
- Resolution chains, config propagation, security boundaries, remote backends, filesystem/network behavior, installers, and update paths need integration/E2E proof when practical; mocks alone are not enough.
- Prefer tests of behavior through callable/public seams. Source-text assertions are generally brittle; use them only for narrow repository-policy/source-authority invariants where executing every platform path is impractical, and pair them with runtime/integration coverage when the behavior is executable.
- Desktop visual/interaction changes require real Electron-window inspection for representative states and sizes in addition to static/type/unit checks.

### CI lane placement

`scripts/ci/classify_changes.py` decides which jobs run from changed paths. Put a regression test in a lane that will execute when the production source changes. A Python test that only asserts about TypeScript/package files may not run on a JS-only change; those checks usually belong in the JS/Vitest suite instead.

Do not weaken a check merely because historical failures are inconvenient. If a failure is unrelated, record the exact existing failure and keep the new contract independently proven.

## Commit and Review Discipline

- Preserve unrelated local/in-flight changes.
- Keep each commit coherent enough to review and revert.
- Before a large replacement, compare the resulting file/commit against the fresh base and verify no unrelated dependencies, generated files, or behavior changed.
- Do not force-push or rewrite shared history unless explicitly required by the maintainer.
- Direct `main` maintenance is allowed by project policy when authorized, but validation standards do not become weaker because there is no PR.

## Routing Table — working in X → read X/AGENTS.md

| Area | Read | Covers |
|---|---|---|
| `run_agent.py`, `agent/` | `agent/AGENTS.md` | turn phases, caching, providers, compression, message invariants |
| `cli.py`, `hermes_cli/`, `main.py` | `hermes_cli/AGENTS.md` | CLI, config, setup, profiles, updater, dashboard backend |
| `gateway/` | `gateway/AGENTS.md` | adapters, streaming, lifecycle, token/secrets boundaries |
| `tools/`, `toolsets.py`, `model_tools.py` | `tools/AGENTS.md` | tools, registry, toolsets, delegation, environment backends |
| `plugins/`, `hermes_cli/plugins*.py` | `plugins/AGENTS.md` | plugin contracts and compatibility |
| `tui_gateway/`, `ui-tui/` | `tui_gateway/AGENTS.md` | JSON-RPC process/transport and terminal UI |
| `web/`, `hermes_cli/web_routers/` | `web/AGENTS.md` | dashboard architecture and API/UI ownership |
| `apps/desktop/` | `apps/desktop/AGENTS.md`, `apps/desktop/src/AGENTS.md` | Stardust Desktop architecture, transport, state, packaging, UX |
| `skills/`, `optional-skills/`, curator code | `skills/AGENTS.md` | skill authoring and curation |
| `cron/`, kanban surfaces | `cron/AGENTS.md` | scheduler and kanban invariants |
| new `gateway/platforms/` adapter | `gateway/platforms/ADDING_A_PLATFORM.md` | platform adapter contract |

Long-form architectural background remains under `website/docs/developer-guide/`. Treat historical Hermes names there as technical history unless they falsely describe current Stardust product ownership, support, update, or release authority.
