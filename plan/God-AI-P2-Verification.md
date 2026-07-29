# God AI P2 验证记录（独立复现）

> 验证方式：headless harness（`runSimulation`，classic，18000 ticks，seeds 1–30），
> 覆盖 Stage 0–4。所有数字由本记录独立重跑得出，非信任用户汇报。
> 结论：**P2 报告诚实、可复现**（与 P0/P1 同，优于早期 v3 虚假汇报）。

## 1. 代码核对（三项修复均在位）

| 修复 | 位置 | 机制 |
|------|------|------|
| P2.1fix 反驻扎区域跟踪 | `GodAIInput.ts:523–525` | 驻扎判定由精确格 `col===pc.col` 改为 ±1 格区域 `Math.abs(col-campCol)<=1 && Math.abs(row-campRow)<=1`，累计相邻格驻扎时间，破解 TANK/CELL 边界子格振荡导致的反驻扎逃逸永不触发 |
| P2.2 Nav-stuck 逃逸改进 | `GodAIInput.ts:638–664` | A* 到中心失败后，先试朝中心方向、再试任意可通行方向（不再 `directMove` 重新选敌回到死循环） |
| P2.4 预测瞄准 / Lead the target | `FireControl.ts:199–257`（纯函数），接线 `:327–328` | `predictEnemyCrossingImpl`：敌人横向移动将在子弹到达时穿越弹道则提前开火；`enemyTimeToCross=perpDist/enemySpeed`，`bulletTimeToReach=parallelDist/bulletSpeed`，时间差 ≤ `TANK/(2*enemySpeed)+6` 则开火。纯检测，不消耗 RNG |

拒绝的方案（用户已记录于 DECISIONS §41）：T2a 仅对准才开火、baseUnderThreat 扩到 row≥22/23/24、campTimeout=60（均导致其他 stage 退步）。

## 2. 独立复现结果（30 seeds, 18000t, classic）

| Stage | P1 基线（用户报） | P2（用户报） | **实测** | base 存活 | gameovers | timeouts |
|-------|------|------|------|------|------|------|
| 0 | 86.7% | 86.7% | **86.7%** (26/30) | 27/30 | 3 (s7,13,17) | 1 |
| 1 | 90.0% | 100% | **100%** (30/30) | 30/30 | 0 | 0 |
| 2 | 93.3% | 93.3% | **93.3%** (28/30) | 28/30 | 2 | 0 |
| 3 | 50.0% | 66.7% | **66.7%** (20/30) | 30/30 | 0 | 10 |
| 4 | 75.0% | 56.7% | **56.7%** (17/30) | 29/30 | 3 | 10 |
| **总体** | 80.0% | 80.7% | **80.7%** (121/150) | — | — | — |

→ 全部 5 个 stage 的胜率与用户报告**逐一对齐**。

## 3. 质量门禁

- `bun run check`：**459 pass / 0 fail**，tsc 干净，oxlint 0 warning / 0 error，oxfmt 通过。
- 回归门禁（`tests/god-ai-regression-gate.test.ts`）注释已更新到 P2（S0 86.7% / S1 100%），
  门限仍为 P1 值（S0 wins≥24 / S1 wins≥25），实测 26/30、30/30 均通过。
- Parity 基线已重锁：seed 2 gameover→stage_clear（anti-camp 修复），seed 7 clear→gameover
  （RNG 时序扰动），seed 42 lives 5→2。与 DECISIONS §41 一致。

## 4. 需要提醒的点（诚实标注）

1. **Stage 4 真实退步 −5**（75%→56.7%）。用户归因为"RNG 时序扰动 / 再分配"，但这在
   确定性模拟下是**真实的 −5 胜**，不是噪声。它发生在**回归门禁覆盖范围之外**
   （门禁只测 Stage 0/1），所以当前门禁**抓不到 Stage 4 的退化**。若后续调参进一步拖低
   Stage 4，不会被现有门禁发现——建议 P3 把门禁扩到 Stage 2–4 或整体聚合。
2. P2 是**行为修复**，不是计划原定的"重跑 CMA-ES"。用户将 CMA-ES 重跑重新编号为 **P3**。
   架构修复已就位，P3 现在跑优化器才会收敛到 >80%。
3. 剩余失败：Stage 3/4 大量 timeout（低击杀、同一卡死模式换种子）、Stage 0 gameover
   （玩家离基地太远）、Stage 4 pursuit timeout（差 2–3 敌清不掉）。这些是 P3（CMA-ES
   联合调参 + anti-stall 强化）的目标。

## 5. 结论

P2 行为修复有效且可复现：Stage 1 达成 100% 完美通关，Stage 3 从 50% 升到 66.7%，
总体净 +1 胜。代价是 Stage 4 −5（已记录、在门禁外）。**下一步 P3：在修好的架构上重跑
CMA-ES（跨难度 × 跨 stage），啃掉剩余卡死与 Stage 4 退化。**
