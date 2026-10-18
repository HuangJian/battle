# y22 阶段 B —— 人类语料重导与 demo 可学性探针（2026-10-18，评审修订 R1）

> **本文件是数据准备 + 探针的规格，不产生任何课程腿。开课 = 人在控制台点。**
> 依据：`docs/nn/experiments.md` **§32 / §33**（demo 路线已经判负过两次）、
> `x20-demo-mix.jsonc`（唯一一条 demo 混 batch 腿的完整前科）、
> `reports/stack2-vs-human-threeway-20261018.md`（人类形态 vs NN 的缺口）。
>
> **R1（2026-10-18）**：按评审修掉 6 个执行硬伤 + 4 个方法问题。改动逐条见 §9。

## 0. 先读这一段：这条路已经判负过一次

| 前科 | 做法 | 结果 |
|---|---|---|
| **§32（2026-09-21）** | 61 局纯人类 BC → 权重 → held-out 255 局 | **mean 0.00 / low 100% / p10 0**，单局解剖「全程按住上、807 tick 零开火」⇒ **分布外退化成常量策略**。判死刑 |
| **§33（2026-09-24）** | `x20-demo-mix`：demo 混 batch，**coef 0.02 / per_mb 128 / 61 shards / 38564 样本 / 300 轮跑满** | **判负**。`demo_bc` 遥测活（5.06→1.16）、kl 健康，但 **entropy 0.64→0.9（策略被冲散）**、首杀晚 20–40t、日常段从未超过基线；形式完备 10 跑三门全不过（最好 −2.3pp 但 p10=1）。**背题 gap 全 < 15pp ⇒ 无背题**。结论句：「跨分布 CE 把策略从自身流形拽下来，**0.02 已是毒性剂量**」 |

⇒ **本计划不是「再做一次 demo 混 batch」**，而是：
**① 先把 160 局语料按当前 schema 重导成可用的库（一次性、纯工程）；② 用一个便宜的可学性探针
（准确率 + 常量策略哨兵）决定「这条路还值不值得再走一次」；③ 只有探针过线才谈课程腿，且剂量从 0.005 起。**

## 1. 现状核查（数字，全部实测）

| 项 | 实测 | 后果 |
|---|---|---|
| 旧语料 `nn-training/data/human-x20-corpus/shard_h_*` | **160 shards / 101153 样本 / 每关 40 局**；**manifest 全部 `obsSchemaMajor=3`**，**没有 `extra.npy`**；导出时间 2026-09-30 | 这是 **v3 语料**，不是 v4。v5 只能重导，不能原地改 |
| 旧 `demo_bank.npz` | **38564 样本 / scalars 30 维 / 无 `extra`**（2026-09-22，61-shard 时代） | 早于 v4（v4 = 2026-10-05）；**同样不是 v4** |
| 当前 schema（`nn-training/common/schema.py` / `src/nn/policy-extra.ts`） | **SCALAR_DIM 40 / POLICY_EXTRA_DIM 37 / OBS_SCHEMA_MAJOR 5** | v3 语料与 v5 不兼容（scalars 30→40；`extra` 是 key gate） |
| `nn-training/tools/build_demo_bank.py` | **`assert tuple(ex.shape[1:]) == (9,)`（硬编码 v4）**，且**不校验 scalars 维度 / 不读 manifest** | 直接跑必炸（缺 `extra.npy` ⇒ `FileNotFoundError`，不是 `AssertionError`） |
| 课程键 | `demo_bank`（仓库相对路径）/ `demo_bc_coef` / `demo_per_mb`；`remote/train_core.py:561` 要求 **coef>0 且 per_mb>0** 才生效，缺 `demo_sha` 即拒收；`trainer/loop_remote_job.py:287` 要求三键齐全 | 「只改 coef」是错的，三个键必须一起给 |
| coef 量级锚（`nn-training/biz/course_spec.py` 897–898 注释） | CE ~1.0 vs PPO value 项 ~0.25 ⇒ **0.02 ≈ 8% 梯度占比**；§33 实测 0.02 已毒性 | 首档必须 **0.005** |
| `nn-training/data/` | `.gitignore:41` 整目录排除 ⇒ 语料与库**不入 git**，源是 `nn-demo/c20-replays-20261001/` 的 **160 局有效 replay + 1 校准局** | 可随时重建；交付物给路径 + sha，不进库 |
| 决策谓词 | `decisionTick` 最后改动 **2026-09-27**（`7f78b840`）；`src/game/World.ts` / `Simulation.ts` / `src/replay/` 自 **2026-09-29** 无改动；旧语料 **2026-09-30** 导出 | 逐局 `nSamples` 复现是强预期；S3 仍作为门 |
| 人类动作标签（旧语料实测） | move：STOP **31.99%** / 上 16.00 / 下 15.16 / 左 18.27 / 右 18.58；fire：松开 **71.08%** / 按住 **28.92%** | S4 门槛的多数类基线 = 31.99% / 71.08%；**不是**遥测口径的 32.2% |
| `gate-halt` | 已平台级化（2026-10-01，`tmp/gate-halt.json` + `tmp/gate-halt.applied.json`）；**当前文件 `mode=notify`** | 「立即停」默认不会停；开腿前要切 `halt`，且 entropy/首杀两条本来就不在任何 guard 里 |

## 2. 交付物（四件，都不含训练腿）

| # | 产物 | 位置 | 验收 |
|---|---|---|---|
| T1 | v5 shard 目录（160 个） | `nn-training/data/human-x20-corpus-v5/` | 见 S1 |
| T2 | v5 demo bank | `nn-training/data/human-x20-corpus-v5/demo_bank.npz` | 见 S2 |
| T3 | 对账报告 | `reports/y22-demo-corpus-v5-<date>.md` | 见 S3 |
| T4 | 可学性探针报告 | `reports/y22-demo-probe-<date>.md` | 见 S4 |

> ⚠ `reports/` 被 `.gitignore:32` 整目录排除 ⇒ T3/T4 是**本地证据**，不入 git；
> durable 结论必须回写 `docs/nn/experiments.md`（本次 §118）。

## 3. 步骤

### S1 重导 shard（当前 encoder，v5）

导出器**不递归**扫描目录：`replays/` 下只有 `w1/w2/w3/x20-fill` 四个子目录，必须逐个传：

```bash
bun tools/sim/export-replay-labels.ts \
  --replays nn-demo/c20-replays-20261001/replays/w1 \
  --replays nn-demo/c20-replays-20261001/replays/w2 \
  --replays nn-demo/c20-replays-20261001/replays/w3 \
  --replays nn-demo/c20-replays-20261001/replays/x20-fill \
  --out nn-training/data/human-x20-corpus-v5 \
  --session y22-fill --min-kills 20
```

- 该导出器用**当前 `ObsEncoder`**（40 scalars / 37 extra / obs 16×26×26）+ 同一 `decisionTick(…, K=10)`
  谓词；`w1+w2+w3+x20-fill` 顶层共 **7+30+25+98 = 160 局**。
- **校准局 `x20-fill/_cal/0.replay` 不会被 `--min-kills` 挡掉**：它的元数据是
  `stage=2000 / seed=414009 / killCount=20`，与真实局 `shard_h_s2000_seed414009` **撞名**。
  只能靠“不递归扫 `_cal`”排除；**禁止**把父目录或 `_cal` 传给 `--replays`。
- **验收（精确，不留 159–161）**：
  - `ls -d …/shard_* | wc -l` = **160**；
  - 新 shard 名集合与旧 `human-x20-corpus/shard_*` 的 **160 个名字逐一相等**（实测旧集合与 160 局 `(stage,seed)` 完全一致）；
  - 总样本数 = **101153**；
  - `_export_report.json`：`files=160 kept=160 skipped=0`；
  - 不存在 `*_2` 之类的重名后缀 shard。
- 复现依据：`decisionTick` 自 2026-09-27 未改，`World`/`Simulation`/`replay` 自 2026-09-29 未改，
  旧语料 2026-09-30 导出 ⇒ 逐局 `nSamples` 应精确相同；S3 负责把它钉死。

### S2 建库（**先修脚本**）

```bash
bash tools/githook/nn-py-safe.sh nn-training/tools/build_demo_bank.py \
  --corpus nn-training/data/human-x20-corpus-v5 \
  --out nn-training/data/human-x20-corpus-v5/demo_bank.npz
```

**必须改的代码（`nn-training/tools/build_demo_bank.py`，最小改动）**：

1. `assert tuple(ex.shape[1:]) == (9,)` → 从 `common/schema.py` 读 `POLICY_EXTRA_DIM`；
2. 补 `assert sc.shape[1] == SCALAR_DIM`（当前**没有任何地方校验 scalars 维度**）；
3. 每个 shard 读 `manifest.json`，`assert obsSchemaMajor >= 5`（防拿旧目录建库）；
4. 打印 `keys/dims/N/sha`，便于 T3 直接引用。

- **验收**：打印行形如 `[demo-bank] shards=160 N=101153 out=… MB sha=<64hex>`；
  `keys == {obs,scalars,extra,actions,masks}`；`scalars (N,40)` / `extra (N,37)` / `obs (N,16,26,26)`。
- **记下 sha256**：它就是课程里的 `demo_sha`（`remote/hub_client.py:843` 由字节算；课程只给
  `demo_bank` 路径，sha 由打包端填）。
- 体量：raw ≈ 101153 × 11.1KB ≈ 1.1GB；`savez_compressed` 后估 **8–15MB**。`concatenate` + 压缩峰值
  约 2.5–3GB，跑之前确认可用内存。

### S3 对账（不写这一步就等于没验）

新增 `nn-training/tools/check_demo_corpus.py`（**入库、可复跑**；不再用一次性 `tmp/` 脚本），逐项断言：

1. **精确 shard 集合**：v5 与 v3 同名 shard 集合完全相等（160 个），且无 `_2` 后缀；
2. **逐局 `nSamples`**：v5 与 v3 同名 shard **完全相同**；总 N=101153；
3. **逐元素复现**：`scalars[:, :30]`、`obs`、`actions`、`masks`、`conditions` 与 v3 **全等**
   （决策谓词未变、v5 只追加 s30–s39；不是“差 <0.5pp”）；
4. **schema**：新 manifest `obsSchemaMajor == 5`，`extra` 37 维、`masks` 7 维、`actions ∈ [move 0–4, fire 0–1]`；
5. **kills**：逐局对 `manifest.kills`；合计 = **3201**（159 局 ×20 + `shard_h_s2001_seed414050` 的 21 杀），
   **不是** 20 × 160；
6. **动作分布**：v5 与 v3 的 STOP / fire 占比逐位相同（全等检查的推论，单独打印便于报告）。

任一条红 ⇒ **停，回 S1 查导出器参数**，不许“先建库再说”。

### S4 可学性探针（**决定要不要开腿**，这是本计划的核心）

BC 失败的两种可能完全不同的原因：
- **(a) 形态不可表达**：obs/网络学不出人类的动作分布 ⇒ BC 路线直接判死，改走奖励侧（C 阶段）；
- **(b) 可表达但跨分布崩**：§33 的「被拽下流形」⇒ 只能靠更小剂量 + 更短窗口，或者放弃。

**S4 只能排除 (a) 的量级问题；它不能证明 (b) 不会发生。** §32 的 val 0.7301 **本来就是按 shard
held-out**（P2-6d，2026-09-02），仍然坍缩；§33 的 `demo_bc 5.06→1.16` 也说明 demo 目标确实被优化了，
腿仍然判负。因此 S4 的定位是 **sanity gate + 常量策略哨兵**，不是“路线判决”。

**入口（不是控制台 BC job）**：控制台的 BC 发布链只收 `<traj>/bc-data/<round>/bc_s*_seed*/` 且过
D14 `course_fp/corpus_fp`；人类 shard 是 `shard_h_*`、manifest 无 `course_fp`，会被全部剔除。S4 走
**一次性本地 BC 探针**（新工具 `nn-training/tools/bc_learnability_probe.py`，经
`bun dashboard/src/launch/cli.ts --script tools/bc_learnability_probe.py …` 启动）。

**S4 分两级（CPU 成本）**：

- **S4a 快筛（本机 CPU，32 局分层子样本 = 8/关）**：`--arch bc`（NNPolicy，同 16ch/40 scalars
  输入；launcher 的 venv 是 CPU torch）10 epoch。实测 32 shards × 10 epoch 约 9.5min
  （15440 train / 4238 val）。**只回答“特征里有没有人类动作”**，不冒充 StudentNet 的最终精度。
  全 160 的 `--arch student` 约 10min/epoch，本机不做；S4b 才用 StudentNet。
- **S4b 确认（只有 S4a 过线才跑）**：`--arch student`（与 y22 PPOStudent 同族，默认 h64/d8）
  在 32 局分层子样本（8/关）上 8–10 epoch；本机 CPU 约 30min，或走控制台/GPU。

步骤：

0. **正控**：先只拿 1 个 shard、100+ epoch（batch 64），确认能过拟合到 move ≥0.95（证明数据/评测管线没坏；
   5 epoch 在 342 样本上只有 10 步更新，过拟合不了）；
1. **按局切 80/20**：`plan_shard_split` 的 shard 级切分（≥2 shards 自动生效），`--val-split 0.2`；
2. **模型**：S4a `--arch bc` / S4b `--arch student`；默认 16ch/40 scalars，`policy_extra=False`，
   不消费 37 维 extra —— 与 y22 基线 arch 一致；
3. **损失**：与 PPO demo 项同数学的 masked CE（**不做 fire 正例加权**，S5 的 `_demo_masked_ce` 也不加权）；
   AdamW + cosine + grad clip 1.0（与 `worker/train/bc.py` 同纪律；裸 Adam 在 raw-uint8 输入上会炸 logits）；
4. **读数**：held-out 混淆矩阵 + per-class recall + 预测动作分布 + 多数类基线；
   **fire 必须同时报 masked 与 all-sample 两套**（见下）；
5. **常量策略哨兵**（新增，防 §32 复发）：用产出的 BC 权重在真实 ladder 上跑 **24 局固定种子**
   （`tools/sim/eval-course-ckpt.ts --course nn-training/levels/ladder-c20-lives1.jsonc --no-dist`），
   读 `stopTicks/fireHeldTicks/playerShots/kills/ticks/moveHist/decisions/cellsVisited`。

**fire 口径（2026-10-18 实测，必须按这个读）**：`computeMasks` 在冷却期把 fire hold 标成非法；
`_masked_ce` 跳过单合法类样本 ⇒ **可训练的 fire 子集只有 43744/101153，其中 hold 只占 1.84%**。
整体 hold 28.92% 绝大多数落在“冷却期只剩 release”的样本里，模型根本不会从这些样本学到 hold。
所以：
- `fire(masked)` 的 top-1/recall 只描述“严格可训练子集”，且其多数类 = 98.16% release，**不是** 71.08%；
- 判断“开火时机可学”必须看 **`fire_all`（全样本）的 hold recall**；
- 若 `fire_all` hold recall 上不去，先别判 BC 死刑——这可能是 mask 口径（S5 的 demo CE 同样跳过这些样本）
  而不是特征不可学；报告要分开写。

| 读数 | 门槛 | 含义 |
|---|---|---|
| move 头 held-out top-1 | **≥ 0.60**（5 类，多数类 0.3199） | 方位可学 |
| fire 头 held-out top-1（**fire_all**） | **≥ 0.75**（2 类，全样本多数类 0.7108） | 开火时机可学（只比多数类高 3.9pp，必须与下一条同看） |
| move 头「STOP」recall | ≥ 0.55 | 人类 31.99% 在停；全 STOP 策略 recall=1.00，故此条不能单独用 |
| fire 头「按住」recall（**fire_all**） | ≥ 0.70 | 人类持火 28.92%；这是防“全不按”的主门。masked keep 里 hold 仅 1.84%，**不要用 masked recall 判这条** |
| 哨兵：预测 STOP 占比 | ≤ 0.60 | >0.60 = 退化成“按住上/停”类常量策略（§32 指纹） |
| 哨兵：fireHeld 占比 | ≥ 0.10 | <0.10 = 学不到开火（人类 0.2892） |
| 哨兵：decisions / cellsVisited | 非恒定 | 同一动作/同一格重复 = 无状态策略 |

- **判**：正控过 + S4a 的 move/STOP 门过 + 哨兵不坍缩 ⇒ 进 S4b；S4b 四条全过 ⇒ **形态可表达，
  值得开 20–30 轮短腿**（不是直接 150 轮）；S4a/S4b 任一条不过 ⇒ **BC 路线判死**，把结论写进
  §33 的延续，转 C 阶段（奖励换靶）。fire_all hold recall 若因 mask 口径失败，必须在报告里单列，
  不得把它当成“特征不可学”的唯一证据。
- **为什么是短腿而不是 150 轮**：§33 的 it10 就出现熵指纹；20–30 轮足以看 `demo_bc`/entropy/首杀
  三条机制读数，失败更便宜。短腿的硬停/读数见 §4。

### S5 只有 S4 过线才写课程（**本文件不写，等 S4**）

届时新增 `nn-training/curricula/y22-demo.jsonc`，单变量 = demo 三键，其余逐字抄阶段 A 胜出的那条腿。
参数按下节；开课前确认 `tmp/gate-halt.json` 是 `halt`（当前是 `notify`）。

## 4. 若开腿：剂量与熔断（照 §33 的失败指纹设计）

| 项 | 值 | 理由 |
|---|---|---|
| `demo_bank` | `nn-training/data/human-x20-corpus-v5/demo_bank.npz` | T2 |
| `demo_bc_coef` | **0.005**（首档）→ 0.01 → 0.02 | §33 实测 0.02 已毒性；源码量级锚 0.02≈8% 梯度。**首档就取 1/4** |
| `demo_per_mb` | **128**（与 §33 同值） | 唯一有前科的值；不引入第二个未知 |
| 起点 | 阶段 A 胜出的腿（若 A 无胜者 ⇒ `y22-null` 的 itN，或退回 `stack2.it200`） | 不跨代混用 |
| 轮数 | **20–30 轮先读机制**；机制过线再续到 150（cap 250） | §33 的 it10 已出指纹；150 轮是确认不是发现 |

**A. 链路硬停（缺一不可，开腿即生效）**

1. **it1 `demo_bc` 遥测非零**（§33 基线 5.06→1.16）。恒 0 = worker 没跑 demo 项，停腿查链路。
2. **entropy 不许涨**：以**本腿 it0** 为锚（y22 基线约 0.76；§33 是 0.64），**涨 >0.15 即停**。
3. **首杀拍不许退**：以**本腿 it0 同口径**为锚（stack2 胜局 605 只作参照），退 >60 拍即红。
4. **gate-halt 必须是 `halt`**：开腿前在控制台切；`notify` 下这些线都不会停。

**B. verdict 读数（结算时判，不作 day-1 硬停）**

5. **shots/千拍** 目标 ≥ 9（起点 7.9，人类 12.4）；**kills/shot ≥ 0.25**（防乱扫）。
6. **命中读数**：杀速 ≥ 3.0（起点 2.76–2.88，人类 4.02）、余量 ≥ 1.0（起点 0.90；
   `(219/伤速率)/(20/杀速)`）。
7. **背题**：eval 面板 860xxx 与语料 414xxx **零重叠** ⇒ 天然干净；仍按 §33 做法在报告里
   出一行「demo 座位 vs 非 demo 座位的 pass 差」，> 15pp 即判背题（预案：coef 再降一半）。

## 5. 风险与坑（已知的）

1. **`build_demo_bank.py` 是 v4 旧代**（断言 9 维）。不改就建不出库 —— 这是本计划**数据侧的代码改动**；
   S4 另有两个工具（探针/对账），不是“唯一代码改动”。
2. **`demo_sha` 的来源**：课程只写 `demo_bank` 路径，`sha` 由打包端（`remote/hub_client.py:843`）按字节算；
   `x20-demo-mix` 头注里手写了 `sha5cb4cb56…`。S2 打完 sha 后**两处都要核**（课程注释 + 实际 manifest），
   不一致就是打了旧库。
3. **`_cal` 陷阱**：校准局 20 杀，不会被 `--min-kills` 挡；它和真实局撞名。S1 只传四个波次目录，
   S3 断言无 `_2` 后缀。
4. **4 关泛化**：语料只有 2000–2003。§33 的分关读数里 **s2003 持续最弱** —— 即使整体不亏，
   也可能是在别关补回来的。开腿后按关读。
5. **bank 体量**：1.1GB raw / 估 8–15MB 压缩。首轮要上传一次 blob；`concatenate` 峰值约 2.5–3GB。
6. **`extra` 是 gate-only（对 y22）**：`train_core.py` 的 key gate 要 `extra`，但 y22 基线 arch
   **没有 `policyExtra`**，`worker/ppo/engine.py` 的 demo 物化只装 `obs/scalars/actions/masks`
   （495 行传 `extra=None`）。**别把“补了 extra”理解成 demo 梯度用上了 v5 新维**；若将来 base 带
   `policyExtra`，demo 会在 `_require_extra` 炸——那是另一条腿的前置修复。
7. **§33 的熔断没生效**（「日常 it10 崩到 5.43/50.5，破 6.19 线未停，gate-halt-mode 未落盘」）——
   平台级化已在 2026-10-01 落地，但**当前 `mode=notify`**；且 entropy/首杀不在 guard 里，只能人盯。
8. **语料不入 git**：`nn-training/data/` 与 `nn-demo/` 都 ignore；两边任一丢失，路线无法复现。
   建议 replays 目录做仓库外备份。

## 6. 不做

- **不做纯人类 BC 锚**（§32 已判死刑，`x20-human-anchor` 的 bc 至今是占位）。
- **不把 414xxx 加进任何 eval 面板**（§15.1；这条从阶段 B 起是硬红线）。
- **不在阶段 A 出结果前开腿**（A 还没结课，现在开 B 等于又拿漂移当背景噪声）。
- **不改 `demo_per_mb`**（唯一有前科的值，不引入第二个未知）。
- **不新增第二事实源**：维度一律从 `common/schema.py` 读，不在工具里抄数字。
- **不把 S4 的 held-out 准确率当“跨分布不会崩”的证据**（§32/§33 已反证）。

## 7. DoD

- [x] S1：精确 160 shards，名字集合与 v3 相等，N=101153，`kept=160/skipped=0`，无 `_2` 后缀
      （`reports/y22-demo-corpus-v5-20261018.md`）
- [x] S2：库 keys/维度/N/sha 全对；`build_demo_bank.py` 的 extra/scalars/manifest 三处断言改成读 schema
      （sha `c7d41c60…`，9,365,977 bytes）
- [x] S3：`check_demo_corpus.py` 全绿 + 报告落盘（逐元素复现、kills=3201、动作分布）
- [x] S4：S4a 出数（move 0.7975 / fire_all hold recall 0.0 / 24 局哨兵 100% STOP）；**判负**，
      结论见 `reports/y22-demo-probe-20261018.md` 并在 `docs/nn/experiments.md` §118 记一条
- [x] S5：**不写**（S4 未过）；开课前 gate-halt=halt 仍适用于未来任何 demo 腿

## 8. 不确定的地方

- **§33 的失败是不是“语料太小”造成的**：61 局 → 160 局是 2.6×，但样本从 38564 → 101153 只是
  同一个四关的更多局。**同分布扩量不一定治跨分布崩**，这是我最大的不确定。
- **S4 的准确率门槛（0.60 / 0.75）是拍的**，没有历史标定；它的作用只是分流（“学得会”vs“学不会”），
  不是判决质量。行为哨兵比准确率更接近 §32 的死法，但它也只是“不坍缩”，不是“开腿必赚”。
- **§33 的 `demo_bc` 数值 5.06 是新语料下的哪一档**未知 — 新库的 it1 值可能完全不同
  （样本量变了），只能看「非零且下降」这个形状，不能比绝对值。
- 语料 `data/` 不入 git，重建依赖 `nn-demo/c20-replays-20261001/`（同样 gitignore）。

## 9. 评审落账（R1）

| 评审发现 | 本文件落点 |
|---|---|
| S1 目录扫描不递归，原命令 exit 2 | §3-S1 四个 `--replays` |
| 校准局 20 杀、与 414009 撞名 | §3-S1 / §5-3；S3 无 `_2` 断言 |
| S3 kills 断言 20×N 错（实为 3201） | §3-S3-5 |
| 旧语料是 v3 不是 v4 | §1 / §3-S3 全文 |
| S4 控制台 BC 链路收 `bc_s*` + D14 `course_fp`，人类 shard 会被剔除 | §3-S4 入口改一次性本地探针 |
| bc.py 只出 masked acc，无 per-class recall | §3-S4 读数（新增混淆矩阵/recall 工具） |
| held-out 同分布准确率不能区分 (a)/(b)，§32 val 0.7301 已按 shard held-out | §0 / §3-S4 定位 / §6 |
| fire 多数类 71.08%，门槛 0.75 只高 3.9pp；STOP recall 对全 STOP=1 | §3-S4 表 |
| “落地检查”混了链路与成绩目标 | §4-A / §4-B 分层 |
| entropy 锚点不是 §33 的 0.64，是 y22 的 ~0.76 | §4-A-2 |
| gate-halt 已平台级化但当前 notify；entropy/首杀不在 guard | §1 / §4-A-4 / §5-7 |
| `extra` 对 y22 是 gate-only，engine demo 物化不装 extra | §5-6 |
| decisionTick/World/replay 未改，复现有证据 | §1 / §3-S1 末 |

## 10. 执行结果（2026-10-18，R2）

| 步 | 结果 |
|---|---|
| S1 | **160 shards / 101153 样本**；名字集合与 v3 逐一相等；`kept=160/skipped=0`；无 `_2` |
| S2 | bank **N=101153**，`obs(101153,16,26,26) / scalars(…,40) / extra(…,37)`，9,365,977 bytes，sha **`c7d41c60f20c9307396e8438a7ddbafd6848ca17d1a970004d229bd59d255200`** |
| S3 | `check_demo_corpus.py` **failures=0**：160 局 `nSamples`、`obs`、`scalars[:30]`、`actions`、`masks`、`conditions` 全等；kills=3201 |
| S4a | 32 局分层子样本（8/关），NNPolicy 10 epoch：move top-1 **0.7975**（多数类 0.3216）、STOP recall 0.7197；fire_all top-1 0.6753（多数类 0.6753）、**hold recall 0.0** |
| S4 哨兵 | 24 局：pass **0/24**、kills/shots **0/0**、moveHist **[14378,0,0,0,0]**（STOP 100%）、idle 99.98%。**2026-10-18 评审判为仪表无效**：S4a 权重无 `kind:'student'`，TS 落到 legacy `NNModel`（inCh=14 读 16ch 权重）⇒ 100% STOP 是错读，**撤销“常量策略坍缩”结论** |
| 判决 | **S4 判负，不开 `y22-demo`（连短腿也不开）**。判负理由 = fire 可训练子集 hold 仅 **806/43744 = 1.84%**（97.2% 的 hold 被 mask 判非法，当前 demo CE 实际只教 release）；哨兵不作为证据。move-only 由 `plan/y22-demo-move-only.plan.md`（R2）接手 |

产物：`reports/y22-demo-corpus-v5-20261018.md`（S3）、`reports/y22-demo-probe-20261018.md`（S4）、
`docs/nn/experiments.md` §118（结论归档）。
