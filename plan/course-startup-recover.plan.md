# Plan: course-startup-recover — 课程起不来要能看见，且改好就能自动生效

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **路径基准**：全文除注明外，python 侧相对 `nn-training/`，TS 侧相对 `dashboard/`。
> **状态**：**草稿（2026-10-05）**。
> **触发**：用户报障「`x20-adv3-open-r2` 开启训练后一直卡着不动」——排查结论是**课程根本没起来**，
> trainer 在启动期把它整条跳过，而控制台**一个字都没说**，人只能对着不动的界面干等。
> **追加触发**（用户裁决 2026-10-05）：「课程改好后，**不应该要求重启 trainer**」——
> 原方案要求人工重启 trainer 才生效，用户判为不可接受。⇒ 本 plan 扩为**两个交付**：
> **P0 改好能自动生效**（不改重启语义）+ **P1 起不来要报警**（不改训练行为）。
> **阅读顺序**：§1 现状（五环证据）→ §2 目标/非目标 → §3 语义规格 → §4 落点 → §5 实施步骤与用例
> → §6 效果与诚实账 → §7 DoD/门禁 → §8 待裁决项 → §9 评审处置台账。
> 行号只作定位加速，**判据看函数名**。

---

## 0. 一句话目标

**两件事，缺一不可**：

- **P0（治本）**：课程文件改好后，被跳过的课**自动重试并开跑**——不要求人重启 trainer
  （当前 `report.skipped` 是进程内终身黑名单 ⇒ 改文件也无效、只能重启整个进程）。
- **P1（可见）**：还没改好时，控制台**红条告警**并停止误报 `ready`——
  而不是一片看起来在正常等待的界面。

---

## 1. 现状：五环证据（判据为「结构上不可达」，不是缺配置）

| 环 | 事实 | 证据 |
|---|---|---|
| ① 故障**已发生且被响亮记录** | trainer 开课时逐课 `try/except`，把 `SystemExit` 原文写进日志 + `ServeReport.skipped` | `trainer/loop_serve.py:634-640`（`_open_courses`）、`:95`（`skipped` 字段） |
| ② 真实事故原文 | `kickstart_init=0.1 但 kickstart_ref 未开 —— 缰绳没开，初值无消费方` ⇒ 整课跳过，`training_log.jsonl` **0 字节** | `tmp/trainer-cluster.log:1312959`（07:47:13）与 `:1313314`（07:51:17 复现一次） |
| ③ **`skipped` 没有出口** | 控制台的 `--json` 契约面走**只读计划视图**分支（`build_rows`），它按 `enabled_courses` 重算每课行，**从不调用 `open_course`、从不碰 `skipped`**；只有 `--serve` 分支才导出 `skipped`（那是训练结束后的 stdout，人看不到） | `trainer/run_rl_cluster.py:315-318`（只读分支，无 `skipped`）vs `:270-282`（`--serve` 分支才有） |
| ④ 更糟：只读视图**把它报成健康的** | 修复前实测 `--json` 对这门课输出 `state=ready`、`current=precollect_join`、`waiting.kind=ready`、「无外部等待，下一步 precollect_join」、13 步待办齐全 —— 一门永远跑不起来的课被报成"随时可以推进" | 实测命令见 §6.1；`trainer/loop_plan.py:295-314`（`waiting_state` 无「配置不可运行」这一态） |
| ⑤ **★ 改好文件也不生效（本 plan 的 P0，用户裁决）** | 每次空转拍重扫发现新课时，**显式过滤掉 `report.skipped` 里的课** ⇒ 被跳过的课**终身不再重试**（`report` 只在进程内存里活到进程退出）。实测：08:06:35 停→开（标记 mtime 已更新），trainer 日志**一行新记录都没有** | `trainer/loop_serve.py:850-852`（`fresh = [c for c in fresh if c not in report.skipped]`，注释「课程文件缺失 = 这一轮修不好；避免每秒刷日志」）；实测 trainer PID 9756 仍是 07:51:11 那个，07:51:17 后无新行 |

**⑤ 是本事故里最贵的一环**：前三环让人**看不见**，⑤ 让人**修好了也不管用**——
它把一次 5 秒的配置修复变成了一次「必须重启共享 trainer」的操作，
而重启 trainer 会**连带打断所有并行课程**（本次 `x20-adv3-kind` 正在 it35，见 §6.4）。
用户裁决：「课程改好后，不应该要求重启 trainer」。

**⑤ 的注释理由为什么站不住**：注释说「课程文件缺失 = 这一轮修不好」——但今天被跳过的**绝大多数**
不是「文件缺失」，而是「文件存在但配置自相矛盾」**恰恰就是等着人去改**的那一类。
把两种故障混进同一个永不重试的黑名单，是这条设计真正的病灶。

**既有先例（同一形状，判据可直接复用）**：`reopened_parked`（`loop_serve.py:593-618`）
处理「课收官后被用户停→开重开」——判据是**开课标记 mtime 变新**。
被跳过的课要走的是**同一条思路**，只是比较对象换成**课程文件**本身（见 §3.4）。

**旁证（同一形状的前科，同源但不在本 plan 范围）**：
- `docs/nn/training-stack.md` §1.1 记过一次「pill 报『推进中』而真 trainer 已收官」——同一个洞的
  「读面拿不到预算」变体，根因都是**判据域不覆盖某类真事实**。
- 现有七类告警（收官 / PPO 排队超时 / 离线静默停摆 / 编辑被拒 / 停机 / 只读…）**没有一条**
  覆盖「开课了但起不来」——`alerts.ts` 逐条核对过（§4.3）。

---

## 2. 目标与非目标

### 2.1 目标

- **G0（P0，治本）改好即生效**：课程文件**发生变化后**，被跳过的课自动重试开跑，
  **不要求重启 trainer**。判据 = **课程文件身份变了**（复用 `_marker_mtime_ns` 同款形状）。
- **G1（P1）判据单点在训练侧**：新增「这门课能不能被打开」的只读判据（复用**同一个** 校验链，
  不另写一份），由只读 `--json` 分支一并导出。
- **G2（P1）控制台红条**：接进既有告警坞（`severity: 'err'`），按既有规则
  「有恢复动作的瞬时态按课过滤 / 无动作的终态全局成列」归入**按课过滤**。
- **G3（P1）不再误报 ready**：起不来的课**不得**同时显示 `waiting.kind=ready` 或「无外部等待」。

### 2.2 非目标（明确排除，防范围膨胀）

- **不修课程文件本身**：`x20-adv3-open-r2.jsonc` 的 `kickstart_init` 矛盾已在本次报障中单独修掉
  （整键删除，有测试背书）；本 plan 只管**起不来/修好了**这两件事本身。
- **不改任何训练行为**（P1 部分）：不碰 `open_course` 的校验规则、不碰 `waiting_state` 既有四态的语义、
  不碰 serve 的单课故障隔离（故障域 = 单课是既定设计）。
- **不改 restart-only 字段的分类**：`kickstart_init` / `kickstart_ref` 等仍是 restart-only
  （`biz/hot_reload.py:40-66`）——那是**给已在跑的课**的语义（衰减曲线不许中途改口径）。
  **P0 只管「还没开成的课」**：它压根没开跑，不存在"中途换判读口径"的问题，两件事正交。
- **不做 trainer 自愈重试「没被人改过」的课**：没有人的新信号就不重试（课程文件或开课标记至少一个变了才重试，§8.4；否则每秒刷日志，
  是 `:851` 注释的**原意**，只是它把判据从"文件没变"错写成了"跳过一次就永不再试"）。
- **不动 hub / 云机侧的故障面**（PPO 无人取、离线停摆已有各自告警）。

---

## 3. 语义规格

### 3.1 新判据：`openable`（训练侧，纯只读）

- **形状**：`{course: str, ok: bool, reason: str}`，`reason` 为空串表示可运行。
- **判据来源**：**必须是 `open_course` 走的那条校验链**，禁止重写。按 §4.1 的方式复用。
- **诚实性**（沿用 `waiting_state` 的既有纪律）：校验链**读不到**课程文件（文件不存在/不可解析）
  时，`ok=false` + reason 如实写「读不到课程文件」——**不得**因为"读不到就假定没事"而放行
  （`waiting_state` 的 `already_done` 同款规矩：算不出来的事实不得当成完成）。
- **零副作用**：不建锁、不写账本、不推权重、不碰 `runtimes`。

### 3.2 与 `waiting` 的合流（**G3 的落点**）

起不来的课**不得**同时说「在等第一步」。取值域处置：

| 情形 | `waiting.kind` | `waiting.text` |
|---|---|---|
| `openable.ok=false` | **新增 `blocked`** | 「课程起不来：<reason 原文>」 |
| `openable.ok=true` | 既有四态不变 | 既有文案不变 |

**为什么加 `blocked` 而不是复用 `idle`**：`idle` 今天说的是「本轮无待办（账本已结算 / 未开训）」——
那是**正常的空转稳态**（门禁停机时的正确读数）。把「配置不可运行」塞进 `idle` 会让停机中的课
和起不来的课长得一样，正是本次事故"分不清"的翻版。**新增一格比复用一格便宜。**

【评审补 2026-10-05】**优先级先定死**（F5）：`finished`（跑满）→ `inflight` → `collect` → `blocked` → `idle` → `ready`；`blocked` 只在 `pending>0` 时报——无待办的课没有「起不来」的待办语义，跑满的课也不因课程文件被改坏而改口。

⚠ 取值域是**契约面**：`dashboard/src/web/view/loop-queue.ts:475` 的 `WAIT_KINDS` 与
`nn-training/tests/trainer/test_loop_plan_waiting.py` 钉住取值域，**两边必须同一次改动里对齐**
（`run_rl_cluster.py` 文件头已写明这条规矩）。

### 3.3 告警条目（`alerts.ts` 新增第 8 类）

- **严重度**：`err`（红）。它不是提示，是「你开的课没在跑」。
- **标题**：`<课> 开课了但起不来：<reason 首句>`（结论先行，第一行就能决策）。
- **detail**：reason 全文 + 定位指针（**课程文件路径，不带行号**——行号会随编辑漂移，§8.3）+ **明确的下一步**：
  「这是课程文件配置问题——配置不可开课（trainer 在跑时会整课跳过；正在跑的课不受影响）。
  **改好课程文件后会自动重试开跑，不需要重启 trainer**（P0 落地后；未落地时需重启）」。
- **动作**：`ack`（「知道了」）+ **不提供** `resume`。
  **为什么不给一键恢复**：恢复动作是「改课程文件」——那是**代码编辑**，控制台没有、也不该有
  替人改课程文件的按钮（§2.2 不做自愈）。但**必须**给出"改完怎么生效"的指引，
  否则用户改完文件发现课还是不动，会以为这条告警在说谎。
- **可见范围**：**按课过滤**（沿用 `plan/dashboard-banner-global` §4.2 的规则：有恢复动作的瞬时态
  按课过滤）。即：看当前课时弹，切到别的课不弹。**理由**：它有恢复动作且**会被自己修好的配置解除**
  （不是终态），与停机横幅同族。
- **ack 键**：`alertAckKey('course-startup', course, <reason>)` ——
  事件身份取 reason：**改了配置后 reason 变 ⇒ 新事件 ⇒ 会重新弹**（这是我们要的：
  "改了但没修好"必须再看见一次）。**不得**用 `mtime` 或纯课名（那会让"改了但没修好"静默）。
- **自动消失**：P0 复活成功后条目**自动消失**（不靠人点「知道了」）——见 §7 的 P1 DoD。

### 3.4 【P0】跳过课的复活判据：`reopenable_skipped`（**本 plan 的治本部分**）

**问题**：`report.skipped` 是**只增不减**的黑名单（`:852` 每拍都把它滤掉），
而它里面装的**绝大多数不是"文件缺失"，而是"文件在、配置错"**——恰恰是**等人去改**的那一类。

**设计**：把「跳过」从**终身黑名单**改成**带指纹的待重试表**。

| 形状 | 说明 |
|---|---|
| 记账 | `report.skipped[course]` 之外，另记 `skipped_at[course] = 课程文件身份`（`mtime_ns` + `size`，与 `_marker_mtime_ns` 同款取法；**不**用 `course_fp` 全量哈希——每拍算 sha 太贵，mtime+size 足够判"人动过文件"）。 |
| 复活判据 | 每拍扫描时，对 `report.skipped` 里的课：**课程文件身份 ≠ 跳过时记的值** ⇒ 撤销 `skipped` 条目并重新 `_open_courses`。 |
| 身份相同 | 保持跳过、**不重试、不刷日志**（这是 `:851` 注释的**原意**，必须保住）。 |
| 文件消失 | 跳过时身份记为 `None`；后来文件**出现**（身份 `None → 有值`）也算"变了" ⇒ 复活。 |
| 复活成功 | 记一行响亮日志（`[serve] 课程 X 配置已修好——重新开课`），**并**让告警坞那条红条**自动消失**（§3.3）。 |
| 复活失败 | 重新记 `skipped` + **刷新身份** ⇒ 下次只在文件**再变**时才重试（不刷日志）。 |

**为什么用「文件变了」而不是「停→开」**：用户 08:06 的操作正是"停→开"，而它无效——
`reopened_parked` 只处理**已收官**的课（`state in (done, aborted)`），
被跳过的课**从不在 `sup.courses` 里**（`_open_courses` 失败就不进 `runtimes`）⇒ 走不到那条通道。
而"人改了配置文件"这个事实**本身**就是最强的复活信号，比任何控制台按钮都可靠。

**为什么不用「定时无条件重试」**：那是每秒重跑一次 pydantic 校验 + 可能建 runtime，
只为等一个人可能永远不会来的修改——`:851` 注释反对的正是这个。**指纹判据两者都满足**。

【评审补 2026-10-05】**跳过来源分类——复活触发按类取，不能只认课程文件**（F1/F2）：

| 跳过来源 | 现状落点 | 复活触发 |
|---|---|---|
| `_open_courses` 配置错（课程文件） | `report.skipped`，不在 `runtimes` | 课程文件身份变（本节判据） |
| `_open_courses` 锁被占 | 同上 | 锁空闲即重试（失败快、自愈类；等文件变是错的） |
| `_enqueue_opened` 入队失败 | `report.skipped` + 已从 `runtimes` 摘除 | 账本/轨迹盘事实变化（或并入文件指纹 + 有界节流重试）；**必须同时记身份**，否则判据未定义 |
| **一步级 SystemExit**（`build_executor`） | `skipped` + `failures`，**课仍在 `runtimes`、队列 ABORTED** | **第二条通道**：reopened_parked 同款「重置队列 + `_enqueue` + 保留 runtime/引擎」——**不得**重建 runtime（C-0 前科：新 runtime runner=None + 池里旧引擎 ⇒ 无限 RETRY） |
| rl-config / env / 权重类 | `report.skipped` | 本 plan 未覆盖（§6.5 清单） |

### 3.5 【P0】与 restart-only 的正交性（**别把这俩混为一谈**）

`kickstart_init` / `kickstart_ref` 在 `RESTART_ONLY_FIELDS`（`biz/hot_reload.py:46-49`）里，
这是**给已在跑的课**的规矩：衰减曲线 `kk(it)=init×decay^(it-1)` 一旦排好，中途改 init
= 中途换判读口径 ⇒ 必须重启。

**但本条腿压根没开跑**——它没有"已排好的曲线"，也没有"已产生的轮次"。
所以 P0 **不动** `RESTART_ONLY_FIELDS`、**不动** `plan_reload` 的分类学，
只补**「没开成的课」的重试通道**。两者是不同问题、不同代码路径，正交。

> ⚠ 若将来有人把 P0 理解成"让 `kickstart_init` 变成 hot 字段"⇒ **那是错的**，会把实验口径改坏。
> 本 plan 的 `DECISIONS` 条目必须写明这条边界。

---

## 4. 落点

### 4.0 【P0】训练侧：复活通道（**先做这一条，它独立解决用户报障**）

| 文件 | 改什么 |
|---|---|
| `nn-training/trainer/loop_serve.py` | ① `ServeReport` 加 `skipped_at: dict[str, tuple[int,int] \| None]`（或等价结构）；② 新增 `_course_file_identity(course) -> tuple[int,int] \| None`（**与 `_marker_mtime_ns` 同款形状**，紧邻摆放）；③ `_open_courses` 失败时记身份；④ 每拍扫描处把 `:852` 的「滤掉 skipped」换成「滤掉 skipped **且身份未变**」，身份变了的先撤销 `skipped` 再重新 `_open_courses`；⑤ 复活成功记响亮日志。 |

**实现约束**：
- **纯函数优先**：复活判据抽成纯函数（如 `reopenable_skipped(states, seen_marker, ...)`），
  与 `reopened_parked` 同款——便于单测，不碰墙钟（§7）。
- **不许每拍算 sha**（`course_fp`）——mtime+size 足够，成本 O(1)。
- **不许引入新的重试节拍**——复用既有 `poll_sec` 空转拍（发现模式本来就在每拍重扫）。

### 4.1 训练侧（判据单点）

| 文件 | 改什么 |
|---|---|
| `nn-training/trainer/loop_plan.py` | 新增 `course_openable(course, traj) -> tuple[bool, str]`：**复用校验链**。`waiting_state` 增 `WAIT_BLOCKED` 分支（§3.2）。 |
| `nn-training/trainer/run_rl_cluster.py` | `build_rows` 每行加 `openable` 字段；`waiting_state` 调用处传入。**不新增 `--json` 顶层 `skipped` 键**——顶层 `skipped` 是 `--serve` 分支的，两分支形状不同是既有事实；`blocked` 走 per-row 与既有 `waiting` 一致。 |

**复用而非重写（关键实现约束）**：`open_course`（`loop_serve.py`）是真训练入口，**不能**为只读视图
调用它（它建 runtime、写账本、可能推权重）。因此按 §6.2 的探针结论选择**校验链的最窄可复用段**：
若 `apply_course` 的自检（`worker/config.py:361-368` 那类 `SystemExit` 族）可在**纯 args + CourseConfig**
的条件下独立求值，则只读侧复用它；**不许**复制一份 `if _ki > 0 and not ref: raise` 的判断。
若实测发现校验链与 runtime 构造不可分，则退化为「`open_course` 失败即 `ok=false`」的**有界试跑**，
并在 §9 台账里登记该退化（**判据仍单点**，只是调用方式不同）。

【评审补 2026-10-05】**依赖方向先解，再谈复用**（F3）：`course_args` 住 `trainer/loop_serve.py`，而 `loop_serve` 顶层就 import `loop_plan`——`loop_plan.course_openable` 直接复用 = 顶层循环 import（炸）/ 函数内每次拉起 `loop_serve`（只读 `--json` 每 10s 一个新进程，正是 run_rl_cluster 惰性 import 要避免的导入代价），且反向依赖倒挂。实现前把校验链核心抽到中立模块（`trainer/course_spec.py` 或 `worker/`），`course_args` 与 `course_openable` 同源 import；并给只读 `--json` 补一条守卫（import 链不含 torch / 冷算耗时不回归）。§6.2 探针只证了 `apply_course` 一段——`course_args` 还含 `resolve_mode`/`merged_mode_args`/`build_argparser`/`course_cli_conflicts`/`validate_args`，逐段确认不豁免。两条覆盖边界写死：① `course_args` 随 serve 级 `--mode` 变，只读端拿不到 mode ⇒ mode 相关校验不在覆盖面；② BC 走 `_open_bc_course` 另一条链（含传输解析），P1 覆盖到哪要明说。

### 4.2 控制台读面

| 文件 | 改什么 |
|---|---|
| `dashboard/src/web/view/loop-queue.ts` | `WAIT_KINDS` 加 `'blocked'`；`LoopQueueRow` 加 `openable: {ok, reason}`；`parseLoopQueue` 解析（**逐字段容错**：python 比控制台旧时缺 `openable` ⇒ 退化成 `{ok:true,''}`，**不得**让旧 python 把控制台打崩——沿用本文件既有的容错风格）。 |
| 同文件的 pill / 行渲染 | `blocked` 态按 err 上色（不进 `ready` 的绿）。**这是 G3 在 UI 侧的兑现**。 |
| 【评审补】`dashboard/src/web/view/interaction.ts` | `AlertAckKind` 联合加 `'course-startup'`——否则 `alertAckKey('course-startup', …)` 不过 typecheck（原落点漏了本文件，F7）。 |
| 【评审补】同文件其余读数点（`loop-queue.ts`） | `WAIT_TITLES` 是 `Record<LoopWaitKind, string>` ⇒ 加 `'blocked'` 必须补 title（编译器会拦）；`waitCls('blocked')` 的色档、`course-matrix.ts`（waiting.text/title/上色消费方）、`server-api-loop-queue.test.ts`（旧 python / 未知 kind 退化）同链一并过。 |

### 4.3 告警坞

| 文件 | 改什么 |
|---|---|
| `dashboard/src/web/view/alerts.ts` | 新增 `courseStartupAlerts(input)`（第 8 类）并入 `buildAlerts`；`AlertInput` 按 §6.2 探针结论取数（`loopQueue.rows` 很可能已到视图层 ⇒ 接线零改动或一行）。 |
| `dashboard/src/web/app/…` | 若探针显示未接线才动。 |

---

## 5. 实施步骤与用例

按 §7 的门禁纪律，每步都要有**确定性**用例（固定 seed、无 `Math.random()`/墙钟）。

**建议顺序：P0 先做**（它独立解决用户报障，且不改控制台），P1 再做。

### 步骤 0【P0，先做】：复活通道

- **失败用例先行**（`nn-training/tests/trainer/test_serve_wiring.py` 或新建
  `test_serve_skipped_retry.py`）——**先在未改代码上确认红**：
  - 「课因配置错被跳过 ⇒ 文件身份**变了** ⇒ 下一拍被重新开课」（今天：永不再试 ⇒ 红）；
  - 「文件身份**没变** ⇒ 仍跳过、且**不重复调用** `open_course`」（防刷日志，红/绿皆须钉）；
  - 「跳过时文件不存在（身份 `None`）⇒ 后来文件出现 ⇒ 复活」（红）。
- 复活判据必须是**纯函数**（同 `reopened_parked` 的测法：喂 dict，不起进程）。
- **这三条用例就是本次事故的回归防线**——它们会锁死「改文件必须重启 trainer」这个回归。

### 步骤 1【P1】：训练侧判据 + 复现用例（先红后绿，§7）

- **失败用例先行**（钉住本事故的形状）：
  `nn-training/tests/trainer/test_loop_plan_waiting.py` 加一例——
  「课程配置自相矛盾（`kickstart_init>0` ∧ `kickstart_ref=false`）⇒
  `waiting.kind=='blocked'` ∧ reason 含 `kickstart_ref` ∧ **不得**是 `ready`」。
  **先在未改代码上确认红**（今天返回 `ready`，正是本事故）。
- 同族加一例「配置正常 ⇒ 既有四态逐字不变」（防回归）。

### 步骤 2：`--json` 契约面 + 控制台解析

- python：`build_rows` 加 `openable`；一条断言 `--json` 输出含该字段的用例
  （`tests/trainer/` 下，形状照既有 `--json` 契约面用例）。
- TS：`parseLoopQueue` 解析 + 容错用例：
  **旧 python 缺字段 ⇒ 不抛、`blocked` 不出现**（`dashboard/tests/web-loop-queue.test.ts`）。

### 步骤 3：告警条目

- `dashboard/tests/web-alert-dock.test.ts` 加：起不来的课 ⇒ 恰好一条 `err` 条目；
  `ok=true` ⇒ **零条**；ack 后消失；**改 reason 后重新弹**（§3.3 的 ack 身份）；
  切到别的课 ⇒ 不弹（按课过滤）。
- `web-app-coursematrix` 类用例：条目真的进了坞（不是只测纯函数）。

### 步骤 4：文档与决策

- `DECISIONS.md` 记一条（§6.3 三问全中：未来会重犯 · 不能就近表达）。
- `docs/nn/console.md` 顶部加 `## §<max+1>`：告警坞第 8 类。
- `docs/nn/training-stack.md`：`waiting_state` 取值域加 `blocked`（**两处必须一起**，是契约面）。

---

## 6. 效果与诚实账

### 6.1 事故复现（改代码前，已实测）

```
$ python nn-training/trainer/run_rl_cluster.py --traj-root tmp --courses x20-adv3-open-r2 --json
{"courses":[{"course":"x20-adv3-open-r2","state":"ready","current":"precollect_join",
  "pending":[13 步],"waiting":{"kind":"ready","text":"无外部等待，下一步 precollect_join"}}],
 "pools":{...}}
```

⇒ 一门**永远跑不起来**的课被报成「无外部等待、下一步 precollect_join」。这就是人"看着卡住"的机制。

### 6.2 探针（实现前必须先跑，§4.1 的选型依据）

实现前跑两个探针并把结论写回本 plan：
1. `apply_course` 的自检链能否在**纯 args + CourseConfig** 下独立求值（不建 runtime）？
   （本次已实测**可以**：`apply_course(args, load_course("x20-adv3-open-r2"))` 直接求值成功，
   只碰 args 与课程对象——但**只覆盖 `apply_course` 段**，`open_course` 还有别的段，
   实现时仍须逐段确认。）
2. `alerts.ts` 的 `AlertInput` 现有取数路径里，`loopQueue.rows` 是否已经传到视图层？

### 6.3 【P0】修复前后的实测对照（用户裁决的依据）

| 时刻 | 动作 | 结果 |
|---|---|---|
| 07:47:13 | 开课 | 跳过（配置矛盾），日志有 reason |
| 07:51:17 | trainer 重启 | 又跳过一次（reason 同上） |
| **~08:00** | **修好课程文件（删 `kickstart_init`）** | **日志无任何变化** —— 文件改对了，进程不知道 |
| **08:06:35** | 控制台**停→开**（标记 mtime 已更新） | **日志仍无任何变化** —— 复活通道不存在 |
| 08:09 | 复查 | trainer PID 9756 仍是 **07:51:11** 那个；`training_log.jsonl` 仍 **0 字节** |

⇒ 「改文件 + 停→开」这两步人已经做全了，**都不管用**；唯一奏效的手段是重启共享 trainer，
而那会打断 `x20-adv3-kind`。这正是用户裁决「不应该要求重启 trainer」的实证。

### 6.4 重启 trainer 的连带代价（量化 P0 的价值）

本次实测：`x20-adv3-kind` 正在 **it35/80**（每轮约 2 分钟，it34 已落账）。
重启共享 trainer = 该课丢失当前轮进度 + **所有并行课程**一起中断。
P0 落地后，这一切的代价归零——**改一行课程文件即可，不必惊动任何在跑的课**。

### 6.5 诚实账（明确不声称什么）

- **【P0 已撤销旧限制】** 原方案「恢复需人重启 TrainingLoop」**作废**——用户裁决判为不可接受。
  P0 落地后：改文件 ⇒ 下一拍自动重试开跑。
- 【P0】**不保证修复瞬时可见**：复活发生在 trainer 的下一个**全局空转拍**（discovery 块只在所有课都无可跑任务时才走到，可能晚于 `poll_sec`；F11），
  不是改完文件立刻开跑。**不承诺零延迟**（也不需要——这比重启整个 trainer 快得多）。
- 【P0】**不覆盖「人改了 rl-config / 环境 / 权重文件」**：指纹只看**课程文件**。
  若故障根因在别处（例：bc 权重文件被删），改课程文件不会触发复活 —— 那是另一类问题，
  诚实记为**未覆盖**。
- 【P1】本 plan **不**让 trainer 改动任何训练行为；告警修好后，恢复仍走 P0 通道或人工。
- 【P1】**假阳性边界（评审补 F4）**：`openable` 说的是「**配置不可开课**」，不是「serve 此刻跳过了它」——正在跑的课把课程文件改成非法（如把 `kickstart_init` 加回来）也会红（restart-only 改动 mid-run 不生效，课照跑）；trainer 没起时也会红（没人跳过它）。控制台现有 `training` 判据区分不了（被跳过的课 `state=ready` ∧ schedulerAlive ⇒ `training=true`）。故文案不得写死 serve 侧事实；要写死「已跳过」，须引入 serve 侧真实跳过面（如 serve 落一份 skipped 状态文件，控制台读它——比 per-row 重新校验更贴近「判据单点」，代价是新增产物）。
- 【P0】**未覆盖清单补全（评审补 F2）**：除 rl-config / env / 权重外，还含——锁被占（等锁释放即自愈，但文件没变 ⇒ 不重试）· `_enqueue_opened` 入队失败（修复对象是账本/轨迹盘）· serve 级 `--mode` 相关校验 · BC 解析链（若 P1 未覆盖）。正确形状 = 跳过时记「原因类别」、复活触发按类取；只认课程文件指纹会把这四类继续留给「重启」。
- 本 plan **不**覆盖 hub/云机侧的启动失败（那类故障的告警已有 PPO 排队超时那条）。
- 本 plan **不**把 `waiting.kind` 的既有四态改名或重排（契约面，只增不改）。
- 本 plan **不**保证能覆盖**所有**起不来的原因：只覆盖走 `open_course` 校验链的。
  「校验链之外、但引擎构造期才炸」的情形归 §8.1。

---

## 7. DoD / 门禁

### P0（复活通道）DoD

- [x] **本次事故的回归用例三条全绿**（§5 步骤 0），且**先红后绿已验证**。
- [x] **反向探针**：把文件身份判据改成恒等（永远"没变"）⇒ **P0 必须失效**
      （守卫不是瞎的；否则这条能力是"看起来有"）。
- [x] **不刷日志**：身份未变时，`open_course` 在 N 拍内**只被调用一次**（用例钉住调用次数）。
- [x] **不影响在跑的课（确定性用例，不靠 live 现场）**：双课 serve（假引擎：一门在跑、一门被跳过）⇒ 在跑那门的 `rounds_done`/指针不受影响、无新 ABORT
      （live 现场若 `x20-adv3-kind` 仍在跑只作补充证据：trainer pid 不变 ∧ it 单调不减——原判据「行数与末行 it 一致」不可判，F8）。
- [x] nn 门禁绿（只判 python 侧）。

### P1（告警）DoD

- [x] `bun run check` 绿（根项目）。
- [x] `cd dashboard && bun run typecheck && bun run test` 绿（动了 `dashboard/**`）。
- [x] nn 门禁绿：`bash tools/githook/nn-py-safe.sh -m pytest …`（**Windows 下须用 Git Bash**，
      仓内 WSL 无法执行 `.exe`；本次报障实测踩过）。
- [x] **三处取值域/契约同步改完**：`loop_plan.py`(python) · `loop-queue.ts`(TS) ·
      `test_loop_plan_waiting.py`(用例) —— 少一处就是契约分叉。
- [x] 反向探针：把 `openable.ok` 硬编码成 `true`，**告警必须消失**（守卫不是瞎的）。
- [x] 【评审补 F3】只读路径零回归：`--json` 冷算仍在既有预算内（控制台 10s TTL）且 import 链不含 torch——这是 §4.1 依赖方向解法的验收。
- [x] 【评审补 F7】`interaction.ts::AlertAckKind`（`'course-startup'`）与 `WAIT_TITLES`（`'blocked'`）编译期覆盖已过。
- [x] **P0 与 P1 联动**：P0 复活成功后，**告警自动消失**（不要求人点「知道了」）——
      否则会出现"课已经在跑了，红条还挂着"。
- [x] plan DoD（本文件）+ MVP DoD（`plan/mvp.md` §10）成立。

### 共同

- [x] **不动训练行为**：P1 是纯观测面；P0 只改"跳过课的重试通道"，不碰已在跑的课的任何步骤。

---

## 8. 待裁决项（不阻塞实现，实现时按默认走）

1. **是否同时覆盖 `--serve` 的 `failures`**（`ServeReport.failures`，一步级 SystemExit，
   2026-09-22 事故那族）？**默认：覆盖**。它与 `skipped` 同源同形（都是"课起不来"），
   两条告警分开报反而让人困惑——但它的读面同样缺失，一并补齐更省事。若评审认为应拆两条，再拆。
   **P0 侧同问**：步级失败后课程已下线，改课程文件是否也该复活它？**默认：是**——但**通道不同**：这类课仍在 `runtimes`、队列 ABORTED，走不了 `_open_courses`；按 §3.4【评审补】的第二条通道（重置队列 + `_enqueue` + 保留 runtime/引擎）实现。
2. **`blocked` 是否也要进 `CoursePill`**（顶部 pill 的色调）？
   **默认：进**（`tone='r'`）。pill 是"一眼扫完在训哪几门"的最小事实，起不来的课不该是灰/绿。
3. **告警文案是否要带课程文件的绝对路径**？**默认：带**（排障时人需要知道改哪个文件），
   但**不带**行号（会随编辑漂移，且 reason 原文通常已自带判据位置）。
4. **【P0】复活要不要也覆盖「停课→开课」这条既有通道的语义缺口**？
   即：被跳过的课，用户「停→开」后**是否也该立刻重试**（而不必等文件变）？
   **【评审补 2026-10-05】按 P0 必做项交付**（不再是可选默认）——用户 08:06 就是这么操作的，它是明确的「我想让它跑」信号。
   实现上只是把开课标记 mtime 也纳入复活判据（与课程文件指纹取并集）。

---

## 9. 评审处置台账

**评审（2026-10-05，agent 复核盘上代码/日志后填写）**：方向成立、证据链扎实——§1 五环逐条对过盘上事实（日志原文与行号、`:852` 终身黑名单、两分支 `--json` 形状、`waiting_state` 四态、告警七类、`WAIT_KINDS`）；事故时间线也对上（07:47 跳过 → 07:51 重启仍跳过 → **08:10:38 重启 serve 后**这门课才真正开课 ⇒「唯一有效手段是重启」有实证）。下列 F1–F13 为评审意见；F1–F8 属**实现前必须处置**，其中 F1/F2 直接改 P0 的复活形状。

| # | 评审意见（结论） | 处置 | 落点 |
|---|---|---|---|
| F1 | P0 复活只覆盖「开课阶段」跳过：步级失败（`build_executor` 同写 `skipped`+`failures`，课仍在 `runtimes`、队列 ABORTED）走不到 `_open_courses`（对已在 `runtimes` 的课直接 continue）；`reopened_parked` 又要求 `course in done`，而 `_settle_rounds` 只把 QUEUE_DONE 记进 done ⇒ ABORTED 课「停→开」也救不回。§8.1「覆盖 failures、同一套指纹判据」按现设计不可实现 | **采纳**：给该类新增第二条通道——reopened_parked 同款「重置队列 + `_enqueue` + 保留 runtime/引擎」（重建 runtime = C-0 无限 RETRY 前科） | §3.4【评审补】/ §8.1 |
| F2 | 指纹只认课程文件 ⇒ 三类继续永久卡死：锁被占（等锁释放即自愈）/ `_enqueue_opened` 入队失败（修复对象是账本）/ rl-config·env 类；且 `_enqueue_opened` 写 `skipped` 不记身份，判据未定义 | **采纳**：跳过时记「原因类别」，复活触发按类取；未覆盖类写进 §6.5 | §3.4 / §6.5【评审补】 |
| F3 | 判据复用有依赖倒挂：`course_args` 住 `loop_serve`，而 `loop_serve` 顶层 import `loop_plan` ⇒ 顶层复用 = 循环 import，函数内复用 = 只读 `--json` 每次拉起 `loop_serve`（正是 run_rl_cluster 惰性 import 要避免的代价）；探针只证了 `apply_course` 一段；`--mode`/BC 覆盖边界没写 | **采纳**：校验链核心抽中立模块（两边同源 import）+ 只读路径 import/耗时守卫 + 边界写死 | §4.1【评审补】/ §7 |
| F4 | `openable`=「配置不可开课」≠「serve 已跳过」：在跑的课把文件改坏会红（restart-only 改动 mid-run 不生效）、trainer 没起也会红；控制台 `training` 判据区分不了（被跳过的课 `state=ready` ∧ schedulerAlive ⇒ `training=true`） | **采纳（口径）**：告警文案不得写死 serve 侧事实；是否引入 serve 侧 skipped 状态文件列为实现选型 | §3.3 / §6.5【评审补】 |
| F5 | `blocked` 与 finished/inflight/collect 的优先级未定 | **采纳**：`finished > inflight > collect > blocked > idle > ready`；blocked 仅当 `pending>0` | §3.2【评审补】 |
| F6 | BC 课：P0 复活适用、`build_rows` 也出 BC 行，但 P1 规格只提 RL 链（`_open_bc_course` 未提） | **采纳（明说覆盖边界）** | §4.1【评审补】 |
| F7 | 落点漏 `interaction.ts::AlertAckKind`（不加 `'course-startup'` 不过 typecheck）；`WAIT_TITLES` 是 `Record<LoopWaitKind,string>`，加 `'blocked'` 必须补 title；course-matrix / server-api-loop-queue.test 在同读链上 | **采纳**：补落点行 | §4.2【评审补】 |
| F8 | P0 DoD「行数与末行 it 一致」不可判（课在推进时两者本来就变），且依赖可能已收官的 live 课 | **采纳**：换成双课确定性用例；live 只作补充证据 | §7【评审补】 |
| F9 | §3.3「带行号」与 §8.3「不带行号」矛盾 | 采纳：统一不带行号 | §3.3（已改） |
| F10 | §2.2「文件没动就不重试」与 §8.4「停→开也复活」措辞冲突 | 采纳：改为「没有人的新信号就不重试」 | §2.2（已改） |
| F11 | 「下一个空转拍（poll_sec 量级）」不准确：discovery 块只在**全局空转**时才走到 | 采纳：改口径 | §6.5（已改） |
| F12 | §8.4（停→开）是用户 08:06 实际做过的动作，属报障场景本体 | 采纳：升为 P0 必做项（不再是可选默认） | §8.4【评审补】 |
| F13 | ack 键用 reason 全文（长中文/含换行）做事件身份 | **接受不改**：语义正确（改了但没修好必须再弹）；若嫌键长可 `course|hash(reason)` 缩短，事件语义不变 | §3.3 |

> 证据核对（不改正文）：`tmp/trainer-cluster.log` 07:47:13 / 07:51:17 两行跳过原文与行号逐字相符；`x20-adv3-open-r2.jsonc` 已删 `kickstart_init`；`reopened_parked` / `:852` / `build_rows` / `WAIT_KINDS` / 七类告警均与盘上一致。

### 9.1 实现记录（2026-10-05，agent）

**交付**（按 §5 建议顺序：P0 → P1 → 控制台 → 文档；§4.0/§4.1/§4.2/§4.3 逐条落地）：

| 面 | 落点 | 回归 |
|---|---|---|
| P0 复活通道 | `trainer/loop_serve.py`：`ServeReport.skipped_at` + `_course_file_identity` / `_lock_signature` / `_skip_fingerprint` / `_record_skip` / `reopenable_skipped`（纯函数）+ `_revive_skipped` / `_revive_aborted`；跳过来源分类 config/lock/enqueue/step；`_enqueue_opened` 成功入队即清历史记账 | `tests/trainer/test_serve_skipped_retry.py` 9 例（判据三类 + 不刷日志 + 停→开 + 第二条通道 + 双课对照） |
| P1 判据单点 | `worker/course_args.py`（新：校验链整块迁入，serve 按旧名重导出——解 F3 依赖倒挂；**落 `worker/` 而非 `trainer/`**：分层门禁 `test_trainer_holds_only_orchestration_modules` 判定 `trainer/` 只许编排，且模块不得与 `biz/course_spec.py` 重名——`test_the_pure_logic_tree_is_gone_from_trainer`）+ `trainer/loop_plan.py`（`WAIT_BLOCKED` / `course_openable`，BC/ `--mode` 覆盖边界）+ `trainer/run_rl_cluster.py`（行内 `openable`） | `tests/trainer/test_course_openable.py` 6 例 + `test_loop_plan_waiting.py`（blocked 优先级/文案；既有两例隔离开课判据）+ `tests/worker/test_serve_course_overrides.py`（搬家后调用点不变） |
| 控制台 | `loop-queue.ts`（`blocked` 取值域 / `openable` 容错解析 / pill 红 / `WAIT_TITLES` / `waitCls`）+ `theme.css` + `alerts.ts`（第 8 类，按课过滤，ack 身份 = reason，无一键恢复）+ `interaction.ts`（`course-startup`）+ `app.tsx`（一行接线 `loopQueueRows`） | `web-loop-queue` / `web-alert-dock` / `server-api-loop-queue` / `training-pills` / `web-view-course-admin`（旧夹具补 `openable` 缺省） |

**门禁读数**：
- `bun run check`：**2373 pass / 3 skip / 0 fail**（根项目）· `bun run build` 过。
- dashboard：`typecheck` 过 · `bun run test` **1398 pass / 0 fail**（末次全量）· `bun dashboard/src/server/build.ts` 三份 bundle 过。
- nn：`test_serve_skipped_retry`(9) / `test_course_openable`(6) / `test_loop_plan_waiting` / `test_serve_course_overrides` / `test_serve_wiring` 相关全绿（py-safe）。
- 先红后绿：P0 用例在 HEAD worktree 上确认红（能力不存在）后转绿；P1 在改代码前由 §6.1 实测复现（`--json` 报 ready）。

**反向探针（已跑）**：
1. P0：把 `_course_file_identity` 换成恒等 ⇒ `reopenable_skipped` 看不见同一份改动（P0 失效）；恢复真判据 ⇒ 同一改动被看见（同一条命令里两边都断言）。
2. P1：`openable.ok=true` ⇒ 告警零条（用例钉住）；`reason` 变化 ⇒ 新 ack 键 ⇒ 重新弹。
3. 只读路径：`run_rl_cluster.py --json` 冷算 **1.16s**、import 链 **0 行 torch**（10s TTL 预算内；F3 的验收）。

**诚实账（已知边界，与 §6.5 一致）**：
- 判据是「配置不可开课」不是「serve 此刻跳过了它」；在跑课被改坏、trainer 没起都会红（文案已按 F4 写）。
- rl-config/env/权重类、serve 级 `--mode`、BC 链不在 P0 指纹 / `openable` 覆盖面。
- 复活发生在**全局空转拍**，不承诺零延迟。
- 环境观察（如实记）：dashboard 全量 4 次连跑中，前 3 次各出现 1 条**未改文件**的抖动失败（course-switch / course-ctx / lan-gate 各一次，隔离运行与末次全量均绿；同一条三文件命令多跑结果 0/1/2 fail 不等）——判为既有夹具/调度敏感抖动，非本改动引入；末次全量 1398/0 fail。

