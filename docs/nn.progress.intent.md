## §16. Intent-Policy 探针轮 1 — 可学习性前置探针（M0b 首轮，2026-08-26 夜）

**结论：gate FAIL。** 全量 2100 局 hard 机械打标（`tools/sim/export-intent-labels.ts`，
21s @8 workers）→ intent-8 分类器（StudentNet 主干 + intent 头，120K 帧自然分布、3ep、
局级 train/val 切分、same-seed）。

| 桶 | acc | majority | margin | 判定 |
|---|---|---|---|---|
| base 基地高压 | 0.563 | 0.420 | **+0.143** | ✅ |
| combat 交战 | 0.627 | 0.562 | **+0.065** | ❌ <0.1 |
| cruise 巡航 | 0.596 | 0.582 | **+0.015** | ❌ 近噪声（n=4187） |

Overall acc 0.594。**confusion 病根**：模型全行只落 HUNT/CRUISE 两列——稀有类
（INTERCEPT 1113 / RETURN_DEFENSE 1143 / HOLD_LANE / CLEAR 帧）完全未被学习；
combat/cruise 桶分歧核心 = **HUNT vs CRUISE 的 endgame 切换**（单帧无时序上下文）。

**处置：按 §3.6 失败五径第一轮 = ①.5 注入版探针（prev-intent+duration teacher-forced，
M4 §4.2 注入同构）+ P2-2 配额采样（每类 15K）**——train_intent_probe.py 加
`--inject`/`--quota` 后重跑。预期：若 margin 显著回升 → 归因「缺时序上下文」、
词表可学（M4 注入特征为必要结构）；若不升 → 升级 ②改词表 / ③obs 时间堆叠。

**附带交付**：幽灵表双口径（自然分布窗口：PICKUP 20395 / HUNT 15469 / CRUISE 14664 /
RETURN_DEFENSE 4374 / INTERCEPT 3047 / CLEAR 778 / HOLD_LANE 692 / **ESCAPE 0 → reflex-only 掩码**）；
±5 tick 翻转率 4.86%；两遍法逐字节确定性与三桶谓词单测已进 CI；M1 tagger 接地钩子
（intentTaggerMode，ON/OFF 字节等价）已落地。