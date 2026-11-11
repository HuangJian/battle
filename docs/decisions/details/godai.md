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


---

## 第五轮归档（2026-11-11）

> 2026-11-11 第五轮瘦身：`DECISIONS.md` 中仍带成篇正文、正文 >6 非空行的 **13** 条决策全文按领域落到本节（索引行与编号仍在 `DECISIONS.md`）。锚点 = `## §<id>`。

## §2026-10-19-godai-a3-turn-economy — A3 转身经济：不请求拿不到的转身（引擎会因此把坦克冻住），胜率 74%→79%

- **取数先行**（hard，8 关 × 3 seed，2026-10-19）：God-AI 每局接受 **240 次转身**，其中 **95.1% 在 12 拍内
  没换来任何一发**（转身是买走位的，但价款用「瞄准锁定」付）；更关键的是 **17.7% 的全部 tick** 在请求一个
  转身冷却**根本不会批准**的方向 —— 而 `applyTurnCooldown` 拒绝时不仅回退 `dir`，还置 `moving=false`
  ⇒ **这 17.7% 的 tick 是白白站桩**。A2 之前无从判断（没有谓词），A2 给了 `turnWillCommit`，A3 由此可做。
- **决策**：在**唯一出口** `GodAIInput.getMoveDirection()` 上做替换（`effectiveMoveDir()`）：
  转身拿不到 ⇒ 改为请求**已提交朝向**（继续走、别站桩），`_moveDir` 里的分支意图不动；
  `isFiring()` 在「有效方向 ≠ 分支瞄准方向」时**不开火** ⇒ **子弹集与 A2 完全一致**，这一刀只买「少站桩」。
  参数 `turnEconomyMode`：0=关 · 1=无条件替换 · **2=仅当已提交朝向未被敌弹穿过时替换**（SHIPPED）。
  mode 1 的心智风险正是 §95 那条：「被拒的躲弹转身必须保持停」，halt 本身就是 dodge。
- **实测（hard 35×20 / 35×60，CRN 配对，vs A2）**：
  | 臂 | 20-seed Δscore | 60-seed Δscore | 胜率(60) | 备注 |
  |---|---|---|---|---|
  | mode 1（无条件） | +0.0393 (p=0.012) | +0.0183 (p=0.033) | 74%→77% | **Steel Web −0.12（p=0.017）** |
  | **mode 2（敌弹守卫）** | **+0.0618 (p=0.0001)** | **+0.0384 (t=4.56, p<1e-4)** | **74%→79%** | 4 关涨 / **0 关跌** |
  60-seed 明细：1150 好 / 947 坏 / 3 平；SUITE **0.5246 → 0.5748**；
  ▲Diamond +0.082 · ▲Concentric +0.102 · ▲Eagle Nest +0.117 · ▲Frozen Field +0.126。
  ⇒ 「别把惯性开进子弹」就是 mode 1 → mode 2 的全部差距（Steel Web 的倒退也随之消失）。
- **新纪元 60-seed 三难度基线（eval-suite v7）**：hard **0.5748 / 79%** · classic **0.7330 / 89%** ·
  chaos **0.5343 / 75%**（A2 后：0.5246/74% · 0.6941/87% · 0.5048/71% ⇒ 三难度全线上升）。
- **⚠ 过程中抓到的确定性回归（本条最贵的教训）**：`getMoveDirection()` 原本是纯读，A3 让它读了
  `world.frame`；而 `InputRecorder.recordFrame()` 在 `sim.tick()` **之后**再读一次输入 ⇒ 重算会得到与
  实际下发不同的方向，`tests/replay-roundtrip.test.ts` 当场炸（score 6600 vs 4000）。
  **修法**：`effectiveMoveDir()` 按 tick 缓存（`_effectiveDirCache`，`endFrame()`/`reset()` 失效）。
  **纪律**：任何让 `getMoveDirection()` / `isFiring()` 变成「依赖时钟」的改动，必须做成每拍快照。
- **口径警告**：天降神兵护卫（`SimulationEnemies.guardAIFor`）自建 God-AI 脑且**用 DEFAULT 参数** ⇒ 它们
  跟随出厂默认、不认 `--params` 覆盖。所以 A/B 的「关」臂里护卫仍开着 mode 2，测出的 +0.0384 是**玩家侧
  净效应**，总效应 ≥ 此值。
- **连带重标**：`det-golden` `3f256031…` → `ed6dc0f9…`；`metrics-v11-hotlane` god 金标 3 例
  （stage 0 seed 9 的 lane 归零 ⇒ 换 stage 0 seed 3）；`midlane-sticky` §164 回到原 seed 10
  （A3 下重新可复现：sticky=0 tick 2855 失守 / sticky=90 过关）；`godai-turn-snap-guard` §80 与
  `godai-turn-commit-fire` 的 A2 用例显式钉 `turnEconomyMode: 0`（各自隔离被测机制）。
- **新增回归**：`tests/godai-turn-economy.test.ts`（5 例：可用转身不替换 / 拿不到则沿已提交朝向 /
  mode 0 不替换 / 敌弹在已提交朝向时保持 null / 替换时不开火）。
- **不做**：不改引擎、不动 `turnCooldownMs`（机制决策待拍板）、不复活 `tests/godai-score-gate.test.ts`。
- **门禁**：`bun run check` **2591 pass / 15 skip / 0 fail** · `bun run build` 过 · `freeze:check` 绿。

## §2026-10-19-godai-audit-hard-700 — 700 局审计：瓶颈画像 + 四个方向的 A/B 全部未发货（负面结果登记）

- **结局构成**（hard 35×20 = 700 局，出厂参数，79.7% clear）：**丢基地 128 / 命耗尽 14** ⇒
  90% 的失败是基地失守，自身生存不是瓶颈。丢基地时玩家距基地 **p50 9 格 · p90 20 格**；
  **第一滴血→失守 p50 886 拍**（时间充足）；掉血后威胁判定 70.7% 为真但玩家在 6 格内只有 40.5%。
- **基地不是被近身拆的**：失守时基地 6 格内平均只有 **0.23 个敌人**；致命弹方向
  **down 40% · left 32% · right 28%**。**13.0% 的 tick（失败局 16.7%）存在 enemyCanShootBase
  的敌人（中位 9 格），AI 只有 14.1% 的时间在追它** ⇒ 远距离顺线点名是主形态。
- **结构性缺口（读码）**：`defenseIntercept` 要求玩家已与敌人同行/同列；`baseLaneSentry` 的站台
  导航分支**显式排除 csb 敌人**；`interceptBase`（T8，权 900）700 局只有 382 拍 ⇒
  「狙击手对基地有射界 + 玩家不在该车道」无人负责。
- **射击效率（逐发追踪）**：命中 60.2%、3.7 发/杀、64 发/局；**hard 0★ 射速上限 ~74 拍/发 ⇒
  已用掉 86% 的射击机会**，所以提速只能靠命中率。命中率随距离崩：0–2 格 96% · 2–4 90% ·
  4–6 77% · 6–9 63% · 9–13 54% · 13+ 47%。未命中里 **23.8% 是横向偏移 >32px**（命中窗只有
  ±19px —— 物理上打不到），24–32px 段另占 11%。
- **A/B 结论（A 臂恒为 `{}` = 出厂默认，hard）**：
  | 方向 | 20-seed | 60-seed | 判决 |
  |---|---|---|---|
  | 新候选 `csbLaneCover`（车道补位） | +0.002 (p=0.84) | — | **无效**（追不上：玩家 1px/拍 vs 敌人横移） |
  | `baseGuardAnchorMode=1`（修不可达守位） | +0.003 (p=0.80) | — | 无效 |
  | 空枪门（整条车道空无一物则不射） | −0.003 (p=0.84) | — | 无效 |
  | 基地受损后动态回防 14/18/14+锚 | −0.007 / −0.000 / +0.000 | — | 无效（触发面太窄，599/700 不变） |
  | 到达时刻命中判定（补 §193-D 横穿漏判） | +0.008 (p=0.25) | **+0.003 (p=0.42)** | 不显著，1 关涨 1 关跌 ⇒ 不发货 |
  | `firingLaneMode=1` | **−0.171** | — | 强负向 |
  | `maxPlayerDistFromBase` 26→12 | **−0.139** | — | 强负向（游走是有产出的） |
  | `baseAlertPickupSuppress=1` | −0.022 (p=0.019) | — | 负向 |
  | `defenseClearShotBonus` 500→2000 | −0.001（699/700 同） | — | 权重已饱和，不是杠杆 |
- **结论**：防守站位侧（4 个角度）与瞄准/开火侧（3 个角度）**都已饱和**。远距离未命中的成因
  主要是敌人在飞行途中的**不可预测转向**（线性外推不可靠），属不可约误差 —— 这解释了为什么
  加预测门既救了 Lattice、又伤了 Iron Curtain。
- **未发货即清理**：所有实验代码/参数已回滚，工作树回到 A3 提交（golden ed6dc0f9 仍绿）。
- **下一步建议（按性价比）**：① `turnCooldownMs` 200→100 的机制决策（实测 71%→81%，
  但人类同规，须拍板 + 真人 playtest）；② 死资产清理（61 个默认 0 参数 + 6 条 never-taken
  分支：`survive`/`suicideReturn`/`firingLane`/`carvePath`/`midLaneHold`/`unifiedCandidates`，
  全部是 mode=0 或 weight=0，非遮蔽性 bug）；③ 别再在"站位/瞄准"上投入。

## §2026-10-20-godai-crosscheck-other-lineage — 与外部研究（godai-tuning-summary-20261020）的交叉核对

- **先核验血缘**：该文档引用的 7 个提交（bbf59ac / ceeb5d4 / 7e43987 / 79968d6 / 97a9402 /
  06acc5e / a139c68）**在本库全部不存在**，`campaign/god-tune` 也不在 origin 分支列表内 ⇒
  **两条并行血缘**（本库为 `god-retune`）。其结论不能直接采信，只能按"概念"逐条核对。
- **口径对齐**：按对方的 35 关 × 120 seed = 4200 局重跑本树 ⇒ **SUITE 0.5629 / 胜率 78%**，
  最弱 Battlement 30.8%。对方 82.14%。**弱关编号完全对齐**（s34 ≈30%、s8 ≈38%、s20 ≈61%），
  且对方 run1 基线 78.86% ≈ 本树 78% ⇒ 差的 ~4pp 来自其六件套，而非口径差异。
- **六件套在本树的存在性（grep 验证）**：`playerPlanCell`（planCell v2）·`GOD_TANKWALL`·
  `predictCrossSlackTicks`·aim-threat 的基地威胁加权 — **全部不存在**；`findEnemyDirectionImpl`
  的打分只有兵种威胁 + 星车 + 血量。⇒ 移植候选明确，但**无一可在本库直接验证**。
- **独立复现对方结论（两条血缘互证）**：
  * 复刻其 aim-threat 到本树（`aimThreatBaseWeight`，直射 +3W / 拆环 +2W / 防守模式 10 格内）：
    **Δ +0.0016（p=0.81）干净 NULL** —— 与其文档自述的 fresh A/B（−0.19pp NULL）一致，
    反证其 run2→run3 的 +2.19pp 确属顺序/噪声（文档自注"归因存疑"是对的）。
  * 其"Hunt 追击移动敌人普遍震荡（~15 次/局）"：本树实测 **25.6 次/局**（navigate 3424 拍/局、
    其中站桩 302 拍），最差 Final Redoubt 52 / Spider 50 / Brick Maze 49。**问题成立**。
  * 但其"因 target 换格 ⇒ A* 每拍重算"这条根因，**本树加了目标格粘性（8/15/30 三档）全部负向**
    （−0.004 / −0.006 / −0.005）⇒ 每拍重算是自适应的，不是纯抖动；与其"拦截狩猎三版全负"互证。
- **仍未移植的唯一正向信号**：坦克墙 GOD_TANKWALL（其 +1.24pp, p=0.0927）。本树 `findPath`
  只有地形代价（外加一个给护卫避让玩家用的 blockedCell），**没有把敌坦克计入击破代价** ⇒
  需要其源码才能可靠移植，不建议凭描述重写。
- **收敛判断**：两条独立血缘在"执行层 knob 已穷尽"上结论一致（其 combat-tuning 13 方向
  1 winner/13 证伪；本审计 12 次 A/B 全 null/负向）。⇒ 下一步是**架构层**（其文档所述
  R46/重写选 B），继续在 knob 上投入的边际收益已接近 0。

## §2026-10-20-godai-mechfix-carvefire — 机制修复（非调参）：carveFire 的钢墙过度否决 + 道具候选不破砖

> 用户政策（2026-10-20）：「机制修复类，就算胜率下降也要做，做完研究为什么下降，再针对原因调优」。

- **缺陷 1 · carveFire 拿整条射线的钢否决眼前的砖**：`bs.steel` 表示**射线某处**有钢。
  钢在 9 格外时，眼前 (x,y-1) 的砖也被拒绝击破 ⇒ 坦克对着砖墙原地卡死（外部研究在 s13
  的 `(9,23)` 记录过 469 拍 stuck）。
  **本树实测**（35 关 × 6 seed）：**1.59% 的全部 tick**（18 184 拍 / 210 局 ≈ **87 拍/局**）
  命中该否决，其中 **13 201 拍本可开火**；分支分布 navigate 5645 · dodge 4467 ·
  baseConnectClear 3936 · t2a 2794。
  **修复**：新增 `brickBeforeSteelImpl`——从坦克前沿逐格走，**先遇砖** ⇒ 这一发打的是砖，
  放行；先遇钢/基地/边界 ⇒ 仍然拒绝。保留 `baseWall`/`baseSteel` 保护不变。
- **缺陷 2 · 道具候选不破砖**：`commitPowerupTail` 只调 `shouldFireInDir`，路被砖挡住时
  既不挖也不走，原地打转（外部研究 s34 `(22,21)` 取证）。**修复**：改用已修复的 `carveFire`
  （能走就正常射击，走不动且眼前是砖就破砖；carveFire 内部仍挡 baseWall/baseSteel）。
- **结果（机制修复，非 A/B 挑选）**：
  | 口径 | 修复前 | 修复后 |
  |---|---|---|
  | hard 60-seed SUITE | 0.5748 | **0.5793** |
  | hard 60-seed 胜率 | 79% | **80%** |
  | Battlement（最弱关） | 0.339 / 32% | **0.479 / 40%**（最弱关易主为 Riverbed 0.373/43%） |
- **过程中被否掉的一次调优（留档）**：20 seed 下加上缺陷 2 后 SUITE 掉到 0.5713、Battlement
  40%→25%，一度怀疑「为拿道具在基地防护砖墙上挖洞」。加 `powerupCarveBaseBand` 基地带守卫
  复测无改善；**60 seed 干净对照（`--params` 开关破砖）得 0.5791 vs 0.5792 = 完全等价**
  ⇒ 20-seed 的崩盘是噪声（单关 20 局 SE≈11%）。守卫与参数已删除。
  **纪律：单关 20 局不足以判断，Battlement 类结论必须 60 seed。**
- **新纪元 60-seed 三难度**：hard **0.5791 / 79%** · classic **0.7299 / 89%** ·
  chaos **0.5449 / 76%**（上一纪元 0.5748/79% · 0.7330/89% · 0.5343/75%）。
- **重标**：`det-golden` `ed6dc0f9…` → `820d25be…`（21 组合全变，行为改动所致）。
- **测试**：`tests/carve-fire-steel.test.ts` 3/3（砖在钢前放行 / 钢在眼前拒绝 / 畅通时不空放）。
- **门禁**：`bun run check` **2594 pass / 0 fail** · `build` 过 · `freeze:check` 绿。
- **未做的同类修复（待下一轮）**：坦克墙（A* 把敌坦克当可击破的墙）、planCell 语义
  （shift 检测用 floor、重规划起点用 round）、dig-compare（绕路 vs 挖砖的 tick 级比价）。

## §2026-10-20-godai-mechfix-tankwall — 坦克墙（机制修复，已发货）+ planCell 与 dig-compare 的核查结论

> 承接 §2026-10-20-godai-mechfix-carvefire 的机制修复清单（#1 坦克墙 / #2 planCell / #3 dig-compare）。

- **#1 坦克墙（SHIPPED，`navEnemyCellCost=2`）**：A* 只算地形，**完全不知道敌车存在** ⇒ 路径
  穿过敌车格后玩家被顶住。**本树实测**（35 关 × 6 seed）：**0.43% 的全部 tick（24 拍/局）**
  请求方向的前方格被敌车 footprint 覆盖且地形可走；分支 navigate 4165 · t2a 367 · aggressive 288；
  其中连续 ≥5 拍的「原地顶牛」累计 664 拍。
  **修复**：`PathConstraints.enemyCells/enemyCellCost` —— 敌车 footprint 按**可击破的墙**计价
  （每格 +2 cells），A* 因此在「绕远」与「打穿」之间按 tick 比价；两条 A* 路径（corridor / dig）
  都带上。
  **后果（hard 35×60 配对）**：SUITE **0.5791 → 0.5825**、胜率不变、**1625/2100 局完全相同**
  （只有 ~23% 的局受影响），**0 关显著倒退**。⇒ 机制正确且不伤人，开为默认。
  新纪元 60-seed：hard **0.5828/79%** · classic **0.7276/89%** · chaos **0.5451/76%**。
- **#2 planCell 语义（量到缺陷，但修复被证否 → 回滚）**：本树 `navigateTowardsImpl` 的缓存键用
  `playerCell()`（round），车身跨半格就翻格。**实测 round 翻格 321.9 次/局，其中 99.3%
  （319.8 次/局）是 floor 未变的「过半格假翻格」**，其中 11.8 次/局紧接着首步反向。
  按 v2 语义改（**缓存键用 floor、A* 起点仍用 round**）后 **hard 60-seed 0.5791 → 0.5556（−0.024）**。
  **下降根因**：本树的 `navigateTowards` **只返回首步**（没有带索引的 path following）⇒ 缓存键
  比 A* 起点更"稳"时，返回的首步会落后一格 —— 这正是对方 v1 踩过的坑（其 v1 −7.1pp）。
  对方 v2（shift 用 floor + 重规划起点用 round）**净收益 ≈ 0**（其自述 −0.15pp）。
  **结论**：缺陷真实但不是"键用错格"，而是"没有路径跟随"；正确修法是引入 `_navPath + index`、
  按 floor 推进索引，属结构性改动且**预期收益≈0** ⇒ 暂不做，已回滚（golden 不受影响）。
- **#3 dig-compare（无需移植）**：本树 A* 已有 `navBrickStopCost: 2`（砖边按格计价）+ fire-stop
  模型（`navFireStopModel`），绕路与挖砖本来就在同一 tick 量纲上比价 ⇒ 对方那条属于"补齐口径"，
  本树不存在该缺陷。
- **门禁**：`bun run check` **2609 pass / 0 fail** · `build` 过 · `freeze:check` 绿（`ba02a9eb…`）。

## §2026-10-21-godai-a9-next-directions — A9 之后的三条候选方向：全部核验完毕（2 负 1 无杠杆）

**结论先行**：三条候选都做了实测。① 横向车道封线 **−1.0pp（负向，已回滚）**；
② 封线距离 14→10 **+0.57pp（n=700，噪声内，不发货）**，自适应 baseHp 版本未做；
③ 命中率侧**没有决策杠杆**（结构决定：硬难度开火免费、瞄向由对齐唯一确定）。

### ① 横向车道（`left`+`right` = 致命弹 60%）：负向，已回滚

按「射手在基地行上」扩展 `baseLaneSeal`：横向封位 = 环外一格 `(bc−2, br)` / `(bc+3, br)`
（环格 `bc−1` / `bc+2` 是 `isBaseProtectionCell`，`findPath` 拒绝路由上去，即使已被打空）。

| 版本 | hard 35×20 配对 |
|---|---|
| 仅纵向（已发货） | **+0.71pp**（568/700 vs 563/700） |
| 纵向 + 横向 | **−0.29pp**（562/700 vs 564/700） |
| 差值 | **≈ −1.0pp** |

**为什么亏**：横向车道只能站在「环外、基地行」那几格封 —— 等于把玩家钉在离鹰几格的开阔地
对枪；而横向的基地射门多数是从基地带内发出的，`baseLaneSentry`(850) 本来就管。纵向不同：
环砖正上方那格是天然的咽喉，站上去既挡弹又拿到整条列。
⇒ 代码里留了明确的「别再重新加」注释，测试也已回滚。

### ② 封线距离（`baseLaneSealRange`）：14 → 10 是 +0.57pp，但只有 700 局

| 臂 | win | kills | score |
|---|---|---|---|
| range 14（发货值） | 81.3% (569) | 12945 | 0.8175 |
| range 10 | **81.9% (573)** | 12979 | **0.8216** |

Δ+0.57pp（±0.61, t=0.94）、+11/−7 对、score +0.0041（t=0.98）—— **三个指标同号但都在噪声内**，
且 14 这个值有 35×60 的三难度证据、10 只有 700 局 ⇒ **不发货**，只记录为下次调优的起点。
（顺带发现：同一配置在不同进程里的胜率会差 ±1 局 —— `world.genId` 是进程级全局计数器，
按 A/B 臂的**运行序号**影响 id 平局判定。所以 700 局里 ≤1 局的差异是噪声地板。）

「按 baseHp 自适应 range」没有实现：它的前提是「基地满血时不值得跨图去封」，但 `canShootBase`
**本身就意味着该列的环砖已被打穿**（环完好时子弹只会打到环砖）—— 也就是「封线」只在环已破
时才触发，天然已经是「该急了」。再加一层 baseHp 闸门没有机制依据。

### ③ 命中率侧（≥6 格未命中）：没有决策杠杆

700 局审计的数据：命中 60.2%、64 发/局 = **射速上限的 86%**（hard 无同屏弹数上限），
命中损失集中在 ≥6 格，主因是敌人**不可预测的转向**。结构性结论：

1. **开火在 hard 是免费的**（无弹数上限、冷却按时间）⇒ 「少开火以提命中」不可能赚 ——
   删掉一发只是在减少样本，不会把省下的资源换成别的收益。
2. **瞄向由对齐唯一确定**（`shouldFireInDir` / A2 的提交朝向门），没有「瞄得更准」的决策位。
3. 唯一剩下的变量是敌人的下一步，而它的转向由 `TacticalIntelligence` 的随机流决定 ——
   想提升只能**学习/预测敌方 AI**，那是另一条工程线，不是本轮的执行层调参。

⇒ 本方向关闭。若后续要做，正确的入口是「敌方转向策略的可预测性审计」（先测敌人下一步
方向的条件熵，再决定值不值得建预测器）。

### 门禁

仅代码注释 + 本条目（横向实现与测试已回滚到 A9 提交态）：`bun run check` 2613 pass /
0 fail · `freeze:check` 绿（`ab119977…` 不变 —— 注释不改行为）。

## §2026-10-27-godai-off-knobs-census — 37 个历史 OFF 旋钮普查：24 个真开过、8 个是死开关、0 个能翻正

**结论先行**：按 `ARCHIVED_KNOB_GROUPS` 的文档开启值把 37 个旋钮全部跑了一遍
（hard 35×4 = 140 局 CRN 配对，可疑项再上 60 seeds）。与 A28 那批不同：
**历史判词这次基本正确**——没有任何一个 OFF 想法在 60-seed 上翻正
（carvePath +0.71@140 → −0.86@2100；actionContractMode +1.43@140 → −0.52@2100）。
真正的发现是 **8 个旋钮从来没被真正打开过**（0–2/140 局变化）。

### 一、死开关（不是想法无效，是代码路径没走到）

| 旋钮 | 死法 | 证据 |
|---|---|---|
| `enemyModelMode` | ① `enemyModelWindowTicks` 出厂 0，而更新点与所有消费方都要求 >0；② `reset()` 顶部用**自适应前**的 `this.params` 解析 `_enemyModel.active`（同一函数里先读后写） | 0/350 → **已修，23/350** |
| `defensePosStandable` | **0 个读取点**（A8 `defenseCellResolve` 无条件取代） | 0/140 |
| `playerHpAwareness` | 只在 `dodgeHorizonCommit`（M9，默认关）内部被读 ⇒ 寄生在输掉的父机制里 | 0/140 |
| `defenseInterceptPredictCells` | 判据被 §134 包含（能进预测窗的敌人早已 `enemyCanShootBase`） | 0/140 |
| `defenseInterceptDigBricks` | 预测窗 ≤3 格 ⇒ 挡道砖必然是基地环砖 ⇒ `!scan.baseWall` 恒假 | 0/140 |
| `suicideReturnMode` | 五条件合取实测 **0.02 tick/局**（「弹打我」∩「弹打基地」几乎互斥） | 1/140 |
| `dodgeCentroidMode` / `dodgeClearanceScore` | ≥2 弹同域才生效，且解与默认 dodge 同向 | 0/140 |

### 二、修复（唯一的代码改动，默认行为不变）

- `GodAIInput` 构造函数：`enemyModelMode > 0 && enemyModelWindowTicks <= 0` ⇒ 补
  `ENEMY_MODEL_DEFAULT_WINDOW_TICKS`（120）。`reset()` 保留同一条兜底（覆盖阶段自适应）。
- 修后 `enemyModelMode:1 + tierWeightScale:1` = **23/350 局变化、胜负中性**
  （+0.00pp / kills 0 / suite +0.004）⇒ 机制真的在跑，只是不赚钱。
- `params.interface` + `ARCHIVED_KNOB_GROUPS`：`defensePosStandable` 标 **RETIRED**
  （0 读取点，勿再接线）。
- 新增 `tests/godai-enemy-model-wiring.test.ts`（3 例：mode 自启窗口 / mode 0 惰性 /
  显式窗口优先）。

### 三、真负向（重新确认，含剂量修正）

`candidateMode` −13.6pp · `firingLaneMode` −18.6pp · `baseGuardAnchorMode` −9.3pp ·
`pathTargetMode` −8.6pp · `huntCommitTicks` −6.4pp · `intentMode` −5.7pp ·
`coverageMode` −4.3pp · `brickHeavy*` −4.0pp · `dodgeHorizonScore` −3.6pp ·
`midLaneHold` −2.9pp · `evasionSteelOcclusion` −2.9pp · `direItemMode` −1.4pp ·
`defenseBreachBonus:300` −1.1pp · `defenseInterceptPredict6+Dig` −1.1pp。

### 四、门禁

`bun run check` 2626 pass / 0 fail · `bun run build` 过 · `freeze:check` 绿
（`a492627b…` 不变：新代码只在 `enemyModelMode > 0` 时生效）。
普查全文：`docs/godai-off-knobs-census.md`。

## §2026-10-27-godai-battlement-riverbed — 两关顽疾尸检：败因是"已开通的中路列 + 远距狙击"，预封线未过关（默认仍 1）

**结论先行**：Battlement / Riverbed 的失败 **100% 是丢基地**（hard 60 局：30/25 负，
0 局命数耗尽）。致命弹不是贴脸凿穿打出来的：丢基地那一刻基地 3 格内敌人中位数 **0**，
发射点在 **row 2–5**（Riverbed 11/17、Battlement 8/19），方向 `down`，沿**敌人自己挖通的
中路列**飞 19–22 格（≈80 拍）。两关每局被敌人拆掉基地列 12.8 / 18.4 块砖（9.6 块在
row 0–15），玩家自己只拆 0.2 / 1.8 块。

**防线为何空转**：`enemyCanShootBase` 占 **9.9%** tick、`isBaseUnderThreat` 14–33%，
但 `baseLaneSentry` 只占 **0.0–0.2%**、`baseLaneSeal` 1.5%。两者都按**到射手**的距离
设 leash（6 / 14 格），20 格外的狙击手在包线之外；sentry 的站位分支还写死 `!bestCsb`。
即：**现有防线是"射手包线"，而威胁是"已开通的列"**。

**试过的修法（`baseLaneSealMode: 2`，实现保留、默认关）**：leash 改为"到封线格"，
并把"敌人站在通透列上"纳入目标。35×60：

| 难度 | Δ胜率 | t |
|---|---|---|
| hard | **−0.86pp** | **−2.09** |
| classic | −0.05pp | −0.33 |
| chaos | +0.52pp | +1.29 |

宽松触发版曾给出硬数据：全局 0.00pp、**Riverbed 58→65（+7pp）**、Battlement 50→50，
但 **Brickworks 88→78 / Twin Towers 98→90** —— "把击杀让给守线"的等价交换，不是免费收益。
变体（基地受损才封 / 只封朝基地挖的 / sentry range 10）在 4 关上都回到 −0.8pp 量级。
⇒ **不发货**，`baseLaneSealMode` 默认保持 1。

**下一步（本轮未做，已量化）**：① `fence` 会把环换成钢 15s、每局已拾取 0.42–0.57 次，
但拾取即生效 —— 把 fence 的**路线优先级**接到"环已破/列已通透"上，比让玩家堵枪眼便宜；
② 贴脸 csb（Battlement 7/19）需要一条"先对齐再打"的最短动作，现有候选全部短路；
③ Riverbed 只有中路一条故事线，①若成立收益应集中在这里。

**实现**：`BaseLaneSeal.ts` 新增 `evalPreSeal` + `columnClearToTop`（mode 1 函数体逐字未动）；
新增 `baseLaneSealCellRange`（14）；`tests/godai-base-lane-preseal.test.ts` 4 例。
`freeze:check` 绿（默认签名不变）· `bun run check` 全绿。
尸检全文：`docs/godai-battlement-riverbed.md`。

## §2026-10-27-godai-a9-preseal-steelgate — A9 mode2 预封线「只在无钢中路」发货：Riverbed 58%→65%，hard 86.1% / chaos 83.2%

**用户裁定**：预封线只用于**中路没有钢铁障碍**的关卡。成立，已发货。

### 一、为什么这个门是对的

钢对敌人**永久**（只有 ★3 玩家能击穿）⇒ 有钢的列根本不存在"狙击列"，预封线在那里的
唯一作用是把玩家从战场拽到一条打不通的列上。35 关基地两列（c12/c13, rows 0–23）的钢数：

```
s8 Riverbed 0 · s14 Steel Web 0 · s21 Checkers 0 · s34 Battlement 0
其余 31 关 2–24 块（Iron Curtain 24 / Ramparts 22 / Brick Maze 12 …）
```

宽触发版亏得最惨的 **Brickworks（钢 4）−10pp / Twin Towers（钢 8）−8pp** 正好在门外。

### 二、两个实现细节（都踩过，写进注释）

1. **门必须「回落」，不能「短路」**：第一版在门不通过时 `return false`，等于在 31 关
   把已发货的 mode-1 封线一起关掉 —— 于是 Brickworks −4 / Twin Spires −6 / Brick Maze −6
   全是"丢了 mode-1"的假损失。改成门不过就继续跑原函数体后，31 关逐关字节一致。
2. **关卡级、不是列级**：只挡"当前这一列"时，另一列照样漏（Twin Spires −6 / Brick Maze −6）。

### 三、实测（35×60，真实 stageIndex、CRN）

| 难度 | Δ胜率 | t | kills | suite |
|---|---|---|---|---|
| hard | **+0.14pp** | 0.43 | +60 | 0.6909 → 0.6947 |
| classic | 0（`CLASSIC_OVERRIDES` 仍 restore 0） | — | — | — |
| chaos | **+0.71pp** | **2.35** | +214 | 0.6435 → 0.6556 |

逐关（hard）：**Riverbed 58%→65%（+7pp）** · Steel Web 88→87 · Checkers 82→82 ·
Battlement 50→50（mean 0.571→0.573）；其余 31 关逐关字节一致（133/2100 局变化）。
2100 局的 +0.14pp 是"4 关的账 ÷ 35"，统计上是噪声 —— **价值在 Riverbed，不在均值**；
chaos 的 +0.71pp(t=2.35) 是附带真信号。

新纪元基线：**hard 86.1% / 0.6976 / 0.8541** · **classic 88.3% / 0.7321 / 0.8730** ·
**chaos 83.2% / 0.6548 / 0.8344**；golden `a492627b… → ea915876…`。
`GUARD_GOD_AI_PARAMS` 无基地 ⇒ 该候选不参与；classic 走 override 0。

**门禁**：`bun run check` 全绿 · `build` 过 · `freeze:check` 绿（新钉）。
新增用例：钢门（左列一块钢 ⇒ 预封线不接管、回落到 mode-1 行为）。
尸检与追加分析：`docs/godai-battlement-riverbed.md`。

## §2026-10-27-godai-seal-cell-rule — 封线格"不必最近基地"：几何成立、行为更差（默认仍 = 最近鹰）

**用户提出**：只要在那条走廊上，任意一格都能挡住炮弹，去**最快到达**的点就行，不必钉死"离基地最近"。
**几何上完全正确**（车身 16px、炮弹 6px，射手与鹰之间任意一格都挡得住），但三种实现全部更差：

| `baseLaneSealCellMode` | Riverbed | Battlement | 全局 hard | kills |
|---|---|---|---|---|
| **0 = 最近鹰（发货）** | 67% | 50% | — | — |
| 1 = 最快到达（任意格） | **57%** | **43%** | −0.29pp (t=−0.95) | −115 |
| 2 = 最近鹰 + 走廊内即驻守 | 63% | 45% | — | −58 |

**根因（探针，Riverbed 40 局）**：

```
最近鹰：封线 2.6% tick（驻守 0.0%）· 基地每局掉 53.9 血 · 0.33 死
最快格：封线 8.0% tick（驻守 1.1%）· 基地每局掉 63.3 血 · 0.15 死
```

1. **封线的代价几乎全是走路**：驻守比例恒 ~0–1% ⇒ 它实际是"移动"候选而非"站桩"候选；
   选最快格只是把 5% 的 tick 从"打架"挪到"走路"。
2. **基地掉血反而更多**（54→63）：走位期间既不挡弹也不输出。
3. **最近鹰那格是回防位**：在口袋内、有环墙挡侧射、本来就是玩家想回的地方 ——
   顺路 + 有掩护 + 让炮弹飞最远（给还击留最多时间）。走廊中段是一次纯暴露的横向远征。

⇒ 默认保持 `baseLaneSealCellMode: 0`；模式 1/2 作为负向记录保留（代码注释里带数字）。
新增用例：同一几何下 0 走位 / 1、2 驻守（三条断言）。
门禁：`bun run check` 全绿 · `freeze:check` 绿（`ea915876…` 不变，默认 0）。

## §2026-10-27-godai-seal-footprint — 封线"从来不停"的真因：车是 2×2、选格只查 1 格（修好更差，默认仍 0）

**用户质疑**："到达点位就停住开火了，还移动干什么？" —— **它想停，但停不下来**。

### 一、真 bug

`TANK = CELL * 2 = 32`，一辆坦克占 **2×2 格**。封线选格的 `standable()` 只检查 1 格：
Riverbed 目标 (12,22) 单格为空，但 2×2 落脚区 (12..13, 22..23) 撞上环砖 (12,23) ⇒
**该格根本站不进去**。逐拍追踪：坦克在 (12,21) y=336 顶着墙按了 25+ 拍 `down`，
`canMoveDir('down')=false`，`onStation`（要求 row=22）永不成立 ⇒ 候选永远在"走"。

### 二、修法（mode 3）与效果

2×2 落脚判据 + 同列直线走（A* 每拍 tie-break 导致"下/左/右"交替，每次转向 12 拍、净位移 0）。
驻守率 **2% → 30%**（Riverbed）/ 14%（Battlement）—— 终于真的停下来打。

### 三、但胜率更差（4 关 × 60 seeds，mode0 vs mode3）

Riverbed 67→**60** · Steel Web 87→87 · Checkers 82→**75** · Battlement 50→**45** ·
kills −107 · score −0.0344（**t=−2.36**）。承诺窗口 120/240 拍在 mode0 上只有 21–22/2100 局差异（−0.10/−0.14pp），无效。

### 四、为什么

能站住的位置 (12,21) 就在环砖边、三面开阔：挡住一条列，却把自己钉在没有掩护的口袋口；
而"走不到"的那版虽然一直在走，方向本身是**朝家**，顺带把防守位补上了 —— 这就是它此前
86.1% 里那部分收益的真实来源。**"停住开火"不是策略选择，是它做不到；做到之后反而更差。**

结论：要么找到"既挡列、又有掩护、还能向上开火"的格子（这张图上不存在），要么放弃守走廊，
改打 `fence` 时机（环破后吃铁墙，整圈换钢 15s）。

实现：`tankFits()` + mode 3，默认 0；承诺窗口（120/240 拍）实测无效后**已删除**，不留死参数。`freeze:check` 绿（`ea915876…` 不变）。

### §2026-10-27-godai-seal-footprint · 更正（240 seeds）

上条用 60 seeds（1 局 = 1.7pp）判 `cellMode 3` 更差 **t=−2.36**，属**过重判决**。
4 关各 240 seeds（960 局/臂）复核：Riverbed 53=53 · Steel Web 85=85 · Battlement 51→52 ·
**Checkers 85→80（唯一真信号）** ⇒ 合计 **−0.5pp（t=−0.71）**，kills −85/960 局。

Checkers 解剖（240 seeds + 事件计数）：玩家被击中 0.58=0.58/局，其中**封线期间 0.01→0.00**；
基地挨弹 0.52→0.61/局；击杀 18.65→18.32/局；致命弹来源行 23/25、方向 down 主导，两臂一致。
⇒ **"两边偷家更多"与"上路来敌更凶"两个假设都不成立**；真实成本是**每局少 0.33 杀**
（站住的 1.3% tick 没把炮弹挡下来，只是少打了一点），收益几乎为零。

教训：60-seed 只能当哨兵；4 局级别的差异必须 240 seeds 才能判。默认仍 0。

## §2026-10-27-godai-seal-vs-melee — "远敌才封线、贴脸就搏击"：两条规则都实现，都中性（默认全 0）

**用户规则**：北边中路的远敌才需要封线挡弹；已经跑到基地边上的就近身搏击。拆成两个门控：

| 参数 | 含义 | 240 seeds 实测 |
|---|---|---|
| `baseLaneSealPreMinDist: 8` | 只封距基 ≥8 格的射手 | 4 关 −0.36pp（t=−0.66）：Riverbed 53=53 · Steel Web 85=85 · Checkers 85→83 · Battlement 51→50 |
| `baseLaneSentryPointBlank: 1` | 贴脸 csb 未对齐 ⇒ 迈一步到其行/列再打 | Riverbed 53→50 · Battlement 51→50，−1.67pp（t=−0.91） |

**为什么第一条没差异**：封线候选本来就排除贴脸射手（`row >= BASE_POS.row - 1` continue），
且其占用只有 1.3–3% tick、期间挡下 0 发（上一节的 0.00 被击中/局）。"别傻傻封线"系统早就在做。

**真正的空档**：丢基地前 180 拍里 **navigate 占 67.7–68.0%**；贴脸 csb 存在 3.5–5.3% tick，
其中未对齐 3.1–4.5%，而**没有任何防守候选处理"未对齐的贴脸射手"**（sentry 要 aligned、
defenseIntercept 要同行同列、midLane 等炮弹、seal 排除 row≥23）。补上这条（实测）只覆盖
1% 量级 tick，不够抵消走位损失 ⇒ 中性。

**下一步**：动 `selectTarget` 的目标优先级——基地带内（row ≥ 20）的敌人应当压过远处的追击目标；
不是继续给封线加门。两个参数默认 0 保留（可复现的负向记录）。

## §2026-10-27-godai-seal-antistuck — "贴脸还一直导航"的真因：卡在走不到的封线格上（防卡死 SHIPPED）

**用户追问**："都已经贴脸了，为什么还一直导航？直接并线背击！" 逐拍追踪 Checkers seed 8：

```
1458 p(4,17) d15 br=baseLaneSeal  sel=(1,24)  band=[]
1478 p(4,17) d15 br=baseLaneSeal  sel=(3,24)  band=[]
1517 p(4,17) d15 br=baseLaneSeal  sel=(5,24)  band=[]
```

玩家卡在 **(4,17)**（离基地 15 格）**连续 100+ 拍**由 `baseLaneSeal` 接管；目标格曼哈顿距离
正好 14（= leash），中间却是墙 ⇒ 走不到；同期 `band=[]`，基地带里根本没有敌人。
**"贴脸不打"并不存在——它不在现场，它在一个走不到的封线点上顶着墙。**

**修法**：`baseLaneSealAntiStuckTicks: 20` —— 走动中 20 拍格子没变 ⇒ 判卡死，放弃封线
120 拍，把决策交回正常链条；`reset()` 清空该状态（frame 每关从 0 开始，否则会跨关静音）。

**实测**：240 seeds × 4 关（20 vs 0）：Riverbed 53→55 · Steel Web 85=85 · Checkers 85→83 ·
Battlement 51=51 ⇒ **−0.09pp（t=−0.18）**；全局 35×60 仅 9/2100 局变化（+0.05pp）。
新基线：**hard 85.9% / 0.6933 · classic 88.3% / 0.7321 · chaos 83.4% / 0.6584**
（hard −0.2 / chaos +0.2 / classic 0 ⇒ 净中性）。

**按用户政策发货**（机制修复即使不赚也做）：这是行为正确性修复，不再"假装在封线"。
det 签名不变（`ea915876…`）——det 语料里没有"卡死封线"的样本，这是语料盲区，记一笔。
