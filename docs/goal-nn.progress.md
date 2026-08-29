# Goal-Space Policy NN — Progress Log（plan/Goal-Space-Policy-Rebuild.md 执行日志）

> 按 AGENTS §5.6 / 用户指令建立。新条目置顶（倒序）。架构改动 / 评估结果 / 教训都记这里。
> 任务卡编号（T0–T12）与规格 § 号均指 `plan/Goal-Space-Policy-Rebuild.md`。
> NN 训练统一经 `nn-training/start-training.sh|.ps1` 启动（AGENTS §5.6 硬规则）。

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
