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
| **A 机器/环境级** | 值描述**这台机器/这条链路**，课程文件天然表达不了 | 留 | `rl.hub_port` `agent_port` `remote_token` `remote_hub_url` `remote_hubs` `cf_protocol` `cf_edge_ip` `slim` `torch_threads` `nodes[]` · **`rl.local_slots` / `rl.workers`**（本机并发配额，见 §1.4b） |
| **B 全局缺省** | 课程文件 schema **有同名键**，这里只是「课程没写时的兜底」 | 只留必要兜底；**范围内全绿的删掉**（判据与执行见 §1.4b） | `rl.mb` `seed_rotate` `keep_iters` `eval_window_sec` |
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
| `rl.difficulty` | B | 矩阵全绿（108/108 课程显式声明：44 个课程文件 + 64 个关卡注入）⇒ 纯兜底；删后读者 `eval_a_once.py:274` 的 `or "hard"` 与删前**同值** |
| `rl.max_ticks` | B | 同上（`eval_a_once.py:273` 的 `or 12000` 与删前的 `12000` 同值） |

**清洗记录**：2026-09-26，本机 `nn-training/rl-config.json`，两批（键见上表）：
P0 死键/废弃块 → 备份 `nn-training/rl-config.json.bak.20260926-171807`；
B 类全绿键 → 备份 `nn-training/rl-config.json.bak.20260926-181554`
（两份 sha256 均已回读校验，gitignore 覆盖）。
清洗后顶层键 = `version / policy / nodes / rl / courses`。

### 1.4 「删配置、不删代码」

用户 2026-09-26 裁决：`intent_rl` 与 `stream / double_buffer / precollect_early` **只删配置，
代码保留**——需要重启 intent 线时能直接复用，不必从 git 历史重补：

- `rl/modes.py::merged_mode_args` 的 `rl.<mode> → intent_rl(legacy) → rl` 三级回退**仍认得**
  `intent_rl`；但 intent/goal 的入口目前**冻结**（`rl/config.py::validate_args` 对
  `mode != per-tick` 响亮拒启）⇒ 重启前须先解冻，且新配置按 x 系列写进 `curricula/*.jsonc`。
- `rl/cli.py` / `rl/rollout_phase.py` 仍认得 `stream`/`double_buffer`/`precollect_early`
  （`rollout_phase` 的提前预采只在 `double_buffer` 开时生效）。

### 1.4b B 类「全绿」判据与本次执行（2026-09-26）

**全绿**（`tools/rl_config_clean.py::is_green`）= 范围内的**每一门**课程都**不靠 rl-config 兜底**
（值来自课程文件或关卡注入）；空集恒「非全绿」——**样本不足就不删**（plan §3.2）。

**范围**（`--scope`）：

| 范围 | 课程表 | 何时用 |
|---|---|---|
| `live`（缺省） | 在训课程 = 有 `tmp/<课>/training-enabled.txt` | 有人正开着课时的常规口径（与训练侧 `enabled_courses` / hub `_course_dir_live` 同闸） |
| `all` | `curricula/*.jsonc` 的全部课程 | **在训集为空时唯一能出结论的范围**；且「全绿于 all ⇒ 全绿于 live」（更强，不反之） |

**本次执行**：操作员在 17:49 把两门在训课停课（控制台日志 `已停课 …（已删开课标记）`）
⇒ `live` 集为空、`--matrix` 出不了结论；改用 `--scope all`（108 门）⇒ 全绿 = `difficulty`、`max_ticks`。

**机器级键永不删**（`tools/rl_config_clean.py::MACHINE_KEYS`）：`local_slots`、`workers`。
它们在 `--scope all` 下会判「全绿」（108/108 课程都写了 `workers`），但 rl-config 里这条是
**裸机读数**而不是课程兜底——`dashboard/src/core/slots.ts::bareCapacity` = `max(rl.workers, rl.local_slots)`，
`rl/config.py::apply_course_machine_overrides` 也把 `rl.{workers,local_slots}` 当本机配额缺省。
删掉 ⇒ `Number(undefined ?? 0)` = 0 ⇒ 容量塌成 0、`checkCapacity` 把每门课都报成超量（假红）。

**保留兜底的（没删）**：`mb` `seed_rotate` `keep_iters` `eval_window_sec` `total_stages`
`rotate_stages` `seed` `lr` `epochs` `gamma` `lam` `target_transitions` —— 在 `all` 范围内都有课程靠
rl-config 兜底（例：`mb` 有 9 门 BC/demo 课没写；`keep_iters`/`eval_window_sec` 几乎全体依赖）。
按 plan §8-O1：**保留**比「零缺省」安全（历史课可复现；新课漏声明不至静默漂到 argparse 默认）。

```bash
cd nn-training
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --matrix --scope all                    # 出矩阵 + 全绿清单
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --drop-b-class --scope all              # dry-run（零写盘）
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --drop-b-class --scope all --apply      # 先备份再删
```

### 1.5 键白名单（防再长草）

`nn-training/rl_config.schema.json`（数据，单一来源）+ `nn-training/rl_config_schema.py`（校验）。

- **只告警不拒**：未知键 / 已退役键在 `run_rl.py` 启动日志里点出来（`[run_rl] rl-config 告警：…`），
  但**绝不 block 开训**——把「配置里多了一个手写键」变成「训练起不来」代价远大于收益。
- 三类命中：顶层段不在白名单、`policy.*` / `rl.*` 键不在白名单、命中 `retired`（带原因）。
- 自由形状段不下钻：`nodes` / `courses` / `rl.remote_hubs` / `rl.intent` / `rl.goal`。
- **新增一个 rl-config 键时，同步 `rl_config.schema.json`**（否则启动会告警）。
- **消费面（2026-09-26 评审更正）**：今天**只有** `rl_config_schema.py`（`run_rl.py` 启动时校验）
  读这份 JSON——`dashboard/src/stack/smoke.ts::rlConfigSmoke` **尚未接线**。早先的 docstring / schema `_doc` /
  `run_rl.py` 注释里写的「与控制台冒烟共用一份、一处增删两侧同时生效」**不成立**，已改掉。

```bash
# 自查（纯逻辑，不需要真配置）
cd nn-training && bash ../tools/githook/nn-py-safe.sh -m pytest tests/test_rl_config_schema.py -q
```

### 1.6 清洗命令与回退

```bash
cd nn-training
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py            # dry-run（零写盘，逐键 diff，脱敏）
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --apply    # 先写 .bak.<ts>（sha 回读校验）再删
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --matrix --scope all   # 「课程 × B 类键」覆盖矩阵
bash ../tools/githook/nn-py-safe.sh tools/rl_config_clean.py --drop-b-class --scope all --apply  # 删全绿 B 类兜底键
```

- 只删 `DELETE_PATHS`（8 个点号路径）+ `--drop-course` 点名的条目；**未知键一个字不碰**。
- `--drop-b-class`（配 `--scope`）才把**矩阵全绿的 B 类兜底键**算进删除清单（缺省关；机器级键永不在内）。
- 结构残缺（缺 `version`/`policy`/`rl`/`nodes`）⇒ 拒删（不写坏唯一的开训入口）。
- 脱敏：`rl.remote_token` / `nodes[].authKey` 只以 `…（len=N）` 出现（红线：含密钥文件永不泄露）。
- 回退：备份文件在 `nn-training/rl-config.json.bak.<ts>`（已 gitignore），直接 `cp` 回去。

### 1.7 待办（未做完的部分）

- ~~**B 类兜底键的删除待训练机出矩阵**~~ **已完成（2026-09-26，§1.4b）**：`live` 集为空（操作员已于
  17:49 停掉全部课程）⇒ 改用 `--scope all`（108 门）出结论，删 `rl.difficulty` / `rl.max_ticks`。
  若之后重新开课、想按「在训集」口径再核一遍：`--matrix --scope live`。
- **停课 `courses.<课>` 条目的清理**：与 `plan/course-archive.plan.md` 的「退出活体」流程
  （删 marker = 三处同时退出）对齐后再执行，避免与封存动作打架。
- **控制台冒烟接线**：`dashboard/src/stack/smoke.ts::rlConfigSmoke` 读同一份
  `rl_config.schema.json` 做未知键提示——**尚未接线**（本次工作树里有他人未提交的 dashboard 改动，
  不混入；接线属独立小改）。注意：plan §3.4/E8 的验收是「启动日志 **与冒烟面板** 都点出假键」，
  今天只达成前半（已用接线钉子用例钉住启动日志那一半）。
- **控制台写面仍会写「已退役」三键**（2026-09-26 评审更正）：
  `dashboard/src/server/actions/preset.ts` 的开课预设 + `TrainLaunchModal` 的 `stream` /
  `double_buffer` / `precollect_early` 三个开关**仍会**把它们写回 rl-config ⇒ ① 开关在
  `validate_args`（单一 PPO 路径）下恒被归一化成 0，是**死开关**；② 写回后下次 `run_rl` 启动会打
  「已退役」告警。这属「只删配置、不删代码」（用户 2026-09-26 裁决）的边界内；**要不要摘掉/标灰
  这三个开关是独立决定**（控制台手感变化），待定。
