# 逐轮采样子集 + 关间轮换（v2 · 评审修订版）

> 状态：**执行中**（2026-10-27 立项；同日晚按评审 A1–A4 / B1–B4 / C1 修订）
> 前置：`f35adb2a`（payload 零落盘）· `6432648a`（逐局流式采集，`worker/stream_collect.py`）
> 关联：AGENTS §15（闭环语料纪律）· `plan/dynamic-rollout-volume.plan.md`（volume 模式）
> 落地代价：要重启 hub / trainer（`trainer/**`、`biz/**`、`worker/**` 都在 `code.zip` 内）

## 0. 一句话

现在是「每轮 35 关全采、每关 `ceil(target/35)` 步」。改成**每轮只采一个窗口**（由 `(rotateSeed, it)`
决定），让每关样本密度提高 `35/N` 倍，同时保证 `ceil(35/N)` 轮内覆盖全部关。

## 0.1 修订摘要（v1 → v2）

| 评审条目 | v1 的写法 | v2 的结论 |
|---|---|---|
| **A1 接线点错** | 「改 `worker/plan.py:96` 的 `stages=[int(s) for s in vol["stages"]]`」 | 那行是**老五字段回退**分支，当前分关形态根本不经过它（关集来自 `games_per_stage_by_stage` 的**键**）。唯一的缝是 **`worker/volume_waves.volume_block()`**（由 `trainer/loop_export.py:162` 调用）⇒ 加 `stages_override` |
| **A2 已有硬闸** | 「其余全是既有机器的自然结果」 | `trainer/loop_volume._volume_stages()`（:153-186）今天**主动 SystemExit** 拒收 `--rotate-stages`/`--curriculum-*` + volume ⇒ 必须**有意识退役**它（且区分「新窗口」与「旧门控」） |
| **A3 配额分母三处** | 只提 `games_per_stage_by_stage` 派生量 | 还有第三个消费者：`trainer/loop_steps._per_stage_quota()`（按 `--stages` **全集**算 `target/35`）→ `_trim_shards_to_quota`（打包前裁）+ `per_stage_quota=`（PPO 装载截断）。不同源 ⇒ 窗口采到的 2/3 被裁掉 |
| **A4 多轮段** | 未提 | `plan["volume"]` 是**单份**块（块内钉 `it`/`rotate_seed`），而窗口是 `it` 的函数 ⇒ 多轮段（`--export-bundle`、离线 notebook 段）会「一份块配多个窗口」。v1 **响亮拒收** |
| **B1 模式 3 面太大** | 「三处一起改」 | 实际 6–7 处且**跨进程**（PPO 引擎 argv `--per-stage-quota` / `train_core` / `plan_run` / `stream_collect.quota` 标量 / iter_rollout 容量预算 / 块键集与等式）⇒ **本版不做**（§7） |
| **B2 身份纪律** | 「进 `corpus_fp`/`course_fp`」 | `course_fp` 是课程**文件字节** sha（自动变）；要动的是 `biz/corpus_fp._identity_payload` 的**显式键集**，且必须沿用「**仅在激活时**进 payload」 |
| **B3 配置形状** | 嵌套对象 `"stage_rotation": {...}` | `flat_overrides` 只产出扁平 dest→值；本版用**扁平标量键**（`rotation_mode` / `rotation_per_it` / `rotation_pinned`），不引入新嵌套模型 |
| **B4 est** | 未提 | `loop_export` 的 `trailing_stage_samples_per_game` 必须**按窗口**取；否则 `volume_block` 的 `ests_by_stage.get(s,0) or est` 静默回退全局 est（2026-10-21 修过的同一个病） |
| **C1 carry_over** | 模式 2 的一部分 | **删除**：同一 `(stage, seed)` 两轮各训一次 = §15.1「重磨固定集」；且要跨零落盘/裁剪/保留边界另存字节。单独立项 |
| **A5 发布期自检**（施工中新发现） | 未提 | `check_plan_against_args` 按 `args.stages` **全集**重解对集 ⇒ 窗口计划在发布期就被「重放与 args 不一致」拒掉。修法：`volume_pairs_from_args(stages_override=块里的关集)` + 子集不自洽转 `ProtocolError` |

## 1. 现状（已逐条核对的 file:line）

| 位置 | 事实 |
|---|---|
| `biz/course.py:107-135` | `--rotate-stages K` 的窗口实现（`per_epoch=ceil(n/K)`、`pos=(it-1)%per_epoch`、`epoch_idx`、`default_rng([rotate_seed,0xA11CE,epoch_idx]).permutation(n)`）。**resume-safe**，本方案照抄这套构造 |
| `trainer/loop_volume.py:153-186` | `_volume_stages()`：**今天拒收** `curriculum_stages` / `rotate_stages` + volume；注释写明「配额按固定关集反解」。本版把它换成「窗口也可」 |
| `trainer/loop_volume.py:536` | `quota = target_per_stage(target, len(stages))` —— 一旦 `stages` 是窗口，本地链的配额/`stage_caps`/`unmet`/`stage_stats` **自动**跟着窗口走（`unmet` 只遍历窗口关 ⇒ 不会恒亮假警） |
| `worker/volume_waves.py:430-525` | `volume_block()`：`stages = parse_stages_arg(args.stages)`；`per_stage_quota = target_per_stage(target, len(stages))`；分关字段 `games_per_stage_by_stage = alloc_games_by_stage(stages, target, ests_hi)`。⇒ 只要传入**窗口**作 stages，三者自动自洽 |
| `trainer/loop_export.py:150-170` | 导出侧：`stages = parse_stages_arg(args.stages)` → `trailing_stage_samples_per_game(stages)` → `volume_block(...)`。窗口必须在**这三步之前**算好并一路传下去 |
| `trainer/loop_steps.py:471` | `_per_stage_quota()` 用 `len(parse_stages_arg(args.stages))`（全集）⇒ 必须带 `it`、按窗口算，或直接读该轮块的 `per_stage_quota`（单一真源） |
| `trainer/loop_remote_job.py:311 / :441` | 两个消费者都在**逐轮**方法里（`it` 在作用域内）：前者 `_trim_shards_to_quota`（打包前裁），后者 `per_stage_quota=`（manifest → PPO 截断） |
| `worker/stream_collect.py:130-132` | 云机侧 `self.stages = vol["stages"]` + `self.quota = target_per_stage(target, len(self.stages))` ⇒ **窗口一换，每关配额自动 ×35/N**；`next_game` 按 `quota − collected` 派，`verify_shards`/声明集一个字不用动 |
| `remote/plan_run.py:366-373` | 已有范式：manifest 的 `per_stage_quota` 与计划不一致 ⇒ WARN + **以计划为准覆盖**（这正是 A3 的修法） |
| `worker/plan.py:84-101` | `pairs_for` 分关路径 = `initial_wave_pairs_by_stage(games_by_stage=...)`（关集 = dict 键）；`stages=[...]` 只在下方的老五字段回退里 |
| `biz/corpus_fp.py:_identity_payload` | **显式键集**；`target_transitions` / `paired_rotate_seed` 都写着「**仅在激活时**进 payload，否则既有课程指纹全体漂移」 |
| `worker/eval_track.py:42` · `trainer/batch_plan.py:44` | `EVAL_SEEDS_BASE = 2_000_000_001`（池外池）· `EVAL_SEED0 = 860001`（B/C 段基址） |

## 2. 配置面（扁平标量键，经 `flat_overrides` 映射到 argparse dest）

```jsonc
// 模式 1：平均轮换（每轮 N 关，ceil(35/N) 轮覆盖一遍）
"rotation_mode": "even",
"rotation_per_it": 12

// 模式 2：指定轮换（pinned 每轮都采 + 其余关用余额轮换）
"rotation_mode": "pinned",
"rotation_per_it": 10,
"rotation_pinned": "2033-2034"
```

- 缺席/`""` = **关闭**（老课程逐字节不变：一个函数都不调用）。
- 三个键都要进 `CourseConfig` 字段 + `flat_overrides` 映射表（`biz/course_spec.py:1058`）。
  **漏映射 = 静默失效**（`ent_break` / `paired_rotate_seed` 前科）。
- `rotation_pinned` 用**关号规格串**（与 `--stages`/`--seeds` 同一个 `parse_stages_arg`），不引入 list 型 dest。
- `rotation_seed` **不新增**：窗口键用既有的 `rotate_seed`（= `paired_rotate_seed` 解析后的值）——
  窗口与种子流必须同键，否则「窗口里的关」和「抽到的 seed」是两个纪元。

## 3. 改动清单

### 3.1 新增 `worker/stage_rotation.py`（纯函数，L0：只 import numpy / `volume_quota.target_per_stage`）

```python
def window_for(
    *, mode: str, it: int, all_stages: Sequence[int], rotate_seed: int,
    per_it: int = 0, pinned: Sequence[int] = (),
) -> list[int]:
    """第 `it` 轮该采哪些关（确定性：只依赖 (rotate_seed, it)；返回值**排序**）。"""

def window_from_args(args, it: int, all_stages: Sequence[int]) -> list[int]:
    """从 args（`rotation_mode` / `rotation_per_it` / `rotation_pinned` / `rotate_seed`）取窗口。

    关闭 ⇒ 原样返回 `all_stages`（调用方无需分支）。
    """
```

规则：

- `even`：`k = min(max(per_it,1), len(all_stages))`；`per_epoch = ceil(n/k)`；`pos=(it-1)%per_epoch`；
  `epoch_idx=(it-1)//per_epoch`；`perm = default_rng([rotate_seed, 0xA11CE, epoch_idx]).permutation(n)`
  ⇒ `positions[pos*k:(pos+1)*k]` 映射到**排序后**的 `all_stages`（用位置而不是关号：老实现的
  `permutation(total_stages)` 隐含「关号 == 0..n-1」，本版不背这个假设）。
- `pinned`：`pinned∩all_stages` 恒在窗口；余额 `per_it` 关从 `all_stages − pinned` 里按 `even` 同一套
  轮换取（最后一个 epoch 可能不足 `per_it`）。
- 校验（失败抛 `ValueError`，调用方转 SystemExit/ProtocolError）：未知 mode · `pinned` 里有不在
  `all_stages` 的关号 · `all_stages` 空。

### 3.2 `worker/volume_waves.volume_block(..., stages_override=None)`

- 传了 `stages_override` ⇒ `stages = sorted({int(s) for s in override})`，并校验它是 `args.stages` 的**子集**
  （打错一个字就响亮报错，不静默当真）。
- 其余一个字节都不改：`per_stage_quota` / `games_per_stage_by_stage` / `est_s_by_stage` 本来就都按
  `len(stages)` 派生 ⇒ 自动变成窗口口径。
- **第五个消费者（评审 A5，施工中发现）**：`worker/plan.check_plan_against_args` 的发布期自检。
  它原先用 `volume_pairs_from_args(args, …)` 按 `args.stages` 全集重解，再与 `pairs_for`（窗口）比
  ⇒ 每份窗口计划在**发布期**被拒，文案还指向错误方向。修法：`volume_pairs_from_args` 加
  `stages_override=`，自检传块里的关集（块是权威），子集不自洽转 `ProtocolError`。

### 3.3 `trainer/loop_export`：窗口在**三步之前**算好

```python
window = stage_rotation.window_from_args(args, int(it), stages)      # stages = 全集（已解析）
per_stage = trailing_stage_samples_per_game(self._traj_root, window, ..., exclude_it=it)
return volume_block(args, est_samples_per_game=est, ests_by_stage=per_stage or None,
                    topup={...}, stages_override=window)
```

### 3.4 `trainer/loop_volume`：退役 v1 拒收 + 窗口化

- `_volume_stages(self, it)`：带 `it`；`rotation_mode` 有效 ⇒ 返回窗口；
  **旧门控**（`curriculum_stages` 或 `rotate_stages>0`）仍**响亮拒收**（那两套与窗口语义不同，混用无解）；
  `--stages` 缺席/不可解析的既有报错不变。
- `_volume_stage_ests_map(it)` 里「按 `args.stages` 全集重解析」那一步**必须去掉**（否则窗口被抹回全集）。

### 3.5 `trainer/loop_steps._per_stage_quota(self, it)`：与块**同源**

- 带 `it`；`rotation_mode` 有效 ⇒ `target_per_stage(target, len(window))`；否则逐字旧行为。
- 两个调用点（`loop_remote_job.py:311` / `:441`）改成 `self._per_stage_quota(it)`（都在逐轮方法里）。
- **加一条守卫用例**：`_per_stage_quota(it) == volume_block(...)["per_stage_quota"]`（同 target/est/窗口）——
  这条能钉死 A3 那类「两处各自反解」的复发。

### 3.6 身份（仅激活时）

`biz/corpus_fp._identity_payload`：`course.rotation_mode` 非空 ⇒

```python
payload["stage_rotation"] = {
    "mode": course.rotation_mode,
    "per_it": int(course.rotation_per_it),
    "pinned": sorted(parse_stages_arg(course.rotation_pinned)) if course.rotation_pinned else [],
}
```

缺席不进（老课程指纹零漂移）。`course_fp`（文件字节）自动变，无需改。

### 3.7 拒绝面（A4）—— ~~已退役~~（2026-10-28，见 §9.6）

> 本节保留为**当时**的拒收方案；多轮包现在允许（配方进计划 + 逐轮派生）。

`trainer/loop_export._export_offline_bundle`：`rotation_mode` 有效且 `n > 1` ⇒ **SystemExit**
（「轮换的窗口是 `it` 的函数，一份计划只能钉一轮；多轮请逐轮下发或先按轮拆包」）。
理由：`plan["volume"]` 单份块 + `pairs_fp` 逐轮重放 ⇒ 一份块配多个窗口必然错，而错法是**静默的**。

## 4. 硬约束（违反就是 bug）

1. **确定性**：窗口只由 `(rotate_seed, it)` 决定；禁 `Math.random()` / 墙钟 / 进程状态。resume 后同窗口。
2. **同源等式**：`volume_block` 的 `per_stage_quota == target_per_stage(target, len(窗口))`、
   `games_per_stage_by_stage == alloc_games_by_stage(窗口, target, est_hi)`（`validate_volume_block_ext` 的既有校验）；
   `_per_stage_quota(it)` 与块里的值必须**逐字节相同**。
3. **评估不重复的判据是 seed 不是关**（AGENTS §15.1；2026-10-27 用户澄清）：35 关全 eval 是**验收仪器**
   （没被采到的关有没有退化，只有它看得见）。**不许**把评估关集从窗口池里剔掉，也**不许**把评估 seed
   喂进训练抽签（A 层 `EVAL_SEEDS_BASE` 池外池、B/C 层 `EVAL_SEED0` 段，均不得触碰）。
4. **身份**：`stage_rotation` 只在激活时进 `corpus_identity_fp`（换策略 = 新实验，AGENTS §15.5）。
5. ~~**单轮计划**：轮换只支持逐轮下发的计划；多轮段响亮拒收（§3.7）。~~
   **已被 §9.6 取代（2026-10-28）**：多轮段改成「配方进计划、每轮按窗口重解」，不再拒收。
6. `per_stage_quota` 的**类型不变**（仍是 `int`）：本版不做加权逐关配额（§7）。

## 5. 验收判据

| # | 判据 | 怎么测 |
|---|---|---|
| 1 | **窗口覆盖**：连跑 `ceil(n/k)` 轮，并集 == 全集、且 epoch 内无重复 | 单测 `window_for` |
| 2 | **配额守恒**：`per_stage_quota × len(窗口) ≈ target`（±取整） | 单测 + `volume_block` 块值 |
| 3 | **同源**：`_per_stage_quota(it)` == 块里的 `per_stage_quota` | 单测（同一 args/target/窗口） |
| 4 | **resume 一致**：同 `(rotate_seed, it)` 两次算出逐元素相同 | 单测（两次独立构造） |
| 5 | **老行为不变**：`rotation_mode` 缺席 ⇒ 块/pairs/配额逐字节同今天 | 既有 `test_volume_plan_block.py` 全绿 |
| 6 | **多轮段拒收**：轮换 + `n>1` 导出 ⇒ 响亮 SystemExit | 单测（假 args + `_export_offline_bundle` 入口或直接测判据函数） |
| 7 | **未采关不退化**：窗口外的关在 35 关全 eval 下不掉 | 实验（每次 eval 700 局），不在门禁内 |
| 8 | **窗口可见**：激活课程的事件行带 `rotation`（mode/all/window）；未激活的行**无此键** | 单测（`write_iteration` / `write_rollout_end` 两种输入逐键对比） |
| 9 | **控制台上屏**：`readIterMetrics` 出 `rotation` / `volume`，两列与面板同源渲染；缺数据显 `—`（不编 0） | dashboard 用例（真 JSONL → 读面 → 面板） |
| 10 | **静默空转被拦**：`rotation_mode` + 无 `target_transitions`（或非 per-tick）⇒ 课程加载期 `ValueError` | 单测 |

## 6. 测试

新增 `tests/worker/test_stage_rotation.py`：
1. `test_even_window_covers_all_in_ceil_rounds`（判据 1，含非连续关号 2000+i）
2. `test_window_is_resume_stable`（判据 4）
3. `test_pinned_window_always_contains_pinned`（模式 2）
4. `test_pinned_rest_rotates_without_overlap`（pinned 之外的余额关轮换覆盖）
5. `test_window_is_sorted_and_subset`（排序 + 子集）
6. `test_unknown_mode_and_bad_pinned_are_loud`（`ValueError`）
7. `test_per_it_zero_or_oversize_is_clamped`

改 `tests/worker/test_volume_plan_block.py`：
8. `test_volume_block_window_override_matches_plan_side`（窗口 12 关 ⇒ `per_stage_quota=ceil(target/12)` 且过 `validate_volume_block`）
9. `test_volume_block_window_must_be_a_subset`（打错关号 ⇒ ValueError）
10. `test_per_stage_quota_follows_the_window`（判据 3：`_per_stage_quota(it)` 与块同源；用 `TrainingSteps` 的既有最小 harness 或直接调 mixin 方法）
11. `test_check_plan_against_args_accepts_a_rotated_window` / `..._rejects_a_window_outside_stages`
    （评审 A5：窗口计划必须过发布期自检；关集 ⊄ `--stages` ⇒ `ProtocolError`）

S4（§9）新增：

12. `tests/worker/test_stage_rotation_events.py`：`event_from_args` 三态（缺席 ⇒ None；even ⇒ window/all 正确；
    pinned ⇒ pinned 原样进读数）+ 与 `window_from_args` 同源（同 args 同 it 两路逐元素相同）
13. `tests/worker/test_rotation_ledger_fields.py`：`write_iteration` / `write_rollout_end` 在轮换激活时
    带 `rotation`（+ `rollout_end` 带 `volume_stage_stats`）；未激活 ⇒ **键不存在**（逐字节老行为）
14. `tests/worker/test_stage_rotation_course.py` 追加：跨字段校验（无 `target_transitions` / 非 per-tick ⇒ 拒课）
15. `dashboard/tests/server-iters-rotation.test.ts`：本机形状 → `IterRow.rotation` / `volume`（逐关排序 +
    达标数）；缺键/坏形状 ⇒ null；`rollout_end` 行也带
16. `dashboard/tests/web-stage-rotation-panel.test.ts`：面板只在有轮换读数时渲染；两列写进 `METRIC_COLS`

## 7. 不做 / 明确排除

- **模式 3（weighted，逐关不同配额）**：需要 6–7 处（含 PPO 引擎 argv `--per-stage-quota type=int`、
  `remote/train_core.py:773/1195`、`remote/plan_run.py:212/366`、`worker/iter_rollout.py:672`、
  `worker/stream_collect.py:132` 的标量配额、`validate_volume_block_ext` 的键集与等式）。
  正确形态是新键 `per_stage_quota_by_stage` + argv JSON，**另立项**。
- **`carry_over`（超采结转）**：与 §15.1 的「同 `(stage,seed)` 不得跨轮重复」直接冲突，且要跨
  零落盘/裁剪/保留边界另存字节（`ShardSegments.begin()` 每轮清账）。模式 2 的价值不依赖它。
- **不改 `target_transitions` 语义**（仍是本轮总样本量）。
- **不动 `spread_games_by_stage` / `stream_collect` 的派发逻辑**：窗口只改「关集」，派发仍是逐局流式。
- **不做动态难度**：pinned 的难关是人工指定（AGENTS §0.2 四条禁令）。

## 8. 分步（本版执行）

| 步 | 内容 | 验收 |
|---|---|---|
| S1 | `worker/stage_rotation.py` + 7 条单测（不接线） | 判据 1/4 |
| S2 | `volume_block(stages_override)` + `loop_export` 窗口/est + `loop_volume` 退役 guard + `_per_stage_quota(it)` 同源 | 判据 2/3/5 |
| S3 | 身份（`_identity_payload` 激活时）+ 多轮段拒收 | 判据 6 |
| S3′ | ~~模式 3~~ / ~~carry_over~~ | 后置（§7） |
| S4 | 可观测性（§9）：账本 `rotation` + 分关进度上屏 + 课程前置校验 | 判据 8/9/10 |
| S5 | 云机轮换（§9.6，2026-10-28）：配方进计划 + 逐轮派生 + notebook 三态开关 + 云机读数进行账 | 多轮包每轮窗口 / 开关三态 / 账本两键 |

## 9. 可观测性：控制台看得见窗口（2026-10-28）

**触发**：S1–S3 落地后，「本轮采哪些关」只活在两个地方 —— 进程日志里 `describe()` 那**一行**
（滚走就没了）、以及 `volume_stage_stats.stages` 的**键**。而这两处都要人主动去捞：控制台里没有
`rotation` 一个字，**开着轮换的课在「指标」页与全关课程长得一模一样**；采了多少/差多少要翻账本 jsonl。
窗口不可见 ⇒ 出了「某关连着几轮没采到」这种账，没有人会在第一时间发现（§15.1 的覆盖承诺全靠人记）。

### 9.1 账本读数（写侧）

`iteration` 与 `rollout_end` 事件在**激活时**多一个 `rotation` 子字典（缺席 ⇒ 一个键都不写，老行逐字节不变）：

```jsonc
"rotation": {"mode": "even", "per_it": 12, "pinned": [], "all": 35, "window": [2000, 2001, …]}
```

- 产出 = `worker/stage_rotation.event_from_args(args, it, rotate_seed=)`（与窗口本身**同一个**
  `window_from_args` ⇒ 账本读数与采集用的窗口不可能两说）；`rotate_seed` 取循环实例上解析后的
  `self._rotate_seed`（与种子流同键）。
- `window` **排序**（与 `volume.stages` 同规）；`all` = `--stages` 全关数（控制台要显示 `12/35`）。
- **`rollout_end` 也带**它 + `volume_stage_stats`：窗口与分关账是**采集侧**事实，采集一收官就该上屏
  （与 2026-10-19「采集收官即多一点」同一口径），不必等 PPO 回传。
- 分关进度**不加新字段**：本机连续配额链收官时写的 `volume_stage_stats`（quota / 逐关
  collected·games·est·cap / shortfall / halted）本来就是按窗口关集算的（§3.4：`_volume_stages(it)`
  是三条腿共用的那一份）⇒ 控制台把 `rotation` 与它**并排读**即可。

### 9.2 控制台读面（dashboard 侧）

- `dashboard/src/server/iters.ts`：`parseIterRotation(raw)` / `parseIterVolume(raw)` 两个纯解析器，
  挂在既有 `buildIterRows` 上（`rollout_end` 行也走同一份）⇒ `IterRow.rotation` / `IterRow.volume`
  （additive，缺键 = null；与 `wire` 同规）。**读面只认账本**：窗口不重算（控制台 import 不到
  `nn-training/`，且重算 = 第二条真相）。
- 解析口径：坏形状（数组 / 缺 window / 关号非整数）⇒ `null`（不半渲染），逐关账按键升序。
- 类型住 `dashboard/src/web/view/metric-types.ts`（`IterRotation` / `IterVolume`），与 `IterWire` 并排。

### 9.3 上屏（两处，都只在有轮换读数时出现）

1. **「指标」页新列**：`窗口`（`12/35`；hover 列出关号）与 `各关进度`（`达标 11/12`；hover 列逐关
   `collected/quota` 与缺口）。列模型进 `view/metric-columns.ts`（表头文字唯一一份），**不**进
   首页速览表（`MAIN_ROW_KEYS` —— 六轮速览放不下窗口）。
2. **「指标」页顶部面板**（`app/panels/StageRotationPanel.tsx`）：① 最新一轮的窗口读数（mode /
   窗口/all / 达标 / halted·capped）；② 该轮逐关表（关 / 收到 / 达标线 / 局数 / 硬顶 / est / 缺口）；
   ③ 最近 ~12 轮的窗口一览（判「每轮换到哪批关、有没有长期缺口」）。无任何轮换读数 ⇒ 面板整块不渲染
   （老课程页零变化）。

### 9.4 课程侧前置校验（拦「配了轮换却没在轮换」）

窗口只在**动态采集**链上生效：配额分母 = `ceil(target / 窗口关数)`（§3.5），关集 = `_volume_stages(it)`（§3.4）。
而 `build_pairs` / `biz/course` 完全不认 `rotation_*` ⇒ 课程若只写 `rotation_mode` 而没写
`target_transitions`（或 `mode != per-tick`），那一轮**照旧全关采**，且账本/日志都不会说什么 —— 正是
「最难发现那种」的静默失效。修法：`CourseConfig` 加一条跨字段校验，加载期响亮拒课：

```
rotation_mode 非空 ⇒ 必须 target_transitions > 0 且 mode == "per-tick"
```

（既有课程全不带 `rotation_*` ⇒ 零影响；先例 = `_margin_pp_or_raise` 一类的「脏值拒课」。）

### 9.5 不做（本版）

- ~~**云机腿（`--export-bundle` 单轮包）的分关账**~~ —— **已落地（2026-10-28，见 §9.6）**。
- **按关曲线 / 覆盖进度条**：先把窗口与分关账搬上屏，看几轮真读数再谈图形化（避免在没有真数据时
  猜什么形状最有用）。

### 9.6 云机 rollout 支持轮换（2026-10-28，用户口径「云机 rollout 也要支持轮换。ipynb 加个配置开关」）

**根因**：`plan["volume"]` 是**一份**块（钉着导出那一轮的窗口），而窗口是 `it` 的函数 ⇒
`pairs_for` / `plan_run` 在第 2 轮起还在用第 1 轮的关集。v1 用 §3.7 拒收多轮包——功能关掉了，没修。

**做法**（全文 `docs/nn/training-stack.md` §46；控制台侧 §43）

1. **配方进计划**（additive）：`plan["rotation"] = {mode, per_it, pinned}`（窗口键 = 计划既有
   `rotate_seed`）+ `volume.est_s_all`（全关 est 表，导出时刻钉住）。缺席 ⇒ 键一个不多。
2. **唯一派生器** `worker/volume_waves.volume_block_for_window`：按窗口重解 stages / 逐关局数 /
   达标线 / 分关 est（同一分配器）；缺关响亮。`worker/plan` 新增 `rotation_recipe` / `round_window` / `volume_for_round` / `rotation_event`，
   `pairs_for` 改成「先取当轮块再重放」；`validate_plan` 四道门（形状 · 需要 volume · 每轮可解 ·
   est_s_all 覆盖）；`check_plan_against_args` 逐轮用当轮窗口/est 重解。
3. **导出腿**：删 §3.7 拒收；`_volume_plan_block` 一次按全关取 est（窗口切出的值与旧调用逐字节相同）；
   `build_plan(rotation=…)`。
4. **笔记本三态开关**：`CFG["rotation"] = auto|on|off` → `--rotation`（缺省 auto **不进 argv**）；
   `plan_run` 逐轮 `volume_for_round`；`off` 降级全关 + 账本 `rotation.override` 留痕；`on` 计划没带
   轮换则拒跑。
5. **读数**：`plan_run._row_extra`（rotation + volume_stage_stats）→ `LEDGER_FIELDS` 两个新键；
   `stream_collect.stage_stats()` 与本机腿同形（`est_src="plan"`）⇒ 控制台**零代码**。

**验收**：`tests/worker/test_plan_rotation_multiround.py`(12) · `test_stream_collect_stats.py`(3) ·
`tests/remote/test_plan_rotation_cloud.py`(7) · `tests/trainer/test_stage_rotation_export.py`(3) ·
`tests/remote/test_offline_boot.py` 的 `--rotation` argv 用例。判据 7（窗口外关不退化）仍待真实验。

**边界**：est 仍是导出时钉住（冻结包）；`off` 覆盖 = 另一个语料分布（只能靠账本留痕）；
离线段不跑 `verify_plan_file`（已退役的 kind=run 门）⇒ 覆写不会撞指纹。
