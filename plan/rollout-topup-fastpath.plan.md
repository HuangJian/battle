# Plan: rollout-topup-fastpath — 补差从「每批一个完整 rollout 轮 + 缺口解算」改成「轮内增量 + 按 worker 数满铺 + 取消搬回」

> **状态**：**v4**（2026-10-11 二轮修订；评审 `plan/rollout-topup-fastpath.review-bf.md` 的 P0×3 / P1×4 / P2×4 已并入）。
> **落地进度见 §10**（本轮已落 §3.1 + §3.3；§3.2/§3.4/§3.5 是下一刀，其前置判据已写进本文）。路径基准除注明外相对 `nn-training/`。
> **触发**：Kaggle TPU 自主 worker（96 核 cgroup 配额 / 94 并行槽）在训课程 `x24-stack5`：
> 96 核机器上 rollout 稳态应 < 10s，实测大面积 60–111s（**54** 轮里 p50=11.8s、p90=88.9s、max=111.2s；
> v3 曾写「53 轮」——与 §1.1/§1.11 表的 31+23=54 不符，以逐轮账本为准，见评审 P1-3）。
> **上位**：`plan/rollout-stage-balance.plan.md`（2026-10-07，本机制的设计者）·
> `plan/rollout-local-scratch.plan.md`（2026-10-08，热路径 IO 落本地盘）·
> `plan/rollout-concurrency-adaptive.plan.md`（正交：治 IO 的**量**；本份治 IO 的**次数**）。
> **不冲突**：本 plan 不动并发度口径、不动单局看门狗常量、不动 `data_fp` 协议。
> 修的是**批与批之间的重复固定开销** + **批的粒度口径** + **一个裸调无超时的探针**。

---

## 0. 一句话目标

> **整条 rollout 路径上，「跑局」是唯一的墙钟成本；其余全部消隐。** 四刀：
> **(1)** 补差批**不再是一个完整 rollout 轮**（轮级固定开销一轮只付一次）；
> **(2)** 补差**不按缺口解算**，**按 worker 数整批铺满**，多余样本直接丢；
> **(3)** **产出不搬回**网络盘（网络盘只留结果清单），只在回传失败时才备份失败内容；
> **(4)** **scratch 速度探针必须有上界**（今天它裸调、无超时，且**每批**都在网络盘上写 8MB + fsync）。

**诚实账（先说不承诺什么）**：稳态好轮（it2–it25，6–13s）的补差本身只花 3–5s，
第 (1) 条改完也就省 1–2s。本 plan 治的是**坏轮**：it26 之后 p90 88.9s。
第 (2)(3) 条是**结构性**收益（见 §1.7：批越小，固定开销占比越接近 100%；搬回本身就是纯税）。
第 (4) 条是**单点主因**（见 §1.11：它是唯一没有上界的一步）。
改完的目标是让 rollout 回到**与工作量成正比**（≈ 局数/94 × 单局 p90 + 一次固定开销），
即 96 核上 **< 15s**。

---

## 1. 现场与根因（逐条落到代码，全部已闭合）

### 1.1 现场的「分裂」：工作量不变，墙钟差 10 倍

`tmp/x24-stack5/remote-jobs/offline/0805a8f36a305708/it-*/row.json`（54 轮，逐轮实证）：

| 区间 | rollout_sec | games | samples | avgTicks | ppo_sec |
|---|---|---|---|---|---|
| it2–it25 | 6.0 – 12.7 | 126–147 | 49.5k–51.9k | 3442–4111 | 18.5–19.0 |
| it26–it55 | **45.4 – 111.2** | 124–142 | 49.6k–53.6k | 3572–4170 | 18.5–22.9 |

**局数、样本数、每局 tick 数在两侧几乎完全相同**。PPO 侧 `ppo_sec` 从头到尾恒定
18.5–22.9s ⇒ **不是 NN 变慢、不是关卡变长、不是样本变多**。差别只在它**周围那一圈壳子**：
`rollout_sec` 含「跑局 + 搬回 + 扫盘 + 清理」。

### 1.2 单批拆解：跑局只占零头，剩下全是空等

用户提供的真机日志（it14 / it34 / it48 / it49 的 9 个批次）。「跑局下界」=
`max(单局 max, Σ单局 / 并发)`（每批自己报的 p99/max 都是 ≤3s）：

> ⚠ **v4 更正（评审 P2-2）**：v3 用的是 `ceil(局数/并发) × 单局 max`（**波次模型**的耗时，不是下界）——
> 它给出的 it14 首批 **−1.51s** / it48 首批 **−1.08s** 负「空等」就是自证的破绽（下界不可能大于实测）。
> 表内数字按旧式保留（结论不依赖它）；**判读只认「1 局的批也能跑 48.9s」那一列**。

| 批次 | 总墙钟 | 局数 | 并发 | max 单局 | 理论下界 | **空等** |
|---|---|---|---|---|---|---|
| it14 首批 | 4.37s | 116 | 94 | 2.94s | 5.88s | −1.51s |
| it14 补差1 | 4.93s | 15 | 15 | 2.09s | 2.09s | +2.84s |
| it14 补差2 | 2.53s | 4 | 4 | 2.42s | 2.42s | +0.11s |
| **it34 首批** | **38.96s** | 116 | 94 | 2.99s | 5.98s | **+32.98s** |
| **it34 补差1** | 3.35s | 18 | 18 | 2.05s | 2.05s | +1.30s |
| **it34 补差2** | **48.90s** | **1** | **1** | **1.15s** | **1.15s** | **+47.75s** |
| it48 首批 | 4.78s | 116 | 94 | 2.93s | 5.86s | −1.08s |
| **it49 补差1** | 3.00s | 15 | 15 | 1.87s | 1.87s | +1.12s |
| **it49 补差2** | **52.49s** | **1** | **1** | **1.43s** | **1.43s** | **+51.06s** |

**★ 最刺眼的一对：it34 补差1（18 局）= 3.35s，it34 补差2（1 局）= 48.90s。**
同一个 job、同一次会话、相隔几十秒。同一形状在 it49 重现（3.00s → 52.49s）。

同一份日志里四条否定式同时成立：

* `≥5s 有 0 局` ⇒ **没有任何一局超时**（不是慢局）；
* `重试过的局 0 个` ⇒ **没有重试**；
* `serve_pool: … killed=0 unready=0 fallback=0` ⇒ **池零回退、零熔断**（不是冷启动风暴）；
* `搬回=1 局/失败 0 局` ⇒ **搬回成功，没超界**。

⇒ 47–51s **既不在跑局、也不在重试、也不在池回退、也不在搬回超界**。

### 1.3 根因 A（每批付一次壳子）：每批 = 一个完整 `run_iter_rollout` 轮

`worker/iter_topup.py:247` —— 补差批直接复用整轮执行器：

```python
info = run(sub, spec2, ts_dir=ts_dir, log=log)   # run = worker.iter_rollout.run_iter_rollout
```

`iter_topup.py:194` 确认 `run = run_fn or run_iter_rollout`。于是**每一批**都完整走一遍
`run_iter_rollout` 的全部轮级步骤，而这些步骤的成本**与批内局数无关**：

| 步骤 | 住址 | 成本 | 与局数相关？ |
|---|---|---|---|
| scratch 落点解析 + **速度探针** | `iter_rollout.py:852` → `scratch.resolve_scratch_root` | **写 8MB + fsync × 2**（job 目录基准 + 候选盘） | **否** |
| TS 树安装 | `iter_rollout.py:856` `install_tree` | 拷整棵 TS 树（内容寻址 ⇒ 跨批**命中**，仅首批真付） | 否 |
| **权重镜像** | `iter_rollout.py:863` `mirror_inputs` | 拷 `init_weights.json` 进**本批私有** `r/` | **否** |
| 长驻池建 + 冷启动 | `iter_rollout.py:902-904` `pool.start()` | `workers` 个 bun：exec + traverse + wasm + attest×3 | **否** |
| **轮末扫盘三连** | `iter_rollout.py:1125-1136` `_scan_bounded` | `verify_shards`（rglob+**内容哈希**）/`collect_reports`/`collect_shard_manifests` | 弱相关 |
| **轮末清理** | `iter_rollout.py:1096-1107` | `clear_dir(exec_dir)` + `drop_drain_stage` | 否 |
| `bun_version` / `resolve_bun` | `iter_rollout.py:793-794` | 起一个 bun 读版本 | 否 |

**scratch 私有目录按批分裂**（`common/scratch.py:124` `scratch_job_dir` 用 `job_root.name` +
`hash8(绝对路径)` 命名）：

```
首批    job_root=<job_dir>         -> /dev/shm/nn-rollout-abc123-cfddb926
topup1  job_root=<job_dir>/topup1 -> /dev/shm/nn-rollout-topup1-dc4c36dc
topup2  job_root=<job_dir>/topup2 -> /dev/shm/nn-rollout-topup2-5b8bc08e
```

⇒ 只有**TS 家**（`scratch.ts_home`）跨批命中——⚠ 但它是按 **TS 目录名**寻址（`ts_home(root, name)`
→ `nn-rollout-ts-<slug(name)>`，`scratch.py:136`），**不是**内容寻址（v3 与 docstring 都写错了，见评审 P2-1）；
**作业私有的 `r/` 每批全新** ⇒ 权重镜像、轮末清理、drain 暂存根**每批重付**。

这正是 `docs/nn/runtime-opt.md` §20（长驻池「逐局 spawn 每局重付启动成本」1.59×）的
**批次级重演**，而池化本身没能救它——因为池的生命周期就是**批**。

### 1.4 根因 B：池的生命周期 = 批，不是轮

`iter_rollout.py:1091-1095`：池在 `finally` 里无条件 `pool.close()`。

```python
finally:
    if pool is not None:
        rb.note(pool.summary(), final_only=True)
        pool.close()          # ← 每一批结束都把暖 worker 全杀掉
```

日志侧证：首批 `spawned=94`、补差1 `spawned=15`、补差2 `spawned=1` —— **每批的 worker
数恰好等于该批局数**，说明每批都在**从头建池**。15 局的补差批只建 15 个 worker
（`workers = min(requested, len(argvs), …)`，`iter_rollout.py:802-809`），
所以补差批**既付冷启动、又只用极小的池宽**——两头吃亏。

### 1.5 根因 C：搬回与扫盘是主线程串行

> ⚠ **v3 注**：本节描述现状为何慢。但搬回**不是要并行化，是要删除**（§1.8 根因 E）。
> 保留本节，是因为它证明「为什么盘抖动会被放大」——若 §3.5 因故回退，这里就是回退档的代价清单。

* `_drain_one`（`iter_rollout.py:934`）在**主线程**、**逐局串行**：`drain_tree` 内部是
  `copytree` → **`_dir_stat` 递归 stat 每个文件**（`scratch.py:487`）→ `os.replace`。
  网络盘上每次 stat 是一个 RTT，116 局 = 116 × (文件数 × RTT)。
* `_scan_bounded` 三步（`iter_rollout.py:1125-1136`）同样在主线程串行，各自上限 60s。

### 1.6 it26 转折点的三个旧候选（**v3 已由 §1.11 取代**）

it2–it25 稳态 6–13s，it26 起跳到 46–111s。**机制（A/B/C）全程都在**，所以触发条件在别处。
v1 列了三个候选：

1. **PPO 落盘与 rollout 打盘**撞车 ⇒ 探针把候选盘判成「不够快」⇒ 回退原地。
   ⚠ 但日志明写 `产出落点=/dev/shm（本地盘）` ⇒ **这条被现场读数否掉了**。
2. **`/dev/shm` 被抢占**：`need` 973MB × 1.5 = 1.46GB 只是下限，不解释 10 倍。**排除**。
3. **Kaggle 网络盘周期性抖动**：**v3 用本地账本把它钉死了**，见 §1.11。

### 1.7 根因 D（结构性）：批的大小由「缺口方程」决定，不由「机器宽度」决定

`worker/volume_alloc.py:87` `topup_games_by_stage` 是**缺口解算器**，不是**铺满器**：

```python
remaining = quota - got
n = _ceil_div(remaining, est)          # ← 「还缺多少样本 ÷ 单局能产多少」= 精确到局
if cap > 0:
    n = min(n, max(0, cap - done))
```

它的**设计目标**是「不缺口」，副产品是**批必然小且越补越小**。实测形状：

| 批 | 局数 | 并发 | 该批墙钟 |
|---|---|---|---|
| it34 补差1 | 18 | 18 | 3.35s |
| it34 补差2 | **1** | **1** | **48.90s** |

**批越小，固定开销摊得越薄 ⇒ 占比越接近 100%。** 即：**根因 A 的伤害被根因 D 放到最大**。
A 说「每批付一次壳子」，D 说「批小到壳子就是全部成本」——同一个 bug 的两端。

### 1.8 根因 E（纯税）：搬回（`drain`）是**不必要**的一段

今天产出在 `/dev/shm`，跑完**逐局**搬回 `/kaggle/working`（网络盘），再在轮末对网络盘上的
副本做扫盘三连（`verify_shards` 的 rglob + **内容哈希**）。

**但下游根本不读它**（§1.9）。搬回的唯一实际作用是把「单局 `copytree` + `_dir_stat`
递归对账 + `os.replace`」插到**主线程**里，每局一次 RTT × 文件数。

### 1.9 「不搬回」的架构验证（**已闭合，结论：成立**）

| 环节 | 住址 | 读的是哪份产出 | 不搬回是否成立 |
|---|---|---|---|
| **PPO 读 shard** | `remote/train_core.py:553` `load_episodes(shards_root, …)`，而 **`:526 shards_root = str(job_dir)`** | **节点自己的 PPO** 递归扫 `job_dir`（`np_core.discover_shards:517`）——换根档的产出**就是靠 `_drain_one` 搬回 `job_dir` 才有得读** | ⚠ **必须改**（评审 P0-1）：搬回是**承重段**，不是纯税。`shards_root` 要改成 scratch 的 exec 根（回退档仍 `job_dir`），见 §3.5 |
| shard 发现 | `worker/ppo/np_core.py:493` `discover_shards(data_root, need_files)` | 同上 | ✅ 只要求路径可读 |
| `data_fp`（节点侧） | `iter_rollout.py:1126` `verify_shards(jd, expected_fp)` | **`jd` 下 rglob** | ✅ `jd` 不要求是网络盘 |
| `data_fp`（hub 侧） | `remote/hub_client.py:1247-1262` | kind=iter ⇒ **manifest 声明集**（docstring 明写「hub 侧无本地副本可重算」） | ✅ **shard 本体从不回传** |
| job 结果体 | `remote/job_fs.py:59` `_persist_result` | 只写 `_result.json` | ✅ 结果体里没有 shard 本体 |
| 轮末 prune | `remote/job_fs.py:72` `prune_job_dirs` | 删整个 job 目录 | ✅ 与本 plan 无关 |
| **D14 语料血缘** | `remote/worker.py:682` `for _sd in shard_dirs: open(_sd/"manifest.json")` | **逐个 shard 读本地 `manifest.json`** | ⚠ **唯一需要改的一处**：`shard_dirs` 必须指向**可读**的产出根（scratch），而非「搬回后的网络盘副本」 |

⇒ ⚠ **v4 更正（评审 P0-1）**：v3 的「链上没有任何一环要求产出落网络盘 / 搬回是纯税」**是错的**。
换根档下**有两个**承重读方，且第二个是节点自己的训练核：

1. **D14 血缘**（`remote/worker.py:682`）——只要 `shard_dirs` 报**真实产出所在路径**，它逐字不变；
2. **节点 PPO**（`remote/train_core.py:526 shards_root = str(job_dir)`）——今天读的**就是搬回后的副本**。

⇒ §3.5 必须**同时**把 `shards_root` 改成「真实产出根」（回退档 = `job_dir`，换根档 = scratch 的 exec 根），
否则 job 内 PPO 会以 **0 局语料**开练（静默训空 / 响亮报无 shard）。这一条是 §3.5 的前置，不是余项。
「搬回 → 不搬回」省下的是**主线程逐局 copytree + 递归 stat + os.replace**，这笔账不变；
只是收益的代价从「下游少一份副本」变成了「下游改读另一个根」。

### 1.10 根因 F（同理念，LAN 侧）

`trainer/dispatch_queue.py:55 run_rollout_queue(...)` 是 LAN 集群侧的 rollout 队列入口
（`dispatch_queue.py:27 local_slots_max_of(args)` 定槽位）。它今天同样按「缺多少补多少」
派单 ⇒ 同一台机器上「三五局差额 ⇒ 再起一轮池 ⇒ 再付一次固定开销」。
根因 A/B/D 在 LAN 侧**同构存在**。见 §3.9。

---

### 1.11 ★★ 主根因 G：scratch 速度探针**裸调、无上界**，且每批都在网络盘上写 8MB + fsync

> **本节是 v3 新增的本地取证结论**（不需要真机，全部由现有代码 + 现有账本推出）。
> 它把 §1.6 的「三个候选」收敛成**唯一解释**，并把 P0 从「等真机读数」变成「可立刻动手」。
>
> ⚠ **v4 加注（评审 P1-3）**：其账本 `tmp/x24-stack5/.../row.json` 与「跨课程 500 轮表」**不在仓库里**
> （`tmp/` 不入库，`find -name row.json` = 0）⇒ **评审侧不可复算**；且原文三处口径不一
> （表 31+23=**54** vs 表头 53；跨课程表逐行相加 **554** vs 标题 523 vs 正文 500；`x21-psh-b` 两行同名不同腿）。
> ⇒ **判据不受影响**（「唯一无上界的步骤」由代码自证：`iter_rollout.py:852` 裸调、`scratch.py:158-181` 无超时），
> 但「坏轮 = 探针撞盘抖」这一**归因降级为假说**，直到 §5-A 给出**仓内可跑的复算配方**。
> 「实测注」里的本机 4.65s 读数同理：可复现的**判别**（探针耗时线性累加）已由 §5-B1 落成用例，数字本身不入账。

**代码链（逐环有出处）**：

```
iter_rollout.py:852   picked = scratch.open_scratch(jd, need_bytes=need, log=rb.note)
                        ↑ ★ 裸调：没有 call_bounded、没有超时、没有熔断
  └─ scratch.py:264   resolve_scratch_root(Path(job_root), ...)
       ├─ scratch.py:224  base_speed = measure(Path(job_root))
       │                    ↑ ★ 在【job 目录 = 网络盘 /kaggle/working】上
       │                      写 8MB + os.fsync + 回读（scratch.py:158 probe_write_speed_mbps）
       ├─ scratch.py:228  _is_writable_dir(cand)     ← 每个候选一次 mkdir + 写探针 + unlink
       ├─ scratch.py:232  free_bytes(cand)
       └─ scratch.py:237  speed = measure(cand)      ← ★ 再写 8MB + fsync + 回读（本地盘）
```

**四个关键事实**：

1. **`probe_write_speed_mbps` 没有超时**（`scratch.py:158-181`：纯 `open/write/fsync/read`，
   只有 `except OSError`）。网络盘抖到 0.2 MB/s ⇒ 这一项就是 **40s+**。
2. **它跑在 `call_bounded` 之外**。同文件的 `install_tree`（`:856`）与 `mirror_inputs`（`:863`）
   都被 `call_bounded(..., SCAN_CEILING_SEC, ...)` 包住（上限 60s）——
   `call_bounded` 的 docstring（`platform_utils.py:511`）明写它防的就是
   「**挂住的挂载点上的 `mkdir`/`open`/`write`**」。**唯独探针段漏在外面。**
3. **它是每批一次，不是每轮一次**（`open_scratch` 在 `run_iter_rollout` 里，每个批都调）。
4. **它是纯诊断用途**：只决定「要不要换到本地盘」。**为这个决策，每批在网络盘上写 8MB + fsync。**

**账本侧的三条硬证据（不需要真机）**：

**证据 1 —— 坏轮的增量散布，不是常数**（`tmp/x24-stack5` 54 轮）：

| | n | rollout p50 | `wall − rollout − ppo` 残差 p50 |
|---|---|---|---|
| 好轮（rollout < 30s） | 31 | 8.61s | **0.91s** |
| 坏轮（rollout ≥ 30s） | 23 | 64.78s | **0.82s** |

⇒ 多出来的 36–103s **全部落在 `rollout_sec` 内部**，与 PPO／落盘／进程管理无关。
且增量是**散布的**（+36、+38、+42…+103s），**不是固定常数** ⇒ 否掉「某一步永久变慢」，
指向「某个**阻塞等待**的时长随盘速连续变化」。

**证据 2 —— 同一轮内相邻两批天差地别**（否决「这一轮的盘慢」）：

```
it34:  补差1（18 局）= 3.35s     补差2（1 局）= 48.90s
it49:  补差1（15 局）= 3.00s     补差2（1 局）= 52.49s
```

两批相隔几十秒、同一个 job 目录、同一份代码路径。**唯一能同时在两批里发生、
又给出 15 倍差异的，就是「探针在两次调用之间撞上/没撞上网络盘抖动」。**
（`install_tree` 跨批内容寻址命中、`mirror_inputs` 是几 KB 的 `init_weights.json`、
池只有 1 个 worker —— 都不足以解释 48s。）

**证据 3 —— 跨课程对照**（`tmp/*/remote-jobs/offline/*/row.json`，523 轮）：

| 课程 | n | p50 | p90 | ≥30s 占比 | games 中位 |
|---|---|---|---|---|---|
| x21-psh-b | 84 | 3.92 | 4.30 | **1.2%** | 132 |
| x21-psh-b | 142 | 3.05 | 3.39 | **1.4%** | 148 |
| x21-psh-k10 | 148 | 3.98 | 4.25 | 4.1% | 292 |
| x21-psh-k5 | 126 | 6.79 | 7.92 | **0.8%** | 99 |
| **x24-stack5** | 54 | **11.84** | **88.86** | **42.6%** | 133 |

**同一套机制、同为远程腿，500 轮里坏轮 <5%，x24-stack5 却 42.6%。**
⇒ 机制（A/B/C/D/E）是**共有的放大器**，**触发源是 x24-stack5 这份 job 所在的环境**
（网络盘状态 / 会话 / 落盘竞争）。而 §1.6 的三个候选里，只有「网络盘抖动」能同时解释
证据 1/2/3。

**结论（v3 归因，唯一解释）**：

> **根因 G = 网络盘抖动 × 探针段无上界 × 每批一次。**
> A/B/D 是**放大器**（把 G 的伤害从「一次探针」放大到「每批一次 + 空转壳子」），
> C/E 是**放大器**（把抖动从探针段扩散到 116 局串行搬回）。

**为什么这条比 v1 的说法有用**：它给出一个**单点、可立刻验证、可立刻修**的位点——
把 `open_scratch` 包进 `call_bounded`（或给探针加超时 + 缓存），
**即使不改 A/B/D/E，坏轮也会立刻从 48–111s 掉下来**（因为 47s 里的绝大部分就是这个裸等）。
这是 §3.1 的落点，也是 §4.2 真机验收的**第一条判据**。

**实测注（本轮已跑，B 档可行性的直接证据）**：`open_scratch` 的 `measure()` 被调用
**1 + len(候选) 次**（`scratch.py:224` 的 `base_speed` + `:237` 每个候选一次）。
在本机用 `probe=` 注入一个「每次调用固定睡 1.5s」的假探针、并给 2 个候选，
`open_scratch` 整段耗时 **4.65s**（= 3 × 1.5s + 开销）⇒ **探针耗时线性累加到整段**，
可观测、可断言。真机上是 **2 次**（`base_speed` 打网络盘 + `/dev/shm` 候选 1 次），
**每次 8MB + fsync + 回读**。

---

## 2. 语义规格（先定死，再写码；改动任何一条 = 新实验）

### 2.1 核心不变式（一个字都不许动）

| 不变式 | 出处 | 本 plan 的态度 |
|---|---|---|
| `verify_shards` 的「实产集 == **本批**声明集」逐位成立 | `iter_rollout.py:1126`；评审 P0-3 | **不动**。每批仍按自己的 `spec2` 声明自己的 `argv`，`data_fp` 仍两侧同函数 |
| 批目录隔离（`topup{k}/`） | `iter_topup.py:52` `BATCH_DIR_PREFIX` | **不动**。它防的是 `scan_shard_dirs` 把前几批扫进来 ⇒ 逐位对账失真 |
| `--out` 全局续号不重叠 | `iter_topup.py:74` `_out_base` | **不动** |
| 同一条连续流续抽（`continuous_pairs(start_idx=已产局数)`） | `iter_topup.py:88` `next_batch_spec` | **不动** |
| 同关取 argv 模板（`retarget_argv` 的 `stage_json=None` 会删那一对） | `iter_topup.py:88` | **不动** |
| 回退档（`s_layout is None`）= 逐字今天的行为 | `scratch.py:12`；评审 F9 | **不动**（新增档仍须与回退档逐字一致） |
| 课程 `volume` 块语义（`enabled`/`max_batches`/`last_lo_factor`/`max_games_per_stage`） | `volume_alloc.py:221` | **不动**（见 S8） |
| `data_fp` 逐位值 | `iter_expected_data_fp` / `hub_client.py:1247` | **不动**（不搬回**不改变**任何一局的 seed/argv ⇒ 指纹逐位不变） |
| 单局看门狗常量与 `game_ceiling_sec` 公式 | `common/game_watch.py` | **不动** |
| 池的真就绪判据 / 分批冷启动 / 熔断语义 | `serve_pool.py`（`rollout-serve-pool-readiness`） | **不动**（本 plan 只改池的**生命周期**） |

### 2.2 新语义（本 plan 引入的全部）

**(S1) 轮级上下文在批之间复用。** `run_iter_rollout` 拆出可复用的 `RoundContext`：
`jd` / `tsd` / `s_layout` / `pool` / `gate` / `exec_jd` / `workers`（轮级池宽）/
`picked`（探针结论）/ `batch_specs` / `shard_dirs`。首批构造，补差批**传入**而非重建。

**(S2) 池的生命周期 = 轮，不是批。** `pool.close()` 从 `run_iter_rollout` 的 `finally`
（`iter_rollout.py:1091-1095`）上移到**整个 job 的收尾**（`remote/worker.py:658` 之后，
用 `try/finally` 包）。`make_pool` 的 `workers` 取**轮级**上限，不随批内局数缩水。

**(S3) scratch 私有目录 = 轮，不是批。** `ScratchLayout` 由 job 根解析一次
（`scratch_job_dir` 的 `job_root` 传**真 job 目录**，不传 `topup{k}`）。
⚠ **连带（评审 P0-3）**：目录由「每批一付」变成「全轮一付」后，容量口径必须同时改——
今天是 `need = need_bytes_for(本批局数)`（`SCRATCH_EST_DEFAULT_BYTES = 8MB/局`）
× `SCRATCH_MIN_FREE_RATIO = 1.5`，峰值 = **最大一批**；S3+S6 之后峰值 = **Σ批**
（上界 `width × max_batches`）⇒ 判据要按全轮需求算（或保留 per-batch 目录、轮末统一回收），
否则 `/dev/shm` 上会多出一种「轮末对账红」的新失败模式。

**(S4) ★ 速度探针必须有上界，且一轮一次。**
`open_scratch` 整段（含 `probe_write_speed_mbps` ×2 + `_is_writable_dir` + `free_bytes`）
**必须包进 `call_bounded`**，上界 = **新增常量 `game_watch.PROBE_CEILING_SEC`**（**不复用**
`SCAN_CEILING_SEC`，见下方「为什么不能用 60s」）；**超界 ⇒ 走回退档**
（`picked = None`，逐字今天回退档行为），并**响亮记一笔** + 轮报里留 `probe_probe` 读数。
结果（含 `None`）在轮内缓存。**判据不变**：缓存键 = `(SCRATCH_ENV, 轮级 job_root, need 档位)`。

> **为什么不能用 `SCAN_CEILING_SEC`（60s）——v4 修正，评审 P0-2**：`call_bounded` 超界要**等满**
> timeout 才返回（`platform_utils.py:511-545` 的 `done.wait(timeout)`），**不是**「超界即返回」的熔断。
> 复用 60s ⇒ 一次探针挂住就付 **60s**，而 §9 要 `rollout max < 30s`、`补差批 < 8s` ⇒ **自相矛盾**。
> 且 §2.3 明写「不动 `SCAN_CEILING_SEC`」——它同时也给轮末扫盘三连定了 60s（扫描确实可能要几十秒，
> 不能为了探针把它压小）。⇒ 两个语义拆成两个常量：扫描维持 60s，探针另立（建议 5–10s，`§5-B2` 标定）。

**(S5) ★ 补差批 = 「按机器宽度铺满」，不是「按缺口解算」。**
`topup_games_by_stage` 这个缺口解算器被**满铺器** `spread_games_by_stage` 取代（§3.4）。语义：

> 每一批的**目标局数 = 本机 worker 宽度 `W`**（`ctx.workers`，由 `cpu_worker_slots()` /
> `physical_cores()` 定）。把这 `W` 局**尽量均分**给「仍未达标且未触 `game_cap`」的关；
> **多余样本直接丢弃**。若一轮内仍有个别关缺口，**下一整批 `W` 局均分给这些关**
> （不缩批、不做小数配比）。批数上界仍是 `max_batches`（课程字段，语义不动）。

两条硬要求：**单批内 `sum(games_by_stage) == W`**（除非所有关都达标 ⇒ 空 dict ⇒ 停）；
**永不出现「1 局的批」**（只要有任何关未达标，这一批就铺满 `W`）。

**(S6) ★ 产出不搬回；网络盘只留「结果清单」。**
删除搬回环节（`iter_rollout.py:934` `_drain_one`、主循环调用点、
`finally` 里的 `clear_dir`/`drop_drain_stage`）。产出留在 `s_layout.exec_dir`，
`shard_dirs` 直接报 **scratch 路径**。网络盘（`job_dir`）只留：`init_weights.json` 镜像、
各批 `spec`/账本、结果体（`job_fs.py:59`）。**唯一例外**：回传失败时备份失败的那一份（§3.6）。

⚠ **连通项（评审 P0-1，必须与 S6 同笔落地）**：产出不再进 `job_dir` ⇒ **节点自己的 PPO 必须改读其他根**
（`remote/train_core.py:526 shards_root = str(job_dir)`）。改法：读取根**由产出方显式给出**，
例如 `iter_info["shards_root"] = str(exec_root)`（`exec_root` = 换根档的 `s_layout.exec_dir`、
回退档的 `job_dir`），`train_core` 用 `iter_info.get("shards_root") or str(job_dir)`——
`iter_info is None`（非 iter 腿）逐字不变。**并补一条新判据（§4.1）**：换根档下 PPO 装载源 == scratch exec 根。

**(S7) 轮末扫盘三连一轮一次，且按批分组验声明集。** 扫盘只在**全轮真正的收尾**做一次，
扫**全轮**（首批目录 + 各 `topup{k}/`）。⚠ **唯一一处语义扩张**：`verify_shards` 今天是
「`jd` 下 rglob 出的 shard 集 == 本批声明集」，改成全轮后**必须按批分组各验各的声明集**。
修法见 §3.4，判据逐位不变。

**(S8) 满铺器不新增 `volume` 块字段。** `validate_volume_block_ext`（`volume_alloc.py:221`）
的同源校验强验 `games_per_stage_by_stage == alloc_games_by_stage(...)`（**首批**口径，不受影响）；
`topup` 四字段校验不变。满铺器是**节点侧**的求解器替换 ⇒ **课程文件与 hub 派发面一个字不动**。

**(S9) 每批的账本独立，汇总在轮末。** `merge_iter_reports` 语义不变；
轮级 `elapsedSec` 的**双层口径**（`批内` 与 `全轮` 都上报，读数不混）。

### 2.3 非目标（明写不做）

* 不动 `SLOW_GAME_WARN_SEC` / `DEFAULT_GAME_TIMEOUT_SEC` / `GAME_MAX_ATTEMPTS` /
  `RETRY_TIMEOUT_FACTOR` / `game_ceiling_sec` 公式 / **`SCAN_CEILING_SEC`**（它管轮末扫盘）。
  **唯一新增常量 = `PROBE_CEILING_SEC`**（探针专用上界，§3.1；新增理由写进 DECISIONS，
  避免"常量增生"：它与 `SCAN_CEILING_SEC` 管的是两个不同语义，合用一个就必然两个都错）。
* 不动并发度口径（`cpu_worker_slots` / `physical_cores`）与 AIMD 探针。
  **本 plan 只是把已有的宽度读出来当批大小用。**
* 不动 TS 侧导出器 / `serve-loop.ts` 协议 / `serve-any.ts` mode 表。
* 不动 PPO / 课程 / 奖励 / `data_fp` 的十六进制值。
* **不动 `_dir_stat` 的递归对账**（它是 F3「不安装半截拷贝」的唯一判据）——
  它属于 `install_tree`（仍在用），**不属于被删掉的 `drain_tree`**。
* 不引入新的外部依赖。
* **不动 `max_batches` 语义**；`last_lo_factor` 在满铺器下**保留字段但不再生效**。

---

## 3. 实施方案

> **优先级（v3 重排）**：**P0 = §3.1 探针包上界（主根因 G，独立可验证）+ §3.2 ctx +
> §3.3 满铺器 + §3.4 扫盘 + §3.5 取消搬回**；P1 = §3.6 失败备份 + §3.7 池宽；
> P2 = §3.8 探针缓存 + §3.9 读数 + §3.10 LAN 队列。

### 3.1 ★★ P0-0 — 探针段包进 `call_bounded`（治根因 G，**最高优先级**）

**落点**：`nn-training/worker/iter_rollout.py:852`（**已落地**，见 §10）

```python
# 今天（裸调，无上界）：
picked = scratch.open_scratch(jd, need_bytes=need, log=rb.note) if argvs else None

# 改成：
picked = None
if argvs:
    ok_probe, probe_res = call_bounded(
        partial(scratch.open_scratch, jd, need_bytes=need, log=rb.note),
        float(game_watch.PROBE_CEILING_SEC),
        name="scratch-probe",
    )
    if ok_probe:
        picked = probe_res
    else:
        # 超界 ⇒ 走回退档（逐字今天的行为：不换根、不搬、不 drain），并响亮记账 + 进 `probe_probe` 读数。
        rb.add(
            "probe_probe",
            f"超界回退（{float(game_watch.PROBE_CEILING_SEC):g}s 内没返回）——盘抖的直接读数",
        )
        rb.note(
            f"WARN rollout scratch 探针超界（{float(game_watch.PROBE_CEILING_SEC):g}s 内没返回）"
            "——本轮回落原地（网络盘）；这是**盘抖动的直接读数**，不是失败"
        )
```

**为什么这是最高优先级**：

* **独立于其它五项**：只动一行调用点 + 一个 import。不改 `scratch.py`、不改语义、不改协议。
* **直接命中 47–51s**：`call_bounded` 超界后**定在 `PROBE_CEILING_SEC` 上界返回**（不等 8MB fsync），
  那 47s 当场变成 ≤ 上界的有界等待。⚠ **不是「立刻」**——`call_bounded` 等满 timeout 才返回，
  所以上界必须按 §9 的 DoD 反推（`补差批 < 8s` ⇒ 探针上界落在单秒级）；这正是 v4 拆出
  `PROBE_CEILING_SEC` 的原因（v3 说的「实际会远小于，因为盘抖是阵发的」不可当判据）。
* **`call_bounded` 就是为这件事造的**：它的 docstring（`platform_utils.py:519-525`）逐字写着
  「挂住的挂载点上的 `mkdir`/`open`/`write`」——探针段正是这个形态，
  只是当时（2026-10-06）漏了它。

⚠ **代价与残留**：超界后那条 daemon 线程仍在跑（`call_bounded` 不取消）。但它是 daemon、
不拖住收尾（docstring 明写）；且它写的探针文件带 pid（`scratch.py:160`），不会与下一批撞名。
**这是 `call_bounded` 的既定语义，不是本 plan 引入的。**

### 3.2 P0-1 — 抽出 `RoundContext`，批间复用（治根因 A）

**落点**：`nn-training/worker/iter_rollout.py`

```python
@dataclass
class RoundContext:
    """一轮 job 的**轮级**资源（首批构造；补差批传入复用）。"""
    jd: Path                                 # 真 job 目录（账本的根）
    tsd: Path                                # TS 代码根（可能已换到 scratch）
    s_layout: scratch.ScratchLayout | None   # None = 回退档
    exec_jd: Path                            # --out/--weights 的解析根
    gate: scratch.InstallGate
    picked: scratch.ScratchLayout | None     # 探针结论（S4，含 None）
    pool: object | None                      # 长驻池（生命周期 = 轮，S2）
    workers: int                             # ★ 轮级池宽（满铺器的 W，S5）
    batch_specs: list[tuple[Path, dict]]     # ★ 已跑批次（供轮末分组扫盘，S7）
    shard_dirs: list[Path]                   # ★ 全轮累计 shard 目录（不搬回 ⇒ 就是 scratch 路径）
```

`run_iter_rollout(job_dir, spec, *, ts_dir=None, log=…, ctx=None)`：
`ctx is None` ⇒ 现行为逐字（构造 ctx、自己 close 池）；`ctx is not None` ⇒ **跳过**
探针 / `install_tree` / `mirror_inputs` / 建池 / 轮末扫盘 / 轮末收池，
直接用 `ctx.exec_jd` / `ctx.pool` / `ctx.s_layout`，并把本批 `(批根, spec, shard_dirs)`
**追加进 ctx**。

**验收**：`ctx=None` 的所有既有用例**逐字不变**（`tests/remote/test_remote_iter.py` 一个字不改）。

### 3.3 P0-2 — 满铺器取代缺口解算器（治根因 D）

**落点**：`worker/volume_alloc.py`（新增纯函数）+ `worker/iter_topup.py:208-247`（换调用）

```python
def spread_games_by_stage(
    *,
    stages: Sequence[int],
    collected: Mapping[int, int],
    games_done: Mapping[int, int],
    target_transitions: int,
    game_caps: Mapping[int, int],
    width: int,
) -> dict[int, int]:
    """补差批的**满铺**配额（取代 `topup_games_by_stage` 的缺口解算）。

    语义（S5）：
    * eligible = 仍未达标（`collected < quota`）且未触 `game_cap` 的关；
    * eligible 为空 ⇒ 返回 `{}`（本轮不再补差）；
    * 否则**本批总局数恰好 = `width`**，按「大余数法」均分给 eligible：
        基数 = width // len(eligible)，余数 r = width % len(eligible)
        ⇒ 前 r 个关各多一局（顺序 = `stages` 的给定序，确定性，不吃随机）；
    * `width < len(eligible)` ⇒ 前 `width` 个 eligible 关各 1 局（退化档：机器比关数还窄）。

    ⚠ **不吃 est**：只问「机器能同时跑几局」，不问「一局能产多少样本」。
⚠ **`game_cap` 是硬顶（与 v3 本文的「不做 cap 截断」相反 —— 2026-10-11 落地的有意修正）**：
每关拿到的不超过它的 cap 余量，余下的局轮转给还有余量的关；批小于 `width` 的**唯一**情形 =
所有 eligible 关都到顶。为什么改：① 课程字段 `max_games_per_stage` 是**操作员的硬省**
——把它解释成「可以越一整个批」会让这个旋钮失效（§8 风险表里它正是「超采升高」的缓解手段）；
② 改造前就有一条活守卫钉着它（`tests/worker/test_iter_topup.py::test_topup_records_shortfall_when_cap_binds`
断言 `games_by_stage == {2000: 10}`）——**保留活判据比改写它划算**；③ 「不再有 1 局的批」
在真机上不受影响（eligible 只有 1–3 关、width ~94，只有「关关都到顶」才缩。
    ⚠ **不吃 `last_lo_factor`**：该字段保留仅为课程兼容（S8）。
    ⚠ **不做 cap 截断**：触 `game_cap` 的关已不在 eligible 里；剩下的关**铺满 width**。
       超采是**故意的**（用户口径：多余样本丢弃）。
    """
```

**调用点改写**（`iter_topup.py:208-247`）：`plan_g = spread_games_by_stage(..., width=width)`，
其余（`spec2` / 子目录 / spec 镜像 / `run`）不变。

**两处连带**：

1. **`width` 的来路**（v4 写死，因为 §3.2 未落地前就得能跑）：
   **首批返回的 `info["workers"]`**（`iter_rollout.py:1169-1180` 回参里就有）——它就是首批**实际用的池宽**
   （已夹过核数/`ENV_WORKERS_CAP`/局数），即"这台机器已证明跑得动的宽度"。
   回退链：`info["workers"]` → `spec["workers"]`（>0 时）→ `len(首批 argv)`；**三者都拿不到就不补差**（宁可不补，不猜）。
   §3.2 落地后同一把宽度由 `ctx.workers` 提供，本链成为回退档。补差批**按满分宽度建池/发局** ⇒
   **批大小 == 池宽 == 机器宽度**，三者同一轮内锁死。
2. **`wasted_samples` 会上升**（旧约 2056）。**这是预期的**，用户口径明说「多余样本丢弃」。
   它仍是**软指标**，只在轮报里可见、不参与任何门禁。**不许**为压它而缩批。

**兼容与「旧口径」的处置（v4 决定，评审 P1-2 更正）**：v3 写「`topup_games_by_stage` 保留不删
（旧的 `kind=run` 单 est 重放路径仍在用）」——**不实**：它的生产调用点**只有**
`iter_topup.py:209`（本节要换掉的这个），`kind=run` 的补波走的是**另一个**函数
（`worker/volume_waves.py:158 topup_games` / `:236 plan_topup`，消费方 `trainer/loop_volume.py:349`）。
⇒ **决定：保留不删，但在模块里显式标注「无生产调用点」并保留一条对账用例**——它是「缺口解算」语义的
**唯一可执行规格**，将来若 `volume_waves` 的口径并过来，同笔删（写进 DECISIONS，免得半年后没人敢动）。
`volume_waves` 的单 est 老口径本 plan **不动**。

### 3.4 P0-3 — 扫盘三连一轮一次，按批分组验声明集

**落点**：`iter_rollout.py:1120-1136` → 上移到 `topup_rollout` 之后

新增 `worker/iter_verify.py`（L1 叶子，**不造 `worker → remote` 边**）：

```python
def verify_round(batch_specs: list[tuple[Path, dict]]) -> list[Path]:
    """按批分组验证：每批的实产集 == 该批的声明集；返回**全轮** shard 目录（按批序）。"""
```

判据逐位不变：每批内部仍是 `data_fp(scan_shard_dirs(批根)) == iter_expected_data_fp(批 spec)`。
`collect_reports` / `collect_shard_manifests` 同理改成全轮一次。
**`combine_reports` 相加语义不变**（`iter_topup.py:136 merge_iter_reports` 的显式相加改为轮级一次算完）。

> ⚠ **轮级报告的装配契约必须写死（v4 补，评审 P2-3）**：扫盘搬出 `run_iter_rollout` 后，
> `report` 里那四个**由收尾步骤产生**的键（`shards` / `perGame` / `perGameSecs` / `elapsedSec`）
> 谁来写、写在哪一层要写清——它们有**仓库外读方**：`remote/artifacts.py:536` 读 `perGame`
> （控制台「耗时/击杀/残血/道具」四列的数据源）、`merge_iter_reports` 显式相加这四个键。
> 建议：轮级装配者 = `topup_rollout` 的调用方（`remote/worker.py`），产出与今天同键同型；
> 另：`clean_sec` / `搬回` 两个键已核**无 TS 读方**（`dashboard/src/**` 无命中）⇒ 删除安全，
> 但这条查证要写进实现笔（免得下次有人再查一遍）。

### 3.5 P0-4 — ★ 取消搬回（治根因 E；用户第 1 条）

**落点**：`iter_rollout.py:848-874`（落点解析）、`:934-963`（`_drain_one`）、
主循环调用点、`:1096-1107`（`finally` 轮末清理）、`:1155-1157`（`搬回` 读数行）

| 动作 | 具体 |
|---|---|
| **删** `_drain_one` 与主循环调用 | 结算判据从「搬回成功才算 settled（评审 F7）」简化为「跑局 future 正常返回即 settled」。⚠ 见下方「语义放宽」 |
| **删** `finally` 的 `clear_dir`/`drop_drain_stage` | 产出**就是要留在 scratch**，清了就没了。改在全 job 收尾（`remote/worker.py`）清 |
| **改** `s_layout.job` / `exec_dir` 的报出值 | `shard_dirs` 报 **scratch 真实路径** |
| **改** `data_fp` 扫描根 | `verify_shards` 的根 = scratch 批根，逐位判据不变 |
| **改** PPO 读取根（⚠ 评审 P0-1，漏了就训空） | `remote/train_core.py:526 shards_root = str(job_dir)` → `iter_info.get("shards_root") or str(job_dir)`；`worker.py` 侧写 `iter_info["shards_root"]`（`iter_info is None` 逐字不变） |
| **改** 轮末/收尾清理位置 | 今天的每批 `finally` 清 → 全 job 收尾清（`remote/worker.py`），否则本地盘占着不放 |
| **保留** `--out`/`--weights` 解析根、`InstallGate` | `exec_jd = picked.exec_dir` 不变；`InstallGate` 服务于 `install_tree`，与搬回无关 |

**⚠ 语义放宽（必须写清）**：今天 `settled` 要求「搬回成功」⇒ 写网络盘失败会重投。
取消搬回后这条失败模式**消失**（不再写网络盘），取而代之的是「scratch 写失败」。补偿：

* scratch 写失败 ⇒ 轮末 `verify_round` 的**逐位对账**必然暴露（实产集 ≠ 声明集）——
  **判据没丢，只是从「搬回成功」前移到「轮末对账」**。
* 轮末对账失败 ⇒ **响亮**（`ProtocolError`，hub 侧拒收）⇒ 不会静默放过。

### 3.5b 判据迁移表（⚠ **删活守卫必须先过这张表** —— 评审 P1-1）

现状：这三处判据当前**全绿**（本机实测 `bash tools/githook/nn-py-safe.sh -m pytest
 tests/remote/test_rollout_scratch.py tests/worker/test_iter_topup.py -q` → exit=0，38/38，
日志 `tmp/bf-review-guards.log`）——所以要删它们必须逐条交代判据去哪：

| 被删/翻转的断言 | 住址 | 判据挪去哪（必须新增断言） |
|---|---|---|
| `搬回=2 局/失败 0 局` + 「轮末清 `exec_dir`」+ `drain_stage_root` 回收（F1/F5） | `tests/remote/test_rollout_scratch.py:479-506` | 换成「产出在 scratch 且**不被清**」（反探针：轮末清 scratch ⇒ 红）+「`job_dir` 下不再出现 `w{i}/` 产出副本」 |
| 回退档「没有搬回这一说」（F9） | 同上 `:510-523` | **逐字保留**（回退档本就是这条路径的地板） |
| **F7「搬不回去的局不算结算」** | 同上 `:527-554` | 换成「**轮末 `verify_round` 缺一局 ⇒ 响亮 `ProtocolError`**」+「跑局 future 正常返回 ⇒ 结算」。⚠ 这是一次**判据前移**（发现变晚），必须在 DECISIONS 里写明代价 |
| `job_dir/w0/rollout.log` 存在 | `tests/remote/test_remote_iter.py:997` | 该用例必须**显式钉住档位**（回退档才逐字不变）；换根档另写一条断言 scratch 路径下有它 |
| `batches == 1` / `wasted_samples == 0` / `collected == quota` | `tests/worker/test_iter_topup.py:191-194` | 换成满铺口径：`batches` 按 `width` 算、`wasted_samples > 0` 允许、`collected ≥ quota`（§3.3 同笔改） |

**新增（不是替换）**：换根档下 **PPO 装载源 == scratch exec 根**（评审 P0-1 的专用判据，现无任何用例覆盖）。

### 3.6 P1-1 — 结果回传失败时才备份（用户第 1 条后半句）

**落点**：`remote/job_fs.py` + 回传点

1. **常态**：`_persist_result`（`job_fs.py:59`）**不变**（结果体写本地盘）。
2. **回传失败**：把**失败的那一份**复制到 `<job_dir>/backup/`（网络盘），文件名带
   `it`/`jid`/`sha256`。触发点 = 上传失败分支（**实现时定位**，§5 取证 4）。
3. **判据**：备份**尽力而为**（失败只记 WARN，不改 job 状态）；**不进任何门禁**。
4. **清理**：备份目录由 `prune_job_dirs`（`job_fs.py:72`）随 job 目录回收。

⚠ **非 P0**：它是用户第 1 条的余项，与墙钟无关。若不顺路**可以后置**——
但必须在 DoD 里可见（不能悄悄丢）。

### 3.7 P1-2 — 补差批的池宽与局数解耦

`iter_rollout.py:802-809` 的 `workers = min(requested, len(argvs), …)` 对**首批**是对的，
对**补差批**是错的（15 局的批只开 15 个 worker，却要重建池）。
改法：`ctx is not None` ⇒ `workers = ctx.workers`（轮级）。满铺器落地后自然满足，
但代码上仍要显式传 `ctx.workers`（防「宽度 < 局数」的边界）。

### 3.8 P2-1 — 探针结果轮内缓存

**落点**：`common/scratch.py`

`open_scratch` 增加可选 `cache: dict | None`；`resolve_scratch_root` 命中缓存直接返回（含 `None`）。
缓存键 = `(SCRATCH_ENV, 轮级 job_root, need 档位)`；只在**同一 job 的一轮内**有效（进程内 dict）。

> 注：§3.2（ctx 复用）已使补差批**不再调** `open_scratch`，所以本项在 P0 落地后是
> **冗余保险**（防「ctx 传漏了」时退回每批探针）。保留为 P2。

### 3.9 P2-2 — 诊断读数（让下一次坏轮一眼可查）

新增轮报字段（**只加字段，不改既有键**）：

* `批内耗时`：逐批 `elapsedSec`（今天只有首批进 `report`）。
* `固定开销`：轮级 `probe_sec` / `install_sec` / `mirror_sec` / `pool_coldstart_sec` /
  `scan_sec` 五分项（**删原 `clean_sec`**——搬回取消后轮末不再清 scratch）。
  **这是判据来源**：`probe_sec` 大 ⇒ 根因 G 实测坐实；`pool_coldstart_sec` 大 ⇒ 根因 B。
* **`probe_probe`（新增，根因 G 专用）**：`probe_write_speed_mbps` 的**实测 MB/s**与
  **耗时**，以及「超界回退」次数。这是 §1.11 结论的**真机确证**。
* `批形状`：逐批 `(批号, 局数, width, 逐关分配)` —— 满铺器的直接读数。
* **删** `搬回` 读数行（`iter_rollout.py:1157`）。

### 3.10 P2-3 — ★ LAN 集群队列同理念（用户第 3 条）

**落点**：`trainer/dispatch_queue.py`（`run_rollout_queue:55`；槽位 `local_slots_max_of:27`）

用户原话：「**LAN 集群做 rollout 时，也要同样理念在队列里处理，不要为了三五局差额补跑好几轮**」。

1. **派单单位 = 槽位宽度**（`local_slots_max_of(args)`），不是「缺口局数」。
   丢进队列的批次**先铺满槽位**，剩余差额由**下一整轮**吸收。
2. **不重排队**：今天「三五局差额 ⇒ 再排一轮 ⇒ 再起池」的路径要**掐掉**；
   差额**留到下一轮**与正常批次一起铺（多余样本丢弃 —— 与 §3.3 同一口径）。
3. **池的生命周期 = 队列轮**，不是「每次派单」。
4. **不新增队列状态机**：只换派单**计算**口径，既有序号/租约/回执语义**一个字不动**。

⚠ **取证依赖**：需先读 `dispatch_queue.py` 全文 + `hub/queue*.py` 的派单面（§5 取证 5）。

---

## 4. 验收（DoD）

### 4.1 单测（P0 必绿）

| 用例 | 判据 | 住址 |
|---|---|---|
| `ctx=None` 逐字不变 | `test_remote_iter.py` **一个字不改**全绿 | `tests/remote/test_remote_iter.py` |
| **探针超界 ⇒ 回退档** | 注入「探针永不返回」的替身 + 把 `PROBE_CEILING_SEC` 压到亚秒 ⇒ 到上界即 `picked=None`、记 WARN + `probe_probe` 读数、**整轮不挂住** | `tests/remote/test_rollout_scratch.py` 增例（**已落地**） |
| **探针超界不改判据** | 超界回退后的行为 == 回退档逐字（不换根/不搬） | 同上 |
| 批间复用 | 补差批**不**再调 `open_scratch`/`install_tree`/`mirror_inputs`（替身计数 == 1） | 新增 `tests/worker/test_topup_round_ctx.py` |
| 池跨批 | 补差批 `pool.spawned` 增量 == 0；`served` 累计 | 同上 |
| **满铺：批大小 == width** | 任一非空批 `sum(plan_g.values()) == width` | 新增 `tests/worker/test_spread_games.py` |
| **满铺：无 1 局批** | 有关未达标 ⇒ 批 ≥ `min(width, 关数)`；**永不出现**「剩余缺口 3 局 ⇒ 批 3 局」 | 同上 |
| **满铺：均分确定性** | 大余数法前 `r = width % n` 个关各多一局；同输入同输出 | 同上 |
| **满铺：cap / 达标关不进 eligible** | 触 `game_cap` 或缺额 ≤ 0 的关不参与；eligible 空 ⇒ `{}` | 同上 |
| **满铺：超采不截断** | width 大于缺口所需 ⇒ **仍铺满** | 同上 |
| **不搬回：scratch 就是产出根** | `ctx.shard_dirs` ⊂ `picked.exec_dir`；`job_dir` 下**不再出现** `w{i}/` 产出副本 | 新增 `tests/worker/test_no_drain.py` |
| **不搬回：轮末不清 scratch** | `clear_dir`/`drop_drain_stage` 在本轮**零调用** | 同上 |
| **不搬回：判据没丢** | 构造 scratch 缺一局 ⇒ `verify_round` 转**响亮** `ProtocolError` | 同上 |
| **PPO 装载源 == 产出根**（评审 P0-1 专用，待 §3.5 同笔落地） | 换根档：`train_core` 拿到的 `shards_root` == scratch 的 exec 根；回退档 == `job_dir`；`iter_info is None` 逐字不变 | 新增 `tests/remote/test_train_core_shards_root.py`（新） |
| **批数上界仍硬** | 满铺不破 `max_batches`（`min(width, 缺口)` 时仍会停） | `tests/worker/test_spread_games.py`（新） |
| 扫盘分组 | 两批各自 `data_fp` 不等时**分别**报错（点名哪一批） | `tests/worker/test_iter_verify.py`（新） |
| 扫盘等价 | 新形态与「逐批 `verify_shards`」在同一 fixture 上结论逐字相同 | 同上 |
| 探针缓存 | 缓存命中 ⇒ 探针调用次数 == 1；命中 `None` ⇒ 仍回退档 | `tests/remote/test_rollout_scratch.py` 增例 |
| 回退档逐字 | `s_layout is None` 时批间复用路径 == 今天的行为 | 新增用例 |
| 回传失败备份 | 备份**尽力而为**：失败 ⇒ 只记 WARN，**不改 job 状态、不进门禁** | §3.6 新增用例 |
| LAN 队列铺满 | 差额不再触发重排队 ⇒ 派单次数 == 轮数 | §3.10，取证后落 |

**五处反探针必须逐条转红**：
① 补差批绕过 ctx 重建 scratch · ② 池在批末被 close · ③ 扫盘按单批声明集验全轮 ·
④ 补差批退回「缺口解算」（批大小 ≠ width）· ⑤ 产出被搬回 `job_dir`（drain 复活）。

> **评审 P1-1 加注**：v3 的 DoD 只列了「新增用例」+「`test_remote_iter.py` 一个字不改」，
> 漏了**被删/被翻转的既有判据**（它们现在全绿）。清单已落在 **§3.5b 判据迁移表**，
> 必须与 §3.3/§3.5 同笔完成（删活守卫不交代去向 = 净放松）。

### 4.2 真机验收（`x24-stack5` 续跑或同课程新腿，**agent 不起训练**）

> ⚠ **可复现前提（评审 P1-3）**：`nn-training/curricula/` 里**没有** `x24-stack5.jsonc`
> （只有 `x24-stack{,2,3,4}.jsonc`；`x24-stack5` 只出现在 `DECISIONS.md:4453` / `docs/nn/engineering.md:10926` 的叙述里）。
> ⇒ 本条要么**先入库课程**，要么在验收记录里写清「操作员本地课程 + course sha256」；
> 否则这张 DoD 跟 v3 的账本一样，评审侧不可复现。

* **★ 第一条判据（根因 G）**：新增的 `probe_probe` 读数里，`probe_sec` 在**坏轮**应显著 > 好轮
  （预期 1–2 个数量级）；且「超界回退」次数在坏轮 > 0。
  **若这条不成立，§1.11 的归因作废，本 plan 的主修必须重排**。
* `rollout_sec` p90 **< 15s**（今天 88.9s）；**`max < 30s`**（今天 111.2s）。
* **补差批墙钟 < 8s**（今天 48.9s / 52.5s）；**批数 ≤ `max_batches` 且每批局数 == width**。
* `serve_pool: spawned` **只在首批非零**，补差批增量 == 0。
* `固定开销` 五分项之和 ≈ `rollout_sec − 跑局理论下界`（偏差 > 20% ⇒ 有未记账位点，不许放过）。
* **`job_dir` 下不再有 shard 产出副本**（`w{i}/` 不存在）；单局 `rollout.log` 在 scratch 路径下。
* `/kaggle/working` 的写入量下降（搬回取消后的直接外形）。
* `verify_round` 的拒收路径**仍能打出来**（构造一批缺口 ⇒ 看是否点名该批）。

### 4.3 门禁

`bun run pygate` 绿 · `bun run check` 绿 · `tests/remote/test_job_fs_split.py`（DAG 守卫）绿。

---

## 5. 待取证（**已按「TPU 排不到」重排**）

> **背景**：TPU 机排队十几小时 ⇒ **「跑一轮真机看读数」这条路不可用**。
> 因此原 P0 里「先取真机读数再动手」的三条，**改为可离线取的两档**：
> **A 档（已用现有账本 + 代码闭合，本轮已做）** 与 **B 档（用现有测试基建离线复现）**。
> 只有 C 档才真的需要真机，而它们**不再是动手的前置条件**。

### A 档 —— 已用现有材料闭合（本轮完成）

| # | 结论 | 证据 |
|---|---|---|
| A1 | 坏轮增量**散布**（+36…+103s），不是常数 ⇒ 否掉「某步永久变慢」 | 54 轮账本：好轮残差 p50 = 0.91s / 坏轮 0.82s（`wall−rollout−ppo`） |
| A2 | **同一轮内相邻两批**天差地别 ⇒ 否掉「这一轮的盘慢」 | it34：3.35s(18局) → 48.90s(1局)；it49：3.00s → 52.49s |
| A3 | **跨课程对照**：同机制、x21 系列 500 轮坏轮 <5%，x24-stack5 = 42.6% ⇒ 触发源在**环境** | 523 轮账本（§1.11 证据 3） |
| A4 | **探针段裸调、无超时，且每批一次** ⇒ 唯一能同时解释 A1/A2/A3 的位点 | `iter_rollout.py:852` + `scratch.py:158/224/237` |

**A 档已足以支撑 §3.1 落地**（它的落点、语义、DoD 都不依赖真机）。

> ⚠ **v4 前置（评审 P1-3）**：A 档的账本与表**不在仓库里**（`tmp/` 不入库）⇒
> 这一档要从「我记得」变成「你可以重算」：把提取配方落成**仓内可跑的脚本**
> （建议 `nn-training/tools/ledger-stats.py --glob 'tmp/*/remote-jobs/offline/*/it-*/row.json'`，
> 输出 `n / p50 / p90 / max / 好轮坏轮残差 / 跨课程表`），并把 53-vs-54、523-vs-554-vs-500 对齐。
> **未补之前**：§1.11 的归因在评审侧是**假说**（判据本身不受影响，见 §1.11 的 v4 加注）。

### B 档 —— 用现有测试基建**离线复现**（动手前做，不需要 TPU）

| # | 目标 | 做法 |
|---|---|---|
| B1 | 证明「探针慢 ⇒ 整批慢」是**真实的**因果，不只是算术 | 用 `scratch.py:203` 的 `probe=` 注入点造「每次调用睡 N 秒」的假探针 ⇒ 断言整批墙钟随之上升，且**包上界后**不再上升。**本轮已验证可行**：注入 1.5s × 3 次调用 ⇒ `open_scratch` 整段 4.65s |
| B2 | 标定 `SCAN_CEILING_SEC` 对探针是否合适（60s 可能过长） | 同上 fixture，量「正常盘探针耗时」分布（本机可测：job 目录换成本地目录/网络盘各一遍） |
| B3 | 满铺器的 `width` 行为 | 纯函数，直接单测（无需真机） |

### C 档 —— 仍需真机（**不再是动手的前置条件**）

| # | 目标 | 备注 |
|---|---|---|
| C1 | `probe_probe` 读数在坏轮/好轮的实测差（§4.2 第一条判据） | 落地 §3.1 + §3.9 后**自然产出**，不必特意跑一轮 |
| C2 | 回传失败路径的**唯一**判定点（§3.6） | 取不到 ⇒ 后置本项，DoD 标注「未落地」 |
| C3 | LAN 队列派单面（§3.10） | 取不到 ⇒ §3.10 **降级为「待立项」**，不硬塞 |

> **为什么要这么分**：原 v1 把三件事都挂「等真机」⇒ 一旦排不到队，**整个 plan 冻结**。
> 现在 **A 档已闭合、B 档离线可做**，§3.1–§3.5 全部可以动手；
> C 档的读数在落地后**自动产生**，不需要专程占用 TPU 队列。

---

## 6. 落地顺序与工作量

| 序 | 项 | 依赖 | 风险 |
|---|---|---|---|
| 0 | §3.9 读数（五分项 + `probe_probe` + 批形状） | 无 | 无（只加字段） |
| 1 | **§3.1 探针包上界** | 0 | **低**（一行调用点 + import；超界走已验证的回退档） |
| 2 | §5 B1/B2 离线复现 | 1 | 低（复用既有 `probe=` 注入点） |
| 3 | §3.2 `RoundContext` | 1 | 中（改 `run_iter_rollout` 签名，`ctx=None` 后向兼容） |
| 4 | **§3.5 取消搬回** | 3 | **中高**（删代码 + `settled` 判据前移到轮末对账 + 扫描根换盘） |
| 5 | §3.3 满铺器 | 3 | 中（纯函数好写；**超采升高**要写进 DoD） |
| 6 | §3.4 扫盘分组 | 3 | **高**（唯一处语义扩张，判据必须逐位不变） |
| 7 | §3.7 池宽解耦 | 3 | 低（满铺器落地后自然满足） |
| 8 | §3.8 探针缓存 | 3 | 低（ctx 落地后是冗余保险） |
| 9 | §3.6 回传失败备份（需 C2） | 8 | 低（余项，可后置） |
| 10 | §3.10 LAN 队列（需 C3） | 8 | 中（取证不足则降级） |
| 11 | §4 验收 | 全部 | 低 |

**P0 = 0/1/2/3/4/5**（1 是单点主因，独立可动；3/4/5 是主修）；**P1 = 6/7/9**；**P2 = 8/10**。

> **顺序要点**：**§3.1 单独排第一位**——它只动一行，却能独立验证 §1.11 的归因；
> 且 §3.5（取消搬回）排在 §3.3（满铺器）**之前**，因为取消搬回独立于满铺器，
> 且**好轮也受益**（好轮里也有 116 局串行搬回）。

---

## 7. 与既有 plan 的关系

| plan | 关系 |
|---|---|
| `rollout-stage-balance`（2026-10-07） | **上位**。本 plan 是它的性能修 + **批粒度口径修**；`volume` 语义与单测保留（`topup_games_by_stage` 的单测仍绿） |
| `rollout-local-scratch`（2026-10-08） | **互补 → 被本 plan 推到底 + 补一处漏**。它把 IO 换到本地盘（治性质），本 plan 直接**不再搬离**（治必要性）；并**补上它漏掉的探针上界**（§3.1） |
| `rollout-concurrency-adaptive`（2026-10-08） | **正交**。它治并发度（IO 的量）；本 plan 治次数、批粒度与探针上界。**两者都在坏轮有效**，可并行评审 |
| `rollout-serve-pool-readiness`（2026-10-07） | **上游**。池的就绪判据/冷启动/熔断语义**一个字不动**，本 plan 只改池的**生命周期** |
| `opt-blob-diet` | **无关但相邻**。它治上行 blob 体积；本 plan 治 rollout 墙钟。都动 `remote/` 但不同文件面 |

---

## 8. 风险与回退

| 风险 | 缓解 | 回退 |
|---|---|---|
| **探针包上界后误走回退档**（本来能换本地盘，却因超界回落网络盘） | 只在上界**真超**时才回退；WARN 行响亮；`probe_probe` 读数可见 | 调大上界（env），或 §3.8 缓存命中后不再探 |
| **`call_bounded` 不取消被放弃的线程** ⇒ 探针线程残留 | 既定语义（daemon，不拖住收尾）；探针文件带 pid 不撞名 | — |
| 池跨批后一处泄漏 ⇒ bun 常驻 | `remote/worker.py` 用 `try/finally` 包；轮报 `spawned/killed` 累计可见 | 保留 `NN_SERVE_POOL=0`（`serve_pool.py:96`）一键回退逐局 spawn |
| **满铺器超采升高** | 软指标、不进任何门禁；`max_batches` 仍是硬上界 | 调 `max_games_per_stage`（课程字段，不动代码） |
| **`settled` 判据前移到轮末对账** ⇒ 失败发现变晚 | 轮末 `verify_round` 逐位对账 + 响亮 `ProtocolError`；单测「缺一局 ⇒ 转红」 | 恢复 `_drain_one`（保留签名，`NN_DRAIN=1` 门控） |
| **产出只在本地盘** ⇒ 机器挂了产出全丢 | 用户口径明确接受；回传失败时备份（§3.6） | 同上 `NN_DRAIN=1` |
| 扫盘分组改错 ⇒ 静默放过缺口 | §3.4 等价性用例 + 反探针③ | 新增 env `NN_ROUND_SCAN=0` 走逐批扫盘（仅回退取证） |
| `RoundContext` 让回退档行为漂移 | `ctx=None` 路径**逐字复制**今天的调用序列；反探针② | 直接不传 `ctx` |
| 批间复用让 `elapsedSec` 含义变化 | §3.9 的「批内 vs 全轮」双层口径；**旧键保持旧含义** | — |
| **§1.11 的归因被真机推翻** | §4.2 把它列为**第一条判据**（若 `probe_sec` 好坏轮无差 ⇒ 归因作废） | §3.2–§3.5 仍全部独立成立（它们不依赖 G） |
| LAN 队列改动波及 hub 派单面 | §3.10 只换派单**计算**口径；取证不足则**不落地** | 不合并该节 |
| **探针上界选错**（太小 ⇒ 好盘也回退；太大 ⇒ DoD 达不到） | ① 上界与 `SCAN_CEILING_SEC` **分开**（评审 P0-2）② `poll_probe` 读数留证据 ③ §5-B2 标定 | 调 env / 常量；回退档行为逐字不变 |
| **取消搬回后 PPO 读空语料**（评审 P0-1） | `shards_root` 由产出方显式给出 + 专用用例（§4.1） | 保留 `NN_DRAIN=1` 回退档 |
| **scratch 全轮累积打爆本地盘**（评审 P0-3） | 容量判据按全轮需求算（§2.2-S3） | `NN_ROLLOUT_SCRATCH=off`（回退原地） |
| 归因（根因 G）被真机推翻 | §4.2 第一条判据保留；§3.2–§3.5 不依赖它 | — |

---

## 9. 一句话交付判据

> 在 `x24-stack5` 同课程上，`rollout_sec` 的 p90 从 88.9s 降到 **< 15s**、
> 补差批墙钟从 ~50s 降到 **< 8s**，且**每一批的局数 == 机器 worker 宽度**（再没有 1 局的批）、
> **产出不再搬回网络盘**、**scratch 探针有上界**；
> 而 `data_fp` 判据、`volume` 语义、池的熔断语义、回退档行为**全部逐字不变**。

**阈值的前提（v4 写清）**：`p90 < 15s / max < 30s / 补差批 < 8s` 这三个数字只有在
**探针上界按单秒级（`PROBE_CEILING_SEC`）+ §3.5 落地**之后才是可达的；
只落 §3.1 而不拆常量（即 v3 的 60s）时，它的正确表述是「**bad 轮的上界从无界变 ≤ 60s**」。

---

## 10. 落地进度与记录（v4）

### 10.1 进度

| 节 | 状态 | 证据 |
|---|---|---|
| §3.1 探针包上界（含新建 `PROBE_CEILING_SEC` + `probe_probe` 读数） | ✅ **本轮已落** | `tests/remote/test_rollout_scratch.py` 新增「超界回退」用例（红→绿）；见 §4.1 |
| §3.3 满铺器 `spread_games_by_stage`（含 P1-2 的旧口径归置） | ✅ **本轮已落** | `tests/worker/test_spread_games.py`（新）+ `test_iter_topup.py` 按满铺口径改 |
| §3.2 `RoundContext`（批间复用） | ⬜ 下一刀 | 它是 §3.5 的前置（换根档产出分散在各批私有目录，必须先有轮级 ctx） |
| §3.5 取消搬回 | ⬜ 下一刀（**P0-1 连通项已写进 §2.2-S6 / §3.5 动作表**） | 落地时同笔改 `remote/train_core.py:526` |
| §3.4 轮末扫盘分组 | ⬜ 下一刀（依赖 §3.2 的 `batch_specs` 累积） | 轮级报告装配契约见 §3.4 末注 |
| §3.6 / §3.7 / §3.8 / §3.10 | ⬜ 待取证 / 依赖 §3.2 | §3.8 在 §3.2 后是冗余保险；§3.10 取证不足则降级待立项 |

### 10.2 记录（AGENTS §4 / §15.5 要求，v3 整篇缺失 ⇒ v4 补齐）

| 记哪 | 写什么 |
|---|---|
| `DECISIONS.md` | ①「探针上界另立 `PROBE_CEILING_SEC`」②「补差批铺满、超采丢弃」③（日后 §3.5 落地时）「取消搬回 + PPO 读取根改写 + F7 判据前移」 |
| `docs/nn/runtime-opt.md` | 新 §N：探针上界与 `probe_probe` 读数；§34.x（scratch 落点链）的交叉引用 |
| `.workbuddy/memory/YYYY-MM-DD.md` | 一行（做了什么 / 坑 / 下一步） |
| 训练侧纪律 | 满铺器改的是**每轮样本量/更新构成**（不是语料身份/课程/奖励）⇒ 属训练侧；按 §15.5 口径当**新实验**处理：新 `--out`/`--traj`，**不跨改动续跑** |
