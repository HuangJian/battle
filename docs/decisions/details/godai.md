# 决策正文归档 — godai

> 2026-11-08 从 `DECISIONS.md` 搬出的决策正文（索引行与编号仍在 `DECISIONS.md`）。

## §2026-10-19-godai-a2-committed-facing-fire-gate — A2 提交朝向开火门：God-AI 不再把 `_moveDir` 当子弹方向，回收 A1 缺口的 65%

- **来龙去脉**：A1（上一条）把「子弹方向 == 开火时刻的已提交朝向」变成硬引擎规则。God-AI 的**通用开火
  不变量**却是「子弹沿 `_moveDir` 飞」——A1 之前这条**靠引擎 bug 免费成立**（转身被冷却拒绝时子弹仍走
  请求方向 = 瞬时甩炮）。A1 之后它只在**转身被接受的那一拍**成立 ⇒ 「转向 + 同拍开火」在 200ms
  转身冷却窗（≈12 拍）内变成**沿旧朝向的偏弹**，白耗一发。诊断全文 → `plan/godai-post-a1-retune.plan.md`。
- **决策（方案 A，已实施）**：
  1. 新增纯谓词 `turnWillCommit(w, p, dir)`（`src/ai/god/turn-gate.ts`）：**本拍子弹会不会沿 `dir` 出膛**。
     对 `applyTurnCooldown` 的三条路径逐条归约后**精确**化简为两式 ——
     `dir === p.prevMoveDir`（无转身，或转身被拒后回退**正落**在该方向）**或** 转身冷却已过期。
     读 World/tank 既有字段，无 RNG、无 World 写入 ⇒ 快照/回放安全（AGENTS §2.2/§2.3）。
  2. **只在唯一 choke point 上闸**：`GodAIInput.isFiring()`（每个候选的 `_fire` 都从这里出闸）
     `&& turnWillCommit(...)`。**不改任何候选的 `_fire` 赋值**——决策语义/分支计数/遥测保持原样，
     只拦「这一枪会飞错方向」的那一拍；本拍只转身，转身提交后 1..k 拍（k ≤ 12）再开火。
     比计划里"逐点加谓词"更小、更不可能漏点（11 个 candidate × 45 处 `_fire=` 赋值）。
  3. 参数 `turnCommitFireGate`（0 = OFF，字节等同 A1 后基线；**1 = 默认，已发布**）。
- **实测（hard 35 stages × 20 seeds，CRN 同种子，eval-suite v7 配对）**：
  | | SUITE | 算术 score | 胜率 | 最弱关 |
  |---|---|---|---|---|
  | A1 后（gate=0） | 0.4189 | 0.4938 | 58.7% | Riverbed 0.212 / 10% |
  | **A2（gate=1）** | **0.5112** | **0.5912** | **71.0%** | Battlement 0.271 / 20% |
  | A1 前（参照） | 0.5602 | 0.6473 | 78% | Battlement 0.284 / 20% |
  配对：**mean Δscore +0.0964 ± 0.0162（t=5.94, p<0.0001）**，441 好 / 256 坏 / 3 平；
  6 关显著改善（Riverbed +0.330 p<0.001 · Ice Palace +0.221 · Eagle Nest +0.219 · Brick Maze +0.197 ·
  Concentric +0.191 · Spider +0.158），**0 关显著倒退**。
  ⇒ 回收 A1 缺口的 **65%**（SUITE 口径 65.2% / 算术 63.4% / 胜率 63.7%）。
- **机制证据（不是"胜率碰巧涨"）**：中位偏弹率（子弹 `dir` ≠ 本拍请求方向）**27.4% → 0.0%**，开火量仅 −5%
  ⇒ 拦的是**飞错方向的发**而非把开火砍掉。代价：每次被拦 episode 中位 **4 拍** / p90 **10 拍**（一个转身
  冷却窗）⇒ 语义是"等转身"；残余缺口主因即这 1..k 拍延迟（下一步 → 方案 C/D）。
- **新纪元 60-seed 三难度基线（eval-suite v7）**：hard SUITE **0.5246** / win **74%** ·
  classic **0.6941** / **87%** · chaos **0.5048** / **71%**（A1 后：0.4123/58% · 0.6421/83% · 0.3786/53%
  —— 三难度**全线上升，无结构性倒退**）。
- **连带重标（A2 换了每一局 ⇒ 旧行为金标按"换局不改判据"处理，同 A1 先例）**：`tools/det-golden.v1.sha256`
  `0ff1ba66…` → `3f256031…`（21 组合全变）；`tests/sim/metrics-v11-hotlane.test.ts` 评估链 god 金标 3 例重标数值；
  `tests/midlane-sticky.test.ts` §164 seed 10 在 A2 下 sticky=0 也能过关 ⇒ **换 seed 7**（仍复现），并**复活** A1 跳过的 sticky=90 用例。
- **新增回归**：`tests/godai-turn-commit-fire.test.ts`（谓词 × 三条引擎路径 5 例 + 端到端"gate ON ⇒ 零偏弹 /
  gate OFF ⇒ 有偏弹"两条，后者防 vacuous）。
- **不做 / 遗留**：不回退 A1、不改引擎、不碰任务侧、不复活 `tests/godai-score-gate.test.ts`。
  NN 执行器（`src/nn/intent-executor.ts` / `goal-executor.ts`）直接读 `god._fire` 绕过 `isFiring()` ⇒
  同一个 A1 语义缺口在 NN 策略上**仍在**（God-AI 评估链不受影响），另行立项。
- **门禁**：`bun run check` **2586 pass / 15 skip / 0 fail** · `bun run build` 过 · `freeze:check` 绿。
- **残余归因（同日调研，未改代码）**：C（等待期机会射击）p=0.39 否定；D（把转身等待加进 §193-D 到达
  时间）**700/700 局逐局相同 = 空转**（该门只拦 0.6% 的发）。只改 `turnCooldownMs`：200→**71.0%** ·
  100→**81.0%** · 0→80.6% ⇒ 残余 7pp 是**转身延迟**代价，aim 侧无头寸；冷却人类同规，机制决策未动。

## §2026-10-20-godai-mechfix-fire-admission — 开火策略准入（按用户定规实现，实测代价大 ⇒ 默认 OFF）

> 用户定规（2026-10-20）：**开火冷却没到，不允许开火策略生效**。原先 t2a 在冷却期仍会把
> `_moveDir` 设成 aimDir（为"对准"转走方向盘），但这一拍根本打不出子弹 ⇒ 白转一次。
> 归因数据：无用转身（转→无位移无开火→短窗口又转）97.4 次/局，其中 **36% 发生在候选切换**，
> 切换 TOP = `navigate→t2a 1568` / `t2a→navigate 799`（700 局样本）。

- **实现**：`Engage` 的 t2a 入口加 `fireBranchRequiresReady`
  · 1 = 冷却期一律不提交 · 2 = 只在「需要转身才能对准」时不提交（已对准可站着等）· 0 = OFF。
- **机制效果成立**：mode2 下「候选切换型无用转身」**35.5 → 23.1（−35%）**，总无用转身 97.4 → 94.6。
- **但代价很大（60-seed）**：
  | 口径 | OFF | mode2 | mode1(20-seed) |
  |---|---|---|---|
  | hard SUITE | 0.5828 | **0.5609** | 1 档 0.5739→0.5645 |
  | classic SUITE | 0.7276 | **0.6807** | — |
  | hard 胜率 | 79% | 78% | 79% |
  | classic 胜率 | 89% | 87% | — |
  | 单关倒退 | — | — | **Oasis −0.119（p=0.023）** |
- **为什么**：t2a 在冷却期的「站着保持对准」并不是浪费 —— 它是**开阔图上的站位**（Oasis 首当其冲）。
  禁掉之后 AI 转身去找路，位置就丢了。这与前面 6 次"稳定化"实验（目标粘性 −0.005、首步转向代价
  −0.014、facing 承诺 −12% 击杀、阻尼 24/30 −0.11/−0.15）指向同一结论：
  **在反应式架构里，这些"看似无用"的转身/等待是承重的。**
- **决定**：代码保留（一个参数即可切档），**默认 OFF**；是否作为政策强制由用户决定。
  若要强制，建议只在小图/砖关启用（Oasis 这类开阔图显式豁免），需按 stage 属性判定。
- **门禁**：`bun run check` **2609 pass / 0 fail** · `freeze:check` 绿（`ba02a9eb…`，与默认 OFF 一致）。

### §2026-10-20 追加：**按用户指令固定 mode1（无开关）**，并重调 §164 sticky 补回损失

- **决定（用户 2026-10-20 拍板）**：`fireBranchRequiresReady` 删除开关，**固定 = 冷却期内 t2a 不提交**；
  classic/chaos 的退步用户明确表示不重要。适用范围**仅限玩家坦克（P1/P2）**——护卫/盟友是独立辅助
  AI，其「挡道避让 + 恢复自主」契约不归本规则管（`tests/godai-guard.test.ts` 锁定）。
- **掉分归因 → 调优（政策要求）**：固定 mode1 后 hard 0.5828 → 0.5654。逐项排查发现主因不是准入本身，
  而是**同纪元下 `midLaneStickyTicks=90` 从"修复"变成了"负担"**：t2a 不再蹲车道 ⇒ sticky 反而把玩家
  钉在原地（Riverbed seed 4/8/10/12… 由 clear 变 gameover）。60-seed sweep（A=90）：**0 → 0.5781
  （+0.0127）· 30 → 0.5747 · 60 → 0.5686 · 90 → 0.5654**，单调 ⇒ 默认改 **0**。
- **最终（三难度 × 60-seed，全部实测）**：
  | 口径 | 上一纪元（坦克墙） | 本纪元（准入+sticky 重调） |
  |---|---|---|
  | hard SUITE | 0.5828 | **0.5788（−0.004）** |
  | hard 胜率 | 79% | **79%** |
  | classic SUITE / 胜率 | 0.7276 / 89% | 0.6452 / 83%（用户接受） |
  | chaos SUITE / 胜率 | 0.5451 / 76% | 0.5433 / 76% |
  ⇒ **重调把准入带来的 hard 损失从 −0.017 收回到 −0.004，胜率不变**。
- **测试重标（新纪元连带）**：
  · `§165` 两条「ENGAGE 提交」用例：补「这一拍能开火」前提（`lastFire = -1e9`），语义不变；
  · `§164` 钻砖复现用例改写：新纪元 seed 10 **不再复现**（sticky=0 过关），sticky=90 反而失守；
  · `§168` pin 阈值：OFF 564 → **422**、ON 347（准入已把 pin 缩短）；
  · 评估链金标：stage0 s3 → 165/30；s5 与 s1 s6 的 `lane` 变 0（违反两列非零）⇒ 换 s12(68/27) 与 s1 s9(194/154)。
- **门禁**：`bun run check` **2594 pass / 0 fail** · `build` 过 · `freeze:check` 绿（`375a529f…`）。


### §2026-10-21 A4 回防滞后（hunt↔defend 目标抖动）—— SHIPPED 默认 3

**背景**：A1（子弹方向 == 已提交朝向）/ A2（提交朝向开火门）/ A3（转身经济）三连已消除
「转身被拒 → 站桩」与「冷却期开偏弹」。本轮继续清剩余的**原地无效转头/振荡**。

**取证工具**：`tmp/turn-waste-probe.ts`（一次性法医脚本，**不入库**）。每 tick 在
`sim.tick()` 前后各采一次玩家状态，把「想走但没走成」的 tick 做互斥分类，并按 branch
归因、按「目标是否同时翻转」二次归因。

#### 一、基线量化（hard，5 关 × 4 seed = 20 局，99280 个玩家可动 tick）

| 类别 | 占比 | 说明 |
|---|---|---|
| 有位移 | 86.67% | |
| 引擎回退转身 | **0.00%** | **A3 已完全覆盖，这一类已被清零** |
| A3 主动停（躲弹） | 0.52% | §95 语义，保留 |
| 分支主动停 | 8.97% | 各分支的主动 stop-and-aim |
| 撞墙/挡住零位移 | 3.84% | |
| **原地打转游程**（≥6 tick 且净位移 <8px） | **48.6 次/局，浪费 12.4% 的 tick** | 本轮目标 |

按归因拆分：目标翻转型 288 / A* 路径型 583。

#### 二、三个根因（逐个取证到 tick）

**① 主因（已修）：回防距离阈值 × 离散格距离 ⇒ 目标每 11 tick 翻转一次**

- 现场（s35 seed1000 t760-850，持续 90+ tick）：坦克在 x=384 的 y=152↔165 之间上下往返。
  `t787 pc=(24,10) want=up` / `t788 pc=(24,9) want=down` —— 同一像素位置，方向相反。
- 根因：`selectTargetUncached` 里
  `playerDistToBase = manhattan(playerCell...) > maxPlayerDistFromBase(26)`，
  而 `playerCell = round(x / CELL)` 是**离散格**，坦克每跨一格（~11 tick）`distToBase`
  就在 26↔27 之间跳一次：26 ⇒ 猎杀（向上），27 ⇒ 回防（向下）。方向一反转，
  200 ms 转身冷却（12 tick）就比走完一格还长，**两次反转都完不成**。
- 修复（`defenseReturnHysteresis`）：**进入**回防保持即时（真紧急不延迟）；**退出**
  要求距离降到 `阈值 − hysteresis`。两条回防门（§159 `maxPlayerDistFromBase` /
  M13 `outnumberedFieldDistCells`）共用**一个闩锁** `_defenseReturnLatch`，因为后果相同；
  任一门都不成立时立即清闩。

**② A6（实测后维持 OFF）：回防目标 (12,23) 本身不可达**

- 现场（s35 seed1000 t2395-2450，300+ tick）：坦克在 x=354↔368 横跳，`tgt=(12,23)` 恒定、
  `navCache=left` 恒定、但 `followPath` 恒返回 null（`plen=0`）。
- 根因：默认防守位 `(12, 23)` 在**全部 35 关上都是基地环砖格**（§137 注释已承认），
  corridor 与 breakBrick 两种 A* 到砖格目标都返回空路径 ⇒ 回防只能靠 `directMove` 盲走破砖，
  撞墙后落进 `directMove` 的 any-passable fallback（选到 `right`，**完全背离目标**）。
- 已有的 `defensePosStandable`（§146 B，默认 0）正是这个修复。打开后：
  **单关总 tick +5.5%**（s35 seeds1-20：160135 → 168987），即玩家真的跑回基地了，
  清关却更慢；35×60 串行 A/B 胜率 79.7% vs base 80.0%（Δ−0.0036, t=−0.33）。
- ⇒ 「回防目标不可达」**无意中把玩家留在了战场上**（打转），修好可达性只是把打转
  换成了回防奔袭，**两者都不产生净胜率** —— 与「站位/防守侧 A/B 全负」的历史结论一致。
  **维持 OFF**（代码与旋钮保留，注释记实测）。

**③ A5（默认 OFF）：`snap()` 半格跳变是 directMove 左右翻转的直接原因**

- 现场：同一像素邻域 `directMove(pc=(22,10)) → right` 而 `directMove(pc=(23,10)) → left`。
- 根因：`canMoveDirRaw` / `canMoveOrBreakImpl` 用 `snap(v,CELL) = round(v/CELL)*CELL`
  把坦克吸附到网格后再 ±CELL 探测。`snapx` 在 x=360 处从 352 跳到 368 ⇒
  「往左能不能走」对**同一堵墙**给出 false / true 两个答案。
- 修法（`navExactMoveProbe`）：移动轴用**真实像素坐标**探测，垂直轴仍用 snap（引擎每 tick
  snap 垂直轴）。**实测反而更差**：更保守的判定让 `directMove` 的 any-passable fallback
  触发更频繁（打转 43.5→52.6 次/局、短抖动 33→63 次/局）。
- ⇒ **病根在 fallback 乱选方向，不在探测精度**。**代码保留、默认 OFF**，注释记实测。

#### 三、A4 的效果

| 指标 | OFF | hysteresis=3 |
|---|---|---|
| 基地↔猎杀翻转（40 局） | 46.7 次/局 | **18.9（−60%）** |
| 原地打转游程（40 局） | 60.7 次/局 | 47.6 |
| **S34 Battlement nav-stuck 钉住**（seed1-25，§168 口径） | 249~893（均值 ~460） | **≤262（均值 ~171）** |
| 同一 seed 8 | 422 拍 | **163 拍** |

hysteresis 档位全量扫描（35×60 串行配对，真实 stageIndex）：

| 档 | 胜率 | Δ vs base | t |
|---|---|---|---|
| base（OFF） | 80.0% | — | — |
| 1 | 79.9% | −0.0016 | −0.15 |
| **2** | 79.1% | **−0.0102** | **−0.97** |
| **3（SHIPPED）** | 79.8% | −0.0029 | −0.27 |

### 三·补 档位选择的决定性证据：S34 钉住时长随 hysteresis 的变化（13 seed 均值）

| hysteresis | 0 | 1 | 2 | **3** |
|---|---|---|---|---|
| nav-stuck 钉住均值（拍） | 457 | **492** | **429** | **168（−63%）** |
| 最差单 seed | 893 | 1028 | 1546 | 262 |
| hard 60-seed Δ | —（基线） | −0.0016 | −0.0102 | −0.0029 |

⇒ **只有 3 真正消除了钉住**；h=1（492）与 h=2（429）不但没修好，h2 还制造了一个 1546 拍的
新钉住。原因是 pin 的抖动幅度不止 1 格（坦克在多个格之间往返），1 格滞后盖不住。
**h1 在 hard 上"看起来更好"（−0.0016 vs −0.0029）是 3 局噪声，而它在机制上是无效的** ——
按机制强度取 3，不按噪声取 1。

#### 四、连带发现（工具层，未改）

**`tools/eval/eval-suite.ts` 的 `runCorpus` 不传 `stageIndex`**（默认 0），而
`SimTask` 的注释自己声明 *"index 0 and index 33 are genuinely different runs on the
same seed"*（`dropOnScoreMilestone` / kill score 随 index 变）。实证：
`defensePosStandable` 在 s35 seeds1-20 上，stageIndex=34 时 17→18 胜，
stageIndex=0 时 17→16 胜 —— **符号翻转**。本项目所有历史基线都跑在 stageIndex=0 口径下。
本轮的结论一律改用**真实 stageIndex 的串行 runner**（`tmp/ab-full.ts`）得出。

#### 五、新纪元连带重标

- `tests/sim/metrics-v11-hotlane.test.ts` 评估链金标：原 stage1 seed9 在本纪元 `lane` 变 0
  （违反「两列非零」）、原 stage0 seed12 的 `lane` 只剩 9（太脆）⇒ 重扫 seed1-30 后换成
  **stage0 seed3 (108/67) / stage0 seed4 (62/74) / stage1 seed27 (255/163)**（lane 都显著非零）。
- `tests/godai-navstuck-zone.test.ts` §168：OFF 复现腿的 pin 被 A4 从 422 缩到 163
  （ON 腿同时失去额外收益）⇒ OFF 腿显式加 `defenseReturnHysteresis: 0` 以保留复现证据。
- 新增 `tests/godai-return-hysteresis.test.ts`（4 条：bug 复现 / 翻转下降 / pin 422→163 /
  闩锁只推迟退出）。

#### 六、三难度 60-seed 满格（35×60 串行配对，真实 stageIndex，全部实测）

| 难度 | base | hysteresis=3 | Δ(win+0.01·kills) | t | 胜率 |
|---|---|---|---|---|---|
| hard | 1679/2100 | 1674/2100 | −0.0029 ± 0.0107 | −0.27 | 80.0% → 79.7% |
| classic | 1738/2100 | 1749/2100 | **+0.0054 ± 0.0057** | **0.95** | 82.8% → **83.3%** |
| chaos | 1640/2100 | 1648/2100 | **+0.0041 ± 0.0108** | 0.38 | 78.1% → **78.5%** |

⇒ **三格全部在噪声内**（|t| ≤ 0.95）：classic / chaos 正向，hard 微负（−0.3pp，5 局）。
没有一格显著。这是本轮的诚实结论 —— **消除振荡本身不买胜率**，与「执行层 knob 已穷尽」
的历史结论一致：释放出来的 tick 只是被拿去走了更多路，没有变成更多击杀
（Δkills：hard −110 / classic +43 / chaos +59）。

**为什么不回滚**：`defenseReturnHysteresis` 修的是一个**明确的机制 bug**（判定函数对同一
位置给出矛盾答案），且它的收益落在别处 —— S34 Battlement 的 nav-stuck 钉住从均值 ~460 拍
砍到 ~171 拍（那正是 §162/§168 记录多年、曾让 P1 在出生点口袋振荡 17-30s 的同类失败）。
胜率 noise 级 + 机制正确 = 按用户政策发货。

#### 七、门禁

- `bun run check` **2598 pass / 0 fail**（15 skip，128657 expect）。
- `bun run freeze:check` 绿（新钉 `c5abb064…`，上一钉 `375a529f…`）。
- 一次性脚本 `tmp/turn-waste-probe.ts` / `tmp/ab-full.ts` / `tmp/scan-pin.ts` /
  `tmp/stand-check.ts` / `tmp/rescan-golden.ts` **用完即删，不入库**
  （残留会挂 typecheck/build）。

## §2026-10-21-godai-a7-dm-fallback — directMove 的 any-passable fallback：承诺逃逸（机制实现保留，默认 OFF）

**结论先行**：`directMove` 的 any-passable fallback **确实**在背离目标的方向上走出
12% 的 directMove tick，但它**没有第二个选项可选** —— 那里只有「背离主轴」与「背离副轴」
两条路。真正可修的是**每拍重新选向**导致的 1 格打转。承诺窗口把打转砍掉 40-48%，
代价是 **chaos −3.05pp（t=−2.77，显著）**：commit 是「固定 40 拍不看目标」的盲走，
代价随威胁密度放大，而它修的振荡不是胜率瓶颈 ⇒ **实现保留、默认 OFF**（与 A5/A6 同口径）。

### 一、前提核对：fallback 到底有多常见、选了什么

包装 `GodAIInput.directMove`（实例属性覆盖原型方法即可，`think()` 走 `self.directMove`），
在 5 关 × 8 局的 hard 上逐 tick 复算 `prefA/prefB` 与 `canMoveOrBreak`：

| 指标 | 值 |
|---|---|
| directMove 调用 | 931–3645 次/局 |
| 命中 fallback（两个朝目标轴都不可走/不可破） | **80–543 tick/局，占 directMove 的 12%** |
| 选出的方向**背离目标**（`(dx,dy)·v < 0`） | **58.7%** |
| 逃逸 episode（连续 fallback tick） | 189 段 / 8 局，均值 9.8 拍 |
| 其中「逃逸失败」（episode+20 拍内净位移 < 8px） | **29.1%** |

**为什么「背离」是结构性的**：走到 fallback 时 `prefA` 与 `prefB`（两个朝目标的轴）
都不可 `canMoveOrBreak`，剩下四条方向里只有 `opposite(prefA)` 与 `opposite(prefB)` 可能
可走，而旧扫描**硬跳过** `opposite(prefA)` ⇒ 唯一幸存者就是 `opposite(prefB)`
= 「沿副轴背离目标」。**它不是选错的，是没有别的可走。**

### 二、尝试过的三种「修法」

1. **rank=1 排序**（按移动后到目标的平方距离给两条「背离」排序 + 主轴反向作最后手段）：
   实测**更差** —— fallback tick 1.86k→3.93k、失败逃逸 29.1%→**43.6%**。
   两个原因：① 两条候选都是「背离」，排序只是换一条背离；② 放开 `opposite(prefA)`
   等于把 §162 特意封住的**封口袋出口重新打开**（Battlement 出生点口袋），打转回来了。
2. **rank=2 交给路由**（fallback 里调 `navigateTowards(target)`）：
   与 rank=1 逐字节相同 —— A* 在这里**恰好返回 null**（目标就是那个不可站的防守格
   (12,23)，环砖内部），路由无话可说。这条证明「病根不在选向，而在无路可走」。
3. **hold 承诺**（选中方向后，在窗口内覆盖每拍重决策）：唯一有效的部分。
   窗口档位（8 局）：12 → **40.1%（比基线差）**、16 → 34.8%、24 → 22.4%、
   32 → 18.3%、**40 → 16.3%**（基线 29.1%）。12 拍档反而变差正是「必须长于
   200 ms 转身冷却（12 拍）」的直接证据 —— 短于冷却的承诺连一次转身都换不回来。

### 三、三难度满格 A/B（35×60 串行、真实 stageIndex、CRN 配对）

本轮把这条口径**固化成工具** `tools/eval/paired-serial.ts`（eval-suite 的 `runCorpus`
不传 `stageIndex`，见 A4 条目第六节）：
`bun tools/eval/paired-serial.ts <difficulty> <seeds> <jsonA> <jsonB> [--stages]`。

`directMoveFallbackHold: 40` vs 0：

| difficulty | win A | win B | Δ | t | suite | score |
|---|---|---|---|---|---|---|
| hard | 79.6% (1672) | 79.0% (1658) | −0.67pp | −0.62 | 0.6139 → 0.5996 | −0.0059 |
| classic | 83.4% (1751) | 82.7% (1737) | −0.67pp | −0.68 | 0.6591 → 0.6518 | −0.0062 |
| chaos | 78.4% (1647) | 75.4% (1583) | **−3.05pp** | **−2.77** | 0.5984 → 0.5663 | −0.0232 |

**chaos 显著负**，且 hard/classic 同号同幅（−0.67pp）。逐局看更清楚：hard 8 局探针里
3 局 clear→gameover、1 局 gameover→clear（s34 seed1000：7200 拍清关 → 2302 拍阵亡）。

**为什么 chaos 掉得最狠**：commit 是「固定 40 拍不重新看目标」的盲走。chaos 的子弹密度
最高，40 拍 = 0.67s 的盲走窗口里挨弹概率最大；hard/classic 窗口多数能走完并换来一次
真正的绕行。而**它修的东西**（1 格打转）在 700 局审计里并不是败因（90% 失败是丢基地）。

### 四、发货判定

按用户政策「机制修复即使胜率下降也要做」，本轮仍**不发货**，理由是它**不是机制修复**：
旧行为没有给出错误答案（只有那一条路可走），它是**缺记忆**；补记忆的代价随威胁密度
线性放大，而收益（少打转）没有转成击杀。真正待试的是**闸门化的承诺**：基地/敌弹威胁
出现时立即放弃 commit（`isBaseUnderThreat` 已有），这需要在 chaos 上单独跑一轮满格表。

### 五、探针教训（会反复踩，务必记住）

直接 `sim.tick()` 驱动 `Simulation` **必须自己调 `input.endFrame()`** —— `runSimulation`
在每拍末尾调它（`tools/sim/simulation-runner.ts:862`），而 `Simulation.tick()` 本身**不调**。
漏掉时 `_thought` 永远为 true ⇒ **`think()` 只在第 0 拍真正跑一次**，之后
`_moveDir` 一直冻结：本轮的第一次探针就是这么得出「directMove 每局只调用 1 次、
fallback 从不触发」的假结论（实际 931–3645 次），坦克在第一个障碍物前站到终局。
另一处同类陷阱：`playerCell()` 返回的是**复用对象**，跨 tick 保存引用会读到后面被改写的值。

### 六、门禁

- `bun run check` 全绿（见下条提交）。
- `bun run freeze:check` **绿且无需换钉**（`c5abb064…` 不变）—— 两个 knob 默认 0 时
  代码路径整段跳过，字节等同。
- 新增 `tools/eval/paired-serial.ts`（串行配对 A/B，真实 stageIndex；本轮三难度表与
  A4 的对比表都用它产）。
- 新增 `tests/godai-dm-fallback.test.ts`（4 例）：默认值守卫、reset 清状态、
  s35 seed1002 上 fallback tick −63% / 失败逃逸 13→2、hold=0 与基线逐字节一致。

## §2026-10-21-godai-a8-defense-cell — 集合点可站化：（12,23）在 35/35 关都是环砖，回防路由从来没通过

**结论先行**：`getDefaultDefensePosition()` 返回 `(BASE_POS.col, BASE_POS.row − defenseRowOffset)`
= **(12,23)**。实测 **35/35 关该格都是基地环砖**，`findPath` 在 **corridor 与
dig-through-brick 两种模式下从出生点与中场都返回空路径** ⇒ 所有「回防」指令
（`selectTarget` 的 baseUnderThreat 分支、M13 外线撤收、§113/§88 回防）都在把导航层
指向一个**永远到不了**的目标：`followPath` 返回 null，`directMove` 只能朝环砖盲走破砖，
玩家在环砖前打转。**已修并发货**（`defenseCellResolve: 1`），35 关全部落到 **(12,22)**。
三难度净值在噪声内（classic **+0.86pp**、hard −0.14pp、chaos −0.62pp）。

### 一、缺陷证据（35 关全量）

```
s1 Outpost:       def=(12,23) terrain=brick standable=N spawnPath=0 midPath=0 digPath=0
...
s35 Final Redoubt: def=(12,23) terrain=brick standable=N spawnPath=0 midPath=0 digPath=0
unstandable 35/35 · empty corridor path from spawn 35 · from mid 35
```

`digPath=0` 是关键：破砖 A* 连「先打穿砖再站上去」都不给，因为目标格本身不可站。
对比修复后的 (12,22)：**digPath 6–10**（从出生点出发的真实破砖路线长度）。

### 二、修复语义（`resolveStandableDefenseCellImpl`）

1. 原格**可站且四邻至少一个可站** → 原样保留（环内单格气泡「可站但 A* 进不去」
   同样算坏格）；
2. 否则沿**基地列向上**最多 6 行 —— 防守位要覆盖的就是基地正面的接近带，列不变
   语义最强（35 关的答案都是 (12,22)，即环砖正上方一格）；
3. 仍无解 → 基地周边 7×10 盒内按曼哈顿距离就近取可站格；
4. 全都没有 → 原样返回（极端封闭地形，不比现状更差）。

按 `tileMap.revision` + 请求格缓存（地形变化 / 伙伴偏移变化才重算），`reset()` 清空；
纯地形函数，无 RNG、与玩家位置无关 ⇒ 决策确定性不受影响（冻结签名换钉是因为行为
真的变了，不是因为随机性）。

**为什么不用现成的 `baseGuardAnchorMode` 评分**：它是环砖外的「有射界」评分
（ringCover/approachCover/laneCover），实测历史是 marginal≈0（§149），且它是旋钮
（默认 0）；本轮要修的是「目标不可站」这个**机制**，用最小语义改动即可 —— 不动
「站哪最有利」的评分，先把「站不上」修掉。

### 三、三难度满格 A/B（35×60 串行、真实 stageIndex、CRN 配对）

`defenseCellResolve: 1` vs `0`：

| difficulty | win 0 | win 1 | Δ | t | suite | score |
|---|---|---|---|---|---|---|
| hard | 79.7% (1673) | 79.5% (1670) | −0.14pp | −0.20 | 0.6146 → 0.6119 | −0.0005 |
| classic | 83.4% (1751) | **84.2% (1769)** | **+0.86pp** | 0.77 | 0.6591 → **0.6687** | +0.0062 |
| chaos | 78.4% (1647) | 77.8% (1634) | −0.62pp | −0.88 | 0.5986 → 0.5943 | −0.0046 |

**新基线（A8 发货后，35×60）**：hard 79.5% / suite 0.6119 · classic 84.2% / 0.6687 ·
chaos 77.8% / 0.5943。

### 四、胜率恢复研究（用户要求：修完再研究怎么把胜率提回来）

三组实验，hard 35×20（n=700，SE≈1.5pp）：

| 候选 | Δ | t | 判读 |
|---|---|---|---|
| `maxPlayerDistFromBase` 26 → **30**（回防更晚） | **+0.86pp** | 0.76 | 噪声内 |
| `maxPlayerDistFromBase` 26 → **22**（回防更早） | +0.43pp | 0.28 | 噪声内 |
| `defenseRowOffset` 1 → **3**（守位行深一格，落到 (12,21)） | **0.00pp** | 0.00 | 完全无差别 |

**两个方向都「正」** ⇒ 这就是噪声，不是杠杆。守位行深 0.00pp 更直接：**站在环砖
上一格还是两格，对胜率完全无所谓**。所以本轮没有「被修坏」的胜率可恢复 ——
A8 的净值本来就是 0。

**为什么净值是 0（与 A4/A7 同一条线）**：修复前「回防」指令是**惰性**的（目标到不了
⇒ 玩家留在原地继续打），修复后玩家**真的会走回家**。也就是说这 0.14~0.86pp 的差异
只是「原地打」和「回家守」两种策略的差价，而两种都≈79%。这与 700 局审计一致：
**90% 的失败是丢基地，但丢基地的原因是远距离顺线点名（致命弹 down 40%/left 32%/
right 28%），不是「玩家没站在 (12,22)」** —— 站到集合点也挡不住隔着半张图的炮弹。

### 五、新纪元连带重标（A8 改变了整条决策轨迹）

1. **评估链金标**（`tests/sim/metrics-v11-hotlane.test.ts`，本测口径 1500 tick）：
   扫 stage0/1 × seed1-30 后**只有 stage0 seed3 变了**（near/lane 108/67 → **47/49**）；
   seed4（62/74）与 s1 seed27（255/163）逐值不变。种子不换，只改数值。
2. **三条历史机制测试腿固定回本纪元之前的口径**（`defenseCellResolve: 0`），否则它们
   测的不再是各自那个机制：
   - `tests/godai-return-hysteresis.test.ts`（A4 翻转计数 57→63 **反向**、S34 OFF 档
     dwell 422→373）；
   - `tests/godai-navstuck-zone.test.ts`（§168 OFF 复现腿，同一 pin 373）；
   - `tests/godai-dm-fallback.test.ts`（A7 fallback 计数 543→149，相对下降被稀释）。
3. `tests/base-defense-standable.test.ts` 改写成**退役守卫**：旧旋钮
   `defensePosStandable` 已不再被读取（近基/远基都无效果），旧行为只能靠
   `defenseCellResolve: 0` 复现。
4. `tests/godai-hub-fields.test.ts` allowlist 补 4 条（`_defCellCol/Row/KeyCol/KeyRow`，
   class C，守卫旗标 `_defCellValid` 已在 reset() 覆盖）。

### 六、门禁

- `bun run check` 全绿；新增 `tests/godai-defense-cell.test.ts`（5 例：默认 1、
  35/35 原格是砖、35/35 解析后可站且四邻有路、35/35 破砖路由从 0 → >0、缓存与
  reset 语义）。
- `bun run freeze:check` **换钉** `c5abb064…` → **`7af64ddf…`**（新纪元三件套齐：
  本条目 + 60-seed 基线 + golden），corpus 21 runs / 108513 行签名。
- 一次性探针 `tmp/defpos-probe.ts` / `tmp/anchor-probe.ts` 用完即删。

## §2026-10-21-godai-a9-base-lane-seal — 远程来袭弹的提前判线 + 封线（SHIPPED，classic restore 0）

**结论先行**：700 局审计说 90% 的失败是丢基地、致命弹来自半张图外的顺线点名（down 40% /
left 32% / right 28%，丢基地时玩家 p50 距基地 9 格、6 格内平均 0.23 个敌人）。原有的
`baseLaneSentry`（§198）只在玩家 `range+1` 范围里反应，远程射手**从来没人管**。
新增候选 `baseLaneSeal`：基地列上出现**直射基地**的远程射手（`canShootBase`，≥8 格）且
玩家在 14 格内时，走到该列**最靠近基地的可站格**（环砖正上方）——站位既挡弹、又拿到向上
的射击线——站定开火。**机制证据**：反应式拦截（t8）触发 **45 → 2 次 / 140 局**，本该在
飞行中才被拦的子弹被提前消掉。三难度 35×60：hard **+0.48pp**、chaos **+0.29pp**、
classic −0.14pp（只有 3 局翻盘且全向下）⇒ classic restore 0。

### 一、为什么是「封线」而不是「继续拦截」

- 反应式拦截（`interceptBase` / t8，权重 900）只能在**子弹已经在飞**之后抢位；飞行时间
  25 拍量级，而玩家 p50 在 9 格外 ⇒ 追不上。
- `baseLaneSentry`（850）只覆盖玩家身边的哨兵站位（`baseLaneSentryRange+1`），远程射手
  不在其射程内。
- 所以缺的正是**提前判线**：在「射手还没扣扳机」的时候占住它到基地的那条线。

### 二、实现（`candidates/BaseLaneSeal.ts`，权重 542）

1. **判线**：遍历敌人，只考虑 `canShootBase`（直射基地，意味着环砖已被拆穿、下一发真能
   打到鹰）或 `canBreachRing`（拆环威胁，可用 `baseLaneSealCbr` 关掉）；要求**垂直车道**
   （列 = 基地列或基地列+1 —— 致命弹 40% 是 `down`）且离基地 ≥ `baseLaneSealMinDist`。
   `csb` 优先，同级按玩家距离排序（近的先处理，飞行时间短）。
2. **选位**：从 `(基地行−2)` 沿该列向上找第一个可站格 —— 35 关都是环砖正上方那一格；
   站上去等于「用自己的车身堵住这条线」。
3. **执行**：`navigateTowards(seat)`（A* 负责绕路）；到点后定身朝上、`shouldFireInDir`
   放行就开火（往上打射手，不会打到自家环砖）。

### 三、参数（全部可 A/B，模式 0 = 字节等同）

| 参数 | 默认 | 作用 |
|---|---|---|
| `baseLaneSealMode` | **1** | 候选入链（classic restore 0） |
| `baseLaneSealMinDist` | 8 | 只封「远程」射手，近带交给 `baseLaneSentry` / `defenseIntercept` |
| `baseLaneSealRange` | 14 | 玩家愿意跨多远去封线（超出就不做） |
| `baseLaneSealCbr` | **0** | 是否连「拆环威胁」也提前封（实测含 cbr 只有 ±0，见下） |

### 四、A/B（35×60 串行、真实 stageIndex、CRN）

| difficulty | OFF | ON | Δ | t | kills |
|---|---|---|---|---|---|
| hard | 79.5% (1670) | **80.0% (1680)** | **+0.48pp** | 1.20 | +101 |
| chaos | 77.8% (1634) | **78.1% (1640)** | **+0.29pp** | 0.66 | +150 |
| classic | 84.2% (1769) | 84.1% (1766) | −0.14pp | −1.73 | −27 |

**为什么 classic 要 restore 0**：classic 是 `bulletCap 1` 的单发对枪，站上车道等于把
每一发都变成对射；2100 局里只有 3 局翻盘，**全部向下**（+0/−3）。t=−1.73 的「显著」是
零方差塌缩的产物，但方向一致 ⇒ 按 §134/§198 的既有规矩 restore 0（byte-identical）。

**为什么 cbr 关掉**：含 `cbr`（连拆环威胁也提前封）时 35×20 上是 **±0**，只封 `csb` 是
+0.71pp。拆环威胁太常见（每个对齐环砖的敌人），提前封线会把玩家从进攻位反复拖走。

### 五、门禁与连带

- `bun run check` **2613 pass / 0 fail** · `bun run build` 过 ·
  `freeze:check` 换钉 `7af64ddf…` → **`ab119977…`**（21 runs / 108513 行）。
- 新增 `tests/godai-base-lane-seal.test.ts`（6 例：默认值 + classic 回退、mode 0 不接管、
  远程同列接管、到点定身开火、非基地列/近带不管、无活射界不管）；`decision-core` 的
  链顺序与权重断言同步（542，严格递减）。
- 评估链金标（metrics-v11）**逐值不变**（三例 seed 的 1500-tick 轨迹未受影响）。
- 新基线（A9 发货后，35×60）：hard **80.0% / suite 0.6172** · classic 84.2% / 0.6687 ·
  chaos **78.1% / 0.5950**。

## §2026-10-21-godai-a10-lead-fire — 预判开火（lead fire）：实装 + 实测负向 ⇒ 默认 OFF

**结论先行**：God AI **确实没有**这条能力（开火门 `shouldFireInDirImpl` → `scanAheadImpl` 只扫
当前几何，全 `src/ai/god` 无一处用 `t.vx/vy` 外推落点）。本轮把它实装成与 NN 观测
`leadHitD[4]` **共用同一份谓词**（`god/lead-fire.ts`，`nn/policy-extra` 反向 import），
实测 hard 35×20 **Δ−2.14pp（t=−1.24）· kills −268 · score −0.0182（t=−1.46）** ⇒ 默认 OFF。

### 一、缺口有多大（hard 140 局 / 770,082 个玩家存活 tick，用 NN 同源判据复算）

| 指标 | 值 |
|---|---|
| `alignedNow`（当前射线有敌） | 305,841（39.7%） |
| 实际开火 tick | 8,890 ≈ **64 发/局** |
| `leadHit(facing)`（按到达时算能中） | 139,295（18.1%） |
| `lead && !aligned`（被开火门结构性拒掉） | 21,235（2.76%） |
| 其中枪已就绪、却没开火 | **5,080 ≈ 36 次/局** |

即：结构上被放弃的发次 ≈ 实际开火量的 **56%**。上一轮「瞄向由对齐唯一确定 ⇒ 无杠杆」
只对**方向**成立，**时机**是真空缺 —— 结论已在上一轮更正。

### 二、实现

- `src/ai/god/lead-fire.ts`：`leadHitForDir(world, dir, pcx, pcy)` —— 轴向 ≤13 格、
  `|lateral + vLat·t*| < TANK/2+BULLET/2 = 19px`、同轴遮挡扫描。**唯一实现**，
  `nn/policy-extra` 改为 import 它（`src/` 不得 import `src/nn/` 的边界门要求谓词落在 ai 层，
  遮挡扫描与命中带因此在本文件内自足）。
- `GodAIInput.isFiring()`：`leadFireMode > 0 && !_fire` 时，沿**已提交朝向**（`p.dir`）判一次
  `leadHitForDir`，命中则放行开火。已有对齐目标（`_fire`）永远优先；谓词本身要求整条车道
  无遮挡（base/环砖/钢都算遮挡）⇒ 不可能穿基地开火。

### 三、为什么是负的

**冷却才是稀缺资源**，不是子弹：64 发/局已经是射速上限的 86%，把一发花在「外推猜测」上，
就会挤掉 1–2 拍之后那个 100% 确定的命中。`leadHitD` 用的是**当前**横向速度线性外推，
而敌人转向不可知 ⇒ 远距离预判的真实命中率远低于判据给出的 100%，于是「用确定的换猜测」
净值必负。kills −268 就是这个交换的直接结果。

**三个「更挑剔的门」全试了，全部更差（而且是显著的）**：

| 模式 | 门 | Δ | t | kills |
|---|---|---|---|---|
| 1 | 对称（13 格 / 19px） | −2.14pp | −1.24 | −268 |
| 2 | 仅横向**朝车道内**的来车（`lat·vLat<0`） | **−4.14pp** | **−2.52** | −286 |
| 3 | 2 + 轴向 ≤6 格 + 命中带 10px | **−2.71pp** | **−2.62** | −184 |

**越挑剔越亏，这恰恰是最好的证据**：把门收紧之后开火次数更少，亏得却更多 —— 因为
「横向正在进入车道 + 距离近 + 偏差小」正是**再等 1–2 拍就会自己对齐**的时刻。此时提前
扣扳机，冷却（0★ 约 74 拍/发）会在敌人穿过车道时正好卡住，那一发 100% 确定的命中被
自己的预判顶掉了。**等待严格优于猜测** —— 这条结论对以后所有「多发一次」类候选都成立：
先问「这发会不会顶掉后面那个确定发」。③「闸门化」因此不必再做：它的前提（对齐机会很远）
与「值得预判」的前提（敌人正在进入车道）互斥。

### 四、门禁

`bun run check` 全绿 · `freeze:check` 绿（`ab119977…` 不变，`leadFireMode` 默认 0 =
字节等同）· 新增 `tests/godai-lead-fire.test.ts`（4 例：静止敌人 32px 打不中 / 同一敌人
横移 16px 打得中 / 钢墙取消 / 枪口后方忽略）。

## §2026-10-27-godai-a28-fix — 失败思路复审：A28 的两次实现错误（修好 +2.24pp，SHIPPED）+ 12 条同批复核

**结论先行**：上一轮把 A28（追击时给「要打好几发」的目标加惩罚）判成「机制空转」
（700/700 逐局相同）是**实现错误**，不是想法错误。两处 bug：
① `Tank.hp` 是**伤害点**（basic 250 / armor 500，玩家一发 ≈100），判据 `hp > 1` 恒真
⇒ 惩罚加给所有目标、排序不变；② 惩罚只挂在 `huntModeTarget`（`canHunt` 残局分支），
而 90% 的选目标走 `normalSelectionTarget` 的最近敌级联 ⇒ 即使修好单位也照样空转。
修好后 hard 35×60：**83.5% → 85.8%（Δ+2.24pp, t=2.70）· kills +380 · suite 0.6567→0.6909**。

同批复审 12 条失败思路，**6 条是误读/实现错误，6 条是真负向**（详见
`docs/godai-failed-ideas-audit.md`）。

### 一、A28 的修法

- `god/constants.ts` 新增 `shotsToKill(hp, referenceDamage)`（单位换算唯一入口）。
- `StrategyPlanner.ts`：`armorChasePen(self, t) = (shotsToKill − 1) × armorChasePenalty`，
  挂到**全部**最近敌级联：`huntModeTarget` / `targetValueMode` / `pathTargetMode` / 普通最近敌。
- 惩罚系数：1 最好（+2.24pp），2 更弱（+1.05pp, t=1.13）—— 2 格会让 AI 为残兵多走太多路。
- 三难度：hard **+2.24pp** · classic −0.62pp(t=−0.88) ⇒ `CLASSIC_OVERRIDES` 还原 0
  （instant 1 弹上限，走位换目标不划算）· chaos −0.10pp(t=−0.11) 噪声保留。
- `GUARD_GOD_AI_PARAMS.armorChasePenalty = 0`：护卫行为逐字节不变（SP 回归线锁定 §159/§160）。

新纪元基线（35×60，真实 stageIndex，CRN）：
**hard 85.8% / suite 0.6909 / score 0.8512** · **classic 88.3% / 0.7321 / 0.8730** ·
**chaos 82.5% / 0.6435 / 0.8293**。golden 重钉 `ee8cfef2… → a492627b…`。

### 二、同批复核（12 条，全部重跑）

| # | 定性 | 修后实测（hard） |
|---|---|---|
| A13 dodge 按 TTI 选弹 | **误读**：`bulletLaneDist` 返回**轴向**距离，不是横向；同速弹下排序恒等 | 探针 717 个多弹 tick：分歧 **0** |
| A27 先杀一枪死的 | 单位错误（`hp>1` 恒真） | 350 局仅 1 局翻转 ⇒ 无杠杆（多方向可打极少） |
| A29 血量池质量模式 | 单位错误（池 ≈700 点 vs 阈值 30 ⇒ 恒真） | 阈值 8 生效后 +22/−22、score +0.0011 ⇒ 中性 |
| A30 站桩守中路 | 判据过宽（`enemyNearLane` 几乎恒真） | mode 2（只认列内来袭弹）−0.86pp ⇒ 仍负 |
| A31 盾牌期激进 | **盾源错**（3s 复活盾当 20s 道具盾，45% 触发错窗）+ `aggressive` 连防线一起关 | mode 2（基地安全才激进）**+0.19pp(t=0.44)** |
| A32/A33 冰冻期点名 | 缺 cooldown/基地门，A32 还硬关火 | mode 2 −1.14pp ⇒ 仍负（冰冻期已在 81% 射速上限） |
| A34 不对消 | **判据方向反了**：`enemyBulletOnRay` 只匹配朝基地飞的弹 | 上限实测 **2.0 发/局**被对消（63 发/局） |
| A35b 打肩膀 | 盾源错（同 A31）+ 前提高估 | 24 拍转向 ≫ 2 发/局 ⇒ 方向关闭 |
| A10.9 parting shot | **派发落穿**：mode 7 落到 mode 6 分支，从未只在转身拍开火 | 真正实现后 **−6.00pp(t=−2.35)** ⇒ 更糟 |
| A15 沿朝向补射 | **落点错**：只在 `!_fire` 试，A3 替换/A2 否决窗口（≈49 拍/局）全丢 | mode 4 **+0.05pp(t=0.06)** · kills +104 ⇒ 中性 |
| A7 rank 2 让 A* 选路 | **结果被覆盖**：`pick` 非空走 legacy 扫描 | 修后 −0.57pp(t=−0.24) ⇒ 中性 |
| A16 到达时命中门 | 实现忠实；预测器噪声 > 滤掉的空弹 | 不复测（−2.29pp 成立） |

### 三、四个可复用的判据

1. **先验单位**：`hp`（点）/ 发数 / 格 三套单位混用是这类 bug 的主源；任何阈值先打印分布。
2. **「逐局相同」是实现空转的指纹，不是想法无效的证据**（本轮 6 条里 4 条被它误判过）。
3. **状态字段 ≠ 机制**：`shieldTimer` 三源合一（复活 3s / 星盾 1.5s / 道具 20s）。
4. **负向结论先算天花板**：A34/A35b 的收益上限 = 2 发/局；A32/A33 的射速上限 = +24%。

### 四、门禁

`bun run check` 全绿 · `bun run build` 过 · `bun test godai` 325 pass / 0 fail ·
`freeze:check` 绿（新钉 `a492627b…`）· 新增 `tests/godai-armor-chase.test.ts`
（4 例：单位换算 ×2 + 选目标排序 ×2，后者直接钉住「惩罚 0 追最近的 5 发装甲 /
惩罚 1 改追 3 格外的 1 发残兵」）。
