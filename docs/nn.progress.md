# NN Player AI — Training Progress Log

> All architecture changes, eval results, and lessons learned are recorded here.
> New entries are appended at the top (reverse chronological).
---

## §84 R2d 写的一半：单进程 supervisor 真的能跑（serve + 引擎池 + 日志行路由）（2026-09-19）

R2c 造好了调度器（`loop_scheduler`）与任务体↔引擎的桥（`loop_runner`），但**没有驱动者**——今天仍是
「一门课一个 `run_rl.py` 进程」。本轮交付驱动者：`rl/loop_serve.py` + `rl/engine_pool.py` + `rl/log.py`
的行路由，入口 `run_rl_cluster.py --serve --courses a,b`（默认仍是只读计划视图）。

**分层各做一次**：进程级（utf8/faulthandler/chdir/启动前 push 一次/bun 检查）→ 课程级（参数解析与
`run_rl.py --course` 逐字段一致 / validate_args / **按课程的单实例锁** / 清本课 hub 停机态 / 建引擎对象）
→ 步骤级（`EnginePool.get` → `ensure_ready` → `LoopRunner.executor`，包在 `prefix_scope(课)` 里）。
`ensure_ready` 按**对象身份**判断：对象换了（首用/被驱逐后重建）走 `_setup()`，没换则幂等补栈。

**四个刻意的设计点**：① serve 模式**不收官停车**——新拆 `TrainingLoop.finish_course(it)`（收敛预采/云机
PAUSE/`run_complete` 落账，与单课程路径共用一份实现），停车会冻住其余课；② 不换 `sys.stdout`，课程归属
改成**行前缀 + 镜像写本课 `out_log`**（两个 Tee 会互相复制）；③ 引擎池驱逐 = 响亮的一次「重启」
（权重可从 `args.out` 复原、**Adam 动量重置**；绝不驱逐在用引擎；容量默认 = 并行课程上限 5）；
④ 初次入队的粒度必须匹配执行体（细粒度 13 步 / 轮粒度单个 `round`），且只用判据原语
（`course_facts` + `pending_tasks(round_tasks(...))`），不拉 `plan_course` 的展示面字段。

**回归**：`tests/test_engine_pool.py`(9) + `tests/test_log_router.py`(6) + `tests/test_serve_wiring.py`(8，
含「轮转交替 a,b,a,b」「13 步全表」「让位让别的课跑完」「容量 1 时驱逐重建再 setup」「坏课隔离」
「参数与 `run_rl.py` 对拍」——对拍 oracle 是在子进程里跑 `run_rl.main()` 本体，本机缺当前 era 权重时
跳过并写清理由，其余情况失败即红）。门禁：python gate **1476 passed / 4 skipped**；根 `bun run check` 绿。

**仍未做**：控制台的入队/暂停 + 单例 `trainingLoop` 卡片（读面卡 R2c-3 已交付）；**R2e** 多课 e2e 与
真机双课并行跑通（serve 的真机行为需要人验）。

## §83 R2c-3 收口：真机 RSS 实测（checkpoint 缓存上限的真实约束是「数量」）（2026-09-19）

R2c-3 的最后一项：单进程 supervisor 要为 N 门课各持一份 torch 栈，`checkpointCacheMb` 给多少
此前只能拍脑袋（本机 0 卡、remote 为主）。新增 `nn-training/scripts/measure_checkpoint_rss.py`
按训练路径**同一套构建代码**把栈造出来，逐课累加测 RSS。

### 实测（本机 CPU-only torch 2.7.1+cpu，单位 MB）

| 档 | 参数量 | model | grads | Adam | refs | **每课增量** |
|---|---|---|---|---|---|---|
| per-tick（无 ref） | 70,216 | 0.7 | 0.0 | 0.6 | 0.0 | **≈1.3** |
| intent（含冻结 ref） | 74,227 | 0.4 | 0.3 | 0.8 | 0.25 | **≈1.8** |
| goal（含冻结 ref） | 70,566 | 0.4 | 0.3 | 0.8 | 0.23 | **≈1.8** |
| **torch 基线**（与课程数无关） | — | — | — | — | — | **294.5** |
| **N=5 累计**（混合档） | — | — | — | — | — | 294.8 → **301.8** |

### 结论

- **每课栈是 MB 级 ⇒ `checkpointCacheMb` 只是第二道保险，真实约束是数量**
  （`checkpointCacheCourses` = 并行课程上限 5）；推荐 `checkpointCacheMb = 256MB`（≈130 课，
  正常永不触发，真触发就该被看见）。
- **两处最容易把表读错的地方**（已写进脚本 docstring + 用例）：① **先暖一次再测**——torch 惰性初始化
  会让**第一份**栈看起来贵两个量级（实测 78MB vs 真值 0.75MB）；② **栈必须活着**——被 gc 回收后
  第二课的增量会变成 0（分配器复用），累计曲线就是假的。
- **不在本表内的峰值**：单轮 episodes/chunk 缓冲（由 `mb` 与本轮样本量决定）是每轮瞬态，且本机 PPO
  池容量 1（跨课排队）⇒ 同一时刻只有一份；已由 forensics 埋点，不属缓存上限管的常驻量。
- **缓存实现本身未做**：随「单进程 supervisor 真上线」的相位落地（R2d/R2e）——今天没有持有者，
  先写就是无消费者的投机代码；这张表 + 两个默认值就是它上线时需要的全部输入。

### 门禁

`nn-training/tests/test_measure_checkpoint_rss.py`（7 例：推荐值取整/余量、候选表恒有默认架构兼底、
表格必须自带「测的哪份权重 / 跳过了谁 / 理论 vs 实测」、真造一份栈的量级保护带）；
nn python gate **1453 passed / 3 skipped**（ruff + mypy 干净）；根 `bun run check` 绿。

### 记录

`DECISIONS.md`（R2 条目「七续」）· `plan/r2-loop-task-queue.md` §6.1/§6.2（表 + 默认值）与相位表
（**R2c-3 ✅ 完成**）。下一相位 **R2d** 需先定「单例 `trainingLoop` 的操作对象键/迁移路径」。

## §82 R2c-3 余下：调度器进控制台（单例卡片 + 每课队列视图 + 在等什么）（2026-09-19）

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

## §81 R2c-3 余下：远端 PPO 三相拆分（发布 / 等结果 / 落位）（2026-09-18）

上一节把 13 步切出来之后，唯一还堵着调度链的就是 **`ppo` 这一步**：它是「打包 → 发布 → 阻塞轮询 →
三重校验落位」一个 400 行函数。闸门不能挂在它前面（= 还没发布就被挡住 ⇒ 永远等不到回传），
所以让位点必须**下沉到这一步内部**。

### ① 三相：同一任务内，不是三个任务

| 相位 | 方法 | 干什么 |
|---|---|---|
| ① 发布 | `_remote_ppo_publish` | 打包（payload/code/blob）→ `publish_job` → WAL attach → **直推时提交** → 会话成型 |
| ② 等结果 | `_remote_ppo_probe`（非阻塞）/ `_remote_ppo_fetch`（阻塞） | 问一句 / 等到 |
| ③ 落位 | `_remote_ppo_land` | 三重校验 + 落位 + job 记账 + WAL 收口 + 结算字段 + 结绳告警 |

`_remote_ppo` 保留为**组合入口**（三相串联），四个既有调用点（本机轮 / 节点轮 / 半离线整段 /
全离线导出）行为不变。

**为什么不拆成 `ppo_publish` + `ppo_wait` 两个任务**：三相共享一份会话（jid / 超时预算 / 打包墙钟 /
运输方式）。拆成两个任务就得把会话序列化到盘上才能跨任务传 —— 那正是 R2b 否决过的「第二份真相」
（`loop-state.json`）。任务粒度只决定让位点密度，而这一步的让位点已经在步骤内部了。

### ② 会话：纯数据，住轮内上下文

`rl/loop_round.RemotePpoJob`（`RoundContext.remote`）：**不进引擎实例属性**（§2.2；单进程多课程会
互相覆盖），**不落盘**（第二份真相）。刻意**不持有 payload 字节**（几十 MB）——直推换节点重发时从
job 目录重读（`find_payload`），于是它会话很小、可打印、读面不触发 IO。

### ③ 探针：状态码分类的唯一实现

`remote/hub_client.probe_job_result`（一次请求）→ 三态：

| 状态 | 含义 | 上层动作 |
|---|---|---|
| `ready`（200） | 结果已落 | 取结果 → 落位 |
| `pending`（202/404） | 还没回（正常排队） | **让位**，下一轮再问 |
| `transient`（网络错 / 5xx） | 「没答」 | 让位，下一轮再问 |
| 410 / status=failed | **终局失败** | **抛 `JobFailedError`** ⇒ 立刻停腿 |

「还没好」与「永远好不了」分开，是 x3-step 事故（把「bun 缺失」写成 25 分钟超时）的直接教训。
hub 与节点两条链路只差端点路径（`/jobs/{jid}/result` vs `/job/{jid}/result`），靠 `path` 注入复用
同一实现；`wait_job` / `wait_result` 两个阻塞版**改建在探针之上**，各自只保留退避策略
（hub 指数退避 / 直推固定 poll —— 历史差异，保留）。`poll_job` / `poll_result` 是给调度器用的
非阻塞出口；超时取 10s（探针误报「还没好」的代价只是再等一轮，永远不会误报成成功）。

### ④ 直推链路：发布即提交

探针要问「那份 job 现在怎么样了」，节点上还没有这份 job 时它只会一直答「还没回」⇒ **提交必须落在
发布相位**（提交本身是有界上传，不是那 25 分钟的等待）。换节点必须**重新提交**（新节点从没见过这份
job）。failover 判决抽成 `_push_over_nodes`：组合入口 `_push_job_round`、发布相位的
`_push_submit_first`、等待相位的 `_push_fetch` 三处共用一份实现（确定性失败 410 的原因在所有节点都
倒时原样上抛）。

### ⑤ 让位点由「谁在驱动」决定：`ctx.resumable`

细粒度驱动器（`LoopRunner`）造上下文时置 `resumable=True` ⇒ 未就绪就 `wait_for`；组合路径
（`run_one_round`）保持 False ⇒ 就地阻塞取结果。于是「组合路径没有让位点」从一个运行期异常
（`RoundYieldError`）变成了一个**事实字段**。

顺带把「在等谁」补进让位结果（`jid`，由 `LoopRunner` 从 `ctx.remote` 取）——状态 + 原因 + 在飞
`job_id` 三件套齐了才够定位一次卡住。

### ⑥ `WAIT_HOOKS` 里不再有 `ppo`（不得加回去）

表里的闸门跑在**步骤之前**，对 ppo 来说那是「还没发布」。让位已由 `step_ppo` 自己产生——
`tests/test_loop_runner.py` 把「不在表里」写成了断言（这个坑已经踩过一轮）。

### ⑦ 验收与实测

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist -n 12） | ✔ **1432 passed / 3 skipped** |
| 根 `bun run check` | ✔ 绿 |

新回归：

| 文件 | 例 | 钉住 |
|---|---|---|
| `tests/test_remote_probe.py` | 14 | 三态分类 · 410 抛而 202/404 不抛 · **只问一次**（预置第二个响应没被消费）· 探针超时 ≤10s · 两条链路端点各自正确 |
| `tests/test_remote_ppo_phases.py` | 8 | 未就绪⇒让位且不取结果 · **再入不重发布** · 不可续跑⇒就地阻塞 · 成功复位连败 · 发布/取结果/落位失败**都**进同一份判决 · 真策略接线（连败到阈值即降级） |
| `e2e/test_loop_supervisor_integration.py` | 改 | ppo 让位用例改为驱动**真三相**（假件只替 `publish`/`probe`/`land`）：a 让位时 b 跑完整轮；`entered` 里 ppo 连着两次（第一次让位、第二次落位）；走完的仍是 13 步各一次 |
| `e2e/test_push_mode_integration.py` | +3 | 发布相位提交 / 换节点**重提交**（断言 payload 真从盘上重读）· 确定性原因优先上抛 · 探针跟着当前节点 |

### ⑧ 踩坑记录

1. **`ast.parse` 不查 `return` outside function**：脚本化拆相时，错误切片让两行落到了模块级缩进，
   `ast.parse` 放行、mypy 才报 `"return" outside function`（`PyCF_ONLY_AST` 只跑解析器，符号表阶段
   的检查在编译成字节码时才发生）。**结论：脚本化搬运之后必须跑 mypy，不能只信 `ast.parse`。**
2. **函数内 import 清单是「拆相前的残留」**：`_remote_ppo` 顶部那份 7 个名字的 import 现在按相位各留
   所需（`git_head`/`iter_shard_dirs`/`pack_code_zip`/`publish_job` 在发布，`wait_job` 在等待，
   `verify_and_land`/`mark_job_completed` 在落位）——否则 ruff F401 立刻红（这正是它的价值）。
3. **测试补丁打在老 HTTP 出口上**：`wait_result` 改走共用探针后，`monkeypatch.setattr(
   "remote.push_client._request")` 不再拦截（真探针出网 ⇒ 用例超时）。补丁目标跟着实现搬到
   `remote.hub_client._request`（**HTTP 出口现在只有一处**）。另外假 `_request` 常把 `timeout`
   声明成 keyword-only，所以探针内部必须**关键字传参**（位置传参会 TypeError，症状是「一调就炸」
   而不是「问了没答」）。

## §80 R2c-3：轮内切成 13 步 + 让位闸门（2026-09-18）

R2 第三相位的第三段。用户口径（本轮）：**把轮内切成 13 步细粒度任务（先提 `RoundContext`）**。

### ① `RoundContext`：轮内状态搬家（为什么不放引擎实例上）

原轮体锁着四个轮内局部量（`pairs` / `dist_cfg` / `t_rollout` / `seg`）与一个**会被改写的指针**
（半离线整段 `_remote_run_segment` 一次吃掉 `it..end_it`）。步骤化之后它们必须跨步可见 ⇒ 提成
显式对象 `RoundContext`（`rl/loop_round.py`）。

**不放引擎实例属性**的理由是硬的：单进程多课程下，`self._pairs` 会被别的课覆盖（§2.2 无隐藏状态，
多课程只是把这条老规矩的代价从「难查」变成「串课」）。上下文生命周期 = 一轮，`it` 变了就换新的。

### ② 步骤表是唯一顺序来源

```
ROUND_TASKS (rl.loop_tasks)  ← 唯一顺序来源
     ├── STEP_ORDER  = ROUND_TASKS（同序）
     └── STEP_METHOD : kind → 引擎方法名
                        └── 组合路径 run_one_round 与细粒度驱动器都从表取步骤
```

步骤实现住在 `rl/loop_round_steps.py`（`RoundSteps` mixin，MRO 排最前）。切法**不是重新设计控制流**：
每一行都从轮体搬来，`break`/`return`/`it -= 1` 改由 `StepResult` 表达、驱动器施加。

| 原轮体 | 现在 |
|---|---|
| 顺序 190 行脚本 | `step_precollect_join` … `step_cleanup` 共 13 个方法，一步一个 `RoundContext` 入参 |
| `break` / `it -= 1` | `StepResult.outcome`（`ROUND_*`）由驱动器施加 |
| 该停就停 | `finish(ROUND_STOP)` / 正常 `None` |

**★ 顺序错纠正（真发现）**：`precollect_join` 必须排**第一**——它产出的是本轮 `it{it}` 的 shard，
而紧随的 `prepare_iter` 靠 `completed_pairs` 看盘决定「保留续跑」还是「清场重建」；反了就把上一轮
的预采整个作废（白烧一轮采集）。原轮体里这次 join 坐在 `run()` 循环头，位置一致。

**写测试时挖出的第二个坑**：`run_one_round` 里「让位」必须**先于通用失败分支**捕获。否则步骤
要求的让位会被 `except Exception` 吞成一次普通失败 ⇒ 落 `iter_error`、计连击、无限重试同一轮
（症状与「每轮都重试」完全一样）。为此立了专门的 `RoundYieldError`：组合路径没有让位点，遇到就让
它响亮上抛；细粒度驱动器才支持让位。

### ③ 让位闸门：一张表，且只有两处能装

`rl/loop_runner.WAIT_HOOKS`（kind → 引擎钩子名）。**钩子不存在 ⇒ 不让位**（老引擎行为逐字节不变），
所以表可以只先装能吃的部分。

| kind | 钩子 | 状态 |
|---|---|---|
| `precollect_join` | `TrainingLoop.precollect_ready` | ✅ 本轮装 |
| `ppo` | `remote_job_ready` | ⛔ **刻意不装**（见下） |
| `eval_join` | —（`WAIT_HOOKS` 里没有） | ⛔ **刻意不装**（见下） |

**⛔ `ppo` 必须先拆三相**：今天的 `_remote_ppo` 是一个阻塞函数（打包→发布→阻塞轮询→三重校验落位）。
在它**前面**加闸门 = 还没发布就被挡住 ⇒ 永远等不到回传。要装它得先把「发布 / 等结果 / 落位」拆开，
否则宁可不装。表里留位 + 理由写在 `loop_runner` 的 docstring 里。

**⛔ `eval_join` 不加让位**：本机 eval 局的墙钟是**故意藏在下一轮 rollout 里**的（`_eval_tail` 交棒、
`_dispatch_delayed_eval` 入口收拢）。给它让位 = 把那条尾巴重新串回轮边界，正好抵消掉当初压掉的
软等窗口。`tests/test_loop_runner.py` 把这个「缺席」写成断言，防止后来者顺手补上。

### ④ 已装的那一处：预采（1 小时轮询 → 让位）

`join_precollect_child` 旧形态每 2s 轮询 `completed_pairs`、上限 **1 小时**。单课程前台跑时这只
是「等一下」；单进程多课程下它就是**一个慢子进程拖垮所有课**的入口。

判据抽成 `rl/rollout_phase.precollect_ready`（**非阻塞**：句柄为空 / 子进程已退出 / 就绪 shard ≥
半波），**步骤内部循环改成调同一个函数** ⇒「调度器认为可以往下走」与「步骤进去真的不阻塞」不可能
分叉（两个口径分叉的症状是「调度器说开训、步骤进去还在轮询」，极难排查）。外部语义不变：超时仍
`terminate` + 回合自己采。`min_wave` 也抽成 `precollect_min_wave()`（两处共用同一个数）。

### ⑤ 读面补全「在等什么」

`WAIT` 时把原因落到队列（`q.reason = result.reason`），推进/收官时清空。理由：状态 + 原因 + 在飞
`job_id` 三者合起来才够定位一次「卡住」；而过期的原因比没有原因更坏。

### ⑥ 验收与实测

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest xdist -n 12） | ✔ **1406 passed / 3 skipped**（含 +28 新例） |
| 根 `bun run check` | ✔ 90s 绿 |

新回归：

| 文件 | 例数 | 钉住 |
|---|---|---|
| `tests/test_loop_round.py` | 13 | 表 == `ROUND_TASKS`、`STEP_METHOD` 与实现一一对应、`precollect_join` 必须最前、组合循环按表施加终止/异常分类 |
| `tests/test_loop_runner.py` | 5 | `WAIT_HOOKS` 的 kind 都在表里、`eval_join` 刻意缺席、钩子缺失⇒不让位、不就绪⇒`WAIT`+原因+`resume_at` |
| `tests/test_precollect_ready.py` | 7 | 非阻塞（<0.5s）、半波即就绪、已退出即就绪、与 `join` 同源、超时仍 terminate |
| `e2e/test_loop_supervisor_integration.py` | +3 | 13 步逐一走完 + **步级轮转**（前两步来自不同课）、ppo 处让位、预采处让位；细粒度与轮粒度**账本形状逐行一致** |

### ⑦ 踩坑记录

1. **mypy 只在 mixin 声明依赖属性时才认 `self.x`**——`RoundSteps` 引用了引擎的 50 个属性/方法，
   必须像既有 `TrainingSteps`/`TrainingGuards` 那样声明；类型要**照实际赋值写**（`_total` 是展示用
   字符串 `"0"/"∞"`、`_node_rollout_sec` 是 `float | None`），写窄了立刻红。
2. **ruff B010 vs mypy method-assign**：测试里替换实例方法时，字面量 `setattr(obj, "name", fn)` 被
   ruff 拦（B010），直接赋值被 mypy 拦（method-assign）。两处都用 `_stub(obj, name, fn)`（**变量名**
   传字符串）——这是本仓测试替换方法的标准手法。
3. **细粒度任务占用资源池 ⇒ 测试必须声明池**：`{local_rollout, local_ppo, eval_local}` 不声明时
   `PoolSet` 会响亮报「未知资源池」（比静默放行好，但写测试时容易愣一下）。轮粒度任务（`kind=round`）
   不占池。

下一步：R2c-3 余下 ① `ppo` 三相拆分 ② `Supervisor` 进控制台 ③ `checkpointCacheMb` 真机 RSS 表。

## §79 R2c-2：抽出 run_one_round + 任务体↔引擎的桥 + 假件集成测试（2026-09-18）

R2 第三相位的第二半。用户口径（本轮）：**写集成测试验证流程，rollout/ppo/eval 一律用假件**。

### ① `run()` 拆出 `run_one_round(it) -> RoundOutcome`（只搬不改）

原 `run()` 的 190 行轮体搬进 `run_one_round`，控制流逐条保留；差异只在**控制流的出口表达**：

| 原 | 新 |
|---|---|
| `break`（停腿/熔断/止损/门/预算） | `return RoundOutcome(ROUND_STOP, it)` |
| `return`（全离线任务包已导出） | `ROUND_BUNDLE_EXIT` |
| 冒烟作废：`smoke_void = True; break` | `ROUND_SMOKE_STOP` |
| `it -= 1`（三种原地重试） | `ROUND_RETRY` |
| 轮末 `self._consec_fail = 0` | + `ROUND_NEXT` |

`run()` 变成驱动器（预采 join / 预算到点检查 / 按 outcome 施加 / 收官 drain / 停车）；
**`RoundOutcome.it` 必须带回**：半离线整段 `_remote_run_segment` 一次吃掉 it..end_it，
丢掉返回值就会重跑已跑完的那一段。

实现方式：一次性拼接脚本（AGENTS §17.1 的纪律）——先在内存里逐 hunk `assert count == N`，
全过才写盘；写完 `ast.parse` 校验。脚本跑完即删（改动本身在提交里）。

### ② `rl/loop_runner.py`（新）：任务体 ↔ 引擎的**唯一**桥

- `planner`：读该课账本 → `next_it`（账本是 SSOT；队列只是本进程的加速器）；课已收官 ⇒ 空表。
- `run_round`：`RoundOutcome` → 四态（NEXT→DONE / RETRY→`retry(same_iter)` / STOP·SMOKE·
  BUNDLE→DONE(final) 并标记收官 / 引擎异常→RETRY）。
- **`WAIT` 的判据不猜**：只认引擎显式提供的 `remote_job_ready(it)` 钩子。**钩子不存在**
  （今天的 `TrainingLoop`）⇒ 按「跑完即 DONE」处理 = 行为与改造前逐字节一致；钩子存在
  （R2c-3 的轮询化实现，或测试里的假件）⇒ 未就绪就 `WAIT` 让位。
- **粒度诚实记账**：今天一个任务 = **一轮**。轮内 13 步需要的轮内局部量
  （`pairs`/`dist_cfg`/`t_rollout`/`seg`）还锁在 `run_one_round` 里，提成 `RoundContext`
  之后即可细化（R2c-3）。粒度只决定**让位点的密度**，不改本模块与调度器的契约。

### ③ 集成测试：重活全假、记账全真

`nn-training/e2e/test_loop_supervisor_integration.py`（4 例）：

```
真 TrainingLoop（真 args / 真 _setup_common / 真 run_one_round 控制流 / 真 _record_iteration）
真 Supervisor（轮转 + 资源票 + 四态收敛 + 按课隔离）
真 LoopRunner
假：_rollout_phase / _serial_ppo / _join_eval / 预采 / 巡检 / 轮转 / 报告打印 / rl-config 读盘
```

| 用例 | 钉住的东西 |
|---|---|
| 两门课在一个进程里各自跑满 2 轮 | 账本互不串（各自 `[1,2]`）· 前两步来自不同课程（轮转公平，不是「跑完 a 再 b」）· 队列收敛 `done` |
| a 在 it2 等远端 PPO（`WAIT`） | b 先跑完 it1+it2（**让位的证据**）· 在飞集带 `job-a-2` · 时钟推进 + 置就绪后 a 续跑 |
| 进程重开 | 指针从账本重建（`next_it=3`）· **不重写任何已有行、不跳轮** |
| 引擎异常 | 原地重试（RETRY 痕迹）· it 不前跳 · 最终成功落账一次 |

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1373 passed / 3 skipped**（28s；+4 集成例） |
| 根 `bun run check` | ✔ 52s 绿 |

### 观察到的一次门禁偶发（非本轮回归）

`e2e/test_multi_course_single_hub_e2e.py::test_single_hub_dispatches_two_courses_to_one_worker`
在一次全量门禁里红过：hub 子进程报「端口 54749 已被占用——拒绝启动（禁止双监听）」。
同文件单跑绿、紧接的全量门禁再跑绿 ⇒ **端口抢占竞态**（xdist 12 路并行下「先探空闲端口、
再启动」之间的窗口被别人抢走），不是改动引入。已记在此处备查；若再现频繁，修法是让
free-port 助手「绑定即持有」到子进程接手（而不是探完就放）。

### 下一步（R2c-3）

`RoundContext` 提出轮内 13 步（细粒度让位）+ 三处长等待真轮询化
（`wait_job` / eval 尾巴 / 预采子进程各加 `*_ready(it)` 钩子）+ `Supervisor` 进控制台
（单例卡片 + 每课队列视图）+ `checkpointCacheMb` 真机 RSS 实测表。

## §78 R2c-1：单进程多课程调度核心 + 只读计划视图（2026-09-18）

R2 第三相位的第一半。设计稿 `plan/r2-loop-task-queue.md` §4/§8 同步（R2c 拆成 R2c-1/R2c-2）。

### 三个新件

| 件 | 职责 |
|---|---|
| `nn-training/rl/loop_scheduler.py` | **纯调度**（无 torch/网络/IO）：`PoolSet` 资源票 + `CourseQueue` + `Supervisor`（公平轮转 → 闸门 → 单线程执行 → 四态收敛） |
| `nn-training/rl/loop_plan.py` | 唯一碰盘的 IO 边缘：账本指针（`LedgerView`）→ `RoundFacts` → `pending_tasks`；shard 结算数；`commit_journal` 在飞集；课程发现 |
| `nn-training/run_rl_cluster.py` | 入口：**一个进程**读所有并行课程，输出「下一步 / 待办 / 在等谁（含 job_id）/ 被什么挡住 / 关键事实」 |

### 三条不可交易的性质（测试逐条钉住）

1. **同一时刻只跑一个任务**（单线程执行器）——用「执行体重入即计数、峰值必须为 1」证明；
2. **`WAIT` 不占执行权**——一门课等远程 PPO 时，另一门课跑完自己整轮（时钟注入，测试不真睡）；
3. **故障域按课**——一门课 `ABORT`（门禁停腿 / 5 连击）只脏它自己的队列，另一门课照常跑完。

外加：轮转公平（`a,b,c,a,b,c` 而不是「跑完 a 再 b」）、`RoundFacts` 算不出时不跳（保守方向）、
执行体抛异常 = 一次失败（不打崩调度器）、planner 空转 ⇒ 响亮抛错。

### 两处设计上的新东西（原设计稿没写到的）

- **票可跨 `WAIT` 保留**（`waiting(hold=True)` / `TaskResult.hold`）：本机 eval 的局还在后台跑时
  `eval_join` 先返回 WAIT——**票必须留着**，否则另一门课的本机重资源会插进来把机器压爆。
  这是我写「池闸门」测试时发现的：若 WAIT 一律还票，容量 1 的池在单线程调度器里**永远不会挡住任何人**
  （一个任务体跑完就还票），池就失去了意义。真实形态是「后台仍在干活」⇒ 票必须能跨步持有。
- **「能跑的都被池挡住」= 没事可做**：否则调度器会在两门互相挡住的课之间空转（灌爆 traces、
  单线程纯烧 CPU）。被挡住的事实留在 `blocked_courses`（读面可见），一旦有票释放就清空重探。

### 与数据的实测（真课程目录，只读）

```
course                   it state    next task          pending inflight
s-dodge                   1 ready    prepare_iter            13        0
tiny-a                    6 ready    prepare_iter            13        0
    facts: iterations=5 last_verdict=None train_sec=1.2 soft_remediate=0 kl_streak=0 shards(it)=0
```

`s-dodge` 的 `shards(it)=150` 而 `iterations=0` 恰好演示了「采集完成、账本未结算」这一态——
计划视图说「rollout 仍要做」（`games_planned` 未知 ⇒ 不跳），而真执行体的 `completed_pairs`
会把已结算的 150 局剔除，两者不冲突（前者是**保守**，后者是**精确**）。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1369 passed / 3 skipped**（24s；+18 新例） |
| 根 `bun run check` | ✔ 绿 |

### 踩坑

1. **重试计数口径**：`q.attempts.get(tid, task.attempt) + 1` 在**首次**失败时把计数推到 2
   ⇒ 5 连击提前一轮变 4 连击停腿。正确口径 = 「已记录的失败次数」，从 0 起。
2. **测试里 planner 必须有限**：`lambda c, it, q: [Task(...)]`（不带 it 门）会每轮供应任务 ⇒
   调度器永远不 idle，`run_until_idle` 直接撞步数上限。「planner 空转」其实是个**真**故障模式，
   所以调度器侧也留了响亮抛错（而不是静默死循环）。
3. mypy：只读模式的 executor 不能返回 `None`（签名要的是 `TaskResult`）——写成「被调用即 raise」，
   既过类型检查，又把「计划视图退化成真执行」这件事变成响亮错误。

### 下一步（R2c-2）

抽 `run_one_round` + 把任务体接到真 `TrainingLoop`；三处长等待改 `WAIT` 让位（`wait_job` /
eval 尾巴 / 预采子进程）；`Supervisor` 进控制台（单例卡片 + 每课队列视图）；`checkpointCacheMb`
真机 RSS 实测。

## §77 R2b 落地：任务模型 + 在飞集；`loop-state.json` 经落地否决（2026-09-18）

R2 第二相位。设计稿 `plan/r2-loop-task-queue.md` §3/§8/§10 同步更新。

### `rl/loop_tasks.py`（新，纯逻辑：无 torch / 无网络 / 无文件 IO）

| 件 | 内容 |
|---|---|
| `Task` | `task_id = course:it:kind`（**幂等键**；重试只动 `attempt`）· `resource` 由 `RESOURCE_OF` 给出 |
| `TaskResult` | 四态 `DONE` / `WAIT(resume_at)` / `RETRY` / `ABORT` + `is_terminal`（DONE/ABORT 出队）；非法状态当场 `ValueError` |
| `ROUND_TASKS` | 一轮 **13 步**：`prepare_iter` → `hot_reload` → `course_iter` → `precollect_join` → `rollout` → `volume_topup` → `eval_dispatch` → `ppo` → `export_weights` → `eval_join` → `record_iteration` → `gate` → `cleanup` |
| `RESOURCE_OF` | 只有 `rollout`/`volume_topup`/`ppo`/`eval_join` 占池；**等待型/记账型不占**——这才是单进程能服务多课程的机制 |
| `RoundFacts` + `already_done` + `pending_tasks` | 幂等判据只认**盘上事实**；`iteration_recorded` ⇒ 整轮为空（★ 不得重写账本、不得重发 job） |
| `resolve_failure` | 与现主循环逐条一致：冒烟作废原地重试 / 死腿立刻 ABORT（不再 5×30s 空转）/ `attempt≥5` 才停、it 不前跳 |

### 在飞集：复用既有 WAL，不建第二份登记表

`rl/commit_journal.py`（I1 的 `started/finish` WAL）新增：

```
attach(phase, round, jid=..., dispatch=..., ts=...)   # 只补事实，**不改状态机**（pending 语义逐字不变）
inflight()                                            # pending + 它们的 job_id / 派发方式 / 开始时刻
```

`_remote_ppo` 在 `publish_job` 拿到 `jid` 后立刻 attach（`jid` 是 publish 的返回值，`start` 时还没有 ⇒
必须另设 op）；`_commit_journal()` 在每 it 首次创建时把在飞集打进日志：

```
WAL replay-check: 1 个未完成提交 [ppo_remote@37 jid=... via push] —— 本地轮由 ppo_ckpt 续跑、远端轮重发同 it job（幂等）
```

### ★ 设计变更：`loop-state.json` **不建**

初稿计划每课一份状态文件（指针 + 在飞集 + 预算）。落地时逐条对照，发现盘上**已有三份权威来源**
覆盖全部四类信息：

| 要落的信息 | 已存在的权威来源 |
|---|---|
| 指针（`next_it`/`rotate_seed`/阶梯） | 账本（R2a `LedgerView`，已接） |
| 预算与门禁计数 | 账本（R2a，已接） |
| 在飞集（job_id / 等谁回传） | `commit_journal.jsonl`（R2b，已接） |
| eval 尾巴意图 / 预采子进程 | `eval_log.jsonl` summary + `(stage,seed,wver)` shard 对账 |

再写一份 JSON = **第二份真相**，分叉方向恰是最贵的一种（续跑读错指针）。⇒ 不建；唯一允许留在内存的是
调度器唤醒条件（`WAIT.resume_at`、资源池票），它可重算。备选否决也记在案：写它当「加速器」（零独立信息并集）、
把在飞集写进账本（账本是**事件流**，在飞是**状态**，混进去污染所有账本读者）。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1351 passed / 3 skipped**（25s；+14 新例） |
| 根 `bun run check` | ✔ 38s 绿 |

新回归：`tests/test_loop_tasks.py`（10 例：任务表/幂等键/资源池/四态/幂等铁律/失败语义）、
`tests/test_commit_journal.py`（+4 例：attach 不动状态机、孤立 attach 不造在飞、finish 后不可复活、
**硬死后带 job_id 的在飞集**）。

### 踩坑

1. **attach 不能进状态机**：`pending()` 是「每个 (phase,round) 的最后一条 op」——若把 attach 当普通记录写，
   它的 op 会顶掉 `start`，未完成提交立刻变「已完成」。实现上 `_scan()` 明确把 attach 从 last-op 里剔除，
   只并入 meta（并加了用例钉住）。
2. **别把在飞集塞进 iteration 事件的 `start`**：`jid` 只能 publish 之后才知道；在 `start` 里塞空值再靠
   `finish` 补，等于让「未完成的那些」永远看不到 job_id——恰是最需要它的场景。

### 下一步

R2c：单进程 supervisor（每课队列 + 资源池容量 1 + `WAIT` 让位）+ `checkpointCacheMb` 真机实测。

## §76 R2 设计稿 + R2a 落地：门禁与续跑改扫账本（重启不再洗白）（2026-09-18）

用户指令：训练循环任务队列化（「trainingloop 不是请求处理器，是一个有状态的会话」）。本轮交付
**设计稿 + 第一个相位（R2a）**；四个未决问题已由用户定案。

### 设计稿：`plan/r2-loop-task-queue.md`（327 行）

**「有状态会话」的字面证据**：`TrainingLoop.__init__`（`rl/loop_core.py:151`）约 60 个跨轮字段；
`run()` 是 190 行顺序脚本 ⇒ 一步阻塞整条腿阻塞 ⇒「多课程 = 多进程」是形状的必然结果，不是选择。

**状态五分类**（R2 的全部工作量）：

| 类 | 内容 | 归宿 |
|---|---|---|
| A 指针 | `next_it` / `rotate_seed` / 阶梯（= `it` 的纯函数） | 账本（已有） |
| B 门禁指标 | `kl_streak` / `ent_streak` / `stop_loss_streak` / `soft_remediate_count` / 累计量 | **改扫账本** |
| C 在飞集 | `job_id` / 等谁回传 / eval 尾巴 / 预采子进程 | 落**意图**，重启按意图重建（线程句柄不可序列化） |
| D torch 对象 | `_model` / `_opt` / `_ref_model` | 每课内存缓存最新 checkpoint（权重 + Adam） |
| E 轮内瞬态 | `_report` / `_agg` / `_volume_*` | **禁持久化**（否则拿旧数字记新轮） |

**任务模型**：一轮 → 13 个细粒度任务，执行器四态 `DONE / WAIT / RETRY / ABORT`；`WAIT`（等远程
PPO、等 eval 尾巴）**不占执行权**是单进程多课程的关键；每条任务先有**盘上判据**（幂等 guard）——
这同时就是「重放安全」与「扫账本」的同一件事。

**用户定案（2026-09-18）**：① 直接做到 R2c 单进程；② 细粒度步骤 + `WAIT` 让位；
③ 本机 `local_ppo`/`eval_local` 跨课排队、池容量 1；④ checkpoint 缓存上限 = 并行课程上限（默认 5）。

### R2a 落地：`nn-training/rl/train_ledger.py`

`LedgerSpec`（阈值，与 `_breaker` 同源）+ `LedgerView`（单遍 `load_ledger` + `apply_event` 增量）
+ `soft_remediate_count(kinds)`。**不新造账本**：`training_log.jsonl` 已含全部事件，且已有五个扫描器
（`rl/resume.py` × 3、`rl/gate_check.py` × 4）——R2a 只是把它们收敛成一份视图。

接线：

```
loop_core._setup_common   ← load_ledger 一次
    next_it / rotate_seed / ent_peak / train_sec_total / train_samples_total
    kl_streak / ent_streak / stop_loss_streak / soft_remediate_count
events.write_*             → 返回事件 dict
loop_guards._ledger_apply  → 写账本处顺手并入视图（观测失败绝不阻断训练）
events.write_stop_loss     → 止损连击的**状态转移**落账（命中 / 从 >0 回落）
```

**刻意不继承**：`_consec_fail`（重试连击是单腿内的进程护栏，继承会「重启即秒死」）、
`_zero_shard_streak`（口径依赖 `_node_rollout`，而 `iteration` 行没有 `rollout_src` ⇒ 会对节点轮报假事故，
R2b 加字段后再接）。

### 这一相位修掉的真 bug

**I2「提示类门 REMEDIATE ×N 即停腿」原先只在内存计数 ⇒ 重启即洗白。** 边际收益枯竭的 N 次确认
可以从头再来（`c6-pickup3` 6 次 / `c6-bonus` 10 次 cloud halt 那类事故的判据）。现在读**整条账本**
（换新 traj = 新纪元，重新计数）；同类问题的 F4 连击与止损连击一并对齐（止损原先在账本里**毫无痕迹**
——命中只打日志，所以新增 `stop_loss` 事件）。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate（ruff + mypy + pytest） | ✔ **1338 passed / 3 skipped**（26s；+18 新例） |
| 根 `bun run check` | ✔ 21s 绿 |

新回归：`tests/test_train_ledger.py`（15 例：与五个旧扫描器**奇偶断言**、增量==单遍、
独立复算连击、坏行/未知事件透明、提示类计数口径）、`tests/test_train_ledger_wiring.py`
（3 例：`_setup_common` 继承、继承计数当轮停腿、空账本从零起）。

### 踩坑（本轮）

1. **`_ent_peak` 的赋值位置**：原代码在 `if start_it > 1:` 分支里才从账本回读 ⇒ 视图化后必须
   提到**无条件**赋值，否则「空账本但 view 有值」的组合说不清（本轮统一为「视图即真相」）。
2. **`NO_CLOUD_HALT_KINDS` 是 `TrainingGuards` 的类属性**（不是模块级名）——从 `rl.loop_guards`
   直接 import 会 ImportError，用 `TrainingGuards.NO_CLOUD_HALT_KINDS`。
3. **mypy 需要显式声明 mixin 属性**：`TrainingSteps` 里调 `self._ledger_apply` 必须在类体声明
   `_ledger_apply: Any`（与既有 `_jsonl_path: Any` 同法），否则 `attr-defined` 报错。
4. **测试期望值别跟着实现写**：agg 缺失轮（`kl=None`）在 `_breaker` 门控下**既不续也不断**连击
   ——我第一版测试写成「断」（期望 1），实测 2 才是对的；已把这条语义写进断言注释。

### 下一步

R2b：`rl/loop_tasks.py`（任务 schema + 四态执行器）+ 每课 `loop-state.json`；随后 R2c 单进程
supervisor（资源池容量 1）+ R2d 控制台单例卡片/队列视图 + R2e e2e。

## §75 多课程单 hub 端到端 + 回环 HTTP 绕开代理（P1 余下 ②）（2026-09-18）

P1 收口：`e2e/test_multi_course_single_hub_e2e.py` 把「一个 hub 进程服务所有并行课程」钉在
**真跨进程**链路上——真 hub 子进程（`--discover --push`，命令行不点课程名）+ 真
`worker_server`（starter 换成写结果的假执行器 ⇒ 不跑 rollout/PPO/torch）+ 训练侧真发布
（`publish_job(dispatch="push")`）与真等待（`wait_job` HTTP 轮询）。

```
训练侧 publish_job(dispatch="push")  →  <traj>/<课>/remote-jobs/<jid>/
hub（真进程，发现课程表）            →  push_client.submit_job（code.zip 随体）
worker_server（真 HTTP 契约）        →  GET /job/<jid>/result
hub accept_result（对账→租约→首写）  →  本课 result/result.json
训练侧 wait_job（控制台读同一份账本）
```

两个用例：① 两门课各发一份 job → 都完成、结果**各回本科目录**、账本不串课、worker
各收一次、派发器 `pushed=2/requeued=0`；② 离线课（`POST /admin/courses?mode=offline` 真热切）
不实时派发（job 停在队首、worker 没碰），同 hub 的在线课照常走完，切回在线后**同一份 job**
（不重发）立刻被推走。

### 实测踩到的四个口径（写进用例注释）

1. **就绪 ≠ 端口能答**：课程表是后台扫描登记的（`DISCOVER_SCAN_MIN_SEC=2s`），端口刚答
   200 时课程表还是空的——只等端口就会假红（首版就这么红的）；等的是「`/admin/queue`
   能答 **且**课程表就位」。
2. **结果落的是 `result/result.json`**（不是 job 根下的 `result.json`），且可领池看的是
   **盘上有没有结果**，不是租约——成功后租约要到 TTL 才消失，所以断言是「无可领的活 +
   派发器自己手上没有在飞」，而不是 `inflight == []`。
3. **`job_completed` 是训练侧写的**（验收落位后 `mark_job_completed`），hub 只写
   `job_pending` + 落结果；用例显式走一次 `mark_job_completed` 并断言「只进本科账本」。
4. `--race off`：竞速广播会把同一份活推给两台，本用例要的是 1:1 派发。

### 顺带修掉的门禁真缺陷：**回环 HTTP 被环境代理截走**

`test_offline_deliver.py::test_offline_endpoints_require_auth` 在门禁里红过一次：hub 日志
明明两次 401，测试侧读到的却是 **502**。根因不在测试——本机用户级环境带
`HTTP_PROXY`/`HTTPS_PROXY`，而 `no_proxy` 写的是 `127.*` 这种通配，Python 的
`proxy_bypass()` **不认**（只认 `host == entry` / `*.suffix` / `.suffix`），实测
`proxy_bypass("127.0.0.1") is False` ⇒ **每一发去 127.0.0.1 的请求都被送到外部代理再转
回来**（代理抖动/回错误页 = 502），本机训练也凭空多一跳。

修法：新增 `remote/net_http.py`（`is_loopback` / `no_proxy_opener` / `urlopen` 替身），
把四条本机 HTTP 出口接上——`hub_client._request`（训练侧↔hub）、`push_dispatch._http`
（hub↔GPU worker）、`worker._request`（worker↔hub，非回环仍用它的显式 ProxyHandler，
Colab 需求不受影响）、`offline_deliver._urllib_opener`；非回环分支刻意仍调
`urllib.request.urlopen`（保住测试里那条 monkeypatch 缝）。

复现→修复（§7）：`tests/test_loopback_http_no_proxy.py`（5 例）——把环境代理指到**死端口**
再打本机真服务，修复前三条出口全部 `ConnectionRefused`（我用临时脚本实测过：raw urllib
URLError、`net_http.urlopen` 200），修复后全绿；另有一例断言非回环仍走 urllib 默认。

只改生产侧还不够：**测试侧另加一层兜底**——`tests/conftest.py` 把精确回环主名
（`127.0.0.1` / `localhost` / `::1`）补进 `no_proxy`（`proxy_bypass()` 认精确匹配），8 个仍用
**裸 `urlopen`** 打本机临时端口的既有用例因此一并脱离代理（实测证据：只改生产侧时
`test_multi_course_hub.py::test_main_discover_picks_up_course_from_disk` 在满载下仍会
在 `/jobs/next` 那一步吃到代理的 `Errno 111`，而 hub 自己毫发无损）。生产靠代码、测试靠环境。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate | ✔ **1320 passed / 3 skipped**（+2 e2e +6 回环用例；ruff + mypy 干净）——连跑两次均绿 |
| 根 `bun run check` | ✔ 19s 绿 |
| dashboard typecheck / test | ✔ 15s 绿（本轮未动 dashboard） |

**P1 到此收口**；余下的「组件卡片按『单例角色 / 按课程』分组」仍按 §74 的说明推迟（属形状
整理，不阻塞任何链路）。

## §74 单 hub + 单隧道（P1 余下 ①）：hub/cloudflared 收敛为单实例 + 课程表从盘上发现（2026-09-18）

用户口径（原话）：`hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程`。
本轮做 **hub + 隧道**（trainingLoop 是有状态会话，收敛要等 P2 的任务队列改造，见 DECISIONS 条目的「备选与否决」）。

### 形状：一个进程，一份地址，课程表来自磁盘

```
训练侧（每课一个会话）        共享实例（一个进程）
tmp/<课A>/remote-jobs  ─┐
tmp/<课B>/remote-jobs  ─┼─→ hub-server :<rl.hub_port>  --discover --traj-root <repo>/tmp
tmp/<课C>/remote-jobs  ─┘        ├─ 扫 <traj>/<课>/{remote-jobs,offline}（新鲜 1h 窗口）自动登记
                                 ├─ 每课一条 FIFO + 跨课程轮转（既有）
                                 └─ cloudflared（单隧道 → 同一个 hub 端口）
```

- **课程表不做注册**：训练侧把 job 发布到盘上就是「这门课在跑」的事实。扫描两处触发——`claim_next()` 前置（2s 最小间隔闸 ⇒ 新课程**下一次轮询**就能被领到）+ 后台 5s 节拍（push 模式/无 worker 时兜底）。登记后不撤销（课程暂停时在飞 job 的结果回传不能 404）。
- **新鲜窗口 1h**：陈旧实验目录的磁盘形状与残留 pending job 完全一样，误登记 = 把死课程的活派给真 GPU worker。
- **统计面唯一**：`scopeOf(key, course)` 是共享槽（`''`）的唯一归一入口；`sharedHubPort/sharedHubUrl/sharedTunnelMetricsPort` 是唯一地址来源（grep 门禁守 `slotPort(…, 'hub')` 零调用面）。
- **换代接管**：共享实例启动前把旧形状（per-course）条目活则杀、死则清账；per-course 条目**拒重建**（fail-closed），否则等于凭空再造一个 hub 读同一棵树。
- **单实例切换要搬状态**：`_adopt_solo()` 把 halt / race / worker 登记 / 鉴权计数从那份 `_JobStore` 搬到队列自己身上。
- **停机达令按课程下发**（连带必修）：单 hub 之前「一个 hub 一份 halt 布尔」≈ 按课程；单进程后那个布尔升格为**进程级**，A 课门禁 ABORT 会连坐停掉 B 课云机（表现为「云机莫名停机」，属最贵的静默故障）。故 halt/resume/status 一律支持 `?course=`（空 = 全课程），训练侧（门禁 / 启动清停机态）与控制台各自带课名。

### 验收

| 面 | 结果 |
|---|---|
| nn python gate | ✔ 26s 绿（ruff / mypy / 全量 pytest **1312 passed / 3 skipped**，含 `test_multi_course_hub.py` 24 例，其中 1 例是**真进程** `--discover` 主流程） |
| dashboard typecheck / test | ✔ 542 pass / 0 fail（`single-hub-tunnel.test.ts` 新 7 例；`cloud-halt.test.ts` +2 例按课程达令） |
| dashboard build:ui | ✔ 三份 bundle（app 61.8KB gzip / log / eval） |
| 根 `bun run check` | ✔ 32s 绿 |

### 踩到的坑（都留了回归）

1. **`tmp/*/remote-jobs` 写进了 TS 块注释** ⇒ `*/` 提前闭合注释，tsc 在下一行开始报 `Module declaration names…` 一类怪错。TS 注释里写通配路径要避开 `*/`。
2. **`withScratch(async () => …)` 不 await 回调** ⇒ `finally` 在第一个 await 处就恢复环境变量，异步体后半段跑去读**线上** `registry.json`，而那份里正好躺着历史遗留的 `hubServers[''] = {pid: 1}`（exit-watchdog 事故污染）——测试看起来「读到 pid 1」其实是读错文件。修法：测试夹具必须 `await fn(...)`。
3. **`_pump` 的 8s 上限在满载下会偶发红**：门禁拿 xdist -n 12 跑，假 worker 的 0.5s 探测超时在满载时会把**健康** worker 误判为「没答」⇒ 派发器把它当忙 ⇒ 没有机器能接活（`test_worker_refusing_job_requeues_to_another` 实测红过一次）。修法：假 HTTP 探测超时提到 2s + `_pump` 上限提到 20s（纯测试夹具余量）。
4. **子进程还活着时读它的 stdout 会阻塞**：`assert st == 200, f"{proc.stdout.read()}"` 一旦被改成**先读后断言**（不在失败分支里惰性求值），就变成等 EOF → 整个用例挂到 60s 超时（mypy 还顺手报 `IO[Any] | None`）。修法：`_startup_output(proc)` 只在失败时调用、且进程未退出时直接返回空串。
5. **`# noqa: BLE001` 在这个 ruff 配置下是 RUF100（未启用该码）**——写了反而红，异常兜底的注释直接当普通注释写。
6. **`toEqual` 比 ProcSpec 会因闭包函数不等而假红**（`healthy`/`ownsResource` 每次构造都是新引用）⇒ 逐可比字段断言（沿用 W4 的写法）。

## §73 控制台 worker 登记入口 + 面板重组（P1 余下）：课程 select 解放 / 在训课程全高亮 / 并行总览（2026-09-18）

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

## §72 hub 中介 push 派发（P1 余下）：队列顺序推空闲 worker + 周期探活 + 超时回落换人（2026-09-18）

用户指令：hub 中介的 push 派发 + worker 登记入口 + 周期 ping 探活，训练侧 push 改走 hub。
抉择与代价全文见 `DECISIONS.md §2026-09-18-goalnn-hub-push-dispatch`，这里只记落地与实测。

**形状：派发权搬到 hub，训练侧只留一句话。** 训练侧发布时写 `manifest.dispatch="push"`
（= `--remote-transport hubpush`，或 auto + `courses.<课>.hub_push` / `rl.hub_push`），
之后回 `wait_job` —— 与 pull 完全同一条收尾链（三重校验 → 落位 → 记账），**不新增第二条
客户端**。hub 侧新模块 `remote/push_dispatch.py` 认领这份活自己推：

- `PushWorkers`（登记表）：读 rl-config `nodes[]` 里 `gpu_push` 的条目（控制台的 worker
  登记入口回写它们），**mtime 热重载**（写完配置不必重启 hub）；周期 `GET /ping` 更新
  在线/忙闲/排队；运行时增删走 `POST /admin/push-workers`（volatile，**且重载后仍保留**）。
- `PushDispatcher`（派发拍）：跨课程轮转 + **每课程至多一份在途**（课程内轮次有硬序）+
  只推**队首**且队首必须是 push job（不越过它推后面的）+ 四道闸（在线/不忙/在飞<并发/
  不在本次避让名单）；上传与等待在**每份 job 一条线程**里做（几十 MB 的 POST 不阻塞拍）。
- 回落：超时（缺省 45min 兜底）/ 连续探活失败（3 次）/ 拒收（409/428）/ 结果入账被拒
  ⇒ 放租约 + 避开那台 + 立刻换人重推（job 停在该课队首）。
- 结果入账与云机 POST **共用** `accept_result`（对账 → 租约 → 首写锁定）；`_post_result`
  改为调用它 —— 两条腿不可能一条校验一条不校验。

**探活的两条硬判定**（都会在回归里咬）：

1. **从没答过 = 不在线**：宁可这一拍不推，也不往一台可能是死的机器上推几十 MB。
2. **失败但未达阈值 ⇒ 当局忙**：状态未知必须当忙，否则一次隧道抖动就够调度器认为它空闲
   并二次推送同一门课（两份 PPO 抢同一轮）。

**缺省关**：`--push` 才启用（不打开连探活线程都不起），既有单课程用例与线上行为逐字节不变；
控制台经 `rl.hub_push` 透传，`--push-config` 显式指向仓库那份 rl-config（指到 per-course
目录 = 登记表恒空，是最难查的一种配错）。

**回归**（14 例，全用假 GPU worker server 的三件套 `/ping` `/job` `/job/{id}/result`，
不跑任何真实运算、不 spawn bun）：

- 纯函数：登记判据与训练侧 `_gpu_push_nodes` 同尺子（`gpu_push` + enabled 缺省 true + url）；
  四道闸 + 注册序稳定挑选。
- 登记表：mtime 热重载、非 push 节点不进表、探活写回在线/忙闲/计数、运行时增删。
- 全链路：happy path（推 → 结果落盘 → **真 hub 端点** `/jobs/{id}/status|result` 读得到 →
  wire 账 `payload_bytes/body_bytes` 实测）；忙的 worker 被跳过；**离线课一份都不推**；
  失联 → 回落队首换另一台（并断言跑死它的那台**不得**再拿同一份）；超时兜底；worker 拒收
  （409）→ 换台；**被篡改的结果**（data_fp 漂）→ 拒收 + 不落盘 + 换台重跑；队首是 pull 活
  → 一份都不推；没有空闲 worker → job 留队首、无租约残留；**训练侧真发布**
  （`publish_job(dispatch="push")`）走完同一条链；`/admin/push-workers` 读/增/删 + 未启用 409。
- 传输裁决（`test_remote_transport.py` +5 例）：`hubpush` 无条件走（缺 hub/token 响亮拒），
  `push`/`pull` 压过配置，auto 只在 opt-in + hub 齐备时切（旧部署一行不改）。

**门禁**：nn python gate **1301 passed / 3 skipped**（ruff + mypy 干净）、根 `bun run check`
（1851+ pass）、dashboard typecheck / test（309 例）/ `build:ui` 三份 bundle 全绿。

**本轮未做（P1 余下）**：控制台「worker 登记入口」UI 与面板重组（课程 select 自由可切 /
在训课程高亮 / 队列与 push 总览）、单隧道（hub/cloudflared 收敛为单例）、多课程单 hub 的
端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

## §71 多课程单 hub（P1）：一个进程托管 N 份账本 + 跨课程轮转 + 离线课 + 分课程权重桶（2026-09-18）

用户指令：多课程并行训练流程与操作重组（上限先设 5；hubserver/trainingloop/selfNode/
cloudflared 各只需一个进程；hub 设 PPO 任务队列；离线课不实时派发但收回传；rollout 集群
按课程缓存最近权重；课程数 < worker 数时用单课程竞速；面板重组；e2e）。抉择与代价全文
见 `DECISIONS.md §2026-09-18-goalnn-multi-course-single-hub`，这里只记落地与实测。

**形状：一个进程托管 N 份 `_JobStore`，磁盘契约逐字节不变。** 多课程不是换一套目录约定，
而是「同一进程里多挂几份账本」（仍是 `tmp/<course>/remote-jobs` + `training_log.jsonl`）。
这条取舍换来的是：既有 100 个单课程 hub 用例、`tmp/<course>` 约定、诊断工具全部照旧，
回滚只需改启动参数。

- `_HubQueue`（调度面）：路由（job_id → 课程，扫 job 目录一次并缓存）+ 每课程 FIFO +
  **跨课程轮转**（`rotation_order` 从上次派发的下一门开始）+ 离线课不参与 + 观测面。
- `claim_next(worker_id, race)`：挑活的唯一入口。超时回收时记下「谁跑死的」，在**还有
  别的活跃 worker** 时避开那位前持有人（用户口径「回落队首并改为推送其它 worker」）；
  独苗时允许自领（否则那台 worker 永远空转）。
- 竞速口径：`race_decision(..., active_courses=N)` = 「窗口内不同 worker 数 > 在派发课程数」。
  **缺省 `active_courses=1` 时与旧口径逐字节等价**（`< 2` ⟺ `<= 1`）⇒ 旧用例一行未改。
- 鉴权面提取 `_AuthGuard`（进程级一份，不按课程各算 ⇒ 封禁阈值不会变成 5×N）；单课程队列
  **借**那一份 store 的鉴权/竞速/停机状态。
- 权重桶按 `(course, kind)`（`tools/agent/weight-buckets.ts`，纯逻辑出列）+ 上传前预检
  `GET /v1/weights?sha=&kind=&course=`（命中连体都不传）。课程身份走进程级 `RL_COURSE_NAME`
  （在 `apply_course` 挂——训练进程唯一知道课程名的地方）。

**实测踩到的两个真缺陷（都有回归）**：

1. **「找不到归属」不能写成空串**：单课程队列（与旧单课程 hub）的课程名**就是空串**
   （`tmp/nocourse` 那套约定）。`course_of` 用空串兼作缺失值 ⇒ 单课程下 `/jobs/next` 刚派
   出的 job 立刻解析不到归属、handler 打到哨兵路径上 **500**（每一次拉活都失败）；
   补传路径同款 ⇒ 单课程每一次补传 **400**。两处都改成 `None` 表缺失。
2. **避让的判定时序**：在队列层「先读 stale 记录再比身份」恒为空——那一刻过期租约还没
   被回收，stale 记录还没写。修正为「闸在队列层（`may_avoid_stale_holder`：有身份 +
   还有替班），身份比对在 store 内」（那里才是回收之后的最新状态）。第一版就是这么写的，
   回归测试当场抓出来。

**另一条实测口径**：避让的闸看的是「窗口内活跃 worker 数」（180s），而租约 TTL 是 300s
——一个 worker 停 poll 超窗口即判离场。测试里必须按真实节奏刷 `note_worker`（活着的
worker 每几秒就打一次 `/jobs/next`），否则两台都被当离场 ⇒ 活跃数 0 ⇒ 避让不开（这是
**对**的行为：连一台活的都没有时，避让只会让这活没人干）。

**门禁**：nn python gate **1270 passed / 3 skipped**（+29 例：`test_multi_course_hub.py` 17 +
`test_weight_course_buckets.py` 12）；根套件（含 `tests/agent/weight-buckets.test.ts` 13 例）
全绿；ruff + mypy 干净。

**本轮未做（P1 余下）**：hub 中介的 push 派发（hub 主动推给空闲 worker + worker 登记入口 +
周期 ping 探活）与训练侧 push 改走 hub；单隧道（随单 hub 自然成立）；dashboard 重组；多课程
单 hub 的 e2e。**P2（用户已拍板）**：训练循环**迭代任务化**（一轮 = 一个任务、执行器不跨轮
持状态、状态全在磁盘），验收 = 「断开续跑 == 连续跑，逐字节等价」；允许牺牲 T4 预采、允许
本机降级改每轮载入、eval 尾巴改盘上轮询。

## §68 x2-rebirth 结课：承伤 106.4→33.9（−68%），终点取 it15 而非末 it；「有效训练量只有前几轮」（2026-09-18）

**一句话**：a2（1 敌关满分）的权重**热启**到 ladder-c02（2 敌 / 6 关），25 轮后停腿。
承伤 **106.4 → 33.9（−68%，配对 CI 不重叠 = 显著）**，零伤通关 37.0%→77.5%，
pass 81.0%→95.0%；通关耗时 928.7→1002.1（**+7.9%，CI 大幅重叠 = 无显著变化**）。
**迁移链成立，判决 = 达成**（承伤主端点达标 + 耗时约束 ≤+10% 守住 + 无掉队关；单 run）。

### 判据走势（锚点半区 300 局，seed0 860001；bootstrap 95% CI）

| it | dmg/局 [95%CI] | 通关 ticks [CI] | pass | 零伤通关 |
|---|---|---|---|---|
| 0 | 106.4 [ 97.2,115.8] | 928.7 [885, 971] | 81.0% | 37.0% |
| 5 | **33.3 [ 26.7, 40.7]** | 909.0 [868, 951] | 92.3% | **80.9%** |
| 10 | 37.7 [ 30.2, 45.5] | 963.0 [923,1006] | 93.7% | 77.2% |
| **15** | **33.9 [ 27.2, 41.7]** | 1002.1 [959,1045] | **95.0%** | 77.5% |
| 20 | 46.1 [ 38.2, 54.9] | 972.0 [930,1012] | 96.0% | 70.5% |
| 25 | 41.6 [ 34.2, 49.7] | 1000.4 [957,1042] | 96.0% | 71.5% |

### 终点 = it15（同种子配对，n=300）

- **it5 vs it15**：承伤 Δ=+0.6（配对 SE 3.9；**变差 41 局 / 变好 41 局，完全对称**）
  ⇒ 承伤不可区分；pass 92.3%→95.0%（b01=10/b10=2，pd 4.00%，McNemar 双侧 **p=0.039**）。
- **it15 vs it25**：承伤 Δ=+7.7（SE 3.6 ⇒ **z≈2.1，it25 显著更差**）；pass +1pp 不显著
  ⇒ it25 是拿承伤换 1pp 通过率。
- **掉队探测**：it5 的 bc 关 **64% < it0 的 74%**（唯一相对 it0 退步的关）⇒ it5 出局；
  it15 六关全部 ≥ it0 ⇒ 无掉队。
- 已另存 `nn-training/weights/x2-rebirth-best/x2-rebirth.it15.best.json`（防 `keep_iters` 清理）。

### 三条纪律（后腿必须继承）

1. **连续量结论必须带 bootstrap CI**。我曾按点估计判「耗时反向 +9%」，加 CI 后
   **完全在噪声内、结论不成立**。⇒ 不带 CI 的连续量结论不进结算节。
2. **终点 ≠ 最后一个 it**。承伤在 **it5 就见底**，后 20 轮零改善、it20/it25 还回退
   并往「更快但更脏」漂 ⇒ **每个 eval 点记承伤，最优 it 立刻另存 `<课>-best/`**。
3. **横盘宽限只给一次、上限 +10 轮**（本条即该先例的出处）：x2 在 **it20** 触发横盘条款，
   用户裁定宽限到 **it25/it30** 再看一次；宽限后 it25 承伤仍显著更差（z≈2.1），
   遂停腿取 it15。**不得二次宽限** —— 否则退化成 x3-start 那种「再等等」跑到 333 轮（§55）。

### 其它结论

- **power 盲区没修**：ac 关 pass 30%→96%，但 `firstHitKind` 仍是 100% basic
  （**先打 power = 0.0%**）⇒ 靠「别的能力」赢的，不是靠先打 power。
- 机器：KL 峰值 **0.0142**（a2 全程 mean 0.0059）⇒ 位移是 a2 的 1.5–2.4 倍，
  再次印证 a2 的「位移是杠杆」归因（§56）。
- **有效训练量只有前 5 轮**（≈12 分钟），后 20 轮（≈50 分钟）是浪费 ⇒ 后继课程据此
  把 `iters` 砍到 40、`eval_every` 改 3（见 `curricula/x3-rebirth.jsonc` L10）。

### seed 台账（池外段占用，L3 纪律：改段 = 换判决条件）

`grep -rn "seed0 40xxxx" nn-training/curricula/*.jsonc docs/*.md`（2026-09-18 实测）：

| 段 | 占用者 |
|---|---|
| 400000–400199 | x3-power（含冻结基线）+ x3-chip-k0/k05/k10 |
| 400200–400399 | 已用（x3-credit-p6 文件头注明） |
| 400600–400799 | x3-credit-p6 / -r2 |
| **400800–400999** | **空闲** ⇒ `x3-rebirth` 预注册此段，中途不得改 |

---
## §69 本机 hub 地址走凭据（`HUB_IP`）：两个 notebook 同键同口径 + 一条对账守卫（2026-09-17）

**用户指令**：`battle.tailscale.ipynb`，把本机 ip 也设置为 secret，避免每次都要手动输入。

**动机**：`CFG["hub_url"]` 是「**每个会话都要手填、值却长期不变**」的值——hub 跑在操作者本机，
Tailscale IP 在设备重注册前是稳定的。这正是 secret 的用途，而它此前是 CFG 里唯一还需要人肉
改的条目；模板值 `http://<本地TS_IP>:8787` 忘了改就会拿一个带尖括号的主机名去连
（错在 DNS 层，比当场点名难查得多）。

**一个键、两种取值**（不必记两套约定）：

```
HUB_IP = 100.64.0.5                     → http://100.64.0.5:8787（配 CFG hub_port，缺省 8787）
HUB_IP = 100.64.0.5:9999                → http://100.64.0.5:9999
HUB_IP = http://hub.tailnet.ts.net:8787 → 原样（换域名/协议/端口都行，同机 127.0.0.1 同）
CFG hub_url 含 "<" → 视为未填（返空串，由调用方响亮失败并点名该填哪个键）
```

`HUB_IP` 有值时**压过** CFG `hub_url`；未设时按 CFG 手填——老会话（没建这个 Secret）行为不变。

**单源一份，两个 notebook 共用**：解析逻辑住在 `remote/tailscale_boot.py::resolve_hub_url`，
消费点是 `notebook_boot.run()`（真跑）与 `diagnose()`（体检）——两个 notebook 都从 GitHub raw
拉这**同一个文件**（体检那边只拉它一个），所以口径不会两边漂。

**与 §2026-09-17-kaggle-cred-before-proxy 同一条时序约束**：`HUB_IP` 与另三个凭据**同批在
引导之前**读。它同样只能公网取（平台 Secrets），引导后 userspace 代理只转发 Tailscale IP ⇒
读出来会是空串；而若它被读成空，症状是「连不上 hub」——与真正的原因（地址没读到）看起来
完全不像，是最费排障时间的一类假象。

**notebook 侧改动**：

| 位置 | 改动 |
|---|---|
| 训练 cell CFG | 新增 `hub_ip` / `hub_port` 两条；`hub_url` 降为「手填兜底」 |
| 训练 cell 内联回退（远端模块拉不到时唯一的活路） | 复刻同一段解析 + **打日志记来源**（`hub = … （来源：HUB_IP / CFG hub_url）`），失败信息同时点名 `HUB_IP` 与 `hub_url` |
| 体检 cell | 自己读 `HUB_IP` 并把 `hub_ip`/`hub_port` 喂进 `diagnose()` |
| 两 cell 的说明（markdown） | 凭据清单补 `HUB_IP`；pull 行不再指向一个要手改的 CFG 值 |

体检 cell 必须同口径，否则会出现「**体检说连不上、真跑却连得上**」这种最难信的一种诊断结论；
`diagnose` 里那句「未配 hub_url，跳过」也改成「未配地址（HUB_IP 凭据 / CFG hub_url）」——
体检报告里的跳过一次不该让人再去猜该填哪个键。

**回归守卫**：新增 `nn-training/tests/test_notebook_hub_ip.py`（**14 例**）。notebook 不 import
仓库代码，所以按 `test_tpu_probe_notebook.py` 的做法把 cell 文本抠出来**独立执行**——
但不止「有这几行」，而是把内联回退算出的 `_hub` 与 `resolve_hub_url` 的返回值**逐例对账**
（8 例取值 + 2 例未填→`SystemExit` 且点名两个键 + 缺省端口跟随 `HUB_DEFAULT_PORT`），
再钉两个 cell 的读取位置早于代理引导、体检 cell 把 `hub_ip`/`hub_port` 喂进 `diagnose`。
先红后绿验过：把 cell 里的缺省端口字面量 `8787` 改成 `9999`，对账断言当场红。

**存量测试立即咬到新凭据**（这是好信号）：`test_bootstrap_proxy.py` 的「凭据前置」用例
（假 secret 只认三个旧键）在新凭据加入后立刻 `KeyError: 'HUB_IP'` —— 它断言的正是
「任何凭据在 tailnet 代理生效后被读即红」，说明这道闸门对**新增凭据**也是自动生效的。
已扩为四键并顺手加固两处：`calls` 断言四个键的读取顺序；`captured["hub"]` 断言
HUB_IP（100.64.0.5）**压过** CFG hub_url（故意留成不同的 100.64.0.9，好让「压过」可观测）；
另加一例「HUB_IP 未设 ⇒ 回落 CFG hub_url」。

**门禁**：nn python gate **1238 passed / 3 skipped**（+15 例；ruff + mypy 干净）；
根 `bun run check` **1851 pass / 0 fail**。

---
## §70 「云端同机 rollout + PPO」全链路集成测试：一条真链路，两个面板读法（2026-09-17）

**用户指令**：写一个集成测试，确保「云端同机 rollout + PPO」全流程畅通，本地 dashboard
能正常读出云端回传的**每 it 权重与指标**；rollout/PPO 用假节点，不跑实际运算。

新增 `nn-training/e2e/test_cloud_iter_e2e.py`（单用例，**~2.0s**，进门禁 e2e 层；见文末"墙钟"）：

```
训练侧 TrainingLoop._remote_iter(it)     真 build_iter_spec + 真 publish_job（磁盘 IPC）
  → 真 hub-server（127.0.0.1 临时端口，真租约/真账本；单 worker ⇒ race=false 独占）
  → 假云机（本测试线程）：真 /jobs/next 领取 → 真下载 payload/code/ts_code（逐 sha 对账）
      假 rollout（按 argv 逐局写 w{i}/rl_sX_seedY + _rl_report.json，**不 spawn bun**）
      → 真 verify_shards（实产集 == 声明集）+ 真 combine_reports（聚合口径不假）
      假 PPO（换一份权重 + opt tar + agg，**不碰 torch**）
      → 真 validate_result + 真 pack_result_v2（v2 线格式）→ POST result
  → 训练侧 真 wait_job → 真 verify_and_land（三重校验 + 权重原子落位 + opt 解包）
  → 真 _export_weights（归档）→ 真 _record_iteration（iteration 账本）
```

**断言面 = 面板的三个真读者**（python 侧独立重实现其读法，并把口径钉在面板源码的字面量
上——双端锚，同 `tests/nn/schema-fingerprint.test.ts` 的思路）：

| 面板面 | 源码 | 本测试断言 |
|---|---|---|
| 每 it 权重 | `eval-board/ckpts.ts::iterFromCkpt`（`/\.it(\d+)\./`）+ `buildEvalCkptsView` | 归档名解出 iter==7、归档/活动指针字节 == **云端回传**的那份（≠ 本地 init） |
| 每 it 指标 | `server/iters.ts::readIterMetrics` | iteration 行字段**存在且非空** + 逐值对账（winRate 0.5 / score_mean 0.1 / dim_means.kills 0.5 / rollout_sec=节点自报 / ppo_cloud_sec=云真训练秒 / wire.rollout_src==node；M0 计量：hub 实测 `up_bytes`==节点 payload 字节、`down_bytes`==result 体字节、`wire.worker.payload_bytes` 同值） |
| job 账本 | `api/ppo-queue.ts::detectPpoQueueStall` | `job_pending` → `job_completed` 有序、`result/result.json` 与 `claimed` 标记齐、无 `fail.json` |

**为什么单独断言「字段存在」**：`readIterMetrics` 用 `Number(r.x ?? 0)`——写入侧漏一个字段
会被**静默读成 0**（曲线掉零、表格 0% 而不报错）。这是读者**不会**替我们发现的回归，
只有存在性断言能抓。

**失败卫生**：假云机任何异常都 (a) 记进 `node.errors`（主线程先断言它，根因不被超时掩埋）、
(b) `POST /jobs/{id}/fail` —— 真链路上这条让训练侧 `wait_job` 立即带原因收兵，测试红了也
是秒级；训练侧另跑在独立线程 + 45s 限时监督（不会陪 `wait_job` 等满 30min）。

**边界（其余面由既有测试承担）**：push 直推云机 → `e2e/test_push_mode_integration.py`；
真 bun rollout → `tests/test_remote_iter_real_bun.py`（本机无 bun/权重时 skip）；
竞速输家叫停仍是后续项（§67）。

**墙钟（首版 6.8s → 现 ~2.0s）**：掐表后最大的那块不是算，是**等**——`wait_job` 的
`poll_sec` 生产默认 **5s**（为隧道抖动设计的节奏），而假云机在 publish 后几百 ms 就把结果
POST 上来了 ⇒ 训练侧仍要等满下一个轮询窗。实测分解：**1.0s** 准备/打包/发布（code.zip 238
文件 + ts_code.zip 340 文件 ~1.4MB + tar.xz payload）+ **5.0s** 轮询窗 + 收尾。
⇒ 加 `fast_wait_poll` fixture：**只**把 `poll_sec` 调成 0.05（`wait_job` 本体全真跑：404 续等、
410 带原因收兵、5xx 退避、超时前 `/status` 二次确认）；真 CLI 解析器 import 提到模块级。
现测 1.99 / 2.00 / 2.08s，占空降到 ~2s（剩下的都是真工作：两包打包 + 真落位）。

**门禁**：nn python gate **1223 passed / 3 skipped**（+1 例）；根 `bun run check` 1851 pass / 0 fail。

## §67 单课程多卡 → 竞速广播：最新 job 派给每个 worker，先回传者胜（2026-09-17）

**用户指令**：只有一个课程在训练、而同时有多个云机 GPU worker 时进入竞速模式——给每个
worker 都分派**最新的** it 任务，用先返回的结果，后返回的直接丢弃。

**为什么终于可以这么做**：§343（2026-09-06）的竞速广播就是这套语义，但 §2026-09-12 P3b
以「多课程并行时同 job 被重复算、慢者 409 白烧」为由 supersede 回独占加超时。用户的
前提（单一课程 + 多卡）恰好把 P3b 的顾虑消掉——那些卡本来就在同一个 hub 上空转。
因此本次是**定向重开**：判据不是“现在跑了几门课”这种没人能可靠回答的问题，而是
**worker 自己报的事实**。

```
每个 worker 在 GET /jobs/next 上自报两个头：
  X-Worker-Id: <hostname:pid>   —— 隧道回源把全流量归成 127.0.0.1，源 IP 分不出 worker
  X-Hub-Scope: <本 worker --poll 的 hub 数>

hub 侧 race_decision(§ remote/protocol.py，纯函数)：
  off  → 永不广播            on → 永远广播（应急强制）
  auto → 窗口 180s 内 ≥2 个**不同** worker 且它们全都 scope==1 → 广播
          · 单 worker 不广播（广播的全部收益就是“最快的卡先跑完”，一个执行者没意义）
          · 任何 worker 报 scope>1 或缺失（旧 worker / 手写 curl）→ 退回独占（P3b 不变）

竞速轮的具体行为：
  claimable_job_ids(race=True)  最新 job **忽略租约**（谁都能领）；更老的维持独占
  claim(jid, race=True)         不下租约、返回空 token，并**清掉先前那份独占租约**
  worker 拿到空 token ⇒ 不心跳（§343 时代的既有分支），`race:true` 只做日志/观测
  胜负：store_result 首写锁定（赢家 200/201）；后到者 409 —— worker 侧 **409 本来就按
        成功处理**（“hub 已有同 job 结果”），不重试、不报失败
```

**落地时抓到的真缺陷（有回归）**：**先独领、后转竞速**的时序（A 先到，hub 当时只看到它
一个 ⇒ 独占 + lease_token；B 入场才开竞速）——若只清“不下新租约”，A 的旧租约仍是
“活租约”，B 的回传会被 `result_token_ok` 按**非持有人 403** 拒收，而 worker 把 4xx 一律
读成**确定性拒绝**并 `POST /jobs/{id}/fail` 上报 job 失败——**一个赢家把输家炸成事故**。
修法两道：① `claim(race=True)` 当场清租约；② `_post_result` 在**租约校验之前**先看结果
是否已落盘，已落盘一律 409（与 token 无关）。回归：`test_race_releases_earlier_exclusive_lease`。

**观测面**：`GET /admin/race`（模式/是否生效/窗口内 worker 数及各 scope）；`POST
/admin/race?mode=auto|on|off` 热切（volatile，与 halt 同性质；切换后清空历史登记）。
`/admin/workers/status` 的体形状**不动**（console 的 set_cloud_halt 读它）——竞速不往里叠字段。
`--race` 启动参数由控制台从 `rl.race_mode` 透传（非法值先归一化为 auto：hub-server 的
argparse choices 会让未知值**秒退**，写错配置不能让整个 hub 起不来）。

**本轮的已知代价（有意接受）**：输家会一直算到结束才发现输了（N-1 份 GPU 白烧、payload
也要多传 N-1 份）。叫停输家（赢家落账后通知在跑副本放弃）**本轮不做**，记为后续。

**验证**：`nn-training/tests/test_race_broadcast.py` 22 例（头解析/判定五分支 + 窗口离场 +
单 worker 不广播 + 身份去重 + 存储层“最新才广播/广播不落租约/清旧租约” + HTTP 全链路
双卡同 job 首写胜 409 丢弃 + 多 hub 退回独占 + 缺头保守 + on/off 热切 + worker 侧头上报与接线）；
`dashboard/tests/hub-server-race-arg.test.ts` 4 例。门禁：nn python **1222 passed / 3 skipped**、
ruff+mypy 干净；dashboard typecheck + **489 pass / 0 fail**。
决策见 `DECISIONS.md §2026-09-17-goalnn-race-broadcast`。

## §66 in-loop eval 墙钟：软等可配（180s→30s）+ 本机份额提前放行（2026-09-17）

**为什么记这一笔**：用户检查训练流程后确认「eval 已藏进下一轮 PPO」——`_dispatch_delayed_eval(it)`
排在 `_serial_ppo(it)` **之前**、读归档 W(it-1)、事后只软等（`select_delayed_eval_it(6)==5`）。
但仍有**两段墙钟暴露在 PPO 之后**（都在 `rl/loop_steps.py`）：

```
改前： it6 collect(W5) ─┬─ 派发 eval(W5) ─┬─ PPO(6)  ◄── 节点侧 eval 藏在这里 ✅
                        │                 └─ _join_eval：软等 ≤180s（硬编码）❌①
                        └─ 本机份额 gate 只在 _join_eval 置位 ⇒ 本机 eval 局
                           在 PPO 收尾后才开跑（local_slots ≈ 5% 语料）❌②

改后： ① **不站等固定秒数**：尾巴交下一轮 rollout 收官这个自然边界收拢（_sweep_eval_tail，非阻塞）
          应急旋钮 policy.evalJoinSoftSec **缺省 0**（>0 = 回到 “PPO 后最多站等 N 秒”）
       ② 本机份额按「本轮 PPO 是否占本机核心」分档放行（policy.evalLocalEarlyEpochs，默认 1）
          · 远端 PPO（--ppo remote）/ 整轮上云（rollout=node）/ stream 轮
              → immediate：本机核心此刻空闲 ⇒ 派发即开闸（节点 hold_for_local 预留同步解除）
          · 本机 PPO → last_epoch：末 early 个 epoch 完成即开闸（复用 ppo_update 的
              on_epoch_done 钩子，判据 `ep_done >= epochs - early`，与吞吐 T4 预采同口径）
          · early=0 → on_join：维持 R6 原语义（纯让位）
          · 远端降级本机（_serial_ppo 落到本地）⇒ _regate_local_eval 收回我们提前放的 gate
```

**为什么是“边界收拢”而不是“站等 N 秒”（2026-09-17 同日修正）**：固定秒数两个方向都错——
尾巴早落地就白站（最常见），尾巴更晚就照样丢。尾巴在**下一轮整段采集**（分钟级）里自己能跑完、
自己写 summary（wver 键控、续跑幂等），所以到下一个自然同步点（下一轮 rollout 收官，即
`_dispatch_delayed_eval` 入口；另加收官 `_drain_pending_eval`）只需零成本观测/清账：已收官的
打一行 `tail settled during rollout`，还在跑的（异常：节点慢/挂）打 WARN 后交后台——时间基准用它
**自己的** `eval_window_sec`（不引入新魔数），它自己的 deadline 会结束它。intent/goal 模式**不动**：
止损判门要吃同轮 summary，仍走全预算 join（`window+60`）。

**验证**：`nn-training/tests/test_eval_timing.py` 新增/更新为 16 例（含「缺省零 join」「边界收拢」）——旋钮读数与坏值回落、放行档三分支、
`early_epoch_reached` 边界（early=0 / early>epochs）、派发即放行 + 降级收回、本机 PPO 未放行 /
epoch 钩子到点放行 / `evalLocalEarlyEpochs=0` 不加钩子、缺省零 join + 边界收拢（已收官/在跑/应急旋钮>0/无尾巴）、`evalJoinSoftSec` 应急值超预算夹回。
门禁：`e2e/test_run_rl.py -k "eval_deferred|eval_post_ppo_weights|eval_local_gate|tail_join_grace|early_race"
5 passed`；nn python 全量 **绿**（**1200 passed / 3 skipped，第一次无 any deselect/跳过**——
§65 记的那条平台性存量红已在 §66.1 修掉）；ruff + mypy 干净。

### §66.1 顺带清掉存量红：盘符/UNC 路径在任何平台都被拒（2026-09-17）

**红点**：`test_remote_iter::test_spec_argv_weights_must_be_relative` 在 Linux 上恒失败——
`_iter_rel_path`（`remote/protocol.py`）只挡 `..` / `~` / POSIX 绝对路径，`os.path.isabs` 与
`Path.is_absolute` 都只看**当前内核**规则 ⇒ `C:/weights.json` 在 Linux 上静默过门。
这不只是“测试红”：Windows 盘符路径真能过 hub 的门，到节点上却指向**宿主盘**（或直接跑挂）——
节点的 cwd 契约不随 hub 内核变。

**修**：`_iter_rel_path` 增两条跨平台判定（只多拒、不放松）：盘符前缀 `^[A-Za-z]:`（绝对值与
**drive-relative `c:x`** 都算）与 UNC（`\\host\share`）。回归用例扩为参数化 5 例（`C:/`、`C:\`、
`c:weights.json`、两种 UNC）+ 一条反向断言（`weights.json`、`sub/dir/w.json`、`./w.json`、
`w-1.2.json`、`a_b/c.json` 照旧放行，防收紧误伤正常相对路径）。修前 4/5 参数化用例红（`//host/…`
本就命中 `os.path.isabs`），修后全绿。

**门禁**：`nn-python-gate.sh` **1200 passed / 3 skipped，rc=0**（首个无 deselect、无跳过的全绿）；
ruff + mypy 干净。

**仍暴露的墙钟（有意保留）**：① 收官 drain（`_drain_pending_eval`，无 PPO 可藏，
≤min(window+60,600)s）；② intent/goal 全预算 join。per-tick 主链现在**不为 eval 站等一秒**。
另：stream 路径「标签超前一轮 + 同 iter 双点」的缺陷**未动**（`--ppo remote` 下不可达，本地
stream 腿才可见），已在上一轮的流检查中记录，待单独处置。

## §65 节点门统一：rollout 与 eval 同用 codehash-files.txt（eval 门改比 codeHash，ping 不再报 engineEpoch）（2026-09-17）

**为什么记这一笔**：eval 节点门比的是 `engine_epoch = sha256(git_full_commit + GAMEPLAY_SPECS
指纹)[0:16]`——**掺了 git commit**，而 gameplay 集还是 TS（codehash-files.ts）/Python
（dist_common.py）两侧手工镜像的第二份清单。于是任何与 rollout/eval 无关的提交（dashboard /
nn-training / docs）都把全节点判 stale，运维只能同步 + 重启 sampler-agent（2026-09-15
x3-power it30：epoch 全员 mismatch → nodes_ok=[] → 600s 零局；2026-09-03 x3-chip-k10
it1–it4 四节点同因全 skip）。用户指令：两者统一用 `tools/agent/codehash-files.txt` 作为
「节点是否可用」的事实来源。

```
唯一事实来源（改这里 = 同时决定 rollout 门与 eval 门）
  tools/agent/codehash-files.txt
    ├ src/nn/**（策略）· tools/sim/export-*（导出器）· tools/agent/*.ts（agent 协议）
    └ 2026-09-17 并入：src/game/** · src/config/** · src/utils/**（RNG）· src/ai/**（God）
                        · tools/det-golden.v1.sha256   ← 原 GAMEPLAY_SPECS 双份清单已删
  排除原则（同处写明）：dashboard/** · nn-training/** · docs/** · plan/** · tests/**
                        —— 它们的提交不得让任何节点变 stale

  /v1/ping 只报 codeHash = hash(清单展开) —— 唯一的节点门字段
  rollout 门（dispatch / rescan）与 eval 门（eval_dispatch §6.6 / batch_eval 严格）同用
    dist_common.check_code_hash(ping, 本机 codeHash)
  engine_epoch = sha256(codeHash)[0:16]（训练机侧算）= **账本记录值**：进
    EvalGameRow.engine / 心跳 / S10 哨兵；**不再是门**，故不进 ping（原 check_engine_epoch 删）
```

**语义变化（有意为之）**：S10「引擎漂移」的范围从 git commit 收窄为清单代码（含 src/nn 策略），
**比旧式更不容易误报**（以前连 docs 提交都算漂移）；而引擎文件入清单后，「改引擎不改名 codeHash」
的漏网口同时关闭。节点门也只剩**一个字段（codeHash）与一个判据**：旧 agent「无 engineEpoch →
过渡期放行」的宽松分支随之取消——codeHash 是 rollout 门一直都在用的字段，比它只严不宽。**代价**：本次自身即清单变更 ⇒ 全节点一次性
判 stale ⇒ 一轮预期内的升级波（同步代码 + agent 重启后自动纳管）。

**门禁实测（2026-09-17 收尾全绿）**：根 `bun run check` **1851 pass / 0 fail**；dashboard
typecheck + **485 pass / 0 fail**（顺带修掉进入本次任务时那条**存量红**
`server-actions-resolve-course-bc`：用例引用的是从未存在过的课程名 `x1-rebirth-a2`，仓内课程是
`x1-rebirth.jsonc`，已改成真课程名）；nn python：ruff + mypy（225 文件）干净、
**1183 passed / 3 skipped**。当时剩下一条未跑
`test_remote_iter::test_spec_argv_weights_must_be_relative` 是**平台性存量红**：
`_iter_rel_path` 只挡 `..` / `~` / POSIX 绝对路径，`C:/weights.json` 在 Linux 上既非
`os.path.isabs` 也非 `Path.isabs`（Windows 上才拦得住）——**已于同日修掉，见 §66.1**。
决策见 `DECISIONS.md §2026-09-17-goalnn-unified-node-gate`。

## §64 控制台两条腿：导出任务包 / 导入产物即评估（2026-09-17）

**为什么记这一笔**：把 §61–§63 的离线能力**接到人手上**——之前「导出任务包 / 导回产物」
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

## §63 产物补传：中途能连上 hub 就自动恢复在线回传（2026-09-17）

**为什么记这一笔**：这是全离线（§62）需求的后半句，也是**第一条「在线能力可选」的链路**
——它之前的每条腿都以「hub 可达」为前提，而这条腿必须**既不影响训练、又能在断网后自愈**。
决策与备选（含六条否决）见 `DECISIONS.md §2026-09-17-goalnn-offline-reconnect-delivery`。

```
节点每轮：checkpoint 落盘（自洽）→ sync()：探 GET /ping（带 token）
  ├ 不通 / 5xx     → 静默跳过（节流记一行），下轮再试           ← 训练继续
  ├ 401/403        → **本会话停用**补传（D9 会把每轮重试的自己封掉；配置错重试无用）
  └ 通 → POST /offline/artifact（权重+opt+账本行，(run_id, it) 幂等、首写锁定）
            └ 第一次连上时把之前攒的**积压一次补齐**（pending = 磁盘上有、delivered.json 里没有）
hub：<job_root>/offline/<run_id>/it-NNN/{weights.json,opt.tar,row.json} + metrics.jsonl
     + result.json（段末摘要，覆盖写）+ 账本审计事件 offline_artifact / offline_result
```

### 三个必须先说清的判定

1. **探活走带 token 的 `/ping`，不新增无鉴权 `/health`**：探针要回答「我能不能用这条链」，
   只证可达会让「token 配错」在第一次上传 ~1.9MB 之后才暴露，还多一个向公网泄露「hub 在线」
   的端点。
2. **401/403/400/413 = 本会话停用**，不是重试：D9 的闭锁是「同 IP 五次无效鉴权封 3600s」
   （每轮重试 = 自己把自己封掉），且体被拒说明形状不对（版本不匹配），重试一百次也不会对。
   5xx / 网络异常才走「下轮再试」。
3. **补传不是 job**：没有 pending、没有租约、不进待领池（`claimable_job_ids() == []`，测试钉住）
   ——记一条 `job_pending` 会让控制台显示一条永远等不到工人的任务，还可能被别的节点领走。

### 写测试时按以往教训卡的两个真缺陷（都有回归）

| 缺陷 | 不修的后果 |
|---|---|
| 传输异常从 `_post` 冒到 `sync()` 外层 | 外层兜住了异常但**跳过了 `_save_ledger`** ⇒ 本轮已投递的几轮只在内存里、下次会话整批重传（幂等但白焚流量）。修法：异常收在 `_post`，返回状态 0 = 没送达 |
| 账本行自称的权重指纹没与实收字节对账 | 产物目录**内部不一致**（人改过 / 半截写入）的权重会以「hub 上的产物」身份进入 eval/续跑，而真因在几千行日志之外。修法：hub 侧 400 拒收 |

另外两处防护按「不可信输入」对待（hub 端）：`run_id` 是目录名 ⇒ `sanitize_run_id`
（`[A-Za-z0-9][A-Za-z0-9._-]*`、≤64、拒 `..`、**非字符串即非法**，不做 str() 兑底——宽容只会
把「上游传错类型」变成看似正常的目录名）；声明长度超限直接 413（不读进内存）。

### 验证面（本机可跑的）

`tests/test_offline_deliver.py`(16)：接通后落位/幂等/重启续投/**hub 关机期间零影响 + 重连一次补齐**
（核心需求）/坏 token 只试一次不多出网/体被拒即停用/`SYNC_CAP` 分次/传输异常不破记账/
越界 `run_id` 拒收且不落盘/指纹不符拒收/重复投递首写锁定/摘要覆盖写 + 审计/未鉴权 401/超限 413/
`sanitize_run_id` 纯函数/**真 hub + 替身 run_job 的整段集成**（逐轮在跑的过程中到达 hub）/hub 无人
听时整段照常跑完。

门禁：nn python gate **1171 passed / exit 0**；根 `bun run check` **1853 pass / 0 fail**；
dashboard typecheck 干净 + **468 pass / 0 fail**。

### 未做（不写成已做）

① 真云端（Kaggle/Colab）端到端一次——本机没有节点，整条链只在真 HTTP 端点 + 替身 `run_job`
上跑过；② 控制台里显示补传进度（账本 `offline_artifact`/`offline_result` 已在写，读方未接）；
③ push 形态的节点没有 hub 地址 ⇒ 默认不开补传（需要显式配 URL）。

## §62 全离线任务包落地：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完（2026-09-17）

**为什么记这一笔**：新增一条「任务可以离线搬运」的交付面（协议外的文件格式 + 两处入口 + 一个
不可信输入的边界）；决策与理由（含四条被否决的备选）见 `DECISIONS.md`
§2026-09-17-goalnn-offline-task-bundle。

### 与半离线（§61）的关系

```
半离线（kind="run"）：hub **在线**发一次 job（计划随 payload）→ 云机自主跑完 → 回传尽力而为
全离线（task bundle）：hub **导出 zip**（计划+课程+权重+动量+代码+TS）→ 人搬上云（dataset/Drive）
                       → 云机 import 后自主跑完 → 产物落 Kaggle output / Drive
                       → **若中途能连上 hub**，自动补传（下一笔；本 § 未做）
```

半离线解决「云机不依赖 hub 也能跑完」，全离线解决「**hub 关机也能开工**」——任务本身不再走网络。

### 形状

```
hub:  run_rl --ppo remote --run-iters N --export-bundle tmp/task.zip
        └ 不训练、不等待：拼 plan + 复用发布链造 manifest（register=False：不记账本/不进待领池）
        └ remote/bundle.export_bundle：task.json 索引（逐件 sha256+字节数）+ README + 六件套
云机: python -m remote.bundle import task.zip --dest /kaggle/working/battle2-<run>
        └ 逐件对账（不符即拒收，一局不跑）→ 铺成可直接续跑的产物目录（it-{it}/ 起点 + ts_code/）
      python -m remote.run_loop --artifacts <dir> --device cuda      # 或 --bundle <zip> 一步到位
        └ code.zip / ts_code.zip 字节走 run_job 的 preloaded（节点无仓、不联网）
产物: it-NNN/weights.json + opt.tar + metrics.jsonl + state.json + LATEST.zip/artifacts.zip
      Kaggle = /kaggle/working（Save Version 即官方打包下载）；Colab = Drive / files.download
```

轮次对齐（整条最容易错的一处）：loop 的 `it` = 包里要跑的**第一轮** ⇒ `plan.start_it = it-1`、
`max_iters = n`（不是 n-1——这里没有「job 自己那一轮」要扣，`args.out` 就是包的起点）。

### 两处按教训卡出来的硬门

| 门 | 不卡的后果 |
|---|---|
| 导入逐件 sha/字节数对账 + zip-slip 拒绝 + magic 校验 | 一次截断的搬运会变成一堆无法归因的怪结果；恶意/损坏成员可在云机上写任意文件（包是人搬来的，最不可信） |
| standalone 入口「代码可用」硬门（`code.zip` 或 `code_cache/<sha>/` 命中） | `run_job` 拿空 base_url 去下载代码，报一个跟真因无关的错（重试/联网都治不了）——改前 `test_run_loop` 的两个用例正是这么红的 |

注意这个门**只卡 standalone 入口**，不卡 `_run_iteration`：半离线轮（hub 发的 kind=run）的代码是
worker 自己那一轮从 hub 下好、已落进内容寻址缓存的——那里没有 `code.zip` 字节也完全正常。

### 验证面（本机可跑的）

`tests/test_bundle.py`(7)：导出→导入的目录形状（起点 checkpoint + opt + TS 树）；改一个字节就
逐件对账拒收；zip-slip 成员拒收且不落盘；拿错包/坏 zip 在读索引这一步停；导出侧三道自检（计划
sha 不符 / 缺 code.zip / 缺 ts_code.zip）；**导入后 `run_standalone` 无网无仓跑到 end_it**
（替身断言 payload 里带了 code.zip 与 ts_code.zip 字节、`base_url/token` 必须为空），再跑一次是
noop（续跑判定认得出同一段，不从 start_it 重来）。

门禁：nn python gate **1154 passed / exit 0**；根 `bun run check` **1853 pass / 0 fail**。

### 未做（不写成已做）

① **自动补传**（用户同一条需求的后半句「中途能连上 hub 就自动恢复产物在线回传」）——设计已定：
每轮 best-effort `POST /offline/artifact`（按 `(run_id, it)` 幂等、首写锁定）+ 轮末
`POST /offline/result`；`GET /health` 轻量探活；连不上静默跳过、下轮再试；产物目录 `delivered.json`
记已投递项（重启后续投）；hub 侧落 `<job_root>/offline/<run_id>/` 并记 `offline_artifact` /
`offline_result` 账本事件；token 走 secret 不进包。② 真云端（Kaggle/Colab）端到端一次——本机无
GPU 节点。③ 控制台入口（导出按钮 / 离线进度显示）。

## §61 半离线整段落地：kind="run"（一次领走整段，节点自主跑完 + 产物可打包下载）（2026-09-17）

**为什么记这一笔**：新增一种 job 语义（跨层协议 + 新的执行器 + 新的产物面），并抓到两个真缺陷；
决策与理由（含四条被否决的备选）见 `DECISIONS.md` §2026-09-17-goalnn-halfoffline-run。

### 需求与判据（用户 2026-09-17）

> 「云机从 hub 领到训练任务（课程、初始权重、代码）后，即使本机 hub 一直失联，它也能全程自主
> 完成训练，并以 kaggle/colab 官方支持方式提供产物（每轮权重和指标）打包下载。」

三问已答：**一次领 = 整段**；**逐轮权重 + 指标 + 可续跑**；**不在云上跑评估**（只记训练指标）。

### 形状：kind="run" 是 kind="iter" 的延长，不是 fork

```
hub（哨兵）                                  云机（自主段执行器）
  build_plan → plan.json（对集入参 + argv 模板 + end_it）
  publish_job(kind="run", plan_bytes=…)  ───► payload: init_weights.json + plan.json + ts_code.zip
                                              ① verify_plan_file（sha / 形状 / 全段对集指纹）→ 一局不跑就拒收
                                              ② run_job(it)（与 kind=iter **同一**执行链）
                                              ③ run_plan_job 尾巴：逐轮合成同构 iter job → run_job
                                                 └ 每轮回传的权重 = 下一轮 payload 的 init_weights.json
                                                 └ opt 走 blob_cache（Adam 动量不丢）
                                              ④ 逐轮落产物：it-NNN/{weights.json,opt.tar} + metrics.jsonl
                                                 + state.json + LATEST.zip；收尾 artifacts.zip
  wait_job(timeout=8h)  ◄───── 合并结果（末轮形状 + iters 明细 + it_end + artifacts 元信息）
  verify_and_land（零新代码：三个指纹逐字段对的是本 job 自己）
  run_segment 事件（逐轮指标进控制台）+ it 跳到段尾，照常走本机结算/归档
```

轮次语义的一条硬约束：**段中间那些轮不派发本机 eval**——它们的权重不在本机归档里，用活指针充
`W(it-1)` 就是刚修过的「eval 标签超前一轮」。段尾权重由下一轮（或收官 drain）正常派发。

### 两个被测试抓出来的真缺陷（都已进回归）

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 1 | `ArtifactStore.start` 用**调用方传入**的计划 sha 做续跑判据，而目录写盘的是另一份格式（hub 走 `dump_plan` 的规范形 `sort_keys=True`） | 新会话拿同一目录永远算不出相等值 ⇒ 每次都被当成**新段**从 `start_it` 重跑；「关掉会话明天接着跑」静默退化成重跑，只有对着日志才看得出来 | 判据改为**写盘之后**对磁盘上的 `plan.json` 算 sha（自描述）；传入值另存 `plan_sha256_declared` 供审计 |
| 2 | 起点快照（`_seed_start_checkpoint`）往 `metrics.jsonl` 写一行（无 agg/report） | 「账本一行 = 一轮、it 唯一」失效，并逼下游用「过滤掉没有 report 的行」绕开——用过滤器掩盖一条本不该写的行 | `checkpoint(..., row=None)` = 只落 checkpoint 不记账；`_combined` 的 report 过滤器随之删掉 |

两个都是**先写测试、后改代码**；`test_standalone_resume_continues_without_duplicate_rows` 就是缺陷 1 的回归。

### 验证面（本机可跑的）

- `tests/test_plan.py`(8)：对集重放逐位一致（rotate / curriculum / seed-rotate 三模式）、
  argv 模板重定向恒等、`PAIR_ARG_FIELDS` 漏字段在**发布期**就红、`pairs_fp` 顺序敏感。
- `tests/test_run_loop.py`(11)：**注入 run_job 替身**跑整条链——替身自己开 payload 验
  `init_weights.json` 的字节（所以「权重逐轮传下去了」不是自报），逐轮 job 与 kind=iter 逐字段同构
  （逐局 stage/seed、`--wver` = 该轮 init 的 sha），合并结果**过 `validate_result`**；预算/上限/重领/
  失败四种停机点都能续跑且账本 `it` 唯一。
- `tests/test_run_segment.py`(12)：段长与等待上限的解析优先级（CLI > courses > rl > 缺省关）、
  `max_iters=n-1` 的区间语义、`publish_job(kind="run")` 把 `plan.json` 放进 payload 且 manifest 记 sha、
  缺计划/缺规格/带本地 shard/缺 ts_code sha 一律拒发、同一计划重发同 job_id、发布结果过协议层校验。
- 门禁：nn python gate（ruff + mypy + pytest tests/ + e2e）**1147 passed / exit 0**；
  根 `bun run check` **1853 pass / 0 fail**。

### 未做（不写成已做）

① 真云端（Kaggle/Colab）端到端跑一次——本机无 GPU 节点，`run_remote` 这条路没跑过；
② 段内**进度上报到控制台**（长段期间 hub 只有等待，逐轮指标要等段尾才可见）；
③ 控制台启动弹窗的 `run_iters` 选项（现只有 rl-config / CLI）；
④ push 传输的等待预算仍 1800s（半离线的自然形态是 pull：Kaggle worker 在会话内长期驻留）。

## §60 节点确定性失败带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410（2026-09-17）

**为什么记这一笔**：这是**跨层协议**新增（新端点、新终局状态、新的失败分类语义），且改了
「失败即终局 vs 逐轮重试」两条既有语义的交界，按 §5 硬规则入账。决策与理由（含四条被否决的备选）
见 `DECISIONS.md` §2026-09-17-job-fail-report。

### 缺口（用户提问暴露，2026-09-17）

云机**确定性**失败（bun 装不上 / TS 运行时取不到 / argv 非法）此前只落在**云机日志**里：

| 链路 | 旧行为 | 训练侧看到 |
|---|---|---|
| pull（worker 轮询 hub） | `worker_loop` 的 `except ProtocolError` 只打一行 `REJECTED … skip (not retried)`——不回传、不还租约 | `wait_job` 等满 **25 分钟**超时 ⇒ 一行「超时」（把能力缺失写成了网络/排队问题） |
| push（hub 推节点） | 节点 `worker_server` 对 failed 返回 **500** | `push_client.wait_result` 把 500 当**瞬时错误**重试到 **1800s** 预算耗尽 |

两条路都白烧一个超时窗口，且重试后通常再撞同一堵墙。

### 改后契约

```
节点判定「这台机器跑不成」
   └─ POST /jobs/{id}/fail  {reason(必填), kind, detail, worker}      # bearer + 活租约须持有人
        └─ fail.json  首写锁定：已有结果不收失败 / 首个原因胜出（原子 tmp+replace）
             ├─ GET /jobs/{id}/result  → 410 + 原因 + fail_kind + fail_detail
             ├─ GET /jobs/{id}/status  → state="failed" + 原因
             ├─ claimable_job_ids()    → 排除（换节点只是重演同一失败）
             └─ 账本                   → job_failed{job_id, reason, kind, worker}
训练侧：wait_job/ wait_result  → 立刻抛 JobFailedError(reason, kind, detail)
        └─ _abort_node_failure    → 第一次就写 gate_verdict: ABORT（真原因入判决）+ 停腿
逐轮重试：publish_job 重发同 job（同幂等键 → 同 job_id）→ 清掉 fail.json（重发即重试）
```

用途边界（写进代码与决策）：**只有确定性失败走这条路**——瞬时失败（网络/5xx）仍走 `release`
回池、不报 `fail`；`CodeChangedError` 刻意不报（它靠重启进程 + 租约回池自愈，报了会把可恢复的
job 钉死）。

### 改动清单（file → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `remote/protocol.py` | `FAIL_NAME="fail.json"` / `FAIL_BODY_MAX=64KB` / `JobFailedError(RuntimeError)`（携 `kind`/`detail`，文档写清与 Retryable/ProtocolError 的分界） |
| `remote/hub_server.py` | `store_job_failure`（有结果不收 / 首写胜 / 原子写）+ `job_failure` + `POST /jobs/{id}/fail`（`reason` 必填、体有界、租约同 result）+ `_get_result` 410 + `_get_status` failed + `claimable_job_ids` 排除 + 账本 `job_failed` |
| `remote/hub_client.py` | `report_job_failure`（尽力而为，绝不炸 worker 主循环）；`wait_job` 对 410 与收尾二次确认的 `state=failed` 立即抛；`publish_job` 重发时清 `fail.json` |
| `remote/worker.py` | `except ProtocolError` 分支补回报（带 `worker_tag()`=`host:pid`，多机共用 token 时定位现场的唯一线索）+ `_failure_detail`（traceback **尾段**：原因是最后一帧） |
| `remote/worker_server.py` | failed 响应 **500 → 410**（500 在 `wait_result` 里是「瞬时错误」语义）；`set_error` 记 `kind` |
| `remote/push_client.py` | `wait_result` 见 410 → 立即抛 `JobFailedError` |
| `rl/loop_steps.py` | `remote_retryable_exceptions()` 收 `JobFailedError`（不进来就会冒泡到 loop_core 通用兜底杀进程）；`_abort_node_failure` 写 ABORT（真原因）+ 停腿，不耗连败配额、不降级；`_push_job_round` 全节点失败时**原样抛**节点失败而非包成 `RetryableError` |
| `dashboard/src/server/api/ppo-queue.ts` | `job_failed`/`fail.json` 不算「排队超时」；账本读法改 **last-write-wins**（旧口径「出现过终局即关闭」会把重发的同一 job 永久当已关闭，真卡住时不告警） |

### 回归与实测

- `nn-training/tests/test_job_fail_report.py`（**9 例**）：端点/校验/租约/首写/410/status/账本、`wait_job`
  **秒级**失败（断言 `<10s`，旧路 1500s）、收尾二次确认也认失败、worker 回报到位、push 410、
  push round 不被包成 RetryableError、**重发清标记**（不清 = 重试永久钉死，故必须钉住）。
- `nn-training/tests/test_remote_degrade.py`（+2 例）：`JobFailedError` 首败即 ABORT 且 `_remote_fail==0`、
  不降级、`remote_calls==1`（不重试）；在捕获集合里。
- `dashboard/tests/server-api-ppo-queue.test.ts`（+2 例）：`job_failed`/`fail.json` 不误报；失败后重发又被盯排队。
- 实测（2026-09-17 本机）：整个 `wait_job` 快速失败用例含起服务仅 **0.52s**（预算 25min）。
- 门禁：nn python gate（ruff + mypy + pytest tests/+e2e/）**exit 0**（1112 例）· root `bun run check`
  **1853 pass / 0 fail** · dashboard typecheck + **457 pass / 0 fail** + 三份 bundle ok。

### 未做（不写成已做）

- 真远程轮次的端到端验证（本机无节点可跑）。
- 节点侧 preflight **硬门**（计划 §5.3：bun 版本对账不匹配直接拒单）仍只有记录与日志。

---

## §59 M3 rollout 上云落地：新 job kind「一整轮」（kind=iter）＋ TS 运行时打包（2026-09-17）

> 编号说明：本节原为 §57，与 origin 已推送的「python 全量门禁提速」撞号，合并时改 §59。

**为什么记这一笔**：这是本仓**训练架构**层面的新增（新的 job kind、新的传输实体、新的执行位置开关），
按 §5 硬规则入账。方案见 `plan/remote-wire-remediation.plan.md` §5；决策与理由见
`DECISIONS.md` §2026-09-17-goalnn-rollout-on-cloud。

### 开工依据（先把话说清楚：这是**人拍板**，不是门开了）

计划 §6 的原文是「门 1 命中 ⇒ M3 直接留档不做」，而门 1 在 M1 实测里已经命中（`http2` 把
2MiB 上行压到 p50 4.8s，见 §56）。本次开工依据是**用户指令**：目标腿是 TPU 实例
（v3-8 = 96 vCPU），rollout 上云在那里收益极高。即：**门的结论没变，是决策权变了**。
收益前提也照旧：≥16 vCPU 的腿才成立（GPU T4×2 = 4 vCPU 直接否，见可行性报告 §3.3）。

### 改动清单（file:line → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `remote/protocol.py` | 新 `kind="iter"`（走 BC 开过的 kind 通道）：追加必填 `ts_code_sha256` + `rollout`；`mode` 红线互斥照旧；`validate_rollout_spec`（argv 白名单 = 只放行 `tools/sim/export-rl-rollout.ts`；`--out`/`--weights` 必须 job 内相对路径；逐局 stage/seed 不重复）；`iter_declared_entries`/`iter_expected_data_fp`（**声明集**的 data_fp，与实产集同一函数两侧各算一次）；result 增 `report` 必填校验（同 BC 分支先例） |
| `remote/hub_client.py` | `pack_ts_code_zip`（`src/**`+`tools/**` 的 `.ts/.jsonc/.wasm`，固定时间戳 ⇒ 内容寻址；**整棵 tools/** 不是只 tools/sim——实测依赖闭包跨到 `../eval/godai-score`）；`publish_job(kind="iter", rollout_spec=…)`：payload **不含 shard**、`data_fp` 用声明集、ts_code.zip 拷进 job 目录、有 opt blob 也仍带 `init_weights.json`（节点要用它跑 rollout）；`verify_and_land` 对 iter 轮按声明集校验（hub 侧无本地 shard 可重算） |
| `remote/iter_rollout.py`（新） | 节点侧执行器：线程池 `bun <argv...>`（cwd = TS 代码根、job 侧路径绝化）→ 逐位校验实产 shard 集 == 声明集 → `combine_reports` 聚合（与本机 rollout 同一个聚合函数）→ 返回 report/shard_dirs/逐局秒数/bun 版本 |
| `rl/iter_job.py`（新） | hub 侧规格构造：**复用** `rl/cmd.build_rollout_cmd`（三导出器 + 课程覆盖 + D14 血缘的唯一拼装点）只换路径，丢掉 argv[0]（本机 bun 绝对路径在云机上无意义） |
| `remote/worker.py` / `worker_server.py` / `hub_server.py` / `push_client.py` | TS 运行时四件套：内容寻址缓存 `ts_code_cache/<sha>`（**已加进 `prune_job_dirs` 豁免名单**，M2 的 `blob_cache` 就是漏了这个）、`/ts-code-sha` 探测、`/jobs/{id}/ts_code` 下载、`428 ts-code-missing` 门、wire 记 `ts_code_bytes/hit` |
| `rl/loop_steps.py` / `loop_core.py` / `cli.py` | `--rollout-src auto|local|node`（缺省 auto→local）；`_remote_iter` = 整轮上云；node 轮本机**完全不采样**（跳过 `_rollout_phase`，也不预采/不补波）；`_rollout_source()` 读 rl-config（CLI > `courses.<课>.rollout_src` > `rl.rollout_src` > local，D14 血缘：选项永不进 curricula） |
| `dashboard/src/{core,stack,server,web}` | 启动弹窗新增「rollout」选项（本机/上云(节点)/auto）+ 当前生效值；route 白名单（非法 400）；preset 落 `rl.rollout_src`（**字符串域，原样落，不过任何换算**——与 `slim` 的双域相反）；console-state additive |

**回退开关**：`--rollout-src local`（= 缺省；逐字节回到旧行为：本机采样 + 只把 PPO 送云）。

### 安全阀与互斥（配错要响亮，不许静默降级）

1. `rollout_src=node` **与 `--target-transitions` 互斥**（补波要求训练侧反复读本地 shard，而上云轮的
   shard 在节点上、跑完即毁）⇒ 配错即 `SystemExit`；
2. 发布 iter job 时 traj 目录**已有本地 shard** ⇒ 拒发（否则 = 双份采集，且下一轮又会被自己拒绝）；
3. 节点侧实产 shard 集 ≠ 声明集 ⇒ `ProtocolError` 拒收（漏局/多局/`wver` 漂都不会因为重试而变好）；
4. `opt_sha` 有值而 blob 取不到 ⇒ 响亮失败（M2 既有规矩，上云轮同样适用）。

### 控制面迁移清点（计划 §5.4 说的「最容易漏一半的地方」）

| 读取点 | 上云轮处理 |
|---|---|
| 本地采样 / 预采 | loop_core 直接跳过 `_rollout_phase` + `spawn_next_collect`（不是「配成 0」，是**结构上不可能**双采） |
| `rollout_sec` | 用节点回传 `report.elapsedSec`（t_rollout 含「等节点跑完 rollout + PPO」的整段墙钟，拿它当采集时间 = 假指标） |
| `metrics_stats` | **跳过**（shard 不在本地，硬跑只会写一份 `shards=0` 的假精度统计），逐维度口径改由 report 的 `dimMeans/scoreStats` 承担 |
| `_check_quota_incident` | **跳过**（上云轮本地零 shard 是预期，否则每轮喊「检查配额」把真事故淹掉） |
| eval | 零改动（仍在本地 hub 用 bun 跑，权重照旧下行归档） |
| 账本/控制台 | `wire.rollout_src` 落每轮取值；「传输」页已有该列（M0 铺的） |

### 实施中踩到 / 修掉的四个坑（都有回归测试）

1. **`_remote_iter` 绕过了 4xx 立即停腿判据**：上云轮不走 `_remote_ppo_or_degrade`（loop_core 跳过
   `_serial_ppo`），于是 x3-step 事故那种「403 白烧 5×30s 重发同一 job 才死」会重演 ⇒ 在
   `_remote_iter` 里补上同一分类（`fatal_remote_http` → 写 ABORT 判决 + `_leg_abort`）。
2. **`push_client` 的 `428 ts-code-missing` 没置 `need_ts`**：典型场景是「探针说缓存命中、真 POST 时
   缓存已不在」（并发清理 / 两课共享节点），此时 `need_ts` 本是 False ⇒ 一直重发不带 ts 的体、
   白烧满重试预算后整轮失败。与 `code-missing` 的 `need_code = True` 同规。**先复现再修**（§7.1）。
3. **`ts_code_cache` 必须进 `prune_job_dirs` 豁免名单**——否则每轮必 miss，而日志上一切正常。
   这正是 M2 `blob_cache` 的同一个坑，所以顺手把热替换测试的期望集也扩成三棵缓存树。
4. **`pack_ts_code_zip` 的白名单不能只放 `tools/sim`**：`export-rl-rollout.ts` 的依赖闭包跨到
   `../eval/godai-score`（v7 评分口径）。手写「该打哪几个子目录」就是在猜依赖图 ⇒ 打整棵
   `tools/**`，并靠 `test_remote_iter_real_bun.py`（真 bun 真跑）当哨兵。

### 本机验收（能测的都测了）

| 计划 §5.5 条目 | 状态 |
|---|---|
| ① 逐位对拍（节点 shard == 本机 rollout，逐字节 diff） | **已验**：`tests/test_remote_iter_real_bun.py`（真 bun + 真权重 + 真 `pack_ts_code_zip` 解包），同 argv 跑两遍逐文件比对，含 `_rl_report.json` 全字段（除 `elapsedSec`） |
| ② report 等价（winRate/outcomes/ticks 同种子） | **已验**：同上，聚合报告 `totalTicks/totalSamples/outcomes/winRate` 落在单局口径上 |
| ③ 电量账 `up_bytes ≈ 0` | **部分**：payload 确实只剩 `init_weights.json`（单测断言「不含 shard」），但**真远程轮次的绝对值未测**（本机无节点可跑） |
| ④ 可回退 `rollout_src=local` | **已验（构造性）**：非 iter 轮 manifest 不含新键、`shard_dirs` 走原路 ⇒ 逐字节旧行为 |
| ⑤ 协议用例（缺字段拒收 / mode 互斥 / report 校验） | **已验**：`tests/test_remote_iter.py`（51 例）+ `test_remote_ppo.py` 扩 1 例 |

**未做（不写成已做）**：真云机/真远程轮次的绝对值（`wire.up_sec`、每轮墙钟、TPU 腿上的 target ~10s 量级）；
本机也没有跑过「hub + localWorker + trainer `--rollout-src node`」的整条本机闭环（那需要一次真 PPO 轮）。
⇒ 下一步的开门条件：拿 TPU 实例跑 ≥2 个独立 run 各 ≥8 轮，比 `rollout_sec`/`ppo_sec`/`wire.up_bytes` 中位。

### 测试与门禁

新增/扩写：`tests/test_remote_iter.py`（协议 + 规格 + 节点执行器 + ts_code 缓存 + 失败语义 + 控制面两处跳过）、
`tests/test_remote_iter_real_bun.py`（逐位对拍）、`tests/test_remote_ppo.py`（`/ts_code` × `/blob` 两条 GET 端点）、
`e2e/test_push_mode_integration.py`（+5：ts 缓存命中/未命中/428 补传/缺字节响亮失败/真 worker_server 闭环）、
`tests/test_remote_hotswap.py`（豁免名单扩到三棵树）、`dashboard/tests/rollout-src-launch-option.test.ts`（+8）。
门禁：nn pytest+e2e **exit 0**（ruff/mypy 干净）、root `bun run check` **1853 pass / 0 fail**、
dashboard **427 pass / 0 fail** + 三份 bundle ok。

---

## §58 远程传输三改 M0–M2 落地：统一计量 + 隧道协议开关 + 协议瘦身（2026-09-17）

> 编号说明：本节原为 §56，与 origin 已推送的「hub-server 重启死锁收口」撞号，合并时改 §58。

**为什么记这一笔**：本笔是**训练架构变更**（云腿线协议 + 校验语义 + 新的运行期开关），按 §5 硬规则入账。
方案与十项验收口径见 `plan/remote-wire-remediation.plan.md`；决策见 `DECISIONS.md` §2026-09-17-goalnn-remotewire-m0m2。
切片提交：**M0 `0bc7a69`**（计量）/ **M1 `fa6a34f`**（隧道协议）/ **M2 `a59b1ef`**（瘦身）。

### 改动清单（file → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `rl/loop_steps.py`（`_remote_ppo`/`_push_job_round`）+ `rl/events.py` | iteration 事件增 additive `wire` 子字典（up/down_bytes、up/pack_sec、blobs_miss、protocol、edge_ip、slim、rollout_src）；`_wire_from_result` 把 worker 半与 hub 半合成一份账 |
| `remote/worker.py` | result 增 `wire`（payload_bytes/dl_sec/unpack/opt_restore/result_bytes/blob_hits/blob_miss_bytes）；`blob_cache/<sha>` 落盘自身产出的 opt；`prune_job_dirs` **豁免** `blob_cache`（否则下一轮必 miss——实施中踩到的真坑） |
| `remote/hub_server.py` | `/jobs/{id}/blob?name=opt\|ref`、serve payload/收 result 时记 `sent_bytes`/`recv_bytes`、`/admin/net-probe?bytes=N`（鉴权 + 固定种子填充，同 bytes 逐字节相同） |
| `remote/push_client.py` / `remote/worker_server.py` | `/blob-sha?sha=` 探测；`/job` 体默认 v2（BRJ2）+ 4xx 一次 JSON 退路 |
| `remote/{protocol,hub_client}.py` | B1 不打占位 `manifest.json`、B2 不打 `opt_init.tar.b64`、B4 有 opt 时不带 `init_weights.json`；B3 `opt_sha`/`ref_sha` 内容寻址（可选键，`slim=false` 逐字节回旧行为） |
| `dashboard/src/stack/{specs,hub,core/types}.ts` + `server/{actions,api}` + `TrainLaunchModal.tsx` | `cf_protocol`(`http2`|`quic`|`auto`，缺省 http2) / `cf_edge_ip`(4|6|auto) 一路到 cloudflared 命令行；抽出 `cfTunnelArgs` 供两处 spawn 共用；复用旧隧道时**比对登记的协议**，不一致则杀旧起新 |

**回退开关**：`slim`（瘦身，缺省关＝旧字节行为）、`cf_protocol`/`cf_edge_ip`（隧道，缺省 `http2`/`4`）。

### 实测数字（本机闭环，`tmp/m2-smoke.log` / `tmp/m2b-e2e.log`）

| 量 | it1（冷） | it2（同会话） |
|---|---:|---:|
| published = hub.sent = worker.payload | 1,683,776 | 1,403,724 |
| blob_hits / blob_miss_bytes | 0 / 0（首轮无 opt_sha） | **1 / 0** |
| worker 结果体（v2 vs 同内容 JSON） | 1,039,382 vs 1,385,656 | 1,041,907 vs 1,389,026 |

- it2 日志出现 `model/opt 从 opt_init（cache）恢复（Adam 动量延续，D5）` ⇒ **1.19MB 的 opt 整轮没过线**（B3 的设计意图）。
- push 侧 B5：真 `worker_server` × 真 `push_client` e2e 实测 v2 体比同内容 JSON 体小 **>20%**（断言在 `e2e/test_push_mode_integration.py`）。
- 门禁：`bun run check` 1853 pass / 0 fail；`bash tools/githook/nn-py-safe.sh -m pytest nn-training/tests nn-training/e2e -q` 310 pass / 0 fail；`bun dashboard/src/server/build.ts` 三份 bundle 通过。

> 📺 **在哪看**：控制台详情抽屉新增「**传输**」页（`dashboard/src/web/app/panels/WirePanel.tsx`）
> —— 上半是每轮 `wire` 账（上行/下行/打包/blob 未命中/协议/瘦身 + 最近 12 轮走势表，数据源
> `metrics.iters[].wire`），下半是 `tmp/tunnel-ab-*.json` 的 A/B 表（p50/p90/max + 倍率徽章，
> 新→旧）。从此不必翻日志/JSON 才能读到这两组数。

### M1 隧道 A/B 实测（已跑：`remote/tunnel_ab_probe.py`，2026-09-17 本机）

探针自建环境（真 hub-server + `cloudflared tunnel --protocol <p> --edge-ip-version 4` × 每腿一条
quick tunnel），每腿每方向 2MiB × N 发；`loopback` 腿作基线。**每臂 2 个独立 run**（run1/run2 各 5 发，
run3 两臂各 8 发）。原始 JSON：`tmp/tunnel-ab-{1,2,3}.json`。

| 腿 | 方向 | run | n | p50 | p90 | max | p50 吞吐 |
|---|---|---|---:|---:|---:|---:|---:|
| loopback | up | 1 | 5 | 0.02s | 0.02s | 0.03s | 890 Mbps |
| loopback | down | 1 | 5 | 0.03s | 0.03s | 0.03s | 576 Mbps |
| **http2** | **up** | 1 / 2 / 3 | 5/5/8 | **4.84 / 4.66 / 4.85s** | 4.84 / 5.90 / 5.39s | 5.26 / 6.49 / 9.41s | ~3.5 Mbps |
| http2 | down | 1 / 2 / 3 | 5/5/8 | 4.80 / 5.09 / 5.30s | 5.03 / 6.61 / 9.26s | 8.72 / 8.42 / 9.44s | ~3.3 Mbps |
| **quic** | **up** | 2 / 3 | 5/8 | **23.42 / 33.04s** | 25.40 / 36.34s | 43.54 / 49.88s | 0.5–0.7 Mbps |
| quic | down | 2 / 3 | 5/8 | 8.50 / 6.20s | 8.90 / 7.49s | 9.77 / 9.07s | 2.0–2.7 Mbps |

**判定（§3.4 判据）**：`http2` 腿 **p50 明显更快**（上行 4.7–4.9s vs 23–33s，**5–7×**；下行 1.2–1.8×），
且 **8 连发无退化趋势**（run3 上行 3.3–9.4s 抖动，p50 仍 4.85s）；`quic` 腿**复现退化**且抖动极大
（p50 23s 而 max 43.5s）—— 与「ISP 对 QUIC(UDP/443) QoS 降质」的病灶签名一致。
⇒ **决策门 1：命中。** 默认取 `http2`/`edge-ip-version 4` 是对的（即 M1 缺省值）。

⚠ **绝对数不可直接换算每轮耗时**：客户端在本机，请求出一遍家宽、响应回一遍（§2.3 已声明）。
真轮次的传输量约 1.2MB 上 + 0.86MB 下 ⇒ 按本表 p50 约 6–8s（远低于门 1 的 20s）。
真实 payload 形状 + 宿主路径下的绝对值仍需云机确认。

### 实施中发现的坑（同批）

- **本机环境代理会杀死隧道探测**：本机设了 `HTTPS_PROXY=http://127.0.0.1:7890`，而 `NO_PROXY` 只含
  localhost/127.0.0.1/内网网段 ⇒ 打 `https://*.trycloudflare.com` 被丢进本地代理，拿回
  `SSL: UNEXPECTED_EOF_WHILE_READING`（而 cloudflared 日志里 `Registered tunnel connection` 一切正常，
  极易误判成「隧道坏了」）。探针客户端已固定 `ProxyHandler({})` 绕过；**云端 worker 没有这种代理**，
  所以按直连量才是对的。
- quick tunnel 的 URL 在 edge **注册完成前**就写进日志 ⇒ 必须先用 `/ping` 就绪门等它真开始服务，
  否则「连接没建好」会被记成「协议慢」，恰好污染本探针唯一要量的东西。

### B6（xz preset 3→6）已量、**不采用**（2026-09-17，本机，真 payload）

测量口径：`tmp/measure-xz-preset.py` —— 取 `tmp/x3-power/remote-jobs/*/payload.tar.xz`（2026-09-15 那次真
self-node push 训练留下的真 job），解包出真 shard 目录，再调**真** `pack_payload`（`PAYLOAD_XZ_PRESET`
按档位改）。3 份独立真 payload，每份 240 个 shard / 裸 ~270 MB。

| job | preset 3 体积 / 打包 | preset 6 体积 / 打包 | 体积 | 打包耗时 |
|---|---|---:|---:|---:|
| `05cb2d44acd0c2d0` | 1,198,096 B / 3.24s | 1,162,552 B / 9.44s | **−3.0%** | **+191%** (+6.20s) |
| `0cb44569e07c7890` | 1,187,392 B / 3.13s | 1,156,264 B / 9.35s | **−2.6%** | **+199%** (+6.22s) |
| `132f5e1cea7c4e16` | 1,227,432 B / 3.47s | 1,192,388 B / 10.00s | **−2.9%** | **+188%** (+6.53s) |

**判定：不动档位**（实测写进 `remote/protocol.py` 常量旁的注释，防止将来重测）。理由：省 ~34 KB，
而按隧道实测 ~3.5 Mbps 那 34 KB 只值 ~0.08s 传输，代价是**每轮关键路径 +6.3s CPU** —— 净亏，
且远低于计划 §4.2 的「收益 <10% 就别做」门槛。解包侧未量到收益（3 份均落在噪声内）。

**副产物：B1/B2/B4 在真数据上的独立验证**（不是合成 shard）：同一批 job 的原盘 payload
（含占位 `manifest.json` + `opt_init.tar.b64` + `init_weights.json`）为 2,045,276 / 2,034,536 / 2,074,996 B，
只打 shard 后为 1,198,096 / 1,187,392 / 1,227,432 B ⇒ **−41.4% / −41.6% / −40.8%**。

### 补记：瘦身开关进启动选项 + 一个真实指标缺口修复（2026-09-17）

**① M2 的 `slim` 现在能在启动训练时选**（计划 §1.4 要求「每个改动都有运行期开关」＋「启动时
提供选项」）。M1 的隧道选项走齐了一条链（config → console-state → route 白名单 → preset → UI →
显示当前生效值），而 `slim` 之前只落了 config 键 + 指标 ⇒ 想 A/B 只能手改 `rl-config.json`，
等于没有「启动时提供选项」这条路。现已补齐同一条链：

| 环节 | 位置 | 行为 |
|---|---|---|
| 类型/双域 | `core/types.ts` `SlimMode` + `CourseConf.slim` + `rl.slim` | UI/console-state 用 `'on'\|'off'`；rl-config **必须** `1\|0` |
| 解析 | `stack/specs.ts` `resolveSlim` / `slimToCfg` | per-course > `rl.*` > 缺省 **on**（与 python `_d("slim",1)` 同口径）；换算只此一个入口 |
| 生效值 | `api/state-view.ts` `modes.slim` | UI 显示「当前生效」，避免「以为改了其实没改」 |
| 契约 | `api/route.ts` preset 白名单 `on\|off`（非法 400） | 与 `mode`/`cfProtocol` 同写法 |
| 落库 | `actions/preset.ts` | `rl.slim = slimToCfg(opts.slim)`（数值域）+ console-state 存 UI 域 |
| UI | `TrainLaunchModal.tsx` 「瘦身」分段控件 | 开 / 关（A/B 对照）；上次选择进 localStorage；当前生效值上屏 |

**② 修的缺口：`wire.protocol` / `wire.edge_ip` 原本**恒为 null***。`_wire_from_result` 读的是
`getattr(args, "remote_cf_protocol", None)`，但 CLI **从未声明这两个参数**（`cf_protocol` 在整个
python 侧只出现在那两行读取处）⇒ M1 §1.4「开关取值必须写进 iteration 事件」实际没被满足：
控制台能显示「此刻生效值」，却回答不了「改用 http2 之后那几轮 vs 之前那几轮」。修法：
`rl/cli.py` 加 `--remote-cf-protocol` / `--remote-cf-edge-ip`（缺省取 rl-config，即控制台回写的键），
`rl/loop_steps.py` 新增 `_course_cf_tunnel(args)`（与 `_course_push_url` 同口径：CLI >
`courses.<stem>.cf_*` > `rl.cf_*` > None；选项住 rl-config，**永不进 curricula**，D14）。
测试 `nn-training/tests/test_wire_cf_tunnel.py`（12 例：CLI 声明与缺省、四级优先级、不串课、
旧 args/坏 config 不炸训练、端到端进 wire）。

> ⚠ 修正一条早前的说法：前文表格里「push 侧 B5」的实测与本次均为本机闭环；
> `wire.protocol` 现在才会真的非 null —— 在此之前「按协议分组统计」是做不到的。

### 未做（不写成已做）

- **M2 云机绝对值确认**（≥8 轮中位 `wire.up_sec`）**未跑**。
- **M3（rollout 上云）按 §5.1 门留档不做**：门 1 已命中（`http2` 把传输打到 ≪20s），门 2 要等云机绝对值，
  且 M2 之后上行只剩 ~1.2MB —— 按 `plan/kaggle-rollout-feasibility.md` 的算术已经是负交易。

### 教训

- **验收 harness 是「待修资产」时先修再量**：`smoke_loopback` 盘上没有近期运行痕迹，实际跑起来三处已腐坏（v2 schema 权重文件、21 列 vs 39 列的 METRICS_DIM、`blob_cache` 被 prune）。**它碰的正好是要改的函数** ⇒ 修它本身就是交付物，而不是绕开它。
- **新缓存目录必须同时改 prune 名单**：`blob_cache` 第一版每轮必 miss，就是因为 `prune_job_dirs` 只豁免 `code_cache`——「缓存命中率」类 bug 会伪装成「协议没生效」。
## §57 python 全量门禁提速：worker 数 × CPU 内线程数必须成对调（2026-09-17）

**背景**：用户问「检查 python 全量门禁，提高可维护性，减少耗时」。门禁墙钟实测 39~50s，而
ruff(~1s) / mypy(~4s) 完全藏在 pytest 后面 ⇒ 只有一个瓶颈：pytest。

**根因（推翻 `docs/goal-nn.progress.md §25` 的旧结论）**：旧默认 `-n 4` + torch 默认内线程
（= 物理核 16）⇒ 4 worker × 16 线程 = 64 线程抢 16 核，**严重超订**。§25 那条「n=4 最优、
auto=16 反更慢」正是这个假象的读数（worker 越多越慢本身就是超订证据），不是「torch import 开销」。

**实测**（16 核，`python -m pytest tests/ e2e/` 单独计时，同机交错 3 次/项）：

| 配置 | 均值墙钟 | 备注 |
|---|---|---|
| `-n 4` 默认线程 | 36.3s | 旧默认 |
| `-n 12` 默认线程 | 44.7s | worker 越多越慢（超订证据） |
| `-n 4` 线程=1 | 39.7s | 只封线程不救小并发 |
| **`-n 12` 线程=1** | **24.7s** | ← 新默认 |
| `-n 8` / `-n 16` / `auto` 线程=1 | 25.5 / 23.5 / 24.0s | 8~16 平坦 |

**改动**：① 门禁 export `OMP/MKL/OPENBLAS_NUM_THREADS`（`NN_GATE_THREADS`，默认 1，0 = 不设）
+ worker = `min(核数, 12)`（`NN_GATE_NPROC`；取 12 同时给内存封顶：峰值 pytest 进程树
RSS ≈ 3.9GB ≈ `-n 4` 的 3 倍）；② 三路工具启动去重成 `run_tool`（原先 LIVE/detach 二选一
复制了三遍，加一个工具就要再抄一遍，漏掉 detach 分支会让 Windows commit 卡死）；③ 去掉 pytest 的
重复 `-q`（addopts 已有 `-q` ⇒ 原本是 `-qq`，把结尾的「N passed in Xs」吞了，hook 日志里看不到
用例数与耗时）——现在日志里是 `1010 passed in 21.2s`；④ `t0` 提到启动工具之前，报告的秒数 = 门禁
真实墙钟（旧版只算「等最慢那个」）。

**同策推广**（防「门禁快、日常入口慢」的漂移）：`task.py` 四个 target 共用 `clean_env()`，线程封顶
只改一处即全生效，worker 一律 `-n auto`（**2026-09-15 把 `-n auto` 判为「沙箱 ~34% 停滞」头号嫌疑
是误判，本次回退**）；`nn-training/Makefile` 加 `NPROC ?= auto` / `THREADS ?= 1` + export；
CI `nn-training.yml` 加 job 级线程封顶，单测层从**无 `-n`**（job 里最长的 pytest 步）改为 `-n 2`
（与 e2e 同口径）。

**验证**：门禁连跑两次 20s / 23s rc=0（改前 39 / 50s）；日志含 `1010 passed in 21.2s`；ruff +
mypy 绿；`test_githook_scripts.py` 新增两条静态护栏（封线程 export + 核数派生 worker 数），并用
**变异测试证明非空转**（删 export / 退回 `-n 4` / 去掉上界 → 3/3 被抓住）；CI 侧封顶用
`taskset -c 0,1` 压成本机 2 vCPU 模拟（`-n 2`：默认 57/73s → 封顶 55/53s）。

**教训（通用形态）**：把「并行度不够」当结论之前，先看**每个进程内部**开了多少线程——
`-n`（进程数）× 库默认线程数（= 核数）是一对乘积，只调一半得到的结论会**反号**（本来该加 worker，
却得出「workers 越少越好」）。

## §56 hub-server 重启死锁收口：D9 只当「无效鉴权」的守门人 + 回环永不封禁 + 端口级实例锁（2026-09-17）

**为什么记这一笔**：本笔含**训练基础设施架构变更**（D9 鉴权/闭锁语义、hub 启动串行化），
按 §5 硬规则必须入账。事故现场：hub-server「自动崩溃后手动重启失败」，控制台只报「意外退出」。

### 一、事故链（三层同族问题，一次收口）

1. **封禁连坐**：旧 `_auth_ok` **先查 `is_blocked` 再验 token** ⇒ 一次误封（本机组件用陈旧 token
   连打 5 次 `/ping`）把该来源 IP 的**全部**流量（console 健康检查、训练循环、worker 拉活）403
   一小时；而封禁只住**进程内存**、只能靠重启清除。
2. **回环当替罪羊**：cloudflared 回源把**隧道流量也全归成 127.0.0.1** ⇒ 回环上的失败里混着隧道
   里的陌生来源，对回环封禁 = 整台机器的服务面连坐（用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）。
3. **重启被自己的守卫挡死**：端口守卫（`_port_guard.ensure_port_free`）是「探测 → bind」的 TOCTOU，
   且 Windows `SO_REUSEADDR` 允许双绑（后启动者静默变僵尸）；旧实例活着占着 8787 ⇒ 新实例被拒 ⇒
   **必须重启才能解封、重启却被自己占的端口挡死**，只能人工杀进程。

### 二、修复（四处）

- **D9 改序**（`remote/hub_server.py`）：先验 token；**合法 token 永远放行**，封禁只拒无效鉴权尝试
  （封禁期内的无效尝试 403，且不再计数/不延长）。
- **回环豁免**（`remote/hub_server.py::_is_loopback`）：`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1`
  上的失败**不计数、不封禁**（`is_blocked` 防御性恒 False）；鉴权边界与 `AUTH FAIL` 审计行不变。
- **原子实例锁**（新 `remote/_instance_lock.py`，`nn-training/.<kind>.<port>.lock`，按端口键控）：
  拿锁 → 端口探测 → bind；陈旧锁按「持有者已死 / 命令行缺本服务指纹（PID 复用）」接管（指纹可
  给多个），身份读不到则 fail-closed 拒启；锁文件**写不下**（只读 FS）则 fail-open + 响亮告警。
  覆盖 `hub_server` 与 **`worker_server`**（push 端口，经 `remote_worker_serve`，2026-09-17 补）——
  worker 僵尸更贵：HUB 会把 job POST 进一个没人应答的监听端口，表现为推送静默卡死。
  自带安全存活探测（Windows `GetExitCodeProcess`——**不复用** `train/loop_util._pid_alive`，后者
  在 Windows 走 `os.kill(pid,0)`＝`TerminateProcess` 会杀持有者；该隐患已于同日单独修掉）。
- **控制台侧**：启动前 `reclaimPort` 回收端口幸存者（`stack/hub.ts`，接在 hub/selfNode）；
  「停止 trainer」释放本课 run_rl/run_bc 锁且**先核验进程身份**再停存活持有者
  （`launch/cli.ts::releaseTrainerLock`）。**`workerServe` 路径故意不接 reclaimPort**：
  worker_server 可能在跑数小时的 PPO job，`/ping` 失败就回收它 = 炮掉在途 job；该路径
  会先复用健康的幸存者，不通时锁会响亮拒启并指向日志（含持有者 PID），交人工处置。

### 二补、隧道来源还原（B，用户点名「确认隧道来源到底该不该计数/封禁」）

**背景**：回环豁免（2 号修复）把隧道入口一并豁免了 —— cloudflared 回源把隧道流量全归成
`127.0.0.1`，于是隧道侧**只 401、不计数、不封禁**，等于对公网暴露面零封禁（D9 只对 tailnet 直连 IP 有效）。

**决定 = B**（方案对比与 C+D/E 被否的理由见 DECISIONS 同条）：`remote/hub_server.py::attributed_source(peer, cf)`
—— **只在「TCP 对端是回环」时**采信 `CF-Connecting-IP`（须是合法 IP 字面量且非回环值）⇒ 按**归因 IP**
计数/封禁；其余（无头 / 头非 IP / 头写回环值 / 对端非回环）⇒ 归因 TCP 对端。直连（tailnet）对端
**只认对端 IP**：那台机器能自己写任何头。审计行带上 `peer=` / `src=` / `via=cf|peer`。

**为什么不需要再加「全局退避闸」**（C+D，曾被列为首选）：退避的收益完全建立在「封禁不可用」之上，
B 一生效就重复了；而 B 若失效（头可伪造），最坏后果**两条都良性** —— ① 轮换头值 ⇒ 拿不到封禁，
退化为回环豁免（不会更差）；② 伪造 tailnet 某 worker 的 IP ⇒ 那只拒它的**无效鉴权尝试**，它带
正确 token 的请求照常放行（改序使然）⇒ 不是对合法对端的 DoS。零收益增量 + 要给每条合法路径加一条
延迟分支 ⇒ 不做。

**未实测假设（登记待验）**：CF 边缘**会覆写** `CF-Connecting-IP`。仓里无 CF 头读取先例、quick tunnel
无 ingress 配置、沙箱无网络 ⇒ 无法离线验证。**实测法（2 分钟）**：向隧道发一次带伪造头的无效鉴权
（`curl -H 'Authorization: Bearer wrong' -H 'CF-Connecting-IP: 203.0.113.7' https://<隧道>/ping`），看
`hub-server.out` 的 `AUTH FAIL` 行 `src=` 是伪造值（⇒ 可伪造，回退 = 删 `attributed_source` 的 cf 分支）
还是真实公网出口 IP（⇒ 假设成立）。**预登记的失效后果**见 DECISIONS 同条，两条都良性。

**访问日志也补上来源**（`log_message`）：回源流量原本全写成 `[hub-server 127.0.0.1]`，正是本次事故
排查的最大阻雾（分不清「本机组件」与「隧道里的陌生人」）；现在归因到 `via=cf` 时写成
`[hub-server 127.0.0.1 src=<真实 IP> via=cf]`，本机组件与直连对端逐字保持旧格式（不加噪）。
`log_message` 只打非常规事件，不刷屏；`self.headers is None` 的早期错误路径有护栏。

**回归**：`nn-training/tests/test_hub_auth_d9_order.py` 11 → **19 例**（归因矩阵 / 隧道源 5 次封禁第 6 次
403 / 被封归因 IP 持合法 token 仍放行 / 本机无头组件仍豁免 / 直连对端自带头不算数 / 伪造头无害 /
访问日志带 `src=` 及两个负例 / `headers is None` 不抛）。
A/B 行为取证（`tmp/cf-red-behavior.log`，detached worktree 跑修复前代码）：
`PRE-FIX: 5 次回环+CF头 无效鉴权返回码 = [401,401,401,401,401]`、`is_blocked(203.0.113.7) = False`、
`_auth_fail = {}` ⇒ 隧道入口确实零计数。

### 三、教训（可迁移）

- **「活着但不健康」的进程是重启链路的头号敌人**：所有自我守卫（端口/单实例锁）都必须能区分
  「真双开」与「上一代残骸」，否则守卫本身变成死锁的一环。判据顺序一律：**存活 → 身份核验 →
  接管/拒启**，身份读不到就 fail-closed 且**响亮打印**（不静默共存）。
- **惩罚性状态机只能惩罚「确定恶意」的那一类**：把合法流量与可疑流量放在同一个计数器里，
  在共享来源 IP（回源/代理/NAT）下必然误伤整机；封禁的作用面必须比鉴权边界**更窄**，不能更宽。
- **取证纪律**：401/403 永不静默（2026-09-16 已立），且审计行必须写「不计数/不封禁」这类**语义**
  说明——否则下一次排障会把「回环不封禁」误读成「封禁失灵」；同理，一旦「对端」不再是封禁对象，
  审计行必须同时打印 `peer=` 与 `src=`——只打一个会让「封的是谁」永远无法事后重建。
- **一次收紧会开出新的口子，必须回头补上**：改序（A）与回环豁免（B）都是「减误伤」，但 A 把封禁
  面从「来源 IP」缩到「无效尝试」、B 又豁免了隧道整整一侧 —— 每一步都对，合起来却让公网入口
  零封禁。这就是 B（2026-09-17 追加）存在的原因：**每次放宽都要重新问一遍「那么谁在守门」**。

### 三补、存活探测的 Windows 隐患收口（同日补修，用户点名）

- **隐患**：`train/loop_util.py::_pid_alive` 在 Windows 侧一直是裸 `os.kill(pid, 0)` —— Windows 上
  那不是探测而是 `TerminateProcess(handle, 0)`：`acquire_lock` 判「锁持有者还活着吗」的**只读查询**
  会直接把持有者杀掉（最坏：打死正在训练的 trainer），而 `except Exception → False` 还会把
  「我杀了它」记成「它本来就是死的」，双开护栏静默失效。`run_rl._runrl_pid_alive` 早期就因这条
  隐患不复用它（自己写了安全分支），loop_util 侧因此露了很久。
- **修**：两处同口径 —— Windows 分支走 `GetExitCodeProcess == STILL_ACTIVE`，并补 `pid <= 0 → 不活`
  护栏（POSIX 上 `os.kill(0, 0)` / `os.kill(-1, 0)` 命中**进程组**、实测成功，会把残缺锁文件里的
  0/-1 当成活人持有 ⇒ 同名课永久拒启）。三处同源：`loop_util` / `run_rl` / `remote._instance_lock`。
- **回归**：`nn-training/tests/test_pid_probe_windows_safe.py`（6 例）——注入假 kernel32 + 监视
  `os.kill`，断言 Windows 分支**零 os.kill**、退出码语义、句柄不泄漏、POSIX 分支不变、残缺锁可清理，
  外加一条行程门禁（三处探测必须保留 `os.name == "nt"` 分支）。
  A/B：修复前红 —— `AssertionError: train.loop_util._pid_alive: Windows 分支不得调用 os.kill，实际: [(pid, 0)]`。
  ⚠️ 坑：伪装 Windows 时 `os.name="nt"` 必须**只在探针调用期间**生效并先于异常还原，否则
  `pathlib` 会把路径解析成 `WindowsPath`，pytest 在报错/cache 阶段直接 INTERNALERROR。
- **同类待收口**：`remote/notebook_runtime.py::_pid_alive`、`tools/tmp-clean.py`（同名写法，未动）。

### 三补二、存活探测唯一化 + 隧道 metrics 端口闸（2026-09-17 第三批，用户点名的两个收口）

**存活探测唯一化**（`nn-training/pid_probe.py`，新增）：18 小时内同类隐患在 3 个不同文件各自
踩过一次——`loop_util` 裸 `os.kill`（会杀锁持有者）、`notebook_runtime` **嵌套闭包**+裸 `os.kill`
（不可测，且用来判断 bootstrap 已起的 `serve_pid` 是否还活 ⇒ 在 Windows 上会把 worker_server
直接杀掉）、`tmp-clean` 缺 `pid<=0`（残锁里的 0/-1 命中**进程组**⇒ `training_running()` 恒真 ⇒
运行目录永远不再收敛，实测 `_pid_alive(0) = True`）。根因面 = 「每加一个调用点就多一份可漂移的
实现」，故收敛为**唯一实现** `pid_probe.pid_alive`（stdlib-only 顶层模块，与 `platform_utils` 同层，
`remote/` 与 `train/` 都直接 import 它——两方向都不能反向依赖对方的包）；四份具名薄壳全部委托；
**唯一保留副本** = 仓根 `tools/tmp-clean.py`（根级开发工具不依赖 nn-training 布局），契约由源码门禁守。
门禁升级：`test_pid_probe_windows_safe.py` 现在把**六处入口**放进同一组断言，并用 AST 断言
`nn-training/` 里真调用 `os.kill(pid, 0)` 的文件**只有 `pid_probe.py`**（AST 而非字符串：新写的
docstring 到处在讨论这个坑，且 `os.kill(pid, 15)` 是**故意发的信号**、不属本不变量）。

**隧道 metrics 端口闸**（`dashboard/src/stack/hub.ts`）：cloudflared 是**第三方二进制**，没法在
它内部装实例锁（hub/worker 那层是 python 自己拿 `O_CREAT|O_EXCL`）⇒ 控制台侧回收是它**唯一**的
一道闸。`stepCloudflared` 在 spawn 前 `reclaimPort(metricsPort)`（与 hub/selfNode 同族），
堵住 `supersedeSlotTunnels` 看不见的幸存者（孤儿/登记丢失/控制台重启竞态）。metrics 端口既是
`--metrics` 的 bind 目标、又是 `/ready` 的探测目标，被占着会**同时**造成「新隧道 bind 失败」与
「就绪读数读自旧僵尸」；后者另加**就绪归属**：`core/proc.ts::portOwnedBy(pid, port)`
（唯一实现，早期叫 `tunnelOwnsMetrics`，因 hub/worker 也要用而改成中性名），就绪 = 「本进程持有
该端口 ∧ /ready 200」。

### 三补三、就绪归属推广到监督器（2026-09-17 第四批，用户点名「监督器那条路径也要」）

`ProcSpec` 新增可选字段 **`ownsResource?: (pid) => Promise<boolean>`**，三处声明：
`hubServerSpec`（hub 端口）、`cloudflaredSpec`（metrics 端口）、`workerServeSpec`（push 端口）；
**监督器**（`server.ts::restart`，变更检测重启）与**启动步骤**的就绪判定都变成
「`ownsResource`（未声明 = 不阻塞）∧ `healthy()`」，未就绪时日志点出归因。

为什么监督器同样需要：它杀旧 pid 后紧接着拉起同一条 spec，若新进程 bind 失败
（EADDRINUSE / python 侧双监听守卫拒绝）**早已退出**，而端口上的旧实例照样答 `/ping` / `/ready`
⇒ 监督器把**僵尸的 200 记成「重启成功」**：账本写新 pid、实际服务的是旧进程 —— 这正是
hub-server 重启事故的相位（账本 pid ≠ 真在服务的那一个）。

**实现时发现并修掉的语义漏洞**（记一笔，避免以后重蹈）：`portOwnedBy` 最初把「占用者清单为空」
一律当成「探测不可用 ⇒ 不判死」——但**没人监听**时 lsof/netstat 也返回空，于是「新进程已死」
会被判成「归属 OK」。现用 TCP 探测二次区分：没人监听 ⇒ **false**（要抓的就是这个）；
有人在监听但列不出归属 ⇒ **true**（工具缺失不该让所有组件启动失败）。测试里就有一个真监听
用例把它担住了（hub 端口无人监听 ⇒ `hub.ownsResource!(child.pid) === false`）。

**保留的刻意选择**：监督器重启路径**不接** `reclaimPort`（它杀的是账本里确切 pid、紧接着拉起
同一 spec，无孤儿窗口；kill 失败时回收也一样杀不掉）——只核归属。

### 四、验收

- `nn-training/tests/test_hub_auth_d9_order.py`（11 例）、`nn-training/tests/test_instance_lock.py`（9 例，
  含真进程顺序双启被拒 / 同时三启恰好存活一个）、`dashboard/tests/trainer-lock-release.test.ts`（13 例）。
- A/B 取证：detached worktree 对 HEAD 跑新测试 → 红（`assert 403 == 200`；日志里 127.0.0.1 被 BLOCKED）。
- 第三/四批（存活探测/隧道闸/就绪归属）：`test_pid_probe_windows_safe.py` 6 → **8 例**、
  `dashboard/tests/training-port-reclaim.test.ts` 6 → **15 例**（含 `ownsResource` 声明门禁、
  监督器接线门禁、`portOwnedBy` 空清单两义、真监听下 `hub.ownsResource` 判 false）；A/B 红：worktree 对 HEAD 跑新测
  6 红（含行为级 `tmp-clean._pid_alive(0) = True`），cloudflared 侧 HEAD 上既无 `reclaimPort`
  也无 `tunnelOwnsMetrics`、就绪判定是裸 `tunnelEdgeReady`。门禁：nn python gate（205 源文件）✓、
  `cd dashboard && bun run typecheck && bun run test`（406 例）✓、`bun run check`（1849 例）✓。
  ⚠️ 本批 gate **首跑红过一次**：`tests/test_remote_ppo.py::test_hub_server_auth_and_job_lifecycle`
  报 `Con…`（连接错误）——单跑该文件绿、`--maxfail=99` 单跑绿、重跑全量 gate 也绿 ⇒ 满编 `-n 4`
  下的**负载型 flake**（同 §313 已归档的那一类），与本次改动无关（日志已删，仅存档此判定）。
- 门禁：`bash tools/githook/nn-python-gate.sh` ✓；`cd dashboard && bun run typecheck && bun run test` ✓；
  `bun run check` ✓。决策记录：DECISIONS §2026-09-17-hub-restart-deadlock-hardening。


## §56 x1-rebirth-a2 结课：从零臂达 **100% / 0 伤**；「位移不足」归因被验证（2026-09-17）

**为什么记这一笔**：这是本项目**第一次从随机初始化出发、不靠蒸馏**把一条 ladder 腿打到满分，
且**承伤压到近零**。更重要的是它把 §55 留下的问题（x1-rebirth 为何 42 轮无进展）**干净地答掉了**：
不是奖励权重不对，是**每轮位移太小**。这条结论会影响后续所有从零臂的设计。

### 一、结果

| 口径 | 起点（scratch） | **a2 @ it45（evaluated）** | 对照 |
|---|---|---|---|
| **eval argmax**（部署口径） | 0.05 | **1.0000** | God AI **0.95** / it333(BC) 1.00 |
| 训练内 rollout wr | 0.096 | **1.0000**（it42–46 连续 5 轮满分） | — |
| **承伤 dmg/局** | **81.3** | **0.877**（`phits=0.0000`） | God AI **55.4** / it333 3.8 |
| ticks/局 | 1432 | **227** | God 439 / it333 360 |
| shots/局 | 22.8 | **3.18** | it333 5.3 |

⇒ **同时达成满分与近零承伤**：「0 伤」这条任务定义（§55 记的用户裁定）**第一次真正落地**。
⇒ 承伤比 God AI 低 **60 倍**以上 —— 这不是「追平规则教师」，是**在教师做不到的维度上超过它**。

### 二、它验证了什么（x1-rebirth 的失败归因）

x1-rebirth 跑 42 轮后**衰退**（it19 峰值 0.168 → it42 0.063），当时定位到三个疑点：
奖励权重失衡 / 缺朝向信号 / 位移太小。a2 用**单侧改动**（放大位移 + 修奖励的边际比）证伪了前两个是主因。

**位移证据（三条，全部同向）**：
| 指标 | x1 | **a2** |
|---|---|---|
| KL（轮内位移） | mean 0.0029 | **mean 0.0059**（max 0.00919） |
| entropy 降幅 | **5% / 42 轮** | **44.65% / 46 轮** |
| 移动分布归一化熵 | 0.9852（19 轮，几乎没动） | **0.919–0.946**（明显出峰） |

⚠ **反向警示**：KL **自始至终没进过 0.01–0.03 那条「目标带」**（46 轮 0 次）。
⇒ 那个带是**拍出来的、错误的**：0.005 量级 + 行为分布成形就已足够。
（教训：不要用「某个看起来合理的区间」当硬判据；用**行为分布是否出峰**当判据。）

### 三、三条可复用的方法论（比结果更值钱）

1. **「排序正确」≠「行为会照排序走」**。x1 的奖励梯度实测是单调正确的
   （0伤通关 +5.35 > 换血 +4.22 > 冒险 −2.25 > 苟 −3.0 > 死亡 −5.75），
   但策略仍学会苟 —— 因为按它当时的技能，冒险 EV ≈ `0.05×5.35 + 0.95×(−5.0) = −4.48`
   **差于**确定的苟 −3.0。**策略走的是「技能条件下的期望」，不是设计者的收益排序。**
   ⇒ 任何奖励设计都必须按**当前技能**算 EV，而不是按「学会之后」算。
2. **奖励权重必须用真实语料标定**。初版 `wDmg=0.3` 是按 `dmg≈3.77/局`（BC 的数）标的，
   而真实语料实测 `dmg≈99.7/局` ⇒ 承伤项被放大到 **−29.9**，是所有正项之和的 **22 倍**，
   把全部正信号压平。⇒ **不能从别的策略的数外推**。
3. **杠杆是「边际收益/成本之比」，不是终局项**。让「走」成为最优的那一步是实测
   「每多走一格」的承伤成本 ≈ **+2.39 dmg/格**：v3 下 `+0.1 收益 vs −0.12 成本` ⇒ 每格净亏 0.02
   ⇒ 策略最优是「坐」；把 `wExplore 0.1→0.2`、`wDmg 0.05→0.03` 后边际转正为 **+0.13/格**
   ⇒ EV 表翻转为 **走 +3.98 > 坐 +2.80 > 莽 −0.89**。
   期间曾误以为「调 timeout」能翻转 —— **不能**，timeout 只是常数项，改不动边际比。

### 四、a2 的最终配置（可作从零臂模板）

```
起点      bc=scratch-init.json（seed 7，非 BC），kickstart_ref=false，warmup=0
位移包    kl_coef 0.05→0.02、lr 3e-4→5e-4、epochs 4→6   ← 本腿的主假设
奖励包    wKill 3.0 / wHit 0.6 / wWin 2.0 / wDmg 0.03 / wShot 0.05 / wExplore 0.2
终局      lives_exhausted −5.0 / timeout −2.0
采集      target_transitions=24000（严格样本量配额，steps 恒定）
```

### 五、局限（不要外推）

- **本关是 1 敌关**（ladder-c01，每关 1 个 basic/power 敌、max_ticks 1500）。
  「100% / 0 伤」是在**这个难度**上取得的，**不可外推到多敌关**。
- `cellsVisited` 是**半解**：它奖励「走到没去过的格子」，不奖励「朝敌人走」
  （39 个 metric 里没有距离/朝向类）。本腿能成，是因为在 1 敌关里「到处走」足够碰到敌人。
  多敌关是否需要真正的朝向信号，**尚未验证**。
- ⚠ `metrics_stats.jsonl` 的 `mean` 是 **per-step**，不是 per-game；与 eval jsonl / shard 的
  局末累计**不可直接比**。本腿中曾因此误判「cells 下降」与「wShot 生效」两次。

### 附：rollout 开销的两个零风险实测（随本腿顺带做，2026-09-17）

> 详版方案文档**未签入**（用户要求）。以下保留结论，避免随文档一起丢失。

- **obs 压缩比 326×**（zlib-9 与 gzip-9 同量级；**零字节占 98.0%**）：
  每局 obs 0.41MB → **≈1.3KB**；后期态整轮 1043 局 0.43GB → ≈1.3MB。
- **6/16 obs 通道恒零**（通道 0/2/3/4/5/12），另 3 个近死（9/13/15，<0.1%）。
  **不是 bug**：`src/nn/obs-encoder.ts:106` 的 v3 变更说明提到「冰面横向速度」等
  **关卡特性通道**，而 ladder-c01 是**空场无冰面/无传送带** ⇒ 这些通道天然为空，
  换到带地形的关卡会活起来。
  ⇒ **砍通道要 bump `OBS_SCHEMA_MAJOR` = 全部历史语料与归档权重作废** ⇒ 不值得，
  记入「下次 schema 升级的输入」。
- **背景**（为什么值得看）：rollout 占每轮墙钟 **42%**（a2 实测 2830s/6731s），
  而 **GPU 是常开 notebook** ⇒ 那段时间在烧配额。
  ⚠ 且**减 `target_transitions` 不省总时间**——总墙钟 = 轮数 × 每轮墙钟，
  rollout 与 ppo **都正比于 target** ⇒ 总墙钟 ∝ 总样本量。
  ⇒ **唯一有效方向是「提高并行度」或「降低每局开销」**。
- **另一个更值钱的方向**（零代码）：`it46` 的 rollout 节点分布是
  self 399 / mac 200 / **a95 6 / a97 6** ⇒ **本机承担 98%，两个远端节点各只跑 1%**。
  修好它们即 **2×**（省 ~21% 总墙钟），性价比高于任何代码改动。

---

## §55 x1-rebirth 开课（纯从零臂）＋ 严格样本量配额机制落地（2026-09-17）

**为什么记这一笔**：本笔含**训练架构变更**（采集配额的执行语义），按 §5 硬规则必须入账；
同时记录新腿「从零 ＋ 0 伤」这个**从未试过的新基准**，以及其奖励梯度的实测标定。

### 一、严格样本量配额（`target_transitions` 路线的执行侧，方案 A）

- **动机**：`seed_rotate` 控的是**局数**，而局时长随训练剧烈变化（随机态磨到超时
  ≈121 samples/局，学会速通后 ≈36 samples/局）⇒ 固定局数会让每轮样本量漂移 3× 以上，
  学习曲线被采样量本身污染。改为按 **samples** 配额后仍只剩「过冲几个百分点」的抖动
  （采集按整局补波）。**再往下压到严格相等**的收益是：`n` 恒定 ⇒ `n % mb` 恒定
  ⇒ **所有 minibatch 的 shape 恒定**（TPU 侧无 host 抖动）。
- **实现（新增 `per_stage_quota`）**：`ppo/common.trim_shard_arrays()` +
  `load_episodes_common(per_stage_quota=)`。三条不变量：
  1. **截断在 GAE 之前** —— GAE 反向递推，先算完再丢尾部会让保留段的 adv/ret 全错；
  2. **截断处不改 `done`** —— 原为 0 时 `compute_gae` 自然用 value bootstrap
     （标准 truncated-episode 处理）；改成 1 等于假装局已结束、低估剩余回报；
  3. **逐关而非全局** —— 全局按序截断会把排在后面的关整关丢掉，破坏
     「短局关淹不了长局关」的分关独立达标不变量。采集侧补波与训练侧截断共用同一个
     `volume_waves.target_per_stage`，两侧不会漂。
- **全链**：`ppo/engine.py`（`load_shard` 带 stage、`--per-stage-quota`、update 模式）→
  `rl/loop_steps.py`（`_per_stage_quota()`，**自带 mode 门**使 serial 与 remote 不会一个
  gate 一个不 gate）→ `remote/{protocol,hub_client,worker}.py`（manifest 字段 + 校验，
  **先挡 bool** 因为 bool 是 int 子类 + worker 接线）。
- **新增共享解析器** `volume_waves.parse_stages_arg()`：采集侧（`_volume_stages`，失败
  **响亮 SystemExit**）与训练侧（`_per_stage_quota`，失败**静默返 0 = 全收**）策略仍不同
  （有意的），但「什么算可解析」从此同源 —— 消除「采集说合法、训练说非法」的裂缝。
- **单测** `tests/test_ppo_quota.py`（4）：trim 边界（0 维字段不切 / `keep≥N` 零拷贝 /
  `keep≤0` 空）；逐关精确（**GAE 收到截断后的长度** ⇒ 证明截在 GAE 之前）；
  配额满丢整 shard 且不跨关借；`quota=0` 全收且 episode 字段集不含 `stage`。
  ⚠ 测试用**旁路 meta** 记录「episode ↔ stage」而不是 `zip` 位置对齐 —— 丢 shard 时位置会错。

### 二、x1-rebirth：纯从零臂（scratch init，不蒸馏）

- **立项依据**（§47/§52/§54 已记）：教师 God-AI 在 `arena2-acbc` 只有 66.25%；
  BC 丢失教师「先打 power」（首命中 power 33.3% → 0.5%）；七条腿 KL 全锁 0.0018–0.0027
  ⇒ **从零是唯一能离开「教师吸引域」的路径**。（另注：goal-nn §5 早就定过同路线，
  §10 的 S1 过门是唯一先例 —— 但那是 **3 命 1 星**，学到的其实是「莽」，
  到 x10 冲不上去 ⇒ **不可当可行性证据**。）
- **初始化**：`scripts/init_scratch_weights.py --seed 7`（trunk×0.1 / 头×0.01 / value×0.1）。
  朴素 kaiming 会让 logits 随机即 ±2000 ⇒ 熵≈0、首个更新自锁（实测 kl=11930 后恒 0）。
- **`kickstart_ref=false`**：它是相对 **`bc`** 的 KL 锚，而本腿 `bc` 就是随机策略 ——
  开着等于把策略钉在噪声上，且恰在 it1–30 探索窗口最猛。
- **「0 伤」＝任务定义**（用户 2026-09-17 拍板）：奖励加 `-wDmg*playerDamageTaken`
  （**唯一正确的承伤信号**；历史 `wDmg` 一直挂在死项 `playerHits` 上 ⇒ 从未真正惩罚挨打）
  与 `-wShot*playerShots`（反「原地狂射」：随机态 20.2 发/局 vs 学会后 5.26 发/局），
  terminal 梯度 `lives_exhausted=-5.0` / `timeout=-3.0`。
  **实测梯度**（`build_reward_fn` 真跑）：
  `0伤通关 +5.35 > 换血通关 +4.22 > 冒险 −2.25 > 苟(超时) −3.0 > 苟(狂射) −3.79 > 死亡 −5.75`。
  标定过程中的一个纠错：`timeout=-2.0` 时是 `苟(−2.0) > 冒险(−2.25)` ⇒ **保守仍占优**，
  −3.0 是由该梯度反推（`|timeout| > 2.25`）而非拍脑袋。
- **位移旋钮**：`lr 1.5e-4 → 3e-4`（goal-nn §7 与 scratch init 配套标定值）＋
  `kl_coef 0.2 → 0.05`（S1 训练期 KL≈0.03，是本项目 BC 起点 0.002 的 15 倍）。
  两处都要改（顶层 `lr` 与 `ppo_schedule` 第一段，运行时以 schedule 为准）。
  ⚠ `kl_cap` 在 remote/serial 路径**未接线**，别拿它当护栏；有效的位移旋钮只有 `kl_coef`。
- **起跑线实测**（200 局 / seed0 860001 / ladder-c01）：随机 **8/200=4.0%**（dmg 18508）
  ｜ God AI **190/200=95.0%**（dmg 11088）｜ it333 **200/200=100.0%**（dmg 754）
  ⇒ x1 对 NN 已被打穿 ⇒ **判据不能只看 wr**（会撞 100% 天花板），要看承伤/速度的连续量。
  另：ladder-c01 **每关只有 1 个敌人**（c 关纯 power）⇒ **「首命中 power」在 x1 上恒为 power、
  零判别力**，power 行为验证必须**升档到多敌关**（acbc）才成立。
- **状态**：工程就绪，**未开训**。另发现 `nn-training/weights/ladder-c01-bc/` **目录不存在**
  （`ladder-c01.jsonc` 的 bc 缺失 ⇒ 该课程当前开不了跑），待补。

---

## §54 T5 x3-credit-p6 结课：双 run 一致判负；奖励重定价路线关闭（2026-09-16）

**为什么记这一笔**：T5 主剂量臂（power 杀信用 2×）两 run 跑满＋800-verdict，冻结判决表
（课程文件头 §4，tie-break 加固版）给出二进制结论。这是结课存档：主端点/双池/指纹/
残差桶旁证一次落齐；§53 本体不动（账本只增不改）。

**跑的是什么**：起点 `x3-start.it333`；相对 x3-start 唯一训练变量＝分敌种杀信用向量
（power 6.0 其余 3.0，残差桶补 bomb 未归因击杀）；`ladder-c03`；紧缰死段日程沿用；
30 轮/run。run1 训练 16:41→21:26（`run_start`×1，`iter_error`×2 均为 it1 hub 超时、
后恢复，`run_complete` 正常收官）；run2 21:29→22:33（`iter_error`×0，干净收官）。
终点 `nn-training/weights/x3-credit-p6/x3-credit-p6.it30.20260916-212523.json` ＋
`nn-training/weights/x3-credit-p6-r2/x3-credit-p6-r2.it30.20260916-223307.json`
（各 it1–it30 齐）。KL max 0.00218 / 0.00240（死水，符合设计）；rollout wr 均值
0.678/0.673、无趋势；日常 eval 锚点 0.65→0.67 / 0.67。

**主端点（power 曝光归一命中/千tick，判线 Δ≥+0.7≈4σ 且配对单侧 p<0.05，两 run 都过）**：
base 3.67±0.13 → run1 **3.59（Δ−0.08，z=−0.45）** / run2 **3.64（Δ−0.03，z=−0.18）**——
两 run 远未达线且方向为负，**主判据明确失败**（无需再算配对 p）。

**指纹旁证**（`phase0-fingerprints.ts`，口径同 T3；verdict 800 局 seed 400600 段）：
killer=power 61.1%→61.7%/65.7%（r2 +4.6pp，z=+1.02，Wilson 重叠，噪声）；
首命中 power 0.1%→0.3%/**0.0%**；C 0.555→0.567/0.560；归一命中份额
15.7%→15.7%/15.8%；**转化率三臂逐位相同 49.6%**。胜/败条件化不动
（败局 power 命中 0.32→0.41/0.25 vs 胜局 4.31→4.21/4.35）。

**胜率与 pd（二级/诊断尺）**：800 配对 run1 576 vs 577（Δ−0.125pp，28/29，
pd 7.12%，p=0.60）、run2 572 vs 577（Δ−0.625pp，33/38，pd 8.88%，p=0.76）；
锚点 200 对两 run 均为 134 vs 130（+2pp，pd 9.0%/7.0%，p 0.24/0.21）。
四条 pd 全落 5–10% 灰度带 ⇒ 按预注册「灰度带不单独判决」取不显著侧；
胜率门（Δ≥+5pp & p<0.05）两 run 均 FAIL。分关只诊断（run1 四关 ±0.5pp 内；
run2 abc−3/bcd−3 vs abd+2/acd+1.5，散布无方向）。

**残差桶旁证**（评审 P0，课程结算节要求）：`kills−ΣkillsByKind` 缺失
1.46%/1.92%/1.20%，不守恒局 3.00%/3.62%/2.62%，最大单局 2——与评审预测
（~1.4% 击杀/~2.5% 局）吻合，**单变量纯度成立**；已知残留（bomb 杀 power 无溢价）
量级不足以解释主端点的 −0.08/−0.03。总量 kills/eHits/dmg 三臂 <1% 波动；
shots −7.2%/−3.3% 而命中率 45.0%→48.2%/46.4% 反升＝"少放空枪"，**熔断不触发**。

**判决（冻结表 §4）**：**判负**。且是"可信的零"：两 run 内部一致度极高
（pass 差 0.5pp、主端点差 0.05）——与 T6（三臂散布 2pp）对比，本轮没有 run 噪声
掩盖效应的问题。解释：**不是信用方向错，是信用没转化成位移**；灰度带不触发
加剂量（wKillPower 3× 不开）。

**元层面（四腿七 run 连续零）**：x3-power / x3-step / x3-chip×3 / x3-credit×2 的
KL max 全部落在 **0.0018–0.0027 窄带**——无论改奖励哪一部分，策略位移同一量级 ⇒
**奖励重定价路线在 `ladder-c03` 上关闭**（DECISIONS
§2026-09-16-goalnn-t5-credit-negative）；pd≈8% 记为本分布噪声地板
（5–10% 带无判别力）。**后继唯一未试方向＝单旋钮放缰 A**（无奖励改动，
KL≥0.005＋pd 过地板为产出，待批准；用户手工开训）；若 A 后主端点仍不动则
执行器天花板分支单立项。

---

## §53 metrics v6 落地：分敌种命中/击杀列（idx31–38，TS+Python 全链，2026-09-16）

**为什么记这一笔**：T5（分敌杀信用）的工程前提（x3-credit-p6 课程文件头「工程前提」
节明示）。§52 判 T6 全面阴性后，T5 主路径依赖本条落地；观测/模型/encoder 未动
（x3-power-followup §52 禁令维持）——本条是**采集通道加宽**，非奖励语义变更。

**落地内容**（plan/t5-metrics-v6.plan.md，全部尾部追加、0–30 列号永久不动）：
- `METRICS_DIM` 31→39，`METRICS_VERSION` 5→6（TS/Python 双侧同源，manifest 与
  summary 均改由常量导出，消除硬编码副本）；idx31–34=kills{Basic,Fast,Power,Armor}
  （只记 `tank_destroyed by=player` 打敌车），idx35–38=hits{同序}（`enemy_hit.targetKind`，
  含致死命中）；列序 = `ENEMY_KIND_ORDER = [basic, fast, power, armor]`。
- 验证三层：跨语言 SSOT 断言（两侧列名/idx 互锁）＋新 `tests/sim/metrics-v6-census.test.ts`
  （独立重实现对账＋守恒 sum(killsByKind)==玩家击杀敌车＋同 seed 双跑确定性）＋
  golden 复用零填充（v6 尾列对 v7 公式零贡献，不重铸 oracle）。
- 新课程 `x3-credit-p6` / `x3-credit-p6-r2`（power 杀信用 2× 主臂 ×2 独立 run，
  §52 新规下限；单变量 vs x3-start；verdict 规格已冻结在课程文件头，
  **待用户手工开训**）。首次开训触发采样节点 codeHash 重编译，属预期。
- ★ 残差桶（评审 P0，方案 A）：bomb 清屏（`SimulationPowerUps.ts:391`）不推
  `tank_destroyed` ⇒ 四桶之和恒 ≤ 标量 `kills` 列（God AI 长局缺 11.6%）。
  公式末尾带 `wKillBasic*(kills − ΣkillsByKind)`——全 3.0 剂量下与 x3-start
  标量公式逐字等价（golden 6 局对账 |Δ|=0），单变量纯度保住；已知残留
  （桶按 basic 计价，bomb 杀 power 无溢价）记在课程文件与结算模板。
- 教训（本条勘误位）：kills 包络上界 40 是单关 2× 余量——多关合一 episode 的
  课程形态须重估（caveat 注释已落在 `reward_validation.py` DEFAULT_RANGES）。

---


## §52 T6 剂量定案：全噪音；run 噪声地板≈2pp 实测；T5 开工条件（2026-09-16）

**为什么记这一笔**：k0 真复刻落 569/800，四臂凑齐。这是 T6 终判账（课程三文件
剂量行同步；DECISIONS §2026-09-16-goalnn-run-noise-floor：地板规则＋wChip 判死）。
k05/k10 结算里的"待定"至此全部兑现，§51 本体不动（账本只增不改）。

**四臂定案表**（同批配对；k0 干净：单 run_start 零 error，eval 满血，KL 死水）：

| 臂 | 胜率 | Δ vs BC | dmg | kills | shots | pd | power/千tick |
|---|---|---|---|---|---|---|---|
| BC it333 | 576 (72.00%) | — | 124.24 | 2.411 | 14.55 | — | 3.76 |
| x3-power（旧 k0） | 553 (69.13%) | −2.875pp | 124.8 | 2.357 | 14.07 | 5.9% | — |
| k0 真复刻 | 569 (71.13%) | −0.875pp | 126.14 | 2.394 | 13.65 | 5.63% | 3.73 |
| k05 | 577 (72.13%) | +0.125pp | 122.18 | 2.409 | 13.01 | 8.13% | 3.73 |
| k10 | 558 (69.75%) | −2.25pp | 121.66 | 2.362 | 14.50 | 8.0% | 3.67 |

配对：k05−k0 +1.0pp（41/33，p=0.21/0.42，null）；k0−power +2.0pp（39/23，
单侧 0.028/双侧 0.056，定义边界）；k0 锚点 pd=5.5%（6/5，与 x3-step 逐数相同）。

**定案三条**：
1. **T6 全面判阴**：dmg/kill 四臂 51.5/52.9/52.7/50.7/51.5 无方向；指纹四臂复印
   （败局 power 命中 0.5–0.8，首命中恒 ~0）；T6 字面叙事（少挨打）证伪——k0 的
   dmg 反而最高（126.14）。"第二半裂"不成立（判例绑定＋shots t=−0.24＋日常反向）。
2. **+3pp 归档为 run 噪声**：复刻对（x3-power/k0，同配方/同起点/同超参/同批种子）
   自己就差 **2.0pp 且配对显著（p=0.028）** ⇒ run sd≈2.0/√2≈1.4–1.5pp；
   +3pp 相对 run 噪声 ~1.5σ；k05−k0 仅 +1.0pp（null）。H1 降级为不可测
   （≤run 本底）；倒 U 不需要假设。**教训：配对检验消对局噪声，
   不消 run 噪声**——纯噪声对 shots 配对 t=−2.46 照样"显著"，教科书 demo。
3. **方法论战利品**：单 run 总 SE≈√(1.6²+1.5²)≈2.2pp ⇒ 现有 +5pp 门线安全
   （5＞2×1.5＋余量，不动）；tie-break 六连 3:3（0.620/0.577/0.597/0.580/
   0.603/0.597 vs 0.60 门）⇒ **T5 立项必须去 C 门**（直接看首命中分布，
   全臂 0.0–0.5% 无争议），没有"以后再说"。

**§46/§50 caveat（只增旧账不动字）**：两条判负的幅度未经重复 run 验证
（x3-power −2.875pp 与 k0 −0.875pp 同配方可差 2pp）；判负结论本身不动
（配对显著＋方向一致），禁外推"有害/有益"，禁拿单 run p 值做因果断言
（McNemar/配对 t 一视同仁）。

**T5 开工条件**：① tie-break 加固（去 C 门）；② 冻结指纹口径
（`plan/t5-endpoint.plan.md`）维持；③ 关键臂自带 ≥2 run（新规：声称效应 <2pp
须跨 run 预注册 pooling，否则结算标「未过噪声地板」）；④ 路一 A 回 backlog
最底层（地板是 run 噪声不是 KL——证能动不解决测 ±2pp，门槛须按 run 本底重算）。

---

## §51 x3-chip-k05 结课：预注册池判阴，单变量池＋3pp 待 k0 定案（2026-09-16）

**为什么记这一笔**：T6 wChip 生存腿主臂（wChip=0.005）30/30 跑满＋800-verdict。
本条是结课存档（课程文件结算节同步）：预注册池给二进制结论，单变量池给待定结论，
"待定"二字是写给未来的门——+3pp 要转正必须过 k0，见下。

**跑的是什么**：起点 `x3-start.it333`；相对 x3-power 唯一训练变量＝奖励加项
`-wChip*playerDamageTaken`（wChip=0.005）；`ladder-c03`；紧缰死段日程沿用；
训练 09:03:55→10:35:17，run_start×4，iter_error×0，run_complete 正常收官；
终点 `nn-training/weights/x3-chip-k05/x3-chip-k05.it30.20260916-103347.json`
（wver `6896d65c1b40c44b`）。KL mean 0.001304 / max 0.002048（死水，符合设计）；
rollout wr 0.61–0.75 无趋势；日常 eval 锚点 0.650→0.675（+2.5pp 噪声内）。

**预注册池（vs 冻结 BC 基线 576/800）：判阴，干净零结果**：
终点 **577/800=72.125%**（Δ=+0.125pp；b01=33/b10=32，pd=8.13%，McNemar 单侧
p=0.5000/双侧 p=1.0000，完美对称噪声；分关全 ±1pp 内）。
dmg 124.24→122.18（−1.66%，≈0.6σ）；kills 2.411→2.409 持平；eHits 持平；
dmg/kill 51.5→50.7（−1.6%，bootstrap CI 跨 0）；shots 14.55→13.01（−10.6%，
唯一显著——但 kills/eHits 全平、命中率 45.8%→51.4% 反升，定性"少放空枪"，
非 s-dodge 式开火崩；日常口径 −4.7% 互证）。
⇒ **主判据未达标**（只记二进制，不写"字面成立"）；**熔断不触发**
（实质重于字面，噪声带执行）。

**单变量池（vs x3-power it30 553/800，同批同种子真对照）：＋3.0pp，判待定**：
**577 vs 553，Δ=+24/800=+3.000pp**；b01=54/b10=30，pd=10.50%，McNemar 单侧
p=**0.0058**/双侧 p=0.0116；**四关同向**（abc +1.5 / abd +3.0 / acd +4.0 /
bcd +3.5pp，目标关涨最多）。配对合法性：800 行配对键零失配；两批独立复跑的
基线 800 行×9 字段逐字段零差异（dmg 合计同 99392；course_fp 不同仅系课程文件
sha，与环境无关）。另：锚点 200 对 b01=13/b10=8，**pd=10.5%**（x3 系第一条过
10% 门的腿——动了，方向待定）。

**机制诚实话**：承伤从未下降（BC→power +0.4%，power→k05 −2.1% p=0.23）⇒
**T6 字面叙事「惩罚挨打→少挨打」未兑现**；唯一稳定改变的是开火频率
（kills/eHits 不降反升）；无 wChip 的 30 轮 PPO 全面退化而 k05 回到 BC 水平 ⇒
候选解释 H1「wChip 系住漂移的 PPO（防退化锚）」vs H0「x3-power 那次是运气差」，
**1 run vs 1 run 分不开 ⇒ 定案条件唯一：k0 同批复刻**
（k0≈553 ⇒ H1；k0≈576 ⇒ H0；落中间 ⇒ 看 k10 剂量斜率）。
30 局"退避" subgroup（shots −42%）系按结局选样，只作描述性旁证、充 k10
watch-item，不进判决（tripwire 只认全局配对数）。

**指纹旁证**（`tmp/x3-chip-k05/fingerprints-report.md`，census 配对）：四指纹逐值重合
（killer power 65.0% vs 64.4%、首命中 power 0.0% vs 0.1%、power 3.73 vs 3.76/
千tick、转化 49.3% vs 49.4%、败局 power 命中 0.49 vs 0.58）——开火方向没动；
分支判 F1 单独→T6 系已知门限硬币（§50 补记），不改判任何东西。

**后继（本条冻结时 k10 在跑 it9，k0 未开）**：k10 按 tripwire 跑完（shots 再下台阶且
kills 跟掉即熔断）；k10 收官→重启 eval 节点（对齐 epoch，k10 腿内缺口已声明不修）→
开 k0（串行；锁与目录隔离允许并行，但算力账不支持——采集顶满＋PPO 排队，
并行两头都慢，串行先拿 k10 答案）；三臂剂量表定案后立 DECISIONS 条目
（本条不同时立案：结局未定，早产不写）。
k0 按需条件已触发（单变量池待定），不再封存。T5 仍是主路径，不受本条影响。

---

## §50 x3-step it30 结课：可测性未恢复（工厂第二段复证）；主路径仍 T5（2026-09-16）

**为什么记这一笔**：课程文件状态是 **HOLD / 禁止开训**（2026-09-15 评审三项 P0 注销），
但 2026-09-16 经用户拍板**授权照原工厂第二段跑满 30 轮**（非误开，追认；仍非重设计 A/B）。
本条只结算**实际跑的那一处理**，不把结果外推到重设计草稿 A/B（单旋钮未测）。

**跑的是什么**（与 HOLD 期否决稿逐字同构，非重设计）：
- 起点 `x3-start.it333`；奖励 `wKill=4.0 / wHit=0.15 / wWin=2.0`；`ladder-c03`；30 轮；
- 日程 it1–20 `kl_coef=0.2, lr=1.5e-4` → it21–30 `kl_coef=0.03, lr=5e-5`（复合处理：缰绳÷6.7 且步长÷3）；
- `kl_cap` 键在配置里但 **remote 路径不接线**（§2026-09-15-goalnn-kl-cap-unwired），it1–30 无硬顶；
- `target_transitions` 缺席 = 默认关（动态采集）。

**主端点（预注册风格：it21–30 KL mean/max vs x3-power 同窗）**：

| 窗 | x3-step | x3-power | 比值 |
|---|---|---|---|
| it21–30 mean | **0.001940** | 0.001508 | **1.286×** |
| it21–30 max | **0.002665** | 0.002313 | **1.152×** |
| it1–20 mean（双侧同紧缰对照） | 0.001293 | 0.001171 | 1.105× |

按重设计草稿三支：**≤1.3× ⇒ 步长/缰绳无辜，问题在信号（奖励/信用）或执行器**。
未到「1.3–2× 部分放大」，更远未到「≥2× 可测性恢复」。`pd` 由日常 eval 离线配对补齐：
it0 vs it30 锚点 **200 对 b01=7 / b10=4，pd=5.5%，Δ=+1.5pp**（分关 Δ：abc +2.0 /
abd +4.0 / acd −2.0 / bcd +2.0pp）——`Δ≤pd` 封死 +5pp 可观测性，与 KL 支同判。

**二级观察（胜率，噪声内横盘）**：
- 训练 wr：it1–10 0.688 / it11–20 0.671 / it21–30 0.677，无趋势；
- 日常 eval 只读 `anchor_wr`：it0 **0.650** → it5 0.660 → it10 0.665 → it15 0.655 → it20 0.675 → it25 0.685 → it30 **0.665**（Δ **+1.5pp**，200 局 SE≈3.32pp 噪声带内；it30 的 0.6725 是 400 局混轨数，与 200 局 it0 不可比；分关 s2002 全程最弱 0.50–0.64）；
- 熵 it1–20 0.580 → it21–30 0.589（略抬，非熔断量级；`ent_break` 结构性不触发）；
- kickstart（对 BC 的 KL 幅值，非系数）it20 0.025 → it30 **0.060**：第二段离 BC 更快，但**单步 KL 几乎没放大**——位移在累积，步长仍是细步，与主端点一致。

**熔断/事件账**：
- it1 一次 `wait_job` 超时 1800s 后重启；it20 撞上 **hub 403 ip blocked ×5** 后异常退出——**未走 T7 ABORT**（`--remote-degrade-after 0` 下连败应响亮停腿，此处重试 5/5 后抛异常崩溃，T7 覆盖范围立缺陷单）；全程 `push_nodes=0`，走课程隧道 hub/pull（cloudflare×2 → Tailscale 直连 `100.124.208.62:8787`），HOLD 担忧的 auto→gpu1 未命中；07:30 第三次 `run_start` 跑完 it20–30；
- `run_complete` 07:49:10「正常收官（it30/30）」；**无** KL/ent 熔断；
- 轮内曲线无断点：it20→it21 单点 +55%（0.00148→0.00230）次轮即回落 0.00179，无持续台阶，与 x3-start it41 处同构。

**与评审 P0 的关系**：本结果**复证 P0-2**——工厂第二段（复合放缰+踩刹车）在 x3-start 333 轮已 max KL=0.00353，此处 10 轮同样抬不到 ≥0.005。**不**构成「任何放缰无效」；重设计 A（`kl_coef=0, lr=1.5e-4`）/ B（`kl_coef=0.03, lr=3e-4`）**仍未测**。

**权重**：`nn-training/weights/x3-step/x3-step.it30.20260916-074742.json`（wver `911b60ffa135e596`，it1–it30 齐，379114 B）。
**正式 verdict（2026-09-16 用户要求补跑，已执行，判负）**：`tmp/x3-step/verdict-it30-seed400000.jsonl`
（800 行，126.1s @6.3 games/s；行顺序与基线逐对对齐，seed 序列一致）：终点 **562/800=70.25%** vs
冻结基线 576/800=72.00%，**Δ=−14/800=−1.75pp**（判线 ≥616/800 且 McNemar 单侧 p<0.05 ⇒ 失败）；
配对 b01=12 / b10=26，pd=4.75%，单侧 p=0.9931（反向），双侧 p=0.0336（显著更差，只记判负+方向为负，
不做因果断言）。分关 abc 155/200=77.5%（+2.5pp）/ abd 150/200=75.0%（−2.5pp）/
acd 121/200=60.5%（−3.0pp）/ bcd 136/200=68.0%（−4.0pp）⇒ 目标 power 关全降，无拆东墙补西墙。
伴随量 kills 2.411→2.377（持平）、shots 14.55→13.44（−7.6% 但命中率 45.8%→48.7% 反升，无开火崩；
日常 eval 口径 shots −1.3% 互证）、dmg 124.2→126.5（持平）⇒ 熔断干净。
附带收获：本次 verdict 行**自带 census 指纹字段**（导出器默认已开 Phase-0），x3-step.it30 在
400000 段的画像半成品已免费到手；要做配对画像只需重跑基线一批。课程文件结算节已同步。

**指纹配对补记（2026-09-16；T5 换尺口径第一次实执行）**：补跑基线 it333 同段 800 局
（`tmp/x3-step/baseline-it333-seed400000.jsonl`，576/800 与冻结基线逐数一致，顺带复验证确定性），
`phase0-fingerprints.ts` 配对报告（`tmp/x3-step/fingerprints-report.md`）四指纹逐值重合——
killer power 64.4% vs 63.7%、首命中 power 0.1% vs 0.1%、power 3.76 vs 3.74/千tick、
转化 49.4% vs 49.5%、败局 power 命中 0.58 vs 0.57/千tick——主端点 Δ≈−0.03/千tick
（≈0.2σ），判线 +0.7 纹丝没碰。按冻结判决表第 4 行：**没学到**（与 KL/pd/胜率三支一致）。
⚠ 副发现（动摇 T3 tie-break 鲁棒性，**不动 T3 事实部分**）：同权重 it333 在 400200 段
C=0.620（F2 成立→tie-break→T5），在 400000 段 C=0.577（F2 差 0.023 未成立→F1 单独→T6）；
C 定义 =(没被杀＋最后才杀)/在场局，SE≈2pp，0.60 门限两侧全是 1σ 内——**T5/T6 标签是门限硬币，
事实（败局几乎不对 power 开火：两段 0.58/0.57/千tick，首命中 power 恒 0.1%）纹丝不动**。
T5 仍是主路径（T6 需另行批准＋破规立案），但后腿引用 T3 结论须带此 caveat；tie-break
加固（直接看首命中分布、去 C 门）留待 T5 立项时定，不在本补记改 T3 冻结规则。

**后继（不因本腿改判 T3）**：T3 已判 **T5 metrics v6（分敌种信用）**为主路径。立项 T5 时**必须**显式声明可测性策略：
- 要么先用单旋钮 A/B 之一证明策略能动（禁止再跑本复合段）；
- 要么主端点直接改 **pd / 权重指纹 / 分敌种命中**，不再依赖 per-iter KL≥0.01。
继续在本复合段加轮数 = 测不出任何奖励重定价（与 x3-power 判负同构）。

---

## §49 R9 远端降级：默认 ABORT + 启动界面 opt-in（T7，2026-09-15；DECISIONS §2026-09-15-goalnn-r9-default-abort）

**为什么**：x3-power it1 远端连败触发旧默认 R9 自动降级 → remote 模式 D2 把
`ppo_backend/model/opt` 置 None（hub 省 torch），降级只改 `args.ppo="local"` 就进
`_serial_ppo` → `None.load_episodes` ×3，5/5 耗尽。用户拍板：**不**默认静默降级到本机。

**决定**：
1. `_ensure_local_ppo_stack()`（`rl/loop_core.py`）：懒加载 torch + backend + model + opt；
   本地启动与降级共用，幂等。降级前必调（`loop_steps._remote_ppo_or_degrade`）。
2. `--remote-degrade-after` **默认 3 → 0**：连败 3 次写 `gate_verdict: ABORT` 停腿。
3. 控制台 `TrainLaunchModal`「降级本机」开关（默认关；localStorage + registry
   `remoteDegrade` 供监督重启复现）→ `--remote-degrade-after 0|3`。

**验证**：`tests/test_remote_degrade.py` 7 用例绿（默认 ABORT / opt-in 建栈）；
`dashboard/tests/local-worker.test.ts` 12 绿（flag 0/3）；`cd dashboard && bun run typecheck` 绿。

---

## §48 采集配额量纲 10× 修（T9）：`est_ticks_per_game` → `est_samples_per_game`（2026-09-15；`plan/x3-power-followup.plan.md` §T9）

**为什么**：§46 的判负把「transitions = samples」定为口径令，但实现层分母仍是 ticks ——
`target_transitions`（已结算 `nSamples` 之和）÷ `est_ticks_per_game`（ticks/局）⇒ 初波反解出的
局数只有应采量的 ~⅒；补波量按同一个错估缩放（`ceil(缺口/980)`），**补满 3 波仍是零头**，
终以 `wave_cap` 收场（评审复算的「兑现 37% 触顶」用例）。同错另蔓延到三处文档（已一并修）。

**决定**（计划 §T9 的「二选一并写明」）：**键改名** `est_ticks_per_game` → `est_samples_per_game`
（值 = 局均 ticks / K；x3 实测 samples/ticks ≈ 0.1007 ⇒ 980 ticks ≈ 98 samples），
**不取**「公式里显式 `/K`」。理由：K 是 exporter 侧实现细节，课程文件不该编码它（K 变 = 全部课程文件改）；
键名自带单位 + `CourseConfig(extra="forbid")` ⇒ 旧键照写即启动期响亮报错，不可能静默复辟。
（DECISIONS 的 `goalnn-dynamic-rollout-volume` 条目已加日期化修订。）

**落到代码的四处**（全部在 `nn-training/rl/`，数学一行未动）：
- `volume_waves.py`：`initial_games` / `topup_games` / `plan_topup` 形参改 `est_samples_per_game`
  （`G0 = max(1, ceil(达标线/est))` 原式）；文件头新增「量纲」节写死 samples/ticks 换算与 K。
- `resume.py`：`trailing_samples_per_game` 只读 jsonl 的 **`samples`**（**绝不读 `ticks`**）——
  这是同一条 bug 的另一半（首轮按声明值采对、第二轮起又 10×）；无历史且无声明值 ⇒ 响亮 ValueError。
- `loop_core.py`：`_volume_est_ticks` → `_volume_est_samples`（兜底读 `args.est_samples_per_game`）。
- `cli.py` / `config.py`：argparse dest 与课程字段同步改名（旧键走 `extra="forbid"` 响亮报错）。

**同批修掉的文档/注释**：`curricula/x3-start.jsonc`（收官总结「23 万 transitions/轮」勘误 = ticks）、
`curricula/_example-custom-stage.jsonc`（量纲节 + 示例键名）、`plan/dynamic-rollout-volume.plan.md` §1/§2.1。

**钉死单测**（`tests/test_rollout_volume.py`）：新增「① T9 量纲钉死（600000/4/980）」——
同一条 target=600000、4 关：新语义（est=98 samples）`G0=1531` **一波达标**；旧语义（est=980 ticks）
`G0=154` **补满 `DEFAULT_MAX_WAVES`=3 波仍 < 达标线的 1/3**。另加
`test_t9_old_ticks_key_name_is_rejected`（改名护栏：旧键照写必须响亮报错）。桩里 `totalTicks` 刻意写成
`10×samples`，任何误读 ticks 的路径都会 10× 暴露。回归：`tests/test_rollout_volume.py` 52 用例 + 
`e2e/test_volume_e2e.py` 10 用例（真调度器/真 shard）绿，nn python gate + 根 `bun run check` 绿。

---

## §47 Phase 0 逐敌种画像（T3）：**败局 = 从不碰 power**，判决 T5（2026-09-15；`plan/x3-power-followup.plan.md` §T3）

**为什么**：800 局探针显示败局 = 早死少开火且「摊薄画像」仅占 2%，x2 时代的机制假设在本批不成立；
容量侧已核足够（67.5K ConvMixer、power 独占 ch9、30 维标量已入模型）。⇒ 先用**零训练成本**的
逐敌种画像定方向（T5 信用 / T6 生存 / 执行），再决定开哪条腿。

**新增埋点（只读观测，不回流 gameplay）**：`enemy_hit` 加 `targetKind`（唯一 push 点
`SimulationCombat`，与 `tank_destroyed.byId` 同构先例）；`tools/sim/export-eval-game.ts` 的
离线 eval telemetry 加 census：`hitsByKind`/`killsByKind`/`exposureByKind`（= 各敌种
「存活×接战 tick」积分，**④ 的分母**）/`firstHitKind`/`firstKillKind`/`killOrder`/`killerKinds`
（killer-kind 经 census 的 id→kind 表回查，已死/已清理的凶手也能归因）。采集器
`export-rl-rollout.ts`（codeHash 热路径）**未动**；报告器 `tools/sim/phase0-fingerprints.ts` 纯离线。

**口径提醒**：census 让 800 局离线探针由 ~92s 变 **118–130s（+28~40%）**，只影响诊断，不进训练。

**四指纹**（池外新段 400200-400399，四关各 200；基线 it333 与终点 it30 各 800 局，**两权重同判**）：

| 指纹 | it333(基线) | it30(终点) |
|---|---|---|
| ① killer-kind（谁杀了我，247/240 次死亡） | **power 64.0%**、fast 26.3%、armor 9.7%、basic 0% | **power 65.4%**、fast 23.8%、armor 10.4% |
| ② 首命中 kind（先打谁） | basic 74.0%、fast 25.9%、**power 0.1%** | basic 73.5%、fast 26.3%、**power 0.3%** |
| ③ power 在场 600 局 | 没被杀 32.0% / 最后才杀 30.0%（位次 2.39） | 没被杀 30.8% / 最后才杀 30.8%（位次 2.39） |
| ④ 曝光归一命中/千tick | power **3.62**（basic 9.49）、转化 49.5% | power **3.61**（basic 9.11）、转化 49.2% |

**胜/败条件化（③ 必须条件化，否则被「败局当然没打完」混淆）**：

| 子集 | power 在场局 | 命中过 power | 杀掉 power | power 占首命中 | power 命中/千tick |
|---|---|---|---|---|---|
| 胜局 558 | 405 | 396（**97.8%**） | 396 | 1 | 4.25 |
| 败局 242 | 195 | 21（**10.8%**） | 12 | **0** | **0.80**（basic 9.14） |

⇒ 败局中 NN 平均每局只在 power 身上落 **0.17 发**（它在场 ~213 tick），而败局凶手 **155/195 = 79.5%** 是 power；
胜局里 power 被打到 97.8% 且转化 50%（四种敌种里最高）⇒ **“打不死 power”不成立**，是“根本不开火”。

**分支判决（预注册 tie-break 原样执行，唯一）**：F1（①处刑 A=0.640）与 F2（②绕开 B=0.999、C=0.620）
**同时成立**，F3（③打不死 E=0.495 不满足）不成立 ⇒ tie-break 看 ② 首命中分布（power 0.1% < 50%）
⇒ **归 T5（metrics v6：分敌种命中/击杀列 + 按敌种加权奖励）**；it30 复算同判（A=0.654/B=0.997/C=0.617）。
T6（wChip）排序第二（① 也成立，但归属不在先；且须用户明示批准 + DECISIONS 破规条款——占位已在
§2026-09-15-goalnn-x3-power-negative）。⚠ ① 与 ② 是**同一机制的因果两面**（不碰 power → power 活着 →
它开火杀人）；若按「① 优先」读法则判决为 T6。两条读法的**共同事实**不变：【败局中 NN 几乎从不对 power 开火】。

**验证**：`tests/sim/phase0-census.test.ts`（从零埋点原始事件流独立重算四指纹逐值对账 + determinism 双跑）
与 `tests/combat-enemy-hit.test.ts`（targetKind 四敌种）共 12 用例绿；产物 `tmp/x3-phase0/*.jsonl` 可复现；
`bun run check` 绿；`freeze:check` 冻结签名不变（只加只读事件字段，未改行为）。另：同新段上两权重的策略差
= 69.75% vs 70.625%（Δ=+0.875pp）——与下一节的 −2.875pp 段不同不矛盾，只说明两权重在池外新段上无实质差异。

---

## §46 x3-power 结课：判负（2026-09-15；课程 `nn-training/curricula/x3-power.jsonc`「终点结算」节）

**为什么**：x2 门暴露的结构性差距——含 power 的关恒差 ~15-20pp——被归因于「伤害摊薄零代价」
（wHit 按次给分 ⇒ 打中两个各一枪与集中打死一个在账上几乎没区别，而 power 必须集火秒掉，
否则它的快弹先赢）。本腿是现有 metrics 语言下的可表达代理：**杀/中信用比陡峭化**
（`wKill` 3.0→4.0、`wHit` 0.3→0.15，即 10:1→27:1）。唯一**训练**变量 = reward params
（formula/scheme/terminal 不动）；唯一例外 = 护栏 `kl_cap` 0.2→0.006（护栏校准，非训练变量）。
30 轮跑满，正式 800-verdict **判负**。

**判决（同批配对，种子 400000-400199 池外新段，四关各 200；权重
`nn-training/weights/x3-power/x3-power.it30.20260915-174103.json`）**：

- 终点 **553/800 = 69.125%** vs 暖启基线 it333 **576/800 = 72.00%**，pooled **Δ = −2.875pp**
  （判线 ≥616/800 且 McNemar 单侧 p<0.05 ⇒ 失败）；b01=12 / b10=35，**pd=5.9%**，
  单侧 p=**0.9998**（双侧 p=**0.0011**，净 −3.35σ）。
- 分关（各 200，同种子配对）：abc 73.0%（−2.0pp，2/6）/ abd 75.5%（−2.0pp，4/8）/
  acd 59.0%（−4.5pp，3/12）/ bcd 69.0%（−3.0pp，3/9）⇒ **四关全降**，无「拆东墙补西墙」。
- 熔断轴**干净**（verdict 800 口径）：kills 2.411→2.357（−0.05 ≈ −1.3σ，噪声内）、
  shots 14.55→14.07（−3%，无崩塌）、dmg 124.2→124.8（持平）、ticks 995→974 ⇒
  非熔断停腿，是**跑满的干净证伪**（预注册「不升反降即停」按噪声带执行）。
- 训练（`tmp/x3-power/training_log.jsonl`，30 个 iteration 齐）：**KL it1 0.000581 → it30
  0.002133**（全程 max **0.00231**@it25；⚠ `kl_cap=0.006` **在本路径不接线**
  （remote/serial 无人消费，DECISIONS §2026-09-15-goalnn-kl-cap-unwired）——旧文
  「从未咬合」改读为「该键未生效，谈不上咬合」）；rollout wr
  0.6292–0.7417 无趋势；日常 anchor（只读 `anchor_wr`）it0 0.65 → it30 0.64（128/200）
  横盘，最大 Δ+3.5pp ≈0.75σ；rotor gap 从未触发 ≥5pp×3 轮 ⇒ 无过拟合。
- 配对硬约束兑现：it0 vs it30 同种子 200 对 b01=4 / b10=6，pd=5.0%，Δ=−1.00pp ⇒ 策略
  在锚点上几乎没动（仅 10/200 翻转），与「pd<5% ⇒ 看不到 +5pp」的数学硬约束互证。

**八字判决（预注册分支，课程文件 284 行起原样执行）**：**「梯度无方向 / 执行瓶颈」**——
KL 仍 ~0.002 ⇒ **不得**写成「信用比无效」（后者须 KL≥0.01 而 Δ≈0 才成立），更**不得**外推
「信用比有害」（单腿 −2.9pp 显著仍可来自起点游走 + acd 噪声）。后继 = **metrics v6**
（分敌种命中列，TS+Python 全链），**不是**继续调 wKill/wHit 剂量。

**量纲勘误（P0，结课评审复算）**：账本口径的 **`transitions = samples`**（`rl/resume.py`
自声明「nSamples 之和」），ticks 只是 clocks；本腿实测 **samples/轮均值 23343 ≈ 2.33 万
transitions/轮**（ticks/轮 231924，samples/ticks = 0.1007 ≈ 1/K）。旧文「23.5 万 transitions/轮」
实为 **ticks —— 10× 误差**；c4-dodge 的「66 万+」同理（≈6.6 万 transitions）。本腿有效性
不受影响（固定 60 局/关，单位无关），但**一切「X 万 transitions」规划须按 samples 重算**
（60 万线 ≈ seed_rotate 1546/关 ≈ 6200 局/轮）。同错蔓延三处
（`curricula/x3-start.jsonc:66`、`_example-custom-stage.jsonc:77`、`plan/dynamic-rollout-volume.plan.md:26`）
+ `rl/volume_waves.py` 分子 samples 配分母 `est_ticks_per_game` 的 10×（已跑出「兑现 37% 触顶」
用例）⇒ 立缺陷单（T9）。

**事件账一句话**：wall clock 13:31→17:41 = **4h10m，其中训练仅 1h16m**（it2–it30 ≈2.6min/轮；
it1 独占 2h54m）。15:00 `wait_result` 超时 1800s → 15:02/15:03 HTTP 530 ×2 → 15:03–15:04
降级本机 PPO 时 `load_episodes` AttributeError ×3、5/5 耗尽 ⇒ **R9 降级落点被打穿**
（任何腿远端连败必踩同一坑，立缺陷单：§7 先复现后修，T7）；run_start ×10 / iter_error ×5 /
轮内 resume ×2 后 KL 与胜率曲线**无断点** ⇒ 判决不受影响。it15 中途 800 探针（预注册）
**显式注销**（非静默蒸发）。

**未做 / 后继**：metrics v6 立项（被 Phase 0 逐敌种画像门控，T3）；wChip 生存腿为备选
（须**用户明示批准 + DECISIONS 破 N3/R5 立案**，三者缺一不可，T6）；不加 rollout 量
（排序：结构先行，加量只做抬升后的收尾平滑器）。evalA it30 三写 `eval_summary` +
末条 `dropped=400` 脏行（settle 用 `total−settled` 反推）+ reuse 回填行缺 `anchor_wr`/`rotor_wr`
⇒ 立缺陷单（T8，控制台 `iters.ts` 按 iter 归并会读到）。

---

## §45 双轨日常评估（Dual-Track Eval Seeds）：P0+P1+P2 单测/本地 e2e
（2026-09-15；计划 `plan/dual-track-eval-seeds.plan.md`）

**为什么**：日常 eval 恒取 `EVAL_SEEDS[:50]`，17 轮零轮换 ⇒ 选点对这 50 种子过拟合
（实测日常 85% vs 全量 80%）。正式门仍用池外段（0-199/1000+/…），本计划只改日常趋势读数。

**语义（v1）**：
- 锚点轨 `EVAL_SEEDS[:50]`（与历史逐字节可比）+ 轮转轨 `EVAL_SEEDS[50+((it-1)%3)*50 : +50]`
  （池内 50-199 三段轮换，周期 3，与锚点无重叠）。
- **仅** A-eval 且 `eval_games_per_stage==50` 时拼双轨（每关 50→100 局）；it0 基线与
  n_seeds=100/200 正式前缀、冒烟小 n_seeds 一律保持 `EVAL_SEEDS[:n]` 逐字节兼容。
- summary 单行 `wins/games` 口径不变，另加 `anchor_wr` / `rotor_wr` / `overfit_gap_pp`。
- 过拟合报警：近 **3** 轮 `mean(anchor)−mean(rotor) ≥ 5pp` ⇒ WARN（选点看轮转线）；
  单轮 gap 只落账。历史窗按 course_fp 收窄、**不按 wver**（过拟合是跨迭代现象）。

**实现**：纯函数在 `rl/eval_local.py`（`rotor_offset` / `rotor_span` / `dual_track_seeds` /
`should_dual_track` / `split_anchor_rotor` / `overfit_gap_pp` / `overfit_fires` +
种子段注册表注释）；`settle_eval_summary` 拆分台账 seed 落段并写三字段；
`EvalDispatcher.run` pairs 构造处接线（日志带 `[dual-track anchor+rotor]`）。

**验证**：`tests/test_dual_track_eval.py` 10 用例（周期/无重叠/报警 4.9 静默・5.0×3 响・
2 轮不响/summary 三字段/续跑去重/本地 runner 注入 100 局双轨 + it=2 换段 + n_seeds=2
前缀不扩）；相关 eval 回归 53 用例绿；ruff+mypy 189 文件绿；根 `bun run check` 1825 pass。
**全量 nn pytest xdist 在本机环境 ~34% 处稳定 KeyboardInterrupt（排除本测试文件后复现）**
= 环境问题，非本改动；单文件/相关子集绿。

**未做（按计划非目标/后续）**：EVAL_SEEDS 扩池、正式门口径、dashboard 必改、
试点 10 轮 wall/gap 基线（需集群无在跑腿时实弹）、课程头模板批量改写。

---

## §44 按样本量动态采集（Dynamic Rollout Volume）：P0+P1 实现与 P2 端到端验证
（2026-09-15；计划 `plan/dynamic-rollout-volume.plan.md`；决策 `DECISIONS.md §2026-09-15-goalnn-dynamic-rollout-volume`）

**为什么**：PPO 吃的是 transitions，不是局数（x3 240 局×967t ≈ 23 万/轮，c4-dodge 600 局×1100+t
≈ 66 万+/轮），而课程至今用 `seed_rotate`（每关局数）配采集量——固定局数下 transitions/轮随局长
漂移（x3 局均 700–1150t）。**只承诺「量准」，不承诺涨点**（反例：x2-acbc 3 倍密度 30 轮 pooled −3/400
——加量解决的是方差不是信号）。

**实现**：课程三键 `target_transitions` / `est_ticks_per_game` / `max_games_per_stage`（缺席 = 老行为
逐字节不变）。纯逻辑 `rl/volume_waves.py`（配额数学 + 按 (stage,wave) 独立种子流 + 终止谓词 +
补波计划 + WAL 行解析）；账本 `rl/resume.settled_stage_totals`；est 来自 jsonl trailing 均值
（`rl/resume.trailing_ticks_per_game`，纯函数可 replay）；接线 `rl/loop_core.py::{_iteration_pairs,
_volume_topup,_dispatch_volume_wave,_volume_journal_replay}`；iteration 事件追加
`transitions_target/_collected/_capped`。**v1 边界**：只接串行路径（stream 保持老语义 + 写日志说明），
与 curriculum/rotate 门控窗口不兼容（响亮 SystemExit）。

**P2 端到端验证**（`nn-training/e2e/test_volume_e2e.py`，10 用例；假 sim 节点 HTTP 经**真调度器**
`run_rollout_queue` 落真 shard，再走真账本与真补波循环；跑法 `cd nn-training && .venv/Scripts/python
-m pytest e2e/test_volume_e2e.py -q`）：

| 用例 | 场景 | 观测（日志原话） |
|---|---|---|
| 定额收敛 | est 高估 20 vs 实际 14，达标线 100/关 | `G0=5/关` → `w1 {0:2,1:2}`（缺 30）→ `w2 {0:1,1:1}`（缺 2）→ `收官 waves=3 collected=224/200 达标关=2/2`；过冲 12t/关 < 1 波 |
| 初波即达标 | est 与实际都是 20 | `waves=1 collected=200/200`，零补波、零额外派发 |
| 跨重启重放 | 3 波收敛后崩在 w2 中间（删其 shard + 抹 WAL 的 finish） | 重启日志：`WAL 重放未完成的补波 w2 games={0:1,1:1} → 2 局`；初波 pairs 与重放 pairs 与首跑**逐字节同一**；预算不重领（重放后即波次上限） |
| 长短局独立 | stage 0 = 5t/局、stage 1 = 50t/局 | 长局关一次到位后**零补波打扰**（`w1 games={0:5}`，stage 1 缺口 0）；短局关被补到 `wave_cap`（`未达标={0:'wave_cap'}`） |
| 掉局补采 | stage 0 前 2 局零样本（真派发真落盘 transitions=0） | `w1 games={0:2}` 补回；该关 `(games=10, transitions=112)` ≥ 100（零样本局占局数不入配额）；正常关不被牵连 |
| 硬顶打标 | `max_games_per_stage=12` | `WARN 触单关局数硬顶（cap=12）但配额未满 shortfall={0:40}` + 事件 `capped`；该关停采 |

**④ 尾部竞速（tail fan-out）下的配额算术**（2026-09-15 追加，4 用例，`tailFanoutN/Dup=4/2`）：

| 用例 | 场景 | 观测（日志原话） |
|---|---|---|
| 副本不污染账本 | `dup_hang=0.35`（副本稳定后到、静默丢弃） | 账本与无竞速**逐值相同**（`(8,112)`/关），`dup_copies>0`，`fanout_settled=3` |
| double-settle 边界 | `dup_hang=0`（副本同速） | 账本**绝不重复计数**（`settled = games×14`、`games ≤ 去重派发对数`）；缺口只允许出现在预算耗尽时（`波次预算未耗尽却未达标` 是本用例最硬一条）；`_volume_collected` 与账本一致 |
| 丢局被补回 | 手工落「dup_settle 那一刀」（rmtree 共享 shard 目录），派发关竞速 ⇒ 损失是唯一变量 | 初波 `(8,112)`/关 → 退休 2 局/关 ⇒ 账本 `(6,84)` → `缺口 16t/关` ⇒ `w1 {0:2,1:1}` 补回 `(8,112)` 且 `waves=2`、WAL 无 pending |
| 预算耗尽要响亮 | 短局关（5t/局）+ 竞速 + `wave_cap` | `wave_cap` 停采时日志给出 `未达标={0:'wave_cap'}`（不是假装达标）；长局关 500t 一次到位不被拖累 |

**竞速取证（新发现，值得后续跟进）**：同节点 fan-out 的两份副本 `out_dir` **逐字相同**
（`traj/itN/dist/<node>/rl_s{stage}_seed{seed}`）——两份结果若都在对方进 `seen` 前通过锁外
`validate_result`（窗口 = 一次 `write_shard`），后到者走 `dup_settle` 分支 rmtree **共享目录**，
那一局数据被删（两行日志坐实：`dup settle s0/seed… — dropped (+retired …\rl_s0_seed…)`）。
单节点 + `dup_hang=0` 下实测每轮 3–5 局被退休（`DEFAULT` 多份副本时更常见）：
`dispatched=25 unique=19 settled={0:(8,112),1:(8,112)}`，即 3 局白跑、补波补回 2 局、
预算被吃到 `wave_cap`。**方向性结论**：丢局只会让账本变短（绝不膨胀），配额达标性由补波兜住，
代价是墙钟——与「宁可多采几局，不可静默少采」一致。跨节点竞速（race lane）不共享目录，
不存在这种损失。

**e2e 拓出的两个真 bug（P2 的全部价值就在这里）**：

1. **账本只认一种 manifest schema**：`settled_stage_totals` 原先只读 `nSamples`（TS exporter 的正规单局形），
   而队列/远端 path 的 shard 是聚合单局形（`totalSamples`，`dist_common.write_shard` **原样写** agent 返回的
   manifest）⇒ 那些关在账本里永远「零样本」、补波永不达标，只会白烧墙钟到波次上限。修：两种都认
   （`nSamples` → 回退 `totalSamples`），两者都缺则计 0（不猜）。单测钉死（`test_settled_totals_accept_both_manifest_schemas`）。
2. **波次序号 `wave_idx` 是决策，不是账本的函数**——它同时是种子流的键。首版设计假定「全部决策都是
   (配置, it, 账本, jsonl) 的纯函数 ⇒ 重算即 replay、无需 WAL」，**该假说被 e2e 证伪**：崩在 w2 中间后重启，
   计数器回到 1，会拿 **wave-1 的种子去补 wave-3 的缺口**（同观测史、不同波次序列——正是计划 §2.3.1 要禁止的
   「重新抛硬币」）。修：`wave_idx` 从 commit journal 回读（`parse_wave_records`）——预算按 WAL 续算
   （跨重启**不重领额度**，防「崩一次就再领 3 波」），停在波中（有 start 无 finish）的那一波按 WAL 的
   对局表**原样重放**，其余继续按账本推进。

**验证**：单测 46 用例 + P2 e2e 10 用例（含 4 个竞速用例）全绿；e2e 全量 70 用例绿；
nn python gate（ruff + mypy 188 文件 + pytest xdist）、根 `bun run check` 1826 用例绿。

**补波决策 overhead 实测（2026-09-15 微基准，纯函数）**：`plan_topup` 6.1µs ·
`parse_wave_records` 28µs · `wave_pairs`(80 seeds) 236µs；典型 3 波决策包 ≈ **754µs**，
相对真 bun 试点单轮 wall ~24s = **0.003%**（DoD 预算 <1%，过两个数量级）。脚本一次性，
已删。

**真 bun 微课试点（2026-09-15，串行路径 + 真 sim；教训见下）**：5 轮跑通，volume
初波/补波/WAL/收官日志全链在位。观测：S1 + `max_ticks=120` 下 timeout 局只产出
~12 samples/game（远低于 est=40）⇒ 3 波后 `collected=144/400`，正确触 `wave_cap`
并响亮打标（`未达标={1000,1001:'wave_cap'}`）——**不是静默少采**。est 用 trailing
均值后它被抬到 120t/局（jsonl 口径是 tick 不是 nSamples），说明生产课的 est 必须
按 **nSamples/局** 校准，不能拿 max_ticks/局长 tick 顶替。

**污染教训（用户 2026-09-15 指令，铁律）**：集成/微课试点用的模拟课程**禁止**
写进 `nn-training/curricula/`（生产课程表）或把试点权重归档进 `weights/`；一律
落在 **`nn-training/e2e/fixtures/`**，经 `--course-file e2e/fixtures/...` 启动且
out/traj 指到测试 tmp。本次误把 `tiny-vol.jsonc` 放进 curricula 并归档了
`rl-weights.it1-5.*`，已全部清除；模拟课程规范落点 =
`e2e/fixtures/tiny-vol.jsonc`。另：真 bun 试点会打真实 dist 节点（self/mac ping、
甚至触发 mac 升级请求）——**隔离未就绪时不得再对生产节点跑试点**；接线验收以
`e2e/test_volume_e2e.py`（FakeAgent，不读生产课程表）为准。

---

## §43 本机 PPO 拆分为独立 worker（2026-09-15，用户指令「把本地 PPO 拆分为一个独立 worker，可以随时启停，与云端 worker 一致，同样支持 pull/push 模式」）

**变更**：本机 PPO 不再是 `TrainingLoop`（run_rl）进程内的阻塞调用，而是新的受管组件
`localWorker`——跑的就是**云端同一个入口** `python -m remote_worker --poll
http://127.0.0.1:<本课 hub> --out tmp/local-worker-<课> --device cpu`（配了
`rl.torch_threads` 才透传 `--threads`）。协议/租约/心跳/幂等重拉/热替换退出码 86 + 内部
监督器重拉**零分叉继承**；push 侧不新增实现（执行面 = 既有 `workerServe`）。控制台 `local`
预设改义为 `hubServer → localWorker → trainingLoop(--ppo remote)`，进程内 PPO 不再是
控制台选项（`--ppo local` / run_bc `--local` 保留给直调 CLI 与 R9 远端失败降级落点）。
决策与拒绝的备选：`DECISIONS.md §2026-09-15-goalnn-local-ppo-worker`。

**唯一的新语义（也是最贵的一个坑）：`--remote-transport {auto,pull,push}`**。`_remote_ppo`
的历史优先级是「rl-config 里本课 `gpu_push` 节点 > hub」——某课用 push 跑过一次后
`courses.<课>.push_node_url` 就留在配置里，于是 `local` 预设会把 job **静默推去云机**，
本机 worker 永远领不到活，而账本/日志看起来「训练正常」。所以 local preset 必须钉
`--remote-transport pull`（run_rl 与 run_bc 同步支持；`auto` = 历史行为零变化，非法组合
响亮 `SystemExit`）。刻意**不**动 `rl.remote_hubs[<课>]`：它是 pull preset 隧道 URL 的家，
而 `stepCloudflared` 复用已建隧道时不会重写它——写本机 hub 进去会把 pull preset 悄悄改成
打本机 hub；本机 hub 只经显式 `--remote-hub-url` 注入。

**push 侧同样可用本机执行面（同日追加，用户指令）**：`configurePushEndpoint` 改为三档裁决
（用户填 endpoint / 复用 config 可用 `gpu_push` / **回落本机 worker_server**），回落时写
`local_push` 节点 + 课程 `push_node_url` 指向本机、预设多起一个 `workerServe`——云/本机两份节点
条目共存互不覆盖（绝不静默吃掉用户填的云 URL）。详见 `DECISIONS.md` 同条目的「追加」bullet。

**整树停止**：`localWorker` 是「父 supervise_worker + 子 worker_loop」两进程，停/重启走
`dashboard/src/core/net.ts::killPidTree`（Windows `taskkill /T /F`；POSIX 先验 `pgid === pid`
再组杀，否则退回单进程 stop），判定唯一来源 `stack/specs.ts::COMPONENT_KILL_TREE`——只杀
父进程会留下继续轮询 hub 抢 job/抢租约的孤儿，「随时启停」名存实亡。

**验证（2026-09-15）**：dashboard `tsc` 干净 + 351 pass / 0 fail（59 文件，新增
`tests/local-worker.test.ts` 11 用例：spec 形态 / poll 目标 / killTree / 双课隔离 / pull 注射 /
重建逐字段一致 / 接线 grep 门禁）；根 `bun run check` 1819 pass / 4 skip / 0 fail；
nn-training python gate 绿（新增 `tests/test_remote_transport.py`：run_rl 与 run_bc 两侧裁决
+ argparse 默认值与 choices）；`bun dashboard/src/server/build.ts` 三份 bundle 与根 `bun run build`
均通过。顺手补了 HEAD 上已红的 `selfNodeSpec.cwd`（控制台以 dashboard/ 为 cwd 启动时的
`Module not found` 回归护栏）。

---

## §42 ipynb 清场 + tpu-probe 单源化（2026-09-13，用户指令「重构 ipynb：删无用 notebook，重新整理 tpu-probe 使其更模块化并保持独立性」）

- **删三个被取代的旧 notebook**（均无活引用，文档中的历史提及保留为史实）：`p4-onset.ipynb`（课程专用 BC → 通用 `battle-bc.ipynb`）、`m2_colab_worker.ipynb` + `p4-onset-rl.ipynb`（旧式内联 worker → 单 cell `battle-rl.ipynb`，运行时已迁 code.zip）。现存三个：battle-rl（云端 worker）、battle-bc（通用 BC 蒸馏）、tpu-probe（吞吐探针）。
- **tpu-probe.ipynb 重构为 §0-§6 分区 + Cell 地图**：§1 vfio 诊断/释放两个 cell 原各自内嵌一份 `find_vfio_holders` 拷贝 → 合并为单一「工具函数」cell（无副作用）+ 两个 thin driver（NameError 时提示先跑工具 cell）；§3 脚本 cell 从"手工保持逐字节一致"改为**单源生成**；测量 cells（TPU 三遍/GPU 单卡/多卡/CPU）原样保留为 §4 thin driver。
- **单源机制**：正本 `nn-training/tools/tpu-probe.py` → 新工具 `tools/sync_tpu_probe_nb.py`（写回 / `--check`）→ notebook 的 `%%writefile tpu_probe.py` cell；新测试 `tests/test_tpu_probe_notebook.py` 双守卫（内嵌==磁盘逐字节 + 工具 `--check` 通过），drift 在 python-gate 常驻拦截。**运行时独立性不变**：notebook 在 Colab/Kaggle 仍自包含（脚本随 cell 写盘，无需克隆仓库）。
- **顺手修三处 HEAD 上已红的旧账**（7aeafb2 / 26f9171 / 99c9367 落地时未跑全门禁）：
  ① `rl/forensics.py::_rss_mb_posix` 同源化——`getrusage ru_maxrss` 在 WSL2 内核**持续滞后**于实际常驻集（实测滞后 ~16kB），与 `/proc/self/statm` 混用导致 `test_rss_mb_sane` **确定性红**（3/3）；改读 `/proc/self/status` 的 VmRSS/VmHWM 同源快照（20 万次采样 0 违例，peak ≥ cur 由内核保证），getrusage 降级路径保留给非 Linux POSIX。
  ② `rl/eval_replays_once.py` 两处 RUF100 unused noqa（`# noqa: E402/BLE001` 指向未启用规则；意图注释保留）。
  ③ `tools/training/console/api.ts:782` `CURRICULA_DIR` → `curriculaDir()`（TS2552，7aeafb2 引入的未定义标识符）。
- **环境记录**：满编 `-n 4` 下 `test_bc_epoch_e2e` 两例曾红（fake_worker POST 400 manifest 校验拒绝），单跑与 `-n 2` 均绿——负载型 flake 非回归，e2e 并发时序对 CPU 争用敏感（§0.1#4 单文件绿 + 全组红 = 环境的又一实例）。

---

## §41 云端多卡确认：PPO/BC 双卡默认真用上（2026-09-13，用户指令「双/多卡时训练跑在双/多卡上」）

**缺口**：PPO 的 DP 能力在（worker run_job 对 --device cuda-dp 包 DataParallel，
T4x2 实测 1.92×，§21）但 cell 默认 `use_multi_gpu: False`（多卡机器第二张卡闲置）；
BC 更是完全单卡（worker `_bc_device` 把 cuda-dp 砍成 cuda，bc.py 无 DP）。

**补齐（默认反转）**：

- **cell 默认 `use_multi_gpu: True`**：auto + 2+ 卡 → cuda-dp（响亮日志）。opt-out
  保留（要逐位可比改 False）——多卡机器重跑 cell 后新会话即生效；在跑腿中途换 DP
  = 数值不可逐位比，操作员须知（markdown/日志双提示）。
- **bc.py 补 DP**：`_resolve_bc_device`（cuda-dp：2+ 卡真 DP；单卡/无卡**响亮退化**
  cuda/cpu——与 worker 同语义）+ `_bc_raw`（DP state_dict 的 "module." 前缀绝不进
  weights.json——TS 运行时格式防线）；train() 经 raw_model 落盘/恢复/计数（DP 与 raw
  共享参数对象，训练原地更新终态即 raw 终态）。
- **worker `_bc_device` 改透传**（不再代砍单卡）；tpu/xla 拒收不变。
- **单卡机器零变化**：resolve 尊重 1 卡现实（cuda），退化路径响亮。

**验证**：`test_bc_dp.py`（12 例：cuda-dp 多卡/单卡退化/无卡退化/透传、_bc_raw 解包
+ "module." 前缀真实存在性断言 + 参数对象同一性、worker 透传/tpu 拒收）+
`test_notebook_runtime.py`（24 例既有）全绿；nn-python-gate 全量绿。
真 2 卡行为由 worker 既有 DP 分支 + §21 T4x2 实测背书（本机无 2 卡，DP 转发无法本地跑）。

## §40 自主审查轮：battle-rl.ipynb 单 cell 化 + 真跑暴露四缺陷修复 + bc-c4 it1 真实产物（2026-09-13）

**BC 云端 notebook 重构（用户指令：网页只执行一个 cell 建连，其余完全自主）**——
`nn-training/ipynb/battle-rl.ipynb` 从 8 代码 cell 顺序流程改为 **markdown + 单代码 cell**：

- 参数置顶（MODE/HUB_URL/HUB_TOKEN + 行为参数）；自动 = 平台/设备探测（CUDA/TPU/CPU，
  TPU 独占设备约束全保留：内核绝不 import torch_xla，占用者诊断在）、保活、
  `/code` 代码引导（**404 = 每 30s 等待重试**——旧版硬失败，顺序敏感；现在先起 worker
  后起 trainer 完全合法）、worker 监督（热替换 86 自动重启 + **崩溃指数退避重启**
  ≤MAX_WORKER_RESTARTS；rc=0 干净退出/-2 配置致命不重启）、push 模式 cloudflared
  自动安装 + bootstrap 优先 hub /code（GitHub tarball 兜底）。中断 = 干净停机。
- **E2E 实证**：本地 hub + 抽取 cell 源码 headless 执行——worker 先起等 /code 重试
  → trainer 后起发布 → cell 引导代码、领 kind=bc 任务、真训练 5.5s、回传 accepted。
- **二次精简（同日）**：运行时逻辑再搬进 code.zip——新模块 `remote/notebook_runtime.py`
  （设备探测/pull/push/崩溃退避重启，入库受 ruff+mypy 门禁），cell 降到 **134 行** =
  参数 + 保活 + /code 引导 + `run_notebook(CFG)` 委托。修运行时不再重发 notebook
  （重跑 cell 即拉新版）；push 的 GitHub tarball 兜底随之删除（/code 等待重试已覆盖）。
  headless E2E 复验 PASS（code.zip 100 个 .py 含本模块）。
- 新夹具课程 `curricula/bc-e2e.bc.jsonc`（BC 管线流校验，对齐 tiny-a 定位；wins_only=false
  适配 300-tick 冒烟）。

**真跑暴露四缺陷（全部修复）**：

1. **smoke 语料污染真轮**：smoke shard 落 `bc-data/it1` 被真跑断点续跑复用（40 局里混进
   1 局 300-tick timeout 局）→ smoke 落 `bc-data/smoke` + 全量重采不复用；`round_name`
   贯通采集/发布/落位三处（iter_bc_shard_dirs/verify_and_land_bc/publish_bc_job）。
2. **本地训练路径根错位**：run_bc chdir 仓库根但子进程 cwd=nn-training，相对路径
   FileNotFoundError → 全 resolve 绝对路径；子进程输出 capture 缓冲到结束（§16.3 违例）
   → Popen 逐行流式中继。
3. **本地 ckpt_every 死键**：train_local_bc 漏传 --ckpt-every（云端 manifest 有）→ 接上；
   真跑 ckpt.10..60 全落盘。
4. **本地 WEIGHTS.md 行全 0**：extra_meta 是 weights JSON **顶层合并**（sizes/best_val_loss/
   history），误读 `meta` 子键 → 修读法 + it1 注册行已人工订正。

**真实产物（bc-c4 it1，全链本地实证）**：arena4 语料 40 局（29 胜保留/11 败过滤，
6442 帧，18.6s 采集）→ CPU 6 线程 60 epoch 37 分钟（val_loss 2.24→1.6260，move acc
0.236→0.566，fire acc 0.761→0.764）→ 归档 `nn-training/weights/bc-c4/bc-c4.it1.20260913-161311.json`
+ 中央 WEIGHTS.md 行 + 活跃指针 `tmp/bc-c4/weights.json`。该权重可直接作 RL 课程
`bc` 种子 / `kickstart_ref` 快照。

## §39 BC 训练整合进 云-HUB-LAN：kind=bc 第二任务类型全链（2026-09-13，用户指令五步）

**目标**：BC 从本地手工流程（export-godai-labels + train/bc.py 手工串）升级为与 PPO 同构的
分布式管线：控制台启动 → LAN 集群生成语料 → 云机 poll/接收任务训练 → 权重回传归档。
设计：`plan/bc-cloud-integration.plan.md`；**全部复用既有管线，只加任务类型，不建第二套体系**。

- **协议（remote/protocol.py）**：manifest 可选 `kind`（缺省 "ppo" wire 兼容）；bc 免必填
  reward/γ/λ、追加必填 `arch`、mode 红线 "bc"（串型互斥）；bc result 校验走 `metrics`
  （无 agg）。幂等键/job_id/data_fp 公式不变（bc `init_weights_fp` 恒 "bc"）。
- **云端（remote/worker.py::_run_bc_job）**：run_job 在 code 守卫后按 kind 分叉——D14 血缘
  校验共用 → import `train/bc.py::train`（code.zip 已含 nn-training 全部 .py）→ BC 权重
  （版本化归档字节）+ metrics 回传；cuda-dp→cuda、tpu 拒收；`--echo` 占位回传。
- **HUB 客户端**：publish_job 增 `kind/extra`，init_weights_path 可选（bc 不拷 init 文件）；
  `iter_bc_shard_dirs` + `verify_and_land_bc`（data_fp 重算/commit 对账，无 opt tar）。
  push 模式零改动（worker_server 同 normalize/run_job）。
- **LAN 语料（mode=bc）**：新导出器 `tools/sim/export-godai-bc.ts`（单局复用
  export-godai-labels.exportGame 纯函数 → npyBytes 内存序列化 → BCV2 容器；wins-only 败局
  = `kept:false` 空容器合法结果）；agent `/v1/task` 收 mode=bc（免权重桶）、ping 加
  `bcSupport` 能力位（fail-closed，旧 agent 不派）；入 codehash-files.txt（升级波）。
  `dist_common`：BC_SHARD_FILES/validate 分支/fetch_task wins+nearMissTimes 透传。
- **派发（rl/bc_dispatch.py）**：紧凑调度器——bcSupport 探活门 → (stage,seed) 队列 →
  节点槽位线程 → fetch_task(mode=bc) → 落盘 `<traj>/bc-data/it{r}/bc_s{stage}_seed{seed}/`
  （manifest 补 course_fp/corpus_fp 血缘）。无竞速（语料不需要最快者胜）；局失败重试一次。
- **BC 课程（rl/bc_config.py + curricula/*.bc.jsonc）**：独立文件种类（RL CourseConfig
  extra=forbid 不兼容、mode/gates/schedule 全不适用）；level 引用复用关卡抽离语义；
  `bc_corpus_identity_fp`（env+corpus 参数；train 超参刻意排除）；轮 r 种子
  `[1+(r-1)*seed_rotate, …]`（§15.1 轮转）。首课 **bc-c4.bc.jsonc**（level=arena4，
  student/60ep/wins-only/near-miss 3×，value_coef=0——§15 M3 教训）。
- **编排器（run_bc.py，torch-free）**：per-course run_bc 锁；账本 `bc_round_completed`
  断点续跑；语料补采 → publish(kind=bc) → push/hub 等待 → verify_and_land_bc →
  backup_weights 归档（`nn-training/weights/<prefix>/<prefix>.it<N>.<ts>.json`）+
  WEIGHTS.md 行（torch-free 复刻）；bc-data 旧轮收敛。--smoke：1 局/max_ticks≤300/
  epochs=1 真一轮，落位即作废（`weights.smoke.json` scratch、不归档、账本零污染）。
- **控制台**：`.bc.jsonc` 全链认课（validateCourseArg/discoverCourses/setCourse/
  sanitizeViewCourse）；BC 课程复用 trainingLoop 组件键 → `bcLoopSpec`（run_bc.py，
  哨兵含 remote 四件套）——启停/监督/退出看门狗零改动；startBcLoop（无 BC 种子播种）；
  smokeTrainBc 三里程碑预演（published job → weights landed → BC SMOKE PASS，伪节点
  **真 BC 训练**非 echo）。
- **E2E 冒烟 PASS**（本机全链实测）：self 节点采语料（arena4 单局）→ push 发布 → 伪 GPU
  节点真 BC 训练（student 67.5K params，1 epoch，2.8s）→ 回传 → verify_and_land_bc 落位 →
  作废退出。回归：nn-python-gate 相关子集 162 绿 + `bun run check` 绿。
- **踩坑三则**：① 单 shard 语料 shard 级切分把全部样本划进 val（train=0 → DataLoader
  num_samples=0 崩）——`make_loaders` 回退样本级切分（test_shard_split 回归锁）；② smoke
  300 tick 内不可能 wins → smoke 关 wins_only；③ agent 结果缓存键不含 wins/nearMiss——
  smoke 轮用独立 iterId 命名空间（`-smoke` 后缀）防缓存回放假结果。
- **运维注意**：LAN 远端节点需 git pull 升级后才有 bcSupport（push 合并后人升）；升级前
  bc 语料只由 self/已升级节点承担（fail-closed 不污染）。

## §38 课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端（2026-09-13，用户拍板）

**机制**（DECISIONS §2026-09-13-hot-reload）：trainer 每 iter（rollout 前）重读课程文件，按
`corpus_identity_fp` 分流——

- **未变（B/C 类）**：`rl/hot_reload.apply_hot_fields` 白名单写回 args（消费点每轮活读：
  iters/gamma/lam/lr/epochs/mb/eval_*/ent_break*），**下一 iter 生效**；结构绑定字段
  （bc/workers/out/traj/backup_*/freeze*/update_kwargs 一次构建族）记 restart-only（`*`），
  响亮日志不静默；max_hours 热应用重算 deadline。
- **变了（A 类）**：拒绝 + `course_edit` 事件（本地账本）+ 控制台错误横幅
  （`courseEditFromLedgerTail` 取尾部窗口最近一条，持久显示；改回文件 → trainer 写
  restored → 横幅消失）；**沿用启动配置继续训练**。整单拒绝（iters+wChip 同改不部分应用）。
- **不泄漏云端**：课程字节启动期冻结（`args.course_frozen_bytes`），D13 快照/course_fp/
  shard `--course-fp` 三处统一用冻结源——任何 mid-run 编辑永不进 job payload。
- 半行写/坏档：沿用旧配置静默重试，不打横幅。

**验证**：`test_hot_reload.py`（分类学/整单拒绝/restart-only 记账/冻结 fp 免疫编辑/字段清单）
+ `console-course-edit.test.ts`（最近一条胜出/restored 顶掉 rejected/半行竞态/防御）；nn-python-gate
绿 + `bun run check` 绿（2067 pass）。**顺带**：F-B6 夹具 s-dodge→c6-dmgfix（原 bc 文件
tmp/s2-cap/weights.json 已被 tmp 清理移除，环境性失败——tmp/ 不该被测试当稳定依赖）。

---

## §37 关卡配置抽离 + D14 语料身份改语义哈希（2026-09-13，用户拍板）

**事故**：mid-run 编辑 c6-chip 课程（iters 30→60）触发指纹脑裂——shard manifest 与 job
envelope 各自在采样/发布时刻重读课程文件字节，D14 把 mismatched shard 整轮拒收（job=abbf…
shard=e6c6…，rollout 卡 it16）。原则修正：**iters 类预算旋钮不改训练契约，mid-run 编辑不应
要求新课程**（我此前把「改文件」与「改语义」混为一谈）。

**分类学**（DECISIONS §2026-09-13-level-extraction，全量版）：A 语料身份（关卡 env + reward
语义，改动=破裂）／B 训练语义非语料（bc/优化器/λ，改动=§15.5 新实验，stop→start 生效）／
C 预算测量路径（iters/eval_*/out 等，随时可改）。

**第一步落地**：

- `nn-training/levels/arena6.jsonc` / `arena4.jsonc`：环境语义（地图/敌人队列/敌数/出生点/
  **命/星**/difficulty/max_ticks）独立成关；课程以 `"level": "arena6"` 引用，内联重复声明 =
  raise；内联旧用法逐字节兼容。
- `corpus_identity_fp`（rl/config.py）：语料身份 = env+reward **解析值**规范化哈希（内联与
  level 引用同形同指纹）；经 `--corpus-fp` 进 shard manifest（exporter TS 双写点）、经
  publish_job 进 job manifest；worker `d14_corpus_match` 优先比 corpus_fp、任一侧缺席回退
  legacy course_fp。course_fp 文件血缘保留（D13 快照/resume 对账不变）。
- c6-dmgfix 已迁移引用 arena6（预检 validate_reward ok）；c6-chip 在跑不迁移。evalB/探针
  直接吃关卡文件（`eval-course-ckpt.ts --course nn-training/levels/arena6.jsonc`）。
- 门禁：nn-python-gate 绿（21s）+ `bun run check` 绿（2031 pass）；新测试
  `test_course_level.py`（合并/冲突/兼容/fp 分类学/同形同指纹）+ `test_d14_corpus_match`。

## §36 多课程并行训练（multi-course parallel training，plan/multi-course-parallel-training.md）

- **性质**：纯工程化改造（课程 = 并行单元），不碰任何训练算法；PPO/BC/课程/熔断/奖励公式零改动。
- **已落地**：
  - **P0**：`tests/training-multi-course.test.ts`（双课 spec/锁/配额/门禁）、`nn-training/tests/test_multi_course_locks.py`、`docs/multi-course-audit.md`。
  - **P1**：`tools/training/slots.ts`（端口算术 `hub_base + slot*10` 唯一归宿 + `checkCapacity`/`allocateSlot`）；锁 per-course（`.run_rl.<course>.lock`/`.train_loop.<course>.lock`，无课程沿用旧名）；账本 `Record<course, Entry>` 四新键 + 旧扁平键读兼容（R1）+ 旧条目一次性回填迁移；`train.ts` 双锁 per-course 预检 + `kill (script, course)`（R3）；`git push` 串行化 `.git_push.lock`。
  - **P2**：hub per-course（jobRoot/jsonl/端口），BC 种子 `courses.ts::seedWeightsFromBc`（缺文件 fail loud）。
  - **P3**：cloudflared/workerServe per-course；`rl.remote_hubs[course]` + 兼容单键；`gpu_push` 清单按 `push_node_url` 过滤（非空零匹配 → WARN + manifest 打标，不抛）；notebook 引导文本。
  - **P3b**（DECISIONS §2026-09-12-multi-course-p3b-supersedes-343）：hub 独占租约 `CLAIM_TTL_SEC=300`（领取即设 owner+expiry+heartbeat，心跳续租，过期回池，有活租约验 `X-Lease-Token`）；worker_server 有界 FIFO `WORKER_QUEUE_MAX=8` + 同 jid 幂等 + `/ping queued`。
  - **P4**：`saveConfig` 落盘前 `capacityError` 加法校验（`Σ eff ≤ max(rl.workers, rl.local_slots)`）；`rl/config.py::resolve_course_quota` 热读 `courses.<课>` 优先 + 响亮行 `[quota] workers X -> Y (multi-course split)`；连续 2 轮零 shard 落盘告警；manifest `course_name`（审计短名，不进幂等键） + dispatch 报告带 course；EvalBoard `batches.jsonl` 跨进程 `claim.lock`（复用 `train.loop_util` 锁，拒绝第三套实现）。
- **P5**：`ConsoleState.activeCourse`（additive + 旧 `course` 回填）；`cloudHalts` per-course（S17，旧 `cloudHalt` 一次性迁移）+ 横幅按课 `立即恢复`；busy 键按课程（S10）+ 监督/watchdog 三元组；`restartSpecFor(key, course)` fail-closed。
  - **W4（R2：删旧扁平账本键读写）**：`loadRegistry()` 每次加载把旧扁平单键（`hubServer`/`cloudflared`/`trainingLoop`/`workerServe` + hub-start 分文件账本）**搬迁**进 per-course 表再删键（`migrateFlatCourseEntries`，缺 `course` 取 console-state、缺 `slot` 取 0；同课已有新条目则保新弃旧）；迁移后 `entryForCourse` 严格按课（不再兜底扁平键）、`registryTriples` 不再枚举扁平键、`saveComponent`/`clearComponent` 类型收窄为 `SingletonComponent`（课程组件写不了扁平键）、`Registry` 类型**不再声明**扁平键（`LegacyFlatRegistry` 只给搬迁读）。修掉 `specs.ts::trainingLoopSpec.healthy` 的遗留扁平读（改 `entryForCourse`，此前多课下恒 false）。
  - **W5（LAN 只读回归）**：门控判定抽成纯函数 `net.ts::isReadonlyAction(method, ip)`（`server.ts` fetch 首行唯一调用点），使「LAN 的 POST 必 403 / 回环放行 / GET 放行 / 来源不可得 fail closed」成为可回归断言；另加一条接线回归（扫 `server.ts` 确认门控与 403 仍在）。
- **P5-W2 同屏多课总览**（已做）：`/api/state` 新增 `courseOverviews`（每课一行：阶段 + 四组件状态点 + 最近一轮 it/胜率/采集·训练耗时 + 停机/排队超时徽标，账本判定进程状态、**不发健康探测**，与慢快照同拍 5s 缓存、课程上限 = 槽位数 4）；新面板 `console/ui/panels/MultiCourseOverview.tsx`（单课自动不渲染，点课程名即切换查看）。完整指标表/节点池仍为「选中课程」视图——总览一行点开即钻取，避免 N 课 × 每拍节点 ping。`/eval` 页已原生支持多课程（checkboxes + 逐课视图 + CSV 归属），无需改。
- **P2 验收补跑 + run_rl 锁跨平台修复（2026-09-13，验收被用户叫停）**：P1 验收（9-12 上午，s5-open20/s-dodge/s1-cap 各 ~1 轮）遗留的 stale 锁让双课验收重启当场两连崩——`run_rl._runrl_pid_alive` 把 Windows 的 `ctypes.windll` 分支写成了**无条件**路径，Linux 上一遇已存在的锁文件（无论持有者死活）即 AttributeError：陈旧锁永不清理、同课双开变崩溃而非响亮拒启（DoD「2026-09-06 事故回归」在本机形同虚设）。当日没暴露是因为 P1 首跑时锁尚不存在（O_EXCL 新建路径未走 liveness 分支），且 `test_multi_course_locks.py` 只覆盖 `loop_util.acquire_lock`、run_rl 锁的 stale 分支零覆盖。修复：按 `os.name` 分支（Windows 分支原样保留；POSIX 走 signal 0 存在性探测，宽捕获同 `loop_util._pid_alive`）；红→绿回归两例（stale 接管 / 持有人活着的同课拒启）。
  - **被叫停前的部分活体证据**（s1 + s-dodge 并行 ~20 分钟，第一轮 PPO 阶段中杀停）：双课 stale 锁自动接管并各持本课锁；采集 shard 零交叉（s1 全为 stage 1000-1002、s-dodge 全为 1050-1052，各 50×3）；rotateSeed 独立（1789176181 vs 1789176208）；C5 push 串行化响亮跳过（`另一进程正在 git push` WARN）。权重文件零改动、无残留 ckpt，下次真实启动按 resume 语义干净续跑。
  - **预算实测（§16.1/16.4）**：本机（0 卡）单轮 = 采集 ~6-8 分钟（150 局 / 8 workers）+ CPU PPO **~1.4 小时**（119 chunks × 4 epochs）→ 5 轮 ≈ 7-8 小时；且 per-tick 的 KL 熔断（0.15/3 连击，硬编码不可 CLI 放宽）大概率在第 3 轮自我停车。**plan P2 的「双课各跑满 5 轮」在本机不现实**——要么 remote GPU 路径，要么用户显式批长的本机跑。**用户指令（2026-09-13）：agent 不得启动实际训练**；后续任何实跑验收需用户明确批准。
  - **流程验证完成（2026-09-13，用户批准的轻量方案）**：新增微型验证课程 `curricula/tiny-{a,b}.jsonc`（1 stage × seed_rotate 2、max_ticks 150、epochs 1、mb 64、lr 3e-5、eval 关、iters 5 自停——**纯课程数据行，零算法代码改动**；同类先例 = s1 夹具课程「不再实际训练」，用户 2026-09-02 决策）。双课并行真实全链 5 轮 × 2 课 **19 秒**完成：iteration 1→5 单调零错、rollout 0.4-0.7s/轮、PPO 0.1-2.0s/轮；隔离证据 = shard stage 互斥（1000 vs 1051，各 10 shard 零交叉）+ course_fp 各异（761b29… vs 9e33ef…）+ rotateSeed 独立 + 每轮 (rotateSeed, it) 全新 seed（§15.1 轮转真实生效）+ 双课锁并行持有后干净自清；C5 push 串行化响亮跳过（WARN 行）同窗复现。**教训两则**：①课程缺 `reward` 块会 FormulaError fail-loud（写夹具课程别漏 version/player/reward）；②真课程全量验收（hub/隧道/账本实弹与 `(course, job_id)` 抽查）仍待用户批准，账本/端口/隧道半由单测覆盖。
- **P5 状态**：**全部完成**（W1–W5）；余 P6 归档（DECISIONS 总条目 + DoD 勾选）。
- **纪律**：配额只住 `rl-config.json` 的 `courses` 块，**永不写 `curricula/*.jsonc`**（一改 `course_fp` 即触发 D14 熔断误判，plan C1）。
- **门禁**：`bun run check` 2009 pass、`bun run build`、`make -C nn-training python-gate` 全绿。
- **merge 复核（85e740d，origin/goal-nn 并入）**：冲突 3 文件（进度日志节号撞车 → 我方改 §36；consoleStatePath 归宿取 paths.ts；停机横幅取按课版 + 追加对方 loopComplete 完训横幅）；两处测试随合并 API 修正（consoleStatePath 导入源、buildExitMarker 补 course 参）。合并后全套门禁绿（check/python-gate/build），**tiny-a/b 双课 5 轮流程验证重跑 PASS**（隔离证据与首次一致：stage 互斥/course_fp/rotateSeed 独立/零错误）。**行为变化知悉**：对方分支把「跑满 iters」从进程退出改为**停车不断开**（等待控制台重启 / EvalBoard B 批认领）——锁在停车期保持持有，验证完需按课停止（S14 kill-previous 被 preflight 挡住时直接 SIGTERM 停车 PID，残留 stale 锁由下次启动的接管路径自动清理）。
- **merge 复核 2（00e387d，关卡抽离 + corpus_fp 语义血缘并入）**：publish_job 的
  `course_name`（归属短名）与 `corpus_fp`（D14 语义血缘）两 kwarg 并存，调用点双传；
  进度日志节号二次撞车 → 对方节改 §37 置顶。合并后 check/python-gate/build 全绿；
  **tiny-a/b 双课 5 轮重跑 PASS（24 秒）**，且新字段全链落位：shard manifest 同时携带
  course_fp（761b29…/9e33ef…，与历次隔离证据同值）与 corpus_fp（4bd04f…/674cdf…，互异）
  ——归属审计与语义熔断两套字段互不干扰。预算类课程编辑不再触发 D14 拒收（§37 分类学）
  不改变本节纪律：机器配额仍只住 rl-config 的 courses 块。停车进程验证完即 SIGTERM 清理。

---

## §35 wDmg 死项修复：c6-dmgfix 基座腿就绪（2026-09-13，用户拍板「先做」）

**机制定案（代码核验，DECISIONS §2026-09-13-reward-wdmg-dead-term）**：玩家非致命命中推
`player_damage`（累计 `playerDamageTaken`）、致命命中推 `player_hit`（`playerHits++`；
另一触发 = 3★ 星盾消耗，本族课程不可达）⇒ **1 命课程 `playerHits` 恒等于败局指示器**
（实测败局分布 {1:110, 2:1}）。`- wDmg*playerHits` 因此是死项：零挨打信息 + 与
`terminal.lives_exhausted` 重复扣败局分（败局 −2 而非 −1）+ 伪装承伤惩罚（调它 = 调死刑）。

**修复（c6-dmgfix.jsonc，预检 validate_reward ok/零警告）**：

- 由 c6-pickup 派生、**唯一训练变量 = 删 `- wDmg*playerHits` 项 + 删 `params.wDmg`**；
  wChip 保持 0.005（剂量轴不碰——0.03 已被 c6-chip 判保守化，0.01-on-c6 留待后续腿）；
  bc=c6-pickup.it35（与 chip 三腿同起点）、iters=60（同 c6-chip 依据）、eval 200。
- 历史课程**不回改**：死项是每败局常数 −1，只平移败局回报、不改局内 credit assignment
  时序结构，已收官结论仍成立；自本腿起新课程模板不再含 wDmg。
- 定位诚实声明：机制修复 ≠ 能力突破，预期效应温和（败局回报 +1.0 的梯度软化）；
  本腿同时是**后续腿的诚实模板基座**（plan-B「满压击杀加成」将派生自本配置）。
- 判据（守门轴来自 c6-chip 的教训）：配对 kills/pickups/shots **不降**（z<−2 即警）、
  dmg/kill 不恶化、胜率 ≥ 平；entropy<0.32 / timeout>0.15 熔断；探针场地 fmap-c6l1。

**背景**：c6-chip（wChip 0.03 on c6）it15 配对已现保守化——kills −0.36（z=−2.38）、
pickups −0.27（z=−2.91）、shots −1.8（z=−2.03）显著降，胜局内 dmg 158→135（−15%，杠杆
本意生效）但败局占比 134→149 席：**0.03 的承伤价格在 c6 经济（6 敌/长局/高道具密度）里
把 wPickup/wKill 激励挤出去了**——c4→c6 迁移损失量得，0.03 不可迁移；该腿跑完 it30 收
正式探针后关账，不续 60。

---

## §34 c4-chip03 关账 + c6-chip 主腿备好（wChip 剂量-响应定案）（2026-09-13）

**c4-chip03（wChip 0.03）it30/30 收官，正式配对探针**（同 §32 协议，200 局 seeds 0-199，
it30 vs bc，逐局证据 `tmp/c4chip03-it30-probe.jsonl`）：

- **dmg 配对差 −24.8±7.6（z=−3.29）**，dmg/kill 49.9→**39.4（−21%）**——主判据大幅成立。
- kills **+0.2（z=+1.68，微升）**、ticks 平（z=+0.87）、胜率 +7pp（McNemar 净 +14，p≈0.17）——
  **无保守化**；探针零超时；胜局内部 dmg/kill 32.1→24.6（−23%），败局 dmg −6.2（z=−0.69）
  不恶化；hazard 无新失败模式（k1 桶 31→22 收窄）。
- **剂量-响应定案**：0.01→−13.7 / 0.03→−24.8（3× 剂量 ≈ 1.8× 效应，单调、未饱和、无坍缩）
  ⇒ wChip 杠杆在 c4 上**确认有效且还有上行空间**，两腿均达标。
- 附：in-loop eval 的「100→200 局」升级在 chip03 全程**未生效**——`EVAL_SEEDS` 仅 100 个
  种子，`[:200]` 静默截回 100（2026-09-06 同型坑：100 曾被截成 20）；判决依赖正式探针未受损。

**EVAL_SEEDS 100→200 修复**（§7 测试先行，nn-python-gate 绿）：

- `rl/eval_local.py`：语料扩至 `range(860001, 860201)`；前 100 seed 逐字节不变 = 旧口径兼容
  （消费方全部 `[:n_seeds]` 前缀切片，已核验）。`tests/test_rl_remote_fixes.py` 钉新契约
  （len 200 + 前缀 100 逐字节 + 860101/860200 边界），红→绿。

**c6-chip 主腿已备好**（`nn-training/curricula/c6-chip.jsonc`，启动前预检通过：
`load_course('c6-chip')` + `validate_reward` ok/零警告）：

- 由 c6-pickup 派生，4 处改动：名/out/traj/backup → c6-chip（fresh）、bc → c6-pickup.it35
  （与 c4 两腿同 bc，三腿跨关对照）、**wChip 0.005 → 0.03**（按头注释三档定档规则判入线性档，
  chip03 dmg差 −24.8 ≤ −20 且 dmg/kill 39.4 ≤ 42 且 kills 不降）、iters 160 → 30（首读段，
  正收益经控制台停止→启动改大续跑）。不配 `gates` 块（先例：notify 不停车）。
- 起点 = bc 零样本 c6 关 **32%**（§27 实测口径）；主判据同 §32：dmg/kill ↓ 且 kills 不降；
  探针场地 `tmp/fmap-c6l1.jsonc`（与 c6-pickup stage 逐字段核验一致）。
- 下一步判读：c6 上剂量-响应若迁移成立（dmg/kill 显著降 + kills 不降）⇒ 续跑 + 剂量继续上行；
  若保守化 ⇒ 停，转 wDmg 死项修复（`-wDmg*playerHits` 在 1 命课程 = 重复计败局分，§32 背景已记）。
- **修订（2026-09-13，启动前用户质疑 iters=30 偏少，核证据后采纳）**：iters 30 → **60**
  （40 热相 + 20 冷却 = ppo_schedule 完整形状）。依据：① c4 证据表明效应慢热
  （chip01 dmg 差 it10 z=−0.27 → it30 才 z=−2.13），30 轮硬停在 c6 上有假阴性风险；
  ② c6 无 30 轮出结论先例（c6-pickup 前 35 轮 in-loop 全是 27-38% 噪声摆动，c6-gae 判决
  用 110 轮）；③ 60 = 去掉硬停不是承诺——探针每 10 轮出趋势 + 熵熔断/止损/人读护栏，
  证据坏了随时早停。成本 ~2.2h；定档规则段同步改为「已应用」时态。

---

## §33 python 门禁 flake 全面审计：静态扫雷 + 负载轰炸（2026-09-13，§31 后续）

**方法**：① 静态扫雷——全测试目录 grep 紧墙钟（sleep/wait/timeout 断言）、被采样日志行
依赖（RACE_LOG_SAMPLE 类）、固定端口、mtime 排序、xdist 共享 tmp 撞路径；② 历史证据——
git log 的历次 flake 修复（`82cc6d6` I9 假红、`b0317ad` 沙箱删除配额、§31）+ tmp 红跑
日志；③ 实证——全量 541 项 × 5 轮禁用 `-x`（首败不停、收全部失败）轰炸，其中 2 轮带
4 spinner、叠加真机 c4-chip03 训练负载；I7 定向 10 连跑（6 spinner）。

**发现与处置**：
- **I7（`test_it_eval_deferred`）set-then-clear 竞态 + 3s 紧等待 → 已修**：
  `eval_th.start()` 之后才 `eval_dispatched.clear()`——负载下主线程若在 start→clear
  之间被调度延迟数秒，eval 线程先置位再被清掉 → 必假红（与 I9 §31 同族）。修法：
  clear 提前到 start 之前（置位必属真实派发）+ 等待 3s→30s（正常 ~10-100ms，覆盖
  ping→POST 权重→worker 孵化→首局 fetch 全链路的负载放大）。
- **I10（tail join grace）`took10 < 15s`**：对设计的 2s grace 有 ~4× 余量，观察保留。
- **其余全部干净**：端口全 bind 0（ephemeral）；mtime 仅等值断言；tmp_path 已由
  conftest 唯一化（pid 参与命名）；`test_dist_common_poll` 的 `dt < 5s` 有 4-5× 余量；
  test_upgrade 的短超时是故意触发降级路径的合法输入；I1/I3 的排序断言走真实回调与
  服务器事件、不经过可采样日志；P0 新增 test_loop_gate_soft_remediate 纯逻辑零时序。

**实证结果**：5 轮全量 2705 次执行零失败（19.4s / 21.3s / 19.8s / 24.8s / 54.4s——
末两轮带 spinner + 真机训练负载）；I7 定向 10/10 绿。结合 §31 修复前的历史红跑，
目前门禁内已知 flake 清零；`-x` 首败即停是门禁的有意设计（快速反馈），审计口径
须用 `-o addopts=` 覆盖。

---

## §32 c4-chip01 关账：wChip 0.01 主判据「小但真」成立（2026-09-13）

**腿**：wChip 0.005→0.01 剂量快筛（c4 关，bc=c6-pickup.it35，30 iters）。it30/30 正常收官
（~57 min；门判 PAUSE→notify 不停车；全程 KL≤0.007、entropy≥0.329、500 局 eval 零超时）。

**正式配对探针**（`eval-course-ckpt.ts`，fmap-c4l1，200 局 seeds 0-199，it30 vs bc 逐局配对，
逐局证据 `tmp/c4chip01-it30-probe.jsonl`）：

- 胜率 62.5% vs 60.0%——McNemar 翻盘 35/40，净 −5（z≈0.46）⇒ **持平**。
- **dmg 配对差 −13.7±6.4（z=−2.13）**；kills +0.0（z=+0.10）；ticks +9.8（z=+0.23）。
- **dmg/kill 49.9→45.3（−9.2%）⇒ 主判据 ✅ 成立（小效应）**，且 kills 不降、ticks 不升
  ⇒ 非保守化，也不是 `-wTick` 伪影（it10 时唯一显著项是 ticks，it30 已消失）。
- **分桶排除构成混杂**：both_win dmg −13.6（z=−1.27，n=85）、both_lose dmg −16.9
  （z=−1.99，n=40）——胜局、败局两类内部都降价；both_win dmg/kill 28.7→25.3、
  both_lose 141.6→132.4；hazard 结构不变（胜局全 k4）。
- in-loop eval（100 局 860xxx）中段横盘（47.8→43.9→…→47.9）但 **it30 终点 44.8 与探针
  45.3 相互印证**——100 局单点 SE≈±5 解析不了这个量级，中段波动是噪声；200 局配对+分桶
  是最低配置（c4-chip03 把 eval 提到 200 局正是为此）。

**修正 it10 中期判读**（c4-chip03 头注释「0.01 弱到策略可直接忽略」）：那是 it10 时点读数
（dmg 配对差 −2.3，z=−0.27）。it30 时 dmg 效应长到 −13.7 ⇒ **0.01 = 弱信号但非零，效应随
训练慢涨**。chip03（0.03，已于 08:21 经控制台开跑）的剂量-响应问题（线性 vs 饱和）以本条
为基线判读；训练中的课程文件不动（变更检测会重启腿），故修正只记此处。

**经验**：快筛腿的「每 10 轮配对探针」协议有效——in-loop 100 局 eval 三轮完全相同（65/65/65）
+ McNemar 翻盘 32 局的「在动但净零」读数，到 200 局口径收敛为「dmg 显著降、胜率平」；
小效应判据必须配大 n 配对 + 分桶，否则会在 it10 误判为「零信号」。

---

## §31 I9 长尾竞速测试 flake：两条失败路径 + 双通道断言修（2026-09-13 复核 `python-flaky.issue.md`）

**问题（复核确认真实并复现）**：`test_run_rl.py::test_it_early_race_v314` 在 xdist -n 4
门禁下偶发红（他机 2/3；本机 8 CPU spinner 负载下复现，`tmp/pygate-flaky-repro2.log`；
4 spinner 与单跑 ×8 均绿）。与 P0 提交 `d17e9f0` 无关（stat 确认未触及调度路径）。

**根因（比原 issue 的分析多一条路径）**：断言的证据链有两层隐性依赖，负载下各自翻车——

- **路径 1（原 issue 发现）**：竞速检查的墙钟时刻。空闲槽无任务可派时按
  `all_settled.wait(0.5)` 空转——**轮询粒度 0.5s 本身 > 0.4s 慢窗**；xdist/沙箱负载下
  首个竞速检查实测晚至 +1s，seed111 已结算离场，race lane 只能命中剩余任务（红跑日志
  命中 `(3,9006)`）。
- **路径 2（本次复核新发现）**：证据通道被采样。`741c395` 起 race drops 每类只打前
  `RACE_LOG_SAMPLE=2` 条；而 v3.7 突发 fanout **不打派发日志**，会在派发突发期抢占
  慢任务的 dup 槽——快副本 0.01s 获胜即把任务弹出 inflight，race lane 从此**结构性
  选不中它**，慢主副本的证据行（`main_by_fanout` 类）落在第 3 条后被采样挤掉。
  复现红跑（repro2）正是此形态：race 命中 `(2,9005)`，`main_by_fanout=3` 只打 2 条。

**修（test_run_rl.py，三处）**：
1. **慢窗 0.4→3.0s 上限（FakeAgent）+ 竞速副本到达即提前放行**：远大于轮询粒度 +
   观测抖动（+1s）；慢 handler 每 50ms 轮询同键并发 fetch 计数，≥2（= 竞速副本已
   派出、被测性质已成立）即提前返回——窗口只在回归（无副本）时才睡满，正常路径单轮
   回到**亚秒级**（实测 I9 0.38s / I6 0.4s，比 0.4s 窗时代还快，门禁墙钟无净增）。
   修掉「v3.15 判据不依赖窗长」的错误注释。round 仍远低于 30s 死锁兜底。
2. **I9 测试 cfg 设 `tailFanoutN: 0`**：突发 fanout 是唯一不打日志的复制通道，关掉它
   让 race lane 成为唯一复制路径（fanout 语义由 I6/longtail 测试覆盖）——证据不再
   依赖「seed111 未被突发复制」的时序运气。
3. **断言改双通道 OR**：日志行（tail-race 等，不采样）**或** FakeAgent 派发计数
   `(0,111) ≥2`（对采样免疫，与 I6/longtail 既有断言同款式）。2026-09-06 曾硬断言
   计数而"HTTP 事件偶发缺席"——OR 化吸收该教训；回归（`pick_race_target` 断）时两
   通道同时缺席必红（已反验：patch `return None` → 红 → 还原；提前放行下仍成立，
   因无副本时慢窗照旧睡满）。

**验证**：单跑绿（`log evidence=True, dispatches=2`；提前放行后 I9 0.38s / I6 0.4s，
较 0.4s 窗时代还快）；反验红（两种窗口形态下各验一次）；门禁 5 连（第 2/4 次带
6 spinner 负载）+ 优化后 3 连全绿。教训：**「结构确定性」判据仍可能依赖墙钟
时序**——写竞速/超时类测试时，慢窗必须按「轮询粒度 × 安全系数」取值；改日志采样
（RACE_LOG_SAMPLE）前必须 grep 测试对被采样行的依赖。

---

## §30 CLI 子进程编码契约：环境无关的三层修（2026-09-13 复核 `python-cli.issue.md`）

**问题（复核确认真实）**：`test_gate_check.py::test_cli_dry_run_exit_code` 用裸
`subprocess.run(..., text=True)` 捕获 `rl.gate_check --json`——父侧解码编码 =
`locale.getpreferredencoding(False)`（**解释器启动期决定，运行时改不了**；zh-CN
Windows = cp936），子侧却由启动环境任意决定（`ensure_ascii=False` 把中文直排进
stdout）。子进程 UTF-8（agent 沙箱常设 `PYTHONIOENCODING=utf-8` 且无 `PYTHONUTF8`）
× 父进程 cp936 → 读线程在 `subprocess._readerthread` 死亡 → `stdout=None` →
`json.loads(None)` TypeError。本机复现矩阵证实：仅 `PYTHONIOENCODING=utf-8` 必红；
`PYTHONUTF8=1` 两侧都 UTF-8 则绿（**它掩蔽而非修复**）——不同 agent 沙箱 env/代码页/
Python 版本（3.15 起 PEP 686 默认 UTF-8）各异，环境解不可能通用。

**修（契约从环境移进代码，三层）**：
1. **`--json` 机器通道改 `ensure_ascii=True`**（`rl/gate_check.py`）——纯 ASCII 字节对
   任何解码器免疫（含我们控制的裸 text=True 父进程与不控制的第三方 agent）；中文经
   `\uXXXX` 传输，`json.loads` 还原无损。人类可读走非 `--json` 分支。
2. **子侧入口钉死**：`platform_utils.force_utf8_stdio()`（运行时 `reconfigure`
   stdout/stderr 为 UTF-8，实测压过强设的 `PYTHONIOENCODING=gbk`），`gate_check.main`
   与 `run_rl.main` 接入——被测 CLI 的字节流恒 UTF-8，与环境解耦。
3. **父侧测试统一出口**：`tests/subproc_util.run_utf8()`（强制 `encoding="utf-8"` +
   `stdout is None` 就地断言），扫掉全部 7 处裸 `text=True`（test_gate_check ×2、
   test_run_rl、test_upgrade ×2——后两处 spawn 的 **bun 管道恒 UTF-8**，本就是同型
   雷点、test_no_torch_on_import ×2）。

**验证**：四场景矩阵（无强制/仅 PYTHONIOENCODING/PYTHONUTF8/沙箱原样）全绿；对抗
探针（子进程强设 GBK env）下 `--json` 输出纯 ASCII、verdict/report 无损；nn-python-gate
全绿。生产侧同模式捕获点（`dist_common` ×2 / `run_rl` 的 git 调用 = ASCII 输出、
`bootstrap` 已 `errors="replace"`）不急；新 subprocess 测试一律用 `run_utf8`。

---

## §29 it0 基线评审修订：重试语义 + 账本双向隔离 + Hero NaN 行（2026-09-13 评审）

对 §28 的 staged 实现做评审后发现三处问题，本条为修复记录（评审 + 修复同一批完成）。

**问题与修法**

- **P1（鲁棒性）基线派发是「单次尝试 + 纯远端」**：原实现只在 `it == _start_it` 派一次且不传 `local_gate`——`EvalDispatcher` 无 gate 时不建权重快照、不启本地 worker（纯远端），若首派时刻节点瞬时全挂/权重 POST 全失败，只记日志跳过，进程内永不重试（docstring 的「失败重试轮」只有跨重启才成立）。改为**落账前每轮重试**：`baseline_summary_landed(traj_dir, wver16)`（eval_local）查 eval_log 是否已有**同 bc 指纹**的 `iter=0` summary，未落账则每轮 rollout 收官后重派，账本去重保证重试只补缺口；落账 wver 缓存于 `_baseline_landed_wver`（bc 换文件 → 新指纹 → 重派新基线）。同时复用当轮 `self._eval_gate`（与 A-eval 同一把门，`_join_eval` PPO 收官置位）——基线本地局与 A-eval 一样让位 PPO；快照文件按流分流（`_eval_frozen_weights-baseline.json`），基线与 A-eval 并发重试轮不互相覆写快照（覆写会让 A-eval 本地局读错权重）。summary 带 `dropped` 也算落账：缺口在控制台诚实显示「缺N」，不为填缺口无限重跑失败局。
- **P2（潜伏）账本互吞只防了单方向**：基线账本按 `iter==0` 隔离了「A-eval 行吞基线」，但 A-eval 账本仍无过滤——若 bc 与 it1 的 `args.out` 指纹偶同（手动拷贝 bc 为 `--out` 启动等路径），it1 中途崩溃重启后 A-eval 会被同 wver 的 it0 行整轮跳过。`eval_done_keys` 新增 `min_iter` 参数：A-eval 传 `min_iter=1`，把 int iter<1 的行挡在去重外；缺 `iter` 的旧行与同 wver 跨 iter 复用（零梯度轮 args.out 未变 → 下轮免重评）照旧保留。顺带发现 **B/C evalboard 行与 A-eval 同册同 `event:"eval"`**（batch_eval 写 `source:"B"/"C"`，畸形批的 iter 缺省还是 0）——基线账本额外排除带 `source` 字段的行，B/C 局不得被当成基线已评估。
- **P3（用户可见 UI）Hero 主表漏入 it0 合成行**：`MetricsTable.buildRows` 有 `iter>0` 守卫，但 Hero「最新 6 轮完整指标」是另一套渲染（`[...iters].sort(desc).slice(0,6)`），腿的前 ~5 轮 it0 必进前 6——合成行的 NaN 字段直接渲染成红色 "NaN%" 徽章、"NaN" 单元格（`fmtPct(NaN)` 只判 `typeof number`）。抽纯函数 `heroMainRows(iters)`（view.ts：过滤 iter>0、倒序、截 6）供 Hero 使用，测试三例钉死。
- **nit**：显式 `--start-it 0` 会与基线的 dist 键空间 `{runId}.0` 撞键（采集/A-eval 任务键 = `{runId}.{it}`）——`validate_args` 启动期拒绝 `<1`；Hero 配对裁判文案 `vs开腿itN` 在 baseIter=0 时改为 `vs bc基线`（it0 是训练前基准，不是开腿首轮）。

**验证**：`test_baseline_eval.py` 重写派发语义测试（落账前重试/在飞跳过/local_gate 复用钉死/落账停/bc 换文件重派）+ `eval_done_keys` 双向隔离（含 B/C source 行与缺 iter 旧行）+ `baseline_summary_landed` 直测 + `--start-it 0` 启动期拒绝；`console-paired.test.ts` 新增 `heroMainRows` 三例（it0 排除/截 6/端到端合成行进不了主表行集）。`bun run check` 绿（2028 pass）；nn-python-gate（ruff + mypy 145 文件 + pytest 全量）绿。

**遗留（未修，已评级）**：console `readEvalGameWins` 仍按 iter 合并 B/C 行（iter≥1 的既有口径，B/C 批恰与 A-eval 同 iter 时配对图混源）——影响面在 evalboard 触发路径，与基线无涉，留作后续单独处理。

---

## §28 it0 bc 权重基线评估：配对基准不再随 run 起点漂移（2026-09-12，用户指令）

**症状**：控制台的 in-loop eval 配对基准恒取 `eval_log.jsonl` 里**第一条** eval 行（`console/iters.ts` 的 `evalIters[0]`）。那条基准随 run 起点漂移：resume 时首条可能是 it50，于是 `vs开腿it50` 实际比的是中途两点，而不是「从起点学会了多少」。

**改法（纯增量，训练侧与 console 两侧）**

- **训练侧**（`rl/loop_core.py::_maybe_dispatch_baseline_eval`）：在本 run **首次 rollout 收官后**（`it == _start_it`；全新腿 = it1）立刻用课程 `args.bc` 派一条 `iter=0` 的干净评估作恒定基线。守卫 `_baseline_eval_weights`：per-tick / 有课程 / `eval_games_per_stage>0` / `eval_every>0` / 有 enabled dist 节点（`nodes=[]` 纯本地路径本就不派 A-eval）/ bc 文件在盘；幂等（在飞跳过 + 跨重启按 `iter==0` 去重）；**失败自吞**（基线是观测设施，不得拖垮主线）。课程默认 `eval_every>1`（c6-bonus=5）⇒ 该轮本无 A-eval，**零重复计算**。
- **`eval_done_keys` 新增 `iter_filter`**（`rl/eval_local.py`）：it0 的已评估账本按 `iter==0` 隔离。必须如此——bc 与 it1 的 `args.out` 指纹**可能相同**（it1 就是 PPO 前的 bc 初始化权重），只按 wver 去重会让 it1 的 A-eval 把基线局吞成「已评估」，it0 行永远不落盘。`dispatch_eval_round/bg` 与 `EvalDispatcher` 加尾参 `baseline=False`；it0 用独立 `iter_id = {runId}.0`（与 A-eval 的 `{runId}.N` 在 agent 结果缓存里键空间隔离）。
- **门判据排除 it0**（`gate_check.read_trend_rows`）：`iter <= 0` 的 summary 不进趋势（用户定案：只当监控/配对基线）。否则会虚增 sustain 的「连续通过」计数、把 plateau 的上升趋势起点拉回 PPO 前。缺 `iter` 字段的旧行照旧保留（不过度收口）。
- **Console**（`console/iters.ts`）：配对基线改为「有 it0 取 0，否则退回首个 eval 轮」（老腿逐字节兼容）；`readIterMetrics` 在有 ≥1 条真实 iteration 行时合成一条 it0 行（只有 `evalData`，rollout 派生字段一律 `NaN` —— 趋势图的缺口约定，写 0 会在图上多画一个假零点）；`MetricsTable.buildRows` 跳过 `iter<=0` 的主行，只出 eval 子行（UI 标「基线」）。

**验证**：`tests/test_baseline_eval.py`（新，6 例：iter 隔离 / 守卫逐条反证 / 只在 `_start_it` 派一次 / 失败不抛 / **端到端** baseline 派发把逐局行与 summary 都写成 `iter=0` 且预置同 wver 的 it1 行不吞它 / 幂等）+ `tests/console-paired.test.ts`（新增 3 例：it0 为开腿基准、无 it0 退回首个 eval 轮、合成行与老腿兼容）+ `test_gate_check.py` 的 `read_trend_rows` 过滤。门禁：nn-python-gate（ruff + mypy 145 文件 + pytest 全量）绿；`bun run check` / `bun run build` 绿。

**遗留**：`eval_every == 1` 的课程在 it1 既有 A-eval 又有 it0 基线（测的是同一套 PPO 前权重）⇒ 会多跑一遍语料（账本按 iter 隔离，it0 行仍落盘）；本腿 c6-bonus 为 `eval_every=5`，不受影响。

---

## §27 metrics v5（`clearTick`）+ outcome 虚拟符号：修复加列只改了一半（2026-09-12）

**背景**：c6-bonus 修「清场后 BONUS TIME 窗口被 max_ticks 截断 ⇒ 歼灭局吃 `terminal.timeout=−2`」需要两个新能力：指标列 `clearTick`（idx30，哨兵 −1）与公式可访问 outcome（虚拟符号 `is_timeout` 等，不占列）。设计侧验证通过：`wClear=4.0` + `wBonusTicks=600` 让「清场+超时」与 `stage_clear` 总回报相等（实测两边均 −14.0）。

- **P0（已修）**：`tools/sim/export-rl-rollout.ts` 只改 `metricsRow()` 的行、`METRICS_DIM` 仍为 30 ⇒ `writeRlShard` 的 `metrics.set(row, i*30)` 在**每局终局行**越界 `RangeError`，整条 RL 采集腿零产出；`tsc`/TS 测试全看不见（`number[]` 无长度类型、无行宽断言）。修：常量改 31 + 行构造提为 `export function buildMetricsRow` + 新增 `tests/export-rl-rollout-metrics.test.ts`（行宽 / 两种哨兵 / 与 Python `METRICS` 条目数跨语言对账）。e2e 复核：1 局 → `metrics.npy (41,31)`、`metrics_version=5`、未清场列 −1。
- **Python 门（已修）**：① `reward_validation.DEFAULT_RANGES` 缺 `clearTick` ⇒ `validate_reward(course)` 抛未捕获 `KeyError`；现改为带列名的 `FormulaError`（由 `validate_reward` 归入 errors），并新增 `test_all_metrics_have_envelope_range` 锁「加列必须登记域」。② `symbolic_envelope` 对引用虚拟符号的加性项不带 outcome 调 `phi` ⇒ FormulaError 被当成数值爆炸记 `inf/超限`（假临界）；现按**每个真实 outcome** 各求一遍取峰值（比「全 0 虚拟」忠实），角点回映加取模。③ `tests/golden/v7_phi_ts_oracle.json` 仍是 30 列 ⇒ 重生成；**`phi` 逐位不变**（v7 不读 clearTick），纯宽度同步，非重新标定。④ `test_item_metrics_layout_locked` 尾部清单补 `clearTick`(idx30)。
- **门禁**：nn-python-gate（ruff + mypy 144 文件 + pytest 全量）**绿**；`bun run check` **2022 pass / 3 skip / 0 fail**；`validate_reward(c6-bonus)` = ok、errors/warnings 均空，`wClear` 项 max_abs=4.0。
- **reward golden 补真实覆盖（已做）**：原先 60 个 case 的 `clearTick` 全是 `0.0` ⇒ `wClear` 是常数项、diff 恒 0，补偿/豁免**零覆盖**；且 `0.0` 是合法值（「第 0 tick 已清场」）而非「未清场」。改：① `_v7_corpus` 显式写哨兵 `-1.0`（对不读该列的 5 门课 reward **逐位无影响**，已验证 60/60 不变）；② 新增 `_clear_metrics()` + c6-bonus 专属单调行序列（tick 0→3000、清场于 tick=600 ⇒ 封顶上界 1200）共 4 case（cleared × {timeout, stage_clear}、uncleared × {timeout, lives_exhausted}）；③ 差分验证：`timeout/cleared` 去掉 `wClear` 项 Δ=**+4.0**、去掉 tick 豁免 Δ=**+18.0**，其余三种 Δ=0 ⇒ 两个新机制都真正参与；④ `cleared-timeout` 总额 == `stage_clear` 总额（均 −10.0），设计意图入 golden。golden 60→64 case，原有 60 个 reward 逐位不变。
- **c6-bonus 起点/教师在新口径下重测（已做）**：新 `win = stage_clear ∪ cleared` 抬高同一权重的读数，而 `gate_check.py` 直接吃 `teacher.wins/games` + eval 行 win_rate ⇒ 两个基准必须同口径重测，否则"学生被抬高、基准仍旧"会白过门。实测（`eval-course-ckpt.ts --course tmp/c6-pickup-strict.jsonc --seed0 860001 --games 100`；旧记录的 kills 3.54 / phits 0.57 / zero_kill_frac 0.14 **逐位复现** ⇒ 只口径变）：bc `c6-pickup.it35` 31/100→**32/100**（`baseline_win_rate 0.31→0.32`）、教师 42/100→**43/100**（`teacher.wins 42→43`）、教师 `timeout_frac 0.03→0.02`（剔除"已清场被截断"，同 `evalboard/stats.ts`）。G1 有效门槛随之 0.36→**0.37**。逐局证据 `tmp/c6bonus-{teacher,bc}.jsonl`。同时订正 c6-bonus 头注释 4 处与磁盘不符的说法（"只改 2 处 / gates 全锁死"、"gates 原样继承"、"报数并列 win/cleared 两口径"、params 名 `wTickFree`→`wBonusTicks`）。
- **仍欠**：① metrics v5 + `win` 口径变更的 `DECISIONS.md` 条目（含 golden 覆盖扩充与 `_v7_corpus` 哨兵改写的理由）。② **其余课程的基准仍是旧口径**：`c6-pickup2`/`c6-pickup3`（0.31 / 42）、`c4-dodge`（0.72 / 68）、`c6b-margin`（0.22 / 42）——凡还有在跑的腿，需按各自 stage 重测；`gate_check.py:293-298` 的 timeout_frac 回退分支仍按**原始 outcome** 算（与 TS 侧新口径不一致，旧行才走到该分支）。

---

## §26 门判决永不停车 → 只联动云机停机/恢复（2026-09-11，用户定案，DECISIONS §2026-09-11-gates-never-park-loop）

- **事故与根因**：c4-dodge 一天内两次"设计内停车"——G13 占空比（it7/it8）与 G4 plateau（it36，win_rate 0.69→0.74 横盘 6 轮）。原语义"非 HOLD 门判决即停车"（§4.3）让 loop 每次停车 → console exit-watchdog 按 §385 自动 halt 云机 + 红横幅，loop 等人手重启，每 ~6-7 轮一次停线。
- **现场误判**：kaggle 收到停机达令后日志静默（纯达令分支不打 60s 存活日志）被读成"罢工"——实际 worker 每 5s 照常轮询（无 poll failed），只是 halt 期无任务可接。
- **改动**：① `rl/loop_guards.py`：`_park_on_gate`→`_apply_verdict`，非 HOLD 判决仅落 `gate_verdict` 事件并返回 False（继续训练）；新增 `_sync_cloud_halt`——REMEDIATE/PAUSE/ABORT→hub halt，HOLD/ADVANCE→resume，实例态 `_cloud_halted` 防重发；② `remote/hub_client.py` 新增 `set_cloud_halt`（console 与 loop 共用 /admin/workers/{} 端点）；③ `remote/worker.py` 停机达令分支补 60s 存活日志。停车只剩预算到顶/F4 熔断/止损 2σ/远端不可用 ABORT 等硬边界。
- **验证**：新增 `tests/test_loop_gate_nopark.py`（REMEDIATE 不停车+halt、HOLD resume、STOP 不发、无 hub 短路）+ `test_remote_hotswap.py` 停机期存活日志 + `test_remote_ppo.py` set_cloud_halt 链路；gate_check/degrade/run_rl -k gate 全绿，mypy 干净。
- **遗留**：loop 直连 hub 的停机不走 console-state（`cloudHalt`），控制台红横幅不会显示门引发的云机停机；如需横幅需 console 侧从 hub /admin/workers/status 同步状态（待办，未做）。

---

## §25 TPU PPO「单步递增爆炸」根因定案 + 修复（2026-09-11，c6b-margin 首个 TPU job 45~52s/step / eta 5.9h）

**用户动作链**：贴远程 log（job 6c0a0424488e5b70）→ 探针 E 段复现（Colab）→ Kaggle TPU 三重否决 → `--step-mark` 收敛 44ms → engine 生产修复。

### 25.1 现象

- 真实 job：142 chunks × 4 ep = 568 步，单步 **24→45→52s 线性递增**（eta~5.9h），hub wait_job(1800s) 必超时。
- 探针 E 段复现（Colab/Kaggle TPU）：E0/E1 均 1.7→13~24s 递增；**E2 干净基准（bench_case 已 warmup）亦 ~10s/step**；`--tail 0` 无尾块、3 chunks 固定 shape 仍递增 → 与 engine 结构 / ref / 尾块 / perm 全部无关。

### 25.2 根因（mechanism）

torch_xla 惰性模式下，`.tolist()` materialize 只断言其依赖子图；`backward()`/`optimizer_step()` 的在途节点不 drain，跨步骤累积 → **编译/执行的图线性变大 → 单步耗时 ∝ 已走步数**。CPU/CUDA eager 即时回收，本地永远看不见（所有"本地 OK、云端 TPU 爆炸"的旧困惑都源于此）。

**连带修正**：`plan/ppo-optimization.plan.md` §0.5「TPU 快 GPU 4.7× / 44 ms/step」是**测量假象**——那次 `chunks=1 epochs=1` 总共 ≤3 个梯度步，递增尚未展开；44ms 真实吞吐需「每步有 mark」的形态（探针 E1+mark 实测 1803→109→71→44→41→42ms）。以往所有 TPU 端 ppo_sec 读数需按此重读。

### 25.3 修复（生产落地）

| 位置 | 改动 |
|---|---|
| `ppo/engine.py::ppo_update` | 每步 stats append 后 `xla_mark_step(device)`（图执行边界；非 XLA no-op，CPU/CUDA 逐位不变） |
| `ppo/engine.py` | ref 预计算后一次性 mark（防首步巨图编译） |
| `ppo/common.py` | `xla_world_size()` helper（新版 `torch_xla.runtime.world_size()` / 旧版 `xm.xrt_world_size()` 双探） |
| `remote/worker.py` | TPU 分支日志：device + world_size（H2 诊断不再静默） |
| `tools/tpu-probe.py` | E 段全套：`--engine/--per-step/--skip-e0/--tail 0/--step-mark` + XLA metrics 累计 + world_size 诊断 |

**验证**：探针 Kaggle 闭环 44ms；58 项相关 python 测试全绿（kickstart_cache/scalar_sync/common/numerics/no_torch_on_import/remote_hotswap/run_rl_m1）。

**待实证**：下一轮真实 TPU job（首步几十秒编译一次性，后续稳定 ~50ms，epoch 时长均匀）；hub 需重打包 code.zip 触发 worker sha 热替换。

---

## §24 云端停机机制（2026-09-11，用户指令：停机 = 停云端省 GPU 配额，本地进程都不停）

承接 §23 的"未决"：§385 只修了 G13 口径，停车仍不省云配额。用户确认语义后落地（DECISIONS §2026-09-11-nntrain-cloud-halt）：

- **hub**：`/admin/workers/{halt,resume,status}`（Bearer 鉴权，volatile）→ 置位后 `/jobs/next` 下发 `{"halt":true}`。
- **worker**：收到达令即干净退出（keepalive 停、cell 走完）——省配额的有效动作止于"worker 停 + 不再轮询空转"；
  Kaggle/Colab 无 release API，session 释放需手工断连或到 9h 上限（tpu-probe 调研，横幅文案如实写明）。
- **console**：exit-watchdog 在 TrainingLoop 死亡（含设计内停车与崩溃）时自动发 halt（幂等），
  console-state `cloudHalt{at,reason}` 持久化 → 顶栏红色横幅 +「恢复云端」按钮（另提供手动 `cloud-halt`/`cloud-resume` 动作）。
- **验证**：test_hub_workers_halt_flow（hub 侧 401/halt/resume）/ worker_loop halts（worker 退出）/
  poll_job 上浮 halt / cloud-halt.test.ts（幂等跳过、失败不写标、恢复清标）。
- 恢复路径：删停机标记 + hub resume 后，**必须重启云端 worker 会话**才重新入队（横幅明示）。
- **修订（2026-09-11 用户确认"停机≠省配额"）**：worker 退出≠云机释放——Kaggle/Colab 宿主会话不因此停止计费
  （Kaggle 无释放 API、Colab 空闲约 90min 才回收）。真释放只发生在：① Colab 部署下 halt 达令触发
  `google.colab.runtime.unassign()`；② 人工断开。横幅/动作文案改为**诚实警告**（配额照烧→必须人工断开），
  不再声称"省 GPU 配额"。测试断言同步（非 Colab 路径必须出现"手工断开"提示）。
- **最终语义（2026-09-11 用户五条，DECISIONS §2026-09-11-nntrain-cloud-halt-final）**：停机=发布"停机命令"
  而非杀 worker——{halt} 随 /jobs/next 同发（有任务同批、空闲单独）；worker 停机过渡**尝试一次**真释放
  （Colab unassign / 其余提示人工断开），**不退出、照常执行任务**（云机不闲置）；halt 清除复位。
  console-state cloudHalt 双态 halted/recovered：红横幅=停机中（含"停不掉继续干活"状态）；灰横幅=曾停机已恢复
  （保留历史，TrainingLoop 重启或手动恢复触发）；本地组件全程不动。

---

## §23 训练停车机制审计 + G13 duty 门修复（2026-09-11，c6b-margin 事故：每小时自停 2 次）

用户指令："TrainingLoop 近一个小时自行关闭了几次，请检查原因，分析问题" → 审计全部 11 个停车点 → 用户拍板"处理所有问题" → §385。

### 23.1 事故根因（数据核对）

- 09:29:33 首启 → it1 冷启动 **47.2min**（占分母 62%，分子仅 68.9s）→ 10:24:25 G13 duty=0.065 停车；
- 10:26:46 重启 → it4 首轮 9.4min（PPO 往返 8min/真训练 2min 排队）→ it6 duty=0.127 又停 → it9 必停 → **死亡螺旋**：
  G13 用「首条 run_start、终身累计、跨重启」口径——冷启动与停车死时间永久锁进分母，任何评估轮必响。
- 热态单轮占空比 42-53%（it5/it7/it8）其实都过 0.35——门不是阈值错，是**口径把"起步费"当事故**。

### 23.2 修复（4 处，§385）

| 位置 | 改动 | 验证 |
|---|---|---|
| rl/gate_check.py | G13 分母基线 = **首个完成迭代结束时刻**（起步热身不计账）+ 完成迭代 ≥2 守卫 + evaluate(only_kinds) | 事故读数回放：213s/463s=0.46 → HOLD（修前 0.065 必停）；c6 慢性 0.15 仍 REMEDIATE |
| rl/loop_guards.py | `_gate` 非评估轮只查 duty（每轮现形）；`_budget_hard_cut` 轮级 max_hours 兜底 | test_train_loop_pure / test_run_rl_m1 通过 |
| rl/loop_core.py | run() 接入轮级预算硬断 | — |
| tools/training/console/exit-watchdog.ts | 账本近 300s 有 gate_verdict/circuit_break → 标「已停车(原因)」非「意外退出」 | tests/exit-watchdog.test.ts 20 通过 |

实测口径：403/404 数字逐项对上（it3 Σcloud=213s/wall=3292s→0.065；it6 575s/4538s→0.127）。

### 23.3 未决（另立决策）

- **停车不省云配额**：pull 模式下停本机 loop 只停派活，Kaggle/Colab worker 按 max_idle(≥1h) 才退出——真省配额需要"停车连带 hub 停机 + 显式释放云会话"的动作链。
- 探针腿 c6b-margin 当前以 GATE_OVERRIDE（HOLD）人工止血跑至 it20 或 4h 预算；删文件后 G13 新口径即时生效（下轮进程重启加载）。

---

## §22 外围组件巡检：goal 热图静默常量（生产档目标策略失效）+ eval 墙损失测 + 我引入的 payload 回归（2026-09-10）

用户指令："检查一下其它组件（sampler-agent, cloudflared, src/nn, export-rl-rollout,
export-eval-game, ...）有没有 bug 或者可以优化的空间"。方法：3 个只读子代理并行 + **逐条回验**
（子代理给的路径与行号一律自核——本轮 5 条外部结论里 3 条是假报，见 §22.5）。改动前先做决定性复现。

### 22.1 ★`goalForward` 目标热图在生产架构下恒为常量（HIGH）

**病根**：wasm 卷积段跑完只回拷 `pooled`（256 B），`offBufA` 写进 wasm 线性内存后**从不搬回 JS**。

    conv-wasm.ts   feats(..., offBufA, offBufB, offBufC, offPooled)
                   pooled.set(f32At(offPooled).subarray(0, pooled.length))  ← 只有这一行回拷
    infer.ts       goalHeatmap[p] += w * this.bufA[...]                    ← bufA 只在非 wasm 分支被填

**决定性复现**（合成 h16/d2 与 h64/d8 合法权重，同一份代码）：

| 架构 | 主干路径 | 换输入后热图 max&#124;Δ&#124; | 热图不同取值 |
|---|---|---|---|
| h16/d2 | TS 手写循环 | 3.889e+2 | 676/676 |
| h64/d8 | **conv-wasm** | **0.000e+0** | **1/676（常量）** |

用真实生产 golden（`goal_net.py --golden --h 64 --d 8`，本次新增 fixture `goal-golden-wasm.json`）：
热图对 py 期望 max&#124;Δ&#124; = **1.245e+1**，而 engage 头 7.6e-6 正常。

**影响面**（生产必踩——`goal_net.py` 默认 `--h 64 --d 8`）：`export-eval-game.ts`（policy=goal 的
评估）、`export-goal-rollout.ts`、`export-counterfactual-goals.ts`、`src/nn/goal-executor.ts`。

**为何一直没被发现（结构性教训）**：TS 侧有**两套等价卷积实现**（TS 手写 / wasm），而 golden 是
**按档位分工**覆盖的——`goal-golden.json` 是 `h=16/d=2` 瘦身档（注释自陈"主干不触发 wasm"），
`student-golden-wasm.json` 是 `h=64/d=8` 生产档但**只覆盖 student 三头**（消费 pooled，回拷正常）。
⇒ 测试跑 TS 路径、生产跑 wasm 路径，两条永不相交，wasm 专属缺陷零覆盖。**新增头/新架构时，
必须同时补一条生产档 golden，别只补瘦身档。**

**修法**：`run()` 补 `bufA.set(f32At(offBufA).subarray(0, bufA.length))`；`runStudentConvWasm` 以
`bufA.length !== H*SP` 作为"架构不符"守卫（不符即退 TS 原路径，正确性优先）。回拷成本实测
**2.9 µs/次 ≈ features(6.9 ms) 的 0.04%** ⇒ 无条件拷贝，换掉"某个头悄悄读陈旧空间特征"这类静默 bug。

**回归**：新增 `tests/fixtures/goal-golden-wasm.json`（h=64/d=8，422 KB，同 student-wasm 量级）+
`tests/nn/goal-infer.test.ts` 一组 5 例：三头对 py golden（热图 ≤1e-3，修复后实测 1.1e-5，修复前
1.245e+1）+ **"热图随 obs 变化"**（通道 0/1 对调；修复前恒 0、676 格只剩 1 个取值）。

### 22.2 eval `baseWallIntact` 恒等于 `baseWallTotal`，baseIntegrity 被钉死在 1.0（MED）

`export-eval-game.ts` 只在 telemetry 初始化时赋值一次，循环内从不更新（对照：`export-rl-rollout`
每决策步重算、`export-observations` 每 tick 重算）。下游 `godai-score.ts`：

    baseIntegrity = 0.55 + 0.45·clamp01(baseWallIntact / baseWallTotal)   // 分子恒等分母 ⇒ 恒 1.0

**实锤（两份独立证据）**：

- **在野扫描**：`tmp/**/_eval_report.json` **173 份，100% `intact == total`**（含 100 个 gameover、
  61 个 stage_clear）——真实对局里墙被打掉却从不记录。
- **A/B（修复后重跑同一 stage/seed）**：s5/seed1 随机策略 → 终局 `5/8`（修复前必为 `8/8`）；
  s5/seed5 基地存活、墙被打到 `1/8` ⇒ `baseIntegrity = 0.606`（**修复前 1.0**）——即注释里
  "墙被打秃但基地还活着"这个领先指标此前完全丢失。

**修法**：终局（循环退出后、构造 `scorable` 前）取一次 `tel.baseWallIntact = countBaseWall(world)`。

### 22.3 我上一提交（`d183997`）引入的回归：陈旧 pending job 永不下架（MED，我的责任面）

`tools/training/hub.ts` 的 `drainStaleJobs()` 仍硬编码 `unlinkSync('payload.zip')`，而同一提交把
payload 容器改名 `payload.tar.xz`（`protocol.PAYLOAD_NAME`）⇒ `unlinkSync` 恒抛 → 被
`catch { /* already gone */ }` 吞 → `n` 恒 0 → **账本里所有陈旧 pending job 保持可领**
（`hub_server.claimable_job_ids` 以 `find_payload` 存在性判定），真 worker 会白烧 GPU 租约去跑
已死运行的局——恰是该函数存在的唯一理由。

**修法**：改为按前缀扫描 job 目录删 `payload.*`（而非硬编码全名），从根上免疫再次改名；`hub.ts`
该处注释同步更正。**回归**：新增 `tests/training-drain-stale.test.ts` 5 例（新名/旧名/两名并存
都被下架；非 payload 文件与已 completed job 不动；坏账本、缺目录不抛）。

### 22.4 `cacheHits` 少计（LOW）

`tools/agent/sampler-agent.ts` 的 `/v1/result` 轮询端命中 `resultCache` 时未累加 `cacheHits`
（提交端有）。trainer 轮询是主要取包路径 ⇒ 缓存命中率被系统性低估。已补。注：该字段目前**只产出、
仓内无消费方**（经 `/v1/status` 暴露），影响限于对外状态接口的口径正确性。

### 22.5 被证伪的假报（记录以免重走）

| 假报 | 证伪 |
|---|---|
| 子代理给的路径 `tools/dist/sampler-agent.ts` | **文件不存在**（只有 `tools/agent/`）——其行号与结论整体不可信 |
| "异步提交路径缺 resultCache 短路" | 两条路径都有（提交端 / 轮询端各一处） |
| "export-rl-rollout 每局重建模型，是可优化项" | `runOne` 确实逐局 `buildModelFromText`，但 `--pack`（生产路径）强制**一进程一局** ⇒ 只跑一次；实测 1.228 ms/次（90% 为 base64→f32），不值得改 |
| "`npy.ts` 解析校验不严" | `npy.ts` 只有 `writeNpy/writeShard`，**无 reader** |
| "cloudflared 日志泄漏" | `tmp/cloudflared-*.log` 归 `tools/tmp-clean.py`（`.log` 在后缀表内，保留 N 天） |

### 22.6 巡检确认为健康（勿动）

- `stepCloudflared`：隧道复用判定走**本地 cloudflared `/ready`**（而非穿隧道 ping）——避免 hub
  出网劣化造成假阴性，是对的；3 次重试 + edge 20 s 确认 + "失败保留基础设施"均为刻意设计。
- `src/nn/obs-encoder.ts`：`obs`/`scalars` 预分配 + `fill(0)`，无每 tick 分配。
- `conv-wasm.ts`：权重上传用 `uploaded !== stemW` 实例指纹守卫，越界有显式 `check()` 抛错；
  wasm 加载/运行异常**有** `console.error`——§22.1 之所以静默，是因为它发生在 wasm **成功**时。

### 22.7 门禁

`bun run check` 绿（tsc + check-decisions + `bun test --parallel`）；oxlint **0 error**（20 条既有
warning，非本次引入）；oxfmt 对本次改动的 6 个源码/测试文件 clean。

### 22.8 连带影响：`docs/goal-nn.progress.md` §3 的 goal 结论需重新解读

§22.1 的缺陷**必须在历史结论里对账**。`docs/goal-nn.progress.md` §1「性能实测」自证 goal 前向
探针跑的是 **h=64/d=8** ⇒ T9a 金丝雀那批 goal 策略实验**全部在 wasm 路径上**，即热图恒为常量、
argmax 恒选同一格。

- **`goal 0.05%`（canary ②）**：除已记录的 executor 短板，还叠加本缺陷 —— "目标选择轴从未生效"。
  该节"执行层短板与**目标选择**、学习无关"的归因**需修正一半**。
- **`goal-god 0.0%`**：零网络，**不受本缺陷影响** ⇒ "执行层无生存能力"这一结论**仍成立**。

已在 `docs/goal-nn.progress.md` §3 追加该注记。**卡 A0 的 goal 重测必须在修复后的代码上做**，
否则重测仍在测常量热图（原计划的重测因此不作数）。

---


## §21 PPO 吞吐：三设备实测 + ref 缓存 / 传输压缩 / 多卡落地 + TPU 设备层（2026-09-10）

> ⚠ **2026-09-11 作废「TPU 快 GPU 4.7×」结论**：44ms/step 是 ≤3 步微基准的测量假象，详见 §25（根因 = torch_xla 惰性图不 drain → 单步线性递增；修复 = 每步 mark）。本节其余（ref 缓存/传输/多卡）结论不受影响。

同一会话四条线：**度量**（三设备实测矩阵）、**优化**（ref 缓存 / 传输压缩 / 多卡）、
**扩容**（TPU 设备层）、**基建**（门禁两项修复）。数字来自自包含探针
`nn-training/tools/tpu-probe.py`（CPU/CUDA/TPU 通用）与一次真实 job 日志
（`826081bcb840f76d`）。台账：`plan/ppo-optimization.plan.md`。

### 21.1 性能画像，以及我三次被实测推翻的假设

一轮 = 37 chunks x 4 epochs = **148 个梯度步**。真实日志逐段（GPU 单轮 47 s）：

```
claim 08:28:02 -> payload 下行 3.83 MB (2.3 s) -> code 缓存命中 + model/opt 恢复
  + ref 加载 + 150 shards load + GAE（全部 <1 s）-> kickstart ref 预计算 3 s
  -> PPO 4 epochs / 148 梯度步 33 s -> result 上行 1.63 MB (7.4 s)
```

**探针被这份日志验证**：真实梯度 33 s / 148 步 = **223 ms/step**，探针测 206 ms（差 8%，
落在 epoch 级统计与心跳开销上）⇒ 以后不必每次上真机验证。

**三次错误假设（留档，勿重走）**：

| # | 假设 | 证伪证据 |
|---|---|---|
| 1 | 「GPU 利用率仅 2.9%，首要嫌疑是 `.item()` 同步串行化」 | A 段（**完全无同步**）就已 191 ms/step；同步税 B_old−A 仅 **+17 ms**；8 次合 1 次只省 **+2 ms**。按 A 段口径利用率 ≈ **7.2%**（旧 2.9% 用了含传输的 70 s 口径）⇒ **D2 结案**。 |
| 2 | 「~40 s 差额是 `load_episodes` + GAE + 统计」 | 日志 `shard IO + GAE done for 150 episodes (0s)`，整段不到 1 s。真差额在**传输**。 |
| 3 | 「探针 A 段在 CUDA 上是算力下限」 | `sync_mode="none"` 走 `Backend.mark()`，CUDA 上它是 **no-op** ⇒ 计时只含 CPU 入队（A=64 ms 而 B_new=192 ms）。已加真正的 `sync()`（`torch.cuda.synchronize()`）。 |

### 21.2 已落地四项

| 项 | 改动 | 实测 | 数值 |
|---|---|---|---|
| **ref 前向缓存** | `ppo/engine.py`：kickstart 的 ref 输出只依赖 (obs,scalars,mask)，逐 chunk 固定且 ref 冻结（BN-free）⇒ 跨 epoch 不变；原「每梯度步重算」改为「按 chunk 预计算一次 + 索引复用」 | CPU +18.6% / **GPU +22.7%** / TPU +31.5%（比例随设备变快而升）；真实日志确认省 **~9 s/轮（20%）** | ✅ 逐位不变 |
| **标量同步批量化** | `ppo/common.py::sync_scalars` + 三后端：每步 6-8 处 `.item()`/`float()` 合为 1 次 `stack().tolist()` | GPU **+2 ms（0.8%）⇒ 收益≈0**（见 21.1 #1） | ✅ 逐位不变 |
| **传输压缩** | `remote/protocol.py`：① 4 个编解码函数改 gzip(level 6)+base64（魔数自动判别，旧格式可解）；② **方案B v2 体**——result 上行改 `BRV2` 魔数 + JSON 头 + gzip **裸二进制段**，省掉 base64 的 33% | ① 上行 1,634,596 → 1,150,292 B（−29.6%）；② **再 → 863,023 B（合计 −47.2%）**，上行 7.4 s → **~3.9 s**；`opt_init` 下行 −31.3% | ✅ 无损；v2 往返逐字段一致 |
| **多卡** | `remote/worker.py` opt-in `--device cuda-dp`（`nn.DataParallel`，单卡自动退化）；产物落盘一律用未包装的 `raw_model` | **B_new 192 → 100 ms/step = 1.92×**（接近线性） | ⚠ 归约顺序变 ⇒ ulp 变，属新开实验臂 |

**三设备实测矩阵**（s/轮 = s/step x 148）：本机 CPU 3.607 s / Kaggle GPU T4x2 191 ms /
**Kaggle+Colab TPU v5e-8 39 ms**（A 段）⇒ **TPU 快 GPU 4.7×**，不是「配额接力」的量级。

**被否掉的两条路**（勿重走）：① 去掉重复权重（`result` 里 `weights_json` 与 `tar/model.pt`
是同一份）—— **hub 刻意免 torch**，无法把 model.pt 转回 `init_weights.json`，去重会破坏
`init_weights_fp` 对账；② 传输「方案B」（gzip 后传裸二进制）能再省到 47%，但要改 POST 体格式
+ hub 端 parser，增量仅 ~1.3 s。

### 21.3 TPU 设备层（扩容，非提速）

`ppo/common.py` 增 `is_xla / xla_device / optimizer_step / xla_mark_step / _to_cpu_state`；
三后端 `opt.step()` → `optimizer_step(opt, device)`；`_ppo_save` 落盘前把 XLA 张量物化到 CPU；
`remote/worker.py` 支持 `--device tpu|xla`；`ipynb/battle-rl.ipynb` cell3/4 改为
CUDA→TPU→CPU 探测。**踩坑四条**（全部来自真机）：

1. **API 改名**：`xm.xla_device()` → `torch_xla.device()`；`xm.mark_step()` → **`torch_xla.sync()`**
   （按 `sync` → `mark_step` 顺序探测，兼容旧版）。
2. **TPU 是独占 PCI 直通设备**（`/dev/vfio/*`）：notebook 探测 cell **禁止 import torch_xla**
   （2.6+ 导入即自动探测 PJRT device、会占住设备），改用 `importlib.util.find_spec`；
   探针必须在**内核进程内**跑（子进程抢不到设备 → `open(/dev/vfio/0): Device or resource busy`）。
   占用者是**别的进程**可 kill 释放；是**本内核**则 Python 无 release API，只能重启 runtime。
3. **`!python -u` 必须带 `-u`**：子进程 stdout 块缓冲 ⇒ 输出要等进程结束才吐，看起来像卡死。
4. **慢段要能跳**：TPU 上 `.item()` 每步 = 整图 materialize（分钟级），C 段每个新尾块 shape
   一次 XLA 编译（数十秒级）。探针新增 `--skip-sync-tax / --warmup / --repeat`，
   notebook 拆成三段各自可完成。

### 21.4 门禁两项修复

- **`platform_utils.rmtree_best_effort`**：沙箱删除保护抛 `SystemExit`（BaseException），
  `shutil.rmtree(..., ignore_errors=True)` **挡不住** ⇒ 直接打死调用线程（实证：`rl/dispatch.py`
  派发线程被打死后不再派发，竞态日志缺失导致 `test_it_early_race_v314` 假红）。
  全部清理路径改走该助手；`rl/workdir_sweep.py` 的计数改挂到返回值上。
- **`test_it_early_race_v314` 假红的真根因（结构性）**：单节点配置下
  `pick_race_target` 的 `nd_id not in inflight_nodes[task]` **恒假** ⇒ v3.10 race lane
  **永不触发**（失败日志里一条 `— race lane` 都没有），断言只能靠 v3.7 fan-out 的
  `next(iter(inflight))`（dict 插入序，取决于线程抢锁）偶然命中 seed111。
  改 `cfg["nodes"]` **追加第二节点**（慢任务挂 A、B 空闲 ⇒ race lane 按 `sorted(inflight)`
  确定性选中 `(0,111)`，全表最小键）；`took9` 由 12~15 s 降至 **2.0 s**，门禁连跑两次全绿。
  修完还**提高**了真实覆盖 —— 原先那条路径在该配置下根本没被执行过。

### 21.5 kickstart 系数阈值：退火到 ~0 后不再白付（2026-09-10 追加；用户确认课程不会回抬）

**发现（来自 21.1 的真实日志）**：`kickstart ref 已加载（kl=1.4551915228366852e-11）` ——
系数已是 **2^-36 ≈ 0**，但判据是 `> 0` ⇒ 照付：ref 权重进 payload（~0.36 MB）+ worker 每轮
预计算 **3 s** + engine 每轮算 ref 前向。而它的数学贡献 `1.46e-11 x 0.126 ≈ 1.8e-12`，相对
`policy=0.0046` 完全可忽略。

**根因**：系数按 `kickstart_kl * kickstart_decay ** N` **几何衰减**，永远到不了精确 0
（2^-36 正是 0.5^36）。

**修法（四处；源头单点归零 + 消费端兜底）**：

| 位置 | 改动 |
|---|---|
| `remote/protocol.py` | 新增 `NEGLIGIBLE_COEF = 1e-9` + `coef_active()`（顶层免 torch，hub 侧也可 import） |
| `run_rl.py::update_kwargs` | **唯一的衰减源**归零：低于阈值直接置 0.0。`rl/loop_steps.kickstart_coef` 只是它的薄包装 ⇒ 单点归零即贯通全链 |
| `rl/loop_steps.py` | 附 ref 字节的条件由 `kick_on` 改为 `kick_on and coef_active(kick_kl)` —— 原先"缰绳早已松开、ref 权重还在每轮空运" |
| `remote/worker.py` | 判据换 `coef_active` 并打日志，兜住"旧 hub 产出的、仍带微小系数的在途 manifest" |

**行为**（decay=0.5）：`it=30` → 1.86e-9 仍活跃；**`it=31` 起精确 0.0**；实测踩到的 `it=37`
现在精确为 0.0。回归测试 `tests/test_run_rl_m1.py::test_kickstart_coef_anneals_to_exact_zero`
（含 1e-9 上下边界）。

**兼容性**：新 hub + 旧 worker、旧 hub + 新 worker 两条组合都安全（精确 0.0 两侧都判"关"；
微系数由 worker 侧阈值兜住）。

### 21.6 payload 容器 zip/deflate → tar.xz（2026-09-10；「下行改 v2」不成立后的实测替代）

**先更正一个误判**：我原以为 payload 下行也背着 base64（"可再省 ~0.6 s"）—— **错**。
pull 模式下 `hub_server._get_payload()` 是 `self._bytes(p.read_bytes())`，**本来就是裸二进制**；
`payload_b64` 只存在于 **push 模式**（`push_client.py` → `worker_server.py`）。所以"改 v2"无事可做。

**顺路量出的真选项**（20 个真实 c5-margin shard，裸 22.3 MB；×7.5 折算 150 份）：

| 方案 | 体积 | 相对 deflate | 打包耗时(150 份) |
|---|---|---|---|
| ZIP_DEFLATED(6)（原状） | 241,382 | — | 1.8 s |
| ZIP_LZMA（只改一个参数） | 187,443 | −22.3% | **9.0 s**（慢 5×，净亏，否决） |
| 单流 lzma(3) | 119,280 | −50.6% | 1.1 s |
| **tar.xz(preset=3)（采纳）** | **123,788** | **−48.7%** | **1.9 s**（与现状持平） |

折算真实 payload **3.83 MB → ~1.96 MB，下载 2.3 s → ~1.2 s（−1.1 s/轮）**；
`tarfile`+`lzma` 都是 **stdlib，无新依赖**；打包 CPU 与现状持平（1.9 s vs 1.8 s）。
`ZIP_LZMA` 被否是因为每个条目独立字典（只省 22%）且慢 5×。

**实现（含新旧互通）**：

- `PAYLOAD_NAME = "payload.tar.xz"` + `PAYLOAD_LEGACY_NAMES = ("payload.zip",)` +
  `find_payload(job_dir)`（**优先新名、回退旧名**）；
- 产侧：`protocol.pack_payload` 与 `hub_client.pack_payload_zip` 改写 tar.xz
  （`PAYLOAD_XZ_PRESET: Literal[3]`——typeshed 的 `w:xz` 重载要求 `Literal[0..9]`）；
- 消费侧**双读**：`_extract_archive()` 用 `zipfile.is_zipfile` 判别，zip 与 tar.xz 都能解
  ⇒ 旧 hub 产的 `payload.zip` 对新 worker、新 hub 产的 `payload.tar.xz` 对旧 worker 都能工作；
- 触点：`protocol.py`（容器+find_payload）/ `hub_client.py`（打包+落盘名）/
  `hub_server.py`（存在性检查、落盘、服务三处）/ `worker.py`（落盘名）/ `rl/loop_steps.py`（push 读字节）。

**验证**：tar.xz 往返逐文件一致；**双读**（旧 zip 仍可解）；`find_payload` 两名并存时优先新名；
同素材体积 **−44.2%**（6 个真实 shard）。回归测试 3 项：容器魔数+体积、旧 zip 双读、find_payload 优先级。

### 21.7 待做

按实测收益排序：**`channels_last`**（同步被排除后升为第一优先；已验证 `cat(obs_cl, coords_nchw)` 会把 layout
静默退回 NCHW，不是传个参数就行，且与 TF32 耦合）、**尾块固定 shape**（仅 TPU 有收益）。
~~payload 下行改 v2~~ → 不成立（pull 模式本就是裸二进制），已由 §21.6 的 tar.xz 替代。
**DECISIONS 条目待补**（建议 `§2026-09-10-ppo-perf`）。

## §20 四项监控修复落地 + PPO job 竞速模型（§343）+ it24 孤儿租约事故复盘（2026-09-06）

§19 的三处发现（+ backup_prefix 第④项）经用户拍板「修全部问题」后全部落地，
回归测试 nn-training/tests/test_rl_remote_fixes.py（6 项）+ 全量 pytest 312 绿：

### 20.1 修复清单（DECISIONS §342）
1. **lr 折算**：`rl/loop_steps.py _course_iter` 把 `sch['lr']` 同步折进 `args.lr`
   （remote 模式经 publish_job → manifest → worker Adam 生效）。实战影响：it24–35
   本就该 1.5e-4（绝对 iter 查表），修复的实战价值是 **it36+ 精调段正确降到
   5e-5**（消除 3 倍超速 + 无 KL 惩罚叠加风险）。
2. **dup shard 退场**：`rl/dispatch.py` dup-settle 分支退役输家副本目录
   （`_dir` 目录名归一化兼容 local wave/远程 shard 两形态）；`remote/hub_client
   .iter_shard_dirs` 同名去重兜底（manifest mtime 最早者胜）。it24 实测：发布
   retire 2 份残留，`shards=150` 与 expectedGames 平，UserWarning 消失。
3. **EVAL_SEEDS 扩 100**：`rl/eval_local.py`。it25 起评估 100 局（CI ±13pp →
   ±10pp 内），旧 20 seed 前缀不变保持可比。
4. **backup_prefix/backup_dir 按课程生效**：归档落 `nn-training/weights/p4-onset/`。

### 20.2 §343 竞速模型（用户指令，详细权衡见 DECISIONS §343）
PPO job 分发从「租约独占」改为「竞速广播」：`/jobs/next` 对所有轮询者广播同一
open job，先回传结果者胜（store_result 首写锁定，迟到 409 丢弃），结果落盘未
验收的 job 从池中剔除。租约/心跳降级为兼容路径不再参与调度。动机 = it24 事故：

### 20.3 it24 孤儿租约事故复盘（08:45–09:20）
- 时间线：08:45:40 发布 it24 → 3s 后旧 Kaggle 会话领取 → 用户家庭网络断 2–3min
  → Kaggle 页面停更，用户重载页面 → 旧 worker 进程死亡，**租约独占下 job 被锁
  满 30min**（孤儿租约）；trainer `wait_job` 同为 30min 超时 → 09:15:45 超时 →
  iter_error → 幂等重发同一 job → 继续等待。断网期间 trainer 侧 502/URLError
  均被 §18 加固的重试吞掉（设计生效），零数据损失。
- 应急处置（已做）：hub_server 仅重启（租约纯内存态即清零，隧道/TrainingLoop
  不动，Kaggle 的 HUB_URL 不变）→ 自愈从 30min 压到即时。
- 根治（§343 已落地）：竞速模型下「断连重连」零等待——重连的 worker 立即重领
  同一 job 重算，不存在孤儿租约概念。
- **Kaggle 侧操作指引**：页面停更 ≠ worker 死了，先看 cell 状态；重载/重跑
  worker cell 安全（幂等键 + 首写锁定兜底）；worker 代码经 code.zip 每任务下发
  （GET /jobs/{id}/code），notebook 只需保持轮询 cell 运行，无需改代码。
- 监控教训：hub-server.out 的请求时序（payload 下载 / heartbeat / result POST）
  是判断「worker 活没活」的最直接证据——heartbeat 绝迹 + result 恒 404 = 租约
  孤儿，而不是 PPO 慢。

## §19 p4-onset 首日监控：lr 调度在 remote 模式未生效 + 重复 shard + 评估截断（2026-09-06，监控发现，待处置）

监控 06:21–08:11 远程推架构首跑（it1–10 完成，it5/it10 两次评估）。训练本身健康
（KL 0.0905→0.0077 单调收敛、熵 0.36 带稳定、value 6.2→2.6），it8 误熔断已由
F4/DECISIONS §339 修复（ENT 改相对崩塌语义 + ent_peak 基线继承，热启动课程自动
豁免，不会复发）。以下三处为监控新发现：

### 19.1 ppo_schedule 的 lr 三段表在 remote 模式下全程未生效（重要，待用户拍板）
- 证据链：`rl/loop_steps.py _course_iter` 只把 `sch['lr']` 写进 `self._opt.param_groups`
  （remote 模式 hub 侧无 optimizer，静默跳过）；`publish_job(..., lr=float(args.lr), ...)`
  传**静态** `args.lr`，全库无 `args.lr =` 赋值点；worker 侧 `remote/worker.py:531/538`
  以 `Adam(lr=manifest["lr"])` 建 optimizer。日志双印证：每轮打
  `[course] ppo_schedule@itN: lr=0.0003`，而 iteration 事件 lr 恒 0.00015
  （`rl/events.py` 写 `args.lr`）。
- 影响：it1–35 实际恒定 lr=1.5e-4——warmup 段（≤15，设计 3e-4）只有一半学习率；
  **it36+ 精调段（设计 5e-5）将 3 倍超速**，phase3 又 kl_coef=0 无 KL 惩罚，风险最大。
  kl_coef 经 `args._kl_coef` + manifest **生效**；`kl_cap` 仅 stream 路径消费
  （remote 强制 stream=0 ⇒ **本模式不接线**，打包≠生效；见
  DECISIONS §2026-09-15-goalnn-kl-cap-unwired）。
- 按 ~2.2 min/iter，it36 约 1 小时内到达。选项：(a) `_course_iter` 把 `sch['lr']`
  同步折进 `args.lr`（一行改动）+ 重启 resume 续跑，it12 起与课程表对齐，
  it1–11 半速段记为既成事实；(b) 维持恒定 1.5e-4 跑完并改课程注释。属实验语义
  变更（AGENTS §15.5），由用户决定。

### 19.2 同 seed shard 双份落盘（dist/<node> 与 wNN 各一份，内容不同）
- 每轮 0–7 个 seed 在 `it{N}/dist/<node>/` 与 `it{N}/wNN/` 各有一份完整 shard
  （it3=7、it4=3、it5=4、it6=4、it7=3、it8=2、it10=1、it11=1；it9 本地-only=0），
  两份 obs sha **不同**（同 seed 同 wver 本应逐字节同——指向跨节点 agent 版本差，
  参数 AGENTS §16.6）。机制高度疑似 tail fan-out 竞速的迟到副本：败者已被拒绝
  结算却仍写盘共存（`rl/queue.py` §16 修的是误回队，未管盘上残留）。
- 发布端 `remote/hub_client.pack_payload` 对重复 arcname 写 zip → zipfile
  UserWarning 刷屏；worker `unpack_payload` extractall 同名后者胜（排序保证 wNN
  在后）→ 实训只吃 150 个唯一 dir 的一份拷贝，**无双计**，但 payload 虚胖、
  账面 shards=151–157 与 expectedGames=150 不平。修复方向：结算后 retire 输家
  副本的盘上目录。

### 19.3 贪心评估被 EVAL_SEEDS 常量截断为 20 局（配置 100 局不生效）
- `rl/eval_local.py EVAL_SEEDS = (860001, 860002, *range(860003, 860021))` = 20 个
  种子；`rl/eval_dispatch.py:107` `EVAL_SEEDS[:n_seeds]` 把课程
  `eval_games_per_stage: 100` 静默截成 20。20 局胜率 95% CI ±13pp——it5/it10 的
  10%（2/20）与起点 p1-ep60 的 14%（100 局）统计上不可区分，阶段判断目前只能靠
  20 局粗评 + rollout 采样胜率双低通道。

### 19.4 其它观察（备忘）
- 备份正常但课程 `backup_prefix/backup_dir` 未被采用：`rl/loop_steps.py:458` 统一
  用模式前缀 `rl-weights` 落 `nn-training/weights/rl-weights.itN.*.json`
  （it1–11 全部已归档，权重安全）。
- it9 低谷（winRate 2.7%、128/150 超时、熵 0.246）归因：重启窗口内 mac 升级重启、
  a95/a97/a98 codeHash mismatch 被排除 → 150 局全本机采集（rollout 132s vs 常态
  40–66s）。it10 恢复 6.7%，it11 起全舰队回归。若 it11+ 超时占比仍 >50% 则需另行归因。
- it1 的 samples=68002 / mean_ret 8.0 为陈旧 shard 混入（resume=150，昨日多次失败
  尝试的同 init 残留）——趋势判断从 it2 起。
- 超时占比 ~45% 印证课程注释预判（max_ticks=2400 对 4 敌偏紧，后续重标定项）。
  走势数据：tmp/p4-onset/training_log.jsonl 与 eval_log.jsonl。

## §18 远程链路冒烟预演：worker --echo + TrainingLoop 作废轮（2026-09-05，DECISIONS §340）

用户拍板：不建虚拟课程——TrainingLoop 跑**真课程**，伪 Kaggle（echo worker）回传 init
权重并带 smoke 标记，TrainingLoop 走完正常落位后识别标记**作废本轮**（it 不前进）。
冒烟保真度 = 与真训练逐字节同路径；作废标记使任何消费方都不会把回显权重当真 PPO
吃进去（自保护）。

落地（`bun run check` / pytest test_remote_ppo 32 绿）：
- `remote/worker.py --echo`：下载/校验/commit/course_fp 全走，跳过 torch 与 PPO，
  result = init 权重回显 + manifest 指纹回显 + `smoke: true`（validate_result 不拒额外键）。
- `rl/loop_steps._remote_ppo`：`verify_and_land` + `mark_job_completed` 后查
  `result["smoke"]` → 抛 `SmokeVoidRoundError`；落位权重与发布时逐字节相同（init 回显），
  无需回滚。
- `rl/loop_core.run`：捕获 SmokeVoidRoundError——`--smoke` 干净退出，真训练 `it -= 1`
  原地重试（不计失败连击、不 sleep）。**作废轮不写 iteration 事件** →
  `last_completed_iter` 续跑锚点零污染（实测账本 0 iteration 事件）。
- `rl/cli.py --smoke`；`tools/hub-start.ts --smoke-only` 新增 Kaggle 交互预演阶段
  （真 job 发布 → echo worker 穿隧道 claim→payload→code→result → 落位 → 作废退出）。
- `remote/hub_client.wait_job` 加固：轮询容忍瞬时网络错误与 5xx（快速隧道抖动曾
  一击废掉整轮迭代 → 连击重试堆积陈旧 pending job）——24/7 隧道运营可靠性修复。
- 闪窗收尾：`archive.py`（git push）/`run_rl.py`（rev-parse）补接
  `platform_utils.POPEN_NO_WINDOW`（console-less 父进程拉 git 会新建可见控制台；
  其余文件的接线早已完成）。
- 闪窗第二三轮排查（看门狗实证法：EnumWindows 扫可见 ConsoleWindowClass 窗口记
  PID）：漏网点共两处——`remote/hub_client.git_head`（每次发布）与
  `dist_common.dirty_hash_files`（每轮派发的 dirty-tree 检查）——均已补接。dirty 检查
  本身保留：它只管**远端** pull 节点的 restart 循环护栏（self 节点经 is_self_node
  豁免、纯重启零 git 操作，2026-08-30 修订），与 code.zip 直接打包无关。
- `tools/hub-start.ts drainStaleJobs`：TrainingLoop 新启动前下架陈旧 pending job
  （删 payload.zip → hub claimable 跳过；账本保留 job_pending 真实历史），避免真
  worker 空烧 GPU 租约。

实测（20:56 冒烟）：发布 8s（断点续跑）→ echo worker 全趟 54s → 落位 → 作废退出；
账本 0 iteration 事件。教训：Bun Windows `detached` = DETACHED_PROCESS（子进程完全
无控制台，hwnd=0 实测），孙进程（git 等）会新建可见控制台闪窗——python 侧子进程
一律挂 POPEN_NO_WINDOW。

## §17 goal-space 策略网络重建开工（2026-08-29，M9 时代）

按 `plan/Goal-Space-Policy-Rebuild.md`（2865 行开发手册，六轮评审收敛）启动 goal-space 轨：
动作空间从 8 路意图换成 26×26 目标热图，NN 输出从 God-AI 候选链掩码改为参数。
**全部进展/决策/教训记 `docs/goal-nn.progress.md`**（本档只留交叉引用）。

已落地（`bun run check` 全绿）：
- T7 网络改造：`nn-training/goal_net.py`（goal 热图 conv + engage + value，inject 9 维语义重定义）+
  `src/nn/infer.ts` goalForward + golden 一致测试（热图 1e-3 按 §T7.3 预案，标量头 1e-4）
- T8-min 契约（E1/E3/E5/E4 纯谓词）· reach-mask 池化 Dijkstra（T3 子件）·
  T8.5 goal-executor + `--policy goal` 全链接线
- T7.2 goal PPO 基建：`ppo_goal.py`（fine 676 / coarse 169 块 logsumexp 双动作空间）+
  `export-goal-rollout.ts` 采集器 + rl/ goal_rollout 分支 + `run_rl_intent.py --goal`
- T6 反事实标注管线：并行多窗口分叉 rollout（cloneWorld 分支克隆）+ §11.8 H 扫描判读
  （实证：argmax 落敌后格 9%@H60 → 44%@H240，长窗口恢复追尾行为）+ `train_goal_bc.py`
  软目标稀疏 CE（λ/τ 训练超参，shard 存 (s_i,k_i)）
- God-AI 基线重钉（super-item 恢复 + pursuit-tail 后）：**78.81%**（1655/2100，hard），
  旧 pinned 75.86% 作废

新基线与偏差决策清单见 goal-nn.progress.md §0/§1/§2。

## §16 tail fan-out 反向竞速修复 + 三节点就绪验证（2026-08-27）

### 16.1 背景
rollout 尾部分发（`nn-training/rl/queue.py` v3.7 tail fan-out）在末段把尾部任务重复派发
竞速取先返回。此前只修了「fan-out 副本迟到被 duplicate 拒收后误回队」的一面（主副本
已 settled → inflight key 被删 → 副本落入正常失败分支 → 无限循环）。单节点 20 局测试
暴露**反向竞速**：fan-out 副本抢先结算、**主副本**（`fanout_copy=False`）迟到被判
duplicate 后仍落入正常回队分支，把已结算任务重新派发（`retried=2`）。

### 16.2 修复
`queue.py` 失败分支在 `if fanout_copy:` 之后新增对等拦截：`task in seen` 即已结算，
静默丢弃（补 inflight 减计数）。重跑同 20 局：`retried=2 → 0`，新增日志
`main s4/seed3 failed (duplicate) — settled by fanout copy, dropped` 命中验证。

### 16.3 三节点验证（mac 192.168.0.88 / a97 192.168.0.97 / a98 192.168.0.98）
- 三节点同 commit（codeHash `1fd6b9bf…`, agentVersion `e0a01e1`），evalSupport 均 true。
- rollout fan-out：30 局三节点，30/30 settled、missing=0、retried=0，负载 12/10/8，
  3 个 fanout 副本正确丢弃。
- m1-eval `--dist-nodes`：30 局（stages 1-5 × seeds 1-6）三节点分布式评估成功，
  BCV2 解码 + v7 维度汇总 + HTML 输出正常。

### 16.4 m1-eval v3.8 混合分派（用户指正 2026-08-27）
v3.7 `runDist` 两个问题被用户当场指出：
1. **本机闲置**：dist 模式下本机只当协调者，16 核全空。
2. **并发未打满**：`i % nodes.length` 轮转指派，节点 concurrency 只决定总线程数，
   不控制每节点在飞请求数——本应 mac4/a97 7/a98 7 = 18 slots，轮转下每节点同额分单。

改为 `runHybrid`：每节点按其 concurrency 生成独立 fetch-loop（在飞请求数 = concurrency，
精确占用节点容量），本地生成 `--dist-local` 个 worker-loop，全部共用同一共享游标——
快节点/本地自动多吃尾部。冒烟 6 局（3 nodes 8 slots + local 2）全过，进度上报打通。
正式 2100 局用 18 slots + local 16，10% 仅 1m12s（v7 纯本地 16 核 51min → 预计 ~12min）。

### 16.5 scheduled sampling BC（M5 补件）训练验证（2026-08-27）
训练：`--ss-eps 0.3 --quota 15000 --epochs 8`（B′ 同语料/shards），8 轮 loss 28.54→1.33、
trainAcc 0.20→0.46。训练期 M5 probe gate FAIL（base margin 0.089 < 0.1）。

**Self-feed gap（eval_intent_m5，评价目标）**：

| 指标 | B′ 基线 | SS ε=0.3 | 变化 |
|---|---|---|---|
| teacher acc | 0.6009 | 0.5741 | -2.7pp |
| self-feed acc | 0.4642 | **0.5531** | **+8.9pp** |
| **gap** | **0.1367** | **0.0211** | **-11.6pp** |
| RETURN_DEFENSE recall | 0.3135 | 0.4768 | +36pp |
| HOLD_LANE recall | 0.0073 | 0.3408 | +33pp |
| CLEAR recall | 0.5018 | 0.8368 | +34pp |
| HUNT recall | 0.9277 | 0.7089 | **-22pp** |
| safetyMisclass | 0.0772 | 0.1139 | 恶化 |

**游戏级（hard 35×60，v3.8 hybrid 18slots+16local，~31min）**：

| 指标 | B′ | SS | Δ |
|---|---|---|---|
| WIN | **71.7%** | **60.1%** (PASS ≥60%) | **-11.6pp** |
| suite | 0.5364 | 0.5036 | -0.033 |

**结论**：SS ε=0.3 把 self-feed gap 压缩到 2.1pp（目标达成），但代价是主力意图 HUNT
recall 0.93→0.71、安全级误判 7.7%→11.4%，游戏胜率 71.7%→60.1% 显著退化（仅勉强
PASS gate）。**ε=0.3 过猛**——自喂注入把重防御类（RETURN_DEFENSE/CLEAR）拉高、
把进攻主力 HUNT 拉崩。下一步：试温和 ε=0.1（预期 gap ~5-8pp，保 HUNT 高位、胜率
回 ~68-70%），或仅对非 HUNT 类做 SS 注入。

### 16.6 v3.9 动态节点发现（用户需求 2026-08-27）
跑批中途上线的 agent 也能贡献算力：rollout（`rl/queue.py`）与 m1-eval
（`tools/sim/m1-eval.ts` runHybrid）都加了周期 agent 发现。

- **rollout**：rescan 线程周期（policy `agentRescanSec`，默认 120s）ping 配置里未在跑的
  节点，合格（codeHash/bun 版本/在线）→ 权重下发（幂等 kept）+ 孵化新 worker 线程
  （共享 pending 队列）；`spawned_ids` 防重复孵化，不重启整轮。
- **m1-eval**：初始激活改为 tryActivate（ping→幂等上传→spawn），离线节点不中断跑批、
  留给 rescan 周期再探；`--dist-rescan` / policy `agentRescanSec` 控制周期
  （默认 120s），运行中被加入配置文件的节点也能被发现；补了无消费者守护
  （dist-local 0 且全离线不永久挂起，2.5s 内标记失败收尾）。

**验证**（本机临时 agent，rescan=8s，late 节点延迟 10-12s 上线）：
- rollout：`rescan: node late online mid-run — weights purged, spawning 2 workers`，
  12/12 settled，late 贡献 7 局，fan-out/tail-dispatch 共存无冲突。
- m1-eval：`rescan: node late online mid-run (+2 slots)`，16 局真实仿真全部完成；
  纯远端 local=0 正常；全离线场景触发 `no consumer available` 守护不挂死。
- 回归：`bun run check` 1536 pass / 0 fail；三节点混合分派 WIN 66.7% 正常。

---

## §15 M3 BC warm-start 双臂（2026-08-26，plan/AI-No-Items-Warmstart.md §6）

### 15.1 训练（纯 BC，value 预置降级说明）
- A 臂（对照）：wins 底料 24 shards（46,359 帧），8 epochs，batch 512，~43 min。
- B 臂（实验）：A + 人像 97 shards（112K 帧），同超参/同 seed（结果见下）。
- **value MC 预置降级**：returns 终局锚定（Σ≡REWARD_SCALE×gatedScore≈O(9)）使 value MSE@coef1.0
  爆方差（train_loss ~10⁴，move acc 卡在多数线 0.23），coef 0.05 仍使 ep-1 loss 6867 且 move 不学。
  结论：该尺度下 value 回归会压制策略头——**A/B 走纯 BC（value_coef=0）**；value 预置留待 M4 前
  用「逐关规范化 returns」再启（工程项，非路线否决）。M3 gate 只测策略，不受影响。

### 15.2 训练收敛（8 epochs / batch 512）
| 臂 | 语料 | move acc | fire acc | val_loss |
|---|---|---|---|---|
| A（wins 24sh） | 46,359 | **0.365**（峰 0.381） | 0.919 | 1.72 |
| B（A+人像 97sh） | 111,872 | **0.441** | 0.792 | 1.81 |

B 臂 move 显著高于 A（0.441 vs 0.365）——人像加权改善了移动；fire 稍低（0.792 vs 0.919，
wins 底料 fire 高度饱和）。

### 15.3 Gate（m1-eval --policy nn，hard 35×10 贪心，350 局真实仿真）
| 臂 | WIN | suite v7 | 说明 |
|---|---|---|---|
| A | **0.0%** | 0.0773 | 全败但非瞬死（有击杀/节奏，score>0），永不清关 |
| B | **0.0%** | 0.0757 | 同上；better move 未转化为清关 |
| **DAgger-blend** | **0.0%** | 0.0777 | wins93sh+dagger50sh，move 0.476（三线最高），仍 0% |

- 对照历史：DAgger 后 0%（L950）——**BC warm-start 三种语料形态均未突破历史 0% 平台**；
  it36≈10% 是旧 schema RL 谱系，跨纪元仅定性参照。
- **按 plan §6 Gate 规则 → 「≈0%：不走 M4，回环整改」**：已完成一轮完整回环
  （纯 BC A/B → DAgger 混合），三条线均 0%——见 §15.4 结论与决策点。
- **工程教训（已落地 commit 3c40e55）**：weights 导出 sanitize NaN→null（TS JSON.parse
  拒绝 Python 非标准 NaN 字面量——value_loss 纯 BC 下 NaN 曾让 m1-eval 350 局全 error 伪读 0 分）。

### 15.4 回环结论与决策点（2026-08-26 晨）
- 三个语料策略（wins-only / wins+人像 / wins+DAgger 混合）在该尺度（46K–185K 帧、6–8ep）
  均把 BC 蒸馏收敛到 **0% WIN**；move acc 持续上行（0.365→0.441→0.476）不转化为胜率——
  印证历史「L950：0% 是策略深度差距，非 bug」。
- **剩余未尝试杠杆**（超出本会话合理机时，需拍板后另行执行）：
  1. **全量底料长训**：corpus-a full 2.77M 帧 × ≥12ep（≈24h CPU）——排除子集尺度不足；
  2. **RL 直接启动**（旧 schema it36 路径达 ~10%，唯一 >0 实证）：schema v2 下以随机 value
     头 + 现行课程重跑 run_rl（PPO 已有 v2 支持，rogue 略）；
  3. **路线 F：高层语义监督**（克隆战术 id + 廉价底层控制器）单独立项（plan §7 兜底）。
- 决策建议：先 1（排除尺度），若仍 0% 则走 2（RL 是唯一 >0 实证路径）；3 作为独立路线立项。

---

## §14 语料纪元 OBS_SCHEMA_MAJOR 1→2（2026-08-26 凌晨，plan/AI-No-Items-Warmstart.md M2）

### 14.1 打包改动（一次 MAJOR，全部落地并提交 f4afb43）
- **① item 头删除**：动作空间 10→7（MASK_DIM 7）；actions→(N,2) [move,fire]、masks→(N,7)。
  TS（infer/policy-input/npy/observations）与 Python（schema/model/student_model/ppo/
  train_bc/dataset/npyio/validate_export/eval_bridge/dist_common/rl-model/rl·stream）锁步。
- **② SCALAR_DIM 24→19**（删 guard/frenzy/rewind stock + frenzyActive/frenzyShotsLeft）；
  **SCALAR_X_INDICES [20,23]→[15,18]**（mirrorX 索引锁步 + 反例测试 test_mirror_scalar_lockstep）。
- **③ wins-only 口径**（export-godai-labels 默认 --wins 1）；near-miss 守家帧超采样默认 3×
  （M1 探针证据：守家桶分歧率 74.6% 最高）；人像导出过滤道具动作帧（guard/frenzy 激活帧剔除）。
- **⑥ returns**：`tools/sim/rl-reward.ts` 共享 RL reward（lossPartialQ/Φ/γ=0.995 折现），
  God-AI 与人像导出均落盘 returns.npy → M3 value 头 MC 预置数据源；train_bc 增 --value-coef
  对 PPOStudent 的 value 头做 MC 回归（纯 BC 兼容不改）。

### 14.2 验证
- determinism：export-godai-labels --verify-determinism（2 关 × 6 npy 字节一致）✅
- mirror 锁步反例测试（[15,18] 翻转、[20,23] 死位不翻）✅；python 快速层 ALL PASS ✅；tsc ✅
- 旧 npy 归档：tmp/rl-traj/it36-39 + e2e-v36-out + exp-bench → tmp/npy-v1-archive/ ✅

### 14.3 重导 + 覆盖率（完成）
- **God-AI wins 底料**：35 关 × 60 seeds 重导（--wins 1 --near-miss-times 3）：
  games=2100，kept=1526（lossSkipped=574），样本 **2,774,052**（含 3× 濒危超采样），
  rawFrames=1,398,906 / nearMiss=687,573（**frac 0.4915，≥2000 帧**，3× 后有效守家帧占比 74.4%）。
- **人像**：nn-demo 104 局 → 97 OK（7 DESYNC 剔除），**65,513** 样本，道具动作帧剔除 17 帧。
- validate_export 双语料 PASS；A/B 语料（corpus-a 330K / corpus-b 396K 帧）构建完成。
- 承压关触发线：nearMiss frac 0.4915 < 0.5（全量占比参考 M1 探针 base 桶 75.6%）——
  已用 near-miss 3× 超采样内置回补（有效占比 74.4%），无需另行补录败局（wins-only 口径）。

### 14.4 BC smoke + M3 双臂（结果见下）
- A 臂（对照）：wins 底料 185 shards BC warm-start（--value-coef 1.0，PPOStudent 带 value MC 预置）。
- B 臂（实验）：A + 人像 97 shards（守家帧加权采样内置）。

---

## §13 无道具纪元开启：M0 + M1（2026-08-26 凌晨，plan/AI-No-Items-Warmstart.md）

> 用户拍板「全部 AI 不使用主动道具 + RL 预热」路线；执行 M0（摘除道具）→ M1（分歧探针）。
> §14 记录 M2 语料纪元（OBS_SCHEMA_MAJOR 1→2）。

### 13.1 M0 God-AI 摘除主动道具（DONE，已 commit）
`superItemMode/GuardThreat 默认归零`。配对复测（A=ON, B=OFF, hard 60 seeds×35）：
胜率 76→75%、Δscore −0.0093±0.0025、t=−3.77、p=0.0002（Lattice 0.519→0.485）——
与预检 B 臂完全一致。DECISIONS §167 修订为 RETIRED by default；全文 → god-ai-tuning §M0。

### 13.2 M1 分歧探针（DONE）
新工具 `tools/diag/divergence-probe.ts`（预注册定义 + 后果代理指标 + 三桶归因）。
数据源：DAgger 学生权重（tmp/student-weights-dagger，schema v1）+ 教师观察。
结果（25 局 hard：s0-4 × sd0-4）：

| 桶 | 帧数 | 分歧率 | 静默率 | 有后果 |
|---|---|---|---|---|
| base（基地高压） | 7646 | **74.6%** | 72.1% | 191 |
| combat（交战） | 1964 | 63.7% | 63.7% | 0 |
| cruise（巡航） | 509 | 38.3% | 38.3% | 0 |
| 总体 | 10119 | 70.6% | 68.7% | 191 |

- 分歧率 70.6%（学生 0% 胜率的必然画像）；**静默分歧占绝大多数**（~97%）——大部分分歧
  是 120-tick 窗口内无可见后果的 tie-breaking/朝向差，不进归因。
- 分桶分布：基地高压桶分歧率最高（74.6% > 交战 63.7% > 巡航 38.3%），特征表完整。
- **归因结论 = ①/③ 边界**（高压桶分歧高且特征完整 → 标签或监督；预注册表原无此格，属
  **表外裁量**——已事后补格入 plan §4 执行态修订，结论不变）：
  采取 **wins-only + 守家帧回补（near-miss 超采样）并预留 DAgger 交互采集轮**。
- 决策影响：M2 的 near-miss 超采样默认 3×（守家帧 = 环受损 OR 敌压基地 ≤12 格，
  即 M3 预注册守家帧判定的简化版）；M3 若 WIN <50% 则先补 DAgger 轮。

---

## §12.5 补丁：eval 本地参与（PPO 收尾后本机算力入列）（2026-08-25 傍晚）

> 用户指出的容量缺口：eval 只派 HTTP 节点，PPO/采集收尾后训练机 idle 无贡献；
> 极端情形（无可用节点）整轮评估直接 skip。

- **实现**（rl/eval_dispatch.py + run_rl.py）：① 派发时刻把 rl_path 复制为
  traj_dir/_eval_frozen_weights.json 冻结快照（主循环 PPO 写回会原地覆盖
  rl_path，本地局读错版本=对账灾难）；② `run_local_eval_game` 本机直跑
  export-eval-game.ts，补 wver/mode 戳后走同一 validate_eval_result 与台账聚合，
  summary 的 nodes 字典自然出现 "local" 键；③ `local_gate`（threading.Event）
  由调用方在「梯度步已尽」时 set——流式=末波排水完、串行=PPO 完成，join 前置
  set；worker 放行前每 5s 醒来看 deadline，不让位时零开销；④ 无节点时 gate 存在
  即 local-only 继续跑（旧行为=整轮 skip）；槽位 policy.evalLocalSlots 默认 4、0=禁用。
- **测试**：test_eval_local_gate 双子用例（gate 开→6 局全落 local+summary 聚合；
  gate 关→runner 零调用+窗口到期 dropped 全记），快速层 ALL PASS ×2 连跑。
  教训复训：台账按 wver 去重，跨进程残留 fixture 会把用例推进 skip 早退——
  work 目录必须 rmtree 重建；断言基线要显式捕获而非硬编码。
- **本地参与保底三件套**（同日追加，用户质询「local 完全没参与 dist 和 eval」驱动，
  语义按用户修订定稿）：
  ① `--local-slots N` 显式 CLI（0=自动 max(2,workers//4)），**调度语义 =
  「dist 阶段最先被分派 → PPO 启动即让位 → PPO 结束转 eval 尾段」**：
  · dist：洗牌后前 N 个任务划入本机专用队列（头部分配，节点线程不可触及，
  确定性保底）；首个 PPO 波次启动置 local_suspend → 本机停止领新任务、保留段
  一次性并回主队列交远端消化（防饿死；远端集体失联超 remoteDeadSec 时让位
  自动失效）。附带修复两处真缺陷：完成判定/missing 原用切割后的 len(tasks)
  会提前 all_settled（日志溢出 3/2 可见）；_fast_enough 的 EWMA 持留曾把 local
  彻底饿死——现豁免（上限+让位自治理）。
  · eval：gate 放行前节点不取最后 evalLocalSlots 局（hold_for_local 纯函数，
  距窗口截止 300s 强制释放），派发行打 `[local tail-reserved ×N]` 标记。
- 生效时机：运行中的进程已加载旧模块，**下次 relaunch 生效**。

---

## §12 R6 破局三件套落地 + 评审修订与重启记录（2026-08-25 下午）

> §11 审计定位 loss-band 局部最优后，实施三方向改动（奖励重做 / 课程化 / PPO
> 收紧），顺带修复两个潜伏奖励 bug。本节含独立评审结论、执行修订（A/B/C）与
> R6 首次启动参数——旧谱系数据已归档，见 §12.4。

### 12.1 改动摘要（均已验证）
- **奖励重做**（export-rl-rollout.ts）：`RL_LOSS_WEIGHTS` 显式重写为守家优先
  （baseIntegrity 0.25 + baseSafety 0.25 + progress 0.30 + tempo 0.08 +
  accuracy 0.06 + openingTempo/loot 各 0.03，和=1.00）；`BASE_LOSS_MULT
  0.25→0.1`（失守更贵）。七键在 lossPartialQ 全部有产出。
- **两个潜伏 bug 修复**（评审独立复核属实）：① tempo 曾除以 `w.tempo(=0.026)`
  → 恒饱和无梯度；② accuracy 因旧 DEFAULT_LOSS_WEIGHTS 无键恒 null。现统一用
  `DEFAULT_STAGE_REFS`（kpmRef=8 / accuracyRef=0.3）。
- **课程化**（rl/course.py + run_rl.py）：`--curriculum-stages/start/every/grow`，
  纯函数 `(order_len,it)` 确定性扩展，种子流 `[rotateSeed,0xC0E,it]` 键控，
  断点续跑安全；排序取自逐关干净评估胜率（与数据重算一致，差异在平局噪声内）。
- **PPO**：GAMMA 0.99→0.995（K=10 决策间隔下信用时域 16.7s→33s，守家是长时域
  行为）、VF_COEF 0.5→1.0。串行/流式双路径经模块常量自动继承。

### 12.2 评审核实与技术性备忘
- 门禁复跑全绿：python 快速层 ALL PASS / tsc 干净 / oxlint 0 error；bun test 的
  12 个失败均为 God-AI 既有行为测试（tests/ 无文件 import 被改模块，src/ 未动，
  归因成立）。Σr≡SCALE×gatedScore 恒等式由终局对账结构保证。
- **kpmRef=8 会再饱和**：God-AI-Evaluation-Redesign.md 已载明默认 kpmRef=8 对
  强手恒饱和（标定后逐关 15–29）。当前水平（~1–4 KPM）梯度可用；策略进步到
  8 KPM 后应换逐关 refs（后续项）。
- 训练 Φ 与评估维度口径不同（评估套件用逐关标定 refs）——终局对账保证正确性，
  但巡检对比 dims 时须知非同源。

### 12.3 执行修订（评审要求，已全部落实）
- **A 轮规模**：启动命令补 `--seeds-per-stage 3`。缺省 10 会令课程满员时达
  350 局/轮且 KL 预算回归必触；35×3=105 局贴合预算。⚠️ it73/74 实测每局成本
  上行至 ~0.0022–0.0025（此前 0.0018），cap=0.20 实际预算仅 ~85 局——课程成熟期
  若 dropped_games 回升，决策规则：实测满轮成本 ×1.1 抬 cap，或 epochs 4→3，
  不盲抬。
- **B 节点同步**：codeHash 只覆盖 export-rl-rollout.ts + src/nn/**，故仅需提交
  后各远端节点 `git pull` + 重启 agent（部署契约见 plan/distributed-rollout.md；
  自动化暂缓属人工步骤）。每迭代重读配置重新 ping，可懒同步中途归队。未同步
  期间以 self+local 容量运行（it73/74 即此状态，~950s 收齐 105 局仍可行）。
- **C 历史归档**：training_log.jsonl（74 轮）/eval_log.jsonl/dist-agent-meta.jsonl
  与旧 RL 权重快照移入 `nn-training/archive/pre-R6-20260825/`；tmp/rl-traj 清空
  重建（保留默认目录以维持自动巡检 HTML）。fresh restart 决策依据：新奖励下
  value 头尺度失准是真代价；「policy 沿用 + value 重置」折中案因科学对照性与
  旧谱系已完整归档而未采纳。

### 12.4 R6 启动参数与观察项
- 启动：`start-training.{sh,ps1} -Script run_rl.py --iters 0 --stream 1
  --seeds-per-stage 3 --curriculum-start 4 --curriculum-every 8 --curriculum-grow 4
  --curriculum-stages "13,1,16,21,8,4,15,31,29,0,33,22,14,17,10,2,27,34,30,3,
  11,12,18,19,24,5,7,23,28,6,9,20,25,26,32"`（顺序=逐关胜率降序）。
- 观察：① 干净评估胜率是否突破 ~13%（历史单轮上限；注意相邻轮翻转噪声底
  13.2%，看 5 轮滑动均值）；② base_destroyed 占比是否从 ~80% 下行；
  ③ **龟缩失败模式**（防守占 50% 的新风险）：timeout 占比上升 + progress 中位
  数下滑即预警；④ dropped_games 是否保持零（验证 A 修正）；⑤ entropy 是否随
  dense 防守信号锐化。
- 判定规则：同墙钟对照点（~it20）干净评估 5 轮均值 ≥ 旧谱系同期（7–9%）为
  有效；持续 ≤ 则考虑回滚权重表单因子（GAMMA/VF_COEF 回滚需改常量，无 CLI 入口）。

---

## §11 收敛性审计：it1-68 平台期定量画像（2026-08-25 午后）

> 用户质询「it1 至今似乎没有收敛和提升迹象」。全量日志定量审计结论：**观察基本
> 成立，但需精确化——初日确有一波真提升（BC≈0% → ~10%），其后 60+ 轮胜率平台；
> 策略仍在动，但动的全是奖励指向的非胜负维度。**

### 11.1 数字
- **rollout 胜率分段**（探索采样）：A it1-12=10.2% / B 13-29=11.2% / C 30-47=10.2% /
  D 48-59=9.3% / E 60+=8.6%；斜率 -0.33pp/10轮 ±0.15（≈2σ 轻微下行，跨机制变更
  证据强度中等）。entropy 斜率 -0.0013/iter±0.0003（1.31→1.22 后回升）；每步 KL
  0.021→0.039 上行 = 更新趋激进而收益不涨，追噪声特征。
- **干净评估**（贪心 70 固定局）：7.9%/7.6%/8.8%/9.0% 分段持平，斜率 ≈0±0.03pp。
  **噪声底实测：相邻轮同键胜负翻转率 13.2%**——单轮 ±5pp 读数全是噪声；配对
  it24 vs it68 净胜 +1 局、score 差 +0.024±0.025 不显著。
- **真实进步在维度层**：it24-32→it60-66 干净评估 progress +0.044 / tempo +0.047 /
  openingTempo +0.037 / mobility +0.053；accuracy -0.024；baseIntegrity 0.245 持平；
  score 0.229→0.245 微涨。

### 11.2 定性：loss-band 局部最优
reward = v7 loss-band 势差密集结算（RL_LOSS_WEIGHTS 里 lives=0）+ 终局
Σr≡SCALE×gatedScore、base_destroyed ×0.25。策略精确优化了这份梯度——「输得更有
质量」（多杀/快节奏/跑位好），而 loss-band 分数可在永不通关前提下持续改善 →
梯度停在「会死但死得漂亮」的局部最优。防守侧无 dense 信用（baseSafety 只在终局
间接体现），baseIntegrity 卡 0.25 不动即症状。教师 God-AI 参照 72%，学生 9%。

### 11.3 若要胜率突破的候选杠杆（均属用户级决策，未实施）
① 防守项入 dense Φ（baseIntegrity/baseSafety 进势函数）或恢复 lives 权重试验；
② 课程加权：对已有胜局的关加权采样（可学梯度所在）；③ self-imitation：干净评估
胜局回灌 BC 正样本；④ 工程侧配套：target_kl 早停（控 KL/step 上行）、best-clean-eval
checkpoint 归档（当前权重只进不退无法回滚峰值）。沿现奖励继续爬的预期：score 维度
缓慢改善，胜率难破平台。

---

## §10 streamKlCap 预算重标定 0.12→0.20（2026-08-25 午）

> 起因：用户质询「最新几次 105 局 rollout 全触发 KL 熔断」。逐波取证（detach
> stdout 日志）后定性：**不是策略失稳，是预算算术脱节**——每训练一局的 KL 成本
> 实测 ~0.0018（it61: 0.132÷69 局），cap=0.12 只买得起 ~67 局；105 局全量需
> cum_kl≈0.19~0.21 → 结构性必触。

### 10.1 取证要点
- 触发的是 **streamKlCap 轮内熔断**（Σ各波均值 >0.12 停派发+dropped 记账），
  不是 F4（每步均值 kl 0.036–0.047 远低于 0.15×3）。
- it60/61/62/64/65/66 全在**第 4 波后、训满 ~69 局时停车**（cum 0.124–0.153，
  dropped 13–37）；唯一未触顶的 it63 恰是续跑轮（仅 2 新局）——天然对照组。
- 波均 kl 与串行时代全轮均值同量级（串行 0.02–0.03 vs 流式首波 0.02–0.03、
  末波 0.046），逐波缓慢递增属波间漂移累积，无异常跳变。
- §8 已记录熔断自流式上线起 33/50 轮静默触发；R2 的「sps 4→3 贴预算」估算
  偏乐观（预算实际只够 ~70 局，105 仍超 ~50%）。
- 影响面：干净评估胜率维持 0.04–0.13 平台无崩塌——代价是每轮 ~1/3 语料白采 +
  训练子集偏向先结算的短局 + it60 高漂移后 it61 rollout winRate 一度 0.0095
  （同轮干净评估正常，探索态扰动非永久损伤）。

### 10.2 决策与观察项
- **决策（用户拍板 A1）**：`rl-config.json` policy 增加 `"streamKlCap": 0.20`
  （≈105 局成本 0.19+增长余量）。热生效：主循环每迭代重读配置，无需重启进程。
  软降档点随动至 0.14（~第 78 局收缩波次），仍保留熔断兜底。
- 观察：① dropped_games 应归零或个位数；② 干净评估胜率是否因单轮移动幅度变大
  而波动加剧（对照 it61 教训）；③ 若某轮 cum_kl 实测 >0.21 仍触顶，说明单位成本
  上行，届时考虑 B3（ppo_update 波内 target_kl 早停）而非继续抬 cap。
- 未采纳：A2 缩轮（sps→2）、B1/B2 降 epochs/lr（拖慢学习）、删除熔断（它是唯一
  轮内漂移约束）。中期候选：B3 target_kl 早停 + C1 预测性停派发（用剩余预算提前
  停派，可把 in-flight 白采砍到个位数）。

---

## §9 Python 侧工程化重组 + 常驻单元测试（2026-08-25）

### 9.1 结构（nn-training/rl/ 新包）
run_rl.py 从 ~1700 行瘦身为 ~500 行入口（CLI + 迭代主循环 + 权重归档/巡检），
编排逻辑抽取至 `rl/`：course（课程纯函数）/ queue（中央队列+本地回退）/
stream（流式波次）/ eval_dispatch（干净评估）/ resume（断点对账）/
reports（聚合）/ breaker（F4 纯逻辑）/ log。入口必须留在顶层——启动器只接受
裸 .py 文件名；`rl/queue.py` 自算 REPO_ROOT（parents[2]）。run_rl 保留全部
re-export，旧引用路径不破。

### 9.2 可测试性抽取与潜在缺陷修复
- **breaker_update(kl_streak, ent_streak, *, kl, entropy, win_rate)** 纯函数化
  （阈值+连击判定），主循环改调它；顺带修复潜在缺陷：流式 checkpoint-complete 轮
  agg=None 时旧代码直接读 agg["kl"] → TypeError → 被兜底 except 吞掉后原地重试。
  现在 agg None 短路（不计连击不告警——本轮本无梯度步）。
- **wave_params(cum_kl, kl_cap, wave_games, wave_cap)** 纯函数化（软降档阈值）。

### 9.3 测试（test_run_rl.py 常驻，禁临时脚本）
快速层默认跑（~30 断言）：parse_range / build_pairs 确定性+sps 重叠=6 回归 /
combine_reports / completed_pairs+resumed_manifests(only/exclude) / jsonl 锚点 /
breaker_update 全规则 / wave_params 边界 / compute_gae 手算用例 / chunk_episodes
ragged / backup_weights 有界清理。集成层 `--itest`：假 HTTP 节点驱动真队列与
流式轮（结算、halt、queue-drained 次序契约、评估恰一次）。教训入册：
① float32 ret 与 float64 重算差 ~1.2e-8 会越过 allclose 默认 atol——手算用例
必须带显式容差；② 大块 SearchReplace 在该文件上会模糊匹配失真（缩进漂移/
整文件截断各一次），结构性移动优先用「新文件 Write + 入口重写」，行内小修才用
SearchReplace 且改后必 py_compile。

---

## §8 流水线衔接优化：KL 遥测补盲 + 熔断止损 + dist/eval 解串（2026-08-25）

> 起因：用户质询「跑这么多轮 KL 都没超 0.1」——jsonl 的 `kl` 在流式模式下只是
> **末 wave 单值**，而轮内累计 cum_kl 实际在 50 轮里触发熔断 33 次（66%，通宵段
> 95%），max 0.129–0.155。遥测盲区让最常触发的路径无人察觉；且熔断后已结算语料
> 整批丢弃、远端仍在采无人消费的局（it53 实测 53/140 局白采）。

### 8.1 落地（run_rl.py / ppo.py / rl-hourly-inspect.ts）
- **KL 记录**：jsonl 新增 `kl_cum`（流式=Σ各 wave，串行=单次更新均值）、
  `halted`、`dropped_games`、`waves`；HTML 巡检「KL 累计」列优先显示 kl_cum，
  ⛔N 角标标注熔断丢局数（旧日志行无 kl_cum 时回退显示单值 kl）。
- **R1 熔断止损**：`halt_event` 贯穿 run_rollout_queue——触顶后 worker 停止领取
  新任务（在途局自然收尾），报告带 `halt_aborted`；cum_kl 过 cap 70% 后软降档
  （wave 12→4 / drain cap 24→8），把过冲从一个整波压到个位数局。
- **dist↔eval 解串（用户指令，同日修订一次）**：初版按「权重分发完毕即并行派发」，
  用户复核后纠正为**中央派发队列清空**时触发——全部采集任务已交到节点/本地线程、
  结果仍在途，评估局顺势填收尾空槽；既不与采集全程抢节点（初版问题），也不必等
  全部结果落定（回到串行时代）。熔断 / collector 收官仅兜底再触发（eval_fired
  护栏去重）。评估线程句柄经 report["_eval_thread"] 回传主循环，jsonl 写回前
  join——修复流式模式评估线程此前根本不被 join 的隐患。**仅流式改此时机**；
  串行关键路径是采集→PPO，评估提前会抢采集节点反而拉长整轮。
- **rollout_sec 锚点修正**：collector 线程 finally 里记 box["t_end"]，主线程以它为
  collect_done——此前最后一个 in-flight wave 的 PPO 时长被误计入"纯采集窗口"
  （即 rollout_sec − pure_collect_sec 缺口 450–650s 的真正来源）。
- **新增耗时字段**：`dist_phase_sec`（ping+POST 阶段）、`tail_drain_sec`、
  `load_sec`（_load_wave 的 npy IO+GAE，此前完全不可见）、`eval_join_sec`
  （轮间气泡直接观测）。join 前置于 jsonl 写回以便同轮入账；崩溃时断点续跑按
  「语料秒回 + PPO checkpoint 完整」路径无损重放。
- **ppo.load_shard astype(copy=False)**：obs 每轮 ~550MB 的无谓 dtype 拷贝消除。
- **UI/遥测微调（用户指令）**：「KL 累计」的口径说明从表头 th-note 移到迭代健康表
  下方；`run_local` 注入 `report["elapsedSec"]`（整局墙钟，与远端 manifest 同口径）
  → dist-agent-meta.jsonl 的 local 行开始带耗时，「采样机健康」表的局均耗时列对
  local 不再恒为 '—'（数据自下次 relaunch 起积累）；串行模式 local 也因此进入
  tail-dispatch 快慢分档速度表。
- **断点续跑「计划口径」修复（it60 实测驱动）**：sps 4→3 后重启同一迭代，目录残留
  旧计划同权重 shard（140 局 vs 新计划 105），旧逻辑把两者混算——resume 日志显示
  「140/105」歧义、报告聚合混入 134 局计划外语料。修复：`completed_pairs` 结果与
  `plan_set` 求交后再算缺口；`resumed_manifests` 增加 `only=` 计划内过滤；日志改为
  「planned pairs on disk (+ ignoring N off-plan shards)」无歧义格式；dist 遥测新增
  `offPlanShards`；早退路径补齐 missing/expectedGames/dist 与全流程同 schema。
  重叠恰为 6 局的数学：sps 变更只改变每关种子索引窗（旧 [4i,4i+4) / 新 [3i,3i+3)，
  底流前缀一致），仅置换前三关相交，3+2+1=6，实测逐位吻合。

### 8.2 设计判断
- **F4 熔断仍读 `kl`（每梯度步均值）不读 kl_cum**：阈值 0.15 是按每更新均值标定
  的（健康带 0.045–0.054）；喂 Σwave 值（常态 0.12–0.15）会三轮即假熔断。轮内
  漂移的治理者是 streamKlCap 本身，kl_cum 只负责可见性。
- **远端全挂时没有本地 eval 兜底**（确认过的现状）：节点配置缺失→纯本地路径
  根本不走 eval 分支；节点配置了但全 ping/POST 失败→回退本地后 dispatch_eval
  因无 evalSupport 节点而 skip。如需本地贪心评估需另起 runner（未做）。
- **R2 配方建议（未改代码）**：PPO 已是瓶颈（ppo_cpu≈927s vs 纯采集≈540s），
  下次启动可试 `--seeds-per-stage 3`（140→105 局）让采集量贴 KL 预算走。

### 8.3 生效与观察项
- 当前运行中的 run_rl 仍是旧代码（run_rl 无锁文件）；改动在下次 relaunch 生效。
- 观察项：① `dropped_games` 应显著下降（软降档+停派发）；② `load_sec` 占比决定
  是否值得做双缓冲预装载；③ eval 与采集并行后 pure_collect_sec 会略涨——只要
  迭代墙钟下降即为净赚；④ `eval_join_sec` 若仍常态 >0，考虑再压 eval 语料。


## §7 干净评估嵌入流水线（2026-08-24 晚，DECISIONS §283）

> 背景：采集成本趋零后，training_log 的 winRate 仍是"带探索噪声的移动靶"——
> rollout 采样熵≈1.27 nats + 每轮 rotate 换 seed，轮间 ±5pp 摆动无法归因。
> 用户拍板：PPO 空窗期把固定语料贪心局分发到全部 agents（权重恰为上一轮 PPO
> 产物，与本轮 rollout winRate 同策略直接对照），评估墙钟藏在 PPO 计算里零成本。

### 7.1 落地（四处）
- `tools/sim/export-eval-game.ts`（**新文件**）：argmax 贪心单局 runner，纯 v7 打分
  （无 F3 门控），只写 `_eval_report.json`。干跑双执行字节级一致（DETERMINISM OK，
  s0/860001 → base_destroyed@3547 ticks、8 杀、score 0.144）。
- `sampler-agent.ts`：`mode=eval` 任务路由 + ping/status `evalSupport` 能力声明 +
  manifest 回显 mode；权重切换删除改尽力而为 + retention 清扫（修在飞评估局
  Windows EBUSY 竞态——此前切换只在 140/140 全结算后发生故未暴露）。
- `dist_common.py`：`fetch_task(mode=)` + `validate_eval_result()`。
- `run_rl.py`：rollout 返回后 spawn 守护线程；语料 `EVAL_SEEDS=(860001,860002)` ×35 关；
  收账 `tmp/rl-traj/eval_log.jsonl`（逐局行 + eval_summary 行，按 wver16 去重断点不重评）。

### 7.2 关键设计判断
- **不动 export-rl-rollout.ts**：codeHash 是 rollout 准入硬门，动它 = 五节点全部
  剔除直到同步完。独立文件 + ping 能力声明 → 逐节点灰度，旧 agent 零影响。
- **节点门四条件**：enabled ∧ ping ∧ evalSupport ∧ bun major.minor 一致。旧 agent
  会静默忽略 mode 参数把评估跑成采样局——能力声明是防误吃的唯一闸门。
- **iterId 后缀 `ev`**：agent 结果缓存键空间天然隔离。
- **读数对照**：eval_summary 行带 rolloutWinRate 同列——clean vs sampled 的系统性
  偏移本身是健康信号（贪心通常更高；若反而更低说明熵还在乱开火）。

### 7.3 生效条件与观察项
- 生效需各节点同步代码并重启 agent（ping 出现 evalSupport 即点亮）；trainer 重启时
  新参数默认已开（--eval-games-per-stage 2）。
- 观察项：① clean winRate 曲线斜率 vs 墙钟——这才是收敛速度的真裁判；
  ② dropped 计数若常态非零，调大 --eval-window-sec；③ 后续可挂 HTML 趋势报告。

### 7.4 流式模式时序缺陷（实跑发现，21:05 修复）
- **现象**：it23 rollout 收官后始终无 [eval] 行。根因：§7.1 的钩子位置假设
  "rollout 返回后有长 PPO 空窗"，这只在串行模式成立；流式的空闲窗已被
  run_rollout_stream 内部 drain 吃掉，返回后距下轮权重分发秒级——后台派发的
  70 局会全体撞上权重切换作废。
- **修正**：串行=后台隐藏（不变）；流式=阻塞执行（预算 --eval-window-sec，
  默认 1500→900s）。诚实账：流式下评估是显式流水线阶段（每轮约 +5min），
  "零成本隐藏"只在串行模式成立。
- 本机 agent 已重启点亮（ping evalSupport=true, agentVersion=55645d4），
  权重缓存已手动幂等补发（避免 self 本轮熔断）。

### 7.5 最终形态（21:57 实跑验证通过）
- 阻塞式也被推翻（用户纠偏：原设计就是"PPO 跑的同时跑 eval"）。终案：
  **eval 派发提前到 collector 收官时刻**（run_rollout_stream 新增
  on_collect_done 回调），与 drain 完全重叠；主循环在写回 jsonl 后才 join
  （预算封顶），保证下轮新权重分发前评估必已收官。
- 同时按用户指令：流式模式禁用收尾智能分发（tail_dispatch=False，无脑转发；
  串行模式保留），并加 drain 横幅日志（积压 < wave 阈值时打印）。
- **实跑验证（it25）**：round done 21:57:23 → eval dispatch 21:57:36 → DONE
  22:04:04（4/70 = 5.7%，dropped=0），期间 drain wave 持续推进至 22:06+——
  评估墙钟完全藏进 drain，迭代间零额外等待。it24（阻塞旧版）：clean 2.9%
  vs sampled 11.4%；it25 clean 5.7%。clean 曲线正式开画。
- 观测注记：drain 横幅在积压降至 wave 阈值下方才打印，属正常节奏。

---

## §6 训练可观测性落地 + 权重逐轮归档 + mb1024/workers10/self-agent2 重启（2026-08-24 早）

> 背景：it2 的 PPO 阶段跑了 **110 分钟全程零输出**（旧代码只有首尾两行日志），
> 任务管理器只见 python ~50% CPU，无法判断进度与健康。用户要求阶段性日志。

### 6.1 可观测性（纯打印，零 RNG/数值影响）
- **ppo.py**：① 分片加载进度（每 128 局一行）；② 更新心跳（≥60s 一行：epoch/chunk/
  step/elapsed/**eta** + 最近 32 chunk 滚动 kl/entropy/policy/value/gnorm）；③ 每 epoch
  汇总（含 ckpt 落盘确认）；`gnorm` 入 stats（clip_grad_norm_ 返回值顺手捕获）。
- **run_rl.py**：本地 rollout 每 10 局结算一行（as_completed 重排，结果按原索引回填）；
  队列模式逐局 settle 行（node/stage/seed/elapsed）；missing 行附结算进度。
- **生产验证（it3）**：rollout 140 局逐局可见；PPO 心跳实时读数 kl≈0.014–0.018、
  entropy 1.33 稳定、gnorm 1.3–1.6、ETA ~2000s——观测黑洞消灭。

### 6.2 权重逐轮归档（用户指令）
- 每次 PPO 写回后 `shutil.copyfile` 至 `nn-training/weights/rl-weights.it<N>.<YYYYMMDD-
  HHMMSS>.json`；保留最近 20 份（有界增长）。命名**刻意避开** weights_io
  `weights.<ts>_ep<N>_val<V>` BC 自动发现正则（§5.3 手工备份同理由）；
  glob 清理只认 `rl-weights.it*.json`，手工备份永不被删。tmp 合成数据单测通过。

### 6.3 重启（用户指令：mb 512→1024、workers 2→10、本地 agent --workers 2）
- it2 PPO 于 07:38 自然完成（kl=0.026 健康）后停机重启，rotateSeed 继承、自 it3 续跑。
- **效果**：rollout 17.5min→**6.7min**（workers 2→10 直连 + macos 8 并发）；PPO 语料
  回归正常轮规模（190 局/192 chunks×4 epochs≈768 步），ETA ~33min（对比 it2 膨胀语料
  110min——频繁重启合并历史 shard 的隐性代价再次确认）。

### 6.4 事故与教训
1. **rl-config.json concurrency 必须 ≤ agent 实际 --workers**：首轮 self 按 10 并发打
   2-worker agent → 3 连 `503 busy` → nodeFailStreak=3 即熔断出局。503 是协调器侧
   计数的失败，重试还烧 MAX_TASK_ATTEMPTS。已改 concurrency=2（下轮 ping 生效）。
2. **配置文件被神秘回写**：07:35 编辑的 self.concurrency 10→2 在重启前被改回 10
   （无进程应写此文件；疑运维编辑器旧缓冲保存）。二次修改后需在后续轮次复核，
   若复发需查写入方。
3. 心跳 ETA 用"本 run 已完成步均速"外推，断点续跑时偏乐观——可接受。

### 6.6 报告口径修复 + 收尾智能分发（tail dispatch）（2026-08-24 午后）

- **巡检报告漏计远程局（真 bug）**：`scanIterDir` 只匹配本机 `w*` 目录，
  `dist/<node>/rl_*_seed*/manifest.json` 全部漏扫——整份 HTML（累计/逐轮表/
  各关表现/首胜）只统计了本机直连份额。远程 manifest 字段与本机导出摘要同构
  （stages/seeds/scoreList/outcomes/totalTicks/dimLists），直接按 RlReport 消费；
  dims 走 dimLists 回退（loot 列远程局暂缺省）。重置账本全量重扫：
  **5 轮 ×140=700 局入表，crossCheck 0 不一致**（修复前每轮都在悄悄报"扫描≠日志"）。
- **报告 UX 四项**：移除「本段新胜局明细」表；「最近迭代健康指标」时间列明确标注为
  **完成时刻（PPO 写回时间）**——jsonl 的 time 字段在 PPO 完成后写入；entropy/KL/
  局均 ticks 列头标注告警阈值（与 healthVerdict 规则同源）；「采样机健康」新增
  「上轮局数」列（meta 按 it×node 聚合，全局最新迭代为基准，未参与=0）。
- **收尾智能分发（tail dispatch）**：此前中央 deque 自由竞争，慢节点可能在
  135/140 时抢走最后一局长局，PPO 空等。现按局均耗时 EWMA 分快/慢两档：
  速度表用跨轮 dist-agent-meta.jsonl 最近 20 局播种、本轮 α=0.3 在线更新；
  队列剩余 ≤ 快速集群单波容量（快节点槽位和，含本机 workers）时慢节点让路
  （`policy.tailFastFactor=1.8` 分档）；120s 无结算进度则饥饿兜底重新准入
  （`policy.tailGraceSec`）。无样本节点乐观按快速处理。分配仍属实时负载语义，
  洗牌确定性不变。**下次重启训练生效。**
- **流水线提案分析（rollout/PPO 重叠）**（→ **被 §6.7 推翻**：否决理由中的
  "语料跨版本"不成立，见 §6.7 的修正）：①前提纠正：当前 rollout 完全结束后才进
  PPO，PPO 独占整机；~50% CPU 是小模型+串行分发的 torch 天花板（§3.10），不是被
  采样抢占，减 worker 无益。②~~严格依赖链……~~（误判：轮内权重本就冻结）。
- **实际可用的提速杠杆仍是缩短 PPO**：epochs 4→3（线性 -25%，KL≈0.02 远低于
  0.08 有富余）或继续观察 mb=1024 后的 KL 再定。

### 6.7 流式迭代落地（--stream 1）：推翻 6.6 的流水线否决（2026-08-24 下午）

- **用户驳倒了两条反对理由**：①"语料横跨两个策略版本"不成立——权重分发只发生在
  迭代边界，整轮 rollout 期间 agent 持有的都是 W(N)，任意时刻到达的语料都出自
  同一策略版本，on-policy 比率数学不受到达顺序影响；GAE 用采样时存储的 value，
  与装载时机无关。②"省 7 分钟不值"是误判——35min/轮 × 几百轮的持续复利，
  改造成本是一次性的。
- **实现**（run_rl.py `--stream 1`，默认关闭；串行路径零改动保字节基线）：
  collector 线程跑 run_rollout_queue（新增 `on_result` 回调 + `local_slots_max`
  参数），本机槽压到 max(2, workers//4) 给 torch 让核；主线程每当积压 ≥12 局
  （policy.streamWaveGames）装载这批 shard（load_shard+compute_gae+wave 内 adv
  归一化）、chunkify 后按 --epochs 遍 ppo_update——每局总更新遍数与串行一致。
  轮内累计 KL 超 policy.streamKlCap（默认 0.12）即转"只采不训"。断点续跑轮
  零结算时回退全量磁盘更新。
- **与串行的语义差异（有意为之，需观察）**：adv 归一化从全轮变为每 wave；
  各 wave 的更新分布在 θ 漂移轨迹的不同点（PPO clip 容忍范围内）；流式期间
  不落 PPO epoch checkpoint（崩溃重启该轮重训，语料靠 completed_pairs 秒回）。
- **验证状态**：py_compile 通过；待下次重启以 --stream 1 实跑一轮观察
  wave KL / entropy / winRate 曲线后再定是否默认开启。
- **流式首次实跑三连 bug（15:31-15:43，全部修复）**：①包装函数返回 report 本身，
  主流程却取 `meta["report"]` → KeyError；②断点续跑"剩余 0 epoch"时
  `stats[0]` IndexError（ppo_update 空聚合无守卫，已加零聚合返回）；③本地局
  shard 目录多一层子目录（w9/rl_s30_seed*/obs.npy），loader 拿工作目录当 shard
  找 obs.npy 扑空 → 本地局全部被 skip（加 `_shard_dir` 探测含 obs.npy 的层）。
  三连杀导致 it13 重试 5/5 耗尽进程退出一次。
- **it13 假零指标事件**：skip-update 路径曾以全零聚合写 jsonl（entropy=0/kl=0），
  报告显示 0.0000 且会误触健康判定。修正为 agg=None → jsonl 写 null → 报告 '—'，
  历史行已补正（真实值只在旧进程日志：kl≈0.0258 / ent≈1.283）。
- **流式轮耗时语义（重要）**：ppo_sec = 各 wave 纯更新时间之和（与 rollout 重叠）；
  rollout_sec = collector 墙钟（含被藏进去的 PPO）。it14 因中途修 bug 重启被劈成
  两半：95 局 resume 秒回不参与训练，仅 45 新结算局训练（188 grad steps vs 串行
  ~572），故 ppo=609.8s 远小于串行 1670s——是语料少了不是变快了；干净轮预期
  ppo_sec ~20m 但完全藏在 rollout 内，墙钟 ~12-15min vs 串行 35min。
  **观察项**：轮内后期 wave 的 kl 天然偏大（数据由数个 wave 之前的 θ 采集，
  it14 末波 kl=0.0514 vs 串行 0.02），cum_kl 封顶 0.12 自动停训，继续观察。
- **it15 巨浪事故与墙钟地板（16:40 修复）**：`_drain` 无上限，结算高峰后积压
  90 局被一口吞下（单波 376 步算 20 分钟）；且 rollout_sec 在收尾训练后才计时，
  把训练尾巴记进采集列（报 30.9m，真实采集仅 ~9min）。修复：wave_cap=max(24, 2×
  wave_games) 封顶 + collector 结束后持续分批清空 + rollout_sec 改为纯采集窗口。
  **关键认知：ppo_cpu/wall≈96%——流式的墙钟地板是 PPO 纯算力**（epochs4/mb1024
  ≈29min），藏只能藏采集的 9 分钟。要破地板需砍 PPO 计算量：mb 2048（步数减半，
  预期 ppo_cpu≈15min）为下一候选实验。it15 本体完全健康：140/140 missing=0、
  ent 1.268、cum_kl 0.068<0.12、winRate 12.1%（较 it14 回升）、新增第 5 节点
  android-98 正常入列。
- **饿死保护（边缘场景：远端集体掉线）**：①启动时全离线 → 既有回退路径
    （run_rollout 纯本机满额）天然安全；②轮中集体掉线（ping 过了之后死）→
  本机线程按满额孵化、并发闸门初始压低，若 `remoteDeadSecs`（默认 150s）
  无任何远端结算则闸门自动放开到满 workers 并打日志——语料供应恢复，
  流式更新继续吃本地波；③30min queueWindow 兜底：到点强制收轮，
  missing 局回队语义不变，PPO 用已有语料照常更新。非流式模式行为零变化
  （cap 恒等于满额，闸门恒开）。

---

### 6.5 rotate 抽签失配事故：it5 整轮重跑（2026-08-24 晚）

- **症状**：09:35 重启后 `resume: 140/140 pairs already done — run 140 remaining`
  ——140 个已完成对却全部未被剔除，整轮 rollout 重跑，it5 语料 280 局。
- **根因**：`build_pairs` 从单一连续流按调用顺序抽签。旧进程 it5 = 流的第 3 次抽取；
  新进程重启后流复位，it5 复用第 1 次抽取（= 旧 it3 的签）→ 与已落盘 shard 完全
  不相交 → `p not in done` 全员命中。铁证：新旧日志的抽签范围逐字节相同
  （7050345..1073087402），而那是旧 **it3** 的签。§281 的"rotateSeed 继承"只保证了
  种子本身连续，没保证流的消费位置与迭代号对齐。
- **修复**：`build_pairs(args, it, rotate_seed)` 改为 **(rotateSeed, it) 的纯函数**：
  permutation 按 epoch 键控（同 epoch 窗口仍平铺公共排列）、seeds 按 it 键控
  （`default_rng([rotate_seed, tag, key])`）。同一 it 任意时刻重放逐字节一致，
  断点续跑剔除真正生效。无状态冒烟验证：交错调用下 it5 直接求值 == 重放值。
- **处置**：丢弃错配的 it5 语料（280 局，含两套不相交签）→ 带修复重启 →
  it5 干净跑满 140 局（winRate 10%，missing=0/retried=0）→ PPO 146 chunks×4 正常。
- **教训**：①"继承种子"≠"可复现课程"——随机消费必须键控到迭代号而非调用序；
  ②对同一文件的并行编辑会相互覆盖（本次 run_rl.py 两处编辑丢过一次，串行重做）；
  ③新节点接入首日隧道偶发 10054 属预期，回队机制兜住（本轮 missing=0）。

---

## §5 队列模式静默跳轮事故复盘 + 修复 + 重启（2026-08-24 凌晨）

> 决策（DECISIONS §282）。事故窗口 00:19–00:44：it1 PPO 完成后，it2/it3 整轮 rollout
> 完成却从未进 PPO，直接跳下轮（用户发现时已在跑 it4）。

### 5.1 根因（证据链定罪，非猜测）
- `resumed_manifests` 不排除本轮已采局，且把本轮 shard manifest（**单局 schema**，
  无 `games`/`totalSamples` 键）原样并入 `combine_reports` → `r["games"]` KeyError
  秒崩 → 主循环 except 吞掉 + `it+=1`。
- 时间线铁证（dist-agent-meta.jsonl）：it3 末局 00:34:48 → it4 首局 00:35:35，
  **间隔 47 秒**（含 sleep 30s）——PPO 根本来不及启动；it2/it3/it4 均无 ppo_ckpt。
- it1 未崩纯属侥幸：438 个前序局全 done → 早返回空报告路径（绕过合并），代价是
  it1 事件 winRate/samples/outcomes 全空（指标盲区另一症状）。
- detach 启动无 stdout 落盘 → 失败栈零痕迹，只能靠数据考古。

### 5.2 修复（run_rl.py ×3 + 启动器 ×1 + 巡检工具 ×1）
1. `resumed_manifests(..., exclude=seen)`：排除本轮已采；双 schema 归一（本地单局式
   转换 / 远端聚合式透传）。真实事故数据验证：修复前 KeyError，修复后 games=140、
   outcomes 全归类（111 bd + 20 le + **7 stage_clear** + 2 timeout）。
2. 全 done 早返回改磁盘聚合出完整报告（消灭空报告盲区）。
3. except 分支：`iter_error` jsonl 事件 + `it -= 1` 原地重试同轮（§281 断点保证
   不重跑已完局）；consec_fail≥5 才退出。
4. start-training.ps1 detach 分支 stdout/stderr → `tmp/run_rl-<stamp>.{out,err}.log`
   （编辑后 BOM 被剥，已手工补回——§2.2 同坑三踩，教训再次确认）。
5. rl-hourly-inspect.ts：null score_mean 渲染崩溃修复（it1 空聚合事件触发；
   这也是巡检 HTML 从未产出的原因）。

### 5.3 运维动作（按用户指令逐项）
- 权重备份：`nn-training/weights/rl-weights.20260824-001921_post-it1ppo.json`
  （= it1 PPO 后权重，SHA256 校验一致；命名避开 BC `weights.*` 自动发现模式）。
- 巡检账本清零重计（用户指令）：删旧 state 后重跑，it1 新基线 = 98 局 / 6 胜 /
  812 击杀；HTML 报告 `tmp/rl-traj/inspection-report.html` 首次成功产出。
- 语料抢救：it2~it5 同权重有效语料全并入 it2（140+140+140+18=**438 局**，
  目录整体 rename 为 `it2/merged_itN` 零拷贝），源目录随之消失。
- 05:29 经启动器 detach 重启（参数同前 + keep-iters 5）：resume 自 post-it1 权重 ✓、
  rotateSeed 继承 ✓、`438/140 pairs already done — run 140 remaining` ✓（macos 节点
  8 并发补采中；本机 self agent 未起被排除，吞吐减半不影响正确性）。

### 5.4 教训
1. **异构数据管道必须 schema 归一后再进聚合器**——两条采样路径（本地/远端）的
   manifest 结构差异在单机时代不存在，分布式化第一天就炸。
2. **吞异常的循环必须有旁路观测**（iter_error 落盘），否则生产事故只剩行为考古。
3. 失败迭代前跳 = 静默丢语料；断点续跑机制（§281）使原地重试成为零成本安全选择。
4. .ps1 编辑三连坑：BOM 必查（第三次踩）。

---

## §4 RL 训练断点续跑机制（2026-08-23）

> 决策（DECISIONS §281）：三层断点续跑，崩溃/停启后自动继续而非重跑。

### 4.1 实现
- **it 续跑**：`--start-it`（缺省自动 = jsonl 最后完成迭代 + 1）。
- **rollout 任务续跑**：`completed_pairs(traj_dir, wver)` 剔除已完整落盘且 wver 匹配的局；
  `resumed_manifests` 并入聚合保报告完整。
- **PPO epoch 续跑**：`ppo_update(..., ckpt_path=it{n}/ppo_ckpt)` 每 epoch 存 model/opt/epochs_done/numpy RNG。

### 4.2 验证
- **PPO 续跑等价**（`tmp/dist-resume-check.py`）：从 checkpoint 续跑 vs 一次跑完，**最终权重逐参数相等**
  ——证明 minibatch 乱序（numpy MT19937）+ optimizer 状态精确重建。首跑失败根因是测试脚本 mA/mB
  随机初始化不同（nn init 走 torch RNG 不受 numpy seed 控），非机制缺陷；同初始后全等。
- **it 断点端到端**：启动1 完成 it1；启动2 日志 `resume: continuing from iteration 2` 直跑 it2
  （不重跑 it1），`it2/ppo_ckpt` 三件套落盘。
- rollout 断点 pure 函数：无 stage/seed 不计入 done，有则计入（dist-resume-check 覆盖）。

### 4.3 教训
- `ppo_update` 的 minibatch 乱序是**唯一**的全局 numpy RNG 消耗点（build_pairs 用独立
  `default_rng`、agents 用 `random.Random`），这是精确续跑的前提，勿在别处引入全局
  `np.random` 消耗破坏确定性。
- 崩溃在「权重写回后、jsonl 写前」的极小窗口会用新权重重跑整轮——on-policy 正确，接受。

---

## §3 RL 阶段设计（承接 P1.5 蒸馏，2026-08-21）

> 决策：DAgger 对 RL 的价值已基本兑现（验证推理链路 + warm-start + 观测充分性证明），
> 继续压 DAgger 边际收益低。下一步主线 = **收尾 round2（锁定最佳 warm-start）→ 转 RL**。
> 用户已拍板走「推荐」路线。

### 3.1 观测审计结论（关键，免扩展）
重读 `src/nn/obs-encoder.ts`（14 通道 + 24 标量）。**obs 已自带基地防御 + 胜利进度信号，RL 无需扩展通道**：
- 空间：ch5=基地(eagle+护墙)、ch7–10=敌种+智能层、ch11=子弹(含方向)、ch13=waveHeat(未来 600 tick 预测刷怪)。
- 标量：s[1]=minBaseDeadline/600（**敌人对基地受损时限**，显式基地威胁计时）、s[6]=护墙完整度、
  s[22–23]=最近基地相对位/向、s[13]=剩余敌人比例（进度代理）、s[0]=最近击杀 slack。
→ **残余 0% 属目标优化缺口（BC 不优化胜率），非观测缺口**。RL 优化真实 reward 即可直接吃现有 obs，不被卡。

### 3.2 架构复用（最小改动）
- 主干：`StudentModel` 的 stem + 8×ConvMixer blocks + fc 作共享特征提取器，**从 DAgger 检查点 warm-start**
  （写死 `tmp/student-weights-dagger2/weights.json`）。
- 策略头：保留 3 个 factored categorical 头（move5/fire2/item3），与 BC 一致 → 动作=(move,fire,item)，
  决策 tick K=10 持有门控不变。
- 价值头：新增 `value_head`（fc 特征→标量），随机初始化、RL 训。PPO 用 shared-trunk + 分离 policy/value。
- 动作空间：离散 factored，PPO categorical 直接适用。首轮不扩 item 头（rewind/emp/decoy/mine 暂不纳入 RL 动作）。

### 3.3 奖励设计（权重待拍板）
r = w_win·win + w_kill·kills + w_base·baseIntegrityΔ + w_surv·survivalTicks − w_dead·baseDestroyed
- win：通关 +1（稀疏终局）；baseIntegrityΔ：每 tick 基地护墙/鹰完整度变化（塑形，引向防御）；
  kills：击杀数（复用 world 击杀计数）；baseDestroyed：终局大惩罚（引向保基地）；
  survivalTicks：弱塑形，防过早送。需小范围网格/经验初值，避免 reward hacking（只保基地不进攻）。

### 3.4 Rollout 回路（TS 导出 + Python 训练，沿用范式）
- TS 端 `tools/sim/export-rl-rollout.ts`：复用 `ObsEncoder`+`StudentModel`(+value head)+headless sim 驱动
  （骨架取 `export-dagger-labels.ts`），按决策 tick 跑策略→采 (obs,scalars,action,logprob,value,reward,done)
  写 trajectory shards（npy，兼容 `load_dataset` 递归扫描）。
- Python 端 `nn-training/ppo.py`：消费 shards，clipped PPO（shared trunk warm-start + value head），
  复用 `train_bc.py` 的 venv/torch 入口（`start-training.ps1`）。
- 在线性：RL 多轮 rollout→训练→再 rollout（on-policy）。首轮用离线固定轨迹 mini-batch PPO 起步验证链路，
  再上异步 on-policy。

### 3.5 任务拆分
观测审计(#12, ✅) / 奖励设计(#11) / round2 收尾→锁定 warm-start(#13) / TS rollout(#15) / PPO(#14) / 首轮量化(#16)。
#13 先跑完锁定最佳 warm-start，再 #11→#15→#14→#16 串行。

### 3.6 接手验证：RL 链路端到端冒烟全通（2026-08-21 接手）

前手交付 `ppo.py` / `export-rl-rollout.ts` / `run_rl.sh` + value head（`student_model.py`
PPOStudent、`infer.ts` 可选 value_head 槽）。接手后逐契约复核（World 字段 /
computeMasks 1=valid / writeNpy 签名 / rules 模块 / buildModelFromJson 全参数透传 /
PPOStudent 继承 arch()→kind:'student' 路由正确）并跑通完整闭环：

```
init   : ppo.py --init-from tmp/student-weights-dagger → value_head 随机初始化，
         missing=[value_head.*] 符合预期；68683 params；JSON 写入 [1,128]+[1] ✅
rollout: export-rl-rollout.ts s0/seed0/600ticks → 59 样本（60 决策点 − 超时丢弃
         末 pending，设计内）；12 个 npy + manifest ✅
update : ppo.py --resume --data --epochs 2 → policy/value/entropy/kl 正常输出 ✅
roundtrip: 用更新后权重再 rollout → TS 加载含训练后 value head 的权重 ✅
门     : tsc --noEmit 绿；bun run check 全绿（1429 pass / 0 fail）
```

**接手修复的 2 个 PPO bug**（冒烟暴露，前手未跑到 update 步）：
1. `ppo_update()` 签名/实现错位：函数体按 dict 取 `batches["obs"]`，调用方传的是
   episode **list** → TypeError。改为按 list 迭代。
2. 维度 bug：episode 张量已是 `(T,14,26,26)`，函数内再 `unsqueeze(0)` 成 5-D 崩溃。
   重构为 **chunk_episodes(mb=256) 固定 minibatch**：GAE 先按局算好，chunk 只作更新
   粒度——同时解决整局 1200 样本单次前向的激活内存问题，并增加梯度步数。
另：补 `--seed`（默认 7，minibatch 洗牌可复现）；移除未使用的 old_val。

**遗留提示**：(a) run_rl.sh 无 `set -e`，单轮失败会静默续跑，正式训练前建议加；
(b) 超时局的末个 pending 被丢弃且 done=0 → GAE 对末步 bootstrap V=0，轻微偏差可接受；
(c) 奖励权重初值（W_WIN=5/W_KILL=0.2/W_BASE=1.0/W_SURV=0.01）按 §3.3 待首轮 reward
曲线监控后定稿。

### 3.13 R3 实施：奖励与 godai-score v7 全面对齐 + 维度遥测（2026-08-21）

**动机（击杀趋势诊断，`tmp/kills_trend.py`）**：旧奖励下击杀数 11 轮无增长
（2.4–3.9 波动；标量法与奖励分解法交叉验证一致）。根因：存活流收益
(W_SURV=0.01×~340 步 ≈ 3.4/局) ≫ 击杀收益 (W_KILL=0.2×3 杀 = 0.6/局)，
策略理性选择苟活。候选 R1/R2/R3/C1 中用户拍板 R3。

**R3 设计**（export-rl-rollout.ts 整体重写）：
- 势函数 Φ(s) = SCALE(10) × lossBandMax(0.4) × Q_partial(s)；Q_partial 按
  DEFAULT_LOSS_WEIGHTS 复刻 weightedQuality 的 null-剔除重分配。
- 支付：决策窗势差 r_t = Φ(t)−Φ(t−1)，终局对账 `extra = SCALE×score − paidTotal`
  ⇒ **每局 Σr ≡ SCALE×score 精确恒等**（胜局经带切换自然放大 0.70 vs 0.40）。
- 苟活零收益（v7 无 survival 维）——结构性修复 reward hacking。
- Telemetry 逐字段对齐 simulation-runner（SAMPLE_TICKS=6/RADIUS=12/RING 8 格/
  事件计数/powerup census same-tick 对账），dims{value,raw} 全量进 manifest 与
  _rl_report → run_rl 聚合 → training_log.jsonl（score_mean/std + dim_means）。

**实现坑**（均已修）：块注释内 `a_*/lp_*` 提前闭合注释；首决策点 pending=null
时 paidTotal 已入账但 flushPending 空转 → 恒等式差 Φ_0（冒烟抓到，−1.80 精确
吻合）；timeout 局 done=0 样本丢失改为统一终局 flush。

**验证**：timeout 6 局 + base_destroyed 6 局恒等式 max|diff|≈1e-7；正式训练
it8 全部 50 shards 恒等成立（2.2e-07）。分数随行为合理分化（10 杀局 0.194 vs
速死局 0.117）。

**R3 首轮观察（it1–it8）**：行为画像剧变——局均 ticks 7000+→3300–4100、timeout
近消失、出门交战捡道具；但 score 平坦 0.10–0.12、击杀未涨。KL 0.036–0.064 偏高
但未触警；entropy 震荡无坍缩。判定：激励结构生效、梯度尚未爬上——待 14h 长跑。

**长跑基建（run_rl.py）**：`--iters 0` 无限 + `--max-hours` 墙钟预算；
`--keep-iters 3` 轨迹磁盘上限（~170MB/iter，不清盘 14h 写满磁盘）；连续失败
5 次重试（30s 退隔）防瞬时故障中止。rotate 模式改**随机分批**（每 epoch 全
35 关随机置换切 7 批）——修固定窗口在续训迭代号归零导致的 stages 0-4 过采样；
再升级为 **35 关×2 种子=70 局/iter**（中断免疫 + 每轮全分布指标，代价迭代
8.4→13–16min）。rotate 种子掺启动时刻防课程重放（记入 run_start.rotateSeed）。
顺手修 wins 计数从未自增的潜伏 bug（历史 winRate 恒 0 掩盖）。

### 3.14 R3 长跑失败复盘：秒投降坍缩 + F3/F3b/F4 修复（2026-08-22）

**失败事实（training_log.jsonl it1–101 全量核验）**：R3 无限长跑在 it41 后 score
钉死 0.1211（精确 = 0.4×(0.256×1.0+0.044×1.0)/0.991，分毫不差），策略坍缩为
**开局弃战退化解**——mobility≤0.04 / progress=0 / lives≈1.00，敌人 ~211 tick
拆基地速终（it100 manifest 单局 ticks=211）。0.121 > 认真打仗实测 ~0.110，
PPO 收敛到投降是理性的。it100 KL=0.96 爆表更新。

**根因（Goodhart 倒挂，三机理）**：
1. **指标冻结**：局越短采样越少，basePressure 只采到开局敌人尚远的样本，
   baseSafety 均值停在 ~0.90–1.0；lives 零损耗。
2. **权重比倒挂**：progress 权重 0.477 但击杀难赚；lives 0.256 躲着就满值。
   低击杀率下交战净亏（阵亡扣的 lives > 击杀赚的 progress）。
3. **鸡生蛋护城河**：打仗有利需先有击杀技术；练击杀必先接受亏损。
   PPO 选不交学费路径——连枪都不开（accuracy=0），比旧苟活更彻底。

**监控失效教训**：KL_WARN 从 it37 持续告警但无动作，夜间无人值守空转 ~60 轮。
→ **告警必须自带牙齿**（F4）。

**F3 基地失守门控**（export-rl-rollout.ts）：`BASE_LOSS_MULT=0.25`，
base_destroyed 局终局锚点 `SCALE×score×M`。**关键设计：M 必须双点落地**——
只改 Φ 不改终局锚点时，对账项 `extra = SCALE×score − paidTotal` 会精确抵消
门控差值（Ng et al. 势塑形不变性：Φ-only 改变重分配时机但不改总回报）；
故 M 同时进 `countersPhi()`（base 拆后所有窗 Φ×0.25，窗口级信用分配语义
一致）与终局锚点（真正翻转总回报排序）。

**F3b 败局剔除 lives 维度**：`RL_LOSS_WEIGHTS = {...DEFAULT_LOSS_WEIGHTS,
lives: 0}`。败局里 lives>0 只出现在 base_destroyed 局（lives_exhausted 局
lives=0），它支付的正是「基地死时自己没死」的投降画像且与交战负相关——
坍缩主要收入源（0.256/0.991）。其余维度审计结论：**保留全部**——
progress/tempo/openingTempo/loot 是努力型指标（多打多得）；baseIntegrity 对
lives_exhausted 局有区分度；baseSafety 的冻结伪影被 F3 门控中和（残余投降
分 ≈0.4×(0.044/0.735)×0.25 ≈ 0.006）；**胜局带不动**（全维度以「赢」为前提，
无被动通路）。godai-score.ts 评估口径不动（God-AI 基线可比性），RL 专用
RL_SCORE_CONFIG 分流。

**F4 KL/熵双判据熔断**（run_rl.py）：KL_BREAK=0.15 连续 3 轮（暴力漂移）或
ENT_BREAK=0.60 连续 8 轮且 winRate<0.5（退化确定性；纯 KL 判据抓不住本次
失败——it65/73/79 尖峰均为单轮，从不连续；熵地板按日志本应 it63 即触发）。
触发后写 circuit_break jsonl 事件 + exit code 3。实现坑：主循环 except 会吞
SystemExit 重试，故用标志位+break 而非 raise；winRate<0.5 守卫防误杀合法收敛。

**冒烟验证**（坍缩权重 s0/seed0，复现秒投降 231 ticks）：gated score
0.1211 → **0.005986**（quality=0.044/0.735×0.4，loot 为 null 一并出分母），
≪ 打仗实测 0.110，倒挂彻底翻转；恒等式 Σr ≡ SCALE×gatedScore 成立
（|diff|=1.75e-9，float32 精度口径）；manifest 新增 rewardScheme=
'v7-aligned-f3' / scoreUngated / quality 三值自洽。tsc + py_compile 通过。

**重启指引**：归档坍缩权重后删除 tmp/rl-weights/weights.json（build_model
自动回退 BC warm-start 全新初始化——坍缩权重 entropy 已塌至 0.36–0.68，
resume 会滑回同一盆地）。经启动器跑：`nn-training/start-training.ps1 -Script run_rl.py ...`。

### 3.15 R4 无限长跑启动 + detach 后台化修复（2026-08-22）

- 坍缩权重已删，旧轨迹归档 `tmp/rl-traj-r3-collapsed`（取证保留）。
- **启动器缺口修复**（start-training.ps1）：detach 分支原硬编码只认
  `train_loop.py`，run_rl.py 落入前台执行（绑终端会话，会话结束即死）。
  放宽为 `-in @('train_loop.py','run_rl.py')`。坑：编辑工具写回剥掉 UTF-8
  BOM → PS 5.1 GBK 误读中文注释 ParserError（§2.2 同坑二踩），手工补回
  EF BB BF。**教训：凡编辑 .ps1 必查 BOM**。
- **run_rl.py cwd 锚定**：detach 的 WorkingDirectory 是 nn-training/，而
  run_rl.py 全部默认路径是 repo 根相对（tmp/...）→ 后台首启即死于读不到
  BC 权重（前台复现过一次）。修法同 train_loop.py 的 REPO_ROOT 模式：
  main() 开头 `os.chdir(dirname(dirname(abspath(__file__))))`。
- R4 启动确认：07:45 run_start（iters=0 无限、max_hours=0、rotate 35×2
  种子、lr 3e-4、epochs 3、BC warm-start 全新初始化），it1 shards 正常产出。
- **每小时自动巡检上线**（TRAE 定时任务）：进程存活 + training_log 健康度
  判读（entropy 地板 / KL 连续超限 / ticks 骤降=秒投降复发特征）+ 异常时
  直接修复并经启动器重启，修复记入本文件。

### 3.16 R4 首次健康度巡检：进程环境性死亡，续训重启（2026-08-22）

- **发现**：12:01 巡检时无 python 进程；日志尾部为 it18（11:32:43 完成），
  `tmp/rl-traj/it19` 半成品 rollout 最后写入 11:35:02 → 进程死于 it19
  rollout 中途。**无 circuit_break**（F4 未触发）、无崩溃栈。
- **根因**：环境性死亡——系统事件日志无重启（uptime 3.2 天）无 OOM，
  推断为现代待机/外部干预杀进程；非代码缺陷，无需改训练代码。
- **18 轮健康度（it1–18，hard，70 局/轮）全部健康**：
  winRate 0%（BC warm-start 起步 4h，base_destroyed 为主败因）；
  score_mean 0.020→0.051 平稳波动（F3 门控压低败局分属预期）；
  entropy 1.14→1.31 缓升（≫0.8 地板）；KL 0.035–0.051（稳态区间
  0.045–0.054 内）；局均 ticks 3105–3891（秒投降 <1000 特征未复发）；
  progress 0.09→0.17 缓爬、mobility 0.46–0.61 远离 0。无新 hack 模式，
  不触发奖励公式修改。
- **修复**：清残留 python → 启动器 detach 重启（同 R4 参数）。权重安全：
  `tmp/rl-weights/weights.json` 停于 it18（11:32:43），run_rl.py
  `build_model()` 自动 resume（run_start 在 build_model 之后写盘，
  12:06:48 run_start = resume 成功）；残缺 it19 目录由每轮开头
  `shutil.rmtree` 自清理，无 off-policy 样本污染。
- **验证**：12:07 python 双 PID 存活 + 新 run_start 参数与原配置逐项一致。
- **待观察**：winRate 连续 18 轮为 0 属 RL 早期正常（progress 在涨），
  若 ~40 轮后仍全零且 progress 停滞，再议奖励倒挂排查（届时报人工确认）。

### 3.17 keep-iters 3→5 无损切换 + 巡检：可胜关卡 6→16（2026-08-22）

- **巡检（19:00，it25–30 健康）**：winRate 1.4%–14.3% 波动、score_mean 0.087–0.183
  随胜率联动（F3 门控正常）；entropy 1.19–1.22、KL 0.022–0.028、局均 ticks
  4000–4300、progress 0.359→0.385 缓爬、mobility ~0.74。无 circuit_break。
- **数据缺口**：it27 原始报告被 `--keep-iters 3` 滚动删除，巡检增量扫描漏扫
  （每关少记 2 局）。胜局记录依赖巡检 state 累计，原始目录保留窗口太短是
  结构性风险 → 用户拍板 keep-iters 3→5（磁盘 ~170MB/iter，5 轮 ~850MB 可接受）。
- **切换时机（无损）**：it31 的 70/70 rollout 报告已完整落盘、PPO 更新进行中
  → 先把 it31 扫入 state（7 胜，含新破关 11/堡垒、20/棱堡；同心圆、终极堡垒
  打破连续 0 胜）→ Stop-Process 停机（权重停于 it30，it31 in-flight 更新作废、
  新 run 首轮重做）→ 启动器 detach 重启，参数同前仅 keep-iters=5。
- **验证**：19:20:42 新 run_start `keep_iters: 5` ✓；python 双 PID 存活 ✓；
  it1 rollout 开跑 ✓。迭代编号重置为 it1，巡检 state 保留累计
  （420 局 26 胜、可胜关卡 16 个）并注明「新 run_start 不重置累计」；
  旧 run 孤儿 it29–it32 目录留存盘上（已扫描/作废，新 run 到同编号时自清理）。
- **教训**：`--keep-iters` 是启动期参数，改值必须重启；重启前先确认当前轮
  rollout 报告是否已完整（70/70），完整则先扫后杀，零数据损失。

### 3.12 决策：不做 rollout/PPO 流水线，瓶颈转向 PPO 自身（2026-08-21）

用户问「下一轮 rollout 能否与上一轮 PPO 并行」。实测相位（it7–it9）：rollout
~2.5min / PPO ~5.8min（**PPO 占 70%，瓶颈已换位**）。

**结论：严格 on-policy 下不能。** 依赖链 `rollout(i+1) ← W_i ← PPO(i)` 是算法语义：
数据必须来自被优化策略本身，提前采集 = off-policy 数据，importance ratio 系统性偏移。
推演过的变体：
- **A. 有界陈旧性**：允许落后一个更新的权重（KL≈0.05 下近似成立），省 ~30%，
  但训练语义改变、与严格基线不可比 → **否决（本轮）**
- **G. 分波流水线**：权重整窗冻结、两波游戏交错 SGD，on-policy 性质保留，
  但收益仅 ~15%（相位不等长限制重叠窗口）→ 复杂度/收益不成比例，**否决**
- **H. epochs 3→2**：PPO 内部直接砍 ~2min/轮（KL 0.045 说明更新有富余）→
  **留作下轮规模决策时的首选提速杠杆**，当前验证跑不动

另清理 `tmp/rl-traj/it10–14` 陈旧目录（旧运行残留；判定用 `find -newermt`，
字符串比较 `%T+` 的 `+` 字符会破坏排序——第一版检查因此全误判 LIVE）。

---

### 3.11 评审处置：rl-mimo.review.md 确认项全部落地（2026-08-21）

外部评审（`rl-mimo.review.md`）逐条核实后处置。**采纳并落地**：
- **Q1 死代码**：删除 `rl_env.py`（np.random mock 桩）/ `rl_ppo.py`（未接线的 PPO 类）/
  `train_rl.py`（无入口调用）；`rl_model.py` 保留加 STATUS 注释（P1 教师模型参考）；
  `eval_bridge.py` 经核实**非死代码**（BC 管线工具，AGENTS/README/启动器引用），保留。
- **Q4 监控**：`run_rl.py` 每轮追加 `training_log.jsonl`（run_start 元行 + per-iter
  winRate/outcomes/samples/policy/value/entropy/kl/lr/mb/epochs）+ KL 预警（阈值 0.08，
  按实测稳态 0.045–0.054 校准；评审建议的 0.02 会永久误报）+ entropy 单轮骤降 >0.1 预警。
- **时间戳**：run_rl.py / ppo.py 全部日志行加 `[HH:MM:SS]` 前缀（此前无法事后分析节奏）。
- **计划偏差回填**：`plan/RL-Bun-Bridge.md` 顶部加状态横幅指向实际 npy shard 架构。

**证伪的评审主张**（留档防复发）：Q5 参数数字全过期且 epochs=8 与 KL 实测矛盾；
Q6 break 路径 flush 已存在；Q8 torch/bun「竞争」不存在（两阶段严格串行）；
3.1 语法错误不存在（py_compile 通过）；3.4 rotate_rng 实为可复现。

**顺延项**：(a) Q2 的 `export-rl-rollout.ts` 权重注释——活体训练每局重新 spawn 该文件，
本轮跑完前禁改；(b) Q3/S4 reward 归一化、Q7 value warmup、S2 v7 对齐——等首轮完整曲线，
当前 advantage 归一化已兜底、曲线健康；(c) S1 复用 train_loop 日志框架——P2。

---

### 3.10 PPO 提速排查：安全杠杆无效，mb 翻倍是正解（2026-08-21）

用户要求 PPO 提速。独立进程基准（90→16 chunks，4 配置 × 2 轮）结论：
- **线程数（12 vs 8）与 flush_denormal 在稳态下零差异**（四配置全部 ~27s/epoch）。
  第一轮测得的巨大差异（28–68s）是杀训练后系统抖动噪声——单次采样基准不可信，
  必须稳态复测。
- 已落地的无害改动：chunk 张量转换移出 epoch 循环（省 2× 冗余转换）、
  `--threads` 参数（默认 8 对齐物理核）、flush_denormal best-effort。
- **真正杠杆：mb 256→512**。梯度步 270→135/轮 → PPO ~5.5min→~3min，一轮
  ~8.5–9min→**~6.5min**，15 轮 ETA 从 ~2.2h 降到 **~1.5h**。动态影响：更少更大
  的更新 = 每轮 KL 漂移更小，与 §3.7 的 KL 偏高顾虑同向，属双赢。

训练已用新配置重启（自 it4 权重 resume，轮番方案不变）。

---

### 3.9 正式训练启动 + rollout 并行化（2026-08-21）

**并行改造两步**：(1) 初版按种子分区 `seeds[k::W]`——seeds=4 时 worker 被钳到 4，
8 物理核只吃满一半；(2) 改为 **按 (stage,seed) 游戏对粒度调度**：ThreadPoolExecutor
把 16 局摊进 W 个并发槽，每局一个 bun 进程（单局启动 ~300ms 可忽略），shard 落
`w{i}/` 子目录、`discover_rl_shards` 递归消费无需改动。实测 16 局 rollout 从串行
~5min 降到 **~1.5–2.5min**（尾效应限制：快局先退、长局收尾）。

**正式训练**（`--epochs 3 --workers 12`，自 BC 全新初始化，权重每轮原子落盘）：
KL 稳定在 0.047–0.054（EPOCHS=3 生效，对比试跑 EPOCHS=4 的 0.094）；entropy
1.298→1.213 缓降（策略开始收敛）；policy loss 0.373→0.096；outcome 出现
lives_exhausted 占比上升（2→4/16 局）的早期存活信号；win rate 仍 0%（hard 冷启动
预期内）。节奏 ~3.5–4 min/轮，15 轮预计 ~1h 内完成。

---

### 3.8 启动脚本收敛：run_rl.sh 下沉为 run_rl.py（2026-08-21）

用户要求减少启动脚本。**不把循环塞进 sh+ps1 各一份**（双实现漂移），而是把
on-policy 循环下沉为 Python：重写 `run_rl.py`（原为旧脚手架 train_rl.py 的包装）
为主循环——bun rollout 走 subprocess、PPO 更新进程内复用 `ppo.py` 的
`load_episodes/chunk_episodes/ppo_update`（模型常驻内存，省去每轮 torch 重启），
权重每轮原子写回（`save_weights_json` 改 temp+os.replace，长跑崩溃不留半截文件）。
删除 `run_rl.sh` 与旧启动脚本 `start_rl.bat`；启动器收敛为规范一对：

```
bash nn-training/start-training.sh --script run_rl.py --iters 15 [--epochs 3 ...]
powershell ... start-training.ps1 -Script run_rl.py --iters 15   # 参数透传
```

新路径小规模验证 EXIT=0（且幂等 resume 生效：从上轮 RL 权重续跑而非重初始化）。
`ppo.py` 单步 CLI（--init-from/--resume）保留不变。
**待清理候选**（旧脚手架，已被新管线取代，删除前待拍板）：`train_rl.py`、
`rl_env.py`、`rl_ppo.py`、`rl_model.py`。

---

### 3.7 小规模试跑 it1：链路全通（2026-08-21）

`ITERS=1 STAGES=0-1 SEEDS=0-1 bash nn-training/run_rl.sh`（hard，MAXT=12000，EPOCHS=4）：

```
init    : BC warm-start + 随机 value head，68683 params ✅
rollout : 4 局 / 1152 样本 / 11498 ticks；outcome 全部 base_destroyed
          （s0sd0 4405t / s0sd1 1284t / s1sd0 2651t / s1sd1 3158t）
          —— hard 冷启动 0% 胜率符合 §3.5 预期 ✅
PPO     : epochs=4 policy=0.5790 value=0.7676 entropy=1.2888 kl=0.09391
          mean_ret=-0.195 ✅
权重验证: value_head.* 新增；40/42 策略张量发生变化（非原样写回）✅
```

**试跑暴露并修复**：run_rl.sh 未 `mkdir -p $TRAJ` → 日志重定向失败导致 rollout/PPO
整步被静默跳过（正是 §3.6-(a) 预警的形态）。已修：`set -eu` + 循环前建目录。

**正式跑前的调参读数**：
- **kl=0.094 偏高**（经验安全区 ~0.01–0.05）：EPOCHS=4 × mb=256 下单轮策略偏移较大，
  有侵蚀 BC warm-start 的风险。建议正式跑降为 EPOCHS=3 或 LR=2e-4，并在 ppo.py 加
  KL 早停（approx_kl 连续超阈则 break epoch 循环）——未实施，待拍板。
- value=0.77 属正常（价值头从零学起，需数轮收敛）。
- entropy=1.29 > 冒烟期 1.06：随机化探索正常，观察后续迭代是否按预期下降。

---

## §2.2 启动器 Windows 本机验证：ps1 双 bug 修复（BOM + 参数风格兼容，2026-08-21）

用 AGENTS.md 规定的幂等自检验证本机 torch 可用性：

- bash 版 `bash nn-training/start-training.sh --check` ✅ 直接通过，
  torch **2.7.1+cpu** @ `.venv/Scripts/python.exe`（OMP threads=12）。
- PowerShell 版 `powershell ... start-training.ps1 -Check` ❌ 解析失败，两个独立 bug：

1. **UTF-8 无 BOM → PS 5.1 按 GBK 解码**。本机无 pwsh 7，Windows PowerShell 5.1
   对无 BOM 的 .ps1 一律按系统 ANSI 代码页（中文系统=cp936）解码，UTF-8 中文注释
   字节被误读后破坏字符串引号配对，整个文件 ParserError。
   **修复**：文件头加 UTF-8 BOM（`EF BB BF`，微软官方推荐做法，对 pwsh 无副作用）。
2. **switch 只匹配 `'--check'`，`-Check` 落入透传**。`powershell -File` 调用时参数
   按字面量进入 `$args`（不做原生参数绑定），`-Check` 无法命中 POSIX 风格分支，
   被当未知参数转发给 train_loop.py → `unrecognized arguments: -Check`。
   **修复**：CLI 解析改 `switch -Regex` + `'^--?name$'`，同时兼容 `-Check` 与
   `--check`（PS switch 默认不区分大小写）；训练脚本参数均为 `--xxx` 长选项，
   不会被误吞。

**验证**：`-Check` / `--check` / `-Echo -Script train_bc.py --arch student` 全部
exit=0 且正确消费/透传参数；`bun run check` 全绿（1429 pass / 0 fail）。

**教训**：(a) 含非 ASCII 的 .ps1 必须带 BOM 才能跨 PS 5.1/pwsh 正确解析；
(b) `-File` 调用无参数绑定，脚本内 CLI 解析必须自行兼容两种前缀风格；
(c) Git Bash 终端显示 powershell.exe 的 GBK stdout 会乱码——那是终端显示问题，
以退出码和 ASCII 行（venv/torch version）为准。

**后续（同日）：VBS 启动器移除。** 删除 `launch-training.vbs` / `launch_rl.vbs`，
`--detach` 改为原生 `Start-Process -WindowStyle Hidden`（ShellExecute 派生完全
脱离控制台的进程，等价旧 VBS 行为，且规避 VBScript 被微软弃用的趋势）；bash 版
detach 分支委托给 ps1（detach 行为单一定义，避免两处维护）。顺手修复
`smoke_test.py` 的 arg-proxy 缺属性 bug（缺 `arch`/`notes`/`resume`，
train_bc.train 2026-08-20 前后新增字段未同步），修复后端到端 PASS。
验证：Start-Process 隐藏派生真实执行 smoke_test 并回传退出码；`-Check`/`--echo`
干跑 exit=0；`bun run check` 全绿。

---

## §2.1 移动死锁修复 + 0% 根因收敛（续 §2，2026-08-20）

### 关键修正：policy-input.ts 的 move-freeze 死锁（§2 评估 0% 的真正主因）

§2 报告「学生 BC 12ep → avgKills=0 / avgTicks=3933」与「+DAgger 6ep 仍 0%」。
重新诊断发现一个被忽视的**部署语义死锁**（非权重/精度/方向问题）：

- `policy-input.ts` 原把 move 头 argmax=0（`none`）映射为 `moveDir = null`；
- `SimulationPlayer` 在 `getMoveDirection()` 返回 null 时设 `moving=false`，
  坦克在出生点原地静止。但 `none` 在教师(God-AI)语义里 = **「保持当前航向」**，不是「停」；
- 世界状态冻结 → 模型每 tick 仍见静止状态 → 持续预测 `none` → 永久锁死，
  0 击杀、基地最终被毁（avgTicks≈3933 即 base_destroyed 时间线）。

**修复**：`none` 改为「持有上次指令方向」(`lastDir`)，坦克保持移动、世界状态
活跃。修复后单局 trace 从 `distinctCells=1 / kills=0` 变为 `distinctCells=200 / kills=1`。

### 修复后正式评估（stages 1-5 × seeds 1-10 = 50 局，hard，--policy nn，权重不变）

```
WIN RATE 0.0% (gate 60%) -> FAIL
totalKills=13  avgKills=0.26  avgTicks=3331
SCORE V7 suite=0.0691 lcb=0.0656 meanWinRate=0
```
avgKills 从 **0（冻结）→ 0.26（移动）**，avgTicks 3331 表明坦克现在会移动并
零星开火，但仍在 ~55s 内阵亡。**结论**：冻结死锁已修复（移动恢复），但残余
0% 来自**分布漂移**——学生在「自己的部署状态」上几乎不开火（部署 trace：
ready=true 时 `fireLogits[0] >> [1]`，fire 命中仅 3/477）。这与 §2 的
「BC 分布漂移」根因一致，但本次是更纯粹的**学生自部署漂移**（教师只在教师
状态上标过 fire）。

### 对 §2 DAgger 回合的影响（重要）

§2 的 DAgger 冒烟（9 局 3725 样本）是在 **freeze bug 存在时**采集的——学生
冻结在出生点，所有采集状态都聚集在 spawn 邻域，是一份**退化分布**，故续训
毫无帮助（move acc 0.35→0.386 也只是拟合 spawn 邻域）。修复后学生真正移动、
访问真实状态空间，DAgger 采集才首次有效。

### 本次交付

| 交付 | 文件 | 状态 |
|------|------|------|
| 移动死锁修复 | `src/nn/policy-input.ts`（`lastDir` 持有语义） | ✅ 已落地，评估验证 |
| DAgger 采集器（清理版） | `tools/sim/export-dagger-labels.ts` | ✅ smoke 2 局/330 样本通过 |
| 正式 DAgger 采集 | `tmp/dagger/`（stages 0-4 × seeds 0-9，50 局） | ✅ 完成：19783 样本 / 163124 ticks，50 shards，obs `(N,14,26,26)` 与 godai 同构（godai 368M / dagger 182M） |
| 混合重训 | `start-training.ps1 -Script train_bc.py --data-dir tmp/mix --arch student --resume tmp/student-weights-full/weights.json --out tmp/student-weights-dagger/weights.json --epochs 30` | ✅ 完成（task 8898no，11663.6s≈3.24h，torch 2.7.1+cpu）。30ep: train_loss 1.90→1.1242, val_loss 1.33→1.2064, move_acc 0.449→0.616, fire_acc 0.860→0.887, item 1.000。`tmp/mix`=100 shards/549M，samples=59995。权重：`tmp/student-weights-dagger/weights.json`(active) + `weights.20260821-123437_ep30_val1.2064.json` |
| DAgger 续训后保留率评估 | `m1-eval --policy nn --weights-dir tmp/student-weights-dagger`（st 1-5×sd 1-10, hard） | ✅ 完成（task LXzEAh）：WIN 0%（gate 60% FAIL）；suite 0.0691→0.1027(+49%)，totalKills 13→104(8×)，avgKills 0.26→2.08，Crossfire 冻结→1。DAgger 证实修复射击漂移；残余 0% 为策略深度差距，非 bug |

### 下一步

1. ✅ `tmp/dagger/` 已采集完成（19783 样本，50 shards）；已合并为 `tmp/mix/`（100 shards / 549M）。
   续训已用启动器拉起。`start-training.sh` 在 Windows 上实测把 `/d/...` 双重化成
   `D:\d\...` 导致 exec 秒失败（task 56DGy3），改走 `start-training.ps1`（原生 Windows
   路径，无 MSYS 转换）成功：`powershell ... start-training.ps1 -Script train_bc.py
   --data-dir tmp/mix --arch student --resume tmp/student-weights-full/weights.json
   --out tmp/student-weights-dagger/weights.json --epochs 30`（task 8898no，后台运行中）。
   **`.sh` 路径双重化 bug 已修复**：新增 `to_win_path` + `MSYS_NO_PATHCONV=1`，`--echo`/`-h`
   实测输出干净 `D:\github\...\python.exe -u D:\github\...\train_bc.py`，不再出现 `D:\d\`。
   目标：把 fire 头在学生自部署状态上拉起。
2. ✅ 重训后保留率评估完成（task LXzEAh）：`m1-eval --policy nn --weights-dir
   tmp/student-weights-dagger`，50 局（st 1-5 × sd 1-10, hard）。
   **结果**：WIN 0%（gate 60% FAIL）；但 suite 0.0691→0.1027(**+49%**)，
   totalKills 13→104(**8×**)，avgKills 0.26→2.08，avgTicks 3331→3572，
   Crossfire 从冻结(0)→1。DAgger **证实修复射击分布漂移**，学生从"几乎不开火"
   变为"会作战"。残余 0% 是**策略深度差距**（基地防御/清场效率/路径），
   非部署 bug——God-AI 同条件 suite≈0.5753(75%胜)。
   → 已选：**迭代 DAgger 第二轮**（用户 skip 决策、按 continue 推进推荐项）。
3. 🔄 DAgger 第二轮采集运行中：`export-dagger-labels.ts --out tmp/dagger2
   --weights-dir tmp/student-weights-dagger`（st 0-4×sd 0-9，学生=第一轮权重，
   状态分布更真实）。完成后 mix=`tmp/godai`+`tmp/dagger2`（丢弃旧 dagger 弱状态），
   resume=`tmp/student-weights-dagger/weights.json`，output=`tmp/student-weights-dagger2`，
   再跑 `m1-eval` 量化。
4. RL 教师落地后，同一管线直接复用（仅换 label 源）。

---

## §2 P1.5: God-AI 教师端到端蒸馏管线（学生架构）验证 (2026-08-20)

> 计划：`plan/RL-Net-Selection.md` §4.3–4.4（v4/v5）。目标：在 RL 教师落地前，用现成
> God-AI 当教师，端到端验证「CoordConv-ConvMixer-Lite 学生（68,554 参数）+ 离线蒸馏 +
> DAgger 在线蒸馏 + TS 推理 + 保留率测量」整条管线。

### What was built

| 组件 | 文件 | 说明 |
|------|------|------|
| 学生模型 | `nn-training/student_model.py` | ConvMixer-Lite h=64/d=8，BN-free，68,554 参数 / ~37M MAdds；forward 内追加 2 个 coord 通道（uint8 0..255，不除 255）；语料保持 14ch 不 bump schema |
| 学生训练 | `nn-training/train_bc.py` | 新增 `--arch student`（默认 `bc` 路径不动）；复用 masked CE / AdamW / Cosine / best-val 导出 |
| TS 学生推理 | `src/nn/infer.ts` | `StudentModel`（conv3x3/conv5x5dw groups=h/conv1x1/GAP/linear，零分配缓冲）+ `ModelLike` 接口 + `buildModelFromJson/Text` 按 `arch.kind` 分发 |
| 输入适配 | `src/nn/policy-input.ts` | 改用 `ModelLike`（cachedModel/loadModel/NNInput.model）；`think()` 决策谓词 `t==0 || t%K==0 || itemAppeared` |
| God-AI 采样器 | `tools/sim/export-godai-labels.ts` | 离线蒸馏语料导出器：`--stages/--seeds/--difficulty/--out/--max-ticks/--verify-determinism`；writeShard 与 BC 格式一致；确定性双跑字节比较 |
| DAgger 采样器 | `tools/sim/export-dagger-labels.ts` | 学生（NNInput）驱动真实引擎 + 独立 RNG 的 God-AI labeler 每 tick 跟读世界；在 `t==0 || t%K==0 || itemAppeared` 采 (state, God-AI label)；labeler 每 tick think 保持内部状态一致 |
| 评估 | `tools/sim/m1-eval.ts`（既有） | `--policy nn --weights-dir <dir>`；God-AI 基线 `--policy god` 同种子对比 |

### Verification results

- **确定性导出**：`--verify-determinism` 双跑 3 局 5 npy 文件字节一致（`[DET OK]` ×3）。
- **TS↔Python 前向一致**：同权重 + 同 obs/scalars（corpus shard 第 0 样本），TS `StudentModel`
  对 Python `StudentNet` 三头 logits maxAbsDiff ≈ 4e-5（float32 累加顺序噪声），argmax 全 MATCH。
- **权重格式**：42 键（stem/8×blocks{dw,pw}/fc/三头 ×{weight,bias}），与 `StudentModel` 完全匹配。
- **端到端冒烟**：9 局 God-AI 语料（8,252 样本）→ 12 epochs → 5×5 hard 评估。
- **DAgger 冒烟**：学生 9 局（3,725 样本）→ 合并续训 6 epochs → 5×5 hard 评估。

### Eval 对比（hard，5 stages × 5 seeds，同种子）

| 策略 | 胜率 | 说明 |
|------|------|------|
| God-AI（教师，基线） | **72%** (18/25) | suite=0.5985 lcb=0.5291 |
| 学生（BC 12ep，8.2K 样本） | 0% (0/25) | avgKills=0，avgTicks=3933 |
| 学生（+DAgger 6ep，11.9K 样本） | 0% (0/25) | move acc 0.35→0.386 |

0% 属**语料量/轮次不足**（不是管线 bug）：学生在打游戏（平均 96 发子弹/局）但 move 头太弱不会瞄准；
God-AI 教师 72% 门内。val_loss 1.73–1.87，move acc ~0.39（5 类 hard-label，teacher 自身随机）。

### 性能实测（本机，torch CPU 8 线程）

- 训练吞吐：学生 ~3.4ms/sample/step（depthwise 5×5 + pw 1×1 在 torch CPU NCHW 上极慢，1.7s/step@b256；
  channels_last 后 0.88s/step@b256；batch512 无增益；16 线程反降）。实测 ~2.5min/epoch @ 8.2K 样本。
- **全量 35×10 语料（~158K 样本）× 25 epochs ≈ 3.5–4h CPU** —— 这是唯一能抬出非零保留率的下一步。
- DAgger 导出：labeler 每 tick think 使导出 ~18× 慢于纯 God-AI 导出（9 局 162s）。

### 关键教训

1. **学生架构的 depthwise 卷积是 CPU 训练瓶颈**：torch 对 groups=h 的 5×5 dw 无高效实现；
   `channels_last` 仅 2× 加速。69K 参数换来 10× 的每样本 FLOPs（vs BC 52K）——训练成本必须
   计入保留率实验预算（web 推理端 TS 零分配 ~ms 级，部署不受影响）。
2. **BC 语料 label 的 teacher 自身随机性**：God-AI 在相同状态有随机性，hard-label CE 天花板低
   （move ~0.4）。DAgger 标签同样受此影响。
3. **确定性契约成立**：TS 端逐字节复现 Python 前向（float32 顺序噪声内），coord 通道公式、
   uint8 0..255 尺度、GAP、scalar concat 全部对齐。
4. **保留率基线（尚未达成）**：需全量语料训练后重测；届时报告 `学生胜率 / God-AI 胜率`。

### 下一步

1. 全量导出 `--stages 1-35 --seeds 1-10` God-AI 语料 → 学生训练（可后台跑，~4h CPU）。
2. 评估 + 算保留率；若不足，追加 DAgger 回合（学生当前权重 + 更多 seeds）。
3. RL 教师落地后，同一管线直接复用（`student_model.py` 不变，仅换 label 源）。

---

## §1 v2: Scalar Fusion Architecture (2026-08-19)

### What changed

The v1 backbone ignored all 24 scalar inputs. v2 concatenates the 24-dim scalar vector with the GAP output before the FC layer:

```
v1:  obs(14×26×26) → Conv(32→48→64) → GAP → FC(64→64) → heads
v2:  obs(14×26×26) → Conv(32→48→64) → GAP → cat(scalars) → FC(88→64) → heads
```

**FC layer input**: 64 (GAP) + 24 (scalars) = 88. Weight shape [64, 88].

### Files modified

| File | Change |
|------|--------|
| `nn-training/model.py` | `nn.Linear(c + scalar_dim, head_hidden)` + `torch.cat([x, scalars], dim=1)` |
| `nn-training/weights_io.py` | `load_state_into` tolerates FC shape mismatch (loads 13/14 params, skips FC) |
| `src/nn/infer.ts` | `fusedBuf` = pooled + scalars → FC; TS forward matches Python exactly |

### Parameter count

| | v1 | v2 |
|--|-----|-----|
| Total | ~50K | ~52K |
| FC input dim | 64 | 88 |
| FC params | 4,160 | 5,728 |

### Training warm-start strategy

Old conv weights (13/14 params) loaded into v2 model. FC layer randomly initialized.
This preserves learned spatial features while the FC layer learns to use scalar inputs from scratch.

### First epoch results (warm-started from v1 R10)

| Epoch | train_loss | val_loss | move_acc | fire_acc |
|-------|-----------|----------|----------|----------|
| 1 | 1.7835 | 1.3834 | 0.590 | 0.852 |
| 2 | 1.2787 | 1.3521 | 0.583 | 0.851 |

val_loss 1.35 at epoch 2 is already lower than v1's from-scratch start (1.91),
confirming the warm-start works — conv features transfer.

### Training timeline (v2, 68K samples)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +21.9% | Starting point |
| **R2** | **0.9984** | — | 🏆 Best — breaks v1 ceiling (1.0919) |
| R3 | 1.0066 | +0.8% | Plateau |
| R4 | 1.0172 | +1.9% | Overfitting begins |
| R5 | 1.0256 | +2.7% | — |
| R6 | 1.0342 | +3.6% | — |
| R7 | 1.0481 | +5.0% | — |

**Pattern**: same as v1 — val_loss bottoms at R2, then monotonically increases.
Scalar fusion lowered the ceiling (0.998 vs 1.092) but didn't change the shape.

### M1 Sim Eval (v2, best weights R2 val_loss=0.9984)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1087  lcb=0.1071  meanWinRate=0
avgKills=3.04  avgTicks=4207
```

**All 350 games ended in gameover.** 0% win rate — same as v1 despite val_loss
improving 8.4% (1.0919 → 0.9984).

### v1 vs v2 comparison

| Metric | v1 (no scalars) | v2 (scalar fusion) | Δ |
|--------|-----------------|--------------------|----|
| val_loss | 1.0919 | **0.9984** | -8.4% ↓ |
| Win rate | 0.0% | 0.0% | — |
| Avg kills | 2.6 | **3.04** | +17% ↑ |
| Avg ticks | 4755 | 4207 | -11% |
| Score V7 | 0.1085 | 0.1087 | +0.2% |

**Key finding**: Scalar fusion improved learning (val_loss ↓, kills ↑) but didn't
improve winning. The model kills 17% more enemies but still can't survive to
clear a stage.

### Per-stage highlights

| Stage | avgKills | Notes |
|-------|----------|-------|
| Ramparts | 8.0 | Highest kills — still 0% win |
| Waterways | 6.5 | — |
| Eagle Nest | 6.4 | — |
| Checkers | **0.0** | Complete paralysis — 0 kills in all 10 games |
| Iron Curtain | 1.1 | — |
| Gauntlet | 1.3 | Worst score V7 (0.089) |

### Why scalar fusion didn't help winning

The model can now "see" lives, base distance, enemy distribution, etc. But it
still can't *act on* this information effectively. Root causes:

1. **BC distribution shift still dominates**
   - Scalar fusion reduces the information gap but doesn't fix the fundamental
     problem: once the NN's trajectory diverges from the human's, it can't recover
   - The model needs to be *robust* to its own mistakes, not just accurate on the
     first few decisions

2. **7×7 receptive field can't capture global strategy**
   - 3 layers of 3×3 conv → 7×7 receptive field on a 26×26 board
   - Model can't reason about "enemies are coming from the north, base is south"
   - Scalars give relative positions but the spatial backbone can't plan paths

3. **Checkers stage = complete failure mode**
   - 0 kills in 10 games — the model literally cannot move or shoot
   - Suggests the model has learned a brittle policy that collapses on certain
     terrain layouts

### Lessons learned (v2 additions)

7. **Scalar fusion is necessary but not sufficient** — the model needs scalars to
   make context-aware decisions, but scalars alone don't solve distribution shift
8. **val_loss continues to be a poor game-performance proxy** — 8.4% improvement
   with zero win-rate improvement
9. **Receptive field is the next bottleneck** — model can see the data but can't
   reason about spatial relationships beyond 7×7
10. **BC has a fundamental ceiling on hard difficulty** — the model needs to be
    robust to its own mistakes, which BC doesn't train for

### Status (2026-08-19)

**v2 scalar fusion: 0% win rate on hard. BC approach has reached its ceiling.**

Next options:
- Train on classic difficulty (easier → model can learn complete strategies)
- Switch to RL (reinforcement learning) — train with win/loss signals
- Increase model capacity (deeper conv, attention mechanism)

---

## §0 v1: Conv-Only Baseline (2026-08-18 → 2026-08-19)

### Architecture

```python
# nn-training/model.py v1
class NNPolicy(nn.Module):
    # Conv backbone: 14ch → 32 → 48 → 64, 3×3 kernels
    # GAP → FC(64→64) → ReLU → 3 heads (move/fire/item)
    # scalars parameter: ACCEPTED but IGNORED in forward()
    def forward(self, obs, scalars):
        x = obs.float()
        x = self.conv(x)           # (B, 64, 26, 26)
        x = self.gap(x)            # (B, 64, 1, 1)
        x = x.flatten(1)           # (B, 64)
        h = self.fc_relu(self.fc(x))  # (B, 64)  ← scalars NOT used
        return self.move_head(h), self.fire_head(h), self.item_head(h)
```

**Fatal flaw**: `scalars` parameter accepted but never concatenated into the FC input.
The model had no access to: lives, base distance, enemy distance, fire cooldown, ring integrity, inventory, etc.

### Training timeline

| Phase | Dates | Samples | Rounds | Best val_loss | Notes |
|-------|-------|---------|--------|---------------|-------|
| Initial baseline | 8/18 17:00 | 43,566 | 1×40ep | 1.2431 | First training run |
| Continuous 40ep | 8/18 21:08–23:30 | 43,566 | 3×40ep | **1.1320** | val_loss rebounded after R2 |
| Corpus expansion | 8/19 07:46 | 68,571 | 21×1ep | 1.4083 | From scratch after venv rebuild |
| Continuous 40ep | 8/19 09:54–15:07 | 68,571 | 9×40ep | **1.0919** (R2) | val_loss rebounded from R3 onward |

### val_loss trend (68K samples, v1)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +9.7% | Starting point |
| **R2** | **1.0919** | — | 🏆 Best |
| R3 | 1.0974 | +0.5% | Plateau |
| R4 | 1.1192 | +2.5% | Overfitting begins |
| R5 | 1.1422 | +4.6% | — |
| R6 | 1.1499 | +5.3% | — |
| R7 | 1.1639 | +6.6% | — |
| R8 | 1.1601 | +6.2% | — |
| R9 | 1.1625 | +6.5% | — |

**Pattern**: val_loss bottoms at R2, then monotonically increases — textbook overfitting.

### M1 Sim Eval (v1, best weights R2 val_loss=1.0919)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1085  lcb=0.1069  meanWinRate=0
avgKills=2.5  avgTicks=4234
```

**All 350 games ended in gameover.** 0% win rate — same as the initial baseline
despite val_loss improving 12% (1.2431 → 1.0919).

### Per-stage highlights

| Stage | avgKills | progress | baseIntegrity | mobility | accuracy |
|-------|----------|----------|---------------|----------|----------|
| Waterways (best) | 5.1 | 0.255 | 0.244 | 0.169 | 0.300 |
| Lattice (worst) | 0.6 | 0.030 | 0.072 | 0.085 | — |
| Ramparts | 4.0 | 0.200 | 0.182 | 0.484 | — |
| Steel Fortress | 2.5 | 0.125 | 0.000 | 0.314 | 0.552 |

**Key observations**:
- `baseIntegrity` ≈ 0 on most stages → base always destroyed
- `progress` ≤ 0.255 → kills at most 25% of enemies
- `mobility` ≤ 0.48 → limited map exploration
- No correlation between avgKills and score — killing more doesn't help if you can't protect the base

### Corpus analysis

**94.2% of training replays are wins** (98/104 cleared all enemies).
Only 6 losses in the corpus (partial clears on Bunker Hill, Labyrinth, Brick Maze, Spider).

This means the NN was trained primarily on winning trajectories but couldn't reproduce them in sim.

### Root cause analysis

#### Why val_loss ↓12% but win rate = 0%

1. **BC loss measures imitation accuracy, not winning ability**
   - val_loss = cross-entropy between NN predictions and human actions
   - A model that perfectly mimics a winning trajectory should win — unless it can't
     maintain the trajectory under distribution shift

2. **Distribution shift (the real killer)**
   - Training: given obs_t, predict action_t (ground truth from human replay)
   - Inference: NN's action_0 may match human, but action_1 diverges slightly →
     obs_1 diverges → action_2 diverges more → ... → cascade failure
   - Even 94% winning training data can't prevent this if the NN lacks the information
     needed to make the same decisions as the human

3. **Missing scalar inputs = missing decision context**
   - Human player decides "retreat to base" based on knowing: "I have 1 life left,
     base ring is damaged, enemy is approaching from the north"
   - NN only sees the 14-channel spatial snapshot — it can't distinguish "aggressive
     push" from "desperate retreat" without scalar context
   - The 24 scalar features (lives, base distance, fire cooldown, enemy count, etc.)
     were available in the encoding but never fed to the model

4. **Model capacity bottleneck**
   - 50K params for 68K samples — near the capacity boundary
   - GAP compresses 26×26 spatial info to 64 dims — heavy information loss
   - 3×3 convs have 7×7 receptive field — can't capture long-range spatial relationships

#### Why move_acc improved but didn't help

- move_acc 0.586 → 0.709 over 10 rounds
- But accuracy is measured against **human actions**, not **optimal actions**
- The human's movement in winning replays is context-dependent — "go left" is only
  correct when you know the base is to the right and enemies are above
- Without scalar context, the NN learns a statistical average of directions, not
  a context-aware policy

### Lessons learned

1. **Never ignore available inputs** — if scalars are encoded, they must be consumed
2. **val_loss is a poor proxy for game performance** — always validate with sim eval
3. **BC requires the model to see everything the human sees** — otherwise distribution
   shift makes inference unreliable
4. **Warm-starting conv weights is effective** — v2 epoch 1 val_loss (1.38) already
   below v1 from-scratch start (1.91)
5. **94% winning corpus ≠ easy BC** — distribution shift dominates even with clean data
6. **Architecture changes require `load_state_into` tolerance** — shape mismatches
   should be caught and handled gracefully, not crash the training loop

---

## §-1 Pre-history (before 2026-08-18)

Training infrastructure established:
- `nn-training/train_loop.py` — continuous training loop with auto-resume
- `nn-training/train_bc.py` — behavior cloning trainer
- `nn-training/start-training.sh` — launch script with VBS detach on Windows
- `tools/replay/export-observations.ts` — NDJSON → npy shard exporter
- `src/nn/infer.ts` — TS runtime inference
- `src/nn/policy-input.ts` — NNInput InputLike implementation
- `src/nn/obs-encoder.ts` — 14-channel spatial + 24-dim scalar encoder
