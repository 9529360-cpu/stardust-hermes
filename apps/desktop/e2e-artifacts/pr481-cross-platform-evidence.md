# PR481 E2E 跨平台收集证据

## 修改

- `e2e/ui-adaptation.spec.ts`：移除环境变量必须指定 D: 盘的模块顶层失败；在未设置 `STARDUST_UI_ADAPTATION_TASK_ROOT` 时，以 `os.tmpdir()`、进程号和时间戳构建每次任务独立目录。显式环境变量仍可覆盖默认值。
- 保留全部真实 UI 行为断言、隔离 sandbox 和截图文件存在断言；未 skip 或删除测试。
- 检查 `e2e/task-center.spec.ts`：截图目录使用 `path.join`，没有 D: 盘固定路径，无需修改。

## 验证结果

工作目录：`apps/desktop`。复用仓库根已有 Playwright/TypeScript 安装，无需安装依赖。

| 命令 | 结果 |
|---|---|
| `STARDUST_UI_ADAPTATION_TASK_ROOT= npx --no-install playwright test e2e/ui-adaptation.spec.ts --list`（修复前） | 失败，复现 `Set STARDUST_UI_ADAPTATION_TASK_ROOT to a task-isolated D: directory`，与 Linux CI 日志相符 |
| `node D:/项目/Stardust-Jarvis/node_modules/@playwright/test/cli.js test e2e/ui-adaptation.spec.ts --list`（修复后） | 通过，收集 2 条 ui-adaptation 测试 |
| `node D:/项目/Stardust-Jarvis/node_modules/typescript/bin/tsc --build tsconfig.e2e.json --pretty false` | 通过，退出码 0 |
| `node D:/项目/Stardust-Jarvis/node_modules/typescript/bin/tsc --build tsconfig.e2e.json --clean --pretty false` | 通过；清理编译生成文件 |
| `node D:/项目/Stardust-Jarvis/node_modules/@playwright/test/cli.js test --list` | 通过，收集 159 条测试、78 个 spec；包含 task-center 3 条和 ui-adaptation 2 条 |
| `node D:/项目/Stardust-Jarvis/node_modules/@playwright/test/cli.js test e2e/task-center.spec.ts e2e/ui-adaptation.spec.ts --list` | 通过，收集 5 条测试、2 个 spec |
| `node D:/项目/Stardust-Jarvis/node_modules/@playwright/test/cli.js test e2e/ui-adaptation.spec.ts --reporter=list` | 原生运行未启动：fixture 提示 `Desktop dist not built`，缺 `dist/electron-main.mjs` |
| `npm run build` | 被 `assert-root-install` 拦截：当前安装缺 `electron`、`blobatar`、`@rolldown/plugin-babel`。未执行 npm ci |

全量收集首次执行时，E2E TypeScript build 的生成 JS 被 Playwright 扫描，导致 Vitest unit 测试被误加载；执行 `tsc --build tsconfig.e2e.json --clean` 后全量收集通过，生成文件已清理。这是本地验证过程中的生成物影响，不属于源码改动。

原生 Electron 行为测试无法在当前依赖状态下完成；跨平台收集与 E2E 类型检查均已验证通过。
