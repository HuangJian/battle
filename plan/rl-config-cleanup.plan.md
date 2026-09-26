# Plan: rl-config-cleanup — 清理 `nn-training/rl-config.json`：只留「别处没有的东西」

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **状态**：实施中（2026-09-25 排查定案；2026-09-26 评审修订 + 用户裁决，见 §0.5）。§8 三处取舍已由用户拍板（**均按推荐档**）：B 类只删矩阵全绿的、`intent_rl` 整块删、schema 固化**做但只告警不拒**。
> **进度**：P0 死键/废弃块 + schema + 工具 + 文档已落（2026-09-26）；**B 类全绿键也已删**（§0.5.2 注）。
> 未做完：停课 `courses.<课>` 条目清理（待与课程封存对齐）、控制台冒烟接线。
> **2026-09-26 补裁**：`intent_rl` 与 `stream/double_buffer/precollect_early` 一律**只删配置、保留代码**；`intent_rl` 重启配方**不做**（需要时照 x 系列重配）。
> **触发（用户 2026-09-25）**：「梳理并清理 `nn-training/rl-config.json`。原则上已经在其它地方配置过的数据，
> 就不应该再往里填了。`intent_rl` 已废弃，如果后期重新启用，也要配置成现在 x 系列课程一样训练。」
> **阅读顺序**：§1.1 现状普查 → §1.2 谁在读它（优先级链）→ **§2 分类与去留判据** → §3 清洗动作
> → §4 落点 → §5 实施 → §6 验证/DoD。行号只作定位加速，判据看函数名。
> **我没有执行的部分**：**本文件含密钥且是所有训练的读入口** ⇒ 真机清洗由用户点头后执行；agent 只写脚本 + dry-run。

---

## 0. 一句话目标

**把 `rl-config.json` 收敛成「只有别处无处安放的东西」：机器/环境级（端口、token、hub 地址、cf 隧道、节点登记表）+
调度策略（policy）+ 每课机器侧旋钮（`courses.<课>`）；训练/采样/评估这类**课程文件已经能声明**的键不再重复填；
`intent_rl`（29 键）整块删除，并留一份「intent 线重启 = 按 x 系列方式配（配置进课程文件）」的配方。**

---

## 0.5 落地口径（2026-09-26 评审修订 + 用户裁决，**本条优先于正文旧叙述**）

### 0.5.1 用户裁决（本次实施）

1. **`intent/goal`：只删配置，不删代码。** 删 rl-config 的 `intent_rl` 整块 + 两个 `policy.*Intent` 键；
   `ppo/intent.py`、`ppo/goal.py`、`rl/modes.py` 的 legacy 回退分支、`rl/eval_m1.py` 与 m1-eval 派发链
   **一个字不改**（§3.1 原「顺手去掉 `rl/modes.py` legacy 分支」**作废**，R3 随之作废）。
2. **`intent_rl` 重启配方（§3.3）不做。** 需要重启 intent 线时照 x 系列重配；且**入口目前是冻结的**
   （`rl/config.py::validate_args` 对 `mode != per-tick` 响亮拒启，单一 PPO 路径），重启前须先解冻。
3. **`stream` / `double_buffer` / `precollect_early`：只删配置，保留代码。** 它们是 CPU 训练期的小剂量，
   GPU 算力充裕后不用了；从 rl-config 删除即可。

### 0.5.2 评审修订（与 2026-09-25 版正文冲突处，以本表为准）

| 处 | 原文（2026-09-25） | 现况（2026-09-26 HEAD） |
|---|---|---|
| §1.1 计数 | `rl` 13 叶 · `nodes` 9 条 · `courses` 13 条 · 总 90 叶 | `rl` **24** 叶（+13 `remote_hubs`）· `nodes` **8** 条 · `courses` **17** 条 |
| §1.2/§9 坐标 | `dispatch.py:191` · `stream.py:193-194` · `modes.py:83-95` · `load_course:1269` · rl/courses `:1339-1340` | `:184` · `:188-189` · `:80-96` · `:1225` · `:1465-1466` |
| §1.2/§9 引用 | `plan/accident.plan.md §3` | **该文件已删** ⇒ 单一 PPO 路径的家 = `docs/nn/training-stack.md §23`（`DECISIONS.md:1323` 同指） |
| §1.4 `upgradeBranch` | 只「删键」 | **同时删读点**：`rl/dispatch.py:184` 的 `policy.get("upgradeBranch")` 去掉（`dist_common.py:499` 注释已声明废弃） |
| §3.2 `stream`/`double_buffer`/`precollect_early` | 归 B 类「全局缺省」 | 实为**恒不生效**（`validate_args` 强制 `stream=0`/`double_buffer=0`；`precollect_early` 只在 `if double_buffer` 分支里读）⇒ 按死键处理 |
| §4 S2/S3 | 自造在训判据与停课候选表 | 判据与计数**对齐 `plan/course-archive.plan.md §1.1`**（17 课程目录 / **4 在训**：`x20-dodge-l1d2/l2a/l2b/l3d2` / 13 已停）；且**只能在训练机执行**（本机 `tmp/` 无课程目录） |
| §3.4 P2 | `--strict-rl-config` 缺省关 | 改为**恒告警、不拒**（E8 的验收要「塞假键 ⇒ 启动日志/冒烟出现告警」，缺省关就达不到）；白名单数据与冒烟共用一份 JSON |

> 注（2026-09-26 更新）：B 类「矩阵全绿」的删除**已在训练机执行**。操作员 17:49 停掉全部课程
> ⇒ `live`（开课标记）集为空、`--matrix` 出不了结论；改用 `--scope all`（`curricula/` 108 门，
> **超集判据**：全绿于 all ⇒ 全绿于 live）⇒ 删 `rl.difficulty` / `rl.max_ticks`。
> `mb` / `seed_rotate` 等在 `all` 范围内**非全绿**（有课程靠兜底）⇒ 按 O1 保留。
> 顺手把 `local_slots` / `workers` 显式归入机器级（`MACHINE_KEYS`，永不删，§3.2-4 的推广）。
> 执行记录与判据 → `docs/nn/rl-config.md §1.4b`；决策 → `DECISIONS §2026-09-26-rl-config-b-class`。

---

## 1. 背景与已定事实

### 1.1 现状普查（2026-09-25，实测）

`nn-training/rl-config.json`：**6 个顶层键**（含密钥 `rl.remote_token` ⇒ 该文件 `.gitignore` 排除）：

| 顶层 | 叶子 | 内容 |
|---|---|---|
| `version` | 1 | 配置版本号 |
| `policy` | 10 | 调度策略（超时/熔断/流式 KL）+ 2 个 intent 专属 + 1 个死键 |
| **`intent_rl`** | **29** | **已废弃的意图线全局块**（legacy） |
| `nodes` | **8** 条 | GPU worker 登记表（含 trycloudflare URL + authKey） |
| `rl` | **24** + 13（`remote_hubs.*`） | 机器级 + 全局缺省旋钮 + hub 地址表 |
| `courses` | **17** 条 | 课程级机器侧旋钮（`rollout_src`、`run_iters`、`paired_kill`） |

### 1.2 谁在读它（**优先级链**，决定「删了会怎样」）

| 读点 | 位置 | 说明 |
|---|---|---|
| 唯一路径来源 | `dist_common.rl_config_path()`（`dist_common.py:59-71`） | env `BCITY_RL_CONFIG` > `nn-training/rl-config.json` |
| 唯一读入口 | `rl/config.py::read_rl_config_file()`（`:49-52`） | 读不到/形状不对 → 空 dict |
| **合并优先级** | `rl/config.py::load_course`（`:1269`） | **课程文件 > rl-config > argparse 默认**；`:1339-1340` 读 `cfg["rl"]` 与 `cfg["courses"][课]` |
| 模式块合并 | `rl/modes.py::merged_mode_args`（`:83-95`） | `rl.<mode>` → **`intent_rl`（legacy）** → `rl`。⚠ **legacy 覆盖 `rl` 同名键** |
| 课程级机器旋钮 | `rl/loop_serve.py::apply_course_machine_overrides`（`:224-255`） | `courses.<课>` 优先于 serve argv **与**课程文件（per-course 最具体），逐键打印生效值 |
| 训练期热读 | `rl/loop_steps.py:87/131/171/235` | `cfg["courses"][stem]` 的 `cf_protocol`/`cf_edge_ip`/`rollout_src`/`run_wait_sec` |
| 控制台 | `dashboard/src/core/config.ts`（load/saveConfig）、`stack/specs.ts`（`trainModeKnobs` 唯一换算）、`stack/smoke.ts:64`（rl-config 契约冒烟） | 启动/开课/登记 worker 会**回写**它 |

### 1.3 `intent_rl` 块逐键对照（实测值）

| 类别 | 键（intent_rl 值 → rl 值） | 删块后影响 |
|---|---|---|
| **同值 7 个** | `total_stages 35→35`、`difficulty hard→hard`、`max_ticks 12000→12000`、`mb 512→512`、`seed 7→7`、`keep_iters 3→3`、`eval_window_sec 1800→1800` | **无行为变化** |
| **不同值 4 个** | `rotate_stages 35→0`、`workers 10→8`、`local_slots 10→0`、`stream 1→0` | intent/goal 模式会改用 rl 的值 ⇒ 行为变化（**这正是用户要的**：新 intent 线按 x 系列口径 = `local_slots 0`/`stream 0`/`workers 8`） |
| **intent 独有 18 个** | `bc` `tmp/intent-weights-Bp.json` · `out`/`traj` `tmp/intent-rl*` · `iters 0` · `seeds_per_stage 4` · `epochs 2` · `lr 5e-05` · `replan 30` · `warmup_iters 1` · `kickstart_kl 1` · `kickstart_decay 0.85` · `eval_at '5,10,…100'` · `eval_seeds 10` · `baseline 0.723` · `kl_break 0.3` · `kl_break_consec 3` · `out_log`/`err_log` | 需按 §3.3 的「新家对照表」逐键处置 |

### 1.4 死键与半死键（实测）

| 键 | 判据 | 结论 |
|---|---|---|
| `policy.upgradeBranch` | ⚠ **不是零消费者**：`rl/dispatch.py:191` 仍读它作兜底 `upgrade_branch_or(str(policy.get("upgradeBranch") or ""))`，而 `upgrade_branch_or`（`dist_common.py:522-525`）**显式值优先于锁存**（`if explicit: return explicit`）。**当前值 = `""`** ⇒ 落回 `run_rl.py:309` 锁存的当前分支 ⇒ **删除零行为变化**；但它正是 2026-08-30「残留旧战役分支名把全部节点 reset」的载体（**填任何非空值都会盖掉锁存**） | **删**（铲掉坑，行为等价）+ **去掉 `rl/dispatch.py:184` 的读点**（只删键不删读点，坑还在） |
| `policy.minDiskFreeMB` | 全域（nn-training + dashboard + tools + md）= **仅 1 处文档命中**（`tools/agent/agent-setup.md:56`），无任何代码消费者 —— 那句文档说的是 **TS 侧**的 `tools/agent/sampler-agent.ts:689 diskFreeMB()`（函数名，不是 rl-config 键） | **删** + 顺手把 `agent-setup.md:56` 的指向写清（免得下一个人以为配置里有这个键） |
| `policy.streamKlCapIntent` / `policy.streamWaveGamesIntent` | 各自只有 1 处消费者（`rl/stream.py:193-194`），都是 intent 流式专属 | 与 intent 线同批 ⇒ **删**（重启 intent 时按当时口径重设） |
| `courses.<已停课程>` | §4 S3 判据 | 删条目 |

### 1.5 红线（实现不得违反）

1. **`rl.remote_token` / `nodes[].authKey` 是凭据**：不得打印、不得写进任何入库文件（本 plan 与脚本日志都要脱敏）。
2. **不许「先删后验」**：清洗前必须备份 + 出「生效值对照」（§4 S4），差异必须逐键有解释。
3. **不动 `courses.<活课>`**：那是控制台热切/开课的写面（`applyTrainModeToConfig`），删了会让「切离线」失效。
4. **不动 `policy` 里真有消费者的键**（超时/熔断/流式 KL）：它们是机器级，别处无处安放。
5. **未跟踪 `*.md` 不入库**（AGENTS §5 红线 #3）；pytest 走 `bash tools/githook/nn-py-safe.sh -m pytest …`；
   dashboard 改动要 `cd dashboard && bun run typecheck && bun run test`。
6. **agent 不起停训练**；清洗脚本先 `--dry-run`，真删由用户执行。

---

## 2. 分类与去留判据（本 plan 的核心）

**判据一句话**：`rl-config.json` 只保留「**别处无处安放**」的键。

| 类 | 判据 | 处置 | 例 |
|---|---|---|---|
| **A 机器/环境级** | 值描述**这台机器/这条链路**，课程文件天然表达不了 | **留** | `rl.hub_port` `agent_port` `remote_token` `remote_hub_url` `remote_hubs` `cf_protocol` `cf_edge_ip` `slim` `hub_push` `torch_threads` `nodes[]` |
| **B 全局缺省** | 课程文件 schema **有同名键**；rl-config 里的值只是「课程没写时的兜底」 | **只留必要兜底 + 注释标明**；与在训课程重复的按 §3.2 逐个清 | `rl.mb` `lr`* `epochs`* `gamma`* `lam`* `workers` `keep_iters` `eval_window_sec` `difficulty` `max_ticks` `total_stages` `rotate_stages` `seed_rotate` `stream` `double_buffer` `precollect_early` `local_slots` |
| **C 调度策略** | 集群调度器的行为阈值，属于「怎么派活」而不是「怎么训」 | **留** | `policy.taskTimeoutSec` `taskFetchTimeoutSec` `queueWindowSec` `statusTimeoutSec` `nodeFailStreak` `streamKlCap` |
| **D 每课机器侧** | 控制台热切/开课的写面；per-course 最具体 | **留活课，删停课** | `courses.<课>.{rollout_src,run_iters,gate_halt_mode,cf_*}` |
| **E 废弃/死** | 代码注释明写废弃，或全域零消费者 | **删** | `intent_rl.*`（29）· `policy.upgradeBranch` · `policy.minDiskFreeMB` · `policy.streamKlCapIntent` · `policy.streamWaveGamesIntent` |

> ⚠ **B 类的取舍要点**：删除全局缺省**会改变「没写该键的课程」的行为**。所以删之前必须先出
> 「**在训课程 × 键**」覆盖矩阵，只删「**所有在训课程都已显式声明**」的键（§4 S2）。
> 反过来，若某键在所有在训课程里都一致（例：`mb` 全是 1024、`max_ticks` 全靠 level 注入），
> 那它填在 rl-config 里就是**第二事实源**（用户原则针对的正是这一类）。

---

## 3. 清洗动作

### 3.1 P0 — 删死键与废弃块（低风险，先做）

```
- policy.upgradeBranch          （代码已不读）
- policy.minDiskFreeMB          （零消费者；顺手修 tools/agent/agent-setup.md:56 的指向）
- policy.streamKlCapIntent      （intent 专属）
- policy.streamWaveGamesIntent  （intent 专属）
- intent_rl{...}                （整块 29 键；**不留空壳**）
```

**⚠ 2026-09-26 改（§0.5.1-1）：只删配置，代码保留。** `rl/modes.py` 的 legacy 回退分支
（`:80-96`）与 `e2e/test_run_rl_m1.py:156-170` 的断言**都不动**——代码仍认得 `intent_rl`，重启时能直接复用。
（原「顺手去掉 legacy 分支 + 改 e2e 断言 + §7-R3」作废。）

**行为影响**：`intent_rl` 的 7 个同值键无影响；4 个不同值键在 intent/goal 模式下会改用 `rl` 值——
**这是有意为之**（新 intent 线按 x 系列口径跑）；18 个独有键按 §3.3 的对照表处置。
`policy.upgradeBranch` 当前是**空串** ⇒ 删除后仍落回 `run_rl.py:309` 锁存的当前分支，**行为等价**
（但若哪天有人填了非空值，它会盖掉锁存 ⇒ 本 plan 把它当坑铲掉）。
**多一个副作用要写进文档**：`rl/modes.py:91-95` 的 legacy 回退消失 ⇒ 不再有「intent_rl 悄悄覆盖 rl」这种隐式行为。

### 3.2 P1 — 清 B 类重复项（需先出覆盖矩阵）

1. 出矩阵：在训课程（§4 S2 判据）× B 类键 → 值与来源（课程文件/level 注入/rl-config/缺省）。
2. 只对「全绿（所有在训课程都显式声明）」的键执行删除；不全绿的键**保留但加注释**：
   `// 兜底：仅当课程未声明时生效；新课程请写进 curricula/*.jsonc`
3. 已知的全绿候选（x 系列实测）：`difficulty`（level 注入 hard）、`max_ticks`（level 注入 12900）、`seed_rotate`（课程 150）、`mb`（课程 1024）、`target_transitions`（非 rl-config 键，仅举例）。
4. **不动** `local_slots`（本机直跑槽位是**机器属性**，见 `NodePills.tsx:213-227` 的读数）与 `torch_threads`（进程级）——它们其实属于 A 类，别误删。

### 3.3 P1 — ~~`intent_rl` 重启配方~~（**2026-09-26 作废，§0.5.1-2：不做**）

删除的 18 个独有键逐键给出「新家」，写进 `docs/nn/rl-config.md`（§4 S5）：

| intent_rl 键 | 新家（重启 intent 线时） |
|---|---|
| `bc` / `out` / `traj` / `iters` / `epochs` / `lr` / `warmup_iters` / `seed_rotate`… | **课程文件**（`curricula/<intent-课>.jsonc`）——与 x 系列完全同构（CourseConfig 已有字段） |
| `kickstart_kl` / `kickstart_decay` | 课程 `kickstart_init`（+ gates）；缺字段的按重启时的口径补 |
| `eval_at` / `eval_seeds` | 课程 `eval_stages` / `eval_games_per_stage` / `eval_every`（x 系列口径） |
| `kl_break` / `kl_break_consec` | 课程 `gates` 块（`ent_break*` 同族） |
| `baseline`（0.723 旧 ENT 基线） | **不带**（那是旧纪元的读数，按新口径重取） |
| `replan`（意图 rollout cadence） | 属 intent 执行器专属 ⇒ 重启时若仍需要，写进课程或 CLI，**不回 rl-config** |
| `out_log` / `err_log` | CLI（`run_rl` 已支持 `--out-log/--err-log`），不进配置 |
| `seeds_per_stage` | 课程（若 CourseConfig 缺该字段 ⇒ 列为「重启时先补 schema」的前置） |

> 一句话给未来的自己：**intent 线重启 = 新建一门 `curricula/intent-*.jsonc`，照 x 系列的字段写；
> `rl-config.json` 只提供机器级那几项。**（`.jsonc` 的字段清单以 `rl/config.py::CourseConfig` 为准。）

### 3.4 P2（**已裁决：做，默认告警不拒**）— 固化 schema，防再长草

新增 `nn-training/rl_config_schema.py`（或并入 `schema.py`）：顶层键白名单 + 每段键白名单；
`read_rl_config_file()` 之后由 `--strict-rl-config` 打开校验（缺省**关**，只显式开启时才检查）；
控制台 `stack/smoke.ts:64` 的「rl-config 契约」冒烟接上它 ⇒ **未知键在开训前就被点出来**（而不是静默沉睡成 legacy）。
**分级（已定）**：未知键一律**只告警不拒**（`log` 一行 + 冒烟面板红字提示），**不 block 开训**
——理由：拒绝会把「配置里多了一个手写键」变成「训练起不来」，代价远大于收益；
是否升级为硬拒另议（不在本 plan）。P2 也顺手保障 §3.2 的「兜底键必须带注释」惯例有地方落地。

---

## 4. 落点（file → 改后行为）

### S1 — `nn-training/tools/rl_config_clean.py`（新，一次性清洗脚本）

- `--dry-run`（默认）：打印**逐键 diff**（`键：旧 → 新`）+ 影响面（谁在读它、是否在训课程依赖）+ 备份路径。
- `--apply`：先写 `rl-config.json.bak.<YYYYMMDD-HHMMSS>`（同目录；该文件已被 `.gitignore` 覆盖，但**仍不进仓**），
  再按 §3.1/§3.2 的**显式白名单**删键；**只删白名单里的键**（不做「未知键一律删」——那是 P2 的活）。
- 全程脱敏（`remote_token`/`authKey` 只打印 `…` + 长度）。
- 删除前 `assert` 顶层结构完整（`version/policy/rl/nodes` 仍在）、`courses` 只删指定条目。

### S2 — 「在训课程 × B 类键」覆盖矩阵（脚本子命令）

`--matrix`：对每门在训课程（判据见下）解析课程文件 + level 注入 + rl-config 合并结果，
输出 markdown 表（键 × 课），标出**只靠 rl-config 兜底**的格子。
**在训判据**：`tmp/<课>/training-enabled.txt` 存在（与 `dashboard/src/server/api/state-view.ts:65-84`、
`hub_server._course_dir_live`、`loop_plan.enabled_courses` 同一判据）。

### S3 — `courses.*` 条目清理

删「非在训」的课程条目（实测待删候选：`x20-clutch-null`、`x3-power`、`x20-clutch`、`x20-steady`、`x20-demo-mix`、
`x20-terminal`…以 S2 的矩阵为准）。**注意** `courses.<课>.run_iters` 与 `gate_halt_mode` 都属 D 类，判据同样是「在训」。

### S4 — 生效值对照（清洗的验收手段）

清洗**前后**各跑一次启动段，抓 `run_rl.py:87` 的「生效启动配置落地日志」
（它逐键标注来源 `rl.<mode>` / `intent_rl(legacy)` / `rl`），做**逐键 diff**：
- 在训课程（per-tick）的生效值：**必须零变化**（除被删的死键）；
- 已删的 intent 键：允许变化，但每条要能对上 §3.3 的对照表。

### S5 — 文档

- `docs/nn/rl-config.md`（新）：A/B/C/D/E 五类的判据 + **「新键该写哪」决策树**（课程文件 or rl-config）
  + `intent_rl` 重启配方（§3.3）+ 清洗记录（清洗日期、删了什么、备份路径）。
- `DECISIONS.md` 一条：`rl-config.json` 的职责边界（**只有别处无处安放的才写它**）。
- 顺手修 `tools/agent/agent-setup.md:56` 的 `minDiskFreeMB` 指向。

---

## 5. 实施步骤（依赖顺序，每步自带验收）

**E0 — 备份**：`cp rl-config.json rl-config.json.bak.<ts>` 并**读回校验**（sha256 对比），记录路径。
验收：备份存在且 sha 相同。（红线 2）

**E1 — S2 矩阵**：跑 `--matrix`，把矩阵贴进 `docs/nn/rl-config.md` 草稿。
验收：矩阵覆盖所有在训课程；「只靠兜底」的格子全部标出。

**E2 — S1 dry-run**：`python -m tools.rl_config_clean --dry-run`。
验收：diff 逐键可读、脱敏、影响面标注正确；**零写盘**（用 mtime + sha256 双验）。

**E3 — S4 对照基线**：清洗前抓一次生效值日志（在训课程各一条）。验收：日志里能看到 `来源=` 标注。

**E4 — 用户执行 `--apply`**（agent 不代跑）：删 §3.1 的 P0 键 + §3.2 的全绿 B 类键 + §3.3 的停课条目。

**E5 — 清洗后对照**：再抓一次生效值日志，逐键 diff = §3.4 的期望。
验收：在训课程零变化；差异逐条有解释。

**E6 — 控制台与门禁**：`dashboard/src/stack/smoke.ts` 的「rl-config 契约」冒烟 + `bun run typecheck && bun run test`；
python 侧 `bash tools/githook/nn-py-safe.sh -m pytest tests/test_rl_config*.py -q`（若无对应用例则新增 §6.1 那两条）。

**E7 — S5 文档 + DECISIONS**。

**E8（P2，已裁决做）— schema 固化**：白名单 + 冒烟接线（§3.4）；**只告警不拒**。
验收：手工往 rl-config 塞一个假键 ⇒ 启动日志/冒烟面板出现告警且**训练仍能起**；删掉假键 ⇒ 告警消失。

命令（pytest 一律走它；全量门禁交给用户）：

```bash
cd nn-training
bash ../tools/githook/nn-py-safe.sh -m pytest tests/test_rl_config_clean.py -q
cd dashboard && bun run typecheck && bun run test
# 全量：bash tools/githook/nn-python-gate.sh（用户跑）
```

---

## 6. 测试与 Definition of Done

### 6.1 用例清单

| 文件 | 动作 | 用例 |
|---|---|---|
| `nn-training/tests/test_rl_config_clean.py`（新） | 新增 | dry-run 零写盘（mtime+sha 双验）· 备份先于删除 · **只删白名单键**（未知键不动）· 脱敏（token/authKey 不出现在输出）· `courses` 只删指定条目 · 顶层结构完整性断言 |
| 同上 | 新增 | S2 矩阵纯函数：来源判定（课程 > level 注入 > rl-config > 缺省）与「全绿」判定 |
| `nn-training/tests/test_rl_config_schema.py`（E8） | 新增 | 未知键**告警**（不是拒）· 已知键不误报 · 白名单与 `read_rl_config_file` 的字段表不漂 |
| 既有 | 保留 | `rl/config.py::read_rl_config_file` 与 `rl/modes.py::merged_mode_args` 的既有用例（含 `e2e/test_run_rl_m1.py:157-170` 的 legacy 回退断言 —— **删 intent_rl 后这条要按新语义改**，见 §7-R3） |

### 6.2 DoD

- [ ] `intent_rl` 块不存在；`policy.upgradeBranch` / `minDiskFreeMB` / `*Intent` 三键不存在；`courses` 只剩在训课。
- [ ] 在训课程（x20-dodge-l1/l3 等）清洗前后**生效值逐键一致**（§4 S4 的 diff 表）。
- [ ] 备份存在且 sha 可验；`rl-config.json` 的 `remote_token`/`authKey` 未出现在任何日志/文档/入库文件。
- [ ] 控制台 rl-config 契约冒烟绿；`bun run typecheck && bun run test` 绿；python 子集绿（全量由用户跑）。
- [ ] `docs/nn/rl-config.md` 决策树 + intent 重启配方 + 清洗记录已落；`DECISIONS.md` 一条。

---

## 7. 风险与回退

| 风险 | 判据 | 处置 |
|---|---|---|
| R1 删 B 类键后某课程行为漂移 | §4 S4 的 diff 出现非预期差异 | 只删「矩阵全绿」的键；有疑义一律保留 + 注释兜底 |
| R2 误删凭据/登记表 | 控制台 smoke 报 token/节点缺失 | 白名单机制（只删列出的键）；`nodes`/`remote_*` 永不在白名单里 |
| R3 删 `intent_rl` 打破既有用例 | `e2e/test_run_rl_m1.py:157-170` 断言 legacy 回退 | 那是**废弃语义**的断言 ⇒ 按新语义改写（去掉 legacy 回退、保留 `rl.<mode>` → `rl` 两级） |
| R4 `courses` 删了活课条目 ⇒ 切离线失效 | 控制台切离线后 `rollout_src` 写不进去 | S3 判据用「在训」而不是「我记得它停了」；删前逐条列给用户确认 |
| R5 schema 固化（P2）把用户卡住 | 开训报未知键 | **已定：只告警不拒**（§3.4）；用例盯住「告警不等于拒」 |
| R6 未跟踪 md 入库 | `git add` 了本 plan | 判据是 `git ls-files <path>`；按红线 #3 |

---

## 8. 裁决（2026-09-25 用户拍板，三点均按推荐档，已锁进正文）

1. **O1 = 只删「矩阵全绿」的 B 类键，其余保留为兜底 + 注释**（§3.2）。
   **否决**「全删、零缺省、课程必须显式声明」：会让历史课程无法复现，且新课程漏声明时是**静默**落到 argparse 默认；
   也否决「B 类一律不动」：那等于放弃本 plan 的主要收益（`mb`/`max_ticks`/`seed_rotate` 这类第二事实源继续留着）。
2. **O2 = `intent_rl` 整块删**（§3.1），**不留空壳**（`intent_rl: {}` 只会让后来者以为它还有语义）。
   删掉后 `rl/modes.py:83-95` 的 legacy 回退分支成为死代码 ⇒ 一并去掉（§7-R3 的用例同步改）。
3. **O3 = schema 固化做，默认只告警不拒**（§3.4、§5 E8）：未知键在启动日志与控制台冒烟里**点出来**，
   但**不 block 开训**；升级为硬拒不在本 plan。

**残余开放问题**：无。

---

## 9. 证据与参考（仓内）

- 现状：`nn-training/rl-config.json`（90 叶子；本节所有值均为实测）。
- 读链：`dist_common.py:59-71`、`rl/config.py:49-52`（读入口）、`:1269`（合并优先级）、`:1339-1340`（`rl`/`courses` 段）、
  `rl/modes.py:83-95`（`intent_rl` legacy 回退，**cleanup 的主对象**）、`rl/loop_serve.py:224-255`（课程级机器旋钮）、
  `rl/loop_steps.py:87/131/171/235`（训练期热读）、`rl/kickstart_burn.py:125-128`、`rl/paired_kill.py:128`。
- 死键证据：`rl/dispatch.py:191`（`upgradeBranch` 的**兜底读点**，配 `dist_common.py:522-525 upgrade_branch_or`：显式值优先于锁存 ⇒ 非空即盖锁存 ⇒ 事故载体；当前空值 ⇒ 删除等价）；
  `tools/agent/agent-setup.md:56`（`minDiskFreeMB` 唯一命中，实为 TS 侧 `sampler-agent.ts:689` 的函数名）；
  `rl/stream.py:193-194`（两个 `*Intent` 的唯一消费者）。
- 控制台面：`dashboard/src/core/config.ts`（load/save）、`stack/specs.ts:163`（`trainModeKnobs` 唯一换算）、
  `stack/smoke.ts:64-83`（rl-config 契约冒烟）、`server/actions/preset.ts:49-138`（回写 `rl.cf_*`/`stream`/`double_buffer`/`precollect_early`/`hub_push`）。
- 生效值日志：`run_rl.py:87`（逐键来源标注 `rl.<mode>` / `intent_rl(legacy)` / `rl`）—— 清洗的验收手段就靠它。

---

## 10. 一句话给接手 agent

**先备份+出矩阵（E0–E1），再只删白名单里的三批：死键（upgradeBranch/minDiskFreeMB/两个 `*Intent`）、
`intent_rl` 整块（不留空壳，顺手去掉 `rl/modes.py` 的 legacy 分支）、矩阵全绿的 B 类重复项与停课的 `courses.*` 条目；
删除前后用 `run_rl.py:87` 的生效值日志逐键 diff，在训课程必须零变化；
然后做 E8（schema 白名单，**只告警不拒**），最后把「新键该写哪」的决策树与 intent 重启配方写进 `docs/nn/rl-config.md`。**
