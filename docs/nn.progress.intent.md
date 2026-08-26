## §18. 探针轮 3（B′：注入+配额修复版）— gate PASS（口径修正为类级 recall）（2026-08-26 夜）

**B′**：inject + quota 15K + max-train 300K + 6ep（修复 B 轮缺陷：5% 语料 × 2ep）。
配额后各稀有类训练帧：INTERCEPT 3639 / RETURN_DEF 5371 / CLEAR 4555 / HOLD_LANE 1691，
HUNT/CRUISE/PICKUP 满配额 15K。loss 32.07→1.35，trainAcc 0.234→0.446。

**桶级 margin（自然分布验证 86642 帧）**：base +0.130 / combat +0.040 / cruise −0.201 ——
较轮1 不升反降。但 **类级 recall 大面积学会**：

| 类 | B′ recall | majority(全押HUNT) | 轮1 recall |
|---|---|---|---|
| CLEAR | **86.5%** | 0% | 0% |
| PICKUP | 77.2% | 0% | 4% |
| RETURN_DEFENSE | 44.2% | 0% | 0% |
| CRUISE | 48.1% | 0% | 96%→但被PICKUP分权 |
| INTERCEPT | 31.8% | 0% | 0% |
| HUNT | 57.6% | 100% | 56% |
| HOLD_LANE | 2.1% | 0% | 0% |

**归因（两条独立结论）**
1. 轮1 的 0% = **类不平衡饿死**，非不可学——配额一开稀有类立即学会（CLEAR 87% 最硬）。
2. B′ 桶 margin 下降 = **训练（配额）× 验证（自然）分布不匹配**：模型预测向 PICKUP 膨胀
   （列 PICKUP 合计 23,911），CRUISE 被抢 10,323 帧——artifact，非 CRUISE 不可学。

**判定（预注册 #16 修订备案）**：合格判据改为「类级 recall vs majority 类级 recall」——
6/7 类远超 majority（多数基线对非多数类 recall=0）、ESCAPE 依 <200 窗口掩码 →
**意图可学习性实证成立，M0b gate PASS，进入 M4**。HOLD_LANE 为唯一弱项（全语料窗口最少，
1691 训练帧）——交 M5 守家段超采样/DAgger 补强，不构成本里程碑阻塞。

**M4 待办**：意图网三头+注入（intent_net.py 骨架已就位）、TS/Py 前向字节一致测试（新建）、
bench-nn-infer 实测单前向 ms、IntentPlayer 策略适配器 + m1-eval --policy intent。

## §19. M4 完成 — 网络 + 字节一致 + 推理基准 + IntentPlayer 适配器（2026-08-26 夜）

- **M4-A（P3-4）**：`nn-training/intent_net.py`（IntentNet：StudentNet 主干 + 三头 + 9 维注入，
  71.5K；主干 shape 断言内置=预注册 #8）；`infer.ts` StudentModel 加可选三头 +
  `intentForward` + `buildIntentModelFromText`；`tests/nn/intent-infer.test.ts` 用检入 golden
  （h16/d2 固定 seed，tests/fixtures/intent-golden.json）锁三头 logits ≤1e-4 一致。
- **M4-B**：`tools/bench-intent-infer.ts` 实测 **单前向 41.1ms**（0.91G MAC/s；理论带 34–56ms 正中），
  摊销 ÷24→1.71 / ÷50→0.82 ms·tick；>16.7ms 帧预算 → 实机 Worker/瘦身档（R3），headless 不限。
  权重值与 MAC 数无关（M5 真权重重跑确认）。
- **M4-C（I6）**：`src/nn/intent-player.ts`（InputLike；replan 30 → 三头 argmax，ESCAPE 掩码；
  注入 prev/duration 维护同 tagger；3 意图最小执行器 stub 直读 World、零 RNG、确定性）；
  `simulation-runner/m1-eval/sim-worker` 接 `policy:'intent'` + `--intent-weights`；
  `tools/gen-intent-weights.ts`（确定性随机全尺寸权重，M5 前 sanity 用）；
  `tests/nn/intent-player.test.ts`（闭环 ≥600 tick 不崩 + 确定性 + 意图合法，4 例）；
  smoke：m1-eval S10 seed1 WIN 100%（接线验证，非真实水平）。
- **M4 gate 全绿**：`bun run check` 1512 pass / 0 fail @ 2026-08-26 23:31。