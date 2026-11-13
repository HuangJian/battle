# NNInput 推理侧规则：canonical 计划（v3.2）

状态：**v3.2（2026-11-13，已处理 `rules.review-qw.md` + `rules.review-k3.md` + `rules.review-glm.md` + `rules.review-ds.md`）· P-1b-0 已落地 + P-1b A3 实测完成（降级）**。
**P-1b-0 已落地**（`src/nn/inference-rules.ts` 谓词 + `tests/nn/inference-rules.test.ts`，12 例；未接 resolver）。
**P-1b A3 实测（2026-11-13，700 局）**：ep80 × `classic35-safe-base` × `2000002001..2020` ⇒ exposure **0.0082%**、loss **0.0017%**、trigger **93.2%**、r>K **0**、net shot **+1**；按 §4.1.4 **暴露率 <1% ⇒ 降级，不进 P0a**。探测工具 `tools/diag/a3-dose-probe.ts`；原始 per-game JSONL 在 `tmp/a3-dose-control-{0..4}.jsonl`。
**P-1b C/走位 pilot（2026-11-13，175 局）**：C1 威胁 **8.62%**、C4 安全窗 **12.84%**、E1 远敌 **36.44%**、B4 冰 **4.41%**（冰关 47–53%）；C1/C4/E1 过 5% 剂量门，B4 只做局部，C2 待 events-on 小测。工具 `tools/diag/positioning-dose-probe.ts`。
**C1 A/B（2026-11-13，175 对 screening，判负）**：control **39.43%** → rule **26.29%**，Δ **−13.14pp**；McNemar **p=0.0052**；按关 bootstrap 95% CI **[−21.71, −4.57]**；kills 14.47→13.25 ⇒ C1 v1 判负，注册表 dormant，不做确认。
**C4 A/B（2026-11-13，175 对 screening，中性）**：control **39.43%** → rule **38.86%**，Δ **−0.57pp**；McNemar **p=1.0**；bootstrap 95% CI **[−6.29, +5.71]**；clearT 11105→10633 仅次要读数 ⇒ win 无改善，不部署，C4 v1 降为 dormant。
**C2 A/B（2026-11-13，175 对 screening，未过闸）**：control **39.43%** → rule **43.43%**，Δ **+4.00pp**；McNemar **p=0.457**；bootstrap 95% CI **[−5.71, +13.14]**；事件决策 **1.38%**、每局 **+72.5** 决策、墙钟 **+14.1%**；stage 1 −80pp 触发安全线 ⇒ 不部署，C2 v1 降为 dormant。
**E1 A/B（2026-11-13，175 对 screening，判负）**：control **39.43%** → rule **32.57%**，Δ **−6.86pp**；McNemar **p=0.162**；bootstrap 95% CI **[−16.0, +2.86]**；墙钟 **+20.0%** ⇒ 不部署，E1 v1 降为 dormant。
**B1 A/B（2026-11-13，175 对 screening，过 futility gate）**：control **39.43%** → rule **46.86%**，Δ **+7.43pp**；McNemar **p=0.142**；bootstrap 95% CI **[−1.71, +16.0]**；机制 **1,410** 个顶钢发射被抑制到 **0**，control 与旧 probe 逐字段 parity **0 mismatch** ⇒ **过 +5pp 闸，待 20-seed 确认，未部署**。
**B1 确认（2026-11-13，700 对，seeds `2000003001..3020`）**：control **36.43%** → rule **42.43%**，Δ **+6.00pp**；McNemar **p=0.0052**；bootstrap 95% CI **[+2.57, +9.57]**；机制 5,630→0；stage 19 安全线未复现（20 新 seed +15pp）⇒ **确认通过**。
**B1 接线 + parity（2026-11-13）**：已接 `tools/sim/export-eval-game.ts`（`--skip-steel-only-shots`，默认关）；B1-off 与 HEAD `af253ba8` 在 stage 0/7/31 的 `_eval_report.json` **3/3 SHA256 逐字节一致**；B1-on stage 0 仅多 `inferenceRules.skipSteelOnlyShots=true`，stage 19 行为改变（kills 2→5）⇒ 门生效。**训练链（rl-rollout/dagger-beta）保持 off；未部署。**
**部署面命名（2026-11-13）**：规则 ID（B1/A3/C1…）只作历史实验索引；**配置/CLI/报告/节点参数一律用英文描述名**。当前映射：`B1 → skip_steel_only_shots`（`--skip-steel-only-shots`、`skipSteelOnlyShots`、`skipSteelOnlyShotsSupport`）。
**B2 A/B（2026-11-13，175 对，两臂 B1 开，判负）**：control **46.86%** → rule **43.43%**，Δ **−3.43pp**；McNemar **p=0.545**；bootstrap 95% CI **[−12.0, +5.14]**；kills 15.41→14.90 ⇒ **win 无改善，不确认/不部署；B2 v1 降 dormant**。
**B4 A/B（2026-11-13，冰关 80 对，两臂 B1 开，判负）**：control **53.75%** → rule **45.00%**，Δ **−8.75pp**；McNemar **p=0.337**；bootstrap 95% CI **[−22.5, 0]**；反向请求 1,992→29 ⇒ **不部署；B4 v1 降 dormant**。
未开课、未 commit。本文件是唯一 canonical 版本，旧 §0–§8 的冲突内容已删除，不再追加「修订补丁」。
证据等级：**A** = 实测读数；**B** = 代码逐行复核；**C** = 推断（附可证伪预测）。

> **v3.2 的 3 个 P0 + 批量 P1（ds 评审，本轮）**
> ① **§8 集成拓扑再修**：真实 v6 接线面 = `NNInput` 类 + `export-eval-game` + `export-rl-rollout` + **`export-dagger-beta`（训练标签链，v3.1 漏了）**；`export-nn-replays` / `record-games-video` 是 **legacy 5/2**，无法表达 `fire=2`，移出接线面；源码门禁改为「v6 正向白名单 + train 链反向禁止」。
> ② **§4.2 统计**：主检验 = cluster bootstrap CI 不含 0；`+3pp` 是**政策门槛（点估计）**，明确「真实效应=3pp 时点估计门槛功效≈50%，加 n 不可修复」⇒ 加 **futility gate**（pilot 点估计 ≥ +5pp 才准冻结确认）；abort 方向改为 `零边际 MDE > 3pp ⇒ 加 n 或杀`；`MDE` 与 `+pp` 分开报告。若必须证明「≥3pp」，改用边际检验（CI 下界 > 3pp）并按其 `MDE_margin = 3pp + 2.80·SE` 另算预算（当前预算不采用）。
> ③ **§0/§5 拆 `P-1b-0`**：纯谓词 + 单测先落地并冻结，再做 P-1b 测量，最后 P0a 接线——解除「P-1b 依赖 P0a」的循环。
> ④ **P1 批量**：`usefulFire` 钉 ±19px 车体带 + 复用 `blocksBullet` / `laneOccluded` + 钢恒阻挡；剂量拆 gate-ready/blocked、净弹数差改窗口局部反事实；DoD 补 v6+rules-off 与谓词矩阵；P-1b 钉 checkpoint 协议；家族/安全线给数值。
>
> 历史：v3.1 的 glm 四修正仍有效（下方保留原块）。

> **v3.1 的 4 个 P0 修正（glm 评审，历史）**
> ① **§8 集成拓扑写错**：`new NNInput` 只有 7 个构造点；四大导出器各自 `buildModelFromText` + 内联 `InputLike`；`export-dagger-labels` 是标签链（`chain='train'` 恒关）。resolver 要接 **9 处同一函数**，不是「6 条 mover 同改」。（v3.2 再修正：真实接线 = 4 处代码；`export-dagger-beta` 补入，`export-nn-replays`/`record-games-video` 判 legacy 移出。）
> ② **P-1b 判据丢失**：恢复 k3 三层剂量判据表并给数值；探针版 `usefulFire` 必须与 P0a **同一实现**（版本号冻结）。
> ③ **A3 剂量口径缺 regime**：0★ 射击间隔 ~74 拍、转向冷却 12 拍、K=10 ⇒ r≤K 是「换发」不是「补发」。按 r>K / r≤K 分层，补净弹数差 + 新朝向首发延迟。
> ④ **统计功效口径错配**：pilot 按 McNemar 估 D、主检验却是 cluster bootstrap。改为 **cluster-aware 功效**；`frozen +pp > MDE ⇒ abort`；`+pp` 必须附 MDE 推导。

---

## 0. 结论与执行顺序

**方向合理，但先别开规则评估。** 按下面顺序，先修文档与测量，再写 A3 工程与测试，最后才烧评估预算。

| 阶段 | 内容 | 出口 |
|---|---|---|
| **P-1a 文档** | 本 canonical 版（已完成） | 单一状态、可执行 |
| **P-1b-0 谓词** | `src/nn/inference-rules.ts` 只落纯谓词：`usefulFire`（±19px 带 + 复用 `blocksBullet`/`laneOccluded`）+ `turnWillCommit` 复用 + 谓词单测；不接 resolver、不加开关、不做 parity | 谓词版本冻结（写进本文件 §4.1.5）— **已完成：`u1-static-lane-2026-11-13`，12 tests green** |
| **P-1b 测量** | 用冻结谓词跑 control 基线 + 埋点核对 + 单局耗时 + A3 三层剂量（r>K/r≤K，拆 gate-ready/blocked）+ C 族/走位族暴露率 + G1 覆盖率；**不跑 rule 臂** | **A3 已完成：exposure 0.0082% ⇒ 降级**；C 族/走位族待测 |
| **P0a A3 工程** | `fireIntent + resolveFireAction` + §7 的 16 条单测 | **暂不执行（A3 闸未过）** |
| **P0b A3 评估** | 筛选 pilot（futility gate ≥+5pp）→ 冻结 → 独立确认（α=0.05 + +3pp 政策门槛） | **暂不执行（A3 降级）** |
| **P1 B1** | 同一 resolver 的 6px 顶钢门（先保守版） | 单条确认后才准组合；**A3×B1 析因已取消** |
| **P2 B2** | 拆砖收益；与 B1 单条析因 | **v1 判负（−3.43pp, p=0.545）⇒ dormant**；B1×B2 组合不再做 |
| **P3 其余** | C2/F1/A2 arm/D3/E1/C1/C4/B4（D1 只作诊断） | C1/C2/C4/E1/B4 均判负/中性 ⇒ dormant；F1 只作诊断 |
| **暂不做** | backlog/dormant/deleted（§3） | 防休眠/no-op 烧预算 |

---

## 1. 事实基线与边界（B）

### 1.1 引擎事实（已亲自复核）

- `hard = DEFAULT_RULES`（`rules.ts:344`），`fireModel:'cooldown'`（`:202`），`turnCooldownMs=200ms≈12 拍`（`:257`）。
- v3 fire：`fire=1` 在写 dir 前 `tryFire`（沿 committed facing）；`fire=2` 在 `turnAccepted` 后 `tryFire`，**被拒转向整发跳过**（`SimulationPlayer.ts:88,114-116`）。
- 转向冷却只在接受转向时更新 `lastTurnMs`；拒绝不更新 ⇒ 同一请求在窗口内逐拍重试（`SimulationCombat.ts:120-138`）。
- `tank.moving=false` 在拒绝转向时被强制写入；A2「沿旧方向走」不是免费收益。
- 敌对子弹 AABB 相撞双方同灭（`SimulationCombat.ts:869-889`）⇒ 朝钢弹可拦截敌弹，B1 不能一刀切。
- 0 星玩家/所有敌人 `bulletPower=1`，钢只被 `isPlayer && level>=3` 打穿（`combat.ts:364-365`）；safe-base 面板基地不可摧毁。
- `computeMasks` 的 fire 只表达 cooldown ready；两个 fire 槽共享同一 ready 位，改 mask = schema bump（`obs-encoder.ts:948-967`）。
- 敌全灭且无存活道具 ⇒ 立即 `stageclear`；有道具 ⇒ bonus window（`SimulationEffects.ts:222-267`）。
- 过期道具会 `alive=false` 并被 cleanup；评估看到的道具没有「已过期仍在场」（`SimulationPowerUps.ts:341-348`）。
- 当前 `NNInput` 只在决策点 forward，getter 只读 committed bool（`policy-input.ts:105-106,174-187,278-330`）。
- **策略在决策点已经能看到转向冷却剩余**：scalar `s[5] = clamp01((turnCooldownMs - elapsed)/turnCooldownMs)`（`obs-encoder.ts:575-583`）。⇒ A3 不是「补网络看不见的信息」，而是「补引擎在 `fire=2` 被拒时的整发丢弃」；训练侧本有条件学会规避，但零训练规则仍是合法补丁。**P-1b 实测已确认**：ep80 上 99.5% 的 fire=2 换向发生在 `r=0`（见 §4.1.8）。

### 1.2 不可动

- 动作空间语义：v6 三头正交、24 组合全合法（用户 2026-10-20 裁定）。
- 任务侧：lives / enemies / 关卡形态 / 终局 / max_ticks。
- `computeMasks` / obs / scalars / extra 的 schema。
- `turnCooldownMs`（人类同规机制）。
- 教师表现不得用于判可学性、定阈值、评难度、设计梯度。
- 仓库红线：**禁止推送**；部署只 commit。`lastDir` 类源码门禁；跨 tick 私有玩法状态须先 DECISIONS。

---

## 2. 核心设计：fireIntent + resolveFireAction（A3 的正确实现）

### 2.1 两层接口

```ts
type FireIntent = 0 | 1 | 2

// decide() 调一次：只从 logits/mask 后选意图，不读未来
selectFireIntent(...): FireIntent

// 每 tick 调一次：纯读 World，把意图解析成 packed number
// bit0 = firing, bit1 = fireBeforeTurn, bit2..4 = reason (0 intent1 / 1 after-turn /
// 2 a3-fallback / 3 suppressed / 4 gate-blocked)
resolveFireAction(world, player, intent, requestedDir): number
```

- **不分配对象**：`SimulationPlayer` 每 tick 调 `isFiring()` 与 `isFireBeforeTurn()` 各一次 ⇒ resolver 每 tick 跑 2 次；返回对象 = 2 次分配/tick，违反 AGENTS §14.2。packed number 或两个标量 getter 均可。
- **v1 严格无状态**：不做 frame-stamp memo，接受 2× 计算（6px 行走 ~69 步 ≪ 68.5K 参数前向）；若 P-1b 实测是热点，再开纯函数 memo 口子并记 DECISIONS。
- 不 rerun forward；不改 `computeMasks`；不写 World；不消费 RNG；legacy 分支完全不经过该模块。

**为什么不用引擎级一行修复（ds §10.4）**：`SimulationPlayer.ts:116` 的「`fire=2` 被拒整发跳过」可以改成「仍沿 committed facing 发射」——一行引擎改动，自动覆盖全部 v6 链、零接线/零 parity。代价：① **无条件**发射（空车道也打），无法做 `usefulFire` 抑制；② `lastActionFire` 会记 2 而不是 1（prevAction 语义不同）；③ 失去逐规则开关与 A/B 能力。选择推理侧 resolver 是有意的：可条件化、可抑制、不动引擎语义、A/B 开关独立。此取舍记入 DECISIONS（P0a 开工前）。

### 2.2 解析规则（A3）

- `intent=1` ⇒ `fireBeforeTurn=true`，始终沿 committed facing；不改成 2。
- `intent=2`：
  - 本 tick `turnWillCommit(world, player, requestedDir)=true` ⇒ `fireBeforeTurn=false`，沿**新朝向**打。注意 `turnWillCommit=true` 也包含 `requestedDir === prevMoveDir`（无实际转向）的平凡情形，此时「新朝向」= 旧朝向。
  - `turnWillCommit=false` 且旧朝向 `usefulFire=true` ⇒ 临时 `fireBeforeTurn=true`，沿旧朝向**换发**。
  - `turnWillCommit=false` 且旧朝向无价值 ⇒ `firing=false`（记录 `suppressed`），**不制造空枪**。
  - 冷却结束那一拍自然回到 after-turn。
- 任何规则不得越过 `passesFireGate`；resolver 只做**换发/抑制**，不追加引擎不允许的子弹（v1 不绕过 cooldown/cap，fallback 仍由引擎门裁决）。

### 2.3 `usefulFire(dir)` 与拦截边界

**实现（P-1b-0 已完成）**：`src/nn/inference-rules.ts::usefulFire`（`USEFUL_FIRE_VERSION = 'u1-static-lane-2026-11-13'`；单测 `tests/nn/inference-rules.test.ts`，12 例）。 `turnWillCommit` 从 `src/ai/god/turn-gate.ts` 原样 re-export，无副本。

**v1 只认 priority 1：可命中敌。** brick 拆砖归 B2 自己的门（避免在主假设里掺入 B2 效应，析因前污染归因）；拦截豁免后置。

- **几何必须钉死（ds §6）**：子弹 6px、坦克 32px ⇒「可命中敌」的判据是**敌中心距车道中心 < 19px**（6/2 + 32/2），不是敌中心落在 6px 线上。复用 `policy-extra.ts:237-274` 的 `leadHitForDir` 几何（`THREAT_ALIGN_BAND_PX=19`、13 格上限、`laneOccluded`），或把该几何抽成 `danger-metrics` 单一函数；**禁止另写一份**。
- **地形谓词必须复用唯一实现（ds §6.2）**：`TileMap.blocksBullet`（brick/steel/base 挡；水/草/冰过，`TileMap.ts:205-208`）与 `laneOccluded` / `firstBulletBlockerPx`（`danger-metrics.ts:231,976`）。注意 level≥3 打穿钢时 `bulletHitsTerrain` 是「销毁并 hit=true」⇒ 子弹仍停在钢格（`SimulationCombat.ts:541-557`），所以**钢对「命中钢后敌人」永远是阻挡**。
- **边界**：排除射手自己；`spawnTimer>0` 的敌不算（与 `leadHitForDir` 同）；诱饵（`isDecoy`）不算；友军 guard（allegiance `ally`/`player`）不是目标；子弹出界 / 基地格挡弹 = 不可命中。
- **静态 lane 的 FP 也要处理**：静态判定同时有 FN（敌人即将进入车道）与 FP（敌人现在在车道、子弹飞 ~74 拍后已离开）。v1 用**静态 lane + 13 格距离上限**（false negative 优先），lead-aware 后置；P0b 必须报告 fallback 实发命中率，命中率显著低于触发率 ⇒ FP 主导，需收紧距离或 lead-aware。
- **拦截矛盾已诚实修正**：v1 **不做**拦截豁免 ⇒ 会误杀部分有拦截价值的发射。这是**有意选择**：先保守抑制，后置精确豁免；k3 的建议方向相反，记录在 §10。用反事实探针量化：被抑制的发射中，其后 N=12 拍内 19px band 上出现相向敌弹的比例（`bullet_cancelled` 事件对账）。
- **砖机会成本探针（ds §10.9，qw §3.4 的延续）**：被抑制的旧朝向发射里，旧车道首格是 brick 的比例（同一次 walk，低成本）。它解释 A3×B2 的负交互，不作为 A3 的收益。
- **r-阈值变体 arm（预设，不是 v1 默认）**：预注册两个阈值点 `r>K`（只对整窗拒绝 fallback）与 `r ≥ ceil(K/2)`；A3 主读数模糊时二选一作剂量-响应第二点，不临时发明规则。

### 2.4 记录字段（必须）

| 字段 | 定义（P0a 实现按此写死） |
|---|---|
| `selectedFireIntent` | 策略原始意图 0/1/2（decide() 提交值） |
| `resolvedFireAction` | packed 解出的实际输出（bit0 firing / bit1 fireBefore / bit2..4 reason） |
| `a3FallbackTicks` | `intent=2 ∧ turnWillCommit=false ∧ usefulFire=true` 的 tick（全部触发，含 fire gate 未 ready） |
| `a3FallbackShots` | 上述 tick 中真正产生 `bullet_fired` 的数量（**实发**，必须与 ticks 分开） |
| `a3SuppressedTicks` | `intent=2 ∧ turnWillCommit=false ∧ usefulFire=false` 的 tick；再拆 `gate-ready` / `gate-blocked` |
| `a3LostAfterTurnTicks` | `intent=2 ∧ turnWillCommit=true ∧ fire gate 关闭` 导致 after-turn 本拍未发弹的 tick |
| `a3NetShotDelta` | **窗口局部反事实**：`[t0, 下一决策)` 内 A3 实发 − bare 本可发（bare 在拒绝 tick 恒 0 发）；不可外推为整局净弹 |
| `a3FirstAfterTurnDelay` | 从转向被接受 tick 到新朝向首发 `bullet_fired` 的 tick 差分布（0★ 间隔 ~74 拍） |
| `a3OldDirShots` / `a3NewDirShots` | 整局配对统计：旧朝向 / 新朝向实发弹数（解释「换发」而非「补发」） |

**记录动作 = resolved**：`SimulationPlayer.ts:85-87` 把 getter 输出写进 `p.lastActionDir/Hold/Fire` ⇒ 下一决策点的 `prevAction` 特征看到的是规则后动作，不是原始意图。A3 因此是**闭环策略修改**，不是纯输出滤波；A/B 两臂各自内部一致，但这使「每 checkpoint 同时评 bare 与 deployed wrapper」成为硬前提。

---

## 3. 候选注册表（一行一状态）

状态定义：**active** = 进 P0–P3 实验；**engineering** = 需改 input 层，非打分规则；**measurement** = 只测量；**diagnostic** = 只诊断，不作 go/no-go；**constraint** = 边界约束；**dormant** = 有条件复活；**backlog** = 未排期；**deleted** = 不做。

| ID | 规则 | 状态 | 前置/理由 |
|---|---|---|---|
| A3 | fire=2 被拒转向的 per-tick fallback（usefulFire 才触发） | **dormant（P-1b 判降级）** | ep80 实测 exposure 0.0082%、loss 0.0017%、r>K=0、net shot +1；99.5% 的 fire=2 换向发生在 r=0。复测条件：新 checkpoint/新面板 exposure ≥5% |
| A2 | 冷却期不请求新转向（turn-cooling arm） | **engineering P3** | 与 A3 是**替代 arm**，不按顺序叠加；`movedTicks↑` 不是收益，看 dmg/go/win/最差 3 关 |
| B1 | 6px 弹道顶钢 fire 门（保守版，无拦截豁免；部署名 `skip_steel_only_shots`） | **active P1（已接线 + parity 通过，未部署）** | 700 对 confirm：36.43%→42.43%，Δ **+6.00pp**；McNemar **p=0.0052**；bootstrap 95% CI **[+2.57, +9.57]**；stage 19 安全线未复现（20 新 seed +15pp, p=0.375）⇒ **确认通过**；已接 `export-eval-game`（`--skip-steel-only-shots`），关闭态 **3/3 byte-identical**；训练链 off |
| B2 | 拆砖免绕路（所选 move 方向被砖挡/2 格内有砖且策略没开火 ⇒ 强制拆墙；部署名 `break_bricks_on_path`） | **dormant（v1 A/B 判负）** | 175 对（两臂 B1 开）：46.86% → 43.43%，Δ **−3.43pp**；McNemar b=37/c=31 **p=0.545**；bootstrap 95% CI **[−12.0, +5.14]**；kills 15.41→14.90、lives 0.891→0.886；机制机会 2,095（control）⇒ win 无改善，不确认/不部署 |
| B4 | 冰面滑行预判（滑行中请求反向 ⇒ 松键保持朝向；实验口 `releaseReverseOnIce`） | **dormant（v1 冰关 A/B 判负）** | 冰关 16/23/27/31 × 20 seed = 80 对（两臂 B1 开）：53.75% → 45.00%，Δ **−8.75pp**；McNemar b=23/c=16 **p=0.337**；bootstrap 95% CI **[−22.5, 0]**；机制反向请求 1,992→29 ⇒ win 无改善，不部署 |
| B5 | 草丛/水路 | **dormant** | 先看 G1 覆盖率 |
| B6 | safe-base 不必回防 | **deleted** | 无推理层干预点；改 obs/战略偏置越界 |
| B7 | 长停强制换向 | **deleted** | 需跨 tick 计数；`hold-fallback` 已证伪 |
| B8 | 6px 中线火力门 | **active（已接部署面，默认关）** | 700 对 confirm **+16.29pp**（p=6.2e-12, CI [+12.43,+20.71]）；课程键 `suppress_useless_fire`（与 B10 互斥） |
| B9 | 自射基地守卫 | **deleted** | safe-base 基地打不穿 ⇒ 休眠 |
| B10 | 预测前移门 | **active（已接部署面，默认关）** | 700 对 confirm **+13.29pp**（p=5.4e-8, CI [+8.86,+18.86]）；课程键 `suppress_lead_miss_fire`（与 B8 互斥） |
| B11 | 钢路径阻挡 | **dormant** | v1 A/B +1.14pp（p=0.897）未过闸 |
| C1 | 威胁车道扣分（tti/hitDist/laneE 四向，v1 w=0.3） | **dormant（v1 A/B 判负）** | 175 对：control 39.43% → rule 26.29%，Δ **−13.14pp**；McNemar p=0.0052；bootstrap 95% CI [−21.71, −4.57]；kills 14.47→13.25。同公式不得重试；变体需新预注册 + 新筛选段 |
| C2 | threat-ONSET 事件决策门 | **dormant（v1 A/B 未过闸）** | 175 对：39.43% → 43.43%，Δ **+4.00pp**；McNemar p=0.457；bootstrap 95% CI [−5.71, +13.14]；事件决策 **1.38%**、每局 **+72.5** 决策、墙钟 **+14.1%**；stage 1 −80pp 触发安全线 ⇒ 不部署 |
| C3 | 包夹突围 | **dormant** | v1 A/B +2.86pp（p=0.125）未过 +5pp 闸 |
| C4 | 无敌窗口主动换血/冲道具（v1：shield + 6 格道具/5 格敌 +0.5） | **dormant（v1 A/B 中性）** | 175 对：39.43% → 38.86%，Δ **−0.57pp**；McNemar p=1.0；bootstrap 95% CI [−6.29, +5.71]；clearT 11105→10633 仅次要读数；win 无改善 ⇒ 不部署；变体需新预注册 |
| C5 | 冻结期策略 | **dormant** | v1 A/B +1.14pp（p=0.791）中性 |
| C6 | 低血撤退到基地上方 | **deleted** | 与 B6 冲突；safe-base 纯浪费 |
| C7 | 对射时机 | **dormant** | v1 A/B −2.86pp（p=0.597）负/中性 |
| C8 | 交叉火力 TTI 最大化 + 紧迫门 | **dormant** | v1 A/B −1.14pp（p=0.727）中性 |
| C9 | 弹道遮挡只挡钢不挡砖 | **deleted** | 改 `laneOccluded` = obs 语义 |
| D1 | BFS + ETA≤TTL 可达性（**链路探针**，非主目标） | **active P3（诊断）** | v1 A/B +0.57pp（p=1.0）中性，仅诊断；主读数 win/clearT；pickup 只诊断；不做「会捡道具」主门 |
| D2 | 道具类型价值表 | **dormant** | 依赖 D1 |
| D3 | guard/frenzy 主动使用（**input 层**） | **engineering P3** | 量 `guardStock/frenzyStock` 拍占比；rewind 不做 |
| D4 | 道具落点危险/出生带 | **dormant** | 依赖 D1 |
| D5 | 过期道具优先级 | **deleted（并入 D1）** | 「过期忽略」是 no-op；真规则是 `ETA≤TTL` |
| D6 | 危险与道具联合权衡 | **dormant** | 依赖 D1 |
| D7 | 静态价值表 + HIGH-only 门 | **dormant** | 依赖 D1 |
| E1 | 残敌 BFS 目标偏置 | **dormant（v1 A/B 判负）** | 175 对：39.43%→32.57%，Δ **−6.86pp**；McNemar p=0.162；bootstrap 95% CI [−16.0, +2.86]；墙钟 **+20%**；同公式不得重跑，BFS 需 caller-owned scratch |
| E2 | 未访问区域探索 | **deleted** | 敌全灭即 stageclear；收窄后假设消失 |
| E3 | 蹲出生点 | **dormant** | 可能 OOD |
| E4 | 时间不足强攻 | **dormant** | 需 slack 覆盖率 |
| E5 | 清场/回防按 TTI 切换 | **dormant** | safe-base 基地不可摧毁，改 timeout/clearT 才可测 |
| E6 | 回防迟滞 | **deleted** | 需闩锁（有状态）；快照风险 |
| F1 | K 扫描（K=5/10/20） | **active P3（仅诊断）** | 跨 K 用 per-千；**K≠10 读数仅诊断，不得直接部署**——K 是训练时的时间抽象，部署需新课程 + DECISIONS |
| F2 | 事件门最小间隔参数 | **deleted** | minGap=3 已实现 |
| F3 | 决策滞后一拍预测 | **deleted** | 主路径 `endFrame()` 看本拍完整末态，前提错 |
| G1 | 规则依赖状态的覆盖率 | **measurement P-1b** | 不存在 ⇒ 休眠 |
| G2 | `--ablate-ch/extra` 消融 | **diagnostic** | 只判 OOD/表征，**不作规则 go/no-go** |
| G3 | 改 obs/结构补特征 | **constraint** | 最后才考虑；改结构 = 全语料重导 |
| A1 | fire=2 偏弹修复 | **deleted** | 前提错（v3 整发跳过） |
| A4 | 等枪就绪再走 | **deleted** | 保持语义已免费提供「就绪自动开火」 |
| B3 | 弹槽门控 | **deleted** | hard 无弹槽门控 |

**计数（逐行数，精确）：本表 43 项 = active 5（B1/F1 + B8/B10 + D1 诊断）+ engineering 2（A2/D3）+ measurement 1（G1）+ diagnostic 1（G2）+ constraint 1（G3）+ dormant 20（A3/B2/B4/B11/C1/C2/C3/C4/C5/C7/C8/E1/B5/D2/D4/D6/D7/E3/E4/E5）+ backlog 0 + deleted 13（A1/A4/B3/B6/B7/B9/C6/C9/D5/E2/E6/F2/F3）。以本表逐行状态为准；不使用「约」计数。**

### 3.1 backlog 批次 v1 定义（2026-11-13，用户要求全试）

统一剂量（175 局，B1 开，ep80 × classic35-safe-base × seeds 2000002001-2005）：
**B8 2.27%**（23.9 次/局）、**B10 2.30%**（24.3）、**B11 6.03%**（63.7）、**C3 1.15%**（12.1）、
**C5 4.26%**（45.0）、**C7 2.50%**（26.4）、**C8 0.84%**（8.9）、**D1 14.61%**（154.4）。
全部有可测机会，按下列 v1 逐条实现 + A/B（两臂都开 B1，rule 臂只加当前这一条）：

| ID | v1 动作（一句话） |
|---|---|
| B8 | 通用废射门：策略要开火，但该方向静态 `usefulFire=false` 且首阻弹格不是 brick ⇒ 抑制开火（B1 的超集；只测 B1 之上的增量） |
| B10 | 预测门：该方向 `leadHitForDir=0`（子弹到达时敌不在车道）且首阻弹格不是 brick ⇒ 抑制开火（比 B8 更严） |
| B11 | 钢挡路换向：所选 move 方向 1 格内是钢且另有可走方向 ⇒ 改走可走方向（不朝钢顶） |
| C3 | 包夹突围：4 格内敌人在左右或上下两侧同时出现 ⇒ 朝远离最近敌、且可走的方向偏置 |
| C5 | 冻结期进攻：`freezeTimer>0` 且 8 格内有活敌 ⇒ 朝最近敌移动偏置 |
| C7 | 对射先手：fire-ready、正前方 13 格内敌人正对玩家 ⇒ 强制开火 |
| C8 | 交叉火力：威胁车道 ≥2 且 4 格内有活敌 ⇒ 朝威胁车道更少/更远的方向偏置 |
| D1 | 可达道具：存在 `ETA≤TTL` 的活道具且 4 格内无敌人 ⇒ 朝该道具移动偏置（只作诊断/偏置，不做主门） |

所有规则默认关；单条 A/B 通过前不得组合，训练链恒关。

**实现落点（2026-11-13，screening 版）**：谓词统一落 `src/nn/inference-rules.ts`
（`uselessShot` / `leadMissShot` / `steelBlockedMove` + `alternativeMoveDir` /
`pincerBreakoutDir` / `freezeAttackDir` / `duelShotReady` / `crossfireEscapeDir` /
`reachablePickupDir`）；`NNInputOptions` 对应 `suppressUselessFire` /
`suppressLeadMissFire` / `avoidSteelPath` / `breakPincer` / `attackWhileFrozen` /
`counterFireFirst` / `crossfireAwareness` / `pickupReachBias`，默认全关；
探针 `tools/diag/positioning-dose-probe.ts` 加同名 CLI flag（偏置权重固定 0.5）与
`ruleApplications` 机制读数；单测 `tests/nn/inference-rules.test.ts` 36 例。
本轮只筛不部署：不接 `export-eval-game` / 课程块 / agent。

**Screening A/B（2026-11-13，175 对，ep80 × classic35-safe-base × seeds 2000002001-2005，
两臂都开 B1，rule 臂只加当前一条；control 46.86% = 82/175）**

| ID | control → rule | Δ | McNemar p | 按关 bootstrap 95% CI | 机制读数 | 判定 |
|---|---|---|---|---|---|---|
| B8 | 46.86% → **58.86%** | **+12.00pp** | **0.0170** | **[+4.00, +18.86]** | 4190 个机会 → 0（抑制 6,837 次） | **过 +5pp 闸 + pilot 显著** ⇒ 进 20-seed 确认 |
| B10 | 46.86% → **60.57%** | **+13.71pp** | **0.0043** | **[+1.71, +22.29]** | 4249 → 0（抑制 8,783 次） | **过 +5pp 闸 + pilot 显著** ⇒ 进 20-seed 确认 |
| B11 | 46.86% → 48.00% | +1.14pp | 0.897 | [−8.00, +9.14] | 11,145 → 0（改走 801 次） | 未过闸 ⇒ dormant |
| C3 | 46.86% → 49.71% | +2.86pp | 0.125 | [0, +6.29] | 机会 2,125（偏置，未记 argmax 改写数） | 未过闸 ⇒ dormant |
| C5 | 46.86% → 48.00% | +1.14pp | 0.791 | [−2.29, +5.71] | 机会 7,876 | 中性 ⇒ dormant |
| C7 | 46.86% → 44.00% | −2.86pp | 0.597 | [−12.57, +5.71] | 4,621 → 3,098（强制开火 2,506 次） | 负/中性 ⇒ dormant |
| C8 | 46.86% → 45.71% | −1.14pp | 0.727 | [−4.57, +1.71] | 机会 1,550 | 中性 ⇒ dormant |
| D1 | 46.86% → 47.43% | +0.57pp | 1.0 | [−6.86, +8.00] | 机会 27,011 | 中性；仅诊断，不部署 |

- control 臂与 B2 轮 control（同 flag：B1 开）逐字段 parity **0/175 mismatch** ⇒ 新规则默认关时行为不变。
- B8/B10 是同一机制（抑制空枪），B10 更严（加预测门）；两者 pilot 都过闸，按协议各做独立确认，不组合。
- 偏置类（C3/C5/C8/D1）的机制读数只证明「机会存在」，未逐次记录偏置是否改变 argmax。
- 产物：`tmp/backlog-ab-{control,b8,b10,b11,c3,c5,c7,c8,d1}-{0..4}.jsonl`；
  聚合 `tmp/backlog-ab-aggregate.ts`（`AB_PREFIX` 可切换）；运行器 `tmp/backlog-ab-run.ts`。

**20-seed 独立确认（2026-11-13，700 对，seeds 2000003001-3020，两臂都开 B1；control 42.43% = 297/700）**

- **B8**：42.43% → **58.71%**（411/700），Δ **+16.29pp**；McNemar b=82/c=196 **p=6.2e-12**；
  按关 bootstrap 95% CI **[+12.43, +20.71]**；kills 15.00→16.64、lives 0.836→1.187、
  clearT 11019→10321、墙钟 2.90→2.25 s/局（−22.4%）；机制 15,853 个机会 → 0（抑制 30,770 次）；
  最差单关 stage 21 **−10pp**，无安全线。
- **B10**：42.43% → **55.71%**（390/700），Δ **+13.29pp**；McNemar b=99/c=192 **p=5.4e-8**；
  按关 bootstrap 95% CI **[+8.86, +18.86]**；kills 15.00→16.58、lives 0.836→1.067、
  clearT 11019→10331、墙钟 2.90→2.22 s/局；机制 16,168 个机会 → 0（抑制 31,171 次）；
  stage 24 **−20pp**（12/20→8/20，配对 b=9/c=5 **p=0.424**）⇒ 未触发安全线。
- **结论**：B8/B10 确认通过（同一机制，B10 更严；**不组合**）。
- **部署面（2026-11-13，已接线，默认全关）**：课程 `inference_rules` 新增
  `suppress_useless_fire` / `suppress_lead_miss_fire`（二选一，同真 = 课程文件响亮拒）；
  透传链与 B1/B2 同构（`apply_course` → `eval_course_once` → 本机/远端评估 →
  agent eval 分支 → `export-eval-game` 同名 CLI），远端参数
  `?suppressUselessFire=1` / `?suppressLeadMissFire=1`，能力位
  `suppressUselessFireSupport` / `suppressLeadMissFireSupport`，任务键
  `:suppressUselessFire` / `:suppressLeadMissFire`，报告归因
  `inferenceRules.{suppressUselessFire,suppressLeadMissFire}`。**训练链恒关**；
  `export-eval-game` 两 flag 同开 `exit 2`。**未改任何课程文件**（要用的课程自己写块）。
- 产物 `tmp/backlog-confirm-{control,b8,b10}-{0..4}.jsonl`。

---

## 4. 验证协议（可执行版）

### 4.1 P-1b-0 谓词与 P-1b 测量（不是纯只读）

**P-1b-0（先落地，冻结后才准开测）**

- `src/nn/inference-rules.ts` 只落纯谓词：`usefulFire(world, tank, dir)`（§2.3 几何）+ `turnWillCommit` 复用（`src/ai/god/turn-gate.ts:55`，禁止复制）+ 谓词单测矩阵（§7 #15）。
- 不接 resolver、不加开关、不做 parity；谓词版本号写入本节并冻结。
- **探针版必须 import 同一函数**：否则剂量读数与部署行为是两个谓词，不可外推。

**P-1b 测量（用冻结谓词，不跑 rule 臂）**

**控制侧探针（已落地）**：`tools/diag/a3-dose-probe.ts` — 用 `NNInput` + `Simulation` 跑 bare 策略，逐拍评估冻结的 `usefulFire` / `turnWillCommit`；输出 per-game JSONL（`type=game`）+ `type=summary`。窗界用 `input.readDecisionStats().n` 增量检测；`r` 用「upcoming tick」时钟（`(world.frame+1)*TICK_MS`）与引擎同口径；fallback 实发为**局部反事实**（控制臂不执行规则）。

0. **协议清单（硬钉，落 `spec.json`）**：`weights` = `tmp/g2-ckpts/ep80.json`（重建全量权重；sha12 记录在产物旁）、`stage-json-file` = `tmp/classic35-safe-base-stages.json`（由 `nn-training/levels/classic35-safe-base.jsonc` 生成）、`difficulty=hard`、35 关、`seeds=2000002001..2000002020`（20/关）、`K=10`、`decisionEvents=false`、`maxTicks=36000`、探针 = `tools/diag/a3-dose-probe.ts`。A3 剂量依赖策略行为，换 checkpoint 就换剂量；P-1b 与 P0b 必须用同一 checkpoint。
1. **基线 seed 段（决定，不再留条件句）**：`2000002001..` 跑 control，**预注册复用**为确认面板的 control 臂（确定性 ⇒ 结果相同，省 700 局）。约束：P-1b 的分析脚本只输出**剂量/机制/墙钟**，不输出胜负/逐关胜负；确认段读出的一切胜负/分层胜负信息不得喂任何冻结前决策。使用前先验证该段未被烧（全仓 grep + 历史日志）。
2. 埋点核对：`outcome/lossDetail/kills/cellsVisited/stopTicks/stopRuns/playerShots/powerUps*/clearTick/ticks`；`movedTicks/pressedStillTicks/cooldownRejectTicks/a3*` 需 `--probe` 或新埋点（`cooldownRejectTicks` 已有，见 `export-eval-game.ts:1374`，只作对账，不替代 exposure）。单局墙钟/ticks per sec，外推总预算；超预算先砍候选，不砍局数。
3. **A3 三层剂量（按 r>K / r≤K 分层）**：
   - 暴露率：选中 `fire=2` 且窗口内至少 1 tick `turnWillCommit=false` 的决策占比；
   - 损失拍：这些窗口 `Σ min(r, 窗口长度)` / 总 alive ticks（`decisionEvents=false` 时窗口长度 = K；事件门开启时必须用实际窗口）；
   - fallback **触发率**：拒绝 tick 上旧朝向 `usefulFire=true` 的比例（这是规则触发率，不是价值）；
   - **净弹数差**：窗口局部反事实 `[t0, 下一决策)`；另报整局配对的 `a3OldDirShots/a3NewDirShots`、`a3FirstAfterTurnDelay`（0★ 间隔 ~74 拍）；
   - **gate-ready / gate-blocked 拆分**：`a3FallbackTicks` 与 `a3FallbackShots` 必须分开（未 ready 的 fallback 不出弹）；用 `ticksUntilFire`（`ThreatBudget.ts:206`）判 ready；
   - 按 stage 分层报告。
4. **预注册判据（P-1b 前冻结；数值是工程初值，来源为空枪风险/样本量/几何，不是教师读数）**：

| 指标 | 降级（不进工程） | 禁止上线 | 进工程 |
|---|---|---|---|
| 暴露率 | < **1%** | — | ≥ **5%**（1–5% 仅局部规则） |
| 损失拍 | < **0.5%** alive ticks（辅助闸，被暴露率大体蕴含） | — | ≥ **0.5%** |
| fallback 触发率 | — | < **20%**（会制造空枪） | ≥ **40%**；20–40% 仅 r>K regime 可进 |
| 分层集中度 | — | 单关贡献 > **50%** 暴露且其他关 <1% ⇒ 只报分层，不聚合 | 覆盖 ≥3 关 |

5. **谓词版本冻结（已完成）**：`USEFUL_FIRE_VERSION = 'u1-static-lane-2026-11-13'`（`src/nn/inference-rules.ts`），单测 `tests/nn/inference-rules.test.ts`（12 例）。此后任何谓词改动 = 升版本 + 重新测剂量（不得沿用旧读数）。
6. G1：规则依赖状态在面板的存在率；G2 消融只记录「网络读不读」，不作 go/no-go。
7. **C 族/走位族暴露率一并测**（C1/C4/C6/E1 等只读探针），给 P0–P3 一个统一剂量基础；否则 A3 剂量判低时，P1/P2 仍按序号空转。
8. **P-1b A3 实测结果（2026-11-13，已完成，A 级）**：
   - **协议**：`tmp/g2-ckpts/ep80.json`（sha12 `0C2713B5406E`）× `classic35-safe-base` 35 关（stage JSON sha12 `F59CBD4C897E`）× seeds `2000002001..2020`；hard、K=10、`decisionEvents=false`、maxTicks 36000；700 局（35×20，无重复）。
   - **读数**：decisions **702,993**；intent2 **44,737**；`intent2Turning` **11,949**（r=0 **11,891** / 0<r≤K **58** / r>K **0**）；exposedDecisions **58**（**0.00825%**）；rejectedTicks **117**（**0.00170%** alive ticks）；fallbackTicks **109**（trigger **93.16%**）；fallbackReady **109**（gate-ready **100%**）；suppressed **8**；bareShots **44,711**；A3 modeled shots **44,712**；**net shot +1**（700 局）。
   - **集中度**：23 关有暴露，最大单关贡献 **8.6%**（>50% 才需只报分层）⇒ 无集中问题，但绝对剂量可忽略。
   - **墙钟**：单局均值 **2.80s**、**3,583 ticks/s**（5 分片 CPU 合计 1,960.7s；并行墙钟约 6–7 min）。
   - **判据**：**暴露率 <1% ⇒ 降级（不进工程）**；损失拍 <0.5% ⇒ 降级；trigger 93% 虽 ≥40%，但在 exposure≈0 时无意义（暴露率是必要闸：<1% 直接降级，不再看 trigger）。
   - **机制解释**：ep80 的 fire=2 换向 **99.5% 发生在 r=0**，**r>K = 0** ⇒ 策略已（通过 obs `s[5]`）学会避免在转向冷却期开火；A3 的前提是真实存在的引擎行为，但对这个 checkpoint/面板几乎从不触发。
   - **结论**：**A3 不进 P0a/P0b**，注册表降为 dormant；B1/B2 可各自单条继续，但原 A3×B1/B2 析因取消。该结论是 **checkpoint/面板特定**的：换新 checkpoint 或新面板若 exposure ≥5%，可按本协议复测。
9. **P-1b C 族/走位族剂量实测（2026-11-13，175 局 pilot，A 级）**：
   - **协议**：同 §4.1.8 的 ep80 × `classic35-safe-base`；35 关 × 5 seed（2000002001..2005）；hard、K=10、events=false；工具 `tools/diag/positioning-dose-probe.ts`，产物 `tmp/pos-dose-{0..4}.jsonl`。
   - **读数**（决策级，分母 = 玩家存活可决策拍）：decisions **171,268**
     - **C1 威胁车道**：threat **14,771（8.62%）**；其中换向 **20.65%**、前进 **86.44%**；单关最高 stage 13 = 17.8% ⇒ **≥5%，进工程候选**。
     - **C4 安全窗**：safe window **12.84%**（shield 7.87%、freeze 5.11%）；安全窗内拾取物 **18.0%/17.1%**、近敌 **28.8%/37.1%** ⇒ **≥5%，进工程候选**（但需用 `ShieldWindow` 区分 propShield vs respawn/star，当前 `shieldTimer` 口径偏宽）。
     - **E1 残敌/走位**：远敌（>8 格）**36.44%** 的残敌决策；面朝墙 **13.71%**；两者同时 **7.05%** ⇒ **高机会**，但规则层是目标选择/BFS，需另做镜像等变与目标层设计。
     - **B4 冰面**：全局 **4.41%**（1–5% ⇒ 仅局部规则）；冰关 stage 16/27/23 分别 **52.9%/52.6%/47.4%** ⇒ 只按冰关局部评估。
     - **C2 事件门**：本次 events=false，`decisionEvents=0`；需单独 events-on 小测（35×2 即可）量 event/n 与 avgΔt。
   - **判据**：C1/C4/E1 过 5% 剂量门（可进工程/单条 A/B）；B4 只做局部；C2 待测。
   - **顺序建议（C1/C4 判后修订）**：C1 v1 判负、C4 v1 中性（§4.1.10/§4.1.11）；下一步 **E1**（远敌 36.4%，目标层改动最大）或 **C2 events-on 剂量**；B4 只在冰关做局部。
10. **C1 A/B 实测（2026-11-13，175 对 screening，A 级，判负）**：
   - **协议**：ep80 × `classic35-safe-base` × seeds `2000002001..2005`；同 seed 配对；control `c1Weight=0` vs rule `c1Weight=0.3`；工具 `tools/diag/positioning-dose-probe.ts`；产物 `tmp/c1-control-{0..4}.jsonl` / `tmp/c1-rule-{0..4}.jsonl`。
   - **结果**：control **69/175 = 39.43%**，rule **46/175 = 26.29%**，Δ **−13.14pp**；McNemar b=43 / c=20，**p=0.0052**；按关 cluster bootstrap 95% CI **[−21.71, −4.57]**；kills **14.47 → 13.25**，lives **0.726 → 0.617**。
   - **分层**：最差 stage 3（−60pp）、7（−60pp）、0（−40pp）；最好 31/10/2（+40pp）；分化大但聚合明确为负。
   - **机制核对**：`threatTurn` 3050 → 3455（**未下降，反而上升**）⇒ 该公式没有减少「在威胁车道换向」，更像把策略推向了另一类坏换向；`hitDist`/`laneE` 项疑似过度避让（kills 下降）。
   - **判据**：聚合负 + CI 不含 0 + 最差关触发安全线 ⇒ **C1 v1 判负，不做确认、不部署**；同公式/同权重不得在同一面板重跑，变体（去掉 hitDist/laneE 或改权重）必须新预注册 + 新筛选段。
   - **代码状态**：`NNInputOptions.c1Weight` 默认 0（未接 `export-eval-game`、未部署）；保留为实验口，但注释标明 v1 判负。
11. **C4 A/B 实测（2026-11-13，175 对 screening，A 级，中性）**：
   - **协议**：同 §4.1.10 的 ep80 × `classic35-safe-base` × seeds `2000002001..2005`；control `c4Bias=0` vs rule `c4Bias=0.5`（shield 窗 + 6 格道具 / 5 格敌优先）；工具 `tools/diag/positioning-dose-probe.ts`；产物 `tmp/c4-control-{0..4}.jsonl` / `tmp/c4-rule-{0..4}.jsonl`。
   - **结果**：control **69/175 = 39.43%**，rule **68/175 = 38.86%**，Δ **−0.57pp**；McNemar b=18 / c=17，**p=1.0**；按关 bootstrap 95% CI **[−6.29, +5.71]** ⇒ **win 无改善**。
   - **次要读数**：kills 14.47→14.70、lives 0.726→0.737、clearT **11105→10633（−4.3%）**；只是次要信号，不构成部署理由（计划口径：win 无改善不部署）。
   - **分层**：最差 stage 29（−40pp）、1/3（−20pp）；最好 2（+60pp）、9（+40pp）、27（+20pp）；无系统性方向。
   - **机制**：`c4Opportunity` 5380/171,268 ≈ **3.1%** 决策；rule 臂 4722（状态分叉后），说明规则有触发但未转化为 win。
   - **判据**：pilot 点估计 −0.57pp，远低于 futility gate 的 +5pp ⇒ **不做确认、不部署**；C4 v1 降为 dormant。变体（换目标优先级/权重/区分 propShield）必须新预注册 + 新筛选段。
   - **代码状态**：`NNInputOptions.c4Bias` 默认 0；`c4MoveBias` 保留为实验口，注释标明 v1 中性。
12. **C2 A/B 实测（2026-11-13，175 对 screening，A 级，未过闸）**：
   - **协议**：同 §4.1.10 的 ep80 × `classic35-safe-base` × seeds `2000002001..2005`；control = events-off（复用 `tmp/c4-control-*`），rule = `--decision-events`（threat-ONSET + minGap=3）；工具 `tools/diag/positioning-dose-probe.ts`；产物 `tmp/c2-rule-{0..4}.jsonl`。
   - **结果**：control **69/175 = 39.43%**，rule **76/175 = 43.43%**，Δ **+4.00pp**；McNemar b=29 / c=36，**p=0.457**；按关 bootstrap 95% CI **[−5.71, +13.14]** ⇒ **未达显著**。
   - **分层**：最差 stage 1（−80pp）、7/21（−40pp）；最好 13/2（+60pp）、28（+40pp）；stage 1 的 0/5 vs 4/5 触发安全线（单侧 p≈0.031）。
   - **成本（C2 不是零成本）**：事件决策 **2,593 / 187,463 = 1.38%**；每局多 **72.5** 次决策；平均 Δt **10 → 9.87**；墙钟 **2.343 → 2.674 s/局（+14.1%）**。
   - **判据**：pilot 点估计 +4.00pp **低于 futility gate 的 +5pp**，且 p 不显著、CI 含 0、stage 1 触发安全线 ⇒ **不做确认、不部署**；C2 v1 降为 dormant。
   - **说明**：这是唯一一个方向为正的候选（kills 14.47→15.07、lives 0.726→0.806、clearT 略快），但成本 +14.1%，且当前证据不足以过闸；若要做更大样本，必须新预注册（例如先固定 minGap/事件定义 + 20 seed 面板）。
13. **E1 A/B 实测（2026-11-13，175 对 screening，A 级，判负）**：
   - **协议**：同 §4.1.10 的 ep80 × `classic35-safe-base` × seeds `2000002001..2005`；control = events-off（复用 `tmp/c4-control-*`），rule = `e1Bias=0.5`（最近敌 >8 格时按 `goal-mask.goalMoveBias` 的 BFS 对齐加/减 move 分）；工具 `tools/diag/positioning-dose-probe.ts`；产物 `tmp/e1-rule-{0..4}.jsonl`。
   - **结果**：control **69/175 = 39.43%**，rule **57/175 = 32.57%**，Δ **−6.86pp**；McNemar b=37 / c=25，**p=0.162**；按关 bootstrap 95% CI **[−16.0, +2.86]** ⇒ 方向为负、未显著。
   - **分层**：最差 stage 7/29（−60pp）、1（−40pp）；最好 9（+60pp）、31/19（+40pp）；分化大。
   - **机制/成本**：farEnemy 62,042→56,882（状态分叉）；blockedFacing 23,338→24,381；**墙钟 2.343→2.810 s/局（+20.0%）**——BFS 每决策一次，成本显著。
   - **判据**：聚合负 + CI 含 0未显著 + +20% 计算 ⇒ **不做确认、不部署**；E1 v1 降为 dormant。BFS 版本只能作实验口；部署级必须用 caller-owned scratch。
   - **代码状态**：`NNInputOptions.e1Bias` 默认 0；`e1MoveBias` 注释标明 v1 判负。
14. **B1 A/B 实测（2026-11-13，175 对 screening，A 级，过 futility gate）**：
   - **协议**：ep80 × `classic35-safe-base` × seeds `2000002001..2005`；同 seed 配对；control `skipSteelOnlyShots=false` vs rule `skipSteelOnlyShots=true`（决策级 6px 顶钢门，v1 无拦截豁免）；工具 `tools/diag/positioning-dose-probe.ts`；产物 `tmp/b1-control-{0..4}.jsonl` / `tmp/b1-rule-{0..4}.jsonl`。
   - **结果**：control **69/175 = 39.43%**，rule **82/175 = 46.86%**，Δ **+7.43pp**；McNemar b=27 / c=40，**p=0.142**；按关 bootstrap 95% CI **[−1.71, +16.0]** ⇒ 点估计为正、CI 刚跨 0。
   - **机制**：control 中有 **1,410** 个「纯顶钢」发射决策，rule 臂被抑制到 **0**；`steelOnlyShotRate` 从 ~0.8% 降到 0，说明门确实在起作用。
   - **Parity**：当前 probe 的 control 与旧 `c4-control` 在 outcome/ticks/kills/lives/clearTick 上 **0 mismatch** ⇒ 新增 C4/E1/B1 代码默认关时行为逐字段不变。
   - **分层**：最差 stage 24（−60pp）、3/29（−40pp）；最好 27/2（+60pp）、18（+40pp）；分化大。
   - **判据**：pilot **+7.43pp ≥ futility gate +5pp** ⇒ 允许冻结确认；但 pilot 本身不显著（p=0.142, CI 含 0），**不得直接部署**。下一步是 **20-seed 独立确认面板**（**seeds `2000003001..3020`**，全仓 grep 无命中 = 未烧；35 关；control/rule 各 700 局；只跑一次；cluster bootstrap α=0.05，win 点估计 ≥ +3pp，McNemar 补充；安全线：任一关 −10pp 且单侧 p<0.10 ⇒ 待复现）。
   - **确认（2026-11-13，700 对，seeds `2000003001..3020`，未烧段）**：control **255/700 = 36.43%**，rule **297/700 = 42.43%**，Δ **+6.00pp**；McNemar b=87 / c=129，**p=0.00516**；按关 bootstrap 95% CI **[+2.57, +9.57]**（不含 0）；机制 **5,630 → 0**；kills 14.45→15.00、lives 0.716→0.836、clearT 11331→11019；墙钟 **−24.3%**（抑制无效射击后更省）。**确认通过**。
   - **安全线复现**：confirm 中 stage 19 为 control 6/20 vs rule 2/20（−20pp，单侧 p≈0.035）触发安全线；在 20 个**新** seed（`2000003021..3040`）上复测：control **1/20** vs rule **4/20**（**+15pp**, p=0.375）⇒ 原回归未复现，判为小样本噪声；stage 19 合并 40 seed 后大致中性，安全线解除。
   - **接线 + parity（2026-11-13，已完成）**：`tools/sim/export-eval-game.ts` 新增 `--skip-steel-only-shots`（默认关），在 `scripted.setAction` 前按最终 `(mv, fire, hold)` 复用 `steelOnlyShot` + `turnWillCommit`；报告仅在开启时写 `inferenceRules.skipSteelOnlyShots=true`。
     - **B1-off parity**：`git worktree add --detach tmp/parity-base HEAD`（HEAD `af253ba8`）跑旧版 eval-game，vs 新版 B1-off：stage 0/7/31（seed `2000003001`、ep80、hard、maxTicks 36000）`_eval_report.json` **3/3 SHA256 逐字节一致**。
     - **B1-on 生效证据**：stage 0 报告仅多 `inferenceRules.skipSteelOnlyShots=true`（其余字段与关闭态相同）；stage 19 行为改变（ticks 4298→4195、kills 2→5、hitRate 0.226→0.387、score 0.098→0.131）。
     - **单元测试**：`tests/nn/inference-rules.test.ts` 新增 9 个 `steelOnlyShot` 用例，全部通过（同文件 21/21）。
     - **未接的链**：`export-rl-rollout.ts` / `export-dagger-beta.ts` 代码未动（训练链恒 off），因此其 rules-off 行为按构造不变；DoD #12 的 rl-rollout parity 由「零改动」满足，待将来 B1 入训练链时再补跨链 parity。
   - **代码状态**：`NNInputOptions.skipSteelOnlyShots` 默认 false；`steelOnlyShot` 注释标明 confirm 通过 + eval-game 已接线 + parity 通过。

### 4.2 统计

- **pilot 估 D 与 cluster-aware MDE**：筛选面板 paired pilot；按关分层 bootstrap 模拟主检验，或 cluster-robust SE（G−1=34 df）反推 MDE；估墙钟。只用于样本量估计，不作结论。McNemar D 只能作配对补充。
- **主检验写死**：配对、按关加权的差值；重抽样单位 = 35 关（有放回）、10000 次、percentile 95% CI（G 小，同时报 t-percentile/BCa）；双侧 α=0.05 判定 = **CI 不含 0**。
- **政策门槛与 futility gate（v3.2 新口径）**：
  - **统计成功** = CI 不含 0；**部署门槛** = 点估计 ≥ **+3pp**（政策常数，不是 MDE）。
  - **数学事实**：点估计门槛在真实效应 = 3pp 时功效 ≈ 50%，加 n 不能修复（对称估计器）。所以只用点估计门槛当闸会「注定一半失败」。
  - 因此设 **futility gate**：筛选 pilot 的点估计必须 ≥ **+5pp** 才准冻结确认；否则直接杀，不烧确认预算（+5pp 是工程初值：给 3pp 点估计门槛留 ~2pp 缓冲；不是教师读数，可在 P0b 前按 MDE/预算重算并记录）。
  - **abort 条件（方向修正）**：`零边际 MDE₀ = 2.80 × SE`；若 `MDE₀ > 3pp` ⇒ 加 n 或杀规则（欠功效）；不要再用 `frozen +pp > MDE`。
  - 若必须证明「至少 3pp」，改用**边际检验**：判定 = 95% CI 下界 > 3pp；其 `MDE_margin = 3pp + 2.80 × SE`，需另算预算。当前预算不采用。
  - 冻结文档必须附：SE 来源、`MDE₀` 推导、futility gate 的 pilot 读数；`+pp` 与 `MDE` 分开报告，不许再用 `max(3pp, MDE)` 混成一个数。
- A3 唯一主假设，确认 α=0.05；其他规则确认时按**程序级家族** Holm 校正（家族 = 本程序全部非 A3 确认候选，不按批次各自 α=0.05），失败不得换 seed 段立即重试。
- 「+3pp 且 p<0.10」只能算探索性进入下一轮，**不是默认开**；「win 无显著改善一律不部署」。
- **分层安全线（给数值）**：聚合赢但任一 stage 单侧 exact `p<0.10` 且 `Δ ≤ −10pp`（n=20/关）⇒ 待复现，不部署；同时报最差 3 关。

### 4.3 面板纪律

- 筛选 / 确认 / 监控三段式；`870001` 已烧；`2000001001` 已被 8 档 5600 局横评使用；确认面板用未烧段。
- **确认段复用（已决定）**：P-1b 的 control 基线预注册为确认 control 臂（§4.1.1）；本程序所有规则**共用同一确认段**（control 臂复用，每条规则只新跑 rule 臂），测试相关在报告中注明；Holm 按程序级家族。
- 同一部署批次只接受预先冻结的候选清单；家族 Holm。

---

## 5. 批次与出口

| 批次 | 内容 | 出口 |
|---|---|---|
| P-1a | 本 canonical 文档 | 单一规格 |
| P-1b-0 | 纯谓词 `usefulFire` + `turnWillCommit` 复用 + 谓词单测 | 谓词版本冻结（已完成，12 tests green） |
| P-1b | 冻结谓词上的 control 测量（不跑 rule 臂） | A3 已完成（降级：exposure 0.0082%）；C/走位待测 |
| P0a | A3 接线 + §7 的 16 条单测 | **暂不执行（A3 降级）** |
| P0b | A3 pilot → futility gate → 确认（同一 checkpoint） | **暂不执行（A3 降级）** |
| P1 | B1（保守版） | **确认通过并已接 `export-eval-game`：+6.00pp, CI [+2.57,+9.57], p=0.0052；B1-off 3/3 byte-identical；训练链 off，未部署** |
| P2 | B2（与 B1 析因） | 单条确认（A3×B2 析因取消） |
| P3 | C1 **判负** · C4 **中性** · C2 **未过闸** · E1 **判负（−6.9pp, +20% 成本）** → F1（诊断）/ A2 arm / D3 / B4（冰关局部）；D1 只作诊断 | 家族 Holm |
| P5 | **只允许已确认组件**合成 fire 模块 | 逐组件消融 |
| 暂不做 | 表中 dormant/backlog/deleted | — |

**P2 析因基线决策树（A3 降级后修订）**：若 B1×B2 组合 ≤ 最优单条 ⇒ 组合不得作为后续基线；B2 对**最优单条**做析因；组合负交互问题转 P5 逐组件消融，不再叠加新规则。

---

## 6. 指标口径

- **win** = `outcome==='stage_clear'`；**go** = gameover；**to** = max_ticks；**clearT** = clearTick 均值。
- **冻死率** = `stopRuns[0] >= 50`，单位是**连续 hold 决策段**（K=10 ⇒ 约 500 tick 首段停滞），不是 tick；同时报 `pressedStillTicks`，区分「主动 hold」与「按方向卡住」。
- **长停段** = 任一 `stopRuns[i] >= 100`；报最长段中位。
- **fire**：`playerShots`、就绪拍利用率、被拒转向丢失的 after-turn 拍、A3 fallback 实发/命中/拆砖/拦截数；另报 `a3OldDirShots`/`a3NewDirShots`、`a3FirstAfterTurnDelay`、`a3FallbackTicks` vs `a3FallbackShots`（gate-ready/blocked 拆分）。
- **跨 K**：shots/千、hit/千、kill/千、dmg/千，不用 raw counts。
- **safe-base 主指标**：win / go / timeout / clearT + 归一化 kill/dmg/shots；pickup 只诊断；`base_destroyed` 读 `lossDetail` 且恒 0。
- **分层**：逐关 delta + 最差 3 关；聚合不掩盖单关恶化。

---

## 7. A3 代码级 DoD（16 条确定性单测，先写再实现）

1. r=0：`intent=2`、转向立即接受 ⇒ after-turn，方向=新方向。
2. r=1：一个拒绝 tick ⇒ 接受 tick 不得 fallback 旧方向。
3. r=4：前 4 tick 若旧方向 useful ⇒ fallback；首个接受 tick 恢复 after-turn。
4. r=12、K=10：整窗拒绝 ⇒ 只在 usefulFire 时 fallback；射击间隔仍由引擎控制。
5. 旧方向顶钢且无拦截价值 ⇒ 不 fallback。
6. **v1 不认 brick**：旧方向首格 brick 不得让 A3 fallback（拆砖归 B2）。
7. `intent=1` 永远 before-turn；B1 只改 `firing`、永不改 `fireBeforeTurn`/intent（单测钉死）。
8. fire gate 未 ready ⇒ 规则不得制造射击；下一 ready tick 行为正确。
9. legacy checkpoint 输出逐字节不变。
10. **resolver 纯函数恢复等价**（构造保证）；`NNInput` committed 状态快照覆盖是**独立既有缺口**（`src/snapshot/` 零引用），单独立项，不阻塞 P0a。
11. **轻量镜像断言**：resolver 输出对 4 方向交换律成立 + 复用 obs 级镜像表（`mirrorScalars`/`POLICY_EXTRA_MIRROR_SWAPS`）；World 级镜像 fixture 后置。
12. 双链 parity：`NNInput` 与 `export-eval-game.ts` **以及 `export-rl-rollout.ts`** 同 seed/权重/开关逐拍输出一致。
13. **换发时间线**：fallback 已发射 ⇒ 接受拍 `tryFire` 被 fire gate 正确阻塞 ⇒ 下一 ready 拍沿新朝向发射（意图仍 fire≠0）；断言不只看语义恢复，还要断言冷却交叠时间线。
14. **v6 + rules-off 逐字节不变**：`intent 0/1/2 → (firing, fireBeforeTurn)` 三值映射与当前 `policy-input.ts:329-330` 完全相同；固定 v6 checkpoint + seed 的 action stream golden 逐拍相等（golden 在改前录制）。
15. **`usefulFire` 谓词矩阵**：车道内敌=true；±18px=true；±20px=false；首格 brick/steel/base=false；水面穿过=true；钢后敌=false；`spawnTimer>0`=false；诱饵按 §2.3 约定。
16. **gate 时间线**：fallback 触发但 fire gate 未 ready ⇒ 不出弹且 `a3FallbackShots` 不增；ready 后下一 tick 行为正确。

另：合法集非空断言（`hold=1`、`fire=0` 恒合法）；随机性只用 `world.rng`；每决策每目标只算一次 BFS（从玩家出发），不用模块级共享 buffer，用调用方 scratch / 实例字段；`usefulFire` 必须 import §2.3 的单一几何实现（禁止副本），且每 tick 零分配。

---

## 8. 集成拓扑与部署纪律（v3.2 修正）

### 8.1 真实接线面（B，本轮逐点核验）

**工具侧 `new NNInput` 有 5 个构造点**（测试侧另有 ≥4 处，`tests/nn/action-v6.test.ts` 一文件就有 4 处）：

| 调用点 | 用途 | chain |
|---|---|---|
| `tools/sim/simulation-runner.ts:638` | 共享入口（policy='nn'） | deploy / eval |
| `tools/sim/nn-trace.ts:48` | 轨迹诊断 | diagnostic |
| `tools/diag/divergence-probe.ts:162` | 分叉诊断 | diagnostic |
| `tools/gap-text-table.ts:898` | 文本表 | diagnostic |
| `tools/sim/export-dagger-labels.ts:140` | **BC 标签链** | **train（恒关）** |

**与 `fire=2` 相关的内联 `InputLike`：3 个 v6 + 2 个 legacy**（v3.1 的「四大导出器」分类有误）：

| 文件 | 动作空间 | 用途 | chain | resolver |
|---|---|---|---|---|
| `tools/sim/export-eval-game.ts`（`ScriptedInput:157`、`buildModelFromText:846`） | **v6**（`:851-852`） | 评估 | deploy | **接线** |
| `tools/sim/export-rl-rollout.ts:470` | **v6**（`:1277-1278`） | 训练语料 | **train（恒关）** | 接线但**禁止开启** |
| `tools/sim/export-dagger-beta.ts:42` | **v6**（`usesHoldFire3:65`、`decodeMove4:48`、`computeMasks(world,true):236`） | **BC/DAgger 标签** | **train（恒关）** | 接线但**禁止开启**（v3.1 漏了） |
| `tools/sim/export-nn-replays.ts:294` | **legacy 5/2**（`computeMasks(world):128`、`decodeMove:299`） | 回放导出 | deploy（现状） | **不接**：无法表达 `fire=2`；v6 权重喂它会因编号错位静默错译 |
| `tools/sim/record-games-video.ts:53` | **legacy 5/2**（`fire===1:58`、`computeMasks(world):207`） | 视频 | deploy / diagnostic | **不接**（同上） |

`tests/nn/action-space.test.ts:163-176` 的「6 条 mover」是 `decodeMove` 源码门禁名单，**不是 resolver 接线面**；不要照它改。若要让 `export-nn-replays`/`record-games-video` 反映 v6 规则，先单独立项升级到 v6 动作空间，不并入本计划。

### 8.2 resolver 接入方式

1. 新建 `src/nn/inference-rules.ts` 纯函数（`selectFireIntent` / `resolveFireAction` + P-1b-0 的 `usefulFire`），只读 World、无对象分配、无跨 tick 状态。
2. **真实接线面 = 4 处代码**：`NNInput` 类（内部按 `usesHoldFire3()` 分流）、`export-eval-game`、`export-rl-rollout`、`export-dagger-beta`；按文件计 = 5 个 NNInput 工具点 + 3 个 v6 内联。调用共享，**不是抄实现**。
3. **源码门禁（正向白名单 + 反向禁止）**：
   - 正向：凡声明 `usesHoldFire3()` 且构造 `Simulation` 的文件必须在名单内（`policy-input.ts`、`export-eval-game.ts`、`export-rl-rollout.ts`、`export-dagger-beta.ts`），漂移即红；
   - 反向：`export-dagger-labels.ts`、`export-rl-rollout.ts`、`export-dagger-beta.ts` 不得出现 rules-on 的开关传参（train 链恒关，防「将来翻默认」）；
   - 不要用「`buildModelFromText` + 字面 `implements InputLike`」当判据：`export-dagger-beta` 的类没有 `implements` 字面（`:42`），会漏判；legacy 工具会误判。
4. `chain` 矩阵：`train` 恒关（`export-rl-rollout`、`export-dagger-labels`、`export-dagger-beta`）；`deploy`（`export-eval-game`、NNInput-deploy）；`diagnostic`（nn-trace/divergence-probe/gap-text-table）。
5. DoD #12 parity 至少覆盖 **eval-game + rl-rollout** 两条内联链。
6. 内联 `ScriptedInput` 目前不持 World 引用；接 resolver 时须在构造时注入 world（或由调用方每 tick 传入），不能复制一份 World 读法。
7. `policy-input.ts:105-106` 与 `InputLike` 的契约注释同步改为：「forward 只在决策点；getter 可做无状态纯函数解析」。

### 8.3 部署纪律

- **开关**：课程文件 `inference_rules` 块（默认全关，键名 = 部署面英文名）；当前唯一键 `skip_steel_only_shots`。评估链透传：`export-eval-game --skip-steel-only-shots`、agent `?skipSteelOnlyShots=1`、`run_local_eval_game(skip_steel_only_shots=…)`；节点能力位 `skipSteelOnlyShotsSupport`（旧 agent fail-closed）。训练链工具（rl-rollout/dagger）源码门禁禁止出现该开关。
- **产物归因**：eval JSON / ledger / 回放 pack manifest 记录 `inferenceRules`（或 `rulesVersion`）——回放存的是 resolved 动作，播放不受影响，但必须能查出该批读数用了哪个 wrapper。
- 只 **commit**（`git commit -F tmp/<ascii-file>`）；**禁止推送**。
- **本批训练 rollout 恒关**（阶段策略，不是永久答案）；每个新 checkpoint 同时评 **bare 与 deployed wrapper**；不得未开课/未记 DECISIONS 就把规则悄悄开进训练 rollout。
- parity 扩开关对称性（joint 4 处漂移 + 无无开关 arm）。
- 门禁：typecheck / 全量测试 / freeze gate；节点 `ts_code.zip` 重打包 + 重启。
- 部署后周期 eval 若吃旧 `code.zip` 快照，判决只用外部横评。

---

## 9. 否决/休眠档案（不要重开）

- **A1**：前提错（v3 整发跳过，无偏弹）；**A4**：空转；**B3**：无弹槽门控；**B9**：safe-base 休眠；**C9**：obs 语义；**F2**：已实现；**F3**：时序前提错；**B10**：休眠嫌疑。
- **`--hold-fallback`**：−8pp（停有防守价值）；**`--joint-safe`**：−3.86pp（17/35 关更差）；**`pickupBias`**：+1.1pp 噪声内。
- **DAgger v1/v2/β 混合**：34.38 / 38.62–43.38 / 52.12，均不敌 g1c 54.88。
- **bc-v6**：win 53.85%，但 65/200 局开局冻死、11 关 0%，不接 PPO。
- God 阴性对照：§200 局部 +5/全关 −30、§216 −116、§239 all-on、§193-D 只拦 0.6%、A28 单位陷阱。
- God 规则只作机制参考，**不作效应量/门槛**；A1 后 God 78→59% 是「旧高胜率靠引擎 bug」的证据。

---

## 10. 评审响应摘要

- **qw 评审**：采纳其核心（`computeMasks` schema 钉住、纯可加联合空转、门槛功效不足、固定面板、safe-base 基地不可摧毁、6 mover/parity、指标字段、部署作用域）；保留意见：A2 可直接读 World 不需私有 `lastDir`；A1 换形态为 A3；B6 保留但后被 k3 判无干预点而删。
- **k3 评审**：采纳其三个 P0 阻塞（A3 逐拍 resolver、control 臂不能估 D、文档必须 canonical 重写）与候选级修正（E2/D5/B6 删除、A2/A3 是 arm、D1 重定位为链路探针、B8/B11 不得直接进 P5、G2 不作 go/no-go、BFS 一次从玩家出发、训练链恒关是阶段策略、指标口径）。未采纳：无（k3 的代码事实均复核成立）。
- **glm 评审（v3.1）**：4 个 P0 全采纳——① §8 集成拓扑重写（7 个 NNInput 构造点 + 4 个内联导出器 + dagger 标签链 chain=train + 源码门禁 + parity 扩 rl-rollout；**ds 轮修正为 5 个工具点 + 3 个 v6 内联 + 2 个 legacy**）；② §4.1 恢复 k3 预注册判据表并给数值、探针版 usefulFire 与 P0a 同一实现、基线 seed 段归属与确认段信息隔离；③ §2.3/§4.1 A3 剂量按 r>K / r≤K 分层、补净弹数差与首发延迟，v1 usefulFire 收窄为「可命中敌」（brick 归 B2）；④ §4.2 功效改 cluster-aware、bootstrap 程序写死、`+pp > MDE ⇒ abort`（**ds 轮判方向反了并修正**）、`+pp` 附 MDE 推导。
  - P1 采纳：B1 v1 无拦截豁免与 k3 建议方向相反，**如实记录为有意选择**（先保守抑制、后置精确豁免），并加反事实探针量化误杀；resolver 改 packed number、每 tick 2 次、v1 无 memo、记录动作=resolved；DoD 补第 13 条换发时间线。
  - P2 采纳：F1 K≠10 仅诊断；DoD #10/#11 限定范围；P2 析因决策树；P-1b 顺手量 C 族/走位族暴露率。
  - 未采纳：无（glm 的代码事实与统计论证均复核成立）；唯一保留是 A2 可直接读 World、不需私有 `lastDir`。
- **ds 评审（v3.2）**：3 个 P0 全采纳——
  - ① §8 拓扑再修：`export-dagger-beta`（v6、train 标签链）补进接线面与 chain 矩阵；`export-nn-replays`/`record-games-video` 判为 legacy 5/2、移出接线面；源码门禁改正向白名单 + train 链反向禁止；真实接线面 4 处代码（NNInput/eval-game/rl-rollout/dagger-beta）。
  - ② §4.2 统计：主检验 = CI 不含 0；+3pp 为政策门槛并明确其边界功效≈50%；加 futility gate（pilot ≥+5pp 才冻结）；abort 改 `MDE₀ > 3pp ⇒ 加 n 或杀`；MDE 与 +pp 分开报告；若必须认证 ≥3pp 则用边际检验并另算预算。
  - ③ §0/§5 拆 P-1b-0（纯谓词 + 单测冻结）→ P-1b（测量）→ P0a（接线），解除循环依赖。
  - P1 全采纳：`usefulFire` ±19px 车体带 + 复用 `blocksBullet`/`laneOccluded` + 钢恒阻挡 + spawn/decoy/ally 边界；剂量拆 gate-ready/blocked、净弹数差改窗口局部反事实、`Σ min(r, 窗口长)`；DoD #14（v6 rules-off）/ #15（谓词矩阵）/ #16（gate 时间线）；P-1b 协议清单（weights sha12 / hard / 35 关 / 20 seed / K=10 / decisionEvents=false / maxTicks）；程序级家族 Holm + 分层安全线数值；A3×B2 砖机会成本探针；`cooldownRejectTicks` 复用；B1 只改 firing 不改 intent；引擎级替代记入 §2.1 与 DECISIONS。
  - 未采纳：无（ds 的代码事实与统计推导均复核成立）；唯一自定是「+3pp 政策门槛 + futility gate」的组合（评审给了 A/B 两方案，本版取 A 的推断口径 + 政策门槛，并把 B 作为预算允许时的升级路径）。
- **P-1b 实测（2026-11-13，A3）**：700 局 control，exposure 0.0082%、loss 0.0017%、trigger 93.2%、r>K=0、net shot +1 ⇒ **A3 降级，不进 P0a/P0b**；B1/B2 继续单条，C/走位待测。原始 per-game JSONL：`tmp/a3-dose-control-{0..4}.jsonl`；聚合脚本：`tmp/a3-dose-aggregate.ts`。
- **P-1b C/走位 pilot（2026-11-13，175 局）**：C1 threat **8.62%**、C4 safe window **12.84%**（shield 7.87/freeze 5.11；窗内拾取 18/17%、近敌 29/37%）、E1 远敌 **36.44%** / 面朝墙 **13.71%**、B4 冰 **4.41%**（冰关 47–53%）；C2 events 待测。⇒ C1/C4/E1 过剂量门，B4 局部；顺序 C1 → C4 → E1。产物 `tmp/pos-dose-{0..4}.jsonl`；聚合 `tmp/pos-dose-aggregate.ts`。
- **C1 A/B（2026-11-13，175 对 screening）**：control **39.43%** → rule **26.29%**，Δ **−13.14pp**；McNemar b=43/c=20 **p=0.0052**；bootstrap 95% CI **[−21.71, −4.57]**；kills 14.47→13.25；`threatTurn` 3050→3455（未下降）。⇒ **C1 v1 判负，不做确认/部署**，注册表 dormant；同公式不得重跑，变体需新预注册 + 新筛选段。产物 `tmp/c1-{control,rule}-{0..4}.jsonl`；聚合 `tmp/c1-ab-aggregate.ts`。
- **C4 A/B（2026-11-13，175 对 screening）**：control **39.43%** → rule **38.86%**，Δ **−0.57pp**；McNemar b=18/c=17 **p=1.0**；bootstrap 95% CI **[−6.29, +5.71]**；kills 14.47→14.70、lives 0.726→0.737、clearT 11105→10633（−4.3%，次要）。⇒ **win 无改善，不部署**；C4 v1 降为 dormant；变体需新预注册。产物 `tmp/c4-{control,rule}-{0..4}.jsonl`；聚合 `tmp/c4-ab-aggregate.ts`。
- **C2 A/B（2026-11-13，175 对 screening）**：control **39.43%** → rule **43.43%**，Δ **+4.00pp**；McNemar b=29/c=36 **p=0.457**；bootstrap 95% CI **[−5.71, +13.14]**；事件决策 **1.38%**、每局 **+72.5** 决策、平均 Δt 10→9.87、墙钟 **+14.1%**；stage 1 −80pp 触发安全线。⇒ **未过 futility gate（+4.0 < +5pp）**，不做确认/部署；C2 v1 降为 dormant。产物 `tmp/c2-rule-{0..4}.jsonl`；聚合 `tmp/c2-ab-aggregate.ts`。
- **E1 A/B（2026-11-13，175 对 screening）**：control **39.43%** → rule **32.57%**，Δ **−6.86pp**；McNemar b=37/c=25 **p=0.162**；bootstrap 95% CI **[−16.0, +2.86]**；kills 14.47→14.14、lives 0.726→0.617、clearT 11105→10633；墙钟 **+20.0%**（BFS 每决策）。⇒ **不部署**；E1 v1 降为 dormant；同公式不得重跑，部署级需 caller-owned scratch。产物 `tmp/e1-rule-{0..4}.jsonl`；聚合 `tmp/e1-ab-aggregate.ts`。
- **B1 A/B（2026-11-13，175 对 screening）**：control **39.43%** → rule **46.86%**，Δ **+7.43pp**；McNemar b=27/c=40 **p=0.142**；bootstrap 95% CI **[−1.71, +16.0]**；机制 1,410 个顶钢发射抑制到 **0**；control 与旧 probe **0 mismatch**。⇒ **过 +5pp futility gate，允许冻结 20-seed 确认**；pilot 不显著，不部署。产物 `tmp/b1-{control,rule}-{0..4}.jsonl`；聚合 `tmp/b1-ab-aggregate.ts`。
- **B1 确认（2026-11-13，700 对，seeds `2000003001..3020`）**：control **255/700 = 36.43%** → rule **297/700 = 42.43%**，Δ **+6.00pp**；McNemar b=87/c=129 **p=0.00516**；bootstrap 95% CI **[+2.57, +9.57]**；机制 5,630→0；kills 14.45→15.00、lives 0.716→0.836、clearT 11331→11019；墙钟 −24.3%。stage 19 原 −20pp 回归在 20 个新 seed（`2000003021..3040`）未复现（control 1/20 vs rule 4/20，+15pp，p=0.375）⇒ **确认通过**；部署待 `export-eval-game` 接线 + parity，训练链保持 off。产物 `tmp/b1-confirm-{control,rule}-{0..4}.jsonl`；聚合 `tmp/b1-confirm-aggregate.ts`。
- **B1 接线 + parity（2026-11-13，已完成）**：`tools/sim/export-eval-game.ts` 接 `--skip-steel-only-shots`（默认关；在 `scripted.setAction` 前复用 `steelOnlyShot` + `turnWillCommit`；报告开启时写 `inferenceRules.skipSteelOnlyShots=true`）。关闭态 vs HEAD `af253ba8` 旧版（`tmp/parity-base` worktree）：stage 0/7/31、seed `2000003001`、ep80、hard、maxTicks 36000 的 `_eval_report.json` **3/3 SHA256 逐字节一致**；开启态 stage 0 仅多 `inferenceRules.skipSteelOnlyShots=true`，stage 19 ticks 4298→4195 / kills 2→5 / hitRate 0.226→0.387 / score 0.098→0.131（门确实生效）。`tests/nn/inference-rules.test.ts` **21/21**（12 usefulFire + 9 steelOnlyShot）+ 2 条部署面源码门禁；`bun run check` **2759 pass / 10 skip / 0 fail**（2769 tests）。训练链 `export-rl-rollout`/`export-dagger-beta` 未动、保持 off；**未部署**。
- **课程 `inference_rules` 块（2026-11-13）**：课程文件新增专用块 `inference_rules: { skip_steel_only_shots: false, break_bricks_on_path: false }`（默认全关；`extra="forbid"` 拼错即拒）。只由 eval 链消费：`eval_course_once` → `args.skip_steel_only_shots` / `args.break_bricks_on_path` → 本机 `run_local_eval_game` / 远端 `fetch_task`（`?skipSteelOnlyShots=1` / `?breakBricksOnPath=1`）→ agent 拼 `--skip-steel-only-shots` / `--break-bricks-on-path`；节点能力位 `skipSteelOnlyShotsSupport` / `breakBricksOnPathSupport`（旧 agent fail-closed）；任务键后缀 `:skipSteelShots` / `:breakBricks`；report 归因 `inferenceRules.*`。该块**不进** `corpus_identity_fp`，rollout 命令（`worker.cmd`）不读；`nn-training/tests/worker/test_inference_rules_course.py` 与 `tests/nn/inference-rules.test.ts` 源码门禁钉住。`bun run pygate` 全绿（6018 passed / 10 skipped）。
- **B2 A/B（2026-11-13，175 对 screening，两臂 B1 开，判负）**：control **82/175 = 46.86%** → rule **76/175 = 43.43%**，Δ **−3.43pp**；McNemar b=37 / c=31，**p=0.545**；按关 bootstrap 95% CI **[−12.0, +5.14]**；kills 15.41→14.90、lives 0.891→0.886、clearT 10817→10685、墙钟 −7.8%；机制 control 臂有 **2,095** 次「砖在前进方向 2 格内且策略没开火」机会（=0.96% 决策，约 12 次/局）。⇒ **win 无改善 + CI 跨 0 ⇒ 不做确认/部署；B2 v1 降 dormant**（完整部署面保留为默认关的实验开关）。产物 `tmp/b2-ab-{control,rule}-{0..4}.jsonl`；聚合 `tmp/b2-ab-aggregate.ts`。
- **B4 剂量 + A/B（2026-11-13，冰关 80 对，两臂 B1 开，判负）**：剂量 175 局：冰面滑行 4.15% 决策，滑行中反向请求 7.18%（全局 0.28%，约 3 次/局）。A/B 只跑冰关 16/23/27/31 × seeds 2000002001-2020（80 对）：control **43/80 = 53.75%** → rule **36/80 = 45.00%**，Δ **−8.75pp**；McNemar b=23 / c=16，**p=0.337**；按关 bootstrap 95% CI **[−22.5, 0]**；kills 16.28→15.64、lives 0.775→0.725、ticks 8571→8106；机制反向请求 **1,992→29**（规则确实生效）。stage 27 −30pp、31 −5pp、16/23 中性。⇒ **win 无改善（点估计为负）⇒ 不部署；B4 v1 降 dormant**；`releaseReverseOnIce` 只保留为 NNInput 默认关的实验口（未接课程部署链）。产物 `tmp/b4-ab-{control,rule}-{16,23,27,31}.jsonl`；聚合 `tmp/b4-ab-aggregate.ts`；剂量 `tmp/b4-dose-{0..4}.jsonl`。
- **计数**：以 §3 逐行状态为准（43 项），不使用「约」计数。
