# §302 追尾导航（pursuit-tail）— 阶段性总结与交接

> 2026-08-29 · 状态：**已收口（第三轮 AlongMode=3 完成）· 默认 OFF（`pursuitTailMode: 0`）**
> 最终结论见 §9；本文 §1–8 保留为历史交接记录。
> 来源：`plan/Intent-Policy-NN-Plan.md` §12.1 实施层缺陷 #3。只动 God AI，不涉 NN。

---

## 1. 目标与根因

**用户观察到的缺陷**：追击时玩家走**并行车道**、到敌人侧方才转向横向开火，经常打空
（敌人又前进了）。应当**并入目标车道后方**一路追射。

**代码层根因（已确认）**：`directMoveImpl`（`src/ai/god/Navigator.ts`）垂直优先——
先对齐目标 ROW。对纵向行进的敌人这恰好是反的：玩家收敛到目标的行，而目标沿自己的列持续
爬升，于是稳定停在并行车道上打横炮（`predictiveFireGate` §193-D 还会压掉其中一大半）。

`directMove` 的择向规则（`|dy| > CELL/2` 即半格为界）：
- 行差 > 半格 → 走垂直（**并行前进**，缺陷状态）
- 行差 ≤ 半格 → 走水平（转向切入 + 开火）

---

## 2. 实现现状

`pursuitTailDirImpl`（`src/ai/god/Navigator.ts`）在 `evalHunt` 里 `pickHuntMoveDir` **之后**、
§182/§85 两道安全网**之前**覆写 `_moveDir`；射击链路保持共用（覆写只改移动，不改 `_fire`）。

**调用点**（`src/ai/god/candidates/Hunt.ts` 约 610 行）：
```ts
if (!navStuck && !self._carveDigActive && self.params.pursuitTailMode > 0) {
  const tailDir = pursuitTailDirImpl(self, p, pc, navTarget, navDist <= 5)
  if (tailDir) {
    const prev = self._moveDir
    if (prev !== tailDir) { self._pursuitTailChanged++; self._pursuitTailLastPrev = prev }
    self._moveDir = tailDir
    self._pursuitTailOverrides++
  }
}
```

**模式号（线性阶梯，都保留着便于对照）**

| mode | 语义 | 状态 |
|---|---|---|
| 1 | 导航到"目标后方 1-2 格"的尾格（先补车道再沿车道） | 已测 −30 |
| 2 | 仅横向并道，车道间隙 ≤ `MaxLaneGap`(4) | 已测 0 |
| 3 | mode 2 + 车道必须能打出一发净射 | 已测 +8 |
| 4 | mode 3 + 横向通道必须畅通 | 未全量测 |
| 5 | mode 3 + 仅限近距（navDist ≤ 5） | 子集测，偏负 |
| 6 | mode 3 + 基地受威胁时抑制 | 已测 −35 |
| **7** | **相邻车道并道（当前主线）** | **已测 −39 / 拆分见 §4** |

**mode 7 的判据**（这是用户纠正后重写的核心）：
```ts
laneGap === 2                                  // 相邻车道（见 §3 的 2×2 陷阱）
&& dist ∈ [MinCells, MaxCells]                 // 距离窗（刚接线，见 §6 待办）
&& |along| ≤ AlongWindow(3)                    // 敌人正在/即将经过
&& laneShotClear(...)                          // 切入后能打出一发净射
&& (AlongMode: 0=两侧 1=仅后方 2=仅侧方/前方)
→ 横向切入敌人车道
```

**参数**（`params.interface.ts` / `params.tables.ts`，DEFAULT 全部 OFF 语义）：
`pursuitTailMode`=0, `pursuitTailCells`=2, `pursuitTailMinCells`=3,
`pursuitTailMaxCells`=9, `pursuitTailMaxLaneGap`=4,
`pursuitTailAlongWindow`=3, `pursuitTailAlongMode`=0。
`CLASSIC_OVERRIDES` 里 `pursuitTailMode: 0`（classic 字节一致）。

**诊断字段**（`GodAIInput`，均已在 `tests/godai-hub-fields.test.ts` ALLOWLIST class A 登记）：
`_pursuitTailOverrides`（触发）、`_pursuitTailChanged`（**真改变方向**）、
`_pursuitTailLastPrev`（改前方向）。

---

## 3. 三个必须知道的实现陷阱（我踩过，别重踩）

1. **覆写触发 ≠ 覆写生效。** 实测 mode 3：触发 46575 tick，**只有 45% 真改变了 `_moveDir`**，
   其余 55% 与 `directMove` 原本的选择一致 → 画面上零差别。
   → **任何**这类"AI 子分支覆写"的改动，第一件事是量 `_pursuitTailChanged / _pursuitTailOverrides`。
   只数触发会严重高估剂量，并产出看不出效果的审查材料。
2. **坦克是 2×2 格**（`TANK=32`, `CELL=16`）→ **"相邻车道"是 `laneGap === 2`，不是 1。**
   实测目标行进中的 laneGap 分布：`gap2 = 26.9%`（最大一档）、`gap1 = 7.9%`
   （车身已横向重叠，横移被 `canMoveDir` 拒绝 → 覆写变空转）。
   第一版用 gap=1，有效剂量 0.04% of ticks，等于没开；改 gap=2 后 0.72%（**18 倍**）。
3. **扫描器/诊断的目标必须读 `input._lastSelectTargetId`**（AI 真实锁定目标），
   不能用"最近敌"代理——后者会因目标摇摆（§170）在 5 秒窗口内换 5 辆坦克，数据全无意义。
   另外统计射击数必须数 `fire` 的**上升沿**（数 tick 会高估一个量级）。

---

## 4. 测量数据（口径：hard 35×60，stageIndex=0，36000 tick，基线 `baseW = 1581/2100 = 75.3%`）

SE 按 discordant 对数 √n 估算（配对 CRN）。

| 构型 | L→W | W→L | **净** | SE | t |
|---|---|---|---|---|---|
| mode 1 尾格导航 | 308 | 338 | **−30** | 25.4 | −1.18 |
| mode 2 纯横向并道 | 304 | 304 | **0** | 24.7 | 0.00 |
| mode 3 +净射门控 | 300 | 292 | **+8** | 24.3 | +0.33 |
| mode 3（MaxLaneGap=2，低剂量对照） | 251 | 278 | **−27** | 23.0 | −1.17 |
| mode 6 +基地威胁抑制 | 258 | 293 | **−35** | 23.5 | −1.49 |
| **mode 7 相邻并道（两侧都并）** | 241 | 280 | **−39** | 22.8 | **−1.71** |
| **mode 7 + AlongMode=1（仅后方＝严格追尾）** | 188 | 187 | **+1** | 19.4 | +0.05 |
| **mode 7 + AlongMode=2（仅侧方/前方）** | 188 | 246 | **−58** | 20.8 | **−2.79** |

**唯一统计显著的一条是负的**：AlongMode=2（敌人在侧方或正朝玩家逼近时切入）净 −58，t = −2.79。

微观剂量（8 关 × 15 seed 抽样）：

| 构型 | 触发率 | 真改变率 | 有效剂量 |
|---|---|---|---|
| mode 3 | 7.7% ticks | 45.0% | ~3.2% ticks（但多是远距离绕路） |
| mode 7（gap=1，**错误**） | 0.51% | 7.5% | 0.04% |
| **mode 7（gap=2，正确）** | 1.39% | **52.2%** | **0.72%** |

`alongWindow` 从 3 起完全饱和（放大到 20 触发数不变）→ 覆写**全部**发生在敌人近距离经过时，
几何上符合预期。`laneShotClear` 是实际的距离上限（远距离线路被地形挡住）。

---

## 5. 当前结论（与用户判断的共识与分歧）

**共识（用户纠正后已确认）**：
- 缺陷机制 = 相邻车道并行前进 → 侧方转向开火打空；修法 = 提前切入敌人车道。
- modes 1–6 的"隔着 2–4 格强行并线"是**错的方向**，已废。

**分歧（数据不支持用户的一处判断）**：
- 用户认为"不一定要严格限定在敌人后方，敌人正在/即将经过都适用"。
- 拆分实测：**仅后方 +1（中性）／仅侧方前方 −58（t=−2.79 显著有害）**。
  原因是切入后玩家挡在敌人去路上或与其贴身，且切入瞬间朝向是横向、要转身才能开火——
  把"侧方转向开火打空"换成了"贴身转身被撞"，更亏。
- **需要用户拍板**：是按数据限定为"仅后方"，还是保留两侧但改造切入后的动作
  （见 §6 路线 B）。

**整体**：追尾并道这个动作，即使在正确的相邻车道几何下，全量净胜率仍是**中性（+1）**，
没有任何构型取得正收益。倾向**阴性**，但建议先做完 §6 的两个实验再定论——
因为 mode 7 的有效剂量直到最后一轮才真正接对（gap=2），前面的负结果大部分是错构型贡献的。

---

## 6. 后继规划（按优先级）

### A. 把 mode 7 的距离窗真正扫一遍（30 分钟）★
`pursuitTailMinCells` 刚接线（此前在 mode 7 分支里是死参数，导致第一个 minCells=4 实验无效）。
`dist = 2 + |along|`，所以 MinCells 等价于最小追尾站距：3⇒|along|≥1、4⇒≥2、5⇒≥3。
```bash
bun tools/diag/ab-multi-param.ts --params pursuitTailMode=7,pursuitTailAlongMode=1,pursuitTailMinCells=4 \
    --difficulty hard --stages all --seeds 1-60 --json tmp/pt-ab-v7-min4.json
```
再扫 5、6。**判据**：任一档 net ≥ +2SE（≈ +40）才叫有信号，否则归档。

### B. 改造"切入之后"的动作（若 A 无信号，1 小时）★
当前切入后交回 `directMove`，它会朝目标格走——玩家在敌人前方时会掉头迎上去（贴身）。
两种改法，任选其一做成 `pursuitTailAlongMode=3/4`：
- **B1 承诺同向**：切入后 N tick 内强制保持与敌人行进同向（`_pursuitTailCommitTicks`），
  不因目标格在身后而掉头。
- **B2 只在能立刻开火时切入**：要求切入后玩家朝向（含转向 cooldown）能在敌人脱离射界前
  完成一次射击，否则不切。
两个都先在小样本（5 关 × 10 seed）看过触发率/真改变率再全量。

### C. 出新一轮录像复核（配合 A/B，30 分钟）
用 `tools/diag/pursuit-tail-scenes.ts --mode 7` 重扫（**注意**：扫描器目前的
`isLateralMerge` 判定沿用了 mode 3 的 gap 语义，需确认对 gap=2 仍成立），
挑 4-5 秒窗口 → `pursuit-tail-export.ts` 导出 → 给用户确认"这就是他要的动作"。
**这一步不能省**：前两轮都靠用户看录像才发现我的机制是错的。

### D. 收口（若 A/B 都无信号）
1. `pursuitTailMode` 保持 0，确认已在 `ARCHIVED_KNOB_GROUPS` 登记（**已登记**）。
2. 更新 `DECISIONS.md` §302（现有条目是按"阴性"写的，**需按 mode 7 的新数据重写**——
   现有条目只覆盖 modes 1–6，未包含 gap=2 的发现与 −58 的显著负结果）。
3. 更新 `docs/god-ai-tuning.progress.md` §302（同上，且 §4 微观数据表需要换成 gap=2 的数字）。
4. 三个新诊断工具保留（`tools/diag/pursuit-tail-{probe,flip,scenes,export}.ts`），
   它们对任何后续"AI 子分支覆写"类改动都可复用。

---

## 7. 工具与文件清单

| 文件 | 用途 |
|---|---|
| `src/ai/god/Navigator.ts` | `pursuitTailDirImpl` + `tailCellUsable` / `laneShotClear` / `lateralRunClear` |
| `src/ai/god/candidates/Hunt.ts` | 调用点（约 610 行）+ `_pursuitTailChanged` 计数 |
| `src/ai/GodAIInput.ts` | 三个诊断字段 |
| `src/ai/god/params.{interface,tables}.ts` | 7 个参数 + `ARCHIVED_KNOB_GROUPS` 登记 |
| `tests/godai-pursuit-tail.test.ts` | 12 个行为级单测（OFF 无副作用 / ON 只在 HUNT 追击窗触发 / 不接管锚点目标 / 不倒退 / 纯函数） |
| `tools/diag/pursuit-tail-probe.ts` | 触发率 + 射击几何 + 败因分解（口径与 ab-param 逐位对齐） |
| `tools/diag/pursuit-tail-flip.ts` | 单 stage@seed 并排时间线 + 首次分歧 ±12 tick |
| `tools/diag/pursuit-tail-scenes.ts` | 并道场景扫描（`--defect 1` 扫 baseline 并行车道缺陷；`--narrate 关:seed:起:止` 逐 0.25s 解说，含"并道?"判定列） |
| `tools/diag/pursuit-tail-export.ts` | 导出 `.replay`，每局自校验 in-process vs `runSimulation` 的 outcome/ticks/killCount |
| `tmp/s302-replays2/` | v2 录像 + `REVIEW.md`（v1 在 `tmp/s302-replays/`，已标注作废） |
| `tmp/pt7sweep.ts`、`tmp/ptgapdist.ts` | 一次性扫描脚本（剂量 / laneGap 分布） |

**常用命令**
```bash
bun run check                      # 1569 pass / 0 fail（当前状态）
bun run freeze:check               # FROZEN-SIGNATURE OK（默认 OFF 字节一致）
bun tools/diag/ab-param.ts --param pursuitTailMode=7 --difficulty hard --stages all --seeds 1-60
bun tools/diag/ab-multi-param.ts --params pursuitTailMode=7,pursuitTailAlongMode=1 --difficulty hard --stages all --seeds 1-60
bun tools/diag/pursuit-tail-scenes.ts --mode 7 --narrate 30:1:1255:1565
```

---

## 8. 纪律提醒（交接给下一位）

- 改 `src/ai/god/**` 前后必须 `bun run freeze:check`，默认 OFF 路径必须字节一致。
- 新增 `GodAIInput` 字段必须登记 `tests/godai-hub-fields.test.ts` 的 ALLOWLIST，否则 check 红。
- `World.state`='stageclear' 但 `SimResult.outcome`='stage_clear'，跨口径比较要先归一化。
- 字符串拼接里的三元表达式**必须加括号**：`a+b+c+cond ? x : y` 会把前缀整个吞掉（踩过）。
- 净胜率判据用 **±2SE**（SE ≈ √discordant，2100 局时约 ±45 净胜）。|net| 在这个带内一律视为噪声，
  不许"挑好看的那个构型"当结论——modes 1–6 的 −35…+8 全是噪声，只有 −58 是真信号。
- **阴性不等于失败，但要留下可复现的证据**：每个构型留 A/B JSON + 触发率/有效剂量，
  这样下一个人不用重跑 modes 1–6。

---

## 9. 收口记录（第三轮：AlongMode=3 等待后并道，2026-08-29，三版）

**§5 的分歧已由用户拍板解决**：侧方/前方**不能提前切入**——要稍微等待，等敌人
经过后再并道。已实现为 `pursuitTailAlongMode: 3`（yield-then-tail）：

- `along ∈ [−1, +窗口]`（并排/逼近/仅错开 1 格）→ `pursuitTailDirImpl` 返回新哨兵
  `PURSUIT_TAIL_HOLD`（Navigator.ts 导出），Hunt 调用点把 `_moveDir` 置 `null`
  （松油门；§182/§153 已有 null=hold 先例，安全网仍在其上运行）；新诊断字段
  `_pursuitTailHolds`（ALLOWLIST class A）。
- `along ≤ −2`（两格错开，2×2 车身垂直错开、横移不被卡）→ **接管整个横移**：
  gap ∈ {1,2} 都按横向直到 gap 0（wake 侧无 along 上限，由 MaxCells 收口）；
  上道后交还 directMove 纵向追击 + `shouldFireInDir` 沿道开火。
- 两格错开是**车身几何阈值**（可调参数不参与 am=3）；`along ≤ 0` 跳过
  `laneShotClear`（该函数同行时刻必 false，会让 hold 在通过瞬间闪断）；
  am=0/1/2 归档构型执行路径逐指令未动，历史 A/B 数据仍可配对。
- **像素级补丁（第三版）**：`along = −2` 是取整值，目标半格下行时其车身仍可能
  物理挡住滑行脚印——滑行被拒且拒者是目标车身 → HOLD 等其远离自行张开
  （拒绝者身份由 AABB 对目标车身验证；`laneShotClear` 已过 + `canMoveOrBreak`
  也拒 ⇒ 拦阻者只能是坦克，钢/基地砖会先死在 laneShotClear，可破砖返回 true）。

**第一版缺陷（用户录像复核抓出）**：hold 正确，但并道半途夭折——旧
`laneGap === 2` 门控在横移一格后（gap→1）交还 directMove，垂直优先把它拽回
并行追击；`along = −1` 时还尝试并道（被碰撞拒绝）。用户判定 s9@11 无"教科书
序列"、s21@8 无并道——扫描表 `入车道` 全 `n` 是铁证。修正 = am=3 自包含
状态机（本节第一条所列）。

**第三处缺陷（用户二次复核 s21@30 38–41s 抓出）**：`along = −2` 是取整值，目标
半格下行时其车身仍物理挡住滑行脚印（逐 tick 诊断 `tmp/s302-diag21-30.ts`：
tgtBlk=1 / othBlk=0 / ey 抖动 153→174，持续 ~24 tick），滑行被拒后 tick 交还
directMove，垂直优先**下追把刚错开的身位又贴回去**（along −2→−1 振荡 1.5s）。
修正 = 并道相滑行被拒且拒者是目标车身（`laneShotClear` 已过 + `canMoveOrBreak`
也拒 ⇒ 只能是坦克；AABB 对目标车身验证）→ **HOLD 等像素间隙自行张开**。

**结果（第三版）**：

| 项 | 数值 |
|---|---|
| 剂量（5 关 × 15 seed） | **Δwin +6/75**、击杀 1413 vs 1358（首次反超）、aligned +223、在车道 7.28%→9.26% |
| 全量 A/B（hard 35×60） | **净 +29（319/290）= 全程序最佳**（弧线 am=0 −39 / am=1 +1 / am=2 −58 / v1 −4 / v2 +16 / **v3 +29** 随机制完整度单调改善），但 < 2SE≈49 仍噪声带 |
| `bun run check` | 1589 pass / 0 fail |
| `freeze:check` | 三轮 src 改动前后均同哈希（默认 OFF 字节一致；基线臂三次均 1581） |

**遗留材料**：DECISIONS §302 已按三版重写；progress §302 新增 §7–9；
复核录像 `tmp/s302-replays3/`（第三版 3 组 cand/base，全 MATCH ✓ + REVIEW.md；
**s21@30 结局翻转 gameover→stageclear**）；probe/scenes/export 三工具已加
`--along-mode`，scenes 支持 hold tick 识别；逐 tick 诊断 `tmp/s302-diag21-30.ts`
（tgtBlk/othBlk/dest 地形，可复用于任何"该并没并"投诉）。§6.D 各项全部完成。
