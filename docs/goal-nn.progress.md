# Goal-Space Policy NN — Progress Log（plan/Goal-Space-Policy-Rebuild.md 执行日志）

> 按 AGENTS §5.6 / 用户指令建立。新条目置顶（倒序）。架构改动 / 评估结果 / 教训都记这里。
> 任务卡编号（T0–T12）与规格 § 号均指 `plan/Goal-Space-Policy-Rebuild.md`。
> NN 训练统一经 `nn-training/start-training.sh|.ps1` 启动（AGENTS §5.6 硬规则）。

## §3 T9a 金丝雀：门 FAIL 与三重归因（2026-08-29，commits b04f4d6..b84c012）

### 训练（本地 10 槽，~14 min / 6 轮）

```
./start-training.sh --script run_rl_intent.py --goal --goal-coarse   --bc nn-training/tmp/goal-bc/weights.json --out nn-training/tmp/goal-rl-t9a/weights.json   --iters 6 --warmup-iters 1 --kickstart-kl 1.0 --kickstart-decay 0.85 --heartbeat 240 --eval-at 999
```

BC：15 epochs loss 59→**4.60**（39,663 点，λ=0.5 τ=1.0，长样本 ×3 加权）。
PPO：140 局/轮 ≈2,200 步；熵 3.15→3.33；value 326→1.65；KL 每 epoch 触发 target_kl=0.04
早停；it6 出现首个 stage_clear（1/140）。

### 门判定（tools/sim/paired-gate.ts，2100 局 --policy goal vs 基线 78.81%）

**canary ② FAIL**：overall **−78.81pp ± 2.49**（goal 0.05%）；B 桶 −83.18pp / C 桶 −78.91pp
非劣界双破。**canary ① PASS**（学习机器正常：BC loss 59→4.6；同网格击杀
random 0.9 → BC 2.9 → PPO 2.9 单调）。

### 三重归因（两次反转的追查，全部记录以警示）

1. **伪影**：首个 "executor-ceiling 85%" 是 `--policy goal-god` 未接入
   m1-eval/simulation-runner policy 链、静默回落纯 God-AI 所致（配对差 0.00pp = 同跑暴露）。
   **教训：新增 policy 必须先做与已知策略的可区分性冒烟（同网格 avgKills 对比）。**
2. **坦克冻结 bug**：接线修复后 goal-god 仍 0% → 插桩发现 989 tick 只走 5 格。根因 =
   §6.1.1 可满足性门 "travelEst ≤ T 否则拒绝重选" 在 T=240（≈10 格 @23 tick/格）下形成
   **移动拴绳**：到达首个目标后任何更远目标被永久拒绝，E4 缓解分支 keeps 旧契约 ⇒ 冻结。
   这是**规格内在矛盾**（承诺期 T=H≤240 vs 地图级 travelEst 300–500 tick），实现期选错了
   语义。**修复**：可满足性门只拒不可达（travelEst=∞），T 只管重评估节奏（commit b84c012，
   含单测更新）。
3. **修复后的真话**：2100 局 goal-god（God-AI 导航目标喂执行器、零网络）＝ **0.0%**
   （−78.81pp ± 2.49）。执行层短板与目标选择、学习无关：L2 裸 A* 跟随 + L3-min 开火
   （顺路+凿墙）+ 窄 dodge（仅 dodge 候选提交时生效）+ 无道具 + 承诺期 240 tick 不回防，
   在 hard 下无生存能力。God-AI 的 78.8% 靠 19 候选全链 + 威胁感知导航 + 主动道具
   （§293 解冻）+ 逐 tick 重定向。

**T9a 最终判定**：②FAIL 且在独立执行器架构下**不可测**（执行器 −78.8pp 淹没一切目标轴
信号）；手册 T9a 隐含前提"执行器 + 好目标 ≈ 竞争力下限"被证伪。T9/T10 暂停。

### 三条路线（待用户决策；本 agent 推荐 B）

- **A. 按手册止损**：机时转 T3——但 T3（导航参数化）量级不足以填 78pp。
- **B. 换集成口径重定义 canary（推荐）**：网络热图作为**目标提供者嵌进 God-AI**
  （替换其导航目标选择，保留全部逐 tick 执行链）——在 78.8% 级执行器上单独测目标轴
  边际贡献。与 R1"反掩码化"不冲突（网络选目的地，不是候选链剪枝）；
  是 §9.2"当函数库/保底层"哲学的自然延伸。改动小、可快速重跑 canary。
- **C. 把独立执行器练到竞争力**：真 L3（§10 全量开火纪律）+ 威胁感知路径 + 道具 +
  回防——本质是重写 God-AI 的执行技艺，风险与工期最大。

### §T9.0（k9）预注册归因处置

新基线由 super-item 恢复 + pursuit-tail 造成，与 T2 承诺机制无关 ⇒ "红利重叠"不适用；
T2 本轨未做 ⇒ 只对新基线判定。

---

## §4 m1-eval auto-dist：每批评估机会性利用远程 agents（2026-08-29，commit 61538b6）

用户指令：远程节点随时可能上线，m1-eval 要像 rollout 一样周期性检查节点状态、每批都
充分利用 agents 算力（"每次跑批能缩短一两分钟都是好的"）。

**缺口 → 修复**（v4.0）：
1. `--dist-nodes` 是 opt-in → **auto-dist 默认开**：不传时默认读 `nn-training/rl-config.json`
   （存在即走混合分派）；`--no-dist` 显式关闭。v3.9 rescan（120s 周期重读配置 + ping）保持
   ——节点中途上线即刻接管份额。死节点开销 = 并行 5s ping（实测 7 死节点配置下 6 局
   总墙钟 5.2s，本地立即开跑）。
2. **无权重策略不可分发** → `kind='none'` 占位桶（3 字节，wver 协议兼容）：
   `god` / `goal-god` 进 DIST_POLICIES 白名单——基线/上限这类最常跑的 2100 局批
   现在可全量外派。
3. **潜在 409 bug**：`uploadWeights(node, 'intent', ...)` 写死 kind——goal 分发会在活节点上
   wver-not-cached 409（此前无活节点从未暴露）。改为随 distKind。
4. **export-eval-game 无 god 分支** → 新增真 God-AI 分支（RNG 派生与 runSimulation 逐字节
   一致；weights 仅 'nn' 策略必需）；顺手修 `scripted.reset()` 写死导致的
   **GodAIInput.reset() 从未执行** bug（关卡自适应参数从未生效——远程 god 局此前
   等于默认参数乱打）。

**护栏测试**（tests/sim/eval-game-parity.test.ts）：
- 远程/本地等价：export-eval-game god ≡ runSimulation god（4 局 outcome/ticks 逐一对齐）。
- **可区分性冒烟**：god vs goal-god 同局必须不同（goal-god 静默回落伪影的永久哨兵）。

**遗留提示**：run_rl_intent 训练器的 post_weights 仍有 60–300s 死节点超时（每轮一次），
靠"配置离线"绕过；如需训练器也 auto-dist 可复用本套 ping-first 激活。

---

## §2 T7.2 goal PPO 基建 + T6 反事实标注（2026-08-29，commits b04f4d6/f74a7cb + pilot）

### T7.2（全绿）

- `nn-training/ppo_goal.py`：GoalRLNet + **双动作空间**（fine 676 / coarse 169 块 logsumexp，
  §T9a.1b）+ multi-head loss（surrogate_clip 主项 + 可选 BC kickstart + engage 辅助 CE +
  value MSE + 熵 + KL 锚）+ ppo_common 变步长 GAE + value warmup + stream backend 别名。
- `tools/sim/export-goal-rollout.ts`：goalPick 回调式采集器（与执行器共享 L2/L3 代码路径）；
  §12.3 奖励 = R_event（继承 INTENT_REWARD 量级）+ 到达 1.0 + 守家 0.5（γ^dt telescoping）
  + 交战效率 0.3；shard 含 goal_mask（u1）/dt/inject/engage，manifest 记 firePolicy（§11.3.1）。
- rl/ goal_rollout 分支：queue 采集命令 ×2 + wkind='goal'，stream semi-MDP 波次语义，
  sampler-agent kind=goal 桶 + heartbeat/goalCoarse URL 参数，`run_rl_intent.py --goal` 开关
  （同一主循环/熔断/巡检/断点续跑复用）。
- `test_ppo_goal.py`：dt 退化字节一致、coarse logsumexp 单调/可导、双空间掩码 logp
  （被 mask 动作 < −1e8）、微型 shard 冒烟 + warmup 冻结断言。
- **采样/训练一致性修复**：coarse 块 logit 两侧统一为"全 4 格 logsumexp + 块级有效性过滤"
  （采集器原按可达格聚合，importance ratio 会偏）。
- 前向实测 **~64ms**（h=64，含间隔 sim），比 §11.9.1 保守口径（110–170ms）快 ~4.5×
  ⇒ hb240 on-policy 单局 ≈1.1s，140 局/轮单核 ≈2.6 min。

### T6（全绿 + pilot 已跑）

- `tools/sim/export-counterfactual-goals.ts`：God-AI 状态分布 + 候选生成（§11.2 六来源 +
  §11.4 确定性 top-K + 来源标记）+ cloneWorld 分支 rollout（§T6.1b，每分支一次克隆）+
  **多窗口检查点打分**（一次 480-tick 分支产出 {60,120,240,480} 四档分数，4× 省时）+
  inject 自馈流（prevGoal = 上决策 argmax）+ cand_src 来源标记 + engage 逐窗口。
- `tools/sim/cf-goal-worker.ts` + WorkerPool：并行 == 串行**逐字节一致**（哈希对账）。
- `tools/sim/cf-hsweep-report.ts`：§11.8 三判据判读。
- `nn-training/train_goal_bc.py`：软目标 + 全 676 维稀疏 CE（λ/τ 训练超参，shard 存原始
  (s_i,k_i)）+ engage CE + **长承诺样本加权**（§8.1.1 a1 缓解#2）。

### Pilot（350 局 = 35 关 × 10 seed，replan30×210 + replan240×140，K=12）

吞吐：**2.06 s/局**（8 workers；含四档窗口分支）⇒ 350 局 ≈ 12 min，符合 T6 验收口径
（≤15 min@6 节点折算）。语料 383MB / 39,663 决策点 / 覆盖率 0.981。

**§11.8 H 扫描定案**（argmax 落点占比，λ=0.5）：

| window | enemyRear(追尾) | anchor(守家) | carve/brick | godTarget 重合 |
|---|---|---|---|---|
| 60 | 8.9% | 0.6% | 1.0% | 72.8% |
| 120 | 21.5% | 1.5% | 2.9% | 56.6% |
| **240** | **39.1%** | 2.9% | 5.7% | 36.9% |
| 480 | 52.7% | 3.5% | 10.5% | 22.9% |

长窗口系统性恢复追尾行为（§11.8 "短窗近视"论断实证成立）；480 超 §11.7 上限 ⇒
**操作点 H = T = 240**。

**§8.1.1 检查⑤（duration 覆盖）诚实记录**：按决策点算长承诺（≥0.5）占 ~12.8%
（replan240 局决策点少 ⇒ 局数占比 40% ≠ 点数占比），低于 50% 目标。缓解：BC 长样本
加权 ×3.0 + PPO on-policy 在 hb240 自行覆盖部署分布。记为 T9a 已知风险。

### 实现期新决策（续 §1）

9. **H 扫描四档共用一个 max-H 分支**：分支在检查点打分，RNG 连续性保证与独立跑一致
   （省 4× 机时；代价是 cand_s 按窗口分文件）。
10. **CF 语料 replan 混合**：210 局 replan=30 + 140 局 replan=240（局数口径 40% 长承诺，
    对齐 a1 缓解#1 的"≥1/3"精神；点数口径不足部分由加权补偿）。
11. **T4 依赖以 L3-min 替代**：T6 的 rollout 开火 = FireControl 原样（§T6.1a 钉死的
    "现有 L3 规则"），未做 T4 的 lateralFire 扫参——T6 依赖修正记账，T5（开火 canary）
    暂缓不影响本轨。

---

---

## §1 网络轨落地：T7 → T8-min → reach-mask → T8.5（2026-08-29，commit 9be15d2）

全部按手册规格实现，`bun run check` 全绿。文件与验收：

| 卡 | 交付 | 验收 |
|---|---|---|
| T7 | `nn-training/goal_net.py`（GoalNet：goal_conv 1×1 on bufA + engage 137→2 + value 137→1）；`src/nn/infer.ts` `goalForward()` + `buildGoalModelFromJson`；`src/nn/goal-inject.ts` §8.1.1 语义表 | `tests/nn/goal-infer.test.ts`：goal/engage/value 三头 TS/Py 一致（热图 1e-3 / 标量 1e-4）；inject 语义 + 保留维恒 0 断言 |
| T8-min | `src/nn/goal-contract.ts`：E1/E3/E5/E4 定序评估 + travelEst≤T 校验 + 默认 premise（baseIntact/playerOperable/stageInProgress + 归因 label） | `tests/nn/goal-contract.test.ts`：三谓词触发/不误触发、E3 动态占位不触发（§6.6 防抖）、E4/E5 边界、E3>E1 定序 |
| T3 子件 | `src/ai/goal/reach-mask.ts`：ReachMasker 池化 Dijkstra（k 砖代价字典序、51 越界格硬遮、revision+start 备忘、零堆分配）+ `selectGoal` 平局取低索引 | `tests/nn/reach-mask.test.ts`：findPath 交叉验证（k=0 ⟺ walk / k<∞ ⟺ carve）、确定性、GC 断言（1000 次 <1KB）、平局防漂移 |
| T8.5 | `src/nn/goal-executor.ts`（L0 dodge 硬约束借 god reflex `_lastBranch==='dodge'`、L1 argmax+top-K 可满足、L2 路径跟随 walk→carve、L3 FireControl + 凿墙开火、有序回退、重选冷却防抖）+ `--policy goal` 全链接线（sim-worker/simulation-runner/export-eval-game/m1-eval dist 白名单/sampler-agent kind=goal） | `tests/nn/goal-executor.test.ts`：同 seed 双跑逐字节一致、E4 心跳间隔= promiseTicks、E3 事件 3 tick 内触发、reset 清态；m1-eval 6 局冒烟出同构报告 |

### 实现期新决策（偏离/澄清手册，均已在代码注释标记）

1. **T7 TS 侧保留 intent 头加载能力**（手册写"删 enemyLogits/anchorLogits"）：§14.3 要求在新代码上重评 it38（判定"不劣"），intent 路径必须可加载；GoalNet（Py）与 goal 权重 JSON 不含 intent 头——"删"落在**新网络定义**上，TS 面向后兼容。与 §9.2.1"旁路不删除"同哲学。
2. **热图头 golden 容差 1e-3**（§T7.3 预案触发）：TS mul+add 与 torch gemm FMA 的固有舍入差实测 1.068e-4（>1e-4 且 <1e-3）；engage/value 保持 1e-4。
3. **reach-mask 的 k 定义**：Dijkstra 字典序最小化 (k, steps)，k = 路径上必须凿毁的砖数（含目标足印内的）；2×2 前缘两格都要凿 ⇒ 穿 1 格厚墙 k=2（写进单测）。λ 不进 ReachMasker 缓存键，mask(λ) 只重着色。
4. **可满足性校验的落法**（§6.1.1 "travelEst ≤ T 否则拒绝重选"）：top-K（K=6）按 heat+mask 降序找第一个 travelEst ≤ T 的格；全不可满足 ⇒ 强制提交 argmax（有目标优于站着不动，telemetry 记 'unsat'）。travelEst = carve-aware A* 步数 × 23 + 8×k。
5. **E4 同格续约语义**：bornTick 重置（承诺期重新起算，防 E4 逐 tick 抖动——实测 481 次 reselect 的教训）+ `pursueSince` 独立累计（inject duration 连续增长，不因心跳确认清零）+ dodgeTicks 重置（"自上次重承诺起"口径）。
6. **重选失败冷却 30 tick**：全遮/无效起点导致提交失败时不再逐 tick 重前向（实测密封场景 2709 次 reselect/8.4s 的教训）。
7. **E3 判据的 executor 侧实现**：mask 按 tileMap.revision 变化时重算（<1ms），E3 = 缓存 mask 在契约格 ≡ −Infinity；配合 §6.6"动态占位不触发"。
8. **硬遮哨兵**：kArr 用 Uint16Array(65535=不可达)，mask 特判 k=0（避免 `-0`，Object.is 语义）。

### 性能实测（本机，修正 §16.1 的 110–170ms 推理口径）

probe：h=64/d=8 goal 前向 + sim tick 间隔计 **~64ms/reselect**（含间隔内 sim）——远低于手册引用的 110–170ms。前向便宜 ⇒ hb240 的 on-policy rollout 单局 ≈1.1s（22 前向 × ~40ms + 0.2s sim），140 局/轮单核 ≈2.6 min，比 §11.9.1 的保守估计（11.9 min）快 ~4.5×。

---

## §0 基线重钉（2026-08-29，T9.0/G1 前置）

God-AI 自手册基线（75.86%，commit 16fc76a 口径）后有两个行为纪元：
`f4dbc0b` super-item 恢复（DECISIONS §293 三件套已跑）+ `97c3447`/`03a25a4` pursuit-tail
（SS302/SS303）。手册 §1 已声明旧 pinned 作废 ⇒ 重跑。

```
bun tools/sim/m1-eval.ts --stages all --seeds 1-60 --difficulty hard --policy god \
  --out reports/godai-baseline-hard-35x60.html     # 2.2 min @7 workers
```

| 项 | 值 |
|---|---|
| **胜率（新 pinned）** | **78.81%**（1655 通关 / 2100），SE 0.89pp，95% CI [77.07%, 80.55%] |
| scoreV7 | suite **0.6001** · lcb 0.594 |
| 最差 5 关 | Battlement 26.7 · Riverbed 41.7 · Bastion 58.3 · Labyrinth 63.3 · Thicket 65.0 |
| 最好 3 关 | Ramparts 100 · Gridlock 95.0 · Fortress 93.3 |
| 产物 | `reports/godai-baseline-hard-35x60.{html,log}` |

**对 T9 门的含义**：主门 = 对本新基线的配对差 ≥2pp 且 CI 下界 > 0。
God-AI 变强 ⇒ 剩余可改善空间被压缩（78.8% 之上每 +1pp 都更难）；
T9a canary 判定保持"方向为正"口径不变（§0.3.1）。
事后注：m1-eval 未单独落 `.json`（计划 §0.2 ③ 的 archetype-report `--report` 输入
需另想办法或给 m1-eval 补 `--json` 出口，T6 前处理）。

### 派工口径（与手册 §0.3.2 依赖图的差异，自主决策）

用户指令聚焦**目标策略 NN 的开发与训练** ⇒ 走**网络轨 + 数据轨 + 训练轨**：
`T7 → T8-min → reach-mask(T3 子件) → T8.5 → T7.2 → T6-pilot → T9a → T9`。
**暂缓**：T2/T4/T5（执行层轨 / 开火 canary —— 会改 God-AI 行为、触发新纪元三件套，
且 T9 卡明定 `fire_head` warm start 缺席时用随机初始化为已记录回退路径）；
T6-生产（2100 局标注按 §9.4.3 须等 T3 全卡，本轮用 T6-pilot 350 局喂 T9a，T9 语料视 canary 结果再定）。
reach-mask 虽记在 T3 卡下，但它是 T7.2/T8.5 的消费件且规格自足（G8 + 评审 a2 池化规格），
按规格独立实现，**不动 God-AI 任何默认参数**（不触发新纪元）。
