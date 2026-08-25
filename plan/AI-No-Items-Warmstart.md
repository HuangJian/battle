# AI 无道具 + RL 预热（Warm-Start）实施方案

> 状态：讨论收敛稿，待批准执行
> 来源：2026-08-25 会话（RL it36 审查 → 基地防守瓶颈 → guard A/B 预检 → 语料/表达/口径讨论收敛）
> 关联文档：DECISIONS §57（v7 评分）、§167（guard SHIPPED）、§6.3b（Phase III 框架）；MANIFEST §13（三扇门）；AGENTS §6/§7/§120（决策记录、先复现、失败子集复跑）

---

## 0. 讨论已收敛的结论（执行基线）

1. **所有 AI 均不使用主动道具**（guard/frenzy/rewind 一律不激活）；道具保留在场、自动生效效果保留（star/tank 等）、地雷避险保留。用户已确认此口径。
2. **guard A/B 预检数据在案**（`tmp/ab-guard-*.json`，同 seed 配对，60 seeds × 35 关）：
   - hard：胜率 76→75%，Δscore −0.0093，t=−3.77，p=0.0002（Lattice 显著变差 65→58%）
   - chaos：胜率 70→69%，Δscore −0.0105，t=−3.63，p=0.0003（Ice Palace / Thicket 显著变差）
   - classic：guard 预设本就为 0，无变化
   - 即：摘除成本 ≈ −1pt × hard/chaos，微小但显著，不构成结构性支柱。
3. **网络底座锁定 PPOStudent（CoordConv-ConvMixer，68.7K 参数，~37M MAdds）**，不再缩参数。950K RLNet 仅作离线教师/标签源，永不进 runtime。
4. **漏斗路线**：God-AI wins-only 语料（底料）+ 人类录像 near-miss（守家宝石）→ BC warm-start（gate）→ kickstart 式短 RL。
5. **一次 schema 纪元原则**：item 头删除、标量维度删除、wins-only 口径、（可能）obs 表达修补，打包进同一 `OBS_SCHEMA_MAJOR` bump，不零敲碎打。

---

## 1. 目标与约束

| 项 | 内容 |
|---|---|
| 近期目标 | warm-start 模型 hard 全 35 关**胜率（WIN）** >50%（gate；口径统一为 WIN，不再混用"保留率"，⑩）；主对照 = M3 先验（it36≈10% 为旧 schema 历史背景，仅定性参照） |
| 中期目标 | 短 RL 超过先验（不追求从零发现守家）；基地防守维度（baseIntegrity）出现首次上行趋势 |
| 硬约束 | 纯 CPU 训练；浏览器推理 <1ms；确定性/回放架构不变；不破坏 classic/chaos 回归门；不引入新依赖；人类道具玩法不受影响 |

---

## 2. 里程碑与决策门（总览）

| 里程碑 | 内容 | 门槛（Gate，不通过不放行） | 主风险 |
|---|---|---|---|
| **M0** | God-AI 摘除主动道具（参数默认归零，保留旋钮） | eval-suite hard/chaos 复测与新基线一致（±噪声） | R4：−1pt 地板侵蚀 |
| **M1** | 分歧探针：分歧定义与归因规则已预注册（§4） | 输出四选一诊断（①标签 / ②表达 / ③监督 / 兼有） | R1：表达墙未解则一切语料工程打折 |
| **M2** | 语料纪元整理：删 item 头 + 删 5 标量维（含 mirror 重编号）+ wins-only 口径 + 道具帧过滤 +（M1 判出的）obs 修补，`OBS_SCHEMA_MAJOR +1` 全量重导 | 锁步/mirror/教师管线断言 + determinism + 状态覆盖率报告 + `bun run check`；重导后 BC smoke 降 loss | R3：重导 CPU 窗口与 RL 训练争机 |
| **M3** | BC warm-start：God-AI wins 底料 + 人像 near-miss 加权（**A/B 双臂对照**，归因预注册）；value 头 MC 预置（数据=M2⑥ return 轨迹） | `m1-eval --policy nn` 贪心**胜率（WIN）** ≥50% → 进 M4；5–50% → 补充人像后复评；≈0 → 回 M1 | R2：人像覆盖稀薄/导出链路 |
| **M4** | kickstart 式短 RL（teacher 交叉熵辅助项 + 衰减 + target_kl 早停/best-eval 归档）；**守家回补追踪线（报告项，距 76% 差值）** | 胜率稳定 >50% 且超过先验；baseIntegrity 上行；追踪线报告在案 | R5：teacher 上限牵引 |

执行顺序 M0→M4 串行，每门不过则回到环内整改，**不跳门**。

---

## 3. M0 — God-AI 摘除主动道具（参数归零）

**改动**（[params.ts](src/ai/god/params.ts) `DEFAULT_GOD_AI_PARAMS` 内，§167 参数块）：

```
superItemMode: 1 → 0
superItemGuardThreat: 1 → 0
（superItemFrenzyAim 保持 0；SKILLED_HUMAN / GUARD_GOD_AI 等命名预设不动）
```

**验收**：
- [ ] `bun tools/eval/eval-suite.ts --compare tmp/ab-guard-on.json tmp/ab-guard-off.json --difficulty hard --seeds 60` 复跑，新基线（OFF）与本次预检 B 臂一致
- [ ] `bun run check` 全绿（含 `tests/super-item-activation.test.ts` 等既有测试——测试测的是旋钮机制，不测默认值，预期不动）
- [ ] DECISIONS.md 记一条（修订 §167：默认归零 + 本次配对数据）；全文进 `docs/god-ai-tuning.progress.md`

**回退**：参数回 1 即恢复，零代码回退成本。

---

## 4. M1 — 分歧探针（全方案最重要的一个门）

0% 学生（DAgger 后仍 0%）的根因三选一：**①标签选择**（败局污染/口径）→ wins-only 有效；**②观测表达**（obs 缺时序/威胁特征）→ 任何语料都无效，须先修编码；**③监督方式**（teacher-forcing 编译误差级联）→ 须换监督或加交互。

**方法**：数据源 = **DAgger 采集 shard**（`export-dagger-labels.ts` 输出的学生-教师成对轨迹，非 God-AI 单臂探针）；**新写一个分歧分桶脚本**（decision-probe/per-seed-diff 不直接适用，⑤）。后果判定用预注册代理指标，直接从学生实际轨迹提取扰动（位置偏移/击杀差），**不做 counterfactual 双臂重放**。

**分歧定义（预注册，防事后叙事）**：分歧 = 学生贪心动作 ≠ God-AI 标签动作，**且**该分歧在后续 T=120 tick 内于学生实际轨迹上引发可观测后果（玩家位置偏移 >1 cell、或双方击杀/受击事件出现差异）。不满足后果条件的"静默分歧"（tie-breaking / 浮点序 / 无后果朝向差）单独计数，**不进归因**。

**输出（按状态类别分桶，而非单一首分歧直方图）**：分歧帧按场景聚类为「基地高压 / 巡航 / 交战」三桶，输出每桶分歧率 + 特征缺失判定表。**归因规则（预注册，四选一 + 边界格）**：
- 分歧集中于「交战」桶且特征表完整 → 结论=①标签选择（M3 wins-only 有效）；
- 分歧集中于「基地高压」桶且该桶缺失特征（威胁时序/基地压力类）→ 结论=②观测表达（M2 追加 obs 修补）；
- 分歧集中于「基地高压」桶 **且特征表完整** → 结论=①/③ 边界（标签或监督均可能：
  守家行为要么是败局污染的标签问题，要么是分布漂移的监督问题——两者治疗不同但可并行：
  **wins-only + 守家帧回补（near-miss 超采样）+ 预留 DAgger 轮**，M3 gate 后按结果收敛）；
- 两桶均高 → 结论=兼有，两项都做；
- **分歧率高 + 桶分布均匀 + 特征表完整 → 结论=③监督方式/分布漂移指纹（teacher-forcing 编译级联）→ M3 增补 DAgger 交互采集轮，而非纯 BC（P0②）**。
- **低分歧率（<15%）**：教师/学生动作高度一致 → 0% 更可能来自分布外状态泛化 → 倾向 ③ 边缘，
  仍需 wins-only + 交互采集。

> ⚠️ 执行态修订（2026-08-26）：本表在 M1 实测前未含「高压桶 + 特征完整」格——实测归因恰落在该表外
> 格子（base 74.6% 分歧率、特征完整）。按纪律应**先补表再判**；本行即事后补录，结论不变
> （DECISIONS §247 措辞已同步为"①/③ 边界"而非"按预注册规则"）。后续探针一律以本修订表为准。

**验收**：按上述预注册规则产出归因结论（①/②/③/兼有），明确到足以决定 M2 是否追加 obs 修补、M3 是否走 DAgger 路径。此门成本极低（纯重放分析，无训练），**先于任何数据工程**。

---

## 5. M2 — 语料纪元整理（一次 MAJOR 打包）

**打包清单**（全部同捆，一次 `OBS_SCHEMA_MAJOR +1`）：

| # | 改动 | 涉及 |
|---|---|---|
| ① | NN 删 item 头（动作空间 10→7，MASK_DIM 10→7） | `student_model.py`（PPOStudent/StudentNet）、`model.py`（NNPolicy）、`infer.ts`、`ppo.py`、`export-rl-rollout.ts`（mask 消费端） |
| ② | SCALAR_LAYOUT 删 idx 7–11（guardStock/frenzyStock/rewindStock/frenzyActive/frenzyShotsLeft），SCALAR_DIM 24→19；**连锁重编号**：`SCALAR_X_INDICES` [20,23]→[15,18]，全部下游（数据增强、npy manifest、检查断言）锁步改 | `schema.py`、`obs-encoder.ts`（锁步，schema MAJOR 强制）、`dataset.py` |
| ③ | wins-only 语料口径：`export-godai-labels.ts` 增加 wins 过滤 + near-miss 濒危帧分层选项（墙损/基地受击帧超采样开关）；**人像导出过滤含道具动作帧**（`a_item≠none` 帧剔除：实测 104 局仅 36 事件 ≈ 0.07% 决策帧，剔除零损耗，且与新政策一致） | 语料导出管线 |
| ④ | （M1 判出则加）obs 表达修补：新增/修正通道或标量（如威胁时序） | `schema.py` + `obs-encoder.ts` |
| ⑤ | SuperItems 代码删除：**归属 M4 验收后的 cleanup 子任务**（DECISIONS §167 修订时标注）；本纪元仅参数归零 | `src/ai/god/SuperItems.ts` |
| ⑥ | **reward/return 轨迹导出（P0③，M3 value 头 MC 预置的必要前置，当前导出器均无此字段）**：TS 侧按 RL reward 定义（v7 loss-band dense + 终局项）逐决策帧结算 reward，γ 折扣反向累积成 return 落盘（γ 数值与 PPO 完全一致）；`dataset.py` 增加消费端；人像导出线同样补 return 字段 | `export-godai-labels.ts`、`export-observations.ts`、`dataset.py`、`ppo.py` |

**非目标**：不给道具生成做世界层修改（人类玩法不动）；RL rollout 世界的道具生成默认保留（见 §10 待决 #3）。

**验收**：
- [ ] schema 锁步测试（沿用 `tests/stages.test.ts` 式的独立实现对照）+ 抽帧 indices 断言
- [ ] **mirror 索引锁步断言**：`SCALAR_X_INDICES` 删除重编号后的反例测试（mirrorX 数据增强前后一致性；旧索引 [20,23] 必须已迁移为 [15,18]）
- [ ] **教师管线锁步断言**：950K RLNet 离线教师消费同一 schema——MAJOR bump 后教师标签管线（含 `rl_model.py` 输入契约）同步重导的 smoke 校验
- [ ] determinism 测试（同 seed 双跑逐字节一致）
- [ ] `bun run check` 全绿
- [ ] 全量 npy 重导完成（成本大头；用 wins-only 缩减语料对冲 labeler ~18× 放大）
- [ ] **状态覆盖率报告**：语料帧按基地压力分桶（基地完好度/受威胁度）统计占比，对照 God-AI 全量轨迹同分桶占比；「高压/濒危」桶占比 < 全量占比 50% 或样本 < 2000 帧 → 触发承压关定向补录
- [ ] **npy 归档（⑪）**：重导前将 tmp/ 下 4841 个旧 schema npy 整体移入 `tmp/npy-v1-archive/`，防新旧混用
- [ ] **schema 消费方清单逐项对勾（④）**：见下方清单，M2 执行时用 grep 重新生成一遍，清单外的新增消费方即锁步漏洞
- [ ] 重导语料上 BC smoke：**量化门槛（⑩）move acc ≥0.35、val_loss ∈ 1.1–1.25（历史区间）**；未达标 → 停，回查编码/标签

**schema 消费方清单（2026-08-25 grep 生成，M2 执行时重新生成逐项对勾）**：
- TS 运行时：`src/nn/infer.ts`、`src/nn/obs-encoder.ts`、`src/nn/policy-input.ts`
- 导出/评估（bun）：`export-dagger-labels.ts`、`export-godai-labels.ts`、`collect-godai.ts`、`export-rl-rollout.ts`、`export-observations.ts`、`export-eval-game.ts`、`nn-trace.ts`、`student-accuracy.ts`、`m1-eval.ts`
- 训练（python）：`schema.py`、`student_model.py`、`model.py`、`rl_model.py`、`dataset.py`、`ppo.py`、`eval_bridge.py`、`weights_io.py`、`train_bc.py`、`smoke_test.py`、`test_run_rl.py`、`validate_export.py`、`npyio.py`

---

## 6. M3 — BC warm-start（双 oracle）

**数据**：
- 底料：M2 重导的 God-AI wins-only 语料（全部 35 关胜局；near-miss 濒危帧按 ③ 的口径分层超采样）；
- 宝石：人类录像 → `tools/replay/export-observations.ts` 导出 (obs, action)；按守家场景（回防/拦截）加权采样。
- **M3.0 前置（已核实，2026-08-25 体检）**：`nn-demo/` 共 104 局、全 hard、覆盖 stage 0–30；`verify-demos.ts` 全量重放结果 **97 OK（100% 通关局，基地全活，场均 20 杀）/ 7 DESYNC**（末帧终局翻转×6 + 早期漂移×1，导出器 verify gate 自动剔除）。格式与当前管线完全兼容，无需新建导出层。**缺口**：stage 31–34 无覆盖 → 人定向补打 2–3 局/关（M3 执行前完成）。

**训练（双臂对照，回答"人像到底是增益还是噪声"）**：
- **A 臂（对照）**：纯 God-AI wins 底料，BC teacher-forcing。
- **B 臂（实验）**：底料 + 人像 near-miss 加权采样。**守家帧判定（预注册，⑦）**：`ringCompleteness < 8/8` 或（敌距基地 ≤N 格 且 玩家距基地 ≤M 格），N/M 复用 God-AI 防守触发现有常量、M3 实施前固定数值——不允许临场定义。
- 两臂同底座（68.7K StudentNet 主干）、同超参、同随机种子；**value 头均用各自先验的 MC 回报预置**（数据 = M2 ⑥ 导出的 return 轨迹；不随机初始化——顺带治 GAE 优势爆方差）。
- 归因规则（预注册）：B > A 且均过 gate → 人像是增益；B < A → 人像在当前权重/采样下是噪声，退回 A 并检查冲突模态；两臂均不过 → 回 M1。
- **M1 ③路径（P0②）**：若 M1 判"监督方式/分布漂移"，M3 先增补 **DAgger 交互采集轮**（复用 `export-dagger-labels.ts`），再回来过 gate。

**Gate**（干净评估，greedy + 固定种子，与 RL 训练评测同口径）：
- `bun tools/sim/m1-eval.ts --policy nn --weights-dir <dir>` hard 全 35 关**胜率（WIN）**，**A/B 两臂同 gate 复评**：
  - **≥50% → 进 M4**（取较优臂作 M4 起点）
  - 5–50% → 半通过：补人像轮次 +（必要时）特定弱关定向补打，复评
  - ≈0% → 不走 M4，回 M1 重查表达
- 参照线：`--policy god` 同种子对比；历史基线 DAgger 后 = 0%、suite 0.1027（nn.progress.md §L950）。

---

## 7. M4 — kickstart 式短 RL

> ⚠️ 执行态修订（2026-08-26，M3 实测）：**value 头 MC 预置已降级**。returns 终局锚定
> （Σ≡REWARD_SCALE×gatedScore ≈ O(9)）使 value MSE@coef 1.0/0.05 均爆方差、压制策略头
> （move acc 卡多数线）→ A/B 双臂实际以纯 BC（`--value-coef 0`）通过；value 预置改为
> **M4 前置条件：以「逐关规范化 returns」重新启用**（见下方 §7 前置条件行）。M3 gate 只测
> 策略，不受该降级影响；M4 若未启用 value 预置则保持随机初始化 value 头（与旧谱系一致）。

- **M4 前置条件（新增）**：① M3 gate 须 ≥50%（当前实测 0% → 先完成回环整改再进 M4）；
  ② value 预置若启用，须先完成「逐关规范化 returns」（returns 除以逐关 gatedScore 基准，
  使 MSE 目标 O(1)，避免重蹈 M3 爆方差）——未完成则以随机 value 头启动。
- 起点：M3 权重（policy 头 = BC 先验，value 头 = MC 预置或随机）。
- 目标函数：现行 clipped PPO + teacher（God-AI）交叉熵辅助项（kickstarting）；辅助项权重随 iter 衰减到 0，防上限牵引（R5）。
- 课程：沿用现有课程（active 关数递增至 20/35，hard）。
- 测量：胜率对**三个参照系**报告——M3 先验（**主对照**）、God-AI guard 前地板（hard 76%）、it36≈10%（**旧 schema 跨纪元背景，不可复跑，仅定性参照，不进验收表（⑥）**）；每 iter 沿用现有干净评估分发；同步跟踪 losses 带 baseIntegrity。
- **守家回补追踪线（Q5，非 gate 必报）**：中期参照 = **hard 胜率 ≥76%**（追平摘除 guard 前的地板）；每 iter 报告「距 76% 差值」与「baseIntegrity 趋势」（⑫：**为报告项，不设二元截止**）；最终目标按 §1 的分阶段目标表执行。
- **工程吸收（⑧，成本近零）**：PPO 波内 `target_kl` 早停（控 KL/step 上行，B3 已立项）+ `best-clean-eval` checkpoint 归档（当前权重只进不退无法回滚峰值，nn.progress.md §11.3-④ 已诊断）。
- **课程消融开关（⑨）**：保留 `--course-ablate` 直接用全 35 关作对照（BC 起点已有全关先验）；默认沿用递增课程并标注为已知风险。
- 验收：胜率稳定 >50% 且 > M3 先验；baseIntegrity 出现首次统计学上行；追踪线报告在案（自 −1pt 起计价，距 76% 差值趋势朝上即视为回补启动）。**口径统一（⑩）**：所有关卡指标一律写「胜率（WIN）」，不再混用"保留率"。
- **收尾 cleanup（Q6 归属）**：M4 验收通过后，执行「删除 `src/ai/god/SuperItems.ts` 及其测试改写」cleanup 子任务（DECISIONS §167 在同次记录中修订为"机制退役"）。
- 兜底支线：若 M4 不及预期，启用**高层语义监督**（克隆战术 id 而非原始动作 + 廉价底层控制器），作为路线 F 正式立项（单独立项，不进本方案 DoD）。

## 7.1 机时预算与中止条件（P2 补充，粗估）

> 基准实测：全扫 8400 局 ≈ 4 min（AGENTS 标定）；人像导出 7 局含验证门 0.83s（本次实测）→ 104 局 ≈ 15s。

| 环节 | 粗估机时（12 线程 CPU） | 中止条件 |
|---|---|---|
| M1 探针 | 分钟级（纯重放分析） | — |
| M2 语料重导 | 人像 104 局 < 1 min；God-AI wins 底料（60 seeds × 35 关 ≈ 2100 局，含 think 标定）≈ **15–30 min**（池化） | 单文件重试 >2 次仍 DESYNC 风暴 → 停，查明漂移 |
| BC 训练（双臂 ×2） | 样本 100–300K 帧 × 20–50 epochs ≈ **1–3 h/臂** | loss 10 epochs 不降或 move acc 停在随机线 → 停，回 M1 |
| 短 RL | 沿用现有课程；**实测每轮 ≈ 31 min**（nn.progress.md：全轮 30.9m，真实采集仅 ~9m，PPO+评估 ~20m）；预算 **10–20 iters ≈ 5–10 h**，**先跑 3 iter 取实测值再锁预算（⑫）** | 连续 5 iter 干净评估胜率无提升且 < 先验 → 停，复盘 reward/课程 |
| 各门评估 | 每次 `m1-eval`/eval-suite ≈ 分钟级 | — |

R3 排程约束更新：**重导窗口（≤30 min）与 RL 训练错峰**；BC 双臂可串行跑（A 先 B 后），不与采样器争核。

---

## 8. 风险台账

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R1 | 表达墙未解（M1 前最大不确定） | 中/高 | M1 前置，先探针后数据工程 |
| R2 | 人像覆盖稀薄或 ≠ RL 最优 | 中/中 | 只作加权宝石不与 wins 底料等权；必要时定向补打弱关 |
| R3 | 全量重导 CPU 窗口与 RL 训练争机 | 高/低 | 排程：重导与 RL 训练错峰（同机）；wins-only 缩量对冲 |
| R4 | God-AI 地板 −1pt（对远期 >90% hard 约束的侵蚀） | 确定/低 | 数据已量化在案；缺口转列为 RL 守家目标，不再返工 |
| R5 | kickstarting 上限牵引 | 低/中 | 助项权重衰减 + 最终以干净评估为准 |

---

## 9. 执行纪律（AGENTS 条款绑定）

- 每个 bug 先写失败测试再修（§7）；确定性：无新 `Math.random()` 进 Simulation（§2.3）。
- 结论级判据 ≥60 seeds（§6.3b）；语料/参数不变时只用失败子集复跑（§120）。
- 每里程碑完成即记录：DECISIONS.md（foundational 级）+ `docs/god-ai-tuning.progress.md` / `docs/nn.progress.md`（tuning 全文）。
- 每个门过了才动下一个；门不过回环整改。

---

## 10. 待决问题（需你拍板）

1. **人像录像档位** ✅ 已核实（2026-08-25）：104 局 hard / 31 关（缺 31–34）/ 同格式可直接导出 / 93.3% 产出率。结论：直接可用，无需新建导出层；只需执行前补打 stage 31–34。
2. **wins 口径细节** ✅ 已拍板：仅 `stage_clear`；濒危帧分层超采样倍率留到 M2 实施时以实验定（默认先 2×）。
3. **RL rollout 世界的道具生成** ✅ 已拍板：保留（世界与人类一致；评估确定性由固定 seed 保证）。
4. **成功判定的量级** ✅ 已拍板：>50% 是 gate；执行 M3 前另立一张分阶段目标表（50→70→85→90+）与">90% hard"终局约束衔接。
5. **M0/M2 合并与否** ✅ 已拍板：不合并，M0 先行（参数级、可回退、立即生效），M2 纪元随后独立推进。