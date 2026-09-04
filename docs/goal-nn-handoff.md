# goal-nn 训练任务交接文档

> 快照：2026-08-30 晚 · 分支 `goal-nn` @ `c80fce9`（全部已推送）· 移交方：训练 agent
> 接收方第一步：读完本文 → 按「权威文档」顺序读三份 → 核对「当前状态」→ 接管监控。
>
> ⚠️ **本文件是历史快照（交接当时的状态），不再更新。**
> **现行执行清单 = `docs/goal-nn-next.md`**（任务优先级 / 门禁红线 / 问人边界 / 交接核对表）。
> 2026-08-30 夜补齐项（P1 闸复测点、熵红线、机时豁免、A5 重做、A10/A11/A-x 作废）
> 见 `DECISIONS.md` §298 与方案 §3.5 / §4.3 / §5。

---

## 1. 使命（一句话）

在玩具竞技场用课程学习从零练出逐决策步执行器，逐级升难，最终在真实 35 关上
不劣于 God-AI 基线 78.81%，并重获"目标轴是否有价值"的可测条件。

## 2. 权威文档（按此顺序读）

1. **`plan/goal-nn-action.md`** — 战役规格：§2 环境阶梯（S1→S4b）、§4 门禁与止损
   （预注册，禁止事后改）、§5 任务卡、§0.2 八条硬纪律。§2–§4 数值冻结
   （2026-08-29 定），新意见只进 progress/DECISIONS。
2. **`docs/goal-nn.progress.md`** — 执行日志（新条目置顶）：§11 → §10 → §9 → §8。
   决策索引在 `DECISIONS.md` §295–§297。
3. **`AGENTS.md`** — 仓库操作手册；**§15（闭环训练语料纪律）是本次战役新沉淀的**
   （语料轮转 / 微课哨兵 / 部署口径分评 / 大批量稳 KL / 换语义=换实验），
   细节与事故案例在 `docs/agents.details.md` §15。

## 3. 当前状态

### 运行中
- **S2 战役**（`tmp/s2-cap`）：arena 1010-1012（size14、3 敌 basic+fast）×
  seed-rotate 50 = **150 局/迭代**（全新 seed，(rotateSeed,it) 键控、断点可复现），
  max-ticks 1200，toy:kill2 奖励，**dodge off（自持探针）**，stream 模式
  （local_slots=10，PPO 波次与采集重叠），干净评估 60 局/迭代
  （--eval-stages 1010-1012，种子 860001-860020 固定）。
- it1 = 33%（150 全新局的诚实基线）；健康线：熵 ~1.5-1.8、kl <0.05、value 收敛。
- 预算：`--max-hours 8 --iters 30` 先到者；用户已表态"只要收敛，不考虑机时"。

### 已完成里程碑
| 卡 | 结果 | 产物 |
|---|---|---|
| A0a/A0/A1 | arena 阶梯 15 张 + 锚值表 + 编号贯通 + CPU 校准 | `reports/arena-god-baseline.{json,md}`、`arena-layout-hashes.json` |
| A2 | 三臂扫描：**kill 臂胜出**（26.7% vs 1.7%/1.7%） | `reports/reward-sweep-S1.{json,md}` |
| A3 | dodge L0 + 覆盖步记账 + A/B（l0≈god≈off，覆盖率 0.62% <2% ✓） | `tmp/dodge-ab/` |
| A8a | headroom −25pp 倒挂 ⇒ 登记方案 3（不开目标头；探针非真上界，A9b 后 A8b 复核） | `reports/goal-headroom-a8a.{json,md}` |
| **A4** | **S1 过门**：17 iters rollout 25%→97/99/100%，arena 自评 96-100%（门 ≥90%） | `reports/s1-exit-report.md` |
| A4b | **缓期**：S1-only 学生缺 S2/S3 技能，真实关复测结构性无解（0/240 是能力不足非迁移证伪） | `reports/p1-a4warm-s0-3.html` |
| A5 | ≈ 未定（两臂贪心逐局 60/60 全同——argmax 收敛同套路；随机口径 A4 占优但噪声大） | 同上 |

### 关键权重文件
- `tmp/s1-cap2/weights.json` — **S1 过门权重**（S2 热启动源）
- `tmp/bc-arena/weights.json` — arena-DAgger BC（val_loss 0.97）
- `tmp/scratch-init/weights.json` — 近均匀 scratch init（纯从零臂必用）
- `tmp/a4-warm/weights.json` — A4 出口（存档）

## 4. 环境与拓扑

- **节点池 8 台**：self(3, 本机 127.0.0.1) / mac(6) / a95(7) / a97(7) / a98(7) /
  lite(2) / a96(失联) / gcs(4)。监控页：`<self-agent-url>/pool`（5-60s 自刷新）。
- **远控语义**：升级分支 = 训练机当前分支（`dist_common.UPGRADE_BRANCH` 锁存，
  启动时 push）；节点 stale → 自动远控 pull+重启；**self/回环节点 = 纯重启**
  （无 pullBranch，零 git 操作——共享工作区禁破坏性 pull）。
- **codeHash 集内文件**（改动即触发全节点升级）：`src/nn/**`、
  `tools/sim/export-rl-rollout.ts`、`export-intent-rollout.ts`、
  `tools/agent/sampler-agent.ts`。改这些 = push 后下一迭代自动升级波。
- **`nn-training/rl-config.json` 是 per-machine 文件（gitignored）**：
  `nodes[]`（节点清单+authKey）、`policy.*`（超时/升级分支空）、
  `rl` 块（run_rl 共享机制默认 11 项：local_slots=10、rotate_stages=0、
  total_stages=35、difficulty、max_ticks、stream=1、mb、seed、keep_iters、
  eval_window_sec、workers=8）。换训练机必须手动带走。

## 5. 运维 runbook

```bash
# 启动/续跑（stream 默认开；--kill-previous 杀旧训练进程，在途 bun 局自然结算）
./nn-training/start-training.sh --kill-previous --torch-threads 8 --script run_rl.py \
  --bc <warm.json> --out <dir>/weights.json --traj <dir> \
  --iters N --max-hours H --stages <arena-ids> --seed-rotate 50 \
  --max-ticks <per-level> --workers 8 --stream 1 \
  --dodge off --reward toy:kill2 \
  --eval-stages <同训练 arena> --eval-games-per-stage 20 --keep-iters 3

# 停止（TaskStop 不可靠——python 子进程会复活；必须进程名限定杀）
pwsh -ExecutionPolicy Bypass -File tmp/kill-s2-clean.ps1   # 模式见文件

# 贪心门评估（60 局/臂，3 arena × 20 seed；模板 tmp/gate-judge.sh）
# 门判定（(stage,seed) 2100 对口径；AGENTS 15.3：部署口径必须单评）
bun tools/sim/paired-gate.ts --baseline-ledger <a>.ledger.jsonl --candidate-ledger <b>.ledger.jsonl

# 监控（短 tail，禁止 sleep-wait）
tail tmp/s2-cap.log；training_log.jsonl 的 iteration 事件；eval_log.jsonl
```

## 6. 检点与门（预注册，到点执行别凭意志）

**S2（当前）**——方案 §2.1 双轨门：全歼率 ≥80%；受伤 ≤1.2×锚(0.012/局)；
存活 ≥80%×锚(538 tick)。锚（A0，God-AI 同场）：胜 100%、击杀 3.0、死亡 0.01、
ticks 中位 672。
- 中途检点：**eval <80% 且 rollout <50% ⇒ 降档减敌**（改 ARENA_LADDER 或减 enemyCount）
- eval >80% 连续 5 迭代 ⇒ 过门 → S3（1020-1022，maze 无基地 8 敌，max-ticks 3600）
- S3 门：全歼 ≥70% + 三项指标达 S2 水平（S3 锚：击杀 7.96、死亡 0.68、中位 2184）
- S4a（1040-1042，有基地 maze，max-ticks 12000）：**锚不可用**（God-AI 仅 49.4%）⇒
  纯绝对门：基地不失守 ≥90% + 行为门不退化；**盯"守家不出去"反向病理**
- S4b（真实 35 关，rotate 35×10）：对 God-AI 78.81% 配对差方向为正；
  每 tier 一次迁移探针（真实关 0-3 × 60 seed vs 随机基线 ledger 配对）
- **预算停 vs 门败必须分写**（止损线 5）：前者结论"未知"，后者"否定"

## 7. 硬纪律（违反 = 事故重演）

1. 训练只经 `start-training.sh`（venv/锁/杀旧），**never raw python**
2. 奖励臂 / 课程语义 / 物理改动 → **停下问人**（本次战役内 kill2/eval-stages/
   stream 默认均经用户确认）
3. 停进程必须用**进程名限定**的 ps1（`python.exe`/`bun.exe` + 子串匹配）——
   含目标子串的 bash 包装会**自杀**（踩过两次）
4. TaskStop 不杀 python 子进程——训练器迁移/重启前后都跑一次 kill ps1 并验证
   remaining=0，否则僵尸训练器复活重写目录（踩过）
5. **改语料/课程/奖励语义 = 新实验新目录**（AGENTS 15.5）+ DECISIONS 记录
6. 新策略/新奖励先做**可区分性冒烟**；eval 报告必带 weightsSha（已内置）
7. 闭环语料逐轮轮转（AGENTS §15.1）；固定小集合的胜率曲线解读作废
8. config 化默认值的每个键过一遍"对所有未来战役安全吗"——切换器默认取保守态
   （rotate_stages 两次 35 关意外的免疫记录，见 progress §10）
9. 训练机重启训练器时，**self 节点纯重启远控会自动跟上**，不要手动 pull

## 8. 事故档案（一句话版，细节在 progress §7-§10）

| 事故 | 免疫 |
|---|---|
| 35 关意外 ×2（远控 upgradeBranch 残留 + rl 块 rotate_stages 照抄） | 分支锁存训练机当前分支；切换器默认保守态；self 禁 pull |
| 幻影基地 / 幻影点 / 幻影 logit（三层量级病理） | warm_start_normalize + init_scratch_weights；真实 obs 校准（合成探针低估 13×） |
| 微课记忆化（固定 12 局 23→44%） | seed-rotate 逐轮全新 seed；部署口径单评 |
| 贪心塌缩（wAlive 锚） | kill2；检点纪律 |
| eval 无权重指纹（A4/A5 评估排障 2h） | 报告已带 weightsSha + EVAL_DEBUG 首帧 logits |
| EADDRINUSE（TIME_WAIT 60s） | reusePort + 重试 120s |
| kill 脚本自杀（bash 包装含目标子串） | 进程名限定 ps1 |

## 9. 开放项

1. **S2 检点**（运行中，自动触发）
2. **S4a 奖励预注册**：进 S4a 前需定 `toy + w_base·baseIntegrity + w_safety·baseSafety`
   的具体权重（方案 §3.4 预留给 A7 出口）——**须与用户确认**
3. **L0 重新启用判据**：某档 off 探针 deaths/局 > 红线 ⇒ 重开 `--dodge l0` 再评
4. **eval shots/deaths 遥测与 kills 矛盾**（reward-sweep-S1.md 已记录）——未修
5. **节点卫生**：lite 横跳（疑双 agent/pull 竞争）、a95 pull 升级、a96 失联
6. **A8b**（S4b 出口复核目标轴；headroom 探针工具已备 `goal-headroom-a8a.ts`）
7. **AGENTS §15 阈值**为量级+指针，集群规模变化时重估（当前锚：150 局/轮 @ ~60 workers）

## 10. 交接清单（接收方打勾）

- [ ] 读完 §2 三份文档
- [ ] `bun run check` 全绿
- [ ] `pwsh tmp/list-rl.ps1` 确认只有一个训练进程
- [ ] `/pool` 页面 8 节点状态核对
- [ ] S2 曲线与检点规则对齐（§6）
- [ ] git log 扫一遍 `docs/goal-nn.progress.md` §8-§11 的 commit 链
