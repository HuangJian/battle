# NN 训练 — 总索引与现行未决

> **2026-09-23 重组**：原单文件 `docs/nn.progress.md`（7 893 行 / 155 节）已按主题拆分为
> 8 篇技术档案（下表）。本文件此后只承担三件事：**记账规则** · **主题文档索引** ·
> **现行未决事项**。历史全文按主题搬走，**编号已全库重排**——旧 `§N` → 新「文档 §M」的
> 全量对照见文末 **附录 A**（155 条）。
>
> 拆分时**未改写正文**，只更新了节内交叉引用；2 节被判「过时/无效」直接删除（见 §2 末）。

---

## 1. 记账规则（替代原「所有条目都写这里」）

AGENTS §5.6 的原口径是「每一条 NN 训练架构变更 / 评估 / 教训都记进 `docs/nn.progress.md` 顶部」。
拆分后口径不变、**落点改为主题文档**：

1. **写到对应主题文档的顶部**，标题格式 `## §<该文件当前最大号 + 1> <标题>（YYYY-MM-DD）`。
   号在**文件内**递增，新条目置顶 ⇒ 号大在上、`§1` 最旧。
2. **一篇条目跨两个主题**：全文写进主责文档，另一篇顶部加一行指针。
3. **只增不改**：旧条目正文不改写；发现旧结论错了，另起一条并链接它（原「账本只增不改」纪律）。
4. **跨主题或未归类** ⇒ 写到本文件顶部（同样 `§N` 规则）。
5. 架构变更按 AGENTS §5 硬规则入账时，仍必须在**对应主题文档**留下条目；`DECISIONS.md` 只存决策，
   全文在主题文档。

## 2. 主题文档索引

| 文档 | 覆盖 | 节数 |
|---|---|---|
| [`docs/nn/legacy.md`](nn/legacy.md) | **早期谱系归档**（2026-08-18 ~ 08-29）：v1/v2 student、P1.5 蒸馏、BC 热启动、无道具纪元、第一代 RL 流水线 —— 已被 goal-space 取代，只作史实与教训 | 22 |
| [`docs/nn/remote-transport.md`](nn/remote-transport.md) | hub / worker / 云机 / 隧道 / 离线任务包 / 产物回传 / 优先级调度 / wire 账 | 32 |
| [`docs/nn/training-stack.md`](nn/training-stack.md) | 训练循环 · 调度器 · supervisor · 课程编排 · 采样配额 · 门禁与停车 · kickstart | 24 |
| [`docs/nn/experiments.md`](nn/experiments.md) | 课程腿判决 / 探针 / 负结果归档（含人类探针与 BC-ref 判死） | 32 |
| [`docs/nn/engineering.md`](nn/engineering.md) | 测试纪律 · 子进程编码契约 · 门禁耗时 · 账本与 metrics schema · 语料指纹 · **共享原语层与分层契约** · **神模块拆分（S4）** | 23 |
| [`docs/nn/console.md`](nn/console.md) | dashboard 侧：组件面 / 调度器视图 / 任务包与产物两条腿 / 回显 | 10 |
| [`docs/nn/runtime-opt.md`](nn/runtime-opt.md) | rollout / eval 运行时：native 内核 · 并发口径 · 派发 · 单局看门狗 · 长驻池 | 22 |
| [`docs/nn/tpu-perf.md`](nn/tpu-perf.md) | TPU / XLA：设备实测 · 单步耗诊断 · 编译缓存 · PPO 吞吐 | 8 |

> **另：每篇多了一个 `决策正文归档` 节（2026-09-23）**。`DECISIONS.md` 同日瘦身，把那批
> 正文 >10 行的决策全文按主题搬进这些文档（共 105 条进本目录，2 条进 `docs/decisions/details/`）。
> 该节锚点是 `### §<DECISIONS 编号>`，与上面的 `## §N` 进度节**不同一套号**；上表「节数」
> 只数 NN 进度节，不含这个归档节。`DECISIONS.md` 的索引行末尾写明了每条正文落在哪一节的哪一锚点。

**相邻文档（本轮未动）**：`docs/nn-diagnosis-methodology.md`（诊断流程纪律）、
`docs/nn-diagnosis-toolkit.md`（诊断脚本源码快照）、`docs/nn.progress.intent.md`（意图头）、
`docs/rl.progress.md`（RL 阶段）、`docs/goal-nn.progress.md`（goal-space 重建）、
`docs/god-ai-tuning.progress.md`（God-AI 调参）。

> ⚠ `docs/nn/experiments.md` §25 记的入库产物 `docs/x20-dissection-414000.md` **在盘上不存在**（git 从未跟踪过它，
> 工作区也无此文件）。该节的判决段原始数据现仅存于 §22/§25 正文与 `nn-training/` 的轨迹目录；
> 引用它时以 §22/§25 正文为准，或找用户确认原件位置。

**已删除 2 节**（机制已从代码中消失，历史价值由 `DECISIONS.md` 对应条目承担）：
§67 竞速广播（2026-09-17；机制被 `docs/nn/remote-transport.md` §30 的 P3「竞速退役」删除）、
§49 R9 远端降级默认 ABORT（2026-09-15；`--remote-degrade-after` 被 `docs/nn/training-stack.md` §23 双删）。

§19 p4-onset 首日监控**保留**（其 §19.1 仍被 `DECISIONS.md` §2026-09-16-…-kl-cap 引为证据）。

**旧号引用清理范围**：本次全库把 `docs/nn.progress.md §N` 重写为新文档号（50 个文件；
代码/测试/课程/计划/DECISIONS 一并改）。已知两处例外，读到时按下文消解：

1. `tools/agent/sampler-agent.ts` 的 `serveResult` 注释仍写 `docs/nn.progress.md §126` ——
   该文件在 node codeHash 集内（`tools/agent/codehash-files.txt`），改一行注释会顶掉节点引擎
   哈希身份，故未动；§126 经附录 A = `docs/nn/runtime-opt.md` §5。
2. 形如 `§N.M` 且指向 **plan / DECISIONS / AGENTS** 的子引用（如 `accident.plan §4.1`、
   `AGENTS §16.6`）不是本档节号，一律未改。
3. ~~`src/nn/conv/conv-optimize.plan.md` 里引的 §140–§142 有意未改~~ **已解决（2026-09-23）**：
   该文档已 **`mv` 到 `plan/conv-optimize.plan.md`**（`plan/**` 在 codehash 排除清单里），
   其内部引用同步重写为 `docs/nn/runtime-opt.md` §9–§11 ⇒ 现在这条例外**不再存在**。
   背景与理由见 `docs/nn/runtime-opt.md` **§17**。

## 3. 现行未决事项（open threads）

> 按「有明确关闭判据」收录。课程级待办可能与后续条目冲突 —— **以对应文档最新一节为准**。

### 3.1 传输与观测（近期主线）

| # | 事项 | 出处 | 关闭判据 |
|---|---|---|---|
| 1 | **P0.5 实机数字未取**：阶段账 `in/out/ppo/wall` 占比、`p90` 取消延迟 | `docs/nn/remote-transport.md` §28 §29 | 一次云-hub-LAN 会话跑 `nn-training/tools/wire_report.py <worker 日志>`，把阶段表贴进 plan/文档。**在此之前 P2 预取的收益结论不成立**（机制可关：`--prefetch-depth 0`） |
| 2 | **回传腿减重（minimize-payload）**：让 `out` 那 25s 本身变小 | `docs/nn/remote-transport.md` §30 | 与 P2.5 正交；两者都做才是全量收益 |
| 3 | 真云机 / 真远程轮次的**绝对值**（`wire.up_sec`、每轮墙钟、TPU 腿 target ~10s 量级） | `docs/nn/remote-transport.md` §7 | 真机轮次读数归档 |

### 3.2 TPU / 性能

| # | 事项 | 出处 | 关闭判据 |
|---|---|---|---|
| 4 | ~~ragged tail 占修后单轮 ~64%~~ **已收尾**：`target_transitions` 取 mb 的倍数（`49152 = 48×1024`，ragged=0）；Plan A「末 chunk 补位 + 权重掩码」被否决（改归约树 ⇒ ulp 差异） | `docs/nn/tpu-perf.md` §8 | 真机验证：重打包后 diag 汇总**只出现 `B=1024`**（再现 `B=896` = 未生效） |
| 5 | **x20-noexplore 放离线 TPU 跑一轮**（分离「设备/配置基线」与「demo 路径开销」） | `docs/nn/tpu-perf.md` §8 | 一轮 diag 对照：≈22s ⇒ 残余 11s 归 demo，可立项；≈33s ⇒ 本腿已到位 |
| 6 | `XLA_NO_SPECIAL_SCALARS=1` **未采用**（会改编译器常量折叠 ⇒ 可能动 fp 结合顺序） | `docs/nn/tpu-perf.md` §7 | 需独立证据，且不与「不动数值」口径冲突 |

### 3.3 rollout / eval 运行时

| # | 事项 | 出处 | 关闭判据 |
|---|---|---|---|
| 7 | 真云机 `ts_code.zip` 通道未确认（现只有 real-bun 哨兵 + 打包门禁的间接证据） | `docs/nn/runtime-opt.md` §5 | 真云机跑一轮 |
| 8 | 真节点上的 T2（预编译 native 库上传 / 节点本机编译）未验 | `docs/nn/runtime-opt.md` §4 | 代码路径已在，缺真机验证 |
| 10 | **arm64 节点的内核吞吐已到顶**：四项循环重排在这类机器上只值 ≈+5%（FP-op 受限，贴着 61–69% 上限），而 FMA 上限值 **+32%** —— 要不要为此开 **new era**（改数值 ⇒ 权重/语料/基线全重做） | `docs/nn/runtime-opt.md` §14 §15 | 二选一并写明：① 走「减少 MAC 数」（缩网络/换结构）⇒ 另立条目；② 接受 FMA 新纪 ⇒ 按 `AGENTS §6.3b` 的三件套（DECISIONS + 60-seed 三难度基线 + golden 重冻）执行。**在此之前 arm64 侧不要再提循环重排的优化**（已无空间） |
| 11 | ~~云机离线 eval 腿仍未入池~~ **已收尾**：池已接入 `run_cloud_eval` + `run_local_eval_game`（每轮一个池、轮末关；上限 `min(slots, 局数)`），实测 **1.19–1.39×**（24/48 局），逐局 `_eval_report.json` **逐字段相同**（`elapsedSec` 除外） | `docs/nn/runtime-opt.md` §22.5 | ✅ 已验：`tests/test_offline_eval_pool.py`（接线 + 执行面，8 用例）+ `tools/perf/bench-eval-pool.py` 真机 A/B |

### 3.4 控制台

| # | 事项 | 出处 | 关闭判据 |
|---|---|---|---|
| 9 | 真机「一个 serve 进程同时带 BC + RL」的实跑 | `docs/nn/console.md` §6 | 一次真跑（与 R2e 同口径） |

### 3.5 课程侧待办（可能已被后续条目取代）

- x20-rebirth M2 三选一（kills 最优 it30 / pass 最优 it30 / 末 it96）+ 池外段 —— `docs/nn/experiments.md` §13
- metrics v6 后继 / wChip 生存腿备选 —— `docs/nn/experiments.md` §4
- 「多敌关是否需要真正的朝向信号」尚未验证 —— `docs/nn/experiments.md` §11
- EVAL_SEEDS 扩池 / 正式门口径 / dashboard 必改 —— `docs/nn/training-stack.md` §8
- 控制台开课回执（起点-基线对照行的 dashboard 侧）—— `docs/nn/training-stack.md` §24

---

## 附录 A：旧编号 → 新位置（全量对照）

> 旧号取自拆分前 `docs/nn.progress.md` 的 `## §N`（拆分时 155 行 = 155 个旧节）。
> **拆分后新增**（上游 conv-optimize 批次，本索引收录并已归位）：§140–§143 与**重号 §131 的第二条**
> （TPU ragged tail 收尾）—— 共 5 条，见下表末尾五行；另有 **§144/§145**（arm64 计时与逐阶段归因）
> 在拆分时尚未归档，后按主题文档局部编号补入 ⇒ 共 7 条，见下表末尾。
> **重号说明**：拆分前有 18 个旧号各对应两个节（两条并行写入线合并所致）——
> **§56、§108–§120、§127–§130、§131**；下表这些号各占两行，以标题区分。
> 每篇主题文档末尾也各自带一份本文件范围的对照（`<!-- OLD-NUMBER-MAP -->` 之后）。

| 旧 § | 新位置 | 标题 |
|---|---|---|
| §-1 | docs/nn/legacy.md §1 | Pre-history (before 2026-08-18) |
| §0 | docs/nn/legacy.md §2 | v1: Conv-Only Baseline (2026-08-18 → 2026-08-19) |
| §1 | docs/nn/legacy.md §3 | v2: Scalar Fusion Architecture (2026-08-19) |
| §2 | docs/nn/legacy.md §4 | P1.5: God-AI 教师端到端蒸馏管线（学生架构）验证 (2026-08-20) |
| §2.1 | docs/nn/legacy.md §5 | 移动死锁修复 + 0% 根因收敛（续 §2，2026-08-20） |
| §2.2 | docs/nn/legacy.md §6 | 启动器 Windows 本机验证：ps1 双 bug 修复（BOM + 参数风格兼容，2026-08-21） |
| §3 | docs/nn/legacy.md §7 | RL 阶段设计（承接 P1.5 蒸馏，2026-08-21） |
| §4 | docs/nn/legacy.md §8 | RL 训练断点续跑机制（2026-08-23） |
| §5 | docs/nn/legacy.md §9 | 队列模式静默跳轮事故复盘 + 修复 + 重启（2026-08-24 凌晨） |
| §6 | docs/nn/legacy.md §10 | 训练可观测性落地 + 权重逐轮归档 + mb1024/workers10/self-agent2 重启（2026-08-24 早） |
| §7 | docs/nn/legacy.md §11 | 干净评估嵌入流水线（2026-08-24 晚，DECISIONS §283） |
| §8 | docs/nn/legacy.md §12 | 流水线衔接优化：KL 遥测补盲 + 熔断止损 + dist/eval 解串（2026-08-25） |
| §9 | docs/nn/legacy.md §13 | Python 侧工程化重组 + 常驻单元测试（2026-08-25） |
| §10 | docs/nn/legacy.md §14 | streamKlCap 预算重标定 0.12→0.20（2026-08-25 午） |
| §11 | docs/nn/legacy.md §15 | 收敛性审计：it1-68 平台期定量画像（2026-08-25 午后） |
| §12 | docs/nn/legacy.md §16 | R6 破局三件套落地 + 评审修订与重启记录（2026-08-25 下午） |
| §12.5 | docs/nn/legacy.md §17 | 补丁：eval 本地参与（PPO 收尾后本机算力入列）（2026-08-25 傍晚） |
| §13 | docs/nn/legacy.md §18 | 无道具纪元开启：M0 + M1（2026-08-26 凌晨，plan/AI-No-Items-Warmstart.md） |
| §14 | docs/nn/legacy.md §19 | 语料纪元 OBS_SCHEMA_MAJOR 1→2（2026-08-26 凌晨，plan/AI-No-Items-Warmstart.md M2） |
| §15 | docs/nn/legacy.md §20 | M3 BC warm-start 双臂（2026-08-26，plan/AI-No-Items-Warmstart.md §6） |
| §16 | docs/nn/legacy.md §21 | tail fan-out 反向竞速修复 + 三节点就绪验证（2026-08-27） |
| §17 | docs/nn/legacy.md §22 | goal-space 策略网络重建开工（2026-08-29，M9 时代） |
| §18 | docs/nn/remote-transport.md §1 | 远程链路冒烟预演：worker --echo + TrainingLoop 作废轮（2026-09-05，DECISIONS §340） |
| §19 | docs/nn/remote-transport.md §2 | p4-onset 首日监控：lr 调度在 remote 模式未生效 + 重复 shard + 评估截断（2026-09-06，监控发现，待处置） |
| §20 | docs/nn/remote-transport.md §3 | 四项监控修复落地 + PPO job 竞速模型（§343）+ it24 孤儿租约事故复盘（2026-09-06） |
| §21 | docs/nn/tpu-perf.md §1 | PPO 吞吐：三设备实测 + ref 缓存 / 传输压缩 / 多卡落地 + TPU 设备层（2026-09-10） |
| §22 | docs/nn/engineering.md §1 | 外围组件巡检：goal 热图静默常量（生产档目标策略失效）+ eval 墙损失测 + 我引入的 payload 回归（2026-09-10） |
| §23 | docs/nn/training-stack.md §1 | 训练停车机制审计 + G13 duty 门修复（2026-09-11，c6b-margin 事故：每小时自停 2 次） |
| §24 | docs/nn/remote-transport.md §4 | 云端停机机制（2026-09-11，用户指令：停机 = 停云端省 GPU 配额，本地进程都不停） |
| §25 | docs/nn/tpu-perf.md §2 | TPU PPO「单步递增爆炸」根因定案 + 修复（2026-09-11，c6b-margin 首个 TPU job 45~52s/step / eta 5.9h） |
| §26 | docs/nn/training-stack.md §2 | 门判决永不停车 → 只联动云机停机/恢复（2026-09-11，用户定案，DECISIONS §2026-09-11-gates-never-park-loop） |
| §27 | docs/nn/engineering.md §2 | metrics v5（`clearTick`）+ outcome 虚拟符号：修复加列只改了一半（2026-09-12） |
| §28 | docs/nn/console.md §1 | it0 bc 权重基线评估：配对基准不再随 run 起点漂移（2026-09-12，用户指令） |
| §29 | docs/nn/console.md §2 | it0 基线评审修订：重试语义 + 账本双向隔离 + Hero NaN 行（2026-09-13 评审） |
| §30 | docs/nn/engineering.md §3 | CLI 子进程编码契约：环境无关的三层修（2026-09-13 复核 `python-cli.issue.md`） |
| §31 | docs/nn/engineering.md §4 | I9 长尾竞速测试 flake：两条失败路径 + 双通道断言修（2026-09-13 复核 `python-flaky.issue.md`） |
| §32 | docs/nn/experiments.md §1 | c4-chip01 关账：wChip 0.01 主判据「小但真」成立（2026-09-13） |
| §33 | docs/nn/engineering.md §5 | python 门禁 flake 全面审计：静态扫雷 + 负载轰炸（2026-09-13，§31 后续） |
| §34 | docs/nn/experiments.md §2 | c4-chip03 关账 + c6-chip 主腿备好（wChip 剂量-响应定案）（2026-09-13） |
| §35 | docs/nn/experiments.md §3 | wDmg 死项修复：c6-dmgfix 基座腿就绪（2026-09-13，用户拍板「先做」） |
| §36 | docs/nn/training-stack.md §3 | 多课程并行训练（multi-course parallel training，plan/multi-course-parallel-training.md） |
| §37 | docs/nn/training-stack.md §4 | 关卡配置抽离 + D14 语料身份改语义哈希（2026-09-13，用户拍板） |
| §38 | docs/nn/training-stack.md §5 | 课程热加载：非语料改动下一 iter 应用；语料改动拒绝+横幅+不泄漏云端（2026-09-13，用户拍板） |
| §39 | docs/nn/remote-transport.md §5 | BC 训练整合进 云-HUB-LAN：kind=bc 第二任务类型全链（2026-09-13，用户指令五步） |
| §40 | docs/nn/engineering.md §6 | 自主审查轮：battle-rl.ipynb 单 cell 化 + 真跑暴露四缺陷修复 + bc-c4 it1 真实产物（2026-09-13） |
| §41 | docs/nn/tpu-perf.md §3 | 云端多卡确认：PPO/BC 双卡默认真用上（2026-09-13，用户指令「双/多卡时训练跑在双/多卡上」） |
| §42 | docs/nn/engineering.md §7 | ipynb 清场 + tpu-probe 单源化（2026-09-13，用户指令「重构 ipynb：删无用 notebook，重新整理 tpu-probe 使其更模块化并保持独立性」） |
| §43 | docs/nn/training-stack.md §6 | 本机 PPO 拆分为独立 worker（2026-09-15，用户指令「把本地 PPO 拆分为一个独立 worker，可以随时启停，与云端 worker 一致，同样支持 pull/push 模式」） |
| §44 | docs/nn/training-stack.md §7 | 按样本量动态采集（Dynamic Rollout Volume）：P0+P1 实现与 P2 端到端验证 |
| §45 | docs/nn/training-stack.md §8 | 双轨日常评估（Dual-Track Eval Seeds）：P0+P1+P2 单测/本地 e2e |
| §46 | docs/nn/experiments.md §4 | x3-power 结课：判负（2026-09-15；课程 `nn-training/curricula/x3-power.jsonc`「终点结算」节） |
| §47 | docs/nn/experiments.md §5 | Phase 0 逐敌种画像（T3）：**败局 = 从不碰 power**，判决 T5（2026-09-15；`plan/x3-power-followup.plan.md` §T3） |
| §48 | docs/nn/training-stack.md §9 | 采集配额量纲 10× 修（T9）：`est_ticks_per_game` → `est_samples_per_game`（2026-09-15；`plan/x3-power-followup.plan.md` §T9） |
| §49 | **已删除** | R9 远端降级：默认 ABORT + 启动界面 opt-in（T7，2026-09-15；DECISIONS §2026-09-15-goalnn-r9-default-abort · 全文 → docs/nn/experiments.md §33） |
| §50 | docs/nn/experiments.md §6 | x3-step it30 结课：可测性未恢复（工厂第二段复证）；主路径仍 T5（2026-09-16） |
| §51 | docs/nn/experiments.md §7 | x3-chip-k05 结课：预注册池判阴，单变量池＋3pp 待 k0 定案（2026-09-16） |
| §52 | docs/nn/experiments.md §8 | T6 剂量定案：全噪音；run 噪声地板≈2pp 实测；T5 开工条件（2026-09-16） |
| §53 | docs/nn/engineering.md §8 | metrics v6 落地：分敌种命中/击杀列（idx31–38，TS+Python 全链，2026-09-16） |
| §54 | docs/nn/experiments.md §9 | T5 x3-credit-p6 结课：双 run 一致判负；奖励重定价路线关闭（2026-09-16） |
| §55 | docs/nn/experiments.md §10 | x1-rebirth 开课（纯从零臂）＋ 严格样本量配额机制落地（2026-09-17） |
| §56 | docs/nn/remote-transport.md §6 | hub-server 重启死锁收口：D9 只当「无效鉴权」的守门人 + 回环永不封禁 + 端口级实例锁（2026-09-17） |
| §56 | docs/nn/experiments.md §11 | x1-rebirth-a2 结课：从零臂达 **100% / 0 伤**；「位移不足」归因被验证（2026-09-17） |
| §57 | docs/nn/engineering.md §9 | python 全量门禁提速：worker 数 × CPU 内线程数必须成对调（2026-09-17） |
| §58 | docs/nn/remote-transport.md §7 | 远程传输三改 M0–M2 落地：统一计量 + 隧道协议开关 + 协议瘦身（2026-09-17） |
| §59 | docs/nn/remote-transport.md §8 | M3 rollout 上云落地：新 job kind「一整轮」（kind=iter）＋ TS 运行时打包（2026-09-17） |
| §60 | docs/nn/remote-transport.md §9 | 节点确定性失败带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410（2026-09-17） |
| §61 | docs/nn/remote-transport.md §10 | 半离线整段落地：kind="run"（一次领走整段，节点自主跑完 + 产物可打包下载）（2026-09-17） |
| §62 | docs/nn/remote-transport.md §11 | 全离线任务包落地：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完（2026-09-17） |
| §63 | docs/nn/remote-transport.md §12 | 产物补传：中途能连上 hub 就自动恢复在线回传（2026-09-17） |
| §64 | docs/nn/console.md §3 | 控制台两条腿：导出任务包 / 导入产物即评估（2026-09-17） |
| §65 | docs/nn/engineering.md §10 | 节点门统一：rollout 与 eval 同用 codehash-files.txt（eval 门改比 codeHash，ping 不再报 engineEpoch）（2026-09-17） |
| §66 | docs/nn/runtime-opt.md §1 | in-loop eval 墙钟：软等可配（180s→30s）+ 本机份额提前放行（2026-09-17） |
| §67 | **已删除** | 单课程多卡 → 竞速广播：最新 job 派给每个 worker，先回传者胜（2026-09-17） |
| §68 | docs/nn/experiments.md §12 | x2-rebirth 结课：承伤 106.4→33.9（−68%），终点取 it15 而非末 it；「有效训练量只有前几轮」（2026-09-18） |
| §69 | docs/nn/remote-transport.md §14 | 本机 hub 地址走凭据（`HUB_IP`）：两个 notebook 同键同口径 + 一条对账守卫（2026-09-17） |
| §70 | docs/nn/remote-transport.md §13 | 「云端同机 rollout + PPO」全链路集成测试：一条真链路，两个面板读法（2026-09-17） |
| §71 | docs/nn/remote-transport.md §15 | 多课程单 hub（P1）：一个进程托管 N 份账本 + 跨课程轮转 + 离线课 + 分课程权重桶（2026-09-18） |
| §72 | docs/nn/remote-transport.md §16 | hub 中介 push 派发（P1 余下）：队列顺序推空闲 worker + 周期探活 + 超时回落换人（2026-09-18） |
| §73 | docs/nn/console.md §4 | 控制台 worker 登记入口 + 面板重组（P1 余下）：课程 select 解放 / 在训课程全高亮 / 并行总览（2026-09-18） |
| §74 | docs/nn/remote-transport.md §17 | 单 hub + 单隧道（P1 余下 ①）：hub/cloudflared 收敛为单实例 + 课程表从盘上发现（2026-09-18） |
| §75 | docs/nn/remote-transport.md §18 | 多课程单 hub 端到端 + 回环 HTTP 绕开代理（P1 余下 ②）（2026-09-18） |
| §76 | docs/nn/training-stack.md §10 | R2 设计稿 + R2a 落地：门禁与续跑改扫账本（重启不再洗白）（2026-09-18） |
| §77 | docs/nn/training-stack.md §11 | R2b 落地：任务模型 + 在飞集；`loop-state.json` 经落地否决（2026-09-18） |
| §78 | docs/nn/training-stack.md §12 | R2c-1：单进程多课程调度核心 + 只读计划视图（2026-09-18） |
| §79 | docs/nn/training-stack.md §13 | R2c-2：抽出 run_one_round + 任务体↔引擎的桥 + 假件集成测试（2026-09-18） |
| §80 | docs/nn/training-stack.md §14 | R2c-3：轮内切成 13 步 + 让位闸门（2026-09-18） |
| §81 | docs/nn/training-stack.md §15 | R2c-3 余下：远端 PPO 三相拆分（发布 / 等结果 / 落位）（2026-09-18） |
| §82 | docs/nn/console.md §5 | R2c-3 余下：调度器进控制台（单例卡片 + 每课队列视图 + 在等什么）（2026-09-19） |
| §83 | docs/nn/training-stack.md §16 | R2c-3 收口：真机 RSS 实测（checkpoint 缓存上限的真实约束是「数量」）（2026-09-19） |
| §84 | docs/nn/training-stack.md §17 | R2d 写的一半：单进程 supervisor 真的能跑（serve + 引擎池 + 日志行路由）（2026-09-19） |
| §85 | docs/nn/training-stack.md §18 | R2d 操作面：进程不绑课程 + 暂停/恢复控制通道（含生效回执）（2026-09-19） |
| §86 | docs/nn/training-stack.md §19 | R3-4：serve 也能带 BC 课（BC 编排体引擎化，单进程 supervisor 收编 BC）（2026-09-19） |
| §87 | docs/nn/console.md §6 | R3-4 控制台半：BC 课与 RL 课在同一张调度器卡片里并列（2026-09-19） |
| §88 | docs/nn/console.md §7 | R3-3：组件卡分族（服务面单例角色 vs 课程面按课程）（2026-09-19） |
| §89 | docs/nn/training-stack.md §20 | R3-5：trainer 收敛为「一个进程服务所有课程」（2026-09-19） |
| §90 | docs/nn/training-stack.md §21 | 本机 PPO worker 也不绑课程：一个进程领所有课程的活（2026-09-19） |
| §91 | docs/nn/console.md §8 | 本机伪 GPU 节点退出控制台：只剩冒烟预演自起自停（2026-09-19） |
| §92 | docs/nn/remote-transport.md §19 | 启动训练不选 pull/push 模式：传输成为部署事实，课程与 worker 节点正交（2026-09-19） |
| §93 | docs/nn/engineering.md §11 | 测试侧端口竞态：`spawn_bound_port()` 把「探端口 → 子进程 bind」收成一个出口（2026-09-19） |
| §94 | docs/nn/remote-transport.md §20 | 离线训练模式：启动选在线/离线 + 云端整段执行 + 补传归位（2026-09-19） |
| §95 | docs/nn/runtime-opt.md §2 | x20-rebirth it19 rollout 208s 复盘：权重并行下发 + kept 短路径 + tail-join grace 默认 0（2026-09-19） |
| §96 | docs/nn/engineering.md §12 | 并发退场 × 磁盘遍历：`walk_shard_dirs`（os.walk）取代 shard 树上的 `Path.rglob`（2026-09-20） |
| §97 | docs/nn/engineering.md §13 | e2e `check()` 静默失败：autouse fixture `_fail_loudly` + 三条被它揭出的既存红（2026-09-20） |
| §98 | docs/nn/engineering.md §14 | 门禁墙钟 157s → 24s：五个「测试替生产超时白等」的坑 + per-test 耗时预算护栏（2026-09-20） |
| §99 | docs/nn/experiments.md §13 | x20-rebirth 停腿结算：it97 意外退出（非门限触发），plateau 判负（2026-09-19） |
| §100 | docs/nn/experiments.md §14 | x20-rebirth 终点结算：池外 it30 胜出但 c06 回测全超限，无合格终点 + 旧能力丢失（2026-09-19） |
| §101 | docs/nn/experiments.md §15 | x20-snowball 立项：里程碑 bonus 腿 + god-prefix 实现一半后否决 revert（2026-09-19） |
| §102 | docs/nn/engineering.md §15 | pathlib 钩子自证随 Py3.12 改写：accessor 已删、rglob 已并发容忍（2026-09-20） |
| §103 | docs/nn/engineering.md §16 | Windows 门禁耗时：TCP 空等 + NTFS 大量小文件（2026-09-20） |
| §104 | docs/nn/remote-transport.md §21 | 大 body 传输停滞：两侧同时沉默（2026-09-20，用户报障：云机 claim 第二个 job 后几分钟无动静、无日志） |
| §105 | docs/nn/remote-transport.md §22 | 大 body 低速重抽 + 每 job 传输账：痛在连接抽签的尾部，不在字节均值（2026-09-20） |
| §106 | docs/nn/engineering.md §17 | e2e 权重下发账本跨用例撞键：门禁随机 flake 的**残留根**（2026-09-20） |
| §107 | docs/nn/experiments.md §16 | x20-steady it76：中途停机 + 重启无损，代价是时间不是质量（2026-09-20） |
| §108 | docs/nn/training-stack.md §22 | continuous volume 报告真源 = 本轮盘上 shard（修 it76 winRate=0 监控盲区，2026-09-20） |
| §108 | docs/nn/experiments.md §17 | x20-steady it135 判决 + 回测：回测门过，判决门差约一半（2026-09-20） |
| §109 | docs/nn/remote-transport.md §23 | 引导期大 body 护栏 + wire 账聚合：M1（低速重抽）的收尾（2026-09-20） |
| §109 | docs/nn/experiments.md §18 | x20-steady 停腿结算：B 有效但不足，且"稳"未被分离证明（2026-09-20） |
| §110 | docs/nn/experiments.md §19 | x20-steady 终点判决（it175）：门边未过 —— 35.5 vs 35，差 4 局（2026-09-20） |
| §111 | docs/nn/experiments.md §20 | 两命对照：加一条命，低分局 35.5→1.6 —— 低分局≈开局事故（2026-09-20） |
| §112 | docs/nn/experiments.md §21 | God 两命对照（414000，800 局）：NN 追平均值、下限反超，差在通关转化（2026-09-20） |
| §113 | docs/nn/experiments.md §22 | 判决段解剖（414000 三份配对）：低分局 97% 是开局事故，金矿在残局+中段（2026-09-20） |
| §114 | docs/nn/experiments.md §23 | C 腿评审修双机制：阶跃改斜坡 + 配对改代码保证（2026-09-20） |
| §115 | docs/nn/experiments.md §24 | C 双臂停腿结算：无结论 —— kk=1 洗掉起点，C-0 it58 云机失联（2026-09-21） |
| §116 | docs/nn/experiments.md §28 | 人类探针 verdict：7/7 轻松可解，6 局首通 —— 开局无环境下限（2026-09-21） |
| §116 | docs/nn/experiments.md §25 | x20 判决段解剖数据归档入库：docs/x20-dissection-414000.md（2026-09-21） |
| §117 | docs/nn/experiments.md §29 | 分歧分析 verdict：探索失败（策略库缺货），不是执行失败（2026-09-21） |
| §117 | docs/nn/experiments.md §26 | 探针 A 补强结果：God-1命 在本段崩盘 seed 上 low% 46.9% ⇒ 开局桶【不封存】，第二轮"环境致死"判断被推翻（2026-09-21） |
| §118 | docs/nn/training-stack.md §23 | 架构：单一 PPO 路径落地（`--ppo` / `--remote-degrade-after` 双删）——训练侧不再有「算力位置」旋钮（2026-09-21） |
| §118 | docs/nn/experiments.md §30 | 人类 wave2：28/28 通关，"不往敌人堆里钻就轻松过"（2026-09-21） |
| §119 | docs/nn/remote-transport.md §24 | 教训：毒包熔断 + worker 崩溃响亮回传 + claim 可观测 + 发布端自检（accident.plan §4.1/4.2/4.3/4.5/4.6，2026-09-21） |
| §119 | docs/nn/experiments.md §31 | 人类 demo 转 BC 语料：37 局 → 24015 样本，工具落地（2026-09-21） |
| §120 | docs/nn/training-stack.md §24 | 教训：缰绳初值显式化 + 干烧熔断（accident.plan §5，2026-09-21） |
| §120 | docs/nn/experiments.md §32 | 人类 BC-ref 判死刑：held-out 255 局 mean 0.00，行为坍缩（2026-09-21） |
| §121 | docs/nn/engineering.md §18 | 修复：本地 resume 的 D14 判据同源化（accident.plan §2/A，2026-09-21） |
| §122 | docs/nn/console.md §9 | 控制台：开课回执的「起点-基线对照行」（accident.plan §5.3，2026-09-21） |
| §123 | docs/nn/experiments.md §27 | accident.plan 收尾七项：配对核对 / 中点杀臂 / 毒包解冻 / 启动模板（2026-09-21） |
| §124 | docs/nn/runtime-opt.md §3 | rollout/eval 优化落地：native features 内核进生产（plan/rollout-eval-opt.plan.md，2026-09-21） |
| §125 | docs/nn/runtime-opt.md §4 | prebuilt 交叉编译分发：节点不再需要 clang（plan/rollout-eval-opt.plan.md §2.5/T2，2026-09-21） |
| §126 | docs/nn/runtime-opt.md §5 | 真机验收：mac(darwin-arm64) / a95(linux-arm64·Termux) 上 native 全绿（plan/rollout-eval-opt.plan.md T2/T3/T4，2026-09-21） |
| §127 | docs/nn/remote-transport.md §28 | 传输∥PPO 优先级调度面（pull 线取活换面）：P1/P1.5/P2 落地（plan/transfer-scheduling，2026-09-22） |
| §127 | docs/nn/runtime-opt.md §6 | 手动 evalA 慢 6.7 倍的根因：绕开了节点池（339s → ~18s，改走 in-loop 同一派发器）（2026-09-22） |
| §128 | docs/nn/remote-transport.md §29 | 传输 QoS + 阶段账 + 软持有预取：P0 / P0.5（仪器）/ P2 落地（plan/transfer-scheduling，2026-09-22） |
| §128 | docs/nn/engineering.md §19 | 本机评估的子进程捕获：gbk 解码把 stdout/stderr 丢成 None（顺带刷屏 65 行/100 局）（2026-09-22） |
| §129 | docs/nn/remote-transport.md §30 | 竞速退役 + 控制台同批 + push 腿：P3 落地（plan/transfer-scheduling，2026-09-22） |
| §129 | docs/nn/remote-transport.md §25 | 离线整段五件套：云机按计划采够样本 / 回传与 PPO 并行 / 云上 A 层 eval（与 PPO 并行）/ 多课程串行 / 断点续跑锚点（2026-09-22） |
| §130 | docs/nn/remote-transport.md §31 | 异步结果回传：把 `out` 从关键路径上摘下来（plan/transfer-scheduling P2.5，2026-09-22） |
| §130 | docs/nn/remote-transport.md §26 | 离线课「点训练 = 重打包」：旧包先作废，导出失败再恢复（2026-09-22） |
| §131 | docs/nn/runtime-opt.md §7 | 并发口径统一：rollout 与 eval 共用 `max(cores−4, cores×0.8)`（2026-09-22） |
| §132 | docs/nn/tpu-perf.md §4 | TPU 步耗诊断进日志：把「8~10s/步」从猜测变成读数（2026-09-22） |
| §133 | docs/nn/tpu-perf.md §5 | 根因定案：demo 混 batch 的 host 索引让 XLA **每步重编译**（2026-09-22） |
| §134 | docs/nn/tpu-perf.md §6 | 真机验证：修复后 PPO 单轮 1200s+ → **88.9s**（2026-09-22） |
| §135 | docs/nn/tpu-perf.md §7 | 程序缓存容量旋钮：找不到 ⇒ 改走**持久化编译缓存**（2026-09-22） |
| §136 | docs/nn/runtime-opt.md §8 | 云机 rollout/eval 的单局看门狗：**>5s 即杀、原地重跑同一 argv**（2026-09-22） |
| §137 | docs/nn/remote-transport.md §27 | 补传 400 的真因 / 导入后指标表看得见 / 引导模块每次刷新（2026-09-22） |
| §138 | docs/nn/console.md §10 | 回传腿也点亮控制台（同步写 per-game.json + 课程账本行）（2026-09-22） |
| §131 | docs/nn/tpu-perf.md §8 | TPU ragged tail 收尾：`target_transitions` 取成 mb 的倍数（重号 §131 的第二条，2026-09-22） |
| §140 | docs/nn/runtime-opt.md §9 | 卷积内核单源化 + 四项循环重排（`src/nn/conv/**`，conv-optimize 落地，2026-09-23） |
| §141 | docs/nn/runtime-opt.md §10 | goal/intent 导出器入池 + `--serve` 协议收敛（`tools/sim/serve-loop.ts`，2026-09-23） |
| §142 | docs/nn/runtime-opt.md §11 | 卷积代码全部收进 `src/nn/conv/**` + wasm 产物可重现（2026-09-23） |
| §143 | docs/nn/runtime-opt.md §12 | 端到端实测：内核优化在一局 rollout 里的确切倍率（2026-09-23） |
| §144 | docs/nn/runtime-opt.md §14 | arm64 实机计时 + 逐项归因：无负项、零回落，但 x64 的 +44% 不迁移（2026-09-23） |
| §145 | docs/nn/runtime-opt.md §15 | arm64 逐阶段归因：6.4 ms 花在哪 + 为什么四项不迁移（FMA 上限实验）（2026-09-23） |
| §146 | docs/nn/engineering.md §21 | `common/` 共享原语层：12 组同名实现收敛 + 13 处 `text=True` 编码隐患（2026-09-23） |
| §147 | docs/nn/engineering.md §22 | 断开 `rl` ↔ `remote` 包循环：纯逻辑叶子下沉 L0 + 导出器路径单源化 + 分层守卫（SCC 证实零跨包环）（2026-09-23） |
| §148 | docs/nn/engineering.md §23 | 神模块拆分三连（S4）：① `loop_steps` 传输/发布簇 → `loop_transport`；② 远端 PPO 腿 13 方法/862 行 → `loop_remote`（= `TrainingSteps` 的基类，依赖方向 = 调用者依赖被调用者）；③ `hub_server` 的 admin 控制面 9 方法 → `remote/hub/admin.py`（并摸清「混入类型声明遮蔽类型库」与「随迁名成环」两个坑）；④ 拆 `worker.py` 前先铺**模块级状态契约**安全网（6 处真状态清点 / 防副本 / 行为可复现）；⑤ `worker.py` 的 wire/低速重抽/bulk 节流簇 → `remote/wire.py`（**状态随簇搬迁** = 唯一所有者 + 显式转发；`_WIRE`/`_BULK` 原地可变仍可任入口注入，`_BEST_RATE` 重绑式标量只认 `remote.wire`）；
⑥ HTTP 传输核心（`_request`/`_read_body`/`_get_with_retry`/`_get_opener`/`_sched_headers`/`_warn_non_200` + `BODY_*` + 2 状态）→ `remote/http.py`（keystone：业务簇都站在它上；依赖 `http ← wire ← worker`）；DI seam 随实现迁移，**按调用点命名空间分档**（`_get_with_retry` ⇒ patch `http`；直调 `_request` 的宿主 ⇒ 仍 patch `worker`）；
⑦ 作业工作区/TAR/git 物化（`REPO_ROOT`/`JOB_DIR_KEEP`/`_persist_result`/`prune_job_dirs`/`unpack_opt_tar`/`pack_opt_tar`/`unpack_payload_or_fail`/`_git_head`/`_ensure_commit`）→ `remote/job_fs.py`（BC 拆出的前置：`_persist_result` 非 BC 专属；五刀里唯一 **seam-free**——全仓直接调用无 `setattr`）；顺手发现 `_ensure_commit` 全仓零调用（既存死代码，已登记未删）；
⑧ BC 作业簇（`_bc_*` ×6 + `normalize_ppo_device` + `resolve_bc_seed` + `_run_bc_job`，363 行，本来就是连续一块）→ `remote/bc_job.py`（底座拆完后业务簇可整块搬；同样 seam-free；守卫第一次把「**顶层零 torch**」机械钉住）；门面：e2e 取 `_bc_post_epoch`/`_bc_fetch_resume`/`_run_bc_job`，tests 取 `normalize_ppo_device`/`resolve_bc_seed`/`_bc_device`（2026-09-23）；
⑨ 下载簇（`_progress_logger` + `download_payload`/`download_code`/`download_ts_code`/`download_blob` + `_cache_blob`/`_resolve_blob` + `_ensure_ts_code`，252 行）→ `remote/download.py`（**「注入点分档」第一次双向验证**：`_resolve_blob` → `download_blob` 是组内互调 ⇒ patch 迁 `remote.download`；宿主 `run_job`/`_prefetch_fill` 用裸名字 ⇒ 仍 patch `worker`；`BODY_*` 从 `remote.http` 取单一定义不复制；`_progress_logger` 孪生仍恰好两份）。
⑩ 作业取活/生命周期/回传面（`peek_jobs`/`request_priority`/`claim_job`/`_priority_rank`/`acquire_job`/`job_started`/`job_ready`/`abandon_job`/`job_status`/`start_cancel_watcher`/`post_result`/`release_job`/`worker_tag`/`_failure_detail`/`job_body_error`/`report_job_failure`/`heartbeat`，连续 543 行 / 17 名）→ `remote/job_lifecycle.py`（**seam 最密的一刀**：60+ 处 patch 逐点定档，迁 17 处 `setattr` / 5 个测试文件；两条新判据——① **「引用即接缝」**：判 worker 侧 patch 是否失效要用 AST 的 `Load` 而非 `Call`（`ResultUploader(upload=post_result, …)` 是传值引用，9 处 patch 照旧有效）；② **重绑式标量不做 `is` 恒等断言**（`_opener` 懒建重绑 ⇒ 原先 `test_http_split` 的恒等断言随文件顺序红绿翻转，本刀在 HEAD 上复现并修掉））。`worker.py` 3450 → **1805** 行；
⑪ **收口：`remote/` 内部依赖账本**——前八刀散在六个拆分守卫里的「不得反向 import `remote.worker`」收成 `tests/helpers/remote_dag.py`（`LAYERS` = 35 个模块 8 层的拓扑秩 + `DEFERRED_CYCLES` = 唯一允许的延迟环）+ `tests/test_remote_dag.py`（18 例：账本双向覆盖 / 顶层边严格向下且无环 / 延迟边同样向下 / 全图环恰等于声明值 / **环里不许出现顶层边** / 引导模块顶层零 `remote.*` / 解析不出的边必须为 0 / 合成源码自证活性 / 独立重实现对账）；★ 实测发现 `remote/` 内真有一个环 `run_loop ⇄ worker`（两边均为**有意**的函数内延迟 import，顶层图仍是无环 DAG）⇒ 当时登记而非拆（**同日 ⑫ 已拆掉**，见下） |
⑫ **拆环：半离线执行引擎下沉 `remote/plan_run.py`**——`run_loop ⇄ worker` 那个被登记的延迟环，两条原路线都没照原样走（① 把 `verify_plan_file`/`run_plan_job` 塞进 `job_fs.py` ⇒ 这俩函数的传递闭包**就是整个执行引擎**（≈963 行），塞进 184 行的 I/O 模块 = 造第二个神模块；② 让 `worker.run_job` 从调用方收这两个函数 ⇒ 方向对（注入）但**对象错**：它们只是门面，要注入的是「一轮怎么跑」= `run_job` 自己）；落地：引擎整块 → 新 `remote/plan_run.py`（**1035 行**，L2），`run_loop.py` 只留 CLI / 独立续跑 / 门面（**1451 → 491**），「一轮怎么跑」由调用方注入（`worker` 尾巴 `run_job_fn=run_job`，CLI 侧 `_real_run_job`），**引擎里那个 `_real_run_job` 兜底删掉**（它就是反向 import 的成因）⇒ 纯向下 + 参数注入 = 环消失、`DEFERRED_CYCLES` 清空；★ **选层是算出来的**：用户口径「沉到 L1」但账本是**拓扑秩**（`LAYERS[m] = 1+max(依赖层)`），`plan_run` 依赖最深到 L1 ⇒ 只能是 L2（顺手加成守卫 `test_every_layer_number_equals_its_topological_rank`，36 模块逐条验算）——要真按字面沉 L1，该做的是**把共同依赖再往下拉一层**；seam（第一次为「拆环」而非「拆文件」）：引擎读的模块全局（`iter_spec`/`pairs_for`/…）迁 `remote.plan_run`，且 `run_loop` **不再转发** `iter_spec`（打错模块 = `AttributeError` 响亮而非静默失效），引擎公开名由 `run_loop` `X as X` 门面（取名字可以、patch 无效），实迁 **1 处** `setattr`、其余测试与 `e2e/` 一行不改；新守卫 `tests/test_plan_run_split.py`（**10 例**）+ 反探针**七处全命中**（含 `plan_run` 标 L1 → 3 处红）；账本加两条硬断言（`undeclared == []` 全图零环 / `not DEFERRED_CYCLES` 要网开一面得先改守卫）；门禁 **2349 → 2359**（2026-09-23）；
⑬ **第九刀：拆宿主**——`worker.py` 余下全是宿主（`run_job` 752 / `worker_loop` 364 / `main` 127 / `_prefetch_fill` 69），按**一条真实调用链**切三块（**1814 → 1281**）：① **训练核**（`run_job` 650-1076，427 行：课程上下文/reward_fn → torch+种子 → 模型构建（opt_init 优先）→ opt 内容寻址 → 多卡 → kickstart ref → demo bank → PPO → 产物/result）→ 新 `remote/train_core.py`；② **进程生命周期**（`HOT_RELOAD_EXIT`/`_request_reload`/`supervise_worker`/`_release_cloud_machine`）→ 新 `remote/worker_proc.py`（纯 stdlib）；③ `_wire_block`（两个调用方都要）→ `remote/wire.py`；**先量再下刀**（AST：核的自由变量 = 11 个块外局部 + 9 个形参，输出只有 `result`/`course` 两个，`global` 0，测试 patch 过这一族名字 **0** ⇒ **seam-free**；唯一语义改动 = 核只收 `payload_bytes` 不收 `raw`，漏传交给 ruff `F821` 当场报出）；**选层级联**：核依赖最深到 L3 ⇒ 拓扑秩只能是 **L4**，`worker` 4→5、`run_loop`/`notebook_runtime`/`worker_server` 5→6、`offline_boot`/`push_bootstrap` 6→7、`notebook_boot` 7→8（秩断言 + 分层断言双向护住），`worker_proc` = L0；**源文本守卫迁移**（四个按行文字面找调用点的老守卫：「找不到就红」正是要的响亮）：`test_job_body_crash`（4 处 `job_body_error`）/ `test_tpu_backend_guard.TestWorkerWiring`（xla 接线）/ `test_worker_device`（`torch.device(...)` 实参）/ `test_priority_schedule`（取消回调接线）；新守卫 `tests/test_train_core_split.py`（**14 例**，含 **★ 接口双向一致**：调用点位置实参个数与关键字集合 == 核的形参集合——漏传一个就红，是本刀最可能的失误形态）+ 反探针**七处全命中**；门禁 **2359 → 2374**（2026-09-24） |
⑭ **第十刀：拆宿主之二（seam 最密的一刀）**——`worker_loop` 把轮询壳与「一轮」织在一起，后者（L943-1119，**177 行**）+ `_prefetch_fill`（69 行）+ `_result_settled` 闭包（21 行）下沉新 `remote/job_round.py`（**418 行**，L4 与 `train_core` 同层：靠 L3 业务簇组装一个单元），`worker.py` **1281 → 1042**；**seam 定档（唯一判据：调用点解析在哪个命名空间）**：`run_job` ⇒ **注入**（`run_job_fn=run_job`，它住 L5 不能反向 import；宿主在调用点读**裸名字值** ⇒ 那 10 处 `W.run_job` patch 一行不改 = 上刀「引用即接缝」的第二次兼现），`job_ready`/`abandon_job`/`release_job`/`report_job_failure`/`start_cancel_watcher` ⇒ 迁移（12 处 setattr / 4 文件），`peek_jobs`/`download_payload`/`PREFETCH_ROUND_SEC` ⇒ 迁移（8 处 setattr + 2 处 `W._prefetch_fill` 调用），`uploader` 跨 job 存活 ⇒ 宿主建制并传入，`_prefetch_fill`/`PREFETCH_WIRE_ID`/`settle_result` ⇒ **断言禁留门面**（留 `X as X` 就是个静默空操作的 patch 目标）；`multi`（“有几个 hub”）是宿主概念 ⇒ 宿主算好 `code_cache_dir` 传；宿主账目改为回读 `RoundOutcome{jid, ok, uploaded, stop}`（搬前它们直接改宿主局部 `done += 1` / `_polls_since_accept = 0` / `return done`）；新守卫 `tests/test_job_round_split.py`（**13 例**，含 **★ 接口双向一致**（20 个入参逐名对账）+ **★ 两条功能性**（假 round 验 `uploaded ⇒ done==1` 与 `stop ⇒ 整条退出`；填充器炸弹/记数验档位二））+ 反探针**七处全命中**；顺手修两个**守卫自身的坑**（上游集合改为**从账本推**——写死的带引号字面量会被 `test_subproc_util` 的 spawn marker 误判；两处早先守卫的 `startswith("remote.worker")` 前缀匹配会误伤合法的 `remote.worker_proc`(L0) ⇒ 改按模块名精确比）；门禁 **2374 → 2387**（2026-09-24） |
⑮ **第十一刀：拆宿主之三（`hub_server` 路由面全切完）**——25 个路由方法（13 `_get_*` + 12 `_post_*`，本体 **644 行**）按**域**分四组混入，与第三步的 `AdminRoutes` 并列进 MRO：`hub/schedule.py`（取活/租约/打点·8 方法·153 行·L0）· `hub/result.py`（回传与终局·7·240·**L4**）· `hub/blob.py`（字节服务·5·46·L0）· `hub/offline.py`（离线段·5·205·L0），`hub_server.py` **3728 → 3017**；**层号是算出来的**（第三次兼现）：`_post_result` 走 `push_dispatch.accept_result`（推/拉必须共用同一个校验函数）⇒ `result` 秩只能 **4**，宿主被迫 **4→5**、`smoke_loopback`/`tunnel_ab_probe` **5→6**（其余三组只依赖 `common.protocol` ⇒ 与 `hub.admin` 同层 L0）；**Phase A 纯搬（逐字节等价，只改 `self.` 的解析命名空间）→ Phase B 去重**刻意分两步（混做会让「某条端点变味」分不清是搬错还是收错）；四类形状收成 **5 个助手（实现只住 `hub_server`，混入以 `Any` 声明并消费）**：`_job_or_404(known=)` 14 处 · `_job_body(cap, known=)` 4 · `_lease_token()` 5（`X-Lease-Token`/`lease-token` 双写法）· `_serve_path(p, missing=)` 5（404 带**具体**原因）· `_read_raw_body()` 3（**上限留给调用方**）；**语义保留点**：`known=True` 只给 `/start` `/fail` `/result`，`/ready` **故意** `known=False`（回传就要开始的那份可能还没落 manifest）· `_job_body` **先闸后体**（写错的 URL 不该让服务端白读一段远端体）· 鉴权仍在**没有 job** 的端点内联（计数钉住）；`PRIORITY_BODY_MAX`/`PEEK_MAX` 随迁 `common/protocol.py`（后者有两个读者——`hub.schedule` 的端点与 `_HubQueue.peek_jobs` 的形参默认值——谁也 import 不了谁 ⇒ 必须住协议层）；新守卫 `tests/test_hub_routes_split.py`（**15 例**：定义唯一 + `HubHandler.X is Mixin.X` **对象级**接线 + 助手唯一实现**且各自活着**（≥N 调用点防死助手）+ **★ 漂移警报**（混入里不许再出现那四种内联形状）+ 内联鉴权计数 + 零上向依赖 + 账本层号关系 + **★ 功能性**（`_Probe(hs.HubHandler)` 用 `object.__new__` 起无 socket 实例走完两侧契约——实例上挂 `_json`/`_bytes`/`_auth_ok` 会让 `mypy method-assign` 与 `ruff B010` 互斗，改**真子类重写** + 空 `__init__` 绕开 `BaseHTTPRequestHandler.__init__`））+ 反探针**十一处全命中**；**又一次撞上「读源码文本的守卫」**（写 `layers["remote.hub_server"]` 会被 `test_subproc_util` 当成「起了真服务进程」⇒ 改成按叶子名从账本取，**连解释这件事的 docstring 里也禁那个带引号字面量**——`_code_of` 只剥 `#` 注释、保留 docstring）；门禁 **2387 → 2402**（2026-09-24） |
⑯ **死代码清理 + 第十三刀：物料落地三兄弟下沉 `download` +「不再拆」的明文理由**——先删 `remote/job_fs._ensure_commit`（第六步之一登记的挂账：全仓零调用、只搬未删；留着的真实代价是让「git 物化」看起来有两条路径，一条真跑、一条从未跑过）及其 `__all__` 条目 / `worker.py` 门面 / 守卫清单；再按「重构 `run_job` / `worker_loop` / `main`」侦察，结论是**只有 `run_job` 里还有一整段可搬**（69 行物料落地）——刀口判据不是「哪段长」而是「哪段的**判据同源**」：payload / code / ts_code 三者失败语义同规（sha 不匹配 ⇒ `RetryableError`（传输损坏，重下可修复）；解包失败 ⇒ `ProtocolError`（内容决定性，不重认领）；sha 内容寻址 + tmp 原子改名），谁改一条另两条必然跟改 ⇒ 必须同住一个模块，于是落成**三兄弟** `_ensure_payload` / `_ensure_code` / `_ensure_ts_code`（`worker.py` **1039 → 1033**，`run_job` **350 → 300**；`download.py` **313 → 519**；层号**一处没动**——`download` 仍 L3，新增边 `job_fs`(L1) 本就向下，无级联）；三者各返回 **`NamedTuple`**（`PayloadLanded` / `CodeLanded`）而不是「顺手多返几个目录」：调用方要的 `code_root.parent`(=`ts_code_cache`) 与 `blob_root` 推导写两遍就是两个口径，而搬前这两者在宿主里**是同一个变量名**（`code_cache_dir` 先当共享根、后被改成 per-sha 目录）——参数改叫 `code_cache_root`、返回值里 `code_root` 与 `code_cache_dir` 分列，顺手把这处**同名歧义**也拆了；**两条语义顺序**（碰了就是 bug，写进 docstring）：清场在解包**之前**（解包要面对空目录，否则 D14 血缘校验与训练链读到上一轮脏数据）· prune 在清场**之后**（放末尾 ⇒ 失败轮永远轮转不掉旧目录）；`sys.path.insert` **留**落地（落地与「可 import」是一件事），热替换护栏 `_ACTIVE_CODE_SHA` **留宿主**（那是**进程**的状态）；顺手的第二处：`worker_loop` 两条分支各写一份的存活日志收成 `_alive_log` + `ALIVE_LOG_SEC`（2026-09-11 现场就是**漏了第二处** ⇒ 停机期日志静默被误读成「worker 罢工」；「到点没有」与计数器复位仍留调用方 = 宿主的账）；**★ 本刀把「不拆」也变成决定**（写进 `worker.py` 头部，不靠口口相传）：三个宿主函数不再往下切——理由是它们就是「宿主」这个概念的形状（`worker_loop` 持有**跨 job 存活**的 `uploader`/`pf_stores`/`halt_seen`/轮询计数器，搬走 = 把一个对象的生命周期交给两个模块管；`main` 的 argparse 声明是**数据**，先例 `remote/run_loop.py`；`run_job` 每分支只做一件事，再切只是把直线扯成跳转），余 **287 行 / 100 条 import / 109 个名字**是显式转发门面；**注入点第三档**：组内互调（`_ensure_*` → `download_*`）⇒ patch `remote.download`；预取填充器 ⇒ `remote.job_round`；宿主 `run_job` 自己读的只有三兄弟 ⇒ `remote.worker._ensure_*`——`download_*` 的转发名**还在但已无读者**（tests 把 `worker` 当**取名字的入口**，**名字是契约、位置不是**：对它们 patch `worker` 现在会**静默失效**）；守卫 `test_download_split.py` +3（★ run_job 里**零**下载调用点（AST 级，比「名字在不在」更硬）· ★ **接口双向一致**（13 个形参，漏传/打错名/多传都红）· ★ 功能性（炸弹放 `worker`、真值放本模块 ⇒ 必须走到「sha 不匹配 ⇒ `RetryableError`」且**不写任何文件**））+ `test_job_round_split.py` +1（★ 存活日志恰好两处调用 + `halted` 一真一假的**字面量** + `polling hub` 只由 `_alive_log` 拥有 + 阈值比较恰好两处且都用 `ALIVE_LOG_SEC`）；反探针 **11/11 命中**；门禁 **2402 → 2406**（2026-09-24） |
⑰ **第十四刀：拆状态（`_JobStore` → 六个域混入）**——`hub_server.py` 里 `_JobStore` 独占 **1002 行 / 49 方法 / 19 个状态字段**，而**一把 `_lock` 守着全部**（30/49 个方法取锁）；按用户指令拆状态，**定档结论 = 混入而不是协作对象**（三条实测判据：① 一把锁是类的**不变式**，各持一把锁 = 换并发语义，不满足「零行为变化」；② 跨域互调 **37/49**（`_claim_locked` → `_job_priority_locked` / `_collect_expired_locked` → `_drop_commitment_locked` → `_bump_epoch_locked`）⇒ 混入留在 `self.X` 上 = **零 seam**；③ tests 直读 `store._leases` / `._lease_owners` / `._stale_holders` / `._claimed` / `._backup_authorized` / `._last_heartbeat` / `halt_workers`（20+ 处断言）⇒ 协作对象会让这些**全部改路**）；六个混入 `store_{ledger,wire,scheduling,leases,results,offline}.py`（47 方法与本体 895 行搬走，共 1273 行）⇒ `hub_server` **3017 → 2072**（−31%），组合类只剩 `__init__` / `note_worker` + **进程级状态**（`halt_workers` / `_workers`——多课程单 hub 下 `_HubQueue` 借的就是这一份）；**状态声明分散的对价**：四个有状态的域各一个 `_init_<域>(self)`，组合类 `__init__` 逐个**显式**调用（不用 `super()` 链：顺序要读得出来），`store_results` / `store_offline` 真无常驻状态 ⇒ **不造 `pass` 空钩**（守卫改成正面断言「其方法体里零 `self.X = …` 赋值」）；顺手删掉旧 `_JobStore.__init__` 里那把**会被 `_AuthGuard` 覆盖掉**的 `Lock()`（一个被丢弃的锁对象是下次读的人的陷阱）；随簇搬走两个对外名字（`ClaimOutcome` / `FREEZE_AFTER_RECLAIMS` → `store_leases`，由 `hub_server` 反过来 import 保测试的取名字入口；`_write_bytes` → `store_offline`——它的唯一调用方在那里，留在原处必成环；离线簇另需 `remote.artifacts` ⇒ 该簇 **L1**，其余五个 L0）；**★ 纯搬对账**：AST 逐成员比对 HEAD vs 新家 ⇒ **58 个成员逐字节等价、零差异**，旧 `__init__` 的 21 条赋值 + 43 行字段注释全部逐字在新家（规模到这个量级时「测试绿」不是搬运等价性的尺子）；守卫 `tests/test_hub_job_store_split.py`（**17 例**：定义唯一 + `_JobStore.X is Mixin.X` 对象级接线 + MRO 逐项对账 + 类常量经 MRO 可达 + 状态归属唯一 + 无钩子即零状态 + 钩子各调恰好一次 + 混入**彼此零 import** + `_write_bytes` 只剩一份 + **★ 两条功能性**：跨域链路（发布 → 认领 → 计量）落在同一个对象上 · **持有 `_lock` 时连最「独立」的计量簇也必须阻塞**（同一把锁，不只是同名属性））+ 反探针 **11/11 命中**；门禁 **2406 → 2423**（2026-09-24） |
⑱ **第十五刀：`_HubQueue`（1033 行 / 76 方法）拆七混入 + 两个类搬出宿主**——先侦察用户点名的「28 个同名委托方法」，侦察**推翻了第一直觉**（不是可删的重复、更不能收成 `__getattr__`）：① 签名分三档——28 条逐参数一致、3 条多一个**前置 `course`**（`claimable_job_ids` / `store_offline_artifact` / `store_offline_result`：store **每课程一份**、队列要跨课程寻址）、1 条**改名**（`abandon` → `abandon_job`）；② 缺归属时返回值**逐方法不同**（`False` / `None` / `{}` / `[]` / `0`）⇒ 收 `__getattr__` 得把一张 31 行「空值表」藏进字符串；③ 它是类 docstring 写下的**对外承诺**，`__getattr__` 会让 mypy 看不见、IDE 跳不过去 ⇒ 改成**三张写死闭集表 + 可执行断言**；**★ 侦察的第二个产出：`queue_peer.QueuePeer` 共同声明面**——七混入必须互不 import（本刀要消灭耦合）却各自要调兄弟方法 ⇒ mypy 一片 `attr-defined`；「各造一份 Protocol」七份会漂、「import 兄弟」违反本刀目标 ⇒ **74 个 `def X(...) -> T: ...`（零实现）**，混入继承它：mypy 看声明、运行时由后续混入的活动实现覆盖，配套两条守卫（体里只有 `...` · 与真实现逐参数一致）——⚠ 第一版把「纯声明」写成「body 里只有 `ast.Expr(Constant)`」，**把 `...` 自己也滤掉了**，`halt_of` 带 docstring 才暴露；`_store_of` **刻意不进**（那要 import `hub.store` ⇒ `queue_peer` L3 ⇒ 混入 ≥L4 ⇒ `hub.queue` L5 ⇒ `hub_server` L6 = 与 `smoke_loopback`(L6) 同层）；**A 相（使能）**：`queue_resume` 要按课程构造/注解 `_JobStore` 而 `remote/hub/*` 不得 import `hub_server`（成环）⇒ 先搬两个类——`_AuthGuard` + `_is_loopback` → `hub/auth.py`(101 行)、`_JobStore` 组合类 → `hub/store.py`(108 行)，两处都**自别名 re-export**（`hs._AuthGuard` / `hs._JobStore` / `patch remote.hub_server.*` 全部照旧）；**B 相**：七混入 `queue_{scope,discover,auth,claims,resume,observe,store_face}.py`(15/3/5/9/10/14/18 成员，222/164/68/294/258/199/137 行) + 组合类 `hub/queue.py`(164 行) ⇒ `hub_server.py` **2072 → 887**（**累计 3017 → 887，−71%**）；**拆法判据与第十四刀同源**（一把 `_lock` 是类的不变式 / 跨域互调是常态 / tests 直读私有状态）⇒ 仍是**混入**（同一个对象、同一把锁、零行为变化，**测试一行没改**）；**★ 与第十四刀刻意相反：`__init__` 不拆钩子**——`_JobStore` 那时拆四个 `_init_*`（状态散在 900 行、四域独立），这里只有 44 行且几处**咬合**（`_solo` 决定 `_now`、`_discover_root` 决定 `_discover_last` 初值、`_adopt_solo` 运行期把 `_halt_default` / `_workers` / `_auth_fail` 从 store 搬到 `self`）⇒ 按域切只会把直线扯成跳转，守卫改为正面断言「组合类体只有那两个类常量 + `__init__` 每条状态声明都带值 + 申报字段集**恰好**是状态表的键集（少的那两个由 `_AuthGuard.__init__` 声明——同一个对象的两段 `__init__`）」；**★ 门面的两面对账**：签名一致抓不住「方法还在、活不干了」⇒ 活性判据**不看名字看结构**（体里**恰好一处**「拿到 store 的取用」且转发目标名 == 声明值），取用**四种写法都认**（`_store_of(job_id)` / `_stores[course]` / `_stores.get(course)` / `_solo`）—— 这是**跑出来的**：第一版只认前两种，`note_worker` 与 `claimable_job_ids` 立刻顶出来，`note_worker` 因此被认定为**唯一一条非转发的同名方法**（队列自己就是登记表的拥有者：`_registry()` 单课程取 store 的 `_workers`、多课程取自己的），写成例外表 `FACADE_OWN` + 一条**反面**断言（它必须碰不到 `_store_of` / `_stores` / `_solo`）；**纯搬对账**：AST 逐成员比对 ⇒ **76/76 逐字节等价**，旧 `__init__` 的 16 条字段声明 + 43 行字段注释逐字在新家，七混入**零重名**；守卫 `tests/test_hub_queue_split.py`（**25 例**，含三条功能性：跨域链路落在同一个对象上 · 持有 `_lock` 时**最外层门面也阻塞** · `_adopt_solo` 跨域搬进程状态）；反探针 **14/14 命中**（域成员换家 · MRO 换序 · 门面签名漂 · 转错名字 · 多取一次 store · `note_worker` 变转发 · 混入带值类常量 · `__init__` 冒出未登记字段 · 状态写者漂 · 声明面长出实现 · 声明面收 `_store_of` · 混入偷 import 兄弟 · 层号漂 · 改名转发漂）；**⚠ 操作教训**：反探针**锚点也要断言命中次数**——⑨ 选 `set_halt` 写 `_halts` 时同方法后面还有 `_halts.clear()`（也是写者），⑭ 的 `abandon` 锚点少写第二个实参：两次「没红」都**不是守卫空档而是锚点写错**，脚本现每条 `assert count(old) == 1`；门禁 **2423 → 2449**，mypy **401 → 413**（2026-09-24） |
⑲ **第十六刀：`hub_server`（887 行）收口 —— HTTP 面 / 引导链 / 薄入口三件**——按用户指令「拆 hub_server 剩下的引导链与 HTTP 面，收口 S4」执行 ⇒ `remote/hub_server.py` **887 → 100 行**（**累计 3017 → 100，−97%**），剩余部分只有 **20 行代码**；三件东西两个新家：`hub/http_face.py`（**L5**，575 行：来源判定（`CF_SOURCE_HEADER` / `SEND_*` / `_is_ip_literal` / `attributed_source`）+ `HubHandler`（五组路由混入的组装 + 5 个通用助手））· `hub/boot.py`（**L6**，310 行：`DISCOVER_SCAN_SEC` 等常量 + `as_hub` / `make_server` / `main`）· `hub_server.py`（**L7**，100 行：入口 + **17 条**自别名 re-export + `__all__`）；**为什么两处而非一处**：HTTP 面对**每个请求**负责、引导链对**一次进程启动**负责（读者/生命周期/失败模式：请求级 500 vs 启动即 `exit(1)`），合成一处就得让 argparse 与 `BaseHTTPRequestHandler` 同住；**层号先算后切**（`LAYERS` 是拓扑秩 ⇒ 中间插一层会顺反向边涨上去）：切前用脚本模拟、切后实测复核 ⇒ **级联只有 3 个模块**（`hub_server` 5→7 · `smoke_loopback`/`tunnel_ab_probe` 6→8），顺带修掉账本顶部把 `hub_server` 列在「L4 组装」的**过时散文**（数字有守卫管、散文没人管）；**★ 薄入口的 `__all__` 就是契约**：入口现在**零定义**（无 `def`/`class`，唯一赋值是 `__all__`），守卫**正面断言**这条（比「11 个搬走的成员不在」更硬 —— 它挡的是「顺手在入口补个小函数」，而层号看不出「只长了一个小函数」），配套 `hs.X is 新家.X` **逐条对象恒等** + 17 名**闭集** + 入口不得挂实现用 import；**★ 踩到的真坑**：`SEND_TIMEOUT_SEC` 的唯一读者 `HubHandler._bytes` 读的是**所在模块的全局** ⇒ 搬走后 `setattr("remote.hub_server.SEND_TIMEOUT_SEC", …)` 变成**静默空操作**（名字还在、没人读它；第十三刀「名字 ≠ 注入点」第二次现身，由 `test_body_transfer_guard` 的「对端半开必须超时断开并打印」真跑时当场报出），守卫两条机械化：`_bytes` 里必须是**裸 `Name`** + 全仓**恰好一处** `setattr` 且必须写在**实现所在模块**上（AST 判据，不用逐行找子串——第一版正是被自己 docstring 里那段「这个坑长什么样」的原文判红）；**⚠ 搬走代码会静默废掉四条读源码的守卫**（本仓第三/四/五次撞上）：`_class_methods(HUB_SERVER, "HubHandler")` → `StopIteration`（改读 http_face）· `test_hub_admin_split` 的「名字不在 hub_server 里」变成**恒真空话**（改成「不在入口也不在 http_face」）· `test_jobs_next_retired::_PROD_FILES` 扫一个只剩 re-export 的**空壳** ⇒ 有人把 `/jobs/next` 加回路由表**不会被发现**（把 http_face 加进扫描集）· 本守卫自己的 docstring 被 `test_subproc_util` 的 spawn marker 命中（改用 `hs.__name__` 取名字）⇒ **搬文件时必须一条条问「谁会按路径读它」**；**★ 顺手修掉一个跨项目静默回归（第十四刀留下、HEAD 上已经红）**：dashboard 的镜像常量守卫 `poison-unfreeze.test.ts` 按**写死路径**读 `hub_server.py` 找 `FREEZE_AFTER_RECLAIMS`，而该常量第十四刀已搬到 `hub/store_leases.py` ⇒ 用例当场失败，但**没有任何门禁会发现**（nn 侧 pre-commit 不跑 dashboard 测试、dashboard 侧只在动过 `dashboard/**` 时才跑）——修法不是换一个写死路径（下次再断），而是**在 python 源码树里搜定义**（搬家不再红、常量真消失才红）；同族盲区：dashboard 监督器哨兵 `pySentinels(HUB_SERVER_ENTRY)` 只盯入口那**一个文件** ⇒ **改 `hub/http_face.py` 不触发重启**（第十一刀起就这样，`hub/schedule.py` 等一直不在哨兵里）⇒ 修成 `hubImplementationFiles()` 枚举 `remote/hub/*.py`，并加一条 dashboard 守卫钉住「哨兵覆盖全部实现文件」；**纯搬对账**：AST 逐成员 ⇒ **11/11 逐字节等价**（含 411 行 `HubHandler` + 202 行 `main`），旧 body **零残余**，docstring 59 行**只改首行**；守卫 `tests/test_hub_entry_split.py`（**13 例**，含两条功能性：从门面拿 `make_server` 真起 127.0.0.1 服务打通 `/ping` 且错 token 401 · `as_hub` 幂等）；反探针 **12/12 命中**；nn 门禁 **2449 → 2462**，mypy **413 → 415**，根 `bun run check` 2120 pass / 0 fail，dashboard **1105 pass / 0 fail** + typecheck 绿（2026-09-24） | ⑳ **第十七刀：`TrainingSteps` 的 in-loop 评估链 → `rl/loop_eval.py`（S4 收口后 `rl/` 侧继续）**——按用户指令「按同一条『真实调用链』手法拆 `TrainingSteps` 本体」执行 ⇒ `rl/loop_steps.py` **940 → 666 行**，新模块 `rl/loop_eval.py::TrainingEval`（**375 行 / 8 成员 / 5 状态槽**）。**刀口先量后定**：20 个方法里只有 **7 个互相调用**且连成一条链（派发 → 尾巴收拢 → PPO 收官 join/交棒 → 收官 drain），其余 13 个都是被轮内步骤各自调用的**叶子**；外部入口只有 3 个（轮内 2 + 收官 1）⇒ 方向 = 调用者依赖被调用者。**切法仍是混入**（同一对象、零行为变化，测试一行不改），但**基类元组是追加**：`class TrainingSteps(TrainingRemote, TrainingEval)` —— 2026-09-23 写下的 `__mro__[1] is TrainingRemote` 与四个「继承真混入」的宿主逐字仍成立。**★ 与第十四/十五刀最大的不同：搬走的全是方法与槽位、模块级名字为零 ⇒ 没有任何 patch 点需要迁移**（对 `rl.loop_steps.*` 的注入本来就是空操作）。**★ 状态归属唯一**：五槽位声明只在 `TrainingEval`；旧类里剩两处**跨模块手**（`_log_report` 写 `_eval_thread`、`_record_iteration` 读 `_eval_join_sec`）**经继承**解析，被写成闭集表（第三条手即红），并有**功能性**守卫真跑这条交棒链（写者住旧模块、读者住新模块、尾巴落在同一实例）。守卫 `tests/test_loop_eval_split.py` **11 例**（成员闭集 / 对象恒等 / 基类元组 / 五槽位单处声明 / 跨模块手闭集 / 顶层 import 闭集 + DI 只许延迟 / 不得反向 import / 跨模块交棒 / 占位响亮失败），反探针 **14/14**；纯搬对账 **8/8 逐字节**（1 处**申报**差异：占位 raise 文案点名新家）+ 5/5 槽位 + 旧类零残余。**⑥ 次撞上「按路径读源码的守卫」**：`tests/test_eval_a_once.py` 断言 loop_steps 源码里含 `eval_dispatch import dispatch_eval_bg` ⇒ 随搬家红（这次是响亮失败），修法升级为**在 `rl/` 树里找谁持有这个名字** + 「拿到它的模块里住着 `_dispatch_delayed_eval`」；另**`ruff format --check` 不是门禁**（HEAD 上本来就有两处不格式，别顺手 format 把 diff 弄脏）。nn 门禁 **2462 → 2473 passed / 3 skipped**；mypy **418** 源文件；根 `bun run check` 2119 pass（1 例 `dist-node-gate` 计时 flake，单独复跑 3/3 绿）。决策 → `DECISIONS.md` §2026-09-24-goalnn-loop-eval-chain-split；全文 → `engineering.md` §23「第十七刀」；计划 → `plan/nn-training-refactor.md` §5.3.16。
㉑ **第十八刀：`TrainingLoop` 的动态采集链（9 成员 / 445 行）→ `rl/loop_volume.py`（`rl/` 侧继续）**——按用户指令「全程自主，继续按同一手法拆 `rl/` 侧神模块」执行 ⇒ `rl/loop_core.py` **1386 → 931 行（−33%）**（搬走 462 行 = 445 方法 + 9 行分节注释，留下 8 行指路注释），新模块 `rl/loop_volume.py::TrainingVolume`（**573 行**）。**先量后定且把量法工具化**（新写 `tests/` 外的侦察器：类内调用图 + 连通分量 + 每方法读的模块全局 + 写槽，一次输出）：`TrainingLoop`（25 方法 / 1089 行）有 **3 条链**（volume **9** · 生命周期 7 · 基线 2）⇒ 取最大且最内聚的 volume（且恰是旧类的**尾块**，连续 462 行纯搬）；同一把尺跑了另外两份候选：`rl/batch_eval.py`（**1785 行，全仓最大**）的 35 个顶层函数是**一个 28 节点巨团**（`consume_requests ↔ claim_pending ↔ read/write_batches ↔ mark_unit_done ↔ _requeue ↔ _persist_of ↔ maybe_dispatch_batch ↔ units_for_batch → plan_units/plan_verdict_units`）⇒ **按链切不动**（要拆得先造「批存储」接口 = 真设计改动），`rl/bc_loop.py` 的 9 方法分量次之。**方向 = 调用者依赖被调用者**（与第二步 / 第十七刀同源）：本簇生产入口**全部**在 `RoundSteps`（`step_course_iter` → `_iteration_pairs`；`step_rollout` → `_volume_active` / `_volume_collect_continuous`）⇒ **`class RoundSteps(TrainingVolume)`**；**刻意不走「给 `TrainingLoop` 加基类」**（那要改组合类 + 四个「继承真混入」的宿主，并让第十七刀守卫的「组合类三件套不变」失守）⇒ `TrainingLoop.__bases__` 与**全部既有守卫一行不改**。**★ 本刀唯一要迁的 patch 目标 = `log`**：`_volume_topup` 的日志（「未达标」/`wave_cap`）按模块全局解析 ⇒ `e2e/test_volume_e2e.py` 里 `setattr(rl.loop_core, "log", …)` 搬后成**静默空操作**（第十六刀 `SEND_TIMEOUT_SEC` 的同族形状，本仓第三次），已改到 `rl.loop_volume.log`；`dist_common` 在两个方法内各有一份重复 import ⇒ **删顶层那份**（而非方法内）——唯一能保持「方法体逐字节不变」的改法。**★ 两个坑都是「散文会撒谎」**：① 侦察初稿把方向写成「外部入口全在 `RoundSteps`（`step_rollout` / `step_volume_topup`）」——而 `step_volume_topup` 是 **VOLUME_RULE_V2 后的退役空步**（`return None`），`_volume_topup` 生产**零调用点**（只由 e2e/单测以 unbound 形式驱动）⇒ 被守卫里「RoundSteps 真在调它」当场顶出，四处散文改对并把「退役空步」本身写成守卫；② 分层快照按设计**先红再登记（第三次）**：`loop_volume → rl.rollout_phase` 使它成为「经 rl 传递可达 remote」的一员 ⇒ 登记进 `RL_ORCHESTRATION`（并写明它**自己不经 remote**，与既有两条理由不同）。**守卫** `tests/test_loop_volume_split.py` **11 例**（成员闭集 / `TrainingLoop.X is TrainingVolume.X` 对象恒等（既有用例正是 unbound 绑定）/ `RoundSteps.__bases__` + 组合类三件套 + **判定 MRO 逐项** / 七槽位声明在新家且 `__init__` 仍全部赋值 / **跨模块手闭集**（`__init__` Store×7 + `_record_iteration` Load×3，量出来的）/ 顶层 import 闭集 / `rl.volume_waves`·`rl.volume_quota` 只许延迟 / 不得反向 import / **两条功能性**：`log` seam 在本模块（打 `rl.loop_core.log` 一个字节都收不到）· unbound 经 MRO 取真实现），反探针 **14/14**；**纯搬对账 9/9 逐字节、零申报差异**（本刀连文案都不用改）+ 旧类零残余 + 新家成员闭集。**顺手同步的 provenance**：`rl/__init__.py` · `README.md` 模块表 · `loop_core.py`（模块 docstring + MRO docstring + 旧位置留**指路注释**）· `loop_round_steps.py`（docstring + 基类 + 删四条已被继承取代的 `Any` 声明）· `volume_waves.py`（5 处模块路径）· `loop_steps.py`（`_volume_stages` 的「定义在 `TrainingLoop` 上」改写）· `tests/test_layering.py`（快照 + 理由）。**刻意不动（写明理由）**：`curricula/x1-rebirth.jsonc` 与 `x3-power.jsonc` 里两处注释仍写旧路径——课程文件**字节**就是 `course_fp` 血缘，为一句注释改字节会让在飞腿的课程身份漂移；`DECISIONS.md` / `plan/dynamic-rollout-volume.plan.md` 的旧路径是历史记录，不改写。nn 门禁 **2473 → 2484 passed / 3 skipped**；ruff `All checks passed`；mypy **420** 源文件；根 `bun run check` 2120 pass / 0 fail；**零 `dashboard/**` 改动**（故不触发其门禁）。决策 → `DECISIONS.md` §2026-09-25-goalnn-loop-volume-chain-split；全文 → `engineering.md` §23「第十八刀」；计划 → `plan/nn-training-refactor.md` §5.3.17。

 ㉓ **第二十刀（S4）：组合根收尾 —— 三簇叶子出组合类 ⇒ `TrainingLoop` 成为纯组合类**——按用户指令「拆 `loop_core` 剩下的基线评估链（2 成员）与叶子方法，让组合根成为纯组合类」执行 ⇒ `rl/loop_core.py` **446 → 240 行**；余下的 7 个叶子按**判据同源**分三簇各自成模块（合计 355 行 = 128 + 96 + 131）：`rl/loop_baseline.py::TrainingBaseline`（`_baseline_eval_weights` / `_maybe_dispatch_baseline_eval`，判据「日志里有没有这份 bc 权重的 it0 干净评估」）· `rl/loop_iter_dir.py::TrainingIterDir`（`_check_quota_incident` / `_prepare_iter_dir`，判据「`self._traj_dir` 里有没有本轮的活」）· `rl/loop_dispatch.py::TrainingDispatch`（`_rollout_phase` / `_eval_on_round` / `_evalboard_yield`，判据「本轮把活派给谁 / 让位给谁」）。组合根余下**只有** `__init__` + `_run_inspect`。**宿主：三簇都挂 `RoundSteps` 一侧，`TrainingLoop.__bases__` 一行不改**——mixin 级父调用者全部是 `RoundSteps`；`_eval_on_round` 另有 `TrainingEval` / `TrainingGuards` 两个 sibling 调用者，但三者互不继承**不构成**「必须挂组合根」的理由（`RoundSteps` 是组合根的第一个基类，挂它的基类里已在 `TrainingLoop` 的线性化上；与第十九刀的区别：那刀两个 caller 都是组合根的基类且交集为空）。**★ 唯一真约束 = `_eval_on_round` 的顺序契约**：`rl/loop_eval.py` 的同名成员是**占位**（body `raise`），真实现必须在 MRO 里更靠前，否则占位胜出 ⇒ 静默返回 falsy 把 eval 全关掉；挂 `RoundSteps` 一侧天然满足，改成组合根**末位**会反过来胜出（反探针 ⑤）。**结构性例外（「纯组合类」在本仓的精确含义）**：`_run_inspect` + 模块级 `run_inspect` 必须同住组合根（`_run_inspect` 按**模块全局**解析它——文档化可替换点，`rl/loop.py` 再导出；而那个模块不能 import `rl.loop_core` 当基类 ⇒ 成环）⇒ 守卫 `test_composition_root_keeps_only_the_structural_pair` 正面钉住闭集 `{__init__, _run_inspect}`。**三张跨模块手表**：入边闭集（`_check_quota_incident` / `_prepare_iter_dir` / `_rollout_phase` / `_evalboard_yield` / `_maybe_dispatch_baseline_eval` 各 1 · `_eval_on_round` 3）· **出边为空**（三簇互不调兄弟方法）· 槽位读者闭集 + **写手唯一**（`_course_fp` / `_corpus_fp` **只在** `loop_lifecycle._setup_common` 被**赋值**，本刀三簇只读）。守卫 `tests/test_loop_core_tail_split.py`（**13 例**，含**全量 MRO 名单与 `RoundSteps.__bases__` 的唯一所有权**——S18/S19 那两处改成只钉相对位置，避免同一份名单三处各自漂；含 **★ 四条功能性**：`_check_quota_incident` 真跑且 `log` seam 落点在本模块 · 顺序契约 · 旧家不再吸收 patch）；反探针 **18/18**；纯搬对账 **7/7 逐字节（留下的 3/3 同）**。**★ 两条教训（都是「判据口径」而不是「代码」错，且都是反探针/真跑拓出来的）**：① 写手断言首版沿用 S19 的宽松口径「谁碰到这个名字」，而 `run_one_round` 也**读** `_course_fp` ⇒ 把赋值点改名成 `_course_fp_x` 时守卫**不红**（探针首次 17/18）⇒ 加 `_self_assigns()`（AST 只看 `Assign`/`AnnAssign` 的 target）并断言赋值点集合 == `{"_setup_common"}`；**只数「谁碰到」不够，要把「谁拥有」写成断言**。② 入边首版用 `src.count("self.x(")` ⇒ 新模块头注里那句「`self._maybe_dispatch_baseline_eval(...)` 的解析与搬家前逐字相同」被算成**一条入边** ⇒ 守卫对着**合法的文档**报假红 ⇒ 改成 AST 计真实 `Call` 节点 + AST 求定义面；**入边是语法事实，就该用语法量**。**⚠ 三个坑全是「搬家后按路径读源码的守卫失效」家族（第九/十/十一次）**：`tests/test_batch_eval.py`（写死路径读 `loop_core.py` 找 `maybe_dispatch_batch` ⇒ 改读持有者）· `e2e/test_loop_supervisor_integration.py`（经中间名字 `rl.loop_core.time` 补 `time.sleep` ⇒ 改成直接补 `time` 模块对象）· **dashboard 跨项目盲区**（`kickstart-receipt.test.ts` 写死路径读 python 源码 ⇒ 改成源码树搜定义）。分层快照先红再登记**第五次**（三模块登记进 `RL_ORCHESTRATION`）。**记账校正**：第十九刀公布的 `loop_lifecycle.py` 行数 617 → 672 还差一（真值 **673**）⇒ 连提交消息（`--amend`）带四处文档一次改齐；教训：**可测量值落盘后量一次，别在编辑过程中随手报**。**刻意不动**：`docs/nn/training-stack.md` / `remote-transport.md` 里带日期的历史记录（那时指针正确）。门禁 nn **2497 → 2510 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + **1105 pass / 0 fail**；`check-decisions` ok。决策 → `DECISIONS.md` §2026-09-25-goalnn-loop-core-tail-split；全文 → `engineering.md` §23「第二十刀」；计划 → `plan/nn-training-refactor.md` §5.3.19。
 ㉒ **第十九刀（S4）：主循环骨架 → `rl/loop_lifecycle.py`**——`rl/loop_core.py` **931 → 446 行**；`TrainingLoop` 本体里最后一条方法间调用链（7 方法 / 351 行）连同 7 个**只能随它走**的模块级定义（150 行，闭包实测）搬到 `rl/loop_lifecycle.py::TrainingLifecycle`（**673 行**）。**★ 宿主判据（本刀题眼，可证不是偏好）**：本簇的入边是 `_evalboard_idle`——被 `TrainingRemote`（`loop_remote` ×1）与 `RoundSteps`（`loop_round_steps` ×2）以 `self.` 调；新混入必须同时是两个 caller 的祖先，而 `set(RoundSteps.__mro__) ∩ set(TrainingRemote.__mro__) == {object}` ⇒ **任何 sibling 宿主都不存在**（S17/S18 的「挂到调用者一侧」在此无解），唯一出路 = 组合根。出边同指：`.run` / `.run_one_round` / `._setup` / `.finish_course` 的调用者全是 `TrainingLoop` 实例（`loop_serve` 的 `engine` 是多态 `BcLoop | TrainingLoop`，而 `BcLoop` 自带同名方法，无关）。**代价 = 组合类元组三件套 → 末位追加四件套**（追加末位的依据：7 个成员名在既有混入里零同名定义）⇒ 两处 S17/S18 写的「组合类三件套逐字不变」断言**演进登记**（两处把心不动：本簇不是组合类的直接基类 · `TrainingSteps.__bases__` / `RoundSteps.__bases__` / 各 `__mro__[1]` 逐字不变）。**状态归属不变**：槽位仍全在 `TrainingLoop.__init__`，本混入的 42 名声明块只是**借用**（守卫按**派生集**对账）；`round_failure` 声明为 `Callable[..., RoundOutcome]` 而非 `Any`（`run_one_round` 直接 `return` 它，而方法体要逐字节保持原样 ⇒ 精度放声明里）。**跨模块手三张表**：入边（×1/×2）· 出边（`_drain_pending_eval` / `round_steps` / `round_failure` / `_sync_cloud_halt`）· 槽位写-读手（`_course_fp` / `_corpus_fp`：`_setup_common` 写、`_prepare_iter_dir` / `_rollout_phase` 读）。守卫 `tests/test_loop_lifecycle_split.py`（**13 例**，含宿主判据的机器形式：入边计数 + 祖先交集为空 + `not hasattr(RoundSteps, "_evalboard_idle")`；含 **★ 三条功能性**：出边手归属 · `run_one_round` 真跑通（终态/异常分类/让位响亮报错）· 旧家不再吸收 patch）；反探针 **18/18**；纯搬对账 **14/14 逐字节（留下的 10/10 同）**。**⚠ 三个坑全是「按路径读源码的守卫失效」家族**：`tests/test_batch_eval.py`（写死路径找 `maybe_dispatch_batch` ⇒ 改读持有者 + 钉「恰好一处定义」）· `e2e/test_loop_supervisor_integration.py`（经中间名字 `rl.loop_core.time` 补 `time.sleep` ⇒ 改直接补 `time` 模块对象）· **dashboard 跨项目盲区**（`kickstart-receipt.test.ts` 写死路径找 `KICKSTART_DEFAULT_WARN` ⇒ 改成源码树搜定义 + 同步四处注释）；**锚点教训**：⑧ 原选字面量在 `loop_round_steps.py` 里有两处，`assert count == 1` 当场拦下（锚点写错 ≠ 守卫空档）。分层快照先红再登记**第四次**（`loop_lifecycle` 登记进 `RL_ORCHESTRATION`，理由：它拿三处编排 import）。门禁 nn **2484 → 2497 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + **1105 pass / 0 fail**。决策 → `DECISIONS.md` §2026-09-25-goalnn-loop-lifecycle-chain-split；全文 → `engineering.md` §23「第十九刀」；计划 → `plan/nn-training-refactor.md` §5.3.18。
