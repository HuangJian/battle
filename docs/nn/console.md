# NN 训练控制台集成 — 技术档案

> dashboard 侧：组件面 / 调度器视图 / 任务包与产物两条腿。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §10 回传腿也点亮控制台（同步写 per-game.json + 课程账本行）（2026-09-22）

用户之问（2026-09-22）：「**如果是云机通过网络请求回传，会算这些数据回显吗？**」——
之前**不会**，而且这件事最刺眼的地方是**两条腿的观测面不一致**：人工导入能修（`docs/nn/remote-transport.md` §27④），
实时回传不能。

### 缺口的确切形状

回传（`POST /offline/artifact`）此前只做三件事：落 `offline/<run>/it-NNN/{weights,opt,row}.json`、
往**本课程**账本追一条 `offline_artifact` 事件、回 200。而控制台那张表只读 `iteration` 事件
（`state-view.ts` → `iters.readIterMetrics`），「耗时/击杀/残血/道具」四列更是**逐局**聚合的
（`readRoundActuals`）——单局 manifest 只在跑它的那台机器上，云机轮末 `prune` 就删了 ⇒
**权重在岸、末轮能评，但表一行不显、四列恒空**。

### 修法：两条腿共用同一张翻译表 + 同一个落点

* `remote/artifacts.ledger_row_from_metrics(row, run_id=…, source=…)`（新增，唯一翻译实现）：
  产物账本行 → 课程账本 `iteration` 事件。`run_id` = 哪条腿/哪个包，`source` = 谁搬的
  （`deliver_import` / `offline_backfeed`）——复盘时一眼分辨曲线从哪来。搬不到的字段**留空**
  （写 0 会被读成「真的零击杀」）。
* `remote/artifacts.metrics_row` 增补（additive，~17 float/轮）：`report.dimMeans` /
  `report.scoreStats`（kills/accuracy/loot 与 score 列的来源）+ `report.perGame`
  （`rl/reports.compact_per_game`，~200B/局）。**不落课程账本**（否则每轮 +65KB），
  由落地方写成 `it<N>/per-game.json`。
* 落地方两处：`remote/deliver_zip.write_per_game_files`（导入）+ `hub_server::_land_round_metrics`
  （实时回传）。**幂等**：账户行同 `it` 已存在就不写；`duplicate` 投递根本走不到（只在接新时写）
  ⇒ 同一轮不会出现两行（读方按 it 画曲线，两行=曲线打结）。
* 读方 `dashboard/src/server/iters.ts`：`readRoundActuals` = **`it<N>/per-game.json` 优先**，
  缺席才回落到 `manifest.json` 目录扫描；两路喂同一个 `aggregateActuals`。
  `ACTUALS_SCHEMA_V` 3→4（旧缓存里的 manifest 值不得压住新落盘的画像文件）。

### 验证

* python：`tests/test_multi_course_hub.py::test_offline_backfeed_moves_the_console_table`
  （回传一轮 ⇒ `it3/per-game.json` 逐字节等于 `row.perGame`、账本恰一行 `iteration`、
  字段搬运 `winRate/samples/expectedGames/ticks/rollout_sec/ppo_sec/kl/dim_means/score_mean`、
  重复投递仍是 `duplicate` 且不写第二行）；`tests/test_deliver_zip.py` 同规（来源标记键从
  `import_run_id` 改为共享表的 `run_id`+`source`）。
* dashboard：`tests/server-iters-round-actuals.test.ts` 5 例（只有 per-game.json 也能算四列 /
  (stage,seed) 取样本多者 / 缺席回落 manifest / 坏 JSON 与非法行跳过 / `readIterMetrics` 行带
  `actuals`）。
* 门禁：`nn-python-gate` **2129 passed** 绿、根 `bun run check` 绿、dashboard
  `typecheck+test` **1108 passed** 绿。

### 留白（不假装完整）

**已经打出来的旧包**（如 19:27 那份 `deliver-x20-demo-mix.zip`）里没有 `dimMeans`/`scoreStats`/
`perGame` ⇒ 那份包的四列与 kills/accuracy/loot/score 仍是空（刻意不编数字）；**新打的包**
（点「训练」重打包之后的每一轮）才会带上。回传腿同理：只有**新代码**跑出来的轮才有 `perGame`。

---

## §9 控制台：开课回执的「起点-基线对照行」（accident.plan §5.3，2026-09-21）

**要治的那件事**：C 双臂从收敛权重（it175）以 `kk(1)=1` 满额缰绳复活，it1 kl=0.90、锚主导
更新连烧 30 轮；两臂 ×12h 只换来「无结论」。开课前**盘上就已经有**的那个事实——「本腿恢复的
权重已经贴着课程 bc 权重那一档（甚至更低），还要拿满额锚去拉」——当时没人被告知：训练侧第五轮
已把这一行打进 `out.log`，但操作员的动作是「夜里点开课就走」。本轮回执把它摆在开课那一下。

**落地**（控制台侧，`dashboard/`）：新模块 `stack/kickstart-receipt.ts`（纯函数组装 + 轻量账本
读）→ `server/actions/course-lifecycle.ts::openCourse` 的 `detail`。读数与阈值全与执行面同源：
基线 = `tmp/<课>/eval_log.jsonl` 文件序末条 it0 行的 winRate（同 `kickstart_burn.baseline_reading`），
起点 = 末条 it>0 行；噪声带/点数走 `courses.<课>.kickstart_burn.{margin_pp,points}`（同
`kickstart_burn.burn_overrides`）。★ 两支：起点低于基线 > 噪声带（熔断从第一个点起算）/
起点在噪声带内且 kk ≥ 0.5（满额锚先把它拉回去，C 事故的那种配置）。

**lesson**：
- **镜像常量必须对着 python 源码核对**（单测读 `kickstart_burn.py`/`loop_core.py` 的文本取
  字面量）：TS 侧抄一份阈值是不可避免的（控制台不能 import python），但抄完不设闸 = 两处判据
  各自安好、对不上号（与 §2/A 的 course_fp/corpus_fp 同一个病的预防）。
- **同一份账本两个读法要当面对账**：控制台的 `readEvalSummaries` 全量 parse（视图构建用），
  回执读法按 `"eval_summary"` 子串预滤（18MB 账本、开课那一下）——单测钉「两者同数」，
  免得快的那份悄悄漂成另一种口径。
- 真盘实测：x20-clutch 差 -6.0pp（起点已低于基线）、x20-steady +2.0pp、x20-clutch-null -3.8pp
  ——三门全是「配 kk=1 会先变差」的配置，回执现在会当场喊。

**门禁**：`cd dashboard && bun run typecheck && bun run test` = **991 pass**（新增
`tests/kickstart-receipt.test.ts` 23 + `course-lifecycle.test.ts` 集成 1）；根 `bun run check` 绿。

---

## §8 本机伪 GPU 节点退出控制台：只剩冒烟预演自起自停（2026-09-19）

**一句话**：`workerServe`（`remote_worker_serve`）不再是被受管组件——没有卡片、没有账本键、
没有日志页入口、没有端口兜底清场、没有「回落本机」的 push 预设；它此后只是 trainingLoop
冒烟预演的一次性配角（`stack/push.ts` 起，跑完/失败即杀）。

用户口径：「workerServe 伪节点直接从 dashboard 去掉，**它只是用于 trainingloop 冒烟测试**，
用户只关心冒烟是否通过，不会手动去开启/停止伪节点。」

### 为什么这不是「换个轴」而是「退出去」

R3-6 曾把它的收敛列为「节点轴」(否决④) 的延后项。但它的语义轴其实是**冒烟预演的临时件**：
生命周期 20s、一次预演一份、与任何 GPU 身份无关。给它造节点轴账本 = 给一个不该常驻的东西
一个常驻身份（而它常驻的唯一后果是把「服务面 · 单例」卡行多塞一个用户不想要的开关）。

### 删了什么（受管面，逐处）

| 处 | 内容 |
|---|---|
| `core/types.ts` | `Component` 键、`Registry.workerServes`、`LegacyFlatRegistry` 项 |
| `core/registry.ts` | `COURSE_COMPONENTS`/`PLURAL`/迁移名单里的 `workerServe`（旧 `workerServes` 表已无读者） |
| `stack/specs.ts` | `WORKER_SERVE_ENTRY` + `workerServeSpec`（ProcSpec 整个删掉） |
| `server/actions/start.ts` | `case 'workerServe'`（含幂等/端口兜底/登记） |
| `server/actions/stop.ts` | 端口映射表里的 push 端口项 |
| `server/actions/smoke.ts` | 冒烟分支 |
| `server/actions/restart.ts` | `restartSpecFor` 分支 |
| `server/actions/labels.ts` · `server/api/component-meta.ts`（`ALL_COMPONENTS`/日志/健康端口）· `server/api/logs.ts`（`LOG_NAME_MATCH`） | 展示与解析面 |
| `web/view/component-groups.ts` | `NODE_FACE_COMPONENTS` 例外名单删除（它存在的唯一理由就是这个键）；`ComponentFamilyId` 回两族 |
| `web/app/panels/LogNavCard.tsx` | 不再 filter（受管组件全集 = 日志页入口全集） |

### 删了什么（能力面）：「一键本机 push」

`applyLocalPushNodeConfig` / `localPushUrl` / `configurePushEndpoint` 的 `allowLocal` opt-in /
`findHealthyGpuPushNode` 的 `includeLocal` / `PushTarget.viaLocalWorker` / `preset.ts` 的
`['selfNode','workerServe','trainingLoop']` 顺序分支。理由：执行面解析在 2026-09-15 就把
「缺 gpu_push → 自动回落本机」改成了**响亮报错**（回落会把「云机连不上」伪装成「训练正常」），
剩下的 opt-in 只服务单测；而伪节点不再是受管组件之后，这条路径唯一的效果就是**把课程 push
目标指向一条没人服务的本机地址**——留着即静默失败通道。`preset.ts` 也从此**到不了** `local`
来源（类型上只剩 manual/config 两档）。

### 保留了什么（读面识别 + 冒烟）

- `NodeConf.local_push` 标记与 `pushTargetFromConfig` 的 `kind: 'local'`：历史配置里残留的本机
  条目与指它的 `courses.<课>.push_node_url` 必须**看得见**（看得见的坏过静默的）；复用扫描
  一律排除它（没有 opt-in 了）。
- 冒烟预习侧 `stack/push.ts`：直接 `spawnBg`（不再造 ProcSpec），仍按课程取槽位 push 端口与
  per-course work 目录（**双课同冒**不得互踩），20s 未就绪则**先杀掉自己起的进程**再抛错
  （旧版对 spawn 后的 `launchSpec` 失败没有兜底，会留一个没人认领的孤儿）。
- 训练侧只改一句引导日志：`remote/worker_server.py` 的 `CodeChangedError` 指引从「重起
  workerServe」改成「重跑 `remote_worker_serve`」。

### 回归（三把防回流尺子）

`tests/push-config.test.ts`：原「本机回落」describe 重写为「没有写入口，只有识别面」——
① **受管组件全集**（`ALL_COMPONENTS`/`COURSE_COMPONENTS`/`registryTriples`）里没有它；
② **启动面与 spec 面**（start/preset/smoke/specs 源码）里没有它，且 `preset.ts` 的 push 顺序
只剩 `['selfNode','trainingLoop']`；③ **冒烟侧确实自起自停**（`stack/push.ts` 里有
`remote_worker_serve` 与 `killPid`）。另：`server-api-logs`（组件日志映射改 5 个）·
`server-api-state-view`（快照 5 组件）· `single-hub-tunnel`（所有课程组件的槽都归 `''`）·
`training-multi-course`（伪节点仍按课程隔离端口/work，但不再是 spec）· `web-component-groups`
（全组件=卡行全集）· `web-components`（SSR 去掉节点面例外）· `training-port-reclaim`（`ownsResource`
只剩 hub/隧道）同步真值。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist） | ✔ 全绿 |
| `cd dashboard && bun run typecheck` / `bun run test` | ✔ 干净 / **651 pass / 0 fail** |
| 三份 bundle（`bun dashboard/src/server/build.ts`） | ✔ all bundles ok |
| 根 `bun run check` | ✔ **1868（1864 pass / 4 skip / 0 fail）** |

记录：`DECISIONS.md §2026-09-19-goalnn-retire-local-fake-node` · plan §5.2 R3-7 · memory。

---

---

## §7 R3-3：组件卡分族（服务面单例角色 vs 课程面按课程）（2026-09-19）

**一句话**：六个受管组件不再排成一行——按**作用域**分成「服务面 · 单例」（selfNode / hub / 隧道，
与课程数量无关）与「课程面 · 按课程」（trainer / 本机 worker，对象 = 当前查看的那门课）。

### 之前错在哪

`<b>共享</b>` 一个布尔徽章 + 操作员记忆，是区分「全机一份 / 一个进程服务所有课程 / 按课键控」
的全部信息。两条真会发生的误读：① hub/隧道已单例（§2026-09-18），卡片却仍按「当前查看的课」
渲染 —— 有人会给这门课**再起一个 hub**（第二个实例抢同一端口）；② trainer 卡片看着像全局对象，
按下去起的却是**当前查看的那门课**（换课程 = 换对象，视觉上零提示）。

### 形状

```
服务面 · 单例   [selfNode 单例] [hubServer 共享] [cloudflared 共享]  │  课程面 · 按课程  [trainingLoop] [localWorker]
```

族标题带悬停说明（「与课程数量无关：一个进程服务所有并行课程（0 门课也在，100 门课也只有一份）」/
「卡片上的对象是当前查看的那门课」）。

### 四个决定

1. **作用域三态单点在 `core/registry.ts::componentScope(key)`**（`singleton` / `shared` / `course`，
   就是既有两张槽位表的另一个面），服务端算一次填进 `ComponentView.scope`（取代 `shared` 布尔）。
2. **族归属 = scope 的函数，视图层不写 key 名单**：名单一旦与槽位规则漂开，症状是某个组件从 UI 上
   **消失**（而它照样被启动、被监督、被冒烟）。族内顺序才是化妆（`ORDER`；未列出的落组尾**不丢**）。
3. **节点面例外声明成数据**（`NODE_FACE_COMPONENTS = ['workerServe']`）：`worker_server` 的语义轴是
   节点/GPU 身份（hub 的 push 派发与竞速也按节点算），渲染在节点行；不再用面板里一行 `filter` 静默
   过滤（读代码的人只看到一行 filter、不知所为何来）。`LogNavCard` 复用同一常量。
4. **`scope` 缺省/未知 ⇒ 按 `course` 渲染**（单侧保守，与调度器卡片的 `kind` 同一条规矩）：少一个
   徽章只是少信息；凭空空贴「共享」会让操作员以为「停它就是停全局」（实际只停本课）。
   徽章只标 scope 说不出来的那件事：共享 / 单例；按课程 **无徽章**（默认语义，每行再挂就是噪声）。

### 回归（两把尺子：全组件恰好归属一处 + 与 registry 对拍）

`tests/web-component-groups.test.ts`(11)：分族与族内顺序（乱序输入）/ 节点面例外是真组件 /
**每个 `ALL_COMPONENTS` 的 key 要么在卡片行要么在例外名单里**（新增组件忘了归档 ⇒ 红）/
**族归属只能是 `componentScope` 的函数**（两份判据不许漂）/ `scope` 缺省保守 / 未列出的 key 落组尾不丢 /
空组不渲染 / 不改动调用方数组（原地 sort 会让上游快照顺序随渲染变化）/ 徽章三态。
`tests/web-components.test.ts` 的分族 SSR 断言（两组标题 + `data-family` + 族内顺序 + 共享×2/单例×1 +
节点面组件不在卡行）。

### 踩到的坑（构建期，值得记）

客户端代码里写**未加引号的 `node:` 对象键**（`Record<ComponentFamilyId, …>` 里的那个 `node: []`）
会让三份 bundle 全红 —— `server/build.ts` 的禁词门禁把 `node:` 当「引入了 node 内置模块」。
修法：只给**会渲染成组**的两族留元数据（`FAMILY_META: Record<'service' | 'course', …>`），
`'node'` 只作为词汇存在；`component-groups.ts` 的 `ComponentFamilyId` 注释里写明了这条。

### 未做（明确记录）

账本键的**真正收敛**：`trainingLoop` / `localWorker` 仍是 per-course 键（控制台仍按课起
`run_rl.py --course`，尽管训练侧已有 `--serve` 单进程服务所有课程），`workerServe` 仍住 per-course 表
（轴却是节点）。那是启动面/监督面的改动（含 `TrainLaunchModal` 精简与旧条目换代接管）；本轮的分族
正是它的前置——换成共享槽后，进程面全在服务面、课程面只剩数据。

---

---

## §6 R3-4 控制台半：BC 课与 RL 课在同一张调度器卡片里并列（2026-09-19）

**一句话**：`LoopQueue`（训练调度器卡）不再被「当前查看的是不是 BC 课」门控，BC 行与 RL 行
**并列**展示；行上带课程种类，于是「在等哪个 GPU job 回传」这件事对两类课程都答得出来。

### 之前错在哪（不是「少个功能」）

`app.tsx` 里那张卡是 `stateView?.isBc ? null : (…<LoopQueue/>…)` —— 而 `isBc` 说的是
**当前查看的那一门课**。用「查看对象的属性」门控「跨课程列表」是范畴错误，后果很具体：
**选中一门 BC 课 ⇒ 整张调度器卡片从页面上消失**，于是 BC 课在调度器视图里根本不存在。
而 BC 课恰恰是最需要这张卡的那类（它的全部墙钟都在「等 GPU 回传」，而这句话只有这张卡说）。

### 三个决定

1. **行上带课程种类**（python `build_rows` 的 `kind` ← `loop_plan.course_kind`，判据 =
   `curricula/<课>.bc.jsonc` 是否存在，与控制台 `isBcCourse` 同源；视图层 `LoopCourseKind`）。
   不带种类 UI 只能猜，而猜错的方式很坏：把 BC 读成 RL 会得到一整排**看着像真的**零
   （门禁 verdict / KL / 连击那些字段在 BC 账本上永远不存在）。
2. **`kind` 缺省/未知一律按 `rl` 渲染**（保守方向是**单侧**的）：python 比控制台旧（还没这个
   字段）时，少一个徽标只是少信息；反过来凭空空贴 BC 标签，会对外宣称「一轮 = 一个任务」
   （而它其实有 13 步）——**假承诺比缺标签贵**。`'BC'` 这种大小写不符同样不认。
3. **文案按种类分叉**（`kindBadge` / `stepTitle` / `pendingTitle` 三个纯函数）：BC 行说
   「一轮 = 一个任务：采集语料 → 发布 job → 等 GPU 回传 → 落位归档」并点名指标在
   `bc_epoch`/`bc_eval` 事件里；RL 行保持「待办 N 步（顺序即依赖顺序）」与 13 步原样。
   BC 行的悬停里**不出现** verdict / KL / 门禁字眼（那些它没有）。

### 顺手改的展示面

`run_rl_cluster.py` 的人读表加了 `kind` 列；BC 行的 facts 行不再摆 `iterations=/last_verdict=`
那一排 RL 的零，改成「bc 课程（轮指针 itN）；指标看账本 bc_epoch / bc_eval 事件」。

### 回归

`tests/test_loop_plan_bc_rows.py`(9，读面：种类 / 指针跟 `bc_round_completed` / 在飞来自
`job_pending` / 作废也是终局 / manifest 缺失不丢在飞 / 未知课程默认 `rl` / 两行共存 / 人读表带列)
· `dashboard/tests/web-app-loopqueue.test.ts`(+3：BC 行与 RL 行并列渲染且只有一行带 BC 徽标 /
两边悬停各说各的 / 卡片不再被 `isBc` 门控——用正则断言那个包裹形状不再存在)
· `dashboard/tests/server-api-loop-queue.test.ts`(+1：`kind` 的保守默认)。

### 踩到的坑

`build_rows` 里局部变量 `kind` 一度既是「等待种类」又是「课程种类」，赋值顺序决定了行里
那个 `kind` 到底是哪一个（症状：控制台把 BC 课标成 `ready`）。改成 `wait_kind` / `kind`
两个名字，并在视图解析器里同样改名（`parseLoopQueue` 里等待种类叫 `waitKind`）。

### 未做

BC 行的**暂停/恢复**能点（控制文件按课程名，与种类无关），但 BC 的指标面板仍是独立的；
真机「一个 serve 进程带 BC + RL」的实跑仍待做（与 R2e 同口径）。

---

---

## §5 R2c-3 余下：调度器进控制台（单例卡片 + 每课队列视图 + 在等什么）（2026-09-19）

R2a/R2b/R2c 把单进程调度器造出来了，但**没人看得见它**：`trainingLoop` 收敛为一个进程之后，
「某门课这一轮为什么还没走完」要翻 N 份日志 + 猜（`run_rl_cluster.py` 原本只给终端看）。
本节把它接进控制台——只读侧，不动训练行为。

### ① 数据源：走 python 只读入口，**不在 TS 重算判据**

`run_rl_cluster.py --json` 的输出就是控制台卡片的契约面（训练侧只读：不训练、不发布、不等待）。
判据（指针 → `RoundFacts` → `pending_tasks`）与 CLI 表逐字段同源；若在 TS 里照账本重写一遍，
就是第二份真相（R2b 否决 `loop-state.json` 的同一条理由）——两边会以不同速度演化。

- `dashboard/src/server/api/loop-queue.ts`：懒算 + **TTL 10s** + 单飞。TTL 比 hub 观测面的 5s 宽，
  因为事实变化的粒度是「一轮」（分钟级），而一次冷算要起一个 python（解释器冷启 + 扫 N 课账本/
  journal/shard 目录，亚秒级）。服务器启动时暖一次缓存，避免 SSR 首屏等子进程。
- `LoopQueueRunner` 是**可注入接缝**（与 `deliver_zip` 导入同惯例）：控制台这一半（argv 形状 /
  结果翻译 / TTL / 单飞 / 与在训事实合并）在 dashboard 用例里全测到，python 那一半由
  `nn-training/tests/test_loop_plan_waiting.py` 钉死——两边各测自己那一半，中间不再叠一层端到端。
- 读失败（解释器缺失 / 超时 / 输出不可解析 / 形状不符）**不抛**：视图带 `error` 上屏，UI 显因 + 空态。
  观测面坏掉不该把整页 `/api/state` 带崩（与 hub 总览 / 隧道 A/B 同口径）。

### ② 「在等什么」的判据留在 python（`loop_plan.waiting_state`）

控制台那一列不是 TS 从 facts 推的散文，而是 python 单点算出的 `(kind, text)`：

| kind | 何时 | 文案 |
|---|---|---|
| `inflight` | 有已发布未回传的 job（`commit_journal.inflight()`） | `等远端回传：ppo@37（jid=… via push）`；多条 → `等 N 个远端任务回传（…）` |
| `collect` | 本轮已落一部分局、配额未满（或未知） | `等采集落盘（120/150 局）` / `采集中：已落 78 局` |
| `idle` | 本轮无待办（账本已结算 / 未开训） | `本轮无待办（账本已结算 / 未开训）` |
| `ready` | 无外部等待 | `无外部等待，下一步 ppo` |

优先级 inflight > collect > idle > ready：**进程外的等待排第一**（结果在别的进程/机器上，
运维唯一能干预的那一类）。

★ **`games_planned` 的诚实性**：盘上今天**没有**任何地方记「本轮计划多少局」（只有 `rl/plan.py`
的课程计划知道）⇒ CLI 传 0 = 未知，此时 `collect` 只报已落局数、**不报分数**（绝不出现 `78/0`）。
真 supervisor 带上课程计划后会走到带分母的文案。这与 `already_done` 同一条规矩：算不出来的事实
不得当成完成，也不得编出分母。

### ③ 卡片：单例 + 两个事实源逐行合并

`dashboard/src/web/app/panels/LoopQueue.tsx`（RL 区，BC 课不出）——表头「调度器 · **单例** ·
在训 N/M · N 课等回传 · 排队等资源 X · 池占用」，每课一行：课程 / 在训-未在训 / it / 下一步 /
待办深度 / **在等什么**（四态各自一个色阶）。点行 = 切到查看该课（与总览卡同一条路径，LAN 只读
下也可用：只改本浏览器的查看目标）。

- **在训与否来自 registry**（`trainingLoop` 进程存活），不是 python 给的：盘上事实看不出「进程还在
  不在」。缺了它，一门停了的课会被读成「等外部」——所以未在训的行淡一档 + 悬停说明「这是盘上事实
  推出的队列状态」。
- 合并放在视图层（`withTraining` 纯函数）：python 说「卡在哪」，registry 说「有没有人在跑」。
- 与「并行课程总览」分工：总览回答 hub 侧「谁在派活 / 谁离线」，本卡回答训练侧「这一轮卡在哪一步」。

### 门禁

- nn python gate：**1446 passed / 3 skipped**（ruff + mypy 干净；+14 新例）
- dashboard：typecheck 绿、**567 passed**（+25：`server-api-loop-queue.test.ts` 13 / `web-app-loopqueue.test.ts` 12）、
  `bun dashboard/src/server/build.ts` 三份 bundle 通过
- 根 `bun run check` 绿、`bun run build` 绿

### 记录

`DECISIONS.md`（R2 条目新增「控制台接线」段，含「不得在 TS 重算判据」的防再犯条款）、
`plan/r2-loop-task-queue.md`（相位表）。**仍未做**：`checkpointCacheMb` 真机 RSS 实测表（R2c 上线
前置条件）、R2d 操作面（入队 / 暂停 / 单例 trainingLoop 卡片）、R2e e2e。

---

## §4 控制台 worker 登记入口 + 面板重组（P1 余下）：课程 select 解放 / 在训课程全高亮 / 并行总览（2026-09-18）

用户指令：控制台 worker 登记入口 UI + 面板重组。抉择与代价全文见
`DECISIONS.md §2026-09-18-goalnn-console-worker-register-and-overview`，这里只记落地与实测。

**形状：登记是配置编辑器，总览是 hub 观测面的读方。**

- **worker 登记**（`/v1` 无关，纯 rl-config）：面板只 upsert `nodes[]` 里的 `gpu_push` 条目
  （hub 按 mtime 热重载），写完 best-effort `POST /admin/push-workers {action:"reload"}`；
  **ping 不通不拦登记**（云机没开机是常态），返回值如实分流 `ok:true`/`ok:false`。
  改 url 或删节点时把 `courses.<课>.push_node_url` 一并改写/清除——留着就变成「指向不存在
  的 URL」⇒ python 匹配 0 个节点后**静默回落 pull**。移除一个 rollout 节点（非 gpu_push）
  会被 409 拒（不把采集节点改写成 push worker）。
- **两列探活刻意分开**：面板直探 `{url}/ping`（此刻这台通不通）与 hub 周期探活结论
  （调度器认不认它在线的唯一判据）各占一列；两列不一致本身就是信号（面板通而 hub 判离线
  ⇒ hub 还没重载配置）。
- **课程 select 解锁**：旧锁定（§367，hub 运行中不许切课）的前提已被「单 hub 托管 N 份账本」
  推翻，多课程并行下它只会把查看/切换锁死。现在恒可切（含历史课程），只改「看哪门课」。
- **在训课程 = registry 里 `trainingLoop` 存活**（服务端 stamp `trainingCourses`）：课程 select
  里**每一门**在训课都带 🔥（不是只标第一门），查看的课之外的用「正在训练：…」标签提示。
- **并行总览**（新面板）：hub 行 = 应答基址 / 在派发课程数 / 活跃 worker 数 / 竞速 / 停机 /
  最近派发（轮转游标）；每课一行 = 在训 / 离线 / iter / 队列深度 / 在飞，点行即切查看。
  这一行回答「某门课为什么在饿着」的四种可能（不在 hub 课表 / 被标离线 / 有活但没空闲 worker /
  队列空），在日志里要靠猜。
- **每课 iter 只认账本里的 `iteration` 事件**（`training_log.jsonl` 是多写者文件：训练侧写
  iteration，hub 追加 job_completed/job_failed；把 `job_completed.it` 当轮次会读出还没跑完的那一轮）。
- **hub 基址不新增配置键**：按 registry 里活着的 hub 条目逐个试 `/admin/queue`，第一个应答者即
  观测源 ⇒ 单 hub 多课与每课一 hub 两种形状都读得对；都没有 → 「hub 无应答」，不编地址。
- **容错与预算**：观测面任何失败（无 hub / 401 / 坏 JSON / 账本不可读）→ null/空态，绝不把
  `/api/state` 带崩；总览与登记表**共用一次** hub 探测（5s TTL + 单飞），冷算里 hub 探测与
  worker 直探并行开跑（串行会到 3×超时）。

**落地**：`dashboard/src/web/view/course-overview.ts`、`src/stack/hub-admin.ts`、
`src/server/api/overview.ts`、`src/server/actions/workers.ts`、`src/web/app/panels/{CourseOverview,WorkerRegistry}.tsx`
（新）；`src/server/api/{route,state-view,index}.ts`、`src/server/actions/index.ts`、
`src/web/view/{index,console-types}.ts`、`src/web/app/app.tsx`、`src/web/theme.css`（改）。

**门禁**：dashboard typecheck / test **533 pass**（+41 例：overview 12 + 登记动作 16 + 面板 SSR 12
+ 课程 select/在训高亮改写 2 + 原有用例不变）/ lint 0 warning / build:ui 三份 bundle 均过；
根 `bun run check` 绿。

**两个实测踩点（都留了回归）**：
1. **`parseHubQueue` 的宽容解析不是装饰**：hub 是独立进程，可能比控制台新/旧一个版本——缺字段
   退化为 0/空比让整页 500 好得多；用例里专门断言「字段全缺 → 全零/缺省」。
2. **回环不可达地址会挂满直探超时**：测试里用 `http://127.0.0.1:1` 当「探过、不通」会让每条用例
   白等 1.5s（本环境对回环拒绝连接是 **DROP** 而非 RST）——改用「401 的假 worker_server」拿到
   即时确定结论，用例耗时从 9.2s 降到 0.3s。这条写进了测试注释，避免后人再踩。

**P1 余下**：① 单隧道——hub / cloudflared / trainingLoop 收敛为单例（现在仍 per-course 拉起），
随之把组件卡片拆成「单例角色」与「按课程」两种形状；② 多课程单 hub 的端到端 e2e
（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

---

## §3 控制台两条腿：导出任务包 / 导入产物即评估（2026-09-17）

**为什么记这一笔**：把 `docs/nn/remote-transport.md` §10–`docs/nn/remote-transport.md` §12 的离线能力**接到人手上**——之前「导出任务包 / 导回产物」
只有命令行，而这条链的每一步（导出前要不要先停训练、包对不对得上课程、评估跑哪一轮）
都是**容易做错且错了不报错**的地方。决策与否决见
`DECISIONS.md §2026-09-17-goalnn-console-task-bundle-exchange`。

```
导出（课程 + 起点权重 + 代码快照 = 整段剩余）
  面板「导出任务包」→ POST /api/exportTaskBundle（回环门控之后）
    ├ 门① 训练在跑？（run_rl 锁的 PID 活着）→ 拒启，说明「包里的起点就是当前进度」
    ├ 门② 没有 tmp/<课>/weights.json → 拒启（包必须有起点）
    └ 起 `run_rl.py --course <课> --ppo remote --run-iters -1 --export-bundle <abs>`
         （detach，日志 logs/<课>/export-bundle.log；**不注册组件**——它是动作不是组件）
  面板按 2.5s 轮询产出文件 mtime（不猜进程）→ 出现即可「下载」（GET /api/taskBundle 流式）

导入（跑完的产物 + 自动按课程配置评估）
  面板选文件 → POST /api/deliverUpload（multipart，走 JSON 动作层之外）
    ├ TS 侧两门：文件名课程须与当前课程一致、≤512MB
    ├ 落 tmp/<课>/deliver-uploads/<ts>-<name>（留证，不覆盖历史）
    ├ python -m remote.deliver_zip --zip … --dest tmp/<课>/deliver --course <课>
    │    （三道门：zip-slip / 形状（拿错包要指明「这是任务包」）/ 课程对账；原子落地）
    └ 接着起 evalA（共享启动器与互斥键 eval:A）评**末轮**权重 → 读数回填指标表
```

**怎么用**：面板「任务包（离线交出去 / 收回来）」；导出 → 下载 `task-<课程>.zip` → Kaggle/Colab
上传跑完 → 把产物 zip 命名 `deliver-<课程>.zip` 选回去 → 自动评估。

**实测（本机可验的部分全跑了）**：
- 真导出一次（c6-chip，dashboard 的**同一 argv**）：`task-c6-chip.zip` **3.3 MB**，`it16 → it60`
  45 轮，包内 `code.zip` 237 文件 / `ts_code.zip` 343 文件，逐件 sha 进 `task.json`。
- 门禁：nn python gate **1187 passed**（新增 5+7）· dashboard typecheck + **485 passed / 0 fail**
  + 三份 bundle ok · 根 `bun run check` **1853 pass / 0 fail**。

**两个落地时抓到的真缺陷**（都有回归）：

1. **「导出互斥」最初是假的**：键在 HTTP 请求里 `add` 又立刻 `delete`（等于没锁）——第二次
   点击会起第二个 `run_rl` 去抢同一门课的锁。改成**子进程退出时释放**（轮询 pid；不用
   `busySince` 那套 5 分钟 TTL——导出合法地会跑过 5 分钟，TTL 会在中途解锁）。
2. **`--export-bundle` 被轮内 shard 门误杀**（**最贵的一个**）：导出既不发 job 也不训练，
   但 `_remote_ppo` 里那条「M3 上云轮 shard 集必须为空（否则双份采集）」的门照旧生效——
   拿任何**跑过一轮**的课导包（traj 下有历史残留 shard）都会 `SystemExit`，包产不出来。
   即「控制台上这个按钮对真课全程不可用」。修法：判定抽成纯函数 `_gate_round_shards`，
   `exporting` 时直接返回空集（`register=False` 是同一条思路：导出不参与发布语义），
   回归 `tests/test_export_shard_gate.py` 同时钉住「关掉 exporting，两条门照旧生效」。

**代理侧踩坑（与功能无关，但会再踩）**：python 一次性进程的 cwd **不是**你 `cd` 的目录
（trainer 内部会切），**手工验证时必须给绝对路径**；相对路径带 `..` 的写入在沙箱下还可能
被静默丢掉——第一次导出「日志说 4.1MB、盘上没有」就是我自己传了相对路径。

---

## §2 it0 基线评审修订：重试语义 + 账本双向隔离 + Hero NaN 行（2026-09-13 评审）

对 §1 的 staged 实现做评审后发现三处问题，本条为修复记录（评审 + 修复同一批完成）。

**问题与修法**

- **P1（鲁棒性）基线派发是「单次尝试 + 纯远端」**：原实现只在 `it == _start_it` 派一次且不传 `local_gate`——`EvalDispatcher` 无 gate 时不建权重快照、不启本地 worker（纯远端），若首派时刻节点瞬时全挂/权重 POST 全失败，只记日志跳过，进程内永不重试（docstring 的「失败重试轮」只有跨重启才成立）。改为**落账前每轮重试**：`baseline_summary_landed(traj_dir, wver16)`（eval_local）查 eval_log 是否已有**同 bc 指纹**的 `iter=0` summary，未落账则每轮 rollout 收官后重派，账本去重保证重试只补缺口；落账 wver 缓存于 `_baseline_landed_wver`（bc 换文件 → 新指纹 → 重派新基线）。同时复用当轮 `self._eval_gate`（与 A-eval 同一把门，`_join_eval` PPO 收官置位）——基线本地局与 A-eval 一样让位 PPO；快照文件按流分流（`_eval_frozen_weights-baseline.json`），基线与 A-eval 并发重试轮不互相覆写快照（覆写会让 A-eval 本地局读错权重）。summary 带 `dropped` 也算落账：缺口在控制台诚实显示「缺N」，不为填缺口无限重跑失败局。
- **P2（潜伏）账本互吞只防了单方向**：基线账本按 `iter==0` 隔离了「A-eval 行吞基线」，但 A-eval 账本仍无过滤——若 bc 与 it1 的 `args.out` 指纹偶同（手动拷贝 bc 为 `--out` 启动等路径），it1 中途崩溃重启后 A-eval 会被同 wver 的 it0 行整轮跳过。`eval_done_keys` 新增 `min_iter` 参数：A-eval 传 `min_iter=1`，把 int iter<1 的行挡在去重外；缺 `iter` 的旧行与同 wver 跨 iter 复用（零梯度轮 args.out 未变 → 下轮免重评）照旧保留。顺带发现 **B/C evalboard 行与 A-eval 同册同 `event:"eval"`**（batch_eval 写 `source:"B"/"C"`，畸形批的 iter 缺省还是 0）——基线账本额外排除带 `source` 字段的行，B/C 局不得被当成基线已评估。
- **P3（用户可见 UI）Hero 主表漏入 it0 合成行**：`MetricsTable.buildRows` 有 `iter>0` 守卫，但 Hero「最新 6 轮完整指标」是另一套渲染（`[...iters].sort(desc).slice(0,6)`），腿的前 ~5 轮 it0 必进前 6——合成行的 NaN 字段直接渲染成红色 "NaN%" 徽章、"NaN" 单元格（`fmtPct(NaN)` 只判 `typeof number`）。抽纯函数 `heroMainRows(iters)`（view.ts：过滤 iter>0、倒序、截 6）供 Hero 使用，测试三例钉死。
- **nit**：显式 `--start-it 0` 会与基线的 dist 键空间 `{runId}.0` 撞键（采集/A-eval 任务键 = `{runId}.{it}`）——`validate_args` 启动期拒绝 `<1`；Hero 配对裁判文案 `vs开腿itN` 在 baseIter=0 时改为 `vs bc基线`（it0 是训练前基准，不是开腿首轮）。

**验证**：`test_baseline_eval.py` 重写派发语义测试（落账前重试/在飞跳过/local_gate 复用钉死/落账停/bc 换文件重派）+ `eval_done_keys` 双向隔离（含 B/C source 行与缺 iter 旧行）+ `baseline_summary_landed` 直测 + `--start-it 0` 启动期拒绝；`console-paired.test.ts` 新增 `heroMainRows` 三例（it0 排除/截 6/端到端合成行进不了主表行集）。`bun run check` 绿（2028 pass）；nn-python-gate（ruff + mypy 145 文件 + pytest 全量）绿。

**遗留（未修，已评级）**：console `readEvalGameWins` 仍按 iter 合并 B/C 行（iter≥1 的既有口径，B/C 批恰与 A-eval 同 iter 时配对图混源）——影响面在 evalboard 触发路径，与基线无涉，留作后续单独处理。

---

---

## §1 it0 bc 权重基线评估：配对基准不再随 run 起点漂移（2026-09-12，用户指令）

**症状**：控制台的 in-loop eval 配对基准恒取 `eval_log.jsonl` 里**第一条** eval 行（`console/iters.ts` 的 `evalIters[0]`）。那条基准随 run 起点漂移：resume 时首条可能是 it50，于是 `vs开腿it50` 实际比的是中途两点，而不是「从起点学会了多少」。

**改法（纯增量，训练侧与 console 两侧）**

- **训练侧**（`rl/loop_core.py::_maybe_dispatch_baseline_eval`）：在本 run **首次 rollout 收官后**（`it == _start_it`；全新腿 = it1）立刻用课程 `args.bc` 派一条 `iter=0` 的干净评估作恒定基线。守卫 `_baseline_eval_weights`：per-tick / 有课程 / `eval_games_per_stage>0` / `eval_every>0` / 有 enabled dist 节点（`nodes=[]` 纯本地路径本就不派 A-eval）/ bc 文件在盘；幂等（在飞跳过 + 跨重启按 `iter==0` 去重）；**失败自吞**（基线是观测设施，不得拖垮主线）。课程默认 `eval_every>1`（c6-bonus=5）⇒ 该轮本无 A-eval，**零重复计算**。
- **`eval_done_keys` 新增 `iter_filter`**（`rl/eval_local.py`）：it0 的已评估账本按 `iter==0` 隔离。必须如此——bc 与 it1 的 `args.out` 指纹**可能相同**（it1 就是 PPO 前的 bc 初始化权重），只按 wver 去重会让 it1 的 A-eval 把基线局吞成「已评估」，it0 行永远不落盘。`dispatch_eval_round/bg` 与 `EvalDispatcher` 加尾参 `baseline=False`；it0 用独立 `iter_id = {runId}.0`（与 A-eval 的 `{runId}.N` 在 agent 结果缓存里键空间隔离）。
- **门判据排除 it0**（`gate_check.read_trend_rows`）：`iter <= 0` 的 summary 不进趋势（用户定案：只当监控/配对基线）。否则会虚增 sustain 的「连续通过」计数、把 plateau 的上升趋势起点拉回 PPO 前。缺 `iter` 字段的旧行照旧保留（不过度收口）。
- **Console**（`console/iters.ts`）：配对基线改为「有 it0 取 0，否则退回首个 eval 轮」（老腿逐字节兼容）；`readIterMetrics` 在有 ≥1 条真实 iteration 行时合成一条 it0 行（只有 `evalData`，rollout 派生字段一律 `NaN` —— 趋势图的缺口约定，写 0 会在图上多画一个假零点）；`MetricsTable.buildRows` 跳过 `iter<=0` 的主行，只出 eval 子行（UI 标「基线」）。

**验证**：`tests/test_baseline_eval.py`（新，6 例：iter 隔离 / 守卫逐条反证 / 只在 `_start_it` 派一次 / 失败不抛 / **端到端** baseline 派发把逐局行与 summary 都写成 `iter=0` 且预置同 wver 的 it1 行不吞它 / 幂等）+ `tests/console-paired.test.ts`（新增 3 例：it0 为开腿基准、无 it0 退回首个 eval 轮、合成行与老腿兼容）+ `test_gate_check.py` 的 `read_trend_rows` 过滤。门禁：nn-python-gate（ruff + mypy 145 文件 + pytest 全量）绿；`bun run check` / `bun run build` 绿。

**遗留**：`eval_every == 1` 的课程在 it1 既有 A-eval 又有 it0 基线（测的是同一套 PPO 前权重）⇒ 会多跑一遍语料（账本按 iter 隔离，it0 行仍落盘）；本腿 c6-bonus 为 `eval_every=5`，不受影响。

---

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §28→§1 · 旧 §29→§2 · 旧 §64→§3 · 旧 §73→§4 · 旧 §82→§5 · 旧 §87→§6 · 旧 §88→§7 · 旧 §91→§8 · 旧 §122→§9 · 旧 §138→§10
