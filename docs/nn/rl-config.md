# rl-config.json — 职责边界与清洗记录

> **一句话**：`nn-training/rl-config.json` 只放「**别处无处安放**」的东西——机器/环境级事实
> （端口、token、hub 地址、节点登记表）、集群调度策略（`policy`）、每课机器侧旋钮
> （`courses.<课>`）。**训练/采样/评估的超参一律住 `curricula/<课>.jsonc`**（课程文件是
> 单一事实源，且进 `course_fp` 语料血缘）。
>
> 来源：`plan/rl-config-cleanup.plan.md`（2026-09-25 排查、2026-09-26 清洗）。
> 节内编号倒序（新条目置顶、号大）。

---

## §1 2026-09-26 清洗：只留「别处无处安放」

### 1.1 判据（五类）

| 类 | 判据 | 处置 | 例 |
|---|---|---|---|
| **A 机器/环境级** | 值描述**这台机器/这条链路**，课程文件天然表达不了 | 留 | `rl.hub_port` `agent_port` `remote_token` `remote_hub_url` `remote_hubs` `cf_protocol` `cf_edge_ip` `slim` `torch_threads` `nodes[]` |
| **B 全局缺省** | 课程文件 schema **有同名键**，这里只是「课程没写时的兜底」 | 只留必要兜底 + 注释；「所有在训课程都显式声明」的可删 | `rl.mb` `workers` `seed_rotate` `difficulty` `max_ticks` `keep_iters` `eval_window_sec` |
| **C 调度策略** | 「怎么派活」而非「怎么训」的阈值 | 留 | `policy.taskTimeoutSec` `taskFetchTimeoutSec` `queueWindowSec` `statusTimeoutSec` `nodeFailStreak` `streamKlCap` |
| **D 每课机器侧** | 控制台开课/热切的写面；per-course 最具体 | 留活课、删停课 | `courses.<课>.{rollout_src,run_iters,paired_kill,gate_halt_mode}` |
| **E 废弃/死** | 代码注释明写废弃，或全域零消费者 | 删 | `intent_rl.*` · `policy.upgradeBranch` · `policy.minDiskFreeMB` · `policy.streamKlCapIntent` · `policy.streamWaveGamesIntent` |

### 1.2 新键该写哪（决策树）

1. 值描述**这台机器 / 这条链路**（端口、token、隧道、节点）？ → **rl-config**（A）
2. 是**「怎么派活」**的调度阈值？ → **rl-config `policy`**（C）
3. 是**这门课的机器侧旋钮**（控制台开课要回写）？ → **rl-config `courses.<课>`**（D）
4. 是**「怎么训」**（超参 / 奖励 / 关卡 / 门 / 评估口径 / 采样）？ → **`curricula/<课>.jsonc`**
5. 都不确定 → **先看有没有读者**。没有读者的键**不要写**；写了会被 §1.5 的白名单告警点出来。

> 为什么课程侧的东西**不能**搬进 rl-config：`courses.<课>` 是「机器侧旋钮」的写面（避免改
> `curricula/*.jsonc` 而抖动 `course_fp` 熔断口径，见 `docs/nn/training-stack.md`）；反过来，
> 训练语义的东西住 rl-config 就变成**第二事实源**——课程文件与 rl-config 各说一套。

### 1.3 本次删了什么

| 键 | 类 | 理由 |
|---|---|---|
| `intent_rl`（整块 29 键） | E | intent/goal 线入口已冻结（单一 PPO 路径，`docs/nn/training-stack.md §23`）+ hub 收敛为单实例；需要时按 x 系列重配 |
| `policy.upgradeBranch` | E | 2026-08-30 事故载体：非空值会盖掉「训练机当前分支」锁存。**读点也一并删掉**（`rl/dispatch.py`） |
| `policy.minDiskFreeMB` | E | 全域零代码消费者（磁盘余量检查在 TS 侧 `tools/agent/sampler-agent.ts`） |
| `policy.streamKlCapIntent` | E | intent 流式专属，随 intent 线一起下线 |
| `policy.streamWaveGamesIntent` | E | 同上 |
| `rl.stream` | E | 单一 PPO 路径下 `validate_args` **强制置 0**，填了也不生效 |
| `rl.double_buffer` | E | 同上 |
| `rl.precollect_early` | E | 只在 `double_buffer` 开时被读；恒 0 ⇒ 不生效 |

**清洗记录**：2026-09-26，本机 `nn-training/rl-config.json`；
备份 → `nn-training/rl-config.json.bak.20260926-171807`（sha256 已回读校验，gitignore 覆盖）。
清洗后顶层键 = `version / policy / nodes / rl / courses`。

### 1.4 「删配置、不删代码」

用户 2026-09-26 裁决：`intent_rl` 与 `stream / double_buffer / precollect_early` **只删配置，
代码保留**——需要重启 intent 线时能直接复用，不必从 git 历史重补：

- `rl/modes.py::merged_mode_args` 的 `rl.<mode> → intent_rl(legacy) → rl` 三级回退**仍认得**
  `intent_rl`；但 intent/goal 的入口目前**冻结**（`rl/config.py::validate_args` 对
  `mode != per-tick` 响亮拒启）⇒ 重启前须先解冻，且新配置按 x 系列写进 `curricula/*.jsonc`。
- `rl/cli.py` / `rl/rollout_phase.py` 仍认得 `stream`/`double_buffer`/`precollect_early`
  （`rollout_phase` 的提前预采只在 `double_buffer` 开时生效）。

### 1.5 键白名单（防再长草）

`nn-training/rl_config.schema.json`（数据，单一来源）+ `nn-training/rl_config_schema.py`（校验）。

- **只告警不拒**：未知键 / 已退役键在 `run_rl.py` 启动日志里点出来（`[run_rl] rl-config 告警：…`），
  但**绝不 block 开训**——把「配置里多了一个手写键」变成「训练起不来」代价远大于收益。
- 三类命中：顶层段不在白名单、`policy.*` / `rl.*` 键不在白名单、命中 `retired`（带原因）。
- 自由形状段不下钻：`nodes` / `courses` / `rl.remote_hubs` / `rl.intent` / `rl.goal`。
- **新增一个 rl-config 键时，同步 `rl_config.schema.json`**（否则启动会告警）。

```bash
# 自查（纯逻辑，不需要真配置）
cd nn-training && bash ../tools/githook/nn-py-safe.sh -m pytest tests/test_rl_config_schema.py -q
```

### 1.6 清洗命令与回退

```bash
cd nn-training
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py            # dry-run（零写盘，逐键 diff，脱敏）
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --apply    # 先写 .bak.<ts>（sha 回读校验）再删
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --matrix   # 「在训课程 × B 类键」覆盖矩阵
```

- 只删 `DELETE_PATHS`（8 个点号路径）+ `--drop-course` 点名的条目；**未知键一个字不碰**。
- 结构残缺（缺 `version`/`policy`/`rl`/`nodes`）⇒ 拒删（不写坏唯一的开训入口）。
- 脱敏：`rl.remote_token` / `nodes[].authKey` 只以 `…（len=N）` 出现（红线：含密钥文件永不泄露）。
- 回退：备份文件在 `nn-training/rl-config.json.bak.<ts>`（已 gitignore），直接 `cp` 回去。

### 1.7 待办（未做完的部分）

- **B 类兜底键的删除待训练机出矩阵**：只删「所有在训课程都显式声明」的键（判据 `--matrix` 的「全绿」）。
  本机实测在训仅 `x20-dodge-l3d2` / `x20-steady-cont` 两门，样本不足以代表全部在训课。
- **停课 `courses.<课>` 条目的清理**：与 `plan/course-archive.plan.md` 的「退出活体」流程
  （删 marker = 三处同时退出）对齐后再执行，避免与封存动作打架。
- **控制台冒烟接线**：`dashboard/src/stack/smoke.ts::rlConfigSmoke` 读同一份
  `rl_config.schema.json` 做未知键提示——**尚未接线**（本次工作树里有他人未提交的 dashboard 改动，
  不混入；接线属独立小改）。
