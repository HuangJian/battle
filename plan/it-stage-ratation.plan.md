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

### 3.7 拒绝面（A4）

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
5. **单轮计划**：轮换只支持逐轮下发的计划；多轮段响亮拒收（§3.7）。
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
