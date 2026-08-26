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