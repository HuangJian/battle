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
| [`docs/nn/remote-transport.md`](nn/remote-transport.md) | hub / worker / 云机 / 隧道 / 离线任务包 / 产物回传 / 优先级调度 / **在飞活的备份副本**（§68） / **自主 worker 收工即停（§70）** / **补传腿忙等修复 + 独立进程（§69）** / **一拖一闸两次自锁（§71）** / **派发模型重构：课程无模式 + 接管 + BC 独占（§72）** / **claim 的进度锚以响应时刻为准（§73）** / **补传腿 `_append` 自锁死：重启重述重入不可重入锁（§74）** / **kind=iter 并发 `workers: 0` = 节点自定 + 课程侧 `workers` 死键（§76）** / **重启不重放已消费的 final：判据要与可判定的证据对齐（§77）** | 47 |
| [`docs/nn/training-stack.md`](nn/training-stack.md) | 训练循环 · 调度器 · supervisor · 课程编排 · 采样配额 · 门禁与停车 · kickstart · **节点门的 bootId 一致性**（§26）· **分关采样平衡（§30）** · **决策周期 K 通道（§31）** | 26 |
| [`docs/nn/experiments.md`](nn/experiments.md) | 课程腿判决 / 探针 / 负结果归档（含人类探针与 BC-ref 判死） | 32 |
| [`docs/nn/engineering.md`](nn/engineering.md) | 测试纪律 · 子进程编码契约 · 门禁耗时 · 账本与 metrics schema · 语料指纹 · **共享原语层与分层契约** · **神模块拆分（S4）** · **六包重组（刀 1–6）** · **门禁 fail-fast**（§63） · **测试不得碰在跑的控制台**（§64） · **测试不得写生产状态（门禁意图 / 循环控制 / EvalBoard / 权重归档）**（§65） · **it0 基线 eval「从未派出」缺口：收工自报 + 缺口三分 + 基线自带门**（§66） · **eval 腿任务缺课程血缘：兄弟课程评估局在节点 resultCache 互串**（§67） · **pytest 私有提交 642MB→159MB：BLAS 内线程封顶**（§68） · **scratch 换根档由速度探针决定：通用用例钉死回退档**（§69） · **锁重入守卫扩容：方法内/闭包锁 + 组合组（hub store 混入）+ 全仓审计**（§70） · **测试债清理：口径先于数字（`text_asserts` / 退役单点 / 三条护栏）**（§71） · **pytest 最慢用例第二轮：第三条 HTTP 缝 + 三处全仓扫描的口径内提速**（§72） | 39 |
| [`docs/nn/test-contract-map.md`](nn/test-contract-map.md) | **测试 → 契约归属表**：两个可执行计数口径与其基线（A=1435 形态 / B=1082 生产源码面）· 已填契约（本次动过与 T1–T5 命中文件）· T2b 行为拒绝登记 · T4 本机恒 skip 登记 · 批次 2 工作清单 | 5 |
| [`docs/nn/console.md`](nn/console.md) | dashboard 侧：组件面 / 调度器视图 / 任务包与产物两条腿 / 回显 / 课程管理页 / 指标表抗轮转抄录 / **回放导出按评估轮选（§32）** / **池历史大流按变化重建（§33）** / **空置内存真凶：logTail 整文件读（§34）** / **v2 词表与钮：接管是唯一真源（§35）** / **self 节点低盘告警：预警档与拒收地板是两件事（§36）** | 36 |
| [`docs/nn/runtime-opt.md`](nn/runtime-opt.md) | rollout / eval 运行时：native 内核 · 并发口径 · 派发 · 单局看门狗 · 长驻池（含**同质入口** `serve-any`，TS 侧 + Python 侧两处）· **节点单实例互斥**（§29）· **一局的墙钟上界**（§32）· **长驻池真就绪 + 有界清理**（§33）· **热路径 IO 换节点本地盘（rollout/eval 共用一套 scratch）**（§34） | 33 |
| [`docs/nn/tpu-perf.md`](nn/tpu-perf.md) | TPU / XLA：设备实测 · 单步耗诊断 · 编译缓存 · PPO 吞吐 | 9 |

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
| 2 | **回传腿减重（minimize-payload）**：让 `out` 那 25s 本身变小 | `docs/nn/remote-transport.md` §30 §40 | **主项已收尾**：opt blob 只装 `opt.pt` + 权重走 `init` blob ⇒ 上行 **−35.3%**（`out` 字节本身变小，与 P2.5 的「摘出关键路径」正交且叠加）。残余未决（需用户另裁）：`opt.pt` 的 Adam m/v 降精度、`blob_cache` 上限、回传降频 |
| 3 | 真云机 / 真远程轮次的**绝对值**（`wire.up_sec`、每轮墙钟、TPU 腿 target ~10s 量级） | `docs/nn/remote-transport.md` §7 | 真机轮次读数归档 |
| 21 | **在飞活的备份副本（竞速）的真机判据未取**（代码/单测已齐，见 `docs/nn/remote-transport.md` §68） | `docs/nn/remote-transport.md` §68 | 两台 online worker：A 拿着租约跑时 B 上线 ⇒ B 的日志 5–10s 内出现 `claimed [mode=backup]（无租约…）`；同一 job 两份回传——先到者 `result accepted`、后到者 `lost the race (409…)`；hub `/admin/queue` 该 job 仍是**单一** inflight 行（备份不设租约）；**量一份重复算力的实际占比**（空闲卡数 × 竞速时长）⇒ 若持续 >1 副本且收益不显，再议上限 |

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
| 11 | ~~云机离线 eval 腿仍未入池~~ **已收尾**：池已接入 `run_cloud_eval` + `run_local_eval_game`（每轮一个池、轮末关；上限 `min(slots, 局数)`），实测 **1.19–1.39×**（24/48 局），逐局 `_eval_report.json` **逐字段相同**（`elapsedSec` 除外） | `docs/nn/runtime-opt.md` §22.5 | ✅ 已验：`tests/remote/test_offline_eval_pool.py`（接线 + 执行面，8 用例）+ `tools/perf/bench-eval-pool.py` 真机 A/B |
| 12 | **离线开课补 it0 基线的真机判据未取**（代码/单测已齐，见 `docs/nn/remote-transport.md` §42） | `docs/nn/remote-transport.md` §42 | 点一次离线开课 ⇒ `tmp/<课>/evalA.log` 出现 `[evalA] DONE it0 … games=<关数×每关局数>`；**等第一轮回传落账后**控制台指标表出现 it0 行、配对基线回到 0（不再是「首条 eval 轮」） |
| 13 | **切离线自动出包的真机判据未取**（代码/单测已齐，见 `docs/nn/remote-transport.md` §43） | `docs/nn/remote-transport.md` §43 | 在线课点「切离线」⇒ 回执带「任务包导出已启动」且 `tmp/<课>/task-<课>.zip` 出现；云机日志出现 `hub 找的落点：…`（不再是固定文案）；**包在导出窗口里云机会等到它**（不再等满 1800s） |
| 14 | **云机清单 + 租约的真机判据未取**（代码/单测已齐，见 `docs/nn/remote-transport.md` §44） | `docs/nn/remote-transport.md` §44 | 清空 `battle.offline/<课>/` 后把 `CFG.course` 留空跑一次 cell：日志出现 `hub 清单：N 条` + `领到租约`，两门课按 `ready`+mtime 升序跑完；**第二台**同时跑 ⇒ `已被 … 持有（Ns 后过期）` 且照旧跑完；控制台 `GET /admin/offline` 的 `leases` 能看到 holder |
| 15 | **预取被挤走修复的真机判据未取**（代码/单测已齐，见 `docs/nn/remote-transport.md` §50） | `docs/nn/remote-transport.md` §50 | 重拉云 worker 跑同一双课程 ≥3 个 job：出现 `prefetch …: 命中（…零下载开算）`；关键下载 `排队 … 才拿到单通道` **≤5s**（现状峰值 17.6s）；`preempt=` 与日志里的「挤走」行数对得上；`p0_p95` **≤6s**（劣化 ⇒ `--prefetch-depth 0`） |
| 16 | **抢占作废字节的真机读数未取**（它决定要不要做双端 Range 续传，见 `docs/nn/remote-transport.md` §51 第 3 行） | `docs/nn/remote-transport.md` §51 | 同 scenario 的三个 job 里把每轮 `preempt=N(wasted X.XXMB)` 相加：**Σwasted ≥ 3.4MB（一份 payload）或单次 ≥2MB ⇒ 立项做 Range**；否则不做（先量后裁，门槛已预注册） |
| 22 | **分关采样平衡（`per-stage-v3`）的真机轮次未取**（代码/单测已齐，见 `docs/nn/training-stack.md` §30） | `docs/nn/training-stack.md` §30 | 一次真机 `kind=run` 轮：云机轮报出现 `volumeTopup{rule=per-stage-v3}` 且 `shortfall_by_stage` 为空（或触 `capped_stages`）；本机链那一轮 iteration 事件出现 `volume_alloc_rule` / `volume_stage_stats`；两处都**不得**退化成旧的全关同局数 |
| 23 | **长驻池「假就绪」修复的真机轮次未取**（代码/单测已齐，见 `docs/nn/runtime-opt.md` §33） | `docs/nn/runtime-opt.md` §33 | 下一次云机 rollout 轮看五条：① 池行 `N/M 真就绪（另 K 个仍在冷启动…）`；② 熔断行/轮末 `serve_pool:` 汇总带 `unready=`；③「单局超界（155s）」= 0；④ **逐局重试行**（`单局重试 2/3：…`）能出现（⚠ 轮末 `重试过的局 N 个` **不是**判据）；⑤ 轮级墙钟 / `spawned` 不因分批变差（变差 ⇒ 先撤分批、只留真就绪判据） |
| 24 | **热路径换本地盘的真机轮次未取**（代码/单测已齐，见 `docs/nn/runtime-opt.md` §34 · `plan/rollout-local-scratch.plan.md` §7） | `docs/nn/runtime-opt.md` §34 | 下一轮云机 rollout 看四条：① `产出落点=…（本地盘；可用 X GB｜本轮需 Y MB）`**不得**是「回退原地」（例外：速度闸测出不够快）；② `单局超界` = 0、`整轮重投` 不出现（出现 ⇒ 本地盘也没顶住 ⇒ 上并发自适应的 L1/L2）；③ `搬回=85 局/失败 0 局` 且逐局 `w*/rollout.log` 存在；④ 单局 p50 显著好于事故轮。eval 腿（同一轮）：`[eval-cloud]` 单局 p50/p90 同步改善 + `eval_log.jsonl` 行数 == settled 局数 |
| 25 | **it0 基线「从未派出」缺口的真机轮次未取**（代码/单测已齐，见 `docs/nn/engineering.md` §66 · `plan/eval-baseline-undispatched.plan.md` §6） | `docs/nn/engineering.md` §66 | 下一门**新课**的 it0 看两条：① it0 summary 带 `close_reason`，且 nodes 里出现 `local`（现场表里 it0 恒无它）× `never_dispatched == 0`、`lost == 0` ⇒ P0-4 生效；② 若仍 `close_reason=workers-gone ∧ never_dispatched>0`（缺口靠 P0-1/2/3 被看见）⇒ 本行转 P2：修远端 worker 线程退出原因（判据：同一轮同时出现这两个读数）。另：正常轮 `close_reason` 恒为 `settled`、`dropped` 与历史读数逐字相同 |
| 26 | **eval 腿课程血缘（course_fp）的真机轮次未取**（代码/单测已齐，见 `docs/nn/engineering.md` §67） | `docs/nn/engineering.md` §67 | **重启集群进程加载新码后**（在跑进程 pid 6852 早于本修复，旧码照旧）：① 同进程并发课程的下几轮 eval **不再**出现 `wver mismatch` 型缺局（`tmp/<课>/dist-agent-meta.jsonl` 里该 reason 计数保持 0，此前 2026-10-06 b0 it155、2026-10-08 cm3 it50 各一次）；② 收官 drain / 人工 `evalA` 把 x23-cm3 it50 的缺局补回（该轮 summary 变 `dropped=0`）；③ 若再现，抓一局的 `dist-agent-meta` reason 与 agent 侧 `taskKey` 形状复核（键尾应带 `c<fp16>` 后缀） |

### 3.4 控制台

| # | 事项 | 出处 | 关闭判据 |
|---|---|---|---|
| 9 | 真机「一个 serve 进程同时带 BC + RL」的实跑 | `docs/nn/console.md` §6 | 一次真跑（与 R2e 同口径） |
| 18 | **归属事件体量/保留期未定**：`job_result_accepted`/`job_rejected` 进课程账本（`tmp/<课>/training_log.jsonl`）后随作业数增长；读侧只读尾部 4000 行 | `docs/nn/console.md` §23 · `docs/nn/remote-transport.md` §55 | 真机跑一段后量账本增速与 `job_rejected` 占比 ⇒ 定保留策略（轮转/裁剪）或写明「不裁剪 + 理由」 |
| 19 | **贡献度「按机时加权」仍开放**（当前只计件：局/job；一局 eval 与一局 rollout 机时不同） | `docs/nn/console.md` §23 | 真机对比读数：计件份额与机时加权份额的 worker 排序是否分叉 ⇒ 分叉才做 |
| 20 | **evalBoard 停用期间账本冻结**（缺省 `BCITY_EVALBOARD=0` ⇒ 控制台不读也不入账；唯一入账调用者在视图路径）；持续入账 / 阶段二归档未做 | `docs/nn/console.md` §29 · plan `plan/dashboard-memory-evalboard-off.plan.md` W7′–W9′ | 阶段二落地：归档器 + 回填 CLI（先 ingest 后归档）后，账本行数与 `evalboardComposeCalls()` 对账；开一次 `BCITY_EVALBOARD=1` 验四处复活 |

### 3.5 课程侧待办（可能已被后续条目取代）

- x20-rebirth M2 三选一（kills 最优 it30 / pass 最优 it30 / 末 it96）+ 池外段 —— `docs/nn/experiments.md` §13
- metrics v6 后继 / wChip 生存腿备选 —— `docs/nn/experiments.md` §4
- 「多敌关是否需要真正的朝向信号」尚未验证 —— `docs/nn/experiments.md` §11
- EVAL_SEEDS 扩池 / 正式门口径 / dashboard 必改 —— `docs/nn/training-stack.md` §8
- 控制台开课回执（起点-基线对照行的 dashboard 侧）—— `docs/nn/training-stack.md` §24
- 人类 c20 语料补录（`x20-fill`，到 30 + 到 40 两档）**已收官（2026-09-30）**：pack 99 局 kit 全过（0 error /
  0 warn，校准局 pass）→ 追加 98 席（跳过复打校准局）＋ 补回历史漏导的 2002/414038 ⇒ `human-x20-corpus`
  **160 shards / 101153 决策步 / 分关 40×4**（`demo_bank.npz` 未重建）；③ 同口径复算脚本 = `tmp/human-lane-census.ts`
  （老 61 局逐局 0 失配）⇒ 主基线 FILL n=98：**R 8.851% / R₁₂₀₀ 4.146%** —— `docs/nn/experiments.md` §68 ①③⑦
- **Wave 2（b 腿）剂量轴已到顶**：7.5% 未过绿线（−10.60%）、15% 判零效应 ⇒ 下一步是**换塑形杠杆**。
  选型已出（2026-09-30）：**停轴退场**（H 系列已实付、全链阴性）；唯一新价候选 = 伤害「时间形态」
  （先补列再定价）；lane 第三档 / 危险税 / 卡死税 / 固定开局窗**退场** ——
  `docs/nn/experiments.md` §70 · 决策 `DECISIONS.md` §2026-09-30-goalnn-lever-scan
- **metrics v10 已落地**（差距四族 15 列，`idx54–68`；只加观测、零公式、零训练腿）——「伤害时间形态」
  定价所需的列已备好（`damageBursts`/`maxDamage120`/`damageWhileLow`）—— `docs/nn/experiments.md` §72 ·
  工程侧 `docs/nn/engineering.md` §57 · 决策 `DECISIONS.md` §2026-09-30-goalnn-metrics-v10
- **伤害「时间形态」立项后 E0 判红 ⇒ 家族退场**（不再有「唯一新价候选」）：三形状全不过预注册 K 表；
  机制 = 低血段承伤天花板是剩余 hp（105）+ 致死一击不在 `player_damage` 口径 ⇒ 该列量的是「**跨线**」
  而非「跨线后继续挨打」（中位 0 决策步、π_new 0.10）⇒ 语义退化为总量税的尾部加权。
  开腿前先跑这个 E0 的做法本身是收获 —— `docs/nn/experiments.md` §73 · 预注册 `plan/damage-time-form.plan.md` §5 ·
  决策 `DECISIONS.md` §2026-09-30-goalnn-damage-form-retired
- **`kickstart_burn` 的 `paired` 口径在同网格多臂课上会静默回退 `baseline`**（三臂共享 `paired_rotate_seed`
  ⇒ 对端不唯一）⇒ 需要「显式指定对端」的机制，否则新止损在 Wave 2/b 腿这类课上**形同未装** ——
  `docs/nn/experiments.md` §69 ⑧
- **止损块已随腿入库（2026-10-02 迁移；同日第二刀删净回落读面）**：`kickstart_burn` / `paired_kill`
  从 `rl-config.json`（机器本地、不入库）迁进 `curricula/*.jsonc`（课程块 = 唯一来源）——
  上一条「显式 peer」的机制现在随课程文件走，换机器/新克隆不再静默降级 `baseline`
  （回测假阳性 49.3% → paired 0.47%）；第二刀又删 `legacy_*` 回落读面、迁 h4-hurt/h4-encl 四臂 +
  x20-clutch 两门、清 rl-config 存量 92 项（48 → 4 门，只剩显式 `run` 档）—— 工程侧
  `docs/nn/engineering.md` §59/§60 · 决策 `DECISIONS.md` §2026-10-02-goalnn-burn-rule-in-course-file ·
  §2026-10-02-goalnn-burn-rule-cut2
- **aim-dodge 杠杆 8 列已落地**（`idx66–73`，dim 74；v10 批次内移除 `enclExempt*` 三列；事件扩展 +
  settle-once registry + 回写机制；零训练腿）⇒ E1–E4 仍待在控制台开课 —— 工程侧
  `docs/nn/engineering.md` §58 · 自标定接口 `docs/nn/threat-lane-reward.md` §12 ·
  决策 `DECISIONS.md` §2026-10-01-goalnn-aim-dodge-metrics · 规格 `plan/aim-dodge-levers.plan.md` §11
- **E1（aim 距离加权）三臂已开课（2026-10-02）**：`h4-aim-c0`（对照）+ `h4-aim-k10` / `h4-aim-k25`
  （κ=0.10 / 0.25 ⇒ wAim=0.0205 / 0.0512，开腿前校准冻结）；判据段 = 段A（seed0 864001–864300，
  it30/35/40 × 300 局）；paired 止损**显式 peer**=c0（修掉 Wave 2 的静默 baseline 回退）——
  `docs/nn/experiments.md` §74 · 决策 `DECISIONS.md` §2026-10-02-goalnn-e1-aim-open
- **E2 前置门已跑**（离线 Pearson，b0 探针 160 局）：r(hurtWeight, playerDamageTaken) = 0.533 < 0.8
  ⇒ 不替换 `wDmg`；hurtW/hurtN ≈ 常数（28.97 / CV 0.474）⇒ E2 记档按剂量而非机制 ——
  `docs/nn/engineering.md` §58 验证节；**开腿前现役复算（2026-10-02，b0 600 局，拌入列）**：
  r_within=0.796（95% CI [0.767,0.824]）⇒ 点估计 ≤0.8 仍不替换（CI 跨线已记档）—— `docs/nn/experiments.md` §74
- **E1 段A收官判机制无效 + E2/E3a 设计冻结（2026-10-02）**：段A pooled dE[d|hit] k10 −0.037
  （反号）/ k25 +0.029（< kill 线 0.039），Q 过 ⇒ 无效（不得记剂量结论）；E2（hurt 叠加）
  `h4-hurt-{c0,f75,f150}`（wHurt=0.0205/0.0411，段B 865001，seed 20261003）+ E3a（encl 速率形）
  `h4-encl-{c0,f75,f150}`（wEncl=3.87/7.74，段C 866001，seed 20261004），MDE/kill 线实测冻结
  （5.84/2.92 局；0.0214/0.0107），paired 止损显式 peer —— `docs/nn/experiments.md` §74 ·
  决策 `DECISIONS.md` §2026-10-02-goalnn-e2-e3a-design；**E2 先、E3a 后，人在控制台按序开课**
- **x20-advanced 开腿设计冻结（2026-10-02）**：B 底包 + 新 5 项（比值/速率形，human-gap
  f=7.5%/项：wAcc=6.72 / wDist=0.738 / wHurt=25.3 / wEncl=4.85 / wCorner=9.71），起点 it175，
  target 49152 / epochs 8 / iters 400；终判 kills/局 + 通关率（414000 段 800 局 vs it175），
  无对照无配对 —— `docs/nn/experiments.md` §75 ·
  决策 `DECISIONS.md` §2026-10-02-goalnn-x20-advanced-design；人在控制台开课
- **x20-advanced-plus 设计冻结 + 旧参数复核（2026-10-03）**：新基线 it415（killEV=33.54；
  由 a355 换基：it415 日常新高经 414000 兑现 28.25%/10.465），
  wAcc=15.3 / wDist=0.738 HOLD / wHurt=77.5 / wEncl=8.60 / wCorner=25.0；旧参数按千 tick 率
  复核只动 wShot 0.05→0.06（NN 多开 36% 火；hits/kills/拾取 gap 全是活得短，wDmg×2 有实付代价）——
  `docs/nn/experiments.md` §76 · 决策 `DECISIONS.md` §2026-10-03-goalnn-x20adv-plus-design /
  §2026-10-03-goalnn-x20adv-plus-oldparams / §2026-10-03-goalnn-x20adv-plus-rebase；人在控制台开课
- **三加压腿设计冻结（2026-10-03）**：same-start q90 + kickstart 锚，预算 250 轮，acc/hurt/encl
  各加压一轴（wAcc 15.3→23.0 / wHurt 77.5→110（20% 封顶）/ wEncl 8.6→13.0），yardstick =
  首达线（acc≥0.69 / hurtR≤0.034 / enclR≤0.194）+ 终判 @414000 vs q90 ——
  `docs/nn/experiments.md` §77 ·
  决策 `DECISIONS.md` §2026-10-03-goalnn-x20adv-3legs-design；人在控制台开课
- **三加压腿收官（2026-10-04）**：it250 全跑满，峰全超 q90——acc80（30.38/11.10）+
  **hu150（32.50/11.37，全场最优）** + en225（30.88/11.25），末段全 decay（acc250 崩盘 −8pp）；
  首达线三条全 Miss（线过高 + 口径差，记档）；收敛排名 acc > hurt > encl；下次短腿+早停 ——
  `docs/nn/experiments.md` §79 · 决策 `DECISIONS.md` §2026-10-04-goalnn-x20adv-3legs-verdict
- **下一阶段三腿设计冻结（2026-10-04）**：三最优 vs 人类全指标对照后，从 hu150 同起跑：
  merge-max（23/110/25 并存 + 胜率熔断）/ dist-lastmile（wDist 0.738→1.5）/
   acc-push（wAcc 15.3→30.0）；预算 250（用户撤回短腿）+ 人工盯盘；必报开局诊断；
   终判 @414000 vs hu150 ——
  `docs/nn/experiments.md` §80 ·
  决策 `DECISIONS.md` §2026-10-04-goalnn-x20adv2-design；人在控制台开课
- **x23 commitment 三整形臂终判（2026-10-09）**：it150 四臂同种子全配对——cm0 0.280 /
  cm1 0.2975（+1.75pp ns）/ cm2 0.300（+2.0pp ns）/ cm3 0.345（+6.5pp：2003 单关 spike +
  pool ns，不认）；方向诊断机制全零动（fireHeld 反向 / stop ≈6 tick/局 / stuck 噪声，
  acc 全平）；三臂全否决归档不续跑，cm0 继续 production 续跑 ——
  `docs/nn/experiments.md` §81 · 决策 `DECISIONS.md` §2026-10-09-goalnn-x23cm-verdict
- **冠军易主（2026-10-09）**：KR10-it150 vs hu150 正面对决 1600 对——0.3738 vs 0.3119
  （−6.19pp，p≈0.0003；kills 12.24 vs 11.22）；hu150 的冠军只对 q90 系成立，现役最优改挂 KR10 ——
  `docs/nn/experiments.md` §82 · 决策 `DECISIONS.md` §2026-10-09-goalnn-kr10-dethrones-hu150
- **fore-it335 加冕（2026-10-10）**：拼池 1600 对 0.4875 vs KR10 0.3531（+13.44pp，χ²cc 58.34；
  kills ~13.7–14.0）；stack-it125 0.4344（+8.12pp）确认有效；fore-it195 0.4331（+7.06pp）。
  现役最优改挂 fore-it335 ——
  `docs/nn/experiments.md` §83 · 决策 `DECISIONS.md` §2026-10-10-goalnn-fore-crown
- **三停腿收官（2026-10-03）**：主腿 it592 停，加冕 **a580**（28.75%/10.723；it175 13.50%/7.619
  → 过关翻倍杀敌 +41%）；plus it167 停，加冕 **q90**（29.25%/10.887）；h2 it330 停，
  诊断成功（hurtR −29%）终点落后归档（最优 h285 25.00%/9.963）——
  `docs/nn/experiments.md` §78 · 决策 `DECISIONS.md` §2026-10-03-goalnn-x20legs-verdict

### 3.6 架构重构（`nn-training` 模块重组）

| # | 事项 | 出处 | 关闭判据 |
|---|---|---|---|
| 17 | ~~**`nn-training` 六包重组还剩一刀**~~ **已收官（2026-09-30）**：刀 1 `hub/` 出包 · 刀 2 `common/` 收口（**唯一 L0**）· 刀 3 `worker/` 出包（**L2**，坐在 `remote/` 下面）· 刀 4 `biz/` 出包（**L1**，纯逻辑 **64**，比计划里的 65 少一个——以快照为准）· 刀 5 编排整包改名 `rl/` → **`trainer/`**（37 个模块，成员一个没变，`rl/` 从此不存在；快照 `RL_ORCHESTRATION` → `TRAINER_ORCHESTRATION`）。**刀 6（同日追加，用户口径改判）**：`worker/` = 本地 torch 训练全栈（算法栈五包 39 文件 + 52 个训练侧单体搬入），`biz/` 只留 **12** 个游戏业务模块，`biz/log.py` → `common/log.py`；账本把 `worker/` 整族入账（150 模块）并按拓扑秩重排（12 抬 / 6 压）。判据自始至终是**机械快照**，刀 4 已把 `plan` §3.6 那 28 个误列名的争议按它判完（归 `biz/`） | `docs/nn/engineering.md` §51–§55 · `plan/nn-training-module-reorg.plan.md` §5.5–§5.6 | ✅ 已验：nn 门禁 **3373 passed / 3 skipped**（ruff 只剩预存 N999 + mypy **547** 文件干净）· 根 `bun run check` **2277 / 0** · dashboard typecheck + **1232 / 0** + 三份 bundle · 反探针 `tmp/cut5_probe.sh` **7/7 红（复位即绿）** · 搬家后的**散文残渣四轮清完**（判据 = 名实，末轮扫描面提到仓库根）+ 顺手修掉 **`specs.ts` 的孤儿哨兵**（指着刀 2 就搬走的 `remote/protocol.py` ⇒ 永不触发）与新守卫 · `DECISIONS.md` 每刀一条 + 哨兵一条。**刀 6 追加读数**：nn 门禁 **3373 / 3 skipped**（ruff 全过，117 处 isort `--fix`）· 根 **2277 / 0** · dashboard **1232 / 0** · `bun run build` 过 · 反探针 `tmp/cut6_probe.py` **5/5** · **刀 7（同日，用户指令「把 nn-training 下剩下的 py 移到合适的目录」）**：14 个入口脚本归位（六个训练/评估入口 → `trainer/` · 两个 worker 入口 → `remote/` · `rl_config_schema` → `worker/` · 五个环境/运维脚本 → `tools/`），`nn-training/` 顶层只剩 `conftest.py`；三条真机前提重算（脚本模式 `sys.path[0]` 遮蔽 stdlib `queue` · `Path(__file__)` 上溯层数 · 启动器 `--script` 写相对路径），快照 37 → **43**、账本 +3（L0/L6/L7）；新守卫 `tests/test_entry_scripts_in_place.py`（顶层只许 `conftest.py` + 11 个入口真起一次）；nn **3373 / 3 skipped** · 根 **2277 / 0** · dashboard **1232 / 0** + 三份 bundle · 反探针 **4/4**（逐条红、复位即绿） · **刀 7 收尾（`.md` 散文轮，同日，用户指令「`.md` 散文里还有 42 个文件写着旧扁平路径」）**：40 个非草稿命中文件里 **21 个改 / 147 处路径 + 10 处连带**（`tmp/md_prose_refs.py`，逐文件断言），19 个整文件留（日志/草稿/年代快照/决策表），判据表 → `docs/nn/engineering.md` §55；根 **2277 / 0** · dashboard **1232 / 0** · **散文轮二（同日，用户指令「继续清归档文档里更早层的旧路径残渣」）**：刀 1–6 之前的旧形（`rl/…` · `remote/…` · 算法栈五包 · 根 L0）**20 个文件 / 418 处**（`tmp/older_refs.py`，三道守卫：已在/无解/keep；落盘后复查 WOULD-CHANGE 0）· keep 55（映射左列 /「原 X」/ 事故字面量 / 决策记录）；根 **2277 / 0** |

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
| §146 | docs/nn/runtime-opt.md §23 | 云机「rollout 卡死」三件套：两条 CPU 腿真交替 + 池熔断/背压 + 并发按核数夹取；含 §23.5「本机几核只有一个答案」（TS `tools/lib/cores.ts` / 脚本 / notebook 全铺）（2026-09-25） |
| §147 | docs/nn/remote-transport.md §47 | 归属（role）：job 自己说「该由哪块盘执行」（`manifest.role` 与 kind 同一快照）+ 认领咽喉点两道正交闸（归属 job 级 / 停摆课程级）+ claim 也带角色头 + 取包端点 mode 闸；行为变更：带标盘不再兼领在线活（`DECISIONS §2026-09-25-goalnn-role-routing`）（2026-09-25） |
| §148 | docs/nn/remote-transport.md §48 | 「离线 = 发一份 kind=run 队列项」那条腿退役：离线课由云机取包接手（本机 `ROUND_OFFLINE_EXIT` + 发布咽喉点响亮拒 + worker 拒收 kind=run）；`X-Battle-Offline` 升级为离线盘**报名**（`/offline/*` 面 → `/admin/queue.offline_disk`）（`DECISIONS §2026-09-25-goalnn-offline-leg-retired`）（2026-09-25） |
| §149 | docs/nn/remote-transport.md §49 | 在线 worker 两块盘**不装 bun**（它们不跑 rollout；tailnet / cloudflared 两条隧道各服务一类云机网络环境，都不退役）：`ensure()` 去 bun 分支 + `ensure_bun`/`bun_path`/`BUN_INSTALL_URL` 退役 + `resolve_bun` 拒单改口；bun 只留在自己跑 rollout 的两条链（Kaggle 离线盘 / 采样节点）（`DECISIONS §2026-09-25-goalnn-online-worker-no-bun`）（2026-09-25；初版的「cloudflared 盘退役」已同日撤回） |
| §150 | docs/nn/engineering.md §26 | `common/` 共享原语层：12 组同名实现收敛 + 13 处 `text=True` 编码隐患（2026-09-23） |
| §151 | docs/nn/engineering.md §27 | 断开 `rl` ↔ `remote` 包循环：纯逻辑叶子下沉 L0 + 导出器路径单源化 + 分层守卫（SCC 证实零跨包环）（2026-09-23） |
| §152 | docs/nn/engineering.md §28 | 神模块拆分三连（S4）：① `loop_steps` 传输/发布簇 → `loop_transport`；② 远端 PPO 腿 13 方法/862 行 → `loop_remote`（= `TrainingSteps` 的基类，依赖方向 = 调用者依赖被调用者）；③ `hub_server` 的 admin 控制面 9 方法 → `hub/admin.py`（并摸清「混入类型声明遮蔽类型库」与「随迁名成环」两个坑）；④ 拆 `worker.py` 前先铺**模块级状态契约**安全网（6 处真状态清点 / 防副本 / 行为可复现）；⑤ `worker.py` 的 wire/低速重抽/bulk 节流簇 → `remote/wire.py`（**状态随簇搬迁** = 唯一所有者 + 显式转发；`_WIRE`/`_BULK` 原地可变仍可任入口注入，`_BEST_RATE` 重绑式标量只认 `remote.wire`）；
⑥ HTTP 传输核心（`_request`/`_read_body`/`_get_with_retry`/`_get_opener`/`_sched_headers`/`_warn_non_200` + `BODY_*` + 2 状态）→ `remote/http.py`（keystone：业务簇都站在它上；依赖 `http ← wire ← worker`）；DI seam 随实现迁移，**按调用点命名空间分档**（`_get_with_retry` ⇒ patch `http`；直调 `_request` 的宿主 ⇒ 仍 patch `worker`）；
⑦ 作业工作区/TAR/git 物化（`REPO_ROOT`/`JOB_DIR_KEEP`/`_persist_result`/`prune_job_dirs`/`unpack_opt_tar`/`pack_opt_tar`/`unpack_payload_or_fail`/`_git_head`/`_ensure_commit`）→ `remote/job_fs.py`（BC 拆出的前置：`_persist_result` 非 BC 专属；五刀里唯一 **seam-free**——全仓直接调用无 `setattr`）；顺手发现 `_ensure_commit` 全仓零调用（既存死代码，已登记未删）；
⑧ BC 作业簇（`_bc_*` ×6 + `normalize_ppo_device` + `resolve_bc_seed` + `_run_bc_job`，363 行，本来就是连续一块）→ `remote/bc_job.py`（底座拆完后业务簇可整块搬；同样 seam-free；守卫第一次把「**顶层零 torch**」机械钉住）；门面：e2e 取 `_bc_post_epoch`/`_bc_fetch_resume`/`_run_bc_job`，tests 取 `normalize_ppo_device`/`resolve_bc_seed`/`_bc_device`（2026-09-23）；
⑨ 下载簇（`_progress_logger` + `download_payload`/`download_code`/`download_ts_code`/`download_blob` + `_cache_blob`/`_resolve_blob` + `_ensure_ts_code`，252 行）→ `remote/download.py`（**「注入点分档」第一次双向验证**：`_resolve_blob` → `download_blob` 是组内互调 ⇒ patch 迁 `remote.download`；宿主 `run_job`/`_prefetch_fill` 用裸名字 ⇒ 仍 patch `worker`；`BODY_*` 从 `remote.http` 取单一定义不复制；`_progress_logger` 孪生仍恰好两份）。
⑩ 作业取活/生命周期/回传面（`peek_jobs`/`request_priority`/`claim_job`/`_priority_rank`/`acquire_job`/`job_started`/`job_ready`/`abandon_job`/`job_status`/`start_cancel_watcher`/`post_result`/`release_job`/`worker_tag`/`_failure_detail`/`job_body_error`/`report_job_failure`/`heartbeat`，连续 543 行 / 17 名）→ `remote/job_lifecycle.py`（**seam 最密的一刀**：60+ 处 patch 逐点定档，迁 17 处 `setattr` / 5 个测试文件；两条新判据——① **「引用即接缝」**：判 worker 侧 patch 是否失效要用 AST 的 `Load` 而非 `Call`（`ResultUploader(upload=post_result, …)` 是传值引用，9 处 patch 照旧有效）；② **重绑式标量不做 `is` 恒等断言**（`_opener` 懒建重绑 ⇒ 原先 `test_http_split` 的恒等断言随文件顺序红绿翻转，本刀在 HEAD 上复现并修掉））。`worker.py` 3450 → **1805** 行；
⑪ **收口：`remote/` 内部依赖账本**——前八刀散在六个拆分守卫里的「不得反向 import `remote.worker`」收成 `tests/helpers/remote_dag.py`（`LAYERS` = 35 个模块 8 层的拓扑秩 + `DEFERRED_CYCLES` = 唯一允许的延迟环）+ `tests/test_remote_dag.py`（18 例：账本双向覆盖 / 顶层边严格向下且无环 / 延迟边同样向下 / 全图环恰等于声明值 / **环里不许出现顶层边** / 引导模块顶层零 `remote.*` / 解析不出的边必须为 0 / 合成源码自证活性 / 独立重实现对账）；★ 实测发现 `remote/` 内真有一个环 `run_loop ⇄ worker`（两边均为**有意**的函数内延迟 import，顶层图仍是无环 DAG）⇒ 当时登记而非拆（**同日 ⑫ 已拆掉**，见下） |
⑫ **拆环：半离线执行引擎下沉 `remote/plan_run.py`**——`run_loop ⇄ worker` 那个被登记的延迟环，两条原路线都没照原样走（① 把 `verify_plan_file`/`run_plan_job` 塞进 `job_fs.py` ⇒ 这俩函数的传递闭包**就是整个执行引擎**（≈963 行），塞进 184 行的 I/O 模块 = 造第二个神模块；② 让 `worker.run_job` 从调用方收这两个函数 ⇒ 方向对（注入）但**对象错**：它们只是门面，要注入的是「一轮怎么跑」= `run_job` 自己）；落地：引擎整块 → 新 `remote/plan_run.py`（**1035 行**，L2），`run_loop.py` 只留 CLI / 独立续跑 / 门面（**1451 → 491**），「一轮怎么跑」由调用方注入（`worker` 尾巴 `run_job_fn=run_job`，CLI 侧 `_real_run_job`），**引擎里那个 `_real_run_job` 兜底删掉**（它就是反向 import 的成因）⇒ 纯向下 + 参数注入 = 环消失、`DEFERRED_CYCLES` 清空；★ **选层是算出来的**：用户口径「沉到 L1」但账本是**拓扑秩**（`LAYERS[m] = 1+max(依赖层)`），`plan_run` 依赖最深到 L1 ⇒ 只能是 L2（顺手加成守卫 `test_every_layer_number_equals_its_topological_rank`，36 模块逐条验算）——要真按字面沉 L1，该做的是**把共同依赖再往下拉一层**；seam（第一次为「拆环」而非「拆文件」）：引擎读的模块全局（`iter_spec`/`pairs_for`/…）迁 `remote.plan_run`，且 `run_loop` **不再转发** `iter_spec`（打错模块 = `AttributeError` 响亮而非静默失效），引擎公开名由 `run_loop` `X as X` 门面（取名字可以、patch 无效），实迁 **1 处** `setattr`、其余测试与 `e2e/` 一行不改；新守卫 `tests/remote/test_plan_run_split.py`（**10 例**）+ 反探针**七处全命中**（含 `plan_run` 标 L1 → 3 处红）；账本加两条硬断言（`undeclared == []` 全图零环 / `not DEFERRED_CYCLES` 要网开一面得先改守卫）；门禁 **2349 → 2359**（2026-09-23）；
⑬ **第九刀：拆宿主**——`worker.py` 余下全是宿主（`run_job` 752 / `worker_loop` 364 / `main` 127 / `_prefetch_fill` 69），按**一条真实调用链**切三块（**1814 → 1281**）：① **训练核**（`run_job` 650-1076，427 行：课程上下文/reward_fn → torch+种子 → 模型构建（opt_init 优先）→ opt 内容寻址 → 多卡 → kickstart ref → demo bank → PPO → 产物/result）→ 新 `remote/train_core.py`；② **进程生命周期**（`HOT_RELOAD_EXIT`/`_request_reload`/`supervise_worker`/`_release_cloud_machine`）→ 新 `remote/worker_proc.py`（纯 stdlib）；③ `_wire_block`（两个调用方都要）→ `remote/wire.py`；**先量再下刀**（AST：核的自由变量 = 11 个块外局部 + 9 个形参，输出只有 `result`/`course` 两个，`global` 0，测试 patch 过这一族名字 **0** ⇒ **seam-free**；唯一语义改动 = 核只收 `payload_bytes` 不收 `raw`，漏传交给 ruff `F821` 当场报出）；**选层级联**：核依赖最深到 L3 ⇒ 拓扑秩只能是 **L4**，`worker` 4→5、`run_loop`/`notebook_runtime`/`worker_server` 5→6、`offline_boot`/`push_bootstrap` 6→7、`notebook_boot` 7→8（秩断言 + 分层断言双向护住），`worker_proc` = L0；**源文本守卫迁移**（四个按行文字面找调用点的老守卫：「找不到就红」正是要的响亮）：`test_job_body_crash`（4 处 `job_body_error`）/ `test_tpu_backend_guard.TestWorkerWiring`（xla 接线）/ `test_worker_device`（`torch.device(...)` 实参）/ `test_priority_schedule`（取消回调接线）；新守卫 `tests/remote/test_train_core_split.py`（**14 例**，含 **★ 接口双向一致**：调用点位置实参个数与关键字集合 == 核的形参集合——漏传一个就红，是本刀最可能的失误形态）+ 反探针**七处全命中**；门禁 **2359 → 2374**（2026-09-24） |
⑭ **第十刀：拆宿主之二（seam 最密的一刀）**——`worker_loop` 把轮询壳与「一轮」织在一起，后者（L943-1119，**177 行**）+ `_prefetch_fill`（69 行）+ `_result_settled` 闭包（21 行）下沉新 `remote/job_round.py`（**418 行**，L4 与 `train_core` 同层：靠 L3 业务簇组装一个单元），`worker.py` **1281 → 1042**；**seam 定档（唯一判据：调用点解析在哪个命名空间）**：`run_job` ⇒ **注入**（`run_job_fn=run_job`，它住 L5 不能反向 import；宿主在调用点读**裸名字值** ⇒ 那 10 处 `W.run_job` patch 一行不改 = 上刀「引用即接缝」的第二次兼现），`job_ready`/`abandon_job`/`release_job`/`report_job_failure`/`start_cancel_watcher` ⇒ 迁移（12 处 setattr / 4 文件），`peek_jobs`/`download_payload`/`PREFETCH_ROUND_SEC` ⇒ 迁移（8 处 setattr + 2 处 `W._prefetch_fill` 调用），`uploader` 跨 job 存活 ⇒ 宿主建制并传入，`_prefetch_fill`/`PREFETCH_WIRE_ID`/`settle_result` ⇒ **断言禁留门面**（留 `X as X` 就是个静默空操作的 patch 目标）；`multi`（“有几个 hub”）是宿主概念 ⇒ 宿主算好 `code_cache_dir` 传；宿主账目改为回读 `RoundOutcome{jid, ok, uploaded, stop}`（搬前它们直接改宿主局部 `done += 1` / `_polls_since_accept = 0` / `return done`）；新守卫 `tests/remote/test_job_round_split.py`（**13 例**，含 **★ 接口双向一致**（20 个入参逐名对账）+ **★ 两条功能性**（假 round 验 `uploaded ⇒ done==1` 与 `stop ⇒ 整条退出`；填充器炸弹/记数验档位二））+ 反探针**七处全命中**；顺手修两个**守卫自身的坑**（上游集合改为**从账本推**——写死的带引号字面量会被 `test_subproc_util` 的 spawn marker 误判；两处早先守卫的 `startswith("remote.worker")` 前缀匹配会误伤合法的 `remote.worker_proc`(L0) ⇒ 改按模块名精确比）；门禁 **2374 → 2387**（2026-09-24） |
⑮ **第十一刀：拆宿主之三（`hub_server` 路由面全切完）**——25 个路由方法（13 `_get_*` + 12 `_post_*`，本体 **644 行**）按**域**分四组混入，与第三步的 `AdminRoutes` 并列进 MRO：`hub/schedule.py`（取活/租约/打点·8 方法·153 行·L0）· `hub/result.py`（回传与终局·7·240·**L4**）· `hub/blob.py`（字节服务·5·46·L0）· `hub/offline.py`（离线段·5·205·L0），`hub_server.py` **3728 → 3017**；**层号是算出来的**（第三次兼现）：`_post_result` 走 `push_dispatch.accept_result`（推/拉必须共用同一个校验函数）⇒ `result` 秩只能 **4**，宿主被迫 **4→5**、`smoke_loopback`/`tunnel_ab_probe` **5→6**（其余三组只依赖 `common.protocol` ⇒ 与 `hub.admin` 同层 L0）；**Phase A 纯搬（逐字节等价，只改 `self.` 的解析命名空间）→ Phase B 去重**刻意分两步（混做会让「某条端点变味」分不清是搬错还是收错）；四类形状收成 **5 个助手（实现只住 `hub_server`，混入以 `Any` 声明并消费）**：`_job_or_404(known=)` 14 处 · `_job_body(cap, known=)` 4 · `_lease_token()` 5（`X-Lease-Token`/`lease-token` 双写法）· `_serve_path(p, missing=)` 5（404 带**具体**原因）· `_read_raw_body()` 3（**上限留给调用方**）；**语义保留点**：`known=True` 只给 `/start` `/fail` `/result`，`/ready` **故意** `known=False`（回传就要开始的那份可能还没落 manifest）· `_job_body` **先闸后体**（写错的 URL 不该让服务端白读一段远端体）· 鉴权仍在**没有 job** 的端点内联（计数钉住）；`PRIORITY_BODY_MAX`/`PEEK_MAX` 随迁 `common/protocol.py`（后者有两个读者——`hub.schedule` 的端点与 `_HubQueue.peek_jobs` 的形参默认值——谁也 import 不了谁 ⇒ 必须住协议层）；新守卫 `tests/hub/test_hub_routes_split.py`（**15 例**：定义唯一 + `HubHandler.X is Mixin.X` **对象级**接线 + 助手唯一实现**且各自活着**（≥N 调用点防死助手）+ **★ 漂移警报**（混入里不许再出现那四种内联形状）+ 内联鉴权计数 + 零上向依赖 + 账本层号关系 + **★ 功能性**（`_Probe(hs.HubHandler)` 用 `object.__new__` 起无 socket 实例走完两侧契约——实例上挂 `_json`/`_bytes`/`_auth_ok` 会让 `mypy method-assign` 与 `ruff B010` 互斗，改**真子类重写** + 空 `__init__` 绕开 `BaseHTTPRequestHandler.__init__`））+ 反探针**十一处全命中**；**又一次撞上「读源码文本的守卫」**（写 `layers["hub.server"]` 会被 `test_subproc_util` 当成「起了真服务进程」⇒ 改成按叶子名从账本取，**连解释这件事的 docstring 里也禁那个带引号字面量**——`_code_of` 只剥 `#` 注释、保留 docstring）；门禁 **2387 → 2402**（2026-09-24） |
⑯ **死代码清理 + 第十三刀：物料落地三兄弟下沉 `download` +「不再拆」的明文理由**——先删 `remote/job_fs._ensure_commit`（第六步之一登记的挂账：全仓零调用、只搬未删；留着的真实代价是让「git 物化」看起来有两条路径，一条真跑、一条从未跑过）及其 `__all__` 条目 / `worker.py` 门面 / 守卫清单；再按「重构 `run_job` / `worker_loop` / `main`」侦察，结论是**只有 `run_job` 里还有一整段可搬**（69 行物料落地）——刀口判据不是「哪段长」而是「哪段的**判据同源**」：payload / code / ts_code 三者失败语义同规（sha 不匹配 ⇒ `RetryableError`（传输损坏，重下可修复）；解包失败 ⇒ `ProtocolError`（内容决定性，不重认领）；sha 内容寻址 + tmp 原子改名），谁改一条另两条必然跟改 ⇒ 必须同住一个模块，于是落成**三兄弟** `_ensure_payload` / `_ensure_code` / `_ensure_ts_code`（`worker.py` **1039 → 1033**，`run_job` **350 → 300**；`download.py` **313 → 519**；层号**一处没动**——`download` 仍 L3，新增边 `job_fs`(L1) 本就向下，无级联）；三者各返回 **`NamedTuple`**（`PayloadLanded` / `CodeLanded`）而不是「顺手多返几个目录」：调用方要的 `code_root.parent`(=`ts_code_cache`) 与 `blob_root` 推导写两遍就是两个口径，而搬前这两者在宿主里**是同一个变量名**（`code_cache_dir` 先当共享根、后被改成 per-sha 目录）——参数改叫 `code_cache_root`、返回值里 `code_root` 与 `code_cache_dir` 分列，顺手把这处**同名歧义**也拆了；**两条语义顺序**（碰了就是 bug，写进 docstring）：清场在解包**之前**（解包要面对空目录，否则 D14 血缘校验与训练链读到上一轮脏数据）· prune 在清场**之后**（放末尾 ⇒ 失败轮永远轮转不掉旧目录）；`sys.path.insert` **留**落地（落地与「可 import」是一件事），热替换护栏 `_ACTIVE_CODE_SHA` **留宿主**（那是**进程**的状态）；顺手的第二处：`worker_loop` 两条分支各写一份的存活日志收成 `_alive_log` + `ALIVE_LOG_SEC`（2026-09-11 现场就是**漏了第二处** ⇒ 停机期日志静默被误读成「worker 罢工」；「到点没有」与计数器复位仍留调用方 = 宿主的账）；**★ 本刀把「不拆」也变成决定**（写进 `worker.py` 头部，不靠口口相传）：三个宿主函数不再往下切——理由是它们就是「宿主」这个概念的形状（`worker_loop` 持有**跨 job 存活**的 `uploader`/`pf_stores`/`halt_seen`/轮询计数器，搬走 = 把一个对象的生命周期交给两个模块管；`main` 的 argparse 声明是**数据**，先例 `remote/run_loop.py`；`run_job` 每分支只做一件事，再切只是把直线扯成跳转），余 **287 行 / 100 条 import / 109 个名字**是显式转发门面；**注入点第三档**：组内互调（`_ensure_*` → `download_*`）⇒ patch `remote.download`；预取填充器 ⇒ `remote.job_round`；宿主 `run_job` 自己读的只有三兄弟 ⇒ `remote.worker._ensure_*`——`download_*` 的转发名**还在但已无读者**（tests 把 `worker` 当**取名字的入口**，**名字是契约、位置不是**：对它们 patch `worker` 现在会**静默失效**）；守卫 `test_download_split.py` +3（★ run_job 里**零**下载调用点（AST 级，比「名字在不在」更硬）· ★ **接口双向一致**（13 个形参，漏传/打错名/多传都红）· ★ 功能性（炸弹放 `worker`、真值放本模块 ⇒ 必须走到「sha 不匹配 ⇒ `RetryableError`」且**不写任何文件**））+ `test_job_round_split.py` +1（★ 存活日志恰好两处调用 + `halted` 一真一假的**字面量** + `polling hub` 只由 `_alive_log` 拥有 + 阈值比较恰好两处且都用 `ALIVE_LOG_SEC`）；反探针 **11/11 命中**；门禁 **2402 → 2406**（2026-09-24） |
⑰ **第十四刀：拆状态（`_JobStore` → 六个域混入）**——`hub_server.py` 里 `_JobStore` 独占 **1002 行 / 49 方法 / 19 个状态字段**，而**一把 `_lock` 守着全部**（30/49 个方法取锁）；按用户指令拆状态，**定档结论 = 混入而不是协作对象**（三条实测判据：① 一把锁是类的**不变式**，各持一把锁 = 换并发语义，不满足「零行为变化」；② 跨域互调 **37/49**（`_claim_locked` → `_job_priority_locked` / `_collect_expired_locked` → `_drop_commitment_locked` → `_bump_epoch_locked`）⇒ 混入留在 `self.X` 上 = **零 seam**；③ tests 直读 `store._leases` / `._lease_owners` / `._stale_holders` / `._claimed` / `._backup_authorized` / `._last_heartbeat` / `halt_workers`（20+ 处断言）⇒ 协作对象会让这些**全部改路**）；六个混入 `store_{ledger,wire,scheduling,leases,results,offline}.py`（47 方法与本体 895 行搬走，共 1273 行）⇒ `hub_server` **3017 → 2072**（−31%），组合类只剩 `__init__` / `note_worker` + **进程级状态**（`halt_workers` / `_workers`——多课程单 hub 下 `_HubQueue` 借的就是这一份）；**状态声明分散的对价**：四个有状态的域各一个 `_init_<域>(self)`，组合类 `__init__` 逐个**显式**调用（不用 `super()` 链：顺序要读得出来），`store_results` / `store_offline` 真无常驻状态 ⇒ **不造 `pass` 空钩**（守卫改成正面断言「其方法体里零 `self.X = …` 赋值」）；顺手删掉旧 `_JobStore.__init__` 里那把**会被 `_AuthGuard` 覆盖掉**的 `Lock()`（一个被丢弃的锁对象是下次读的人的陷阱）；随簇搬走两个对外名字（`ClaimOutcome` / `FREEZE_AFTER_RECLAIMS` → `store_leases`，由 `hub_server` 反过来 import 保测试的取名字入口；`_write_bytes` → `store_offline`——它的唯一调用方在那里，留在原处必成环；离线簇另需 `remote.artifacts` ⇒ 该簇 **L1**，其余五个 L0）；**★ 纯搬对账**：AST 逐成员比对 HEAD vs 新家 ⇒ **58 个成员逐字节等价、零差异**，旧 `__init__` 的 21 条赋值 + 43 行字段注释全部逐字在新家（规模到这个量级时「测试绿」不是搬运等价性的尺子）；守卫 `tests/hub/test_hub_job_store_split.py`（**17 例**：定义唯一 + `_JobStore.X is Mixin.X` 对象级接线 + MRO 逐项对账 + 类常量经 MRO 可达 + 状态归属唯一 + 无钩子即零状态 + 钩子各调恰好一次 + 混入**彼此零 import** + `_write_bytes` 只剩一份 + **★ 两条功能性**：跨域链路（发布 → 认领 → 计量）落在同一个对象上 · **持有 `_lock` 时连最「独立」的计量簇也必须阻塞**（同一把锁，不只是同名属性））+ 反探针 **11/11 命中**；门禁 **2406 → 2423**（2026-09-24） |
⑱ **第十五刀：`_HubQueue`（1033 行 / 76 方法）拆七混入 + 两个类搬出宿主**——先侦察用户点名的「28 个同名委托方法」，侦察**推翻了第一直觉**（不是可删的重复、更不能收成 `__getattr__`）：① 签名分三档——28 条逐参数一致、3 条多一个**前置 `course`**（`claimable_job_ids` / `store_offline_artifact` / `store_offline_result`：store **每课程一份**、队列要跨课程寻址）、1 条**改名**（`abandon` → `abandon_job`）；② 缺归属时返回值**逐方法不同**（`False` / `None` / `{}` / `[]` / `0`）⇒ 收 `__getattr__` 得把一张 31 行「空值表」藏进字符串；③ 它是类 docstring 写下的**对外承诺**，`__getattr__` 会让 mypy 看不见、IDE 跳不过去 ⇒ 改成**三张写死闭集表 + 可执行断言**；**★ 侦察的第二个产出：`queue_peer.QueuePeer` 共同声明面**——七混入必须互不 import（本刀要消灭耦合）却各自要调兄弟方法 ⇒ mypy 一片 `attr-defined`；「各造一份 Protocol」七份会漂、「import 兄弟」违反本刀目标 ⇒ **74 个 `def X(...) -> T: ...`（零实现）**，混入继承它：mypy 看声明、运行时由后续混入的活动实现覆盖，配套两条守卫（体里只有 `...` · 与真实现逐参数一致）——⚠ 第一版把「纯声明」写成「body 里只有 `ast.Expr(Constant)`」，**把 `...` 自己也滤掉了**，`halt_of` 带 docstring 才暴露；`_store_of` **刻意不进**（那要 import `hub.store` ⇒ `queue_peer` L3 ⇒ 混入 ≥L4 ⇒ `hub.queue` L5 ⇒ `hub_server` L6 = 与 `smoke_loopback`(L6) 同层）；**A 相（使能）**：`queue_resume` 要按课程构造/注解 `_JobStore` 而 `hub/*` 不得 import `hub_server`（成环）⇒ 先搬两个类——`_AuthGuard` + `_is_loopback` → `hub/auth.py`(101 行)、`_JobStore` 组合类 → `hub/store.py`(108 行)，两处都**自别名 re-export**（`hs._AuthGuard` / `hs._JobStore` / `patch hub.server.*` 全部照旧）；**B 相**：七混入 `queue_{scope,discover,auth,claims,resume,observe,store_face}.py`(15/3/5/9/10/14/18 成员，222/164/68/294/258/199/137 行) + 组合类 `hub/queue.py`(164 行) ⇒ `hub_server.py` **2072 → 887**（**累计 3017 → 887，−71%**）；**拆法判据与第十四刀同源**（一把 `_lock` 是类的不变式 / 跨域互调是常态 / tests 直读私有状态）⇒ 仍是**混入**（同一个对象、同一把锁、零行为变化，**测试一行没改**）；**★ 与第十四刀刻意相反：`__init__` 不拆钩子**——`_JobStore` 那时拆四个 `_init_*`（状态散在 900 行、四域独立），这里只有 44 行且几处**咬合**（`_solo` 决定 `_now`、`_discover_root` 决定 `_discover_last` 初值、`_adopt_solo` 运行期把 `_halt_default` / `_workers` / `_auth_fail` 从 store 搬到 `self`）⇒ 按域切只会把直线扯成跳转，守卫改为正面断言「组合类体只有那两个类常量 + `__init__` 每条状态声明都带值 + 申报字段集**恰好**是状态表的键集（少的那两个由 `_AuthGuard.__init__` 声明——同一个对象的两段 `__init__`）」；**★ 门面的两面对账**：签名一致抓不住「方法还在、活不干了」⇒ 活性判据**不看名字看结构**（体里**恰好一处**「拿到 store 的取用」且转发目标名 == 声明值），取用**四种写法都认**（`_store_of(job_id)` / `_stores[course]` / `_stores.get(course)` / `_solo`）—— 这是**跑出来的**：第一版只认前两种，`note_worker` 与 `claimable_job_ids` 立刻顶出来，`note_worker` 因此被认定为**唯一一条非转发的同名方法**（队列自己就是登记表的拥有者：`_registry()` 单课程取 store 的 `_workers`、多课程取自己的），写成例外表 `FACADE_OWN` + 一条**反面**断言（它必须碰不到 `_store_of` / `_stores` / `_solo`）；**纯搬对账**：AST 逐成员比对 ⇒ **76/76 逐字节等价**，旧 `__init__` 的 16 条字段声明 + 43 行字段注释逐字在新家，七混入**零重名**；守卫 `tests/hub/test_hub_queue_split.py`（**25 例**，含三条功能性：跨域链路落在同一个对象上 · 持有 `_lock` 时**最外层门面也阻塞** · `_adopt_solo` 跨域搬进程状态）；反探针 **14/14 命中**（域成员换家 · MRO 换序 · 门面签名漂 · 转错名字 · 多取一次 store · `note_worker` 变转发 · 混入带值类常量 · `__init__` 冒出未登记字段 · 状态写者漂 · 声明面长出实现 · 声明面收 `_store_of` · 混入偷 import 兄弟 · 层号漂 · 改名转发漂）；**⚠ 操作教训**：反探针**锚点也要断言命中次数**——⑨ 选 `set_halt` 写 `_halts` 时同方法后面还有 `_halts.clear()`（也是写者），⑭ 的 `abandon` 锚点少写第二个实参：两次「没红」都**不是守卫空档而是锚点写错**，脚本现每条 `assert count(old) == 1`；门禁 **2423 → 2449**，mypy **401 → 413**（2026-09-24） |
⑲ **第十六刀：`hub_server`（887 行）收口 —— HTTP 面 / 引导链 / 薄入口三件**——按用户指令「拆 hub_server 剩下的引导链与 HTTP 面，收口 S4」执行 ⇒ `hub/server.py` **887 → 100 行**（**累计 3017 → 100，−97%**），剩余部分只有 **20 行代码**；三件东西两个新家：`hub/http_face.py`（**L5**，575 行：来源判定（`CF_SOURCE_HEADER` / `SEND_*` / `_is_ip_literal` / `attributed_source`）+ `HubHandler`（五组路由混入的组装 + 5 个通用助手））· `hub/boot.py`（**L6**，310 行：`DISCOVER_SCAN_SEC` 等常量 + `as_hub` / `make_server` / `main`）· `hub_server.py`（**L7**，100 行：入口 + **17 条**自别名 re-export + `__all__`）；**为什么两处而非一处**：HTTP 面对**每个请求**负责、引导链对**一次进程启动**负责（读者/生命周期/失败模式：请求级 500 vs 启动即 `exit(1)`），合成一处就得让 argparse 与 `BaseHTTPRequestHandler` 同住；**层号先算后切**（`LAYERS` 是拓扑秩 ⇒ 中间插一层会顺反向边涨上去）：切前用脚本模拟、切后实测复核 ⇒ **级联只有 3 个模块**（`hub_server` 5→7 · `smoke_loopback`/`tunnel_ab_probe` 6→8），顺带修掉账本顶部把 `hub_server` 列在「L4 组装」的**过时散文**（数字有守卫管、散文没人管）；**★ 薄入口的 `__all__` 就是契约**：入口现在**零定义**（无 `def`/`class`，唯一赋值是 `__all__`），守卫**正面断言**这条（比「11 个搬走的成员不在」更硬 —— 它挡的是「顺手在入口补个小函数」，而层号看不出「只长了一个小函数」），配套 `hs.X is 新家.X` **逐条对象恒等** + 17 名**闭集** + 入口不得挂实现用 import；**★ 踩到的真坑**：`SEND_TIMEOUT_SEC` 的唯一读者 `HubHandler._bytes` 读的是**所在模块的全局** ⇒ 搬走后 `setattr("hub.server.SEND_TIMEOUT_SEC", …)` 变成**静默空操作**（名字还在、没人读它；第十三刀「名字 ≠ 注入点」第二次现身，由 `test_body_transfer_guard` 的「对端半开必须超时断开并打印」真跑时当场报出），守卫两条机械化：`_bytes` 里必须是**裸 `Name`** + 全仓**恰好一处** `setattr` 且必须写在**实现所在模块**上（AST 判据，不用逐行找子串——第一版正是被自己 docstring 里那段「这个坑长什么样」的原文判红）；**⚠ 搬走代码会静默废掉四条读源码的守卫**（本仓第三/四/五次撞上）：`_class_methods(HUB_SERVER, "HubHandler")` → `StopIteration`（改读 http_face）· `test_hub_admin_split` 的「名字不在 hub_server 里」变成**恒真空话**（改成「不在入口也不在 http_face」）· `test_jobs_next_retired::_PROD_FILES` 扫一个只剩 re-export 的**空壳** ⇒ 有人把 `/jobs/next` 加回路由表**不会被发现**（把 http_face 加进扫描集）· 本守卫自己的 docstring 被 `test_subproc_util` 的 spawn marker 命中（改用 `hs.__name__` 取名字）⇒ **搬文件时必须一条条问「谁会按路径读它」**；**★ 顺手修掉一个跨项目静默回归（第十四刀留下、HEAD 上已经红）**：dashboard 的镜像常量守卫 `poison-unfreeze.test.ts` 按**写死路径**读 `hub_server.py` 找 `FREEZE_AFTER_RECLAIMS`，而该常量第十四刀已搬到 `hub/store_leases.py` ⇒ 用例当场失败，但**没有任何门禁会发现**（nn 侧 pre-commit 不跑 dashboard 测试、dashboard 侧只在动过 `dashboard/**` 时才跑）——修法不是换一个写死路径（下次再断），而是**在 python 源码树里搜定义**（搬家不再红、常量真消失才红）；同族盲区：dashboard 监督器哨兵 `pySentinels(HUB_SERVER_ENTRY)` 只盯入口那**一个文件** ⇒ **改 `hub/http_face.py` 不触发重启**（第十一刀起就这样，`hub/schedule.py` 等一直不在哨兵里）⇒ 修成 `hubImplementationFiles()` 枚举 `hub/*.py`，并加一条 dashboard 守卫钉住「哨兵覆盖全部实现文件」；**纯搬对账**：AST 逐成员 ⇒ **11/11 逐字节等价**（含 411 行 `HubHandler` + 202 行 `main`），旧 body **零残余**，docstring 59 行**只改首行**；守卫 `tests/hub/test_hub_entry_split.py`（**13 例**，含两条功能性：从门面拿 `make_server` 真起 127.0.0.1 服务打通 `/ping` 且错 token 401 · `as_hub` 幂等）；反探针 **12/12 命中**；nn 门禁 **2449 → 2462**，mypy **413 → 415**，根 `bun run check` 2120 pass / 0 fail，dashboard **1105 pass / 0 fail** + typecheck 绿（2026-09-24） | ⑳ **第十七刀：`TrainingSteps` 的 in-loop 评估链 → `trainer/loop_eval.py`（S4 收口后 `rl/` 侧继续）**——按用户指令「按同一条『真实调用链』手法拆 `TrainingSteps` 本体」执行 ⇒ `trainer/loop_steps.py` **940 → 666 行**，新模块 `trainer/loop_eval.py::TrainingEval`（**375 行 / 8 成员 / 5 状态槽**）。**刀口先量后定**：20 个方法里只有 **7 个互相调用**且连成一条链（派发 → 尾巴收拢 → PPO 收官 join/交棒 → 收官 drain），其余 13 个都是被轮内步骤各自调用的**叶子**；外部入口只有 3 个（轮内 2 + 收官 1）⇒ 方向 = 调用者依赖被调用者。**切法仍是混入**（同一对象、零行为变化，测试一行不改），但**基类元组是追加**：`class TrainingSteps(TrainingRemote, TrainingEval)` —— 2026-09-23 写下的 `__mro__[1] is TrainingRemote` 与四个「继承真混入」的宿主逐字仍成立。**★ 与第十四/十五刀最大的不同：搬走的全是方法与槽位、模块级名字为零 ⇒ 没有任何 patch 点需要迁移**（对 `trainer.loop_steps.*` 的注入本来就是空操作）。**★ 状态归属唯一**：五槽位声明只在 `TrainingEval`；旧类里剩两处**跨模块手**（`_log_report` 写 `_eval_thread`、`_record_iteration` 读 `_eval_join_sec`）**经继承**解析，被写成闭集表（第三条手即红），并有**功能性**守卫真跑这条交棒链（写者住旧模块、读者住新模块、尾巴落在同一实例）。守卫 `tests/trainer/test_loop_eval_split.py` **11 例**（成员闭集 / 对象恒等 / 基类元组 / 五槽位单处声明 / 跨模块手闭集 / 顶层 import 闭集 + DI 只许延迟 / 不得反向 import / 跨模块交棒 / 占位响亮失败），反探针 **14/14**；纯搬对账 **8/8 逐字节**（1 处**申报**差异：占位 raise 文案点名新家）+ 5/5 槽位 + 旧类零残余。**⑥ 次撞上「按路径读源码的守卫」**：`tests/trainer/test_eval_a_once.py` 断言 loop_steps 源码里含 `eval_dispatch import dispatch_eval_bg` ⇒ 随搬家红（这次是响亮失败），修法升级为**在 `rl/` 树里找谁持有这个名字** + 「拿到它的模块里住着 `_dispatch_delayed_eval`」；另**`ruff format --check` 不是门禁**（HEAD 上本来就有两处不格式，别顺手 format 把 diff 弄脏）。nn 门禁 **2462 → 2473 passed / 3 skipped**；mypy **418** 源文件；根 `bun run check` 2119 pass（1 例 `dist-node-gate` 计时 flake，单独复跑 3/3 绿）。决策 → `DECISIONS.md` §2026-09-24-goalnn-loop-eval-chain-split；全文 → `engineering.md` §28「第十七刀」；计划 → `plan/nn-training-refactor.md` §5.3.16。
㉑ **第十八刀：`TrainingLoop` 的动态采集链（9 成员 / 445 行）→ `trainer/loop_volume.py`（`rl/` 侧继续）**——按用户指令「全程自主，继续按同一手法拆 `rl/` 侧神模块」执行 ⇒ `trainer/loop_core.py` **1386 → 931 行（−33%）**（搬走 462 行 = 445 方法 + 9 行分节注释，留下 8 行指路注释），新模块 `trainer/loop_volume.py::TrainingVolume`（**573 行**）。**先量后定且把量法工具化**（新写 `tests/` 外的侦察器：类内调用图 + 连通分量 + 每方法读的模块全局 + 写槽，一次输出）：`TrainingLoop`（25 方法 / 1089 行）有 **3 条链**（volume **9** · 生命周期 7 · 基线 2）⇒ 取最大且最内聚的 volume（且恰是旧类的**尾块**，连续 462 行纯搬）；同一把尺跑了另外两份候选：`trainer/batch_eval.py`（**1785 行，全仓最大**）的 35 个顶层函数是**一个 28 节点巨团**（`consume_requests ↔ claim_pending ↔ read/write_batches ↔ mark_unit_done ↔ _requeue ↔ _persist_of ↔ maybe_dispatch_batch ↔ units_for_batch → plan_units/plan_verdict_units`）⇒ **按链切不动**（要拆得先造「批存储」接口 = 真设计改动），`trainer/bc_loop.py` 的 9 方法分量次之。**方向 = 调用者依赖被调用者**（与第二步 / 第十七刀同源）：本簇生产入口**全部**在 `RoundSteps`（`step_course_iter` → `_iteration_pairs`；`step_rollout` → `_volume_active` / `_volume_collect_continuous`）⇒ **`class RoundSteps(TrainingVolume)`**；**刻意不走「给 `TrainingLoop` 加基类」**（那要改组合类 + 四个「继承真混入」的宿主，并让第十七刀守卫的「组合类三件套不变」失守）⇒ `TrainingLoop.__bases__` 与**全部既有守卫一行不改**。**★ 本刀唯一要迁的 patch 目标 = `log`**：`_volume_topup` 的日志（「未达标」/`wave_cap`）按模块全局解析 ⇒ `e2e/test_volume_e2e.py` 里 `setattr(trainer.loop_core, "log", …)` 搬后成**静默空操作**（第十六刀 `SEND_TIMEOUT_SEC` 的同族形状，本仓第三次），已改到 `trainer.loop_volume.log`；`common.distribution` 在两个方法内各有一份重复 import ⇒ **删顶层那份**（而非方法内）——唯一能保持「方法体逐字节不变」的改法。**★ 两个坑都是「散文会撒谎」**：① 侦察初稿把方向写成「外部入口全在 `RoundSteps`（`step_rollout` / `step_volume_topup`）」——而 `step_volume_topup` 是 **VOLUME_RULE_V2 后的退役空步**（`return None`），`_volume_topup` 生产**零调用点**（只由 e2e/单测以 unbound 形式驱动）⇒ 被守卫里「RoundSteps 真在调它」当场顶出，四处散文改对并把「退役空步」本身写成守卫；② 分层快照按设计**先红再登记（第三次）**：`loop_volume → trainer.rollout_phase` 使它成为「经 rl 传递可达 remote」的一员 ⇒ 登记进 `RL_ORCHESTRATION`（并写明它**自己不经 remote**，与既有两条理由不同）。**守卫** `tests/trainer/test_loop_volume_split.py` **11 例**（成员闭集 / `TrainingLoop.X is TrainingVolume.X` 对象恒等（既有用例正是 unbound 绑定）/ `RoundSteps.__bases__` + 组合类三件套 + **判定 MRO 逐项** / 七槽位声明在新家且 `__init__` 仍全部赋值 / **跨模块手闭集**（`__init__` Store×7 + `_record_iteration` Load×3，量出来的）/ 顶层 import 闭集 / `biz.volume_waves`·`biz.volume_quota` 只许延迟 / 不得反向 import / **两条功能性**：`log` seam 在本模块（打 `trainer.loop_core.log` 一个字节都收不到）· unbound 经 MRO 取真实现），反探针 **14/14**；**纯搬对账 9/9 逐字节、零申报差异**（本刀连文案都不用改）+ 旧类零残余 + 新家成员闭集。**顺手同步的 provenance**：`trainer/__init__.py` · `README.md` 模块表 · `loop_core.py`（模块 docstring + MRO docstring + 旧位置留**指路注释**）· `loop_round_steps.py`（docstring + 基类 + 删四条已被继承取代的 `Any` 声明）· `volume_waves.py`（5 处模块路径）· `loop_steps.py`（`_volume_stages` 的「定义在 `TrainingLoop` 上」改写）· `tests/test_layering.py`（快照 + 理由）。**刻意不动（写明理由）**：`curricula/x1-rebirth.jsonc` 与 `x3-power.jsonc` 里两处注释仍写旧路径——课程文件**字节**就是 `course_fp` 血缘，为一句注释改字节会让在飞腿的课程身份漂移；`DECISIONS.md` / `plan/dynamic-rollout-volume.plan.md` 的旧路径是历史记录，不改写。nn 门禁 **2473 → 2484 passed / 3 skipped**；ruff `All checks passed`；mypy **420** 源文件；根 `bun run check` 2120 pass / 0 fail；**零 `dashboard/**` 改动**（故不触发其门禁）。决策 → `DECISIONS.md` §2026-09-25-goalnn-loop-volume-chain-split；全文 → `engineering.md` §28「第十八刀」；计划 → `plan/nn-training-refactor.md` §5.3.17。

 ㉓ **第二十刀（S4）：组合根收尾 —— 三簇叶子出组合类 ⇒ `TrainingLoop` 成为纯组合类**——按用户指令「拆 `loop_core` 剩下的基线评估链（2 成员）与叶子方法，让组合根成为纯组合类」执行 ⇒ `trainer/loop_core.py` **446 → 240 行**；余下的 7 个叶子按**判据同源**分三簇各自成模块（合计 355 行 = 128 + 96 + 131）：`trainer/loop_baseline.py::TrainingBaseline`（`_baseline_eval_weights` / `_maybe_dispatch_baseline_eval`，判据「日志里有没有这份 bc 权重的 it0 干净评估」）· `trainer/loop_iter_dir.py::TrainingIterDir`（`_check_quota_incident` / `_prepare_iter_dir`，判据「`self._traj_dir` 里有没有本轮的活」）· `trainer/loop_dispatch.py::TrainingDispatch`（`_rollout_phase` / `_eval_on_round` / `_evalboard_yield`，判据「本轮把活派给谁 / 让位给谁」）。组合根余下**只有** `__init__` + `_run_inspect`。**宿主：三簇都挂 `RoundSteps` 一侧，`TrainingLoop.__bases__` 一行不改**——mixin 级父调用者全部是 `RoundSteps`；`_eval_on_round` 另有 `TrainingEval` / `TrainingGuards` 两个 sibling 调用者，但三者互不继承**不构成**「必须挂组合根」的理由（`RoundSteps` 是组合根的第一个基类，挂它的基类里已在 `TrainingLoop` 的线性化上；与第十九刀的区别：那刀两个 caller 都是组合根的基类且交集为空）。**★ 唯一真约束 = `_eval_on_round` 的顺序契约**：`trainer/loop_eval.py` 的同名成员是**占位**（body `raise`），真实现必须在 MRO 里更靠前，否则占位胜出 ⇒ 静默返回 falsy 把 eval 全关掉；挂 `RoundSteps` 一侧天然满足，改成组合根**末位**会反过来胜出（反探针 ⑤）。**结构性例外（「纯组合类」在本仓的精确含义）**：`_run_inspect` + 模块级 `run_inspect` 必须同住组合根（`_run_inspect` 按**模块全局**解析它——文档化可替换点，`trainer/loop.py` 再导出；而那个模块不能 import `trainer.loop_core` 当基类 ⇒ 成环）⇒ 守卫 `test_composition_root_keeps_only_the_structural_pair` 正面钉住闭集 `{__init__, _run_inspect}`。**三张跨模块手表**：入边闭集（`_check_quota_incident` / `_prepare_iter_dir` / `_rollout_phase` / `_evalboard_yield` / `_maybe_dispatch_baseline_eval` 各 1 · `_eval_on_round` 3）· **出边为空**（三簇互不调兄弟方法）· 槽位读者闭集 + **写手唯一**（`_course_fp` / `_corpus_fp` **只在** `loop_lifecycle._setup_common` 被**赋值**，本刀三簇只读）。守卫 `tests/trainer/test_loop_core_tail_split.py`（**13 例**，含**全量 MRO 名单与 `RoundSteps.__bases__` 的唯一所有权**——S18/S19 那两处改成只钉相对位置，避免同一份名单三处各自漂；含 **★ 四条功能性**：`_check_quota_incident` 真跑且 `log` seam 落点在本模块 · 顺序契约 · 旧家不再吸收 patch）；反探针 **18/18**；纯搬对账 **7/7 逐字节（留下的 3/3 同）**。**★ 两条教训（都是「判据口径」而不是「代码」错，且都是反探针/真跑拓出来的）**：① 写手断言首版沿用 S19 的宽松口径「谁碰到这个名字」，而 `run_one_round` 也**读** `_course_fp` ⇒ 把赋值点改名成 `_course_fp_x` 时守卫**不红**（探针首次 17/18）⇒ 加 `_self_assigns()`（AST 只看 `Assign`/`AnnAssign` 的 target）并断言赋值点集合 == `{"_setup_common"}`；**只数「谁碰到」不够，要把「谁拥有」写成断言**。② 入边首版用 `src.count("self.x(")` ⇒ 新模块头注里那句「`self._maybe_dispatch_baseline_eval(...)` 的解析与搬家前逐字相同」被算成**一条入边** ⇒ 守卫对着**合法的文档**报假红 ⇒ 改成 AST 计真实 `Call` 节点 + AST 求定义面；**入边是语法事实，就该用语法量**。**⚠ 三个坑全是「搬家后按路径读源码的守卫失效」家族（第九/十/十一次）**：`tests/trainer/test_batch_eval.py`（写死路径读 `loop_core.py` 找 `maybe_dispatch_batch` ⇒ 改读持有者）· `e2e/test_loop_supervisor_integration.py`（经中间名字 `trainer.loop_core.time` 补 `time.sleep` ⇒ 改成直接补 `time` 模块对象）· **dashboard 跨项目盲区**（`kickstart-receipt.test.ts` 写死路径读 python 源码 ⇒ 改成源码树搜定义）。分层快照先红再登记**第五次**（三模块登记进 `RL_ORCHESTRATION`）。**记账校正**：第十九刀公布的 `loop_lifecycle.py` 行数 617 → 672 还差一（真值 **673**）⇒ 连提交消息（`--amend`）带四处文档一次改齐；教训：**可测量值落盘后量一次，别在编辑过程中随手报**。**刻意不动**：`docs/nn/training-stack.md` / `remote-transport.md` 里带日期的历史记录（那时指针正确）。门禁 nn **2497 → 2510 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + **1105 pass / 0 fail**；`check-decisions` ok。决策 → `DECISIONS.md` §2026-09-25-goalnn-loop-core-tail-split；全文 → `engineering.md` §28「第二十刀」；计划 → `plan/nn-training-refactor.md` §5.3.19。
 ㉒ **第十九刀（S4）：主循环骨架 → `trainer/loop_lifecycle.py`**——`trainer/loop_core.py` **931 → 446 行**；`TrainingLoop` 本体里最后一条方法间调用链（7 方法 / 351 行）连同 7 个**只能随它走**的模块级定义（150 行，闭包实测）搬到 `trainer/loop_lifecycle.py::TrainingLifecycle`（**673 行**）。**★ 宿主判据（本刀题眼，可证不是偏好）**：本簇的入边是 `_evalboard_idle`——被 `TrainingRemote`（`loop_remote` ×1）与 `RoundSteps`（`loop_round_steps` ×2）以 `self.` 调；新混入必须同时是两个 caller 的祖先，而 `set(RoundSteps.__mro__) ∩ set(TrainingRemote.__mro__) == {object}` ⇒ **任何 sibling 宿主都不存在**（S17/S18 的「挂到调用者一侧」在此无解），唯一出路 = 组合根。出边同指：`.run` / `.run_one_round` / `._setup` / `.finish_course` 的调用者全是 `TrainingLoop` 实例（`loop_serve` 的 `engine` 是多态 `BcLoop | TrainingLoop`，而 `BcLoop` 自带同名方法，无关）。**代价 = 组合类元组三件套 → 末位追加四件套**（追加末位的依据：7 个成员名在既有混入里零同名定义）⇒ 两处 S17/S18 写的「组合类三件套逐字不变」断言**演进登记**（两处把心不动：本簇不是组合类的直接基类 · `TrainingSteps.__bases__` / `RoundSteps.__bases__` / 各 `__mro__[1]` 逐字不变）。**状态归属不变**：槽位仍全在 `TrainingLoop.__init__`，本混入的 42 名声明块只是**借用**（守卫按**派生集**对账）；`round_failure` 声明为 `Callable[..., RoundOutcome]` 而非 `Any`（`run_one_round` 直接 `return` 它，而方法体要逐字节保持原样 ⇒ 精度放声明里）。**跨模块手三张表**：入边（×1/×2）· 出边（`_drain_pending_eval` / `round_steps` / `round_failure` / `_sync_cloud_halt`）· 槽位写-读手（`_course_fp` / `_corpus_fp`：`_setup_common` 写、`_prepare_iter_dir` / `_rollout_phase` 读）。守卫 `tests/trainer/test_loop_lifecycle_split.py`（**13 例**，含宿主判据的机器形式：入边计数 + 祖先交集为空 + `not hasattr(RoundSteps, "_evalboard_idle")`；含 **★ 三条功能性**：出边手归属 · `run_one_round` 真跑通（终态/异常分类/让位响亮报错）· 旧家不再吸收 patch）；反探针 **18/18**；纯搬对账 **14/14 逐字节（留下的 10/10 同）**。**⚠ 三个坑全是「按路径读源码的守卫失效」家族**：`tests/trainer/test_batch_eval.py`（写死路径找 `maybe_dispatch_batch` ⇒ 改读持有者 + 钉「恰好一处定义」）· `e2e/test_loop_supervisor_integration.py`（经中间名字 `trainer.loop_core.time` 补 `time.sleep` ⇒ 改直接补 `time` 模块对象）· **dashboard 跨项目盲区**（`kickstart-receipt.test.ts` 写死路径找 `KICKSTART_DEFAULT_WARN` ⇒ 改成源码树搜定义 + 同步四处注释）；**锚点教训**：⑧ 原选字面量在 `loop_round_steps.py` 里有两处，`assert count == 1` 当场拦下（锚点写错 ≠ 守卫空档）。分层快照先红再登记**第四次**（`loop_lifecycle` 登记进 `RL_ORCHESTRATION`，理由：它拿三处编排 import）。门禁 nn **2484 → 2497 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 pass / 0 fail；dashboard typecheck + **1105 pass / 0 fail**。决策 → `DECISIONS.md` §2026-09-25-goalnn-loop-lifecycle-chain-split；全文 → `engineering.md` §28「第十九刀」；计划 → `plan/nn-training-refactor.md` §5.3.18。
 ㉔ **第二十一刀（S4）：`TrainingSteps` 的产物出包簇 → `trainer/loop_export.py`**——`trainer/loop_steps.py` **667 → 541 行**，4 成员（`_ensure_ts_code` / `_volume_plan_block` / `_export_offline_bundle` / `_export_weights`）→ 新 `trainer/loop_export.py::TrainingExport`（**210 行**）。**刀口：先答「有没有链可牵」，没有就按判据同源取簇**——类内调用图**只有唯一一条方法间调用链**（`_export_offline_bundle` → `_volume_plan_block`），其余 **8 个是叶子**（零互调）⇒ 改按判据「产物打包 / 打指纹 / 缓存 TS 码」取**出包家族**。**宿主 = 末位追加**（`class TrainingSteps(TrainingRemote, TrainingEval, TrainingExport)`；`__mro__[1]` 仍 `TrainingRemote`；`TrainingLoop.__bases__` 一行不改），依据 = **实测遮罩面**（4 成员名在既有混入零同名 `def`）。**patch 面零迁移**（`trainer.loop_steps` 只 patch 过 `log`，测的是**留守**的 `_write_iter_stats`），但 `log` 在新模块解析 ⇒ 守卫钉 seam 落点。**★ 真实发现**：`_export_offline_bundle` 的「无起点权重」分支**走不到**——`weights_fingerprint` → `sha256_file` 对不存在文件**响亮抛 `FileNotFoundError`**（不是 falsy）⇒ 功能性用例改测 `iters<=0` 门。**坑**：dashboard 跨项目盲区（第十六/十九/二十刀同族）—— `course-lifecycle.ts` 两处注释把 `--run-iters<0` 守卫指到 `loop_steps.py`，而守卫在 HEAD 上**早已**住 `trainer/loop_remote.py` ⇒ 改成 `trainer/loop_remote.py`（注释-only）。守卫 `tests/trainer/test_loop_export_split.py`（14 例）+ 反探针 **19/19**；纯搬对账 **4/4 逐字节**（留下的 8/8 同）；分层快照先红再登记**第六次**。门禁 nn **2510 → 2524 passed / 3 skipped**；根 `bun run check` 2120 / 0；dashboard **1105 / 0**。
 ㉕ **第二十二刀（S4）：`loop_remote` 的 862 行连通分量按判据同源切四簇**——`trainer/loop_remote.py` **997 → 35 行**（−96%），13 方法（862 行，**一条连通分量**）→ 四个混入（方法体 69 / 470 / 76 / 247 行）：`trainer/loop_remote_push.py::TrainingRemotePush`（**把 job 送到节点**：提交/首发/取回）· `loop_remote_job.py::TrainingRemoteJob`（**一个 job 的四步** + 组合入口）· `loop_remote_fail.py::TrainingRemoteFail`（**失败唯一处置策略**：确定性停腿 / 连败配额）· `loop_remote_drive.py::TrainingRemoteDrive`（**谁驱动**：轮内三相 / 整轮 / 半离线段）。**刀口 = 没有链可牵就按判据同源**（S19/S20 的链取法在此无解——13 节点本就连成一个连通分量）。**宿主 = 把 DAG 写进类声明**：`Push ← Job ← {Fail, Job} ← Drive ← TrainingRemote`（组合根仍在原文件、**零方法**）⇒ `TrainingSteps.__bases__` / `TrainingLoop.__bases__` / 测试宿主 / 既有 import 调用点**一行不改**。**patch 面**：`_push_submit`/`_push_wait_result` → `loop_remote_push`（e2e 两处改址）、`common.distribution` → `loop_remote_drive`、`log` → 每簇；patch 旧的 `trainer.loop_remote.*` 现在是**静默空操作**。守卫演进 6 处 + 新守卫 `tests/trainer/test_loop_remote_split.py`（15 例，含三条功能性）+ 反探针 **24/24 全红**；纯搬对账 **13/13 逐字节**；分层快照先红再登记**第七次**。**★ 新形态坑**：`test_loop_volume_split` 按旧文件类名枚举方法——类体切空后**静默读到空集也算过**（与「按路径读源码响亮失败」不同）⇒ 搬家还要问「谁会静静地读到空」。**dashboard 跨项目盲区（同族）又一次**：`--run-iters<0` 守卫随 `_remote_run_segment` 迁到 `loop_remote_drive.py`（S21 才刚指到 `loop_remote.py`）⇒ 同步改址。门禁 nn **2524 → 2539 passed / 3 skipped**；根 `bun run check` 2120 / 0；dashboard **1105 / 0**。
 ㉖ **第二十三刀（S4）：`loop_guards` 的 13 成员按判据同源切四簇（多 sink 的 DAG）**——`trainer/loop_guards.py` **785 → 194 行**（−75%），13 方法 → 四簇（177 / 200 / 287 / 78 行）：`biz/loop_guards_trip.py::TrainingGuardsTrip`（`_breaker` · `_stop_loss`；判据 = **过程面**硬边界——更新健康度 / 评估显著度，**连击式**）· `loop_guards_leg.py::TrainingGuardsLeg`（`_kickstart_burn` · `_paired_kill`；判据 = **结果面**停腿——退回了吗 / 比对照臂差吗，读趋势 + **停云机**）· `loop_guards_gate.py::TrainingGuardsGate`（`_gate` · `_apply_verdict` · `_warn_min_train_unreachable` · `_budget_hard_cut`；判据 = **课程结束门一整族**）· `loop_guards_sweep.py::TrainingGuardsSweep`（`_rotate_cleanup`；判据 = **轮级磁盘回收**）。**刀口：先选文件，再选切法**——`loop_steps.py` 余下 8 叶**被否决**（类内零互调 + 判据彼此无关：读盘/配额/落账/取证/journal ⇒ 拆它只能**按大小**）；`loop_guards.py` 的调用图是**多 sink 的 DAG**（既非 S22 的连通分量、也非 S19/S20 的单链）：`_ledger_apply` ← **3 簇**、`_sync_cloud_halt` ← **2 簇 + 外部** `loop_lifecycle.finish_course`。**宿主 = 留宿主**（提供者留根、调用者出包），依据 S19 已记录的规则「**入边来自多个 sibling ⇒ 锁进组合根**」⇒ 四个共享 sink + 三个判决词表常量留根；四簇零互调 ⇒ 元组顺序惰性；`TrainingLoop.__bases__` / `TrainingSteps.__bases__` / 既有 import 与测试宿主**一行不改**。**★ 本刀与前四刀最大的差别 = patch 面零迁移**：留根的两人正是 **patch 锚点**——`set_cloud_halt` / `common.distribution` 被**四个测试文件 5 处**以 `monkeypatch.setattr("trainer.loop_guards.…", raising=True)` 打桩（`test_paired_kill` · `test_loop_gate_nopark` · `test_loop_gate_soft_remediate` · `test_kickstart_plan` · `test_loop_park`），而唯一真调用点 `_sync_cloud_halt` 留根 ⇒ 那些文件一行不改；「**谁是 patch 锚点**」因此升级为**宿主定档的硬判据**（新守卫正面钉锚点必须在根、四簇不得持有 + 一条功能性证明「按根打桩真能拦住真调用」）。**mypy 逼出的设计事实**：首轮 **13 个 `attr-defined`**（四簇只借 `self.` 而不拥有槽位）⇒ 每簇加声明块，并把**声明面本身写成契约**：`declared == (self.X 派生集) ∪ (借用的方法)`，多一个没用声明也红；`leg`/`sweep` 原本从不需要 `Any` ⇒ 补 `from typing import Any`（否则 ruff F821）。**守卫演进 4 处**：`test_loop_core_tail_split`（全量 `MRO_NAMES` 插四名 + `INBOUND_CALLS["_eval_on_round"]` 呼叫点随 `_gate` → `loop_guards_gate.py`）· `test_loop_volume_split`（guards 覆盖面**主动**扩成「四新家 + 组合根」——S22 「静默读到空」的**预防性**应用）· `test_layering`（**未红**：四簇经 `rl` 传递**不达** remote，无需登记——「先红再登记」的反面，**不红也是信息**）· dashboard `specs.ts:416`（**无需改**：它指的 `_gate_halt_mode` 正留根，与 S21/S22 的盲区坑相反）。新守卫 `tests/trainer/test_loop_guards_split.py`（**27 例**，含 9 条功能性/机制性）+ 反探针 **27/27 全红**；纯搬对账 **12/12 逐字节等**（留守 4/4 同）。**★ 反探针的元教训**：首版两条变异「存活」（⑳ 干烧熔断不再读起点基线 · ㉒ 预算硬断不再停腿）——查下去全是**探针锚点打偏**（变异落在那两条 fixture **走不到**的分支上：⑳ 改的是 `baseline is None` 分支而 fixture 带 it0 基线；㉒ 改的是「未到顶」分支而 fixture 已过顶）⇒ 改成作用于命中路径的变异后 27/27。**反探针出现「存活」时，先怀疑探针本身，再怀疑守卫。** provenance 同步：`trainer/__init__.py` 模块表（1 → 5 行）· `README.md` 模块表（补 5 行）· `paired_kill.py` / `workdir_sweep.py` / `train_ledger.py` ×2 / `events.py` / `gate_check.py` / `config.py` 的指针改址。刻**意不动**：`docs/nn/training-stack.md` / `experiments.md` / `docs/rl.progress.md` / `plan/feasibility-map.md` 里带日期的历史记录。门禁 nn **2539 → 2566 passed / 3 skipped**；ruff `All checks passed`；mypy 绿（438 文件）；根 `bun run check` 2120 / 0；`check-decisions` ok。决策 → `DECISIONS.md` §2026-09-25-goalnn-loop-guards-cluster-split；全文 → `engineering.md` §28「第二十三刀」；计划 → `plan/nn-training-refactor.md` §5.3.22。
 ㉗ **第二十四刀（S4）：`batches.jsonl` 非原子落盘 —— 先写失败测试再修（§7）**——不是拆分而是**真缺陷**单独提前落地（用户指令「先把非原子落盘那个缺陷按 §7 写了失败测试再修（不等 B2，独立一小刀）」）：`trainer/batch_eval.write_batches` 从 `Path.write_text` 改为「同目录固定临时名 + `os.replace`」原子发布，改动只有**一个函数体**（+ 一条用例 + 一条 docstring）。**病根**：`write_text` 先 `open('w')` **原地截断** live 文件再写字节 ⇒ 「截断」到「写完」之间存在一个**读者可见**的窗口；而 `dashboard/data/evalboard/batches.jsonl` 有一个**无锁的跳语言读者**（console/TS 的 `batches.ts::loadBatches`，「runner 单写；console 只读」、坏行**静默跳过**），它的 `enqueueBatch` 去重（「同 course+rung+ckpt 的 pending 批已存在则返回它」）**依赖读全** ⇒ 短读会**重复入队**。**实测**（探针 `tmp/probe_batch_store.py`，读者逐字节照抄 `loadBatches` 读法；log = `tmp/probe-batch-store-final.log`）：修前 12 批/2.7 KB 短读 **126/622 = 20.3%** · 100 批/22 KB 114/453 = 25.2% · 2000 批/444 KB 144/376 = 38.3% + 12 坏行；修后三档全 **0 短读 / 0 坏行**（0/671 · 0/513 · 0/398）。**★ §7 三步已照做**：① 先在未改动代码上写确定性失败用例 `tests/trainer/test_batch_eval.py::test_batch_ledger_publish_is_atomic` 并确认**红**（实测断言失败于「读者读到了 `[0]` 批」）→ ② 只做让它的最小改动 → ③ 新用例绿 + 同关注点 30 例绿 + 全部门禁绿。**★ 确定性是刻意设计的**：不靠线程时序、不碰墙钟—— monkeypatch `Path.open`，在 live 文件**被以 `'w'` 打开的那一刻**读一次台账（写方假定地截断则必读到残局，原子发布则 live 文件根本不会被打开来写）；**并发探针只能当证据，不能当回归用例**（比例型断言会 flake）。**临时名固定**（`batches.jsonl.tmp`，不随机）：崩溃残留被下次直接覆盖、**不累积** ⇒ 不需要 `finally` 清理、不需要改 `.gitignore`（设计文档里原写的 `finally` 清理因此作废）；字节格式与写者唯一性**一字不改**。**被否决的备选**：保留 `write_text` + 加 `fsync`（不改截断语义）· 随机/PID 后缀临时名（残留累积 + 多一个删除失败面）· 改为 append-only 台账（改字节格式、动 TS 双侧契约 = 真设计改动）。**同族未修（另开一刀）**：`dashboard/src/evalboard/batches.ts::rewriteBatches`（TS 侧 `claimPending` / `updateBatch`）用**同样**的截断式 `writeFileSync`。设计文档已同步落地状态：`plan/nn-training-refactor.md` §5.5 标注「✅ 已先行落地」、§5.5.2 换「已修」、§5.5.4 的 B2 去掉「修非原子写」、§5.5.5 的⑤ 改成「已提前落地」。门禁 nn **2566 → 2567 passed / 3 skipped**；ruff / mypy 绿；根 `bun run check` 2120 pass / 0 fail（121404 expect，与改动前逐字一致 ⇒ 确认当日那 2 条 `dist-node-gate` 失败是并行负载 flake）。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-ledger-atomic-publish；全文 → `engineering.md` §28「第二十四刀」。
 ㉘ **第二十五刀（S25/B1）：`batch_eval` 拆分第一步 —— 「批语料规划 + 判据/门」出包 `trainer/batch_plan.py`**——`trainer/batch_eval.py` **1805 → 1616 行**（−189），新模块 **271 行**，23 个成员（13 函数 + 10 常量）**纯搬**：`REPO_ROOT`（路径锚点）· `LADDER_JSON` / `CORPORA_JSON` / `LEVELS_DIR` · `REGRESSION_EVERY` / `BATCH_STAGE_BASE` / `EVAL_SEED0` / `SEGMENT_LEN` · `KIND_FOR_POLICY` / `_KIND_CHAR` · `is_transient_error` / `node_gate_reason` / `kind_for_policy` · `load_ladder` / `plan_units` / `corpora_path` / `load_corpora` / `corpus_doc` / `plan_verdict_units` / `units_for_batch` / `_forces_of` / `batch_iter_id` · `select_next_unit`。**为什么先 B1**：B2（台账事务化）是真设计改动、B3/B4 依赖 B2 的接口；B1 是**零锁零台账**的纯函数面（只读 `ladder.json` / `corpora.json` / 课程关卡文件）⇒ 可单独验证/提交/回滚，并把 B2 的改动面削到「台账 + 执行器」。**宿主的选法在本刀是反向的**：搬的是被调用者 ⇒ 判据只剩「依赖方向单一」（`batch_eval → batch_plan` 单向；不 import 旧家（AST 钉）、不 import `remote`；顶层 import 闭集；**分层快照未红** = 不登记，与 S23 一样「不红也是信息」）。**零迁移依据 = 门面 21 条自别名再导出**（`X as X`）⇒ `from trainer.batch_eval import plan_units` 等调用点（含 `dashboard/src/evalboard/kick-once.py`）一行不改且 `batch_eval.X is batch_plan.X`；两个私有名（`_forces_of` / `_KIND_CHAR`）刻意不导出。★ 格式细节是 ruff 逼的：`combine-as-imports=false` 下带 `as` 的括号块会被要求拆开⇒一条一行（同 `hub/server.py`）。**唯一演进的守卫**：`test_dist_common_poll` 把「同名定义只许住 `common/distribution.py` / `batch_eval.py` + 读 `batch_eval.py` 文本」写死了 ⇒ 改成**按定义搜家**（非 `common.distribution` 的同名定义恰好一个且必须纯转发 + `importlib` 按名找家后 `is` 对象级比）——搬家不再让它静默失效。**★ 反探针擞出的真漏洞**：首版 21 条变异 **4 条存活**（四个镜像常量改值）—— 守卫**拿常量比自己**（`assert u["seed0"] == bp.EVAL_SEED0`）⇒ 改常量同时改掉断言两边；修法 = 补一条**字面量**断言（这四个常量与 `dashboard/src/evalboard/{runner,store}.ts` 双侧镜像，值本身就是契约：2000 / 860001 / 100 / 3）+ 功能性断言换回字面量 ⇒ 重跑 **21/21 全红**。**“测试绿”与“篘改会红”是两件事。** 验证：纯搬对账 **23/23 搬走 + 36/36 留下逐字节等**（对 `git show HEAD:` 用 AST 行区间取原文）· 闭集 `59 = 23 + 36` · 再导出 21/21 对象恒等 · 新守卫 `tests/trainer/test_batch_plan_split.py`（**19 例**，含 7 条**从新家调**的功能性）。provenance：`trainer/eval_m1_once.py`（`KIND_FOR_POLICY` 指针）· `dashboard/src/evalboard/corpora.ts`（双侧契约的 Python 路径）。门禁 nn **2567 → 2586 passed / 3 skipped**（ruff + mypy 绿）；根 `bun run check` 2120 / 0；dashboard typecheck + **1105 / 0**；`check-decisions` ok。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-plan-b1-split；全文 → `engineering.md` §28「第二十五刀」；计划 → `plan/nn-training-refactor.md` §5.5.4（B1 行）。
 ㉙ **第二十六刀（S26/B2）：台账收进唯一所有者 `trainer/batch_store.py`（`BatchStore`）**——`trainer/batch_eval.py` **1616 → 1190 行**（−426），新模块 **680 行**。**这一刀与前五刀不同类**：前面按「链 / 判据」搬代码，这一刀**按状态所有者收拢**（第一次把「谁拥有可变状态」当刀口）——台账有 8 个独立 read-modify-write 点、六种落盘策略（两种在没变时也整文件重写）⇒ 按链切无解（真因不是调用图，是**台账没有所有者**）。契约：`batches.jsonl` + 两个请求文件只有一个所有者，`status` / `node_dist` 的每次变更都在**具名转移**里，**一次转移 = 一次事务 = 一次落盘**；写面 8（`enqueue` / `enqueue_verdict` / `abort` / `claim` / `set_units_of` / `mark_unit_done` / `requeue` / `reopen_for_resume`）· 读面 3 · 请求面 4；`_claim_guard`/`_claim_locked` → `BatchStore._tx`（跨进程仍复用 `worker.train.loop_util` 的 `claim.lock`，进程内 `RLock` + 同线程同 root **可重入**），`_persist_of` 并入 `set_units_of` 后**消失**。**★ 两条刻意保留的特例语义**（集中化时最容易被抹平，守卫正面钉）：「`units.of == 0`（未定型）时 `mark_unit_done` 不判 done」（否则判决批在 `set_units_of` 之前被标 done ⇒ 剩余 unit 永远跑不到）·「`aborted` 批的在途 unit 只回填 `node_dist` 不复活，且 `requeue` 不改 `aborted`」。**★ 实施时才浮出的坑（设计未预见，已回写 plan §5.5.7）**：锁若只放在**单个具名转移**上，`consume_requests` 分不清「锁忙」与「去重跳过」两种 `None` ⇒ 会把请求标成已消费却**没建批**（丢请求、无日志、进度看着正常）⇒ 整轮也拿一把 `_tx`（内层转移可重入、不多付文件锁），回归钉子 `test_lock_busy_consumes_nothing_and_marks_nothing`；落盘统一成「改了才落盘」（byte 等价）+ `_publish` 为**全仓唯一台账写点**（调用者闭集守卫）。**验证三套独立证据**：① 纯搬对账 **9/9**（AST 去 docstring 后逐字等价；`read_batches` 一处**宣告差异** = 裸字面量提成常量 `BATCHES_FILE`，脚本要求宣告的替换逐字对得上，否则「宣告」变成遮盖真差异的挡箭牌）；② **★ 差分探针 54/54**（同一串台账操作分别打在**旧实现**（`git show HEAD:` 的 `batch_eval.py`，按文件路径 import）与**新 store** 上，逐操作比较掩码后的台账/`requests.done` 字节 + 返回值 —— 这是「B2 不是纯搬但行为等价」的主证据）；③ 新守卫 `tests/trainer/test_batch_store_txn.py`（**15 例**：结构契约 6 + `store` 只持有 root 且不缓存台账 + 六条功能性）。**★ 反探针 22/22 全红，首轮 1 条存活 = 真守卫空档**：删掉 `claim` 的「running ∧ 仍有未完成 unit ⇒ 可认领」分支**全绿** —— 既有 `test_queue_claim_done_cycle` 那条带注释「running + incomplete 也可被 claim（重启/孤儿批续跑）」的断言走的其实是 `pending` 分支（`mark_unit_done` 已把它改回 pending）⇒ 孤批续跑这条**生产上真实存在**的路径此前无人覆盖；补 `test_claim_resumes_an_orphaned_running_batch` 后全红（**「存活先怀疑探针」只是第一嫌疑，不是免检**）。**★ 顺手记下、本刀不改的既存缺陷**：`running ∧ of == 0` 的批永远不可认领（既不 pending 也不算 incomplete），生产上是「认领 → 判卷/展开 → `set_units_of`」之间的窗口，崩在窗口里只能等 `abort`；但把 `of == 0` 算 incomplete 会让另一个进程在派发前抢走整批（双派）—— 两个方向都有代价，属真设计问题（差分探针已证与旧实现逐字相同），写进守卫注释与工程记，**不静默改**。**守卫演进 3 处**：`test_batch_plan_split`（旧家读 `REPO_ROOT` 的次数 1 → 0，改为断言使用者在 `batch_store.py`）· `test_eval_requests` 两处改址（`_CLAIM_WAIT_SEC` 的 monkeypatch 目标；`_requeue` → `BatchStore.requeue`）· `test_batch_eval` 的 `_reopen_for_resume` 同改。**provenance**：`trainer/batch_plan.py`（「读改写仍住 batch_eval」→ `batch_store`）· `biz/eval_heartbeat.py`（`batch_eval.data_root` → `batch_store.data_root`）· `dashboard/src/evalboard/{requests,verdict-cli}.ts` + `dashboard/tests/evalboard-requests.test.ts` · 两处 `batch_eval.py:703` → **符号名** `BatchEvalRunner._run.record`（行号跨刀必漂，第三次为此改引用）。门禁 nn **2586 → 2608 passed / 3 skipped**（ruff + mypy 绿，442 源文件）；根 `bun run check` 2120 / 0（121404 expect）；dashboard typecheck + **1105 / 0**；`check-decisions` ok。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-store-b2-split；全文 → `engineering.md` §28「第二十六刀」；计划 → `plan/nn-training-refactor.md` §5.5.4（B2 行）。**下一步 = B3**（`BatchEvalRunner` + `dispatch_batch_bg` 纯搬 → `trainer/batch_runner.py`；两处按路径读源码的守卫 + 五处 `setattr("trainer.batch_eval.…")` 需随之改址）。
 ㉚ **第二十七刀（S27/B3）：`batch_eval` 拆分第三步 —— 执行面纯搬出包 `trainer/batch_runner.py`**——按用户指令「做 B3：把 `BatchEvalRunner` + `dispatch_batch_bg` 纯搬到 `trainer/batch_runner.py`（含两处按路径读源码的守卫与五处 setattr 改址）」执行。`trainer/batch_eval.py` **1190 → 223 行**（−967），新模块 **1031 行**，纯搬 **8 个成员**（`BatchEvalRunner` 896 行 · `dispatch_batch_bg` 36 · `_heartbeat` 8 + 五个执行器独占常量 `BUSY_BACKOFF_CAP_SEC`/`STUCK_GRACE_SEC`/`RECOVER_PING_SEC`/`NO_CONSUMER_GRACE_SEC`/`NODE_RECOVERY_TRIES`）；逐字节对账 **8/8** + 成员并集守恒（`HEAD = 旧家 + 新家`，零凭空消失/新增）。**★ 本刀题眼 = 注入点改址**：执行器的依赖注入靠**模块全局**（`bun_version`/`log`/`run_local_eval_game`），搬家后 `monkeypatch.setattr("trainer.batch_eval.X")` 不再生效（门面也 import `log` 自用 ⇒ 名字还在、没人在读 = **静默空操作**，S16/S19 记过两次的同款坑）⇒ 改址 **5 处 setattr + `run_local_eval_game` 1 处 + 两处按路径读源码的守卫**（`test_batch_eval_wver.py` 的 `SRC` · `test_eval_loot_fields.py` 的文件清单）；而**五个常量与 `_heartbeat` 刻意不转发** ⇒ 打在旧家是**响亮** AttributeError。**守卫演进**：`test_batch_plan_split` 的入边闭集原本只数旧家 ⇒ 搬走后静默退化成「2/6」（`BatchEvalRunner` 那四条调用点已搬家）⇒ 改成 `_inbound_calls()` **两个宿主合并计数**（用例名同步去掉 `in_the_old_home`）。**★ 反探针 17/17 全红，首轮 1 条存活 = 真守卫空档（第二个同族案例）**：把 `wver = hashlib.sha256(...).hexdigest()` 改成 `[:16]` 时 `test_batch_eval_wver.py` **全绿** —— 既有断言是**子串**匹配（后面多一个 `[:16]` 照样命中）⇒ 补 `test_wver_is_never_derived_from_a_slice()`（AST 取所有 `wver` 赋值，断言右值内不含任何切片/下标），守住 2026-09-19 那场事故的**上游形态**（此前只守下游实参）；这条同时是本刀「守卫随成员改址」的自证。**新守卫** `tests/trainer/test_batch_runner_split.py`（**12 例** / 316 行：结构 6 —— 定义唯一 · 门面对象恒等 · 不转发就 AttributeError · 无反向边（AST）· import 闭集 · **台账零手写**三问；**注入点契约 3** —— 三个 DI 名必须**裸名调用**且是本模块全局 · 五个常量在类成员里**裸名读** · `dispatch_batch_bg` 裸名构造；功能性 3 —— 心跳写点与失败静默 · 守护线程名/参数 · `_done_keys` 读盘口径）。**provenance**：`biz/agent_meta.py`（干净评估派发调用者）· `trainer/queue.py`（两处 `bun_version` 消费者）· `biz/eval_local.py`（`BatchEvalRunner._run.record` 的符号名引用）· `dashboard/src/evalboard/runner.ts`（执行侧 Python 路径）；**刻意不动**带日期的历史记录（`common/distribution.py` / `test_dual_track_eval.py` / `docs/evalboard-phase0-census.md`）。**B4 因此不再是独立一步**：门面已自然退成目标形态（常量 + `maybe_dispatch_batch` + 再导出，223 行 < 估的 ~290），plan §5.5.4 已对齐标注；**余下真实工作 = B5**（`_run` 的 821 行按阶段切）。门禁 nn **2608 → 2621 passed / 3 skipped**（ruff + mypy 绿，444 源文件）；根 `bun run check` 2120 / 0（121404 expect）；dashboard typecheck + **1105 / 0**；`check-decisions` ok（478 ids）。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-runner-b3-split；全文 → `engineering.md` §28「第二十七刀」；计划 → `plan/nn-training-refactor.md` §5.5.4（B3/B4 行）。
 ㉛ **第二十八刀（S28/B4）：`batch_eval` 拆分第四步 —— 门面收尾，把「门面是门面」变成机器可判的契约**——按用户「continue」（承 B3 收官时的「下一步 = B4」）执行。`trainer/batch_eval.py` **225 → 166 行**。**本步不是搬家**（B3 之后目标形态已达成），而是把门面形态钉成契约 + 清两处遗留：① 新守卫 `tests/trainer/test_batch_eval_facade.py`（**11 例**）—— 模块级 `def` 闭集 = {`maybe_dispatch_batch`} · 模块级赋值闭集 = {`ONESHOT_EVAL_KIND`} · 自别名再导出表**闭集**（双向，源码自别名集合 == 表；21 plan + 14 store + 2 runner）且对象级恒等 · 刻意**不**转发的名字（执行器五常量 + `_heartbeat` · store 文件名常量 · 三个台账私有 seam）必须**响亮** AttributeError · 门面**不改写** store 交回的台账 dict（AST：函数体零下标赋值 + `store.{claim,requeue,set_units_of}` 三次具名转移）。② 删掉一处旧实现残留：`maybe_dispatch_batch` 里那行 `batch.setdefault("units", {})["of"] = len(units)`（B2 之前「就地改台账再落盘」的第二半，同行原还有 `_persist_of(root, …)`）——B2 之后它**无任何读者**（`store.claim()` 交回的是认领时的台账快照；执行器只读 `batch_id`/`iter`，`of` 走参数 `unit_of`）⇒ 删它行为等价，删完「门面不是第二写者」才成为可断言的事。③ 8 段散落的「`X` 已搬到 `Y`」指路注释（含「本模块只剩台账 / 执行 / 接线」这类**已过期**说法）合并成 docstring 里一张「面 → 实现在哪 → 搬出日期」表 + 一栏「刻意不转发的名字与理由」。**★ 侦察出来的真空白**：`maybe_dispatch_batch` 此前**没有任何直接单测**（只被轮内 `loop_lifecycle._evalboard_idle` / `loop_dispatch._evalboard_yield` 间接覆盖），而它是门面里**唯一还活着**的逻辑 ⇒ 补主线（认领 → 规划（`k%3==2` 回归位 ⇒ of 2→3 落台账）→ 起一条执行线程）+ **三条 requeue 路径**（模式不适 / 规划失败 / nn 缺权重）+ 判决批「权重取 unit 而非批次级 `rl_path`」。**★ 反探针的第三种归因：断言无区分力**（S26「存活先怀疑探针」、S27「守卫搜索方式太松」之后）——首轮 **13/14**：把判决批权重改成 `rl_path or unit_ckpt` 优先**存活**，因为原用例传的是 `rl_path=None`（`None or w` 与 `w or None` 同结果）⇒ 不是探针打偏、也不是守卫太松，而是**断言没有区分力**；改成故意传**另一份** `rl_path` 并断言「发车用的是 unit 的 ckpt」后 **14/14 全红**，还原 sha256 无漂移。**★ 记一笔不改的既存缺陷**：`select_next_unit` 过滤后为空（`only_rungs` 里没有一个命中本次 plan 的 unit）时返回 `(units, None, None)`，门面 `if nxt is None or unit is None: return None` **不 requeue**；而 `claim` 的可跑判据是 `pending ∨ (running ∧ of>0 ∧ len(done)<of)`，建批时 `of` 已默认 2、`done` 还空 ⇒ **该批每个 idle 窗都会被再认领一次，永不发车、不落日志、状态永远 `running`**，与三条兄弟路径（模式不适 / 规划失败 / 缺权重，全都「记日志 + 退回队列」）不一致 ⇒ 属真设计问题（requeue 会变成每窗重试的另一种循环），**不静默改**，另开一刀。门禁 nn **2621 → 2632 passed / 3 skipped**（ruff + mypy 绿）；根 `bun run check` 2120 / 0（121404 expect）；`check-decisions` ok（479 ids）。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-eval-facade-b4；全文 → `engineering.md` §28「第二十八刀」；计划 → `plan/nn-training-refactor.md` §5.5.4/§5.5.5。**B1–B4 至此完整收官**；余下真实工作 = B5（`BatchEvalRunner._run` 的 821 行按阶段切），按 §5.5.4 另开一轮。
 ㉜ **第二十九刀（S28/B4 的既存缺陷）：无可跑 unit 的批不再静默卡死（§7 单独一刀）**——B4 侦察时发现、当刀故意不改的那条缺陷，按 §6.3「有被否决备选 + 未来会重犯 + 不能就近表达」记下后**另开一刀**。**病根**：`maybe_dispatch_batch` 在「规划成功但 `select_next_unit` 无待跑 unit」时静默 `return None`，而该批上一行已被 `claim` 置为 `running`，`claim` 的可跑判据是 `pending ∨ (running ∧ of>0 ∧ len(done)<of)`，`of` 建批时默认 **2**（`enqueue` 写 `units: {of: 2, done: []}`）⇒ **每个 idle 窗都被再认领一次，永不发车、不落一行日志、状态永远 running**（本仓最忌讳的「进度看着正常」）。**可达条件**：`only_rungs` 无一命中本次 plan——正常路径不触发（`backfill.ts` 与 console 都从同一个 rung 推 `ladder_pos`），但 ladder.json 在入队与派发之间被改过、或判决批的 `语料id#关卡名` rung 与 ladder rung id 不同域时到得了。**决定 = 取代码自身的先例**（§6.2 第三条）：三条兄弟路径（模式不适 / 规划失败 / nn 缺权重）全是**记日志 + `store.requeue`**，只有这一条不一致 ⇒ 补成同形。**被否决的备选**：① 只记日志不改状态（可观测性有了，「永不发车 + 每窗空转」仍在）；② 改标 `aborted`（终止且可见，但 abort 在本仓一直是**人的动作**，让 runner 自动中止用户请求的批是**新的权力**，得单独设计：谁有权中止 / console 怎么展示 / 能不能重入）⇒ 不是本刀能隐式决定的。**代价写明**：批回 `pending` 后每窗重新 plan 一次，与模式不适那条路同形，但多了日志 ⇒ 可见可排查。**§7 三步**：① 先在未改动代码上写确定性失败用例 `tests/trainer/test_batch_eval_facade.py::test_a_batch_whose_filter_matches_no_unit_is_not_left_silent` 并确认**红**（`assert 'running' == 'pending'`）→ ② 只加「记日志 + requeue」一个分支 → ③ 新用例绿 + 同关注点 12 例绿 + 门禁绿。反探针 **15/15 全红**（新增 ⑭：把该分支改回静默 return）。门禁 nn **2632 → 2633 passed / 3 skipped**；根 `bun run check` 2120 / 0（121404 expect）。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-unrunnable-filter-loud-requeue；全文 → `engineering.md` §28「第二十九刀」。
 ㉝ **第三十刀（S30/B5a）：`BatchEvalRunner._run`（821 行）按**相位**切三段，机器体逐字节不动**——按用户「continue」（承 B4 收官时的「下一步 = B5」）执行，plan §5.5.4 的 B5「另开一轮」正式开轮（设计落 plan §5.6）。**先侦察再落刀**（`tmp/recon_b5*.py`）：`_run` 内只有 **11 个嵌套 def**，其余全是顺序语句；分三段 A 单元开头 119 行 / S 通道机器 615 行 / D 收尾 85 行。**量化决定刀口**：外层赋值 **65** 个名字、**58** 个被机器段读 ⇒ 机器段 **212 处引用**，其中 **139 在 11 个闭包内**（worker 77 · bringup 19）；**零 `nonlocal`** —— 状态靠**可变容器**（`seen: set` / `settled: [0]` / `lanes: dict`）绕过闭包只读限制 ⇒ 「按调用图切」只能得到一个 615 行的连通分量。**病根与 B2 同型：不是没有链，是状态没有所有者。** ⇒ 本刀取**相位边界**（开头 = 短路 / 机器 = 并发与背压 / 收尾 = **对台账的唯一交代**）：`_run` **6 行**（只讲相位）· `_open_unit` **158**（产出 `_UnitPlan`：**29 字段的对外契约**）· `_run_channels` **677**（**原 615 行机器体逐字节保留** + 8 条取值）· `_settle_unit` **87** · `_log_provenance` **47**。`_UnitPlan` **只收机器与收尾真用到的 29 个名字**，开头内部的中间量（`policy_cfg`/`window`/`god`/`iter_base`/`pairs`/`done_before`/`snapshot_path`）**不出界**（否则契约退化成「什么都依赖」）。**验证**：① **逐段字节对账**（`tmp/verify_b5.py` 对 `git show HEAD:`）—— A 119 · S 615 · D_head 34 · D_tail 20 · PROV 31 行**全部原样且各出现一次**；新写的只有粘合（契约 + **8 条恒等映射取值** `x = plan.x` + 两次调用 + `typing` 导入）。② **契约闭合**：字段 == 返回实参 == 取值段取到的名字（少一个即 NameError）；收尾/账块参数表是**闭集**。③ **反探针 14/14 全红**（`tmp/probe_b5.py`），sha256 无漂移，其中 ④（相位塞逻辑）· ③（取值非恒等映射）· ⑥（调用顺序错位）正对着本刀新增的断言。④ **★ 本刀真正的收益 = 新获得的可测性**：`_settle_unit` / `_log_provenance` 现在**直接可调** ⇒ 台账交代（全结算 ⇒ `mark_unit_done` + `node_dist`；部分 ⇒ `reopen_for_resume`）、「任何失败只记日志绝不抛出」、两条响亮告警、两处短路全成了单测（`tests/trainer/test_batch_runner_phases.py` 10 例）—— 拆分前这些只能用「跑一个真单元」间接看。**守卫演进 2 处**：`test_batch_runner_split` 的 import 闭集（+`typing`）· `test_batch_plan_split` 的入边归属者改名（`_run.bringup` → `_run_channels.bringup` 等，第三次撞上「搬成员 = 改归属者名字」）。**对账脚本自身的一个假红**：收尾签名检查首版把 `closing` 当成漏参 —— 机器里另有一个同名局部（作用域不同）⇒ 修检查器而不是改代码（同一节课：红的先怀疑检查器）。**遗留 = B5b**（`_run_channels` 仍 677 行；状态对象化是**改写**：212 处引用 + 40 处 `self.` 改回指 ⇒ 字节对账不成立，主证据须换成「AST 变换证明 + 差分探针」，已写进 plan §5.6.3，两刀分开提交各自可回滚）。门禁 nn **2633 → 2643 passed / 3 skipped**（ruff + mypy 绿）；根 `bun run check` 2120 / 0（121404 expect）。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-runner-phase-split；全文 → `engineering.md` §28「第三十刀」；计划 → `plan/nn-training-refactor.md` §5.6。
 ㉞ **第三十一刀（S31/B5b）：`_run_channels` 的 677 行通道机器收进 `_UnitLanes`（**状态对象化**）**——按用户「continue」（承 B5a 收官时的「下一步 = B5b」）执行，plan §5.6.3 正式落地。**刀口 = 按状态所有者，不按调用图**（病根同 B2：外层 65 个名字里 58 个被机器段读、212 处引用里 139 在闭包内、**零 `nonlocal`**、状态靠可变容器 `seen`/`settled: [0]`/`lanes` 绕开闭包只读限制 ⇒ 按调用图切只能得到 677 行连通分量）。`_run_channels` **677 → 4 行**（只剩 `return _UnitLanes(self, plan).run()`），机器体成 **675 行 / 13 方法**：29 个契约字段 + 23 个状态容器成属性（`__init__` 从 `_UnitPlan` **逐名**接）、11 个闭包成方法、主循环成 `run()`。**★ 偏离 plan §5.6.3（记进 DECISIONS）：目标形态原写「新模块 `rl/batch_lanes.py`」，实际**不换模块** —— 机器体读**本模块的 20 个全局**（`log`/`run_local_eval_game`/`is_transient_error`/`node_gate_reason`/`_record_agent_meta`/`pick_race_target`/`register_inflight`/`pop_inflight`/`clear_inflight`/`common.distribution`/`deque`/`threading`/`time`/`json`/`BUSY_BACKOFF_CAP_SEC`/`STUCK_GRACE_SEC`/`EVAL_TASK_ATTEMPTS`/`eval_loot_fields`/`eval_census_fields`/`_UnitPlan`），换模块会把每一条 `monkeypatch.setattr("trainer.batch_runner.X", …)` 变成**静默空操作**（S16/S19/S27 同款）；被否决备选：20 个依赖显式注入（契约退化成「什么都依赖」）· 保留闭包显式传状态（同量改写无收益）。**验证（字节对账不成立 ⇒ AST 规范化等价，对全输入成立）**：`tmp/verify_b5b.py`（**不用生成器**的独立机制）四节全绿 —— ① **段落守恒**：旧体 48 条顶层语句被「前置取值 8 + 状态块 23 + 10 个 def + 主循环 6 + 收尾 1」**恰好覆盖一次**；② **规范化等价**（`self.owner.X`→`self.X`、`self.X`→`X`、抹掉 `AnnAssign.simple`）⇒ 状态块 23/23 逐节点等 · 11 个闭包**签名 +self、体逐节点等** · `run()` = 主循环 6 + 收尾 1；③ **注入点**：从旧体**导出**的 20 个模块全局逐个断言在新类里仍是**裸名**（且无 `self.<全局名>`）；④ 注释守恒 80 条全在（**宣告删除 1 条**：前置取值段那句「显式取值 ⇒ 下面逐字保留、不改成 plan.x」正是本刀改写的东西 ⇒ 换新注释）；**差分探针（plan 原第 2 条）按实情收敛**：本刀改动的测试文件只有 4 个（3 处结构守卫演进 + 1 新守卫），其余用例两版逐字节相同 ⇒ 两版全绿（HEAD 2643 / 新 2653）即「同 fixture ⇒ 同可观测行为」的跨版本差分（主力 `test_eval_dispatch_resilience` 是真跑 `dispatch_eval_round` 的端到端场景）。**守卫演进 3 处（「搬成员 = 改归属者名字」第四次）**：phases 的契约段搬到 `_UnitLanes.__init__`（+「每个字段必须真被读」）· plan_split 入边归属者**换类**（`…_run_channels.worker` → `_UnitLanes.worker`）· runner_split 常量读取改为**两个类合并扫**（`BUSY_BACKOFF_CAP_SEC`/`STUCK_GRACE_SEC` 随机器搬走，只扫旧类会静默退化成 3/5）。**★ 反探针 21/21 全红**（无存活；首轮 5 条「锚点不唯一/零命中」**是探针自己的错** —— 锚点出现 2–4 次或缩进写错 ⇒ 修探针、并让「锚点不唯一」显式报错而不是静默跳过），sha256 无漂移。**★ 新获得的可测性**（同 B5a）：`lane_state`/`mark_tripped`/`window_open` 直接可调（归一化 `authKey`→`key` 那场 2026-09-19 事故 · 幂等 · 「`max_recovery_tries` 才判死」· 槽位不为负 · 关窗优先于墙钟）⇒ 新守卫 `tests/trainer/test_batch_lanes_split.py` **10 例**（结构 6：类定义唯一 · 方法名闭集 13 · **无嵌套 def** · `_run_channels` 只剩转发 · **实例属性面 == 闭集 64** · 机器零台账；功能 4）。门禁 nn **2643 → 2653 passed / 3 skipped**（ruff + mypy 绿，447 源文件）；根 `bun run check` 2120 / 0（121404 expect）；`check-decisions` ok（482 ids）。决策 → `DECISIONS.md` §2026-09-25-goalnn-batch-lanes-objectify；全文 → `engineering.md` §28「第三十一刀」；计划 → `plan/nn-training-refactor.md` §5.6.3/§5.6.4。**B1–B5b 全系列至此收官**；§5.6.4 留了余下候选与**反判据**（`worker` 三段共享同一批 mutable 账 ⇒ 现在切只会得到「互相传 6 个参数」）。

> **编号说明（2026-09-25 合并）**：本节的三条本地条目原为 §146/§147/§148，与 origin 已推送的
> 条目撞号；按先例（见 `ad0f448`）**已推送一侧保号**，本地三条改 §150/§151/§152。它们的全文落点
> 也随之指向 `engineering.md` §26/§27/§28（本地那三节同因撞号改号，见该文件头「合并说明」）。

---

**S5 第一刀（2026-09-27）：`worker/eval_local` 的「逐局 eval 行 schema + 账本 I/O」簇 → `biz/eval_rows.py`**
——按用户指令「重构 nn-training：降低模块耦合 / 复用代码 / 提可维护性与可扩展性」执行。前序 S1–S4（§26–§28）
收口后，用 `tmp/recon_god.py` 扫描剩余大文件的**模块级连通分量**：`biz/eval_local.py`（1130 行）有 **5 个
干净分量**（缝最清），取其中的「eval 行/账本 I/O」簇（**连续行 287–602，逐字节不动**，17 名 = 三字段抽取器
+ `eval_row` + 账本读/并/去重）。`biz/eval_local.py` **1130 → 870 行**；新模块 `biz/eval_rows.py`（345 行，
stdlib-only）；原模块留 `X as X` 门面 ⇒ 全仓调用点与测试一行不改（`tests/worker/test_eval_ledger_merge.py` /
`test_eval_loot_fields.py` / `test_eval_row_v8.py` / `test_eval_census_fields.py` 的 import 原样）。
**为什么**：`remote/` 侧（`hub/queue_resume` 补传合并 / `deliver_zip` 产物导入 / `offline_eval` 行构造）
本要 `import biz.eval_local` 才拿得到纯行/账本原语——传输层伸手进运行器；独立后纯数据模块可被任一侧依赖。
**验证**：逐字节对账 `diff` 零差异；新守卫 `tests/worker/test_eval_rows_split.py`（8 例：定义唯一 / stdlib-only
闭集 / 不得反向 import / 门面对象恒等 / 旧 import 路仍成立 / `merge_eval_rows` 去重 + summary 单调）；
门禁 nn **3120 → 3128 passed / 3 skipped**，ruff + mypy（498 文件）绿。**本刀刻意不做**：把 `remote/`
调用点改指 `biz.eval_rows`（会动 `test_hub_queue_split` 的 lazy-import 断言与 `remote_dag` 台账）——留作下一刀。
决策 → `DECISIONS.md` §2026-09-27-goalnn-eval-rows-split；全文 → `engineering.md` §30。

**S5 第二刀（2026-09-27，同日续）：`remote/` 只用纯原语的三处调用点改指 `biz.eval_rows`**——
`hub/queue_resume.py`（延迟 `append_eval_rows`/`append_eval_summaries`）· `remote/deliver_zip.py`
（延迟 `merge_eval_rows`）· `remote/offline_eval.py`（模块级 `eval_row`；它对运行器
`run_local_eval_game`/`settle_eval_summary` 的依赖**合法保留**）。这样才真正把 `remote → eval_local` 的边
从 3 条收到 1 条（合法运行器依赖）。同步 `tests/hub/test_hub_queue_split.py` 的 lazy-import 断言到 `biz.eval_rows`，
并新增两条**边守卫**（防止这边悄悄长回来）。门禁 nn **3128 → 3130 passed / 3 skipped**。

**S5 第三刀（2026-09-27，同日续二）：双轨/过拟合/结算簇 → `biz/eval_track.py`**——把 `worker/eval_local` 里
「双轨日常评估」这个关注点整块搬走（四段跨度、23 名，逐段逐字节不动）：`EVAL_SEEDS` 池 + 双轨种子分类
（`rotor_offset`/`rotor_span`/`dual_track_seeds`/`a_eval_seed_list`/`is_anchor_seed`/`is_rotor_seed`/
`split_anchor_rotor`/`should_dual_track`）+ 过拟合判决（`overfit_gap_pp`/`overfit_fires`/
`_read_recent_track_wrs`/`_maybe_warn_overfit`）+ 结算（`settle_eval_summary`/`report_winrate_safe`/`_acc`/`_ratio`）。
`biz/eval_local.py` **870 → 573 行**；新模块 `biz/eval_track.py`（404 行；依赖面 = stdlib + `biz.log`）；
原模块留 `X as X` 门面 ⇒ 调用点（`gate_check`/`eval_a_once`/`eval_dispatch`/`remote/offline_eval`…）与
`tests/worker/test_dual_track_eval.py` 一行不改。**为什么**：它是「读账本算趋势」的纯逻辑（不起进程），与
「怎么跑一局」分离后，读者依赖的是「双轨怎么算」而不是「评估器怎么起」。**刻意不搬 `EVAL_ITER_SUFFIX`**
（它是 dispatch 的 eval iterId，不是双轨）——「按关注点切」而非「按相邻切」。**验证**：分段逐字节对账
（`tmp/verify_track_exact.py` 四段 `ALL BYTE-EXACT`）；新守卫 `tests/worker/test_eval_track_split.py`（9 例，含反向：
执行面必须留守）；门禁 nn **3130 → 3139 passed / 3 skipped**，ruff + mypy（498 文件）绿。
决策 → `DECISIONS.md` §2026-09-27-goalnn-eval-rows-split（同日续二）；全文 → `engineering.md` §30（同日续二）。

**S5 第四刀（2026-09-27，用户指令「按同一手法拆 `trainer/bc_loop.py` 的独立连通分量」）：BC job 回传消费 → `trainer/bc_ingest.py`**
——用 `tmp/recon_god.py` 扫出 `trainer/bc_loop.py`（1433 行）的 6 条模块级链（回传消费 / 归档·磁盘有界 / 语料采集 /
任务发布 / 运行时解析 / CLI），取「**等一个远端 BC job 回传并把回传入账**」这簇（两段跨度共 11 名，逐字节不动：
`IDLE_WARN_SEC`/`POLL_SEC`/`POLL_MAX_SEC` + `READY`/`PENDING`/`TRANSIENT` + `BcWait` + `ledger_bc_epoch` +
`run_epoch_eval` + `ingest_bc_metrics` + `wait_bc_round`）。`trainer/bc_loop.py` **1433 → 1213 行**；新模块
`trainer/bc_ingest.py`（291 行）；原模块留 `X as X` 门面 ⇒ 调用点与 monkeypatch 点（`bc_loop.time` /
`bc_loop.wait_bc_round`）一行不改。**为什么**：① 一段**连续跨度**（行 374–622）；② **独立所有者**（「怎么问结果、
怎么写账本」≠「一轮怎么跑」）；③ **两条驱动路径共用**（单课程阻塞 `wait_bc_round` 与 supervisor 让位
`BcWait.poll_once`）。**分层快照先红再登记**：`bc_ingest` 沿用「`_request` 函数内 import」口径 ⇒ 成为
`RL_ORCHESTRATION` 直接成员（新登记 + 说明）。**验证**：分段逐字节对账（`tmp/verify_bc_ingest_exact.py` 两段
`BYTE-EXACT`）；新守卫 `tests/trainer/test_bc_ingest_split.py`（9 例，含反向「编排面必须留守」+ `poll_once` 三态分类 +
`backoff_sec` 指数封顶）；门禁 nn **3139 → 3148 passed / 3 skipped**，ruff + mypy（503 文件）绿。
决策 → `DECISIONS.md` §2026-09-27-goalnn-bc-ingest-split；全文 → `engineering.md` §31。

**S5 第五刀（2026-09-27，用户指令「拆 `remote/hub_client.py` 的 HTTP 往返分量，先把 HTTP 面做成可注入薄壳」）：HTTP 面 → `remote/hub_http.py`**
——把 `remote/hub_client.py`（1688 行）里「发请求 / 读回答」那一旮整块搬到新模块 `remote/hub_http.py`（478 行，
**两段跨度共 17 名，逐字节不动**：`HubClientError` + `_request` 传输薄壳 + 回传消费
`probe_job_result`/`poll_job`/`wait_job`/`_wait_state_note`/`_job_failed_from_body`/`ProbeResult` + 三态与阈值常量 +
行政面 `report_job_failure`/`set_cloud_halt`/`hub_halted`/`clear_halt_on_startup`）。`hub_client.py` **1688 →
1299 行**，留 `X as X` 门面 ⇒ 历史 import 一行不改。**为什么**：传输面与「打包/磁盘 IPC 发布/校验落位」是两件事；
且 `probe_job_result` 是**两条链路**（pull `wait_job` / push `push_client`）共用的唯一状态码分类实现。**关键约束**：
门面留 `_request` 转发名，但注入点**只能是** `remote.hub_http._request`（照转发名打补丁是空操作——**刻意**的分档）；
`AUTH_HEADER`（`common.protocol`）不再经 `hub_client` 转发 ⇒ `e2e/test_bc_epoch_e2e.py` 改向所有者取。**分层**：
`hub_http`=L1，`hub_client` 顶层 import 它 ⇒ 升 **L2**（先红再登记）。**验证**：分段逐字节对账
（`tmp/verify_hub_http_exact.py` 两段 `BYTE-EXACT`）；新守卫 `tests/remote/test_hub_http_split.py`（10 例，含分层方向 +
**seam 正反两档** + import 面闭集 + 三态/410 终局）；门禁 nn **3148 → 3158 passed / 3 skipped**，ruff + mypy（505 文件）绿；
根 `bun run check` **2181 pass / 0 fail**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-hub-http-split；全文 → `engineering.md` §32。

**S5 第六刀（2026-09-27，用户指令「拆 `common/protocol.py` 的格式域（先核 L0 单份性守卫）」）：失败类型族 + 线格式 → `common/errors.py` + `common/wire_codec.py`**
——`common/protocol.py` **1708 → 1482 行**。**先核 L0 单份性守卫**：`tests/common/test_common_layer.py` 只守「共享原语
单一实现」与「`common/` stdlib-only」，**没有**任何断言 `protocol.py` 内部布局 ⇒ 拆分不触发。`recon_god.py` 扫出
**7 组**模块级链；取「**字节↔对象**」这一整片：① **失败类型族**（`ProtocolError`/`RetryableError`/
`UnreapableChildError`/`JobCancelledError`/`JobFailedError`/`CodeChangedError`）→ `common/errors.py`（118 行，
**零 import 叶子**）；② **传输编码 / v1+v2 线格式**（`_pack_wire`/`_unpack_wire`/`encode_*`/`decode_*` + `WIRE_*`
常量 + `pack_job_v2`/`unpack_job_v2`/`pack_result_v2`/`unpack_result_v2`）→ `common/wire_codec.py`（231 行）。两簇
**三段跨度逐字节不动**；`protocol.py` 留 `X as X` 门面 ⇒ 全仓 `from common.protocol import …` 一行不改。**为什么 errors
必须一起走**：v2 解包要抛 `ProtocolError`，若 `wire_codec` 反向 import `protocol` 而 `protocol` 又为门面 import
`wire_codec` ⇒ 模块级成环；失败类型抽成零依赖叶子后两边都能向下依赖。**关键不变式**：`wire_codec` 抛的与全仓
`except` 的必须是**同一个类**（守卫用 `is` 钉住）。**验证**：三段逐字节对账（`tmp/verify_protocol_exact.py` 两段
`BYTE-EXACT`）；新守卫 `tests/common/test_protocol_split.py`（9 例，含 **`errors` 零 import 叶子** + 不得反向 import
`common.protocol` + v1 往返/旧格式兼容 + v2 job/result 往返 + 截断/余料响亮拒绝）；门禁 nn **3158 → 3167
passed / 3 skipped**，ruff + mypy（508 文件）绿；根 `bun run check` **2181 pass / 0 fail**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-protocol-split；全文 → `engineering.md` §33。

**S5 第七刀（2026-09-27，用户指令「继续拆 `common/protocol.py` 的 job 身份簇（`idempotency_key` / `job_id` / `collision_rows`）」）：job 身份簇 → `common/job_identity.py`**
——`common/protocol.py` **1482 → 1390 行**；一段跨度共 4 名（逐字节不动）：`idempotency_key`（D1 幂等键，
含 2026-09-24 加的 `course_fp`）· `job_id`（`sha256(键)[:16]`）· `collision_rows`（发布端撞名守卫的**唯一判据**）
+ `SIBLING_MANIFEST_GLOB`（扫描面单一定义）→ `common/job_identity.py`（134 行，**stdlib-only 叶子**）。
**为什么**：身份是「两个 store 会不会抢同一份活」的唯一答案，与「manifest 字段合不合法」「字节怎么编解码」
是三个问题；`collision_rows` 是**发布那一刻**的哨兵，但**不需要**协议校验面。**刻意留守** `job_seed`（D5
**种子**，不是身份——recon 里它是孤立节点，与身份簇零同族边）。与 ⑥ 的差别：这 3 名**一个不抛
`ProtocolError`** ⇒ 不需要带 `errors`，是更单纯的叶子（依赖面 = stdlib only，零环风险）。**验证**：逐字节
对账（`tmp/verify_job_identity_exact.py` ⇒ `BYTE-EXACT`）；新守卫 `tests/common/test_job_identity_split.py`（9 例，
含**键含 `course_fp`** + 撞名守卫只在「完整键相同 且 别的 store」命中 + 缺键/坏 JSON/扫不到根不误伤）；
门禁 nn **3167 → 3176 passed / 3 skipped**，ruff + mypy（510 文件）绿；根 `bun run check` **2181 pass / 0 fail**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-job-identity-split；全文 → `engineering.md` §34。

**S5 第八刀（2026-09-27，用户指令「拆 `common/protocol.py` 的 payload zip 打包面（`pack_payload` / `unpack_payload`）」）：语料归档面 → `common/payload.py`**
——`common/protocol.py` **1390 → 1238 行**；**三段跨度共 10 名**（逐字节不动）：① `PAYLOAD_NAME` +
`PAYLOAD_LEGACY_NAMES`；② `INIT_WEIGHTS_NAME` + `PAYLOAD_PERTURB_NAME` + `PAYLOAD_XZ_PRESET`；
③ `find_payload` + `_add_bytes` + `_extract_archive` + `pack_payload` + `unpack_payload` → `common/payload.py`
（213 行，stdlib + `common.errors`）。**为什么**：它是**字节容器**面（一份语料怎么变成一个可传输的字节串），
与「manifest 字段合不合法」「这份活是谁」「HTTP 体上限多少」「内容寻址 blob 路径」都不同。**两处事故复盘的判据都住这里**：
① 判别**先 tar 后 zip**（2026-09-21 EOCD 启发式把完好 tar.xz 误判成 zip ⇒ 毒包每 5 分钟复现、零告警空转 3.5h）；
② **空包必须响亮**（判别反转后的新失败形态）。**刻意留守**：`TS_CODE_NAME`（别的产物：TS 运行时 zip 走独立
body 段）· `FAIL_*`/`PRIORITY_BODY_MAX`/`PEEK_MAX`（HTTP 体上限）· `OFFLINE_*`（补传端点）· `is_content_sha`/
`blob_path`（**内容寻址 blob** 面，opt-blob-diet M2）。**验证**：三段逐字节对账（`tmp/verify_payload_exact.py`
⇒ 3 段均 `BYTE-EXACT`）；新守卫 `tests/common/test_payload_split.py`（11 例，含 tar.xz 往返 + legacy zip 双读 +
空包/坏容器响亮 + 扰动换字节）；门禁 nn **3176 → 3187 passed / 3 skipped**，ruff + mypy（512 文件）绿；
根 `bun run check` **2181 pass / 0 fail**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-payload-split；全文 → `engineering.md` §35。

**S5 第九刀（2026-09-27，用户指令「拆 `common/protocol.py` 最后一片：manifest/rollout 校验面」）：manifest 契约面 → `common/manifest.py`**
——`common/protocol.py` **1238 → 848 行**；**四段跨度共 38 名**（逐字节不动）：P `PROTO`；R 角色**词汇**
（`ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/`ROLE_FIELD`）；M manifest schema（`MANIFEST_*` 7 个 + `MANIFEST_KINDS`）
+ `KIND_ROLES` + `role_of` + TS/plan 产物契约（9）；D `normalize_manifest` + rollout 规格校验（5）+ shard 命名/语料指纹
（7）→ `common/manifest.py`（547 行，stdlib + `common.errors`）。**⚠ 没按相邻切**：① **角色只搬「值域」**——
`ROLE_HEADER`/`ROLE_HEADER_VALUE`（HTTP 头名）与 `role_from_header`（会话角色解析）**留守** `protocol`（请求头面，
不是 manifest）；② `BLOB_*`/`blob_path` 留守（内容寻址 blob；搬走还会制造 `manifest → protocol` 的边）；
③ `PROTO` 随行（只服务 `normalize_manifest`）。**切前先量**：对候选名集做过**双向闭包检查**——候选块对外零引用留守名、
留守节点零引用候选名，两个方向都空才动刀。**验证**：四段逐字节对账（`tmp/verify_manifest_exact.py` ⇒ 4 段均
`BYTE-EXACT`）；新守卫 `tests/common/test_manifest_split.py`（13 例，含 role 词汇与请求头面的归属 + bc/iter mode 互斥 +
`KIND_ROLES` 穷举 + `data_fp` 同源 + rollout argv 白名单/相对路径/重复局/workers=0 拒收）；门禁 nn **3187 → 3200
passed / 3 skipped**，ruff + mypy（514 文件）绿；根 `bun run check` **2181 pass / 0 fail**。
**系列收口**：`protocol.py` 1708 → 848（**−50.3%**），四刀（⑥–⑨）各成可独立依赖的叶子/格式域，
新模块图**无环且严格向下**；剩余 848 行是零散常量与单函数面（租约/优先级/push/课程模式 · HTTP 体上限 · 离线端点 ·
`run_id` 清洗 · blob 路径 · `validate_result` · `job_seed` · `coef_active`，彼此不构成独立所有者）。
决策 → `DECISIONS.md` §2026-09-27-goalnn-manifest-split；全文 → `engineering.md` §36。

**S5 第十刀（2026-09-27，用户指令「拆 `biz/config.py` 的文件面/类面与课程解析面」）：三面 → `biz/config_file.py` + `biz/course_spec.py` + `biz/course_resolve.py`**
——`biz/config.py` **1566 → 604 行**（**−61.4%**），三面三模块（**逐字节不动**）：① **文件面** `RL_CONFIG_ENV` ·
`rl_config_path` · `read_rl_config_file`（1 段）→ `biz/config_file.py`（55 行，stdlib + `common.distribution`，双向闭包两个方向皆空）；
② **类面**（17 段 25 名：`CourseConfig`/`GatesSpec`/`GateRule`/`GateTeacher`/`StageSpec`/`Spawn`/`SpawnVariant`/`RewardBlock`/
`ParamSchedule`/`PpoScheduleEntry`/`PlayerBlock`/`StateInitBlock`/`GATE_*`/`_GATE_*_FIELDS`/`_gate_ratio`/`_default_lives`/
`STAGE_JSON_MAX_BYTES`/`CUSTOM_STAGE_BASE`）→ `biz/course_spec.py`（953 行，pydantic + `biz.reward_library`）；
③ **解析面**（9 段 9 名：`CURRICULA_DIR`/`LEVELS_DIR`/`resolve_level`/`load_course`/`resolve_course`/`course_from_args`/
`resolve_state_init_bank`/`_resolve_courses`/`_LEVEL_ENV_KEYS`）→ `biz/course_resolve.py`（146 行，`biz.course_spec` + 函数内 `common.jsonc`）。
**⚠ 关键切分判断**：① **类面 + 解析面同刀**（一个环的耦合体，先例 ⑥）：解析面静态依赖类面（`load_course` 造
`CourseConfig`）；类面唯一反向边是 `GatesSpec` 跨课门校验调 `_resolve_courses` ⇒ 段内插**函数内**延迟 import（顶层会成环，
唯一一条静态边方向 = 解析面 → 类面）；② `resolve_state_init_bank` 随解析面（路径解析，读 `CURRICULA_DIR`）、
`_default_lives` 随类面（只喂 `CourseConfig.reward_spec`）；③ `RLConfig`/`validate_args`/`corpus_identity_fp`/`apply_course` 一族**留守**。
**两处测试指针改指**（模块全局的 setattr 必须打在新家，否则静默失效）：`test_ladder_factory` 的 `"biz.config.LEVELS_DIR"` →
`"biz.course_resolve.LEVELS_DIR"`；`test_state_init` 的 `import biz.config as cfg` → `import biz.course_resolve as cfg`。
**验证**：逐段逐字节对账（`tmp/verify_config_file_exact.py` / `tmp/verify_course_faces_exact.py` ⇒ 1+17+9 段全 `BYTE-EXACT`，
类面 1 处既知插入=延迟导入）；新守卫三个共 24 例（定义唯一 / 依赖面闭集+无环（顶层禁反向边）/ 门面对象恒等 /
**模块全局是活读取点** · 契约语义：gates 未知 kind · 跨课引用找不到 · level 注入+重复声明拒收 · 冻结字节/互斥/无参 None ·
三基准银行路径 · 坏 JSON 空 dict · env 可重定向）；门禁 nn **3200 → 3224 passed / 3 skipped**，ruff + mypy（**520** 文件）绿；
根 `bun run check` **2181 pass / 0 fail**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-config-faces-split；全文 → `engineering.md` §37。

### 2026-09-27 S5 第十一刀：`common/distribution.py` 两个小簇（shard 面 + 权重下发账本）下沉（用户指令）

`common/distribution.py` **1503 → 1373 行**（−130；diff +53/−183），两个新模块（**逐字节不动**）：
① **`common/shard.py`**（137 行，stdlib-only）——三份 shard 清单（`SHARD_FILES`/`INTENT_SHARD_FILES`/`BC_SHARD_FILES`）+
`BC_COLLECTOR` + `_shard_files_for`/`validate_result`/`write_shard`（7 段 7 名）；② **`common/weights_ledger.py`**（74 行，零 import 叶子）——
`_WEIGHTS_PUSHED` + `weights_push_cache_reset`/`note_weights_pushed`/`forget_weights_node`/`weights_already_pushed`/
`partition_weights_nodes`（2 段 6 名）。门面 `X as X` ⇒ 全仓 `common.distribution.X(...)` 与 `from common.distribution import …` 一行不改。
**⚠ 关键切分判断**：① **账本按 6 名搬、不按 recon 的 2 节点搬**——`recon_god.py` 组3 只圈出查询/拆分两名，
但 `_WEIGHTS_PUSHED` 是**共享模块全局**（记/忘/清写它）⇒ 只搬两名会让孩子反向 import 门面（成环）或读到副本；
② `import base64` 随实现走（留守零引用；ruff `unfixable=["F401"]` ⇒ 手动删）；`BC_COLLECTOR` 单独搬、注释一分为二
（`BC_MODE`/`BC_WVER` 留守）；③ 组织边是留守 → 块的**单方向**（`post_weights_parallel`/`refresh_weights` 调账本），
由门面承接；④ **patch 面零迁移**（全仓都按门面解析 + 账本 dict 是同一对象）。
**两处名单面补登记**（不登记 = 判据瞎着）：`tests/test_layering.py` 的 `L0_TOP_MODULES` += 两新模块；
`nn-training/pyproject.toml` 的 `py-modules` += 两名（否则 editable 安装下 `import common.distribution` ImportError）。
**验证**：逐段逐字节对账（`tmp/verify_dist_clusters_exact.py`，跨度表独立复写 ⇒ 7+2 段全 `BYTE-EXACT`，`common.shard`
1 处既知插入=collector 注释行）；新守卫两个共 19 例（定义唯一 / 依赖面闭集+无环 / 不得碰上层包 / 门面对象恒等 /
**模块全局是活读取点** · 契约语义：三态清单选择 · 七条拒收+BC 败局跳过 · 落盘只写清单内文件+`manifest.json` indent=2 单次写 ·
v1/v2 双模 · kind 分桶 · note 幂等 · forget 范围/计数 · reuse/need 拆分）；门禁 nn **3224 → 3243 passed / 3 skipped**，
ruff + mypy（**520 → 522** 文件）绿；根 `bun run check` **2181 pass / 0 fail**；check-decisions 529 ids。
决策 → `DECISIONS.md` §2026-09-27-goalnn-dist-clusters-split；全文 → `engineering.md` §38。

### 2026-09-27 S5 第十二刀：评估「让位/份额（尾巴）策略」两处一起切 → `biz/eval_yield.py`（用户指令）

`biz/eval_local.py` **573 → 497 行**（−76），新模块 **`biz/eval_yield.py`**（186 行，**零依赖叶子**）：
① **旧家 L110–L226（117 行）逐字节搬入**——5 常量（`EVAL_LOCAL_SLOTS_DEFAULT` / `EVAL_LOCAL_RELEASE_GRACE` /
`EVAL_INFLIGHT_GRACE_SEC` / `EVAL_JOIN_SOFT_SEC_DEFAULT` / `EVAL_LOCAL_EARLY_EPOCHS_DEFAULT`）+ 7 判决函数
（`eval_join_soft_sec` / `eval_tail_overran` / `eval_local_early_epochs` / `local_gate_release_plan` /
`early_epoch_reached` / `hold_for_local` / `release_local_gate_if_starved`）+ 两段族注释；② **派发器三判决点提点**
（`eval_dispatch` 873 → 882）：尾段预留量 / 宽限强制释放点 / 在飞落账宽限 → `reserve_local_slots` /
`local_release_due` / `inflight_grace_cap`（**原式逐项等价**）。门面 12 块 `X as X` ⇒ 旧 import 路径一行不改；
判据读者（`trainer/eval_dispatch` / `trainer/loop_eval` / `trainer/batch_runner`）改从新家取（派发器**仍**依赖旧家拿执行面）。
**⚠ 关键判断**：① 「两处一起」= **一个所有者**，不是两刀（只搬一份 ⇒ 派发器里仍是第二份实现；只提公式 ⇒
判据仍留在 573 行运行器里）；② `EVAL_TASK_ATTEMPTS`（单局重试上限）留守运行器（执行重试，不是让位份额）；
③ 门面块放 `eval_local` 顶部门面区（`biz.eval_track` 之后）——isort 把连续 import 区当一个排序域，紧贴
`eval_rows` 门面块会要求把既有 17 块整段挪位（零既有块挪位是这条放置的理由）；④ 判据**零 patch 点**
（全仓无 `setattr` 到这些名；`e2e/test_run_rl.py` 的 `ed.hold_for_local` 仍在 `trainer.eval_dispatch` 命名空间里=同一对象）。
**三处既有守卫带日期改判/补登记**：`test_eval_track_split.py`（原先钉「尾巴策略留守」→ 收窄到执行面）·
`test_loop_eval_split.py`（DI 名单 `biz.eval_local` → `biz.eval_yield`）· `test_batch_runner_split.py`（import 闭集 +1）。
**验证**：逐段逐字节对账（`tmp/verify_eval_yield_exact.py`，独立复写；基线=动刀前快照，另做 HEAD 交叉验证：
搬走 117 行在 HEAD 版本里同样逐字节存在）；新守卫 19 例（定义唯一 / 执行面反向留守 / 零依赖叶子 / 无反向 import /
门面恒等 / 边缘重指+判决点迁移 / 让位真值表 · 无节点开闸 · 放行档三档+提前点 · 越窗与站等旋钮 · 三公式等价）；
门禁 nn **3243 → 3262 passed / 3 skipped**；ruff 全过；mypy **526** 文件绿；根 `bun run check` **2181 pass / 0 fail**；
check-decisions 530 ids。决策 → `DECISIONS.md` §2026-09-27-goalnn-eval-yield-split；全文 → `engineering.md` §39。

### 2026-09-27 S5 第十三刀：`plan_run` 18 节点交接面下沉 → `remote/plan_handoff.py`（用户指令）

`remote/plan_run.py` **1090 → 577 行**（−513），新模块 **`remote/plan_handoff.py`**（630 行，**L2**）：
**4 跨度逐字节纯搬**（562 行 / 26386 B）——A1/A2 = `TS_TREE_DIR`+`TS_CODE_ZIP_NAME`（`ITER_RETRIES` 夹中间留守）·
R2 `[83,543]` 461 行（`_log_default` · `verify_plan_file` · `RunContext` · `open_run_context`+8 种子助手 ·
`EVAL_ALTERNATE_WAIT_SEC`）· R3 `[708,803]` 96 行（评估装配三件）；新头=既知插入。`plan_run` 留 **18 名 `X as X`**
⇒ `remote/run_loop.py` 13 个 import 块与全仓调用点一行不改（门面链 `run_loop ← plan_run ← plan_handoff`）。
**⚠ 关键判断**：① **18 而非最小闭包 17**（纳入 `verify_plan_file` ⇒ 「校验→取包→上下文→装配」语义完整；块→外仍空已实测）；
② 评估生命周期被劈两半（装配走、驱动+收线留守）= 闭包代价 ⇒ 槽位契约定进新模块 docstring；③ `EVAL_ALTERNATE_WAIT_SEC`
**双命名空间**（留守读 `plan_run`、搬走读新家）⇒ 既有 patch 点零迁移；④ 依赖单向（新模块零引用 `plan_run`；引擎侧 9 节点引用交接面）；
⑤ `_setup_cloud_eval` 的 `from remote.offline_eval import …` 是**函数内延迟 import**（offline_eval L1 ⇒ L2 方向合法，守卫白名单登记）。
**分层**：新模块 **L2**；`plan_run` **2 → 3**；`tests/helpers/remote_dag.py` 三处同步（表项 + L2/L3 段文字）——自动秩校验先红后绿。
**验证**：逐段逐字节（`tmp/verify_plan_handoff_exact.py`：4 段全 `BYTE-EXACT` + 原家 `ABSENT` + 18/18 门面齐备）；
新守卫 `tests/remote/test_plan_handoff_split.py` **8 例**（定义唯一 / 依赖面闭集（8 stdlib + 8 仓内）/ 禁反向 import / 门面恒等 /
`verify_plan_file` 四道门功能性 / 双命名空间事实 + 写入面四槽守恒）；改判 `test_plan_run_split`（10 → 11 例）·
`test_worker_offline_cap`（import 改指新家）· `remote_dag`。门禁 nn **3262 → 3271 passed / 3 skipped**；ruff 全过；
mypy **526 → 528** 文件绿；根 `bun run check` **2181 pass / 0 fail**；check-decisions **531 ids**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-plan-handoff-split；全文 → `engineering.md` §40。

### 2026-09-27 S5 第十四刀：`offline_boot` 交付面 + standalone 运输面 → `remote/offline_deliverable.py`（用户指令）

**量测先行**（用户点名「取包/交付是否真独立分量」→ 判决）：取包面闭包 **47/86 节点 · 743 行 ≈ 文件本体**、
心跳/租约簇 29 节点 · 349 行 ⇒ **两都不切**；**交付面 = 引用图上唯一干净闭集**（13 节点 · 179 行，块→外为空，
4 条 facade 边，外部触发 = notebook 中途取回格）。障碍是 **standalone 运输面**（本文件是 notebook 从 GitHub raw
单文件拉起的引导单元）⇒ 用户指令「设计运输面（懒装载 + 拉取列表 + 守卫改判），再落刀」。

**设计（`plan/…§5.7.5`）= 懒装载 + PEP 562 `__getattr__` 门面**（三条守卫钉死顶层 import 兄弟文件：
DAG 的 `TOP["remote.offline_boot"] == ∅`（`Try` 不改变 AST 深度）+ 单文件真 exec 守卫 + common_layer 清单）：
① `_load_deliverable()` 按 `("offline_deliverable", "remote.offline_deliverable")` 装载（**引导兄弟优先**——
与刚刷新的 offline_boot 同源；与 `_load_tailscale_boot` 的 remote-first 有意不同）；② 门面只转发**闭集 13 名**
（未知名照常 `AttributeError`）⇒ notebook 取回格与 ~15 处用例**零迁移**；③ 内部 **6 处调用点**
（`run_one_course`×3 / `_queue_work_dir` / `resolve_courses` / `run`）改走 `_load_deliverable().x(…)`。

**刀口**：`remote/offline_boot.py` **1801 → 1647**（−154）· 新模块 **227 行**（stdlib-only ⇒ L0）；
**2 跨度逐字节纯搬 198 行**（A `L94–100` 常量 7 行 · B `L1008–1198` 交付块 191 行）+ 新头（既知插入）；
notebook **3 处**（主引导格 fetch 名单 + pop 名单、取回格 raw 兜底改双文件循环）；`remote_dag` 两处登记
（LAYERS `0` + `STANDALONE_BOOT_MODULES`）；`test_common_layer` 清单 +1。

**验证**：逐段逐字节（`tmp/verify_offline_deliverable_exact.py`：A/B 各 ×1 + 原家 13 处定义/常量 `ABSENT` +
门面/调用点/口径到位）；新守卫 `tests/remote/test_offline_deliverable_split.py` **12 例**（定义唯一 / **stdlib 闭集** /
禁反向 import / 门面恒等 / 门面闭集 + 笔误不吞 + 无套娃门面 / 装载顺序 + 命名语义 / notebook 名单 ×2）；
改判 `test_offline_boot`（exec 守卫升级为**引导文件集形态**（真 exec + 真调交付面）+ 反例
「兄弟缺失 ⇒ 装载照过、首次用交付面 `ImportError` 点名」；双生常量守卫改指新家 + `is` 恒等）·
`test_offline_notebook`（名单 +1）· 门禁 nn **3271 → 3285 passed / 3 skipped**；ruff 全过；
mypy **528 → 530** 文件绿；根 `bun run check` **2181 pass / 0 fail**；check-decisions **532 ids**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-offline-deliverable-split；全文 → `engineering.md` §41。

### 2026-09-27 S5 第十五刀：`gate_check` 求值器接口 —— 输入面 + 判决面成家 → `biz/gate_inputs.py` + `biz/gate_judges.py`（用户指令「next」= §5.7.3 入口）

**量测（反判据收敛）**：1412 行 `biz/gate_check.py`（`recon_god` 口径「1 组 31 节点巨团」）在**引用图**上实测
**47 节点 = 46 单团 + 1 孤立**（`sum_train_samples`——模块内零引用，只被测试/账本线 import）；三面判据
（块→外为空）：判决项面 **25 节点 / 588 行**、输入读数面 **14 节点 / 288 行**、常量面 6 节点。原反判据「按链切
不动 ⇒ 必须先设计求值器接口、是真设计改动」**收敛**：「独立求值一项」（`only_kinds=("duty",)`）已由
`evaluate` 暴露（`loop_guards_gate` 在用）⇒ 设计点只剩**共享输入模型归属**（`EvalRow`/`_row_from_summary`/
`_num`/`BudgetInfo` 归输入面）⇒ DAG 单向 **`gate_inputs` ← `gate_judges` ← `gate_check`**（无环；运行期零
`biz.config`——判决面只 TYPE_CHECKING 取 `GateRule`/`GatesSpec` 类型，`_lazy_config` 仍归引擎）。

**刀口**：`biz/gate_check.py` **1412 → 622**（引擎：`evaluate` 两趟 + ADVANCE 附加条件 + CLI + 全量 37 名门面）；
两个新家——**`biz/gate_inputs.py` 379 行**（15 名：`EvalRow`/`BudgetInfo` + 行归一化 + `read_trend_rows` +
`first_*`/`count_*`/`sum_*` 事件扫描 + `load_override` + 常量/异常；**stdlib-only 叶子**）· **`biz/gate_judges.py`
572 行**（22 名：`_Partial`/`_Ctx` + 统计助手 + 11×`_eval_*` + `_route_by_completion` + 注册表 + **判决项接口**
`_eval_one`）。**10 跨度逐字节**（输入面 5 段 `L85–91`/`L96–98`/`L103–126`/`L128–166`/`L277–556`；判决面 5 段
`L93–94`/`L256–274`/`L559–1045`/`L1050–1057`/`L1249–1270`）+ 新头/门面（既知插入）；既知改动：docstring
分层指针 · TYPE_CHECKING 块删（`GatesSpec` 只由 `evaluate` 内 `_lazy_config()` 解包，ruff F401 实提）·
清 `math`/`Callable` 空转 import · 缝空行归一。

**验证**：逐段逐字节（`tmp/verify_gate_faces_exact.py`：10 跨度各 ×1（原文 sha `233caa57…`）+ 原家 40 名定义
与 `GateRule`/`math` 零残留 + 门面 37 名 `is` 恒等 + 新家导入 `biz.config`/torch/numpy 均不入 `sys.modules`）；
新守卫 `tests/worker/test_gate_inputs_split.py` **12 例** + `tests/worker/test_gate_judges_split.py` **11 例**（定义唯一 /
依赖闭集 / 禁反向 / 门面恒等 / 判决项接口（注册表 6 键 + 5 分支 = 11 kind + 兜底 dormant）/ 运行期纯净子进程 /
不进 `remote_dag.LAYERS` / 读数与分流语义）；改判 `test_gate_check` 的 purity 扩面三模块；`trainer/__init__.py`
速览补三行（`gate_check.py` 此前未列）。nn 门禁 **3285 → 3308 passed / 3 skipped**（+23）；ruff 全过；
mypy **530 → 534** 文件；根 `bun run check` **2181 pass / 0 fail**；check-decisions **533 ids**。
决策 → `DECISIONS.md` §2026-09-27-goalnn-gate-faces-split；全文 → `engineering.md` §42。

### 2026-09-28 python 源文件 LOC 预算护栏：单文件 < 1000 行（`tests/` · `e2e/` · `offline_boot` 豁免）

**用户指令**：「给 python 门禁加一个测试：python 源文件 loc 必须 < 1000 行（tests/e2e/offline_boot 除外，
不包括注释/空行/doc）」⇒ 新增 `nn-training/tests/test_python_loc_budget.py`（**5 例**）进 python 门禁。

**口径**：`LOC = 物理行 − 空行 − 纯注释行 − docstring 行`（docstring = 模块/类/函数首语句字符串，AST 判定；
语句级多行字符串——长 `log()`/`raise` 文案——**算代码**）；**≥1000 即红**。扫描面 = `git ls-files --cached
--others --exclude-standard '*.py'`（跟踪 + 未跟踪未忽略；用 git 而非 os.walk 是因为 `nn-training/tmp/` 那类
被 ignore 的备份副本实测 600+ 份不该进预算，而未跟踪的**新源码必须进**）。

**量测（`tmp/measure_loc_budget.py` / `tmp/measure_offline_budget.py`）**：该口径下全仓只有 4 个 ≥1000 ——
`tests/remote/test_remote_iter.py` 1309 · `tests/remote/test_remote_ppo.py` 1258 · `e2e/test_run_rl.py` 1245 ·
`remote/offline_boot.py` 1127（次高危 `trainer/batch_runner.py` 949 · `trainer/dispatch.py` 936）。落刀后扫描面实测
**542 个 .py ⇒ 入预算 258 · 豁免 284 · 超限 0**。

**豁免（用户裁定，走 `ask_user`）**：测试层 `tests/` · `e2e/` **整体**豁免（测试体量是「覆盖了多少场景」的函数，
压它等于少测）；`remote/offline_boot.py` 单文件豁免（standalone 运输单元、§5.7.3 实测拆不开）。被否决：
超限文件进白名单不设上限 · 棘轮（登记当前 LOC 只准不涨）· 本轮拆那两个超限测试。**顺带实测并否决「压缩
`offline_boot` 行数」**：行预算（分类重叠）空行 187 / 纯注释 94 / docstring 292 / 多行字符串 75 / `log()` 跨度 153 /
`raise` 跨度 58，真实控制流 **902 行 / 825 条语句 = 2.00 行/语句**（已是地板），字面重复 ≈ 0 ⇒ 安全压行上限
~15–25 行（1%），代价是碰 95 条事故诊断文案 ⇒ 不做。

**三条防漂移断言**：扫描必须真扫到货（≥200 非豁免文件 + 锚在列）· 豁免目录名只许命中 `nn-training/tests/` ·
`nn-training/e2e/`（防当通配符）· `offline_boot.py` 降到阈值下即红（**豁免会过期，必须收回**）。

**验证**：主判据 **0.30s**（per-test 预算 5s/30s）；先用临时探针 `rl/_loc_probe.py`（1103 LOC + 100 行注释 +
100 空行）确认真会红并点名，再删探针。nn 门禁 **3308 → 3313 passed / 3 skipped / 0 failed**；ruff 全过；
mypy **534 → 535** 文件；根 `bun run check` **2181 pass / 0 fail**。
决策 → `DECISIONS.md` §2026-09-28-goalnn-python-loc-budget；全文 → `engineering.md` §20「决策正文归档」·
锚 `### §2026-09-28-goalnn-python-loc-budget`。

---

## 2026-09-30 · nn-training 按角色重组（六包 `common/biz/worker/remote/hub/trainer`）—— 第一刀 hub 出包

**用户指令**：「根据功能模块区分重组 nn-training 的文件架构：hub/trainer/worker/remote/biz/common」。
plan → `plan/nn-training-module-reorg.plan.md`（六桶判据 / 完整映射表 / 契约与守卫迁移清单 / 五刀刀序）。

**目标偏序**（`nn-training/tmp/reorg_audit.py` AST 扫全部 import 边，当前违规 **0**）：
`common(L0) < biz · 算法栈(L1) < worker(L2) < remote(L3) < hub · trainer(L4)`。
**Q5 改判**：算法栈（`models/ppo/data/train/scripts`）**保持顶层并列**、不并入 `trainer/` —— 因为
`remote/bc_job.py` / `remote/train_core.py` 就是云机 worker 的训练/评估执行体（import `ppo.engine` /
`data.weights_io` / `train.bc`），并入会长出 9 条 `remote → trainer` 向上边。

**第一刀**：`remote/hub/*`（27）+ `remote/hub_server.py` → `hub/` + `hub/server.py`（`-m hub.server`），
三个站在门面上的运维工具（`smoke_loopback`/`tunnel_ab_probe`/`backfill_offline`）随刀进 `hub/`；
`remote/hub_client.py`/`hub_http.py` 不动（客户端侧线路）。机械改名 88 文件 / 594 处。

**四个坑（测试绿也会瞎掉）**：① `remote_dag` 的 AST 门禁 `startswith("remote")` ⇒ hub 内部边一条不进
账本（改 `ledger_package()`，账本扩成**一账两包**）；② 按**叶子名**判据被 `http.server` 撞车 ⇒ 改点分全名；
③ `test_subproc_util` 的 spawn 标记要改成**带尾逗号的 argv 元素**形态（`hub.server` 现在也是账本键/名字比较的串）；
④ **⚠ `__file__` 深度算术静默失效**（`hub/store_offline.py` 的 `parents[3]` → `parents[2]`）——
搬 python 文件必须 grep `parents[`。**反探针**：改名 `hub/http_face.py` ⇒ hub 守卫响亮红。

**读数**：nn 门禁 **3368 passed / 3 skipped**（mypy 545 文件零问题）；根 `bun run check` **2277 pass / 0 fail**；
dashboard typecheck 绿 + **1225 pass / 0 fail**。
⚠ **环境漂移**：venv 的 ruff 比仓库基线新 —— `ruff format .` 会重排 296 个无关文件（**别跑**）；
`ruff check .` 在 HEAD 上就有 1 条预存 `N999 tools/tpu-probe.py`。本刀只跑 `--select I`。

决策 → `DECISIONS.md` §2026-09-30-goalnn-nn-training-module-reorg；
后续刀序 → `plan/nn-training-module-reorg.plan.md` §5（刀 2 `common/` · 刀 3 `worker/` · 刀 4 `biz/` · 刀 5 `trainer/`）。

---

## 2026-09-30 · 刀 4：`biz/` 出包 —— 纯逻辑从 `rl/` 分家（64 模块）

**判据是机械的**：`biz/` = `rl/*.py − RL_ORCHESTRATION`（那张快照在刀 3 前就存在）。分家后
`rl/` **只剩编排**（37 个模块，每个都可达 `remote|worker`）—— `test_rl_holds_only_orchestration_modules`
按**集合相等**钉住它（往 `rl/` 丢一个不达远端的纯逻辑模块，快照对账的两个方向都看不见）。
刀 2/3 的读数与坑见 `docs/nn/engineering.md` §51；本刀全文见 **§52**。

**读数**：nn 门禁 **3372 passed / 3 skipped**（mypy **547** 文件干净）· 根 `bun run check` **2277 pass / 0 fail** ·
dashboard typecheck 绿 + **1225 pass / 0 fail**（另加新增的 4 例 spawn 路径守卫）。
审计器（三源 + `biz` 桶）向上边 **0**；反探针五条全部能红（搬回 / 越层 import / 删族文件 / 未登记 biz / spawn 旧路径）。

**五个坑（都是「测试绿也会静默瞎掉」）**：① ⚠ **扫描面缩水是哑的** —— 8 个守卫用
`(ROOT/"trainer").glob("*.py")` 扫「谁调用/谁定义 X」，搬走后判据变**永真** ⇒ `source_scan` 新增
**两棵树口径**（`logic_py_files`/`logic_module`/`logic_dotted`），7 处扫描面 + 4 处目录元组 +
7 处 banned 名单统一补齐；② ⚠ **一族文件可以跨两棵树**（`trainer/loop_guards.py` + 4 个
`biz/loop_guards_*.py`）⇒ 用两棵树解析器，判据与期望值一字不改；③ ⚠ **模板串**（`f"rl.{x}"`）
文本通行证看不见（7 处人工）；④ ⚠ **逗号分片路径** `path.join(NN_TRAINING, 'rl', 'x.py')` 漏掉 1 处
**真 spawn**（dashboard「导出 replay」，点按钮才炸）⇒ 补 `dashboard/tests/python-spawn-paths.test.ts`；
⑤ ⚠ **改写范围漏了仓库根 `tests/`**（跨语言 SSOT 按路径读 `rl/reward_library.py`）⇒ 根 check 1 红。

决策 → `DECISIONS.md` §2026-09-30-goalnn-nn-training-biz-package；
契约与模块表 → `nn-training/biz/__init__.py`（64 模块按域分组）· `nn-training/trainer/__init__.py`（编排 37）。

## 2026-10-01 · 孤儿租约早收（claim 后零心跳超宽限即回池）

事故形状：claim POST 到达即设租约、`lease_token` 随响应一起丢（客户端读超时）⇒「hub 有主、世上无人
持有 token」，job 在 peek 里隐身满一个 `CLAIM_TTL_SEC=300`（bc-human-retrial `460b637b`，V100 烧 5 分钟）。
新规则：**自 claim 起一次成功心跳都没有**且静默超 `ORPHAN_GRACE_SEC=180` ⇒ 判孤儿，走同一条
`_collect_expired_locked` 回池 + 账本事件 `lease-orphan-reaped`；push 腿（`push:` 前缀持有人）豁免、
离线腿不适用、手工认领适用。判据只认「心跳」这一个既有信号（`/start`/`/ready`/`/result` 都不碰租约，
hub 也看不到下载进度）；判据唯一（`_lease_state` 四态 + `_lease_held` 布尔），池过滤 / 认领闸 /
`lease_worker` / `inflight` 四处同源——只在池过滤加判据会做出「池里看得见、claim 说 held」。

决策 → `DECISIONS.md` §2026-10-01-goalnn-lease-orphan-reap；
全文 → `docs/nn/remote-transport.md` §52；计划 → `plan/lease-orphan-reap.plan.md`（2026-10-01 评审修订版）。

## 2026-10-02 · transfer-residual：让路税清算（条件让路 + 按传输累计上限 + 观测补面）

触发：查「预取 / 多课程单 worker 下还能否再压缩或隐藏传输」⇒ **隐藏已到位**（稳态 `in` 4.7%、`out` 全
overlap、余量 ≈9×），残余是**控制面让路税**：L2 真机实测一条 wire 传输总让路 **7.0s（38%）**，其中
**5.5s 压在关键路径**（`payload` 1.0 + `blob:opt` 2.0 + `blob:ref` 2.5）；两本账互证（`yield=14` × 0.5s
步长 = 五条「让路合计」行之和）。机制：`_request` 包住整段控制请求、`_read_body` 每 256KB 分片都
`pace`，取消环（1.5s 一个、在途 ~1.0s）+ 预取 peek 让占空比 ~80%，而旧预算只是**单次调用** 5s、
**一条传输无累计上界**。

落地（W0–W3）：S1 条件让路（最老在途 ≥ `YIELD_AFTER_SEC_DEFAULT=1.0s` 才让）+ S2 按传输累计 ≤5s；
库层缺省保持老语义（0.0/None），worker 显式装配 `--bulk-yield {auto,always,never}`、`worker_loop`
**finally 还原**（`_BULK` 是进程单例——不还原会让同 pytest 进程的 `test_control_plane_bypass.py`
确定性变红）。观测：label 带优先级（`bulk payload/P1`）/ `p0_p50` 上 wire 行 / 排队按 jid 归属
（`take_wait`，`_get_with_retry` + `post_result` 两处）/ payload 预取命中入账（在 `run_one_round`
take 命中处，避开 push 腿共用的 `_ensure_payload`）/ 每轮 prefetch 摘要（`stats()` 首个生产读者）/
`wire_report.py` 新增调度账聚合（旧日志无 `p0_p50` 兼容）。注释：`hub/queue_claims.py`「深度」段改为
「有效窗口 = `min(depth, 开课数)`」。**预注册门槛（不改默认）**：预取单会话 `hits/misses < 1:3` 且
白传 ≥3MB ⇒ 默认 `--prefetch-depth 0`；`--bulk-yield never` 臂下 `p0_p95`/`p0_max` 劣化 <2× ⇒ auto 的
total 收到 0。

读数：定向 remote 5 文件 **92 passed**（新 19 例 + 1 例加断言，既有断言零改动）· nn 门禁
**3483 passed / 9 skipped**（ruff + mypy 全量）· 根 `bun run check` **2345 pass / 0 fail**。
决策 → `DECISIONS.md` §2026-10-02-goalnn-transfer-residual-yield；全文 → `docs/nn/remote-transport.md`
§53；计划 → `plan/transfer-residual.plan.md`（2026-10-02 评审修订版，P0-1..P0-5 已修）。

## 2026-10-02 · eval-final-round-and-dropped：收官轮 eval 缺失 + dropped 局静默丢失

触发：h4-aim-k25 真机产物——it40（收官轮）训练期间 eval **零局**（手点 evalA 才有）、it0 缺 8 局、
it25 缺 2 局且原因不进账本（`[eval] drain:` 日志零命中）。三条根因：① 收官 drain 只接在单课程
前台入口（`run()` 尾部），多课程 serve 的 `finish_course` 没有 drain，而延迟派发只到 it-1 ⇒
最后一轮权重没有 eval 路径；② `seen`（认领）在 `record()` 落盘前计数且异常逃逸（**不能下移**
——tail-race 副本可在原件 record 进行中结算，下移就双计）；③ 缺口既不补派也不带原因。

落地（W0–W5）：`finish_course(it, *, drain=True, block=True)` 收编收官 drain（serve RL 传
`block=False`、BC 按 kind 分派**不带 kwargs**；`run_complete` 只保证已派发）；claim/landed 拆分
（`_settle_complete` 与 `settle_eval_summary` 只认落盘，参数 `seen`→`landed`、`dropped` 公式
一字不动）；`record()` 失败本线程重试 + `record-failed` meta + WARN；summary 新增 `missing`
（有界 20 + 原因 record-failed/node-failed/undispatched）；收官 drain 按归档权重**逐轮升序**补
（不再 `cand[-1]`），每条早退一行日志（G8）。

读数：nn 门禁 **3494 passed / 9 skipped**（ruff + mypy 全量）· 新增/改写用例 10 例
（`test_finish_course_drains_eval.py` 5 · resilience 3 · eval_timing 2（多轮 + G8）· gate_inputs
兼容 1）。决策 → `DECISIONS.md` §2026-10-02-goalnn-eval-final-round-and-dropped；全文 →
`docs/nn/engineering.md` §61；计划 → `plan/eval-final-round-and-dropped.plan.md`。

## 2026-10-02 · course-pill-precision：pill 精确化（hub 派发面 + 龄 + 预取窗口）与只读读面收官判据

触发（用户当日两条）：① 顶部课程 pill 的在线课只说本地进度词（本地「在等什么」五态）
——h4-aim-c0 的现场（首个 job 被一个误启持有者**有心跳地**按住 27 分钟：真 GRAD 33.7s，对照
h4-aim-k10/k25 同配置 2.7/3.3 分钟）在 pill 上只会说「等回传」，与正常回传无法区分；②
h4-aim-k10/k25 跑满 40 轮仍显示「推进中」——只读读面（`run_rl_cluster --json`）按盘重建计划
却拿不到 `iters` 预算 ⇒ `state='done'` 这条分支在 RL 课上从未点亮。

落地三条链：

- **hub 观测面**（纯加法）：inflight 行补 `claimed_ago`/`computing_ago`（缺失 → null 不编 0）、
  顶层补 `peeked_courses`（窗口 60s；只在 `peek_jobs` 返回候选时记，`claim_next` 不记）→
  `docs/nn/remote-transport.md` §54；
- **训练侧只读面**：收官判据单点 `worker/loop_tasks.budget_exhausted(it, iters)`，
  `LoopRunner.planner` 行为逐字节不变，`plan_course(iters=)` 跑满 ⇒ 空任务表 ⇒ `QUEUE_DONE`，
  `waiting_state(finished=)` ⇒ `WAIT_IDLE` + 诚实文案；读不到 iters ⇒ 0（保持今天行为）→
  `docs/nn/training-stack.md` §28；
- **控制台 pill**：新四态（卡住 Nm r / 等回传 Nm y / 排队·无人取 y / 预取中 g）+ 龄 + jid 连接
  （本机/远端一分为二）+ 未登记持有者点名 + 三态 `peeked` 退化（旧 hub ⇒ 「排队中」，不编
  「无人取」）；控制台不新增判据（收官走 python 的 `state='done'`）→ `docs/nn/console.md` §22。

读数：nn python 门禁 **3505 passed / 9 skipped**（ruff + mypy 全量）· dashboard **1264 pass /
0 fail**（typecheck + 三 bundle ok）· 根 `bun run check` **2345 pass / 12 skip / 0 fail** ·
`bun run build` 过。决策 → `DECISIONS.md` §2026-10-02-goalnn-course-pill-precision；计划 →
`course-pill-precision.plan.md`（2026-10-02 评审修订版：P1–P4 已并入并实施）。

## 2026-10-02 · worker-contribution-view：并行 worker 贡献度面板（承接面归属 + 两组不合并 + 课程矩阵）

触发（用户 2026-10-02）：「七门课程并行训练，两个云端 worker 一起领任务……想要在 dashboard 上一个
直观的方式查看多个并行 worker 的贡献度」。评审先纠两处结构性错误：W2 的旧锚点
`hub/store_leases.py::mark_completed` 是**生产零调用者的死代码**（真实 `job_completed` 写手 = 训练侧
`remote/hub_client.py::mark_job_completed`，且无 worker 身份），身份只在 hub **承接结果那一刻**可得；
`DayBucket` 无 per-event 时间戳 ⇒ 滚动窗不能从日桶投影，须有界子日事件环。plan →
`plan/worker-contribution-view.plan.md`（2026-10-02 评审修订版）。

落地（W0–W4）：

- **W2 hub 承接归属**（唯一 trainer/hub 侧改动；扩事件不新建账本）：`job_lifecycle.post_result` 结果 POST
  补 `X-Worker-Id`；`push_dispatch.accept_result` 成功写 `job_result_accepted`（worker = 显式传入 ∪ 当前
  租约持有人，push 腿 `push:` 前缀）；**三条 409 路径**（`hub/result.py` 早退 / `store_result` 首写锁定
  失败 / push 腿输家）写 `job_rejected`；事件与训练侧 `job_completed` 同册（`tmp/<课>/training_log.jsonl`），
  读侧按 `job_id` join。→ `docs/nn/remote-transport.md` §55；
- **W1 纯函数 + 滚动窗**：`web/view/contribution.ts`（份额分母组内自洽 / 单点标记 / 缩略 / `fmtShare`
  缺数据「—」）；`pool-types` 新增 `30m`/`2h` 滚动窗（既有 4 档一字不动）；`pool-history` 新收**有界子日
  事件环**（`ROLLING_KEEP_MS`=2h+10min、每节点 `ROLLING_CAP=20000`、触顶 `rollingTruncated`）+ `byCourse`
  课维度 + `projectCourseBreakdown`（按课名，不用 `it`）；
- **W3 装配 + UI**：`server/contribution.ts`（fs 唯一实现：采样聚合 ⊕ PPO 账本尾部 join ⊕ 在飞；memo 仿
  `aggMemo`，不新增缓存层）+ `WorkerContribution.tsx`（full：份额列表 ⇄ 课程矩阵 + 脚注；compact：首页
  一行）+ 接入 `NodeStats`/`NodePills`/`app.tsx` + `theme.css`（份额条纯 CSS `data-w`，无内联 style）。
  → `docs/nn/console.md` §23；
- **W4 文档**：console §23 · remote-transport §55 · 本条 · DECISIONS。

**★ 全量门禁抓到一条定向没覆盖的真回归**：`_append_attribution` 的无身份回退曾直接调 `hub.lease_worker`
——而它住在每课 `_JobStore`（`_HubQueue` 没有）⇒ 旧 worker（结果 POST 不带头）的结果面直接 **500**。
修法：经 `_store_of` 解析归属课程再取租约持有人；`test_remote_ppo.py` 两条既有用例 + 新回归用例
（`test_missing_worker_identity_falls_back_to_lease_holder`）钉住。

读数：dashboard typecheck 绿 + **1277 pass / 0 fail**（新 12 例）+ 三 bundle ok · nn 全量门禁
**3511 passed / 9 skipped**（ruff + mypy 571 文件全量；新 6 例 = 归属 5 + 回退回归 1）· 根 `bun run check`
**2345 pass / 12 skip / 0 fail**（122509 expect；check-decisions ok 584 ids）· `bun run build` 过。
决策 → `DECISIONS.md` §2026-10-02-goalnn-worker-contribution；全文 → `docs/nn/console.md` §23 ·
`docs/nn/remote-transport.md` §55；计划 → `plan/worker-contribution-view.plan.md`（评审修订版 W0–W4）。

## §25 控制台请求路径「零聚合」+ 聚合增量入账（plan/dashboard-reload-perf，2026-10-03）

用户报「dashboard 重载 ~10s + 进程常驻 1GB」。三条根因：① 贡献度缩略在 `/api/state` 里**裸调**
`aggregateNodeHistory()` + `buildContributionView()`（违反 WC-plan §1.3「挂既有 SWR」）；② 聚合
`walk()` 在 memo 判断之前（命中 memo 也付 37ms），`≤2MB` meta 走 `readFileSync(...).split('\n')`
（27MB 文本 → 13 万行数组，峰值 +116MB）；③ PPO 侧 `listCourseLedgers()` 无 memo 且判定在读取后。

修法（三件一起做）：R1 缩略挂 `FleetProbes.contributionBrief`（后台顺手产出，`inflight` 走
`peekHubAdmin()` 上一拍值）；R2 聚合改「扫描 memo + 每流增量入账（未变零读 / 变大读分块游标增量 /
回退与水位后退全量重建）+ 可合并桶归并（尾窗口引理，流序 = 目录名 canonical）+ 水位 pending 翻转」，
truncated 流保持尾部重建；R3 `scanPoolStreams()` 一次 walk 两组候选，PPO 与采样共用。

读数：dashboard typecheck 绿 + **1333 pass / 0 fail**（新 16 例：G1 零聚合 3 + G2/G3/G4/A2/扫描 memo/
尾窗口/sources 展示序/结构守卫 8 + W3 三条 + W4 一条 + 同源一致性 1）· `bun run build:ui` 三份 bundle ok · 根
`bun run check` **2345 pass / 12 skip / 0 fail** · `bun run build` 过。验收工具 `dashboard/tools/perf-probe.ts`
（人跑；基线数字落 plan §1/PR —— 本检出 tmp 为空，真机复测交用户终端）。
决策 → `DECISIONS.md` §2026-10-03-goalnn-request-path-zero-aggregate；全文 → `docs/nn/console.md` §25；
计划 → `plan/dashboard-reload-perf.plan.md`（评审修订版 A1–A6）。

## 2026-10-04 · offline-eval-backfill：云机没回传 eval 时 hub 端用 LAN 集群补评（+3 宽限 / 收官立即）

触发（用户 2026-10-04 逐字）：「离线课程，如果云机未回传 eval 结果（云机可能未启用 eval 以节省
CPU/墙钟），则在 +3 it 的权重回传后在 hub 端使用 LAN 集群跑 eval。注意课程完成指定轮数收官时，
如果最后一轮需要 eval，也要执行。」

语义：评估点 `N`（`N>=1 ∧ N%eval_every==0`）在「该 run 已回传 `max(its) >= N+3` 仍无
`(N, W16)` 的 `eval_summary`」时补评（宽限）；`end_it_reached` 时最后一个评估点
（`floor(it_end/eval_every)*eval_every`）缺读数 ⇒ 立即（收官，不等 +3）。`W16` = 回传轮
`row.json.weights_fp` 前 16 位（缺则按权重文件 sha256 现算）；权重取回传树
`remote-jobs/offline/<run>/it-NNN/`（首选）或交付镜像 `deliver/<run>/it-NNN/`。动作 = 复用
`launchEvalA`（唯一启动点：LAN 节点 + 本机份额、同语料/账本/去重），每拍至多一个（单槽互斥）、
账本幂等、失败 10min→2h 指数退避。落点：`dashboard/src/server/offline-eval-backfill.ts`（新）·
`api/overview.ts` 增 `offlineResults`（同一次 `/admin/offline` 探测，零新增网络）· `server.ts`
60s ticker + 启动先跑一拍。逃生阀 `BCITY_NO_OFFLINE_EVAL_BACKFILL`。

读数：dashboard typecheck 绿 + **1375 pass / 0 fail**（新 21 例）· 根 `bun run check`
**2353 pass / 12 skip / 0 fail** · `bun run build` 过 · 必红自查两条（删宽限判据 / 删收官分支）
均验证会红。决策 → `DECISIONS.md` §2026-10-04-goalnn-offline-eval-backfill；全文 →
`docs/nn/console.md` §28；计划 → `plan/offline-eval-backfill.plan.md`。

## 2026-10-05 · course-startup-recover：课程起不来要能看见，且改好就能自动生效

用户报障（全文与时间线 → plan §1/§6.3）：`x20-adv3-open-r2` 因课程文件自相矛盾被共享 trainer 整课
跳过；人改好文件 + 控制台停→开都无效，唯一出路是重启 trainer（会打断所有并行课程）。

**P0**：跳过从**终身黑名单**改成**带判据指纹的待重试表**——课程文件身份（mtime_ns+size）/ 开课标记
mtime / 按课程锁签名 / 账本 mtime，**按跳过来源取事实**（config / lock / enqueue）；一步级 SystemExit
那族走**第二条通道**（重置队列 + 保留 runtime/引擎，不重建——C-0 无限 RETRY 前科）。不刷日志：
判据没变不重试；「停→开」（标记 mtime 变新）也是复活信号。**P1**：只读判据 `openable`（校验链抽中立
模块 `worker/course_args.py`（分层法定的训练栈家——`trainer/` 只许编排，且不得与 `biz/course_spec.py` 撞名），serve 与只读视图同源；解 F3 依赖倒挂）+ `waiting.kind='blocked'`
（不再把起不来的课报成「无外部等待，下一步 …」）+ 控制台 pill 红 / 告警坞第 8 类（按课过滤；ack 身份
= reason ⇒ 改了但没修好会再弹；不给一键恢复——恢复是改文件）。边界明说：判据是「配置不可开课」不是
「serve 已跳过」；rl-config/env/权重类不覆盖；复活发生在全局空转拍。

读数：nn py-safe 相关全绿（P0 9 新 + openable 6 新 + waiting/override/wiring 回归）· 先红后绿与两条
反向探针均已跑（判据 stub ⇒ P0 失效；openable ok=true ⇒ 告警消失）· 只读 `--json` 冷算 1.2–2.4s、
import 链 0 torch · `bun run check` 2373/0 · dashboard typecheck + 1398/0 + 三份 bundle · `bun run build` 过。
决策 → `DECISIONS.md` §2026-10-05-course-startup-recover；全文 → `docs/nn/training-stack.md` §29 /
`docs/nn/console.md` §30；计划 → `plan/course-startup-recover.plan.md`。
## 2026-10-05 · offline-online-status-switch：pin 重新成为硬意图 + 离线 = 等待（两条报障闭环）

触发（用户 2026-10-04 报障×2）：①「手动切成在线不稳定」——切了被离线盘抢回（pin 坍缩）、
进离线撤掉的在线 job 回来不复活、本机腿把离线等待当收官写假 `run_complete`；②「云机掉线后
课程永久停摆」——死租约冻结全池、无包腿烧满导包触发后新盘拿到 `give_up` 并被 skip（本会话
永久放弃）、导包窗口无主也不释放（`runtime ... 100%` 假信号）。

语义（六轮评审修订版，行为变更 21 条）：五档权威 `authority_of`（pinned_online 重新拦
claim/seize/翻模式；冷课读盘派生；读失败退缺省）；租约六态 `lease_verdict`（revoked 不看
身份、顺序 expired→revoked→mine→stale→foreign；静默 180s 自动接管；心跳刷 `beat_at`；墓碑
形状定死）；busy 活性口径（stale/revoked/expired 不算忙，窗口锚 `claimed_at`）；`_cancelled`
按账本净态对账（生产写者 = `remote/hub_client.publish_job`）；停课/冷课 claim 409 `not_offline`；
新一轮交接（换主 ∨ 超 900s）重置导包触发；`pinned_online` 回传不推权重 + 段末包身份盖章；
`resolve_courses` 返回 `(picks, blocked)` + blocked 不占 idle 预算；训练侧离线 = 等待（不写假
`run_complete`，回灌带 pin、复原前提 `--rollout-src auto`）；控制台单一派生 `courseStatus` +
意图表 v2 + last-known-good（失败保旧值带 stale 标注）。存量盘点：全仓零真课程派发记录 ⇒ 无
存量 pinned 课。

读数：nn python gate **3672 passed / 9 skipped**（ruff+mypy 过）· e2e **112 passed**（T1–T8）·
dashboard typecheck 绿 + **1400 pass / 0 fail** + 三份 bundle ok · 根 `bun run check`
**2361 pass / 12 skip / 0 fail**。决策 → `DECISIONS.md` §2026-10-05-goalnn-offline-online-status-switch；
全文 → `docs/nn/remote-transport.md` §67 / `docs/nn/console.md` §31；计划 → `plan/offline-online-status-switch.plan.md`。
**2026-10-06**：Console 「导出回放」换轮架构重做（`plan/replay-export-eval-round-picker.plan.md`）。
评审二轮 → `plan/replay-export-eval-round-picker.review-bf.md`；
DECISIONS → `DECISIONS.md §2026-10-06-goalnn-replay-export-eval-round-picker`；
全文 → `docs/nn/console.md` §32；
门禁 → `bun run check`（根）/ 控制台 typecheck+test/build（`dashboard/`）/ nn python 用例 `nn-training/tests/worker/test_eval_replays_once.py`；
落盘 → `plan/replay-export-eval-round-picker.plan.md` 二轮供述 + 签入 `[consolidation] replay-export-eval-round-picker: …`。

**2026-10-07**：补传腿忙等修复 + 独立进程（`plan/offline-deliver-isolation.plan.md` 的 P0+P1，二次评审处置见其 §10）。
根因 = `_repost` 只唤醒不消费 ⇒ `_drain_loop` 100% 核自旋（实测 0.6s 空转 **50085 圈**，且忙等期间零日志）；
判据换成不变量探针 `_idle_spins == 0`（确定性，无窗口/容差）；欠账落盘（`owed_reposts` / `rejected`）
+ 每轮 CPU 埋点（`os.times` 差 = 云上 H6a/H6b 唯一判据，`plan/tpu-cpu-silent-downgrade.plan.md` §10.3 的闭环）。
P1 = `remote/deliver_worker.py`（子进程）+ `remote/deliver_proc.py`（`DelivererProcess`，子进程起不来 ⇒ **sticky 降级**
线程模式且补传仍送达）；切两模块是 LOC 预算（<1000 代码行/文件）逼的。
DECISIONS → `DECISIONS.md §2026-10-07-goalnn-offline-deliver-isolation`；全文 → `docs/nn/remote-transport.md` §69；
门禁 → nn python gate **3747 passed / 0 failed**（顺带修 `e2e/test_auto_handoff_e2e.py` 里赌开发机开着 dashboard 的用例）；
未做（显式）→ P2 `_eval_rows_for` / `_row_for` 增量读。

**2026-10-07（二）**：长驻池「假就绪」+ 重试路径裸文件 IO（`plan/rollout-serve-pool-readiness.plan.md` 的 P0+P1；
评审 → `plan/rollout-serve-pool-readiness.review-hy.md` 两处 P0 判据 + 七条 P1 → v2 修订后落地）。
三处根因：① `start()` 只判 `w.dead` ⇒ 「起来了但没报就绪也没退」的 worker 被当暖的发出去（`94/94 就绪` 是假满）
+ 94 个 bun 一次性并发 `Popen`（冷启动风暴）；② 重试路径 `_clean_attempt`（rglob+rmtree）**裸调且排在 `retry_line` 之前**
⇒ 44 条线程各自卡满 155s、整轮 32s → 355s，而「重试过的局 0 个」是假象；③ `call_bounded` 只弃线程、不给子进程处置
（评审把 ③ 下调为**卫生项**：本轮那 44 条线程手里没有子进程）。
判据修正（评审的）：清理超界 ⇒ **不就地重跑**（被放弃的删除者晚到会删新写者刚写的同一批路径）⇒ 归机器级停滞交整轮重投；
且已核**两条链的重投都先整目录清场**（hub 重领与自主段 `plan_run` → 同一个 `download._ensure_payload`，preloaded 路径同样清场）。
落地 = 真就绪判据 + 分批冷启动（`SPAWN_BATCH_SIZE=16`）+ 未就绪不杀（`unready` 计数）+ `retry_line` 前置 +
有界清理（`game_watch.CLEAN_CEILING_SEC=5.0`；预算 `145 ≤ 155` ⇒ 公式不动）+ 弃线自杀（`abandoned` 标记随调用链走）。
DECISIONS → `DECISIONS.md §2026-10-07-goalnn-serve-pool-readiness`；全文 → `docs/nn/runtime-opt.md` §33；
门禁 → nn python gate **3795 passed / 15 skipped**（ruff + mypy + tests/+e2e/）；未决 → §3.3 #23。

**2026-10-07（三）**：自主 worker **收工即停**（`plan/offline-worker-graceful-exit.plan.md` 的 P0+P1；一轮评审修订见其 §7）。
现场 = Kaggle 两门课 17:03 跑完、17:04 hub 已报「completed + 本会话已跑过这份包」，却**空转到 17:33**（29 分钟）才收工，
且收工后 `[keepalive] alive` 报到 17:49。两条根因性质不同：① **空烧** = 全终态也走 1800s 的 `idle_wait_sec`；
② 收工后**保活从不 `set()`**（全仓无一处），而点名路（`CFG.course` 直调 `_run_batch`）**不过 `_run_auto`** ⇒ 必须停在 notebook 层。
判据 = `all_terminal(manifest, served)`：**并上「本会话已跑过这份包（同 sha）」**——现场那门是 `claimable` 行、
只被 `served` 过滤（`state=ready`），只判终态**修不掉现场**（评审 R2）；为此 `resolve_courses` 增加第三返回值 `manifest`（原始 hub 行）。
新旋钮 `idle_wait_terminal_sec=300`（`0` = 立刻收工，走 `_num`）；`shutdown_kernel_on_exit` 默认 False 且**标注未验证**
（杀 kernel 是否真让 Kaggle 释放会话/TPU 未证；`set()` 也不会触发 Colab 的 `unassign`）。
DECISIONS → `DECISIONS.md §2026-10-07-goalnn-offline-graceful-exit`；全文 → `docs/nn/remote-transport.md` §70。

**2026-10-07（四）**：离线「一拖一」闸**两次自锁**（用户报「离线 worker 领不到 `x21-psh-k10`，切离线/在线都没用」）。
k10 自己没毛病（`ready` + 包在 + 无主），唯一拒因 = `busy: x21-psh-k5 正在交接（导包中）`。根因一：
窗口锚 `claimed_at` 每次 pending_export claim 被 `begin_auto_handoff` 重写成 now，而**重试者正是被卡的
那台机器**（15s 一拍）⇒「超窗 900s ⇒ 不再占闸」的逃生门被自己推着走，永久失效。根因二（独立）：
k5 已到 it151 ≥ `iters=150`，`--export-bundle` 的导出分支只住轮内（`step_course_iter`），轮体一次都不进
⇒ **静默不导**，只 `ALL DONE` + 一场 ~1 小时的收官 drain（逐检查点 400 局 eval）⇒ 包永远不出现，
「重试」永远没尽头。三条出路（切 k10 模式 / 切 k5 在线 / 停课 k5）全无效的理由逐条写进 §71。
落地 = ① 锚改 `flipped_at`（episode 起点，重试不刷新）② 豁免面从 `pinned_online` 扩到
`is_runnable_offline`（停课/冷课残留不占闸）③ `export_refusal`（纯函数）在 `run()` 启动期对
`start_it > iters` 的导出**响亮拒导**（裸 `[run_rl]` 行给 exit-watchdog）——`iters<=0` 与轮内
`_export_offline_bundle` 同口径。R2-d 到此作废（旧用例按新契约重写）。
DECISIONS → `DECISIONS.md §2026-10-07-offline-busy-gate-anchor`；全文 → `docs/nn/remote-transport.md` §71。
