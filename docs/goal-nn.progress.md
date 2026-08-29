# Goal-Space Policy NN — Progress Log（plan/Goal-Space-Policy-Rebuild.md 执行日志）

> 按 AGENTS §5.6 / 用户指令建立。新条目置顶（倒序）。架构改动 / 评估结果 / 教训都记这里。
> 任务卡编号（T0–T12）与规格 § 号均指 `plan/Goal-Space-Policy-Rebuild.md`。
> NN 训练统一经 `nn-training/start-training.sh|.ps1` 启动（AGENTS §5.6 硬规则）。

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
