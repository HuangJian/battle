# dashboard — 训练控制台

Battle City Web 的 NN 训练观测 + 操作面板。**独立 bun 项目**（自带 package.json /
tsconfig / tests），从仓库根一条命令启动：

```bash
bun run dashboard          # = cd dashboard && bun run start → http://127.0.0.1:8900
```

权限边界（DECISIONS §2026-09-09-goalnn-console-lan-readonly）：绑定 `0.0.0.0`，局域网可
**只读**查看任意课程/日志/节点统计；启 / 停 / 冒烟 / 模式 / 节点编辑等 POST 动作仅接受
回环来源（fail closed：来源不可判定即拒）。

## 依赖（自包含）

本项目有自己的 `node_modules/`，**不依赖仓库根**的安装：

```bash
cd dashboard && bun install     # 只需一次；按 bun.lock 还原
```

依赖解析已实测指向 `dashboard/node_modules/*`（`import.meta.resolve` 可复核）。
`bun.lock` **入库**（`.gitignore` 的 `*.lock` 原本只管运行时 PID 锁文件，已开例外）——
它是本项目可复现安装的唯一保证。`node_modules/` 沿用仓库根的忽略规则。

与根项目的关系：**彻底拆开**（2026-09-14）——根 `package.json` 不再声明本项目独有的
`preact` / `preact-render-to-string`，根 `tsconfig.json` 的 `include` 无 `dashboard`，
根门禁用 `--path-ignore-patterns='dashboard/**'` 排除 `dashboard/tests/`。
两个方向都实测过：根解析 `preact` 失败（不再是供给方），本项目解析正常；
对 146 个源文件做裸包名逐一解析，**零泄漏** —— 包依赖只有 `preact` /
`preact-render-to-string`，其余全是 `node:` / `bun:` 内建或相对源码 import。
两份 `node_modules` 因此互不重叠：任何一边没装，另一边照样能跑自己的门禁。

## 项目内命令

```bash
bun run start        # 启动控制台（:8900；--port N 可改）
bun run launch       # python 无头启动器：bun run launch --script run_rl.py --check
bun run build:ui     # 手动重建客户端 bundle（--analyze 看模块体积 top10）
bun run typecheck    # tsc --noEmit（独立于仓库根）
bun run test         # bun test --parallel --timeout=50000 tests
bun run lint         # oxlint
bun run format       # oxfmt
```

根仓库的 `bun run check` **不再**覆盖本项目（如上），所以本项目的门禁就是上面这两行。
提交时不必记得手跑：pre-commit 在 staged 含 `dashboard/` 时会自动跑 `typecheck` + `test`
（tsc 报错按 `dashboard/` 前缀归因，staged 命中才拦；逃生口 `SKIP_DASHBOARD_GATE=1`）。

## 目录结构

```
src/
  core/        基础原语：paths（路径事实唯一来源）/ log / net / proc / venv /
               config（rl-config 读写）/ registry（进程账本）/ slots（槽位端口算术）/
               sentinels / reload / types
  stack/       训练栈组件编排：specs（ProcSpec 构造）/ hub（拉起步骤）/ courses /
               smoke（启动前门禁）/ push（GPU 节点）
  launch/      python 无头启动器 CLI（venv/锁/冒烟/kill-previous/detach）
  evalboard/   评估板领域：store（逐局账本 schema）/ ladder（阶梯与门）/ batches /
               requests / runner / stats / sentinels / engine / ingest
  server/      HTTP 与服务端逻辑
    server.ts       监听、路由表、SSR 出口、监督器接线
    api/            只读端点（/api/state /api/pool /api/log/* /api/evalboard）+ 动作路由
    actions/        动作处理器（启/停/冒烟/预设/节点/预演/重启）
    eval-board/     评估板端点（视图合成 / 入账 / 探针 / 自动爬梯）
    iters.ts pool-history.ts exit-watchdog.ts build.ts
  web/         SSR + 浏览器 UI（禁 IO / 禁 node: / 禁 Bun 全局，有构建期禁词门禁）
    view/           共享视图类型与纯函数（客户端安全，唯一事实源；`routes.ts` = 路由真相）
    components/     通用 UI 原子（DataTable / TrendChart / Pill …）；**P1 起的行/反馈原语**：
                    StatusRow（行：状态点·名称·值·徽章·元信息·动作，含显式折叠头）/
                    StatusDot / SectionHeader / Empty（四态）/ InlineNotice（面板内动作结果一行）；
                    **P2b 起的总览两块**：AlertDock（告警坞：排序/折叠在 view/alerts.ts）/
                    KpiStrip（六格 KPI 条：取值/口径在 view/kpi.ts）
                    —— 「同一语义只有一个原语」，不要在面板里自绘（docs/dashboard-redesign.md §4.2）；
                    坞与 KPI 的领域判据全在 view/ 的纯函数里（组件只管折叠开关与动作绑定）
    app/            SSR 首屏 + hydrate 的浏览器应用（app / log / eval 三入口 + panels）
      shell/        应用外壳（Shell / Sidebar / Topbar；三档响应式，见 docs/dashboard-redesign.md §3.1）
      app.tsx       外壳 + 路由页面分派（/ · /metrics · /nodes · /wire，服务端 stamp `page`）
    render.tsx theme.ts
tests/        92 个本子系统的测试（原根 tests/ 的同名文件迁入 + 两巨型文件按分层拆开）
data/evalboard/  EvalBoard 账本数据根（默认值；EVALBOARD_DATA 可覆盖）
```

## 测试分层

测试**镜像 `src/` 的模块**（AGENTS §8）：一个测试文件对应它覆盖的那个模块，
文件名形如 `web-view-rows` ↔ `src/web/view/rows.ts`、`server-api-pool` ↔
`src/server/api`。dashboard 侧当前 **863 个用例 / 92 个文件**（2026-09-20；P2b 告警坞+KPI 条前为
804 / 90，P2a 课程矩阵前为 776 / 88，P1 行原语前为 746 / 87，重设计前基线 717 / 86），
拆分产出的文件最大 264 行（单节走势图；其余均 <170 行）。

> ⚠ **样式层没有任何断言**：整份 `theme.css` 被内联进 SSR 的 `<style>`，因此**裸类名/裸字符串断言
> 永远为真**（断言的是样式表，不是 DOM）——切出 `#root` 再断言（`web-ssr-console.test.ts::body()`）。
> 更极端的一类：**未定义的 CSS token 不会报错也不会崩**，只是那条声明静默失效
> （实例：`var(--line)` 用了四处而 `--line` 从未定义，两个徐章没描边、矩阵操作列竖线不可见很久
> 无人发现；见 `docs/dashboard-redesign.md` 审记 C15）。改样式后请 `grep` 新 token 有没有定义。

> ⚠ **web 用例只能断言结构，挡不住交互缺陷**：本仓 web 测试全部是 SSR（无 `happy-dom`/`jsdom`），
> 而 `preact-render-to-string` **丢弃全部事件处理器**（实测 `h('pre', {onClick}, 'x')` → `<pre>x</pre>`）
> —— 一个挂了隐藏点击区的元素和没挂的，渲染出的 HTML 一模一样。所以「点这里会误触收起」
> 这类缺陷靠读代码评审，别以为写了断言就守住了（实例与结论：`DECISIONS.md
> §2026-09-20-dashboard-shell-routing` 的「P1 续」第 2 条）。

按关注点聚合的两个巨型测试文件已按上表分层拆完：`training-console-preact.test.ts`
（1234 行 / 19 个 describe）与 `training-console.test.ts`（1354 行 / 17 个 describe，
含趋势图嵌套 describe）合计占原目录 42% 行数。拆分的唯一可接受判据是**用例与断言逐一不变**：
拆分前后 `bun run test` 都是 `306 pass / 0 fail` 且 `1907 expect()` 调用数一致 ——
计数对不上就说明有块搬漏或重复（实测抓到过一次：第二轮同名文件覆盖了首轮的 3 个
describe / 6 个用例，靠 `it(` 标题逐项 diff 找回）。

测试夹具**留在各自文件内**，唯一例外是 `tests/helpers/console-fixture.ts`：
`training-console.test.ts` 的旧夹具是 `beforeAll`/`afterAll` 级别的真实文件备份还原，
多文件 `--parallel` 下会互踩，且它的顶层 `await import()` 要求 env 重定向先就位——
该 helper 把「独立 scratch 目录 + env 重定向 + 被测模块 import」封成一次，
调用方只 import 它就不可能搞错顺序。纯函数测试不需要它（如 `web-view-format` 系）。

## 纪律

- **调用方只 import 各目录的 `index.ts` 桶**，不直连内部文件——内部拆分可自由演进，
  对外面由桶兜底。
- **路径只从 `src/core/paths.ts` 取**：它是全项目唯一用 `import.meta.dir` 上溯的地方；
  其余模块自算相对深度，一次搬迁就会静默错位。
- **`src/web/**` 是客户端代码**：不得 import `node:` / `fs` / `Bun` 或服务端层；三份
  bundle 构建时断言 gzip 体积与禁词（`bun run build:ui`）。
- **只读消费游戏契约**：`src/`（stages / arena-ladder / config-stage / difficulty /
  combat）与 `tools/agent/codehash-files` 是权威唯一源，用相对路径只读引用；反向依赖
  永远禁止。
