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
| [`docs/nn/remote-transport.md`](nn/remote-transport.md) | hub / worker / 云机 / 隧道 / 离线任务包 / 产物回传 / 优先级调度 / wire 账 | 31 |
| [`docs/nn/training-stack.md`](nn/training-stack.md) | 训练循环 · 调度器 · supervisor · 课程编排 · 采样配额 · 门禁与停车 · kickstart | 24 |
| [`docs/nn/experiments.md`](nn/experiments.md) | 课程腿判决 / 探针 / 负结果归档（含人类探针与 BC-ref 判死） | 32 |
| [`docs/nn/engineering.md`](nn/engineering.md) | 测试纪律 · 子进程编码契约 · 门禁耗时 · 账本与 metrics schema · 语料指纹 | 19 |
| [`docs/nn/console.md`](nn/console.md) | dashboard 侧：组件面 / 调度器视图 / 任务包与产物两条腿 / 回显 | 10 |
| [`docs/nn/runtime-opt.md`](nn/runtime-opt.md) | rollout / eval 运行时：native 内核 · 并发口径 · 派发 · 单局看门狗 | 12 |
| [`docs/nn/tpu-perf.md`](nn/tpu-perf.md) | TPU / XLA：设备实测 · 单步耗诊断 · 编译缓存 · PPO 吞吐 | 8 |

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
3. `src/nn/conv/conv-optimize.plan.md`（与重排后的内核同目录）里引的 `docs/nn.progress.md §140–§142`
   **有意未改**：`src/nn/**` 在 node codeHash 集内，改注释会顶掉节点引擎哈希身份（同上面 1 的理由）；
   旧号经附录 A 消解为 `docs/nn/runtime-opt.md` §9–§11。

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
> （TPU ragged tail 收尾）—— 共 5 条，见下表末尾五行。
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
| §49 | **已删除** | R9 远端降级：默认 ABORT + 启动界面 opt-in（T7，2026-09-15；DECISIONS §2026-09-15-goalnn-r9-default-abort） |
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
