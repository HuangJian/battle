"""worker/stage_rotation.py —— 逐轮采样子集（**窗口**）的唯一定义（纯函数，账本 L4）。

plan/it-stage-ratation.plan.md §3.1。为什么要单独一个模块（2026-10-27 评审 A3）：

窗口的消费者有**三个**，而且分属两个进程/两套链：

  * `trainer/loop_export`（导出计划里的 `volume` 块 → 云机按它采）；
  * `trainer/loop_volume`（本机连续配额链：配额 / 硬顶 / unmet / 分关账）；
  * `trainer/loop_steps._per_stage_quota`（打包前裁剪 + PPO 装载截断）。

三处各算一次 = 三条腿可能各用一个窗口，而错法是**静默**的：采集按 12 关、裁剪按 35 关
⇒ 约 2/3 的样本被裁掉还照常打「丢 N 局未传」。所以窗口只在这里算，三处都调它。

## 判据

* **窗口只由 `(rotate_seed, it)` 决定**（与 `biz/course.py:107-135` 的 `--rotate-stages`
  分支同一个构造：`default_rng([rotate_seed, 0xA11CE, epoch_idx])`）⇒ resume-safe：
  断点续跑后抽出同一个窗口。
* 差别只有一处（有意）：那边 `permutation(total_stages)` 把关号当作 `0..n-1` 的**位置**
  （非连续关号——例如自定义关的 `2000+i`——会算出窗口外的关号）。本版打的是**位置**，
  再映射到**排序后**的关集，所以任意关号都成立。
* 返回值**必排序**：`volume.stages` 的顺序会进声明集/`data_fp`，乱序会让同一轮两次导出
  得到不同指纹（看起来像换了实验）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from worker.volume_waves import parse_stages_arg

#: 窗口排列的流标签（与 `biz/course.py` 的 `--rotate-stages` 同一个常量：同一个
#: `(rotate_seed, epoch_idx)` 在两处抽出同一个排列，便于把老旋钮与新模式对账）。
EPOCH_TAG = 0xA11CE

#: 支持的模式（`""` = 关闭；关闭时调用方拿到**全集**，无需分支）。
MODES = ("even", "pinned")


def _positions(rotate_seed: int, epoch_idx: int, n: int) -> list[int]:
    """`0..n-1` 的一个确定性排列（epoch 键控：同一 epoch 内的所有窗口共用一个排列）。"""
    rng = np.random.default_rng([int(rotate_seed), EPOCH_TAG, int(epoch_idx)])
    return [int(i) for i in rng.permutation(int(n))]


def _rotate_window(
    stages: Sequence[int], *, it: int, rotate_seed: int, k: int
) -> list[int]:
    """在 `stages` 上取第 `it` 轮的窗口（`ceil(n/k)` 轮铺满一遍，窗口间不重叠）。"""
    n = len(stages)
    if n <= 0:
        return []
    if n == 1:
        return [int(stages[0])]
    per_epoch = -(-n // k)
    pos = (int(it) - 1) % per_epoch
    epoch_idx = (int(it) - 1) // per_epoch
    perm = _positions(rotate_seed, epoch_idx, n)
    return [int(stages[i]) for i in perm[pos * k : (pos + 1) * k]]


def window_for(
    *,
    mode: str,
    it: int,
    all_stages: Sequence[int],
    rotate_seed: int,
    per_it: int = 0,
    pinned: Sequence[int] = (),
) -> list[int]:
    """第 `it` 轮该采哪些关（**排序**；确定性；`mode` 空 ⇒ 全集）。

    * `even`：每轮 `per_it` 关，`ceil(n/per_it)` 轮覆盖一遍（`per_it >= 1` 必须显式给）；
    * `pinned`：`pinned` 每轮恒在，余额 `per_it` 关从「其余关」里轮换（`per_it = 0` 合法
      = 每轮只采 pinned）；
    * `mode` 为空 ⇒ 返回全集（关闭窗口）。

    失败一律 `ValueError`（调用方转 SystemExit / ProtocolError —— 静默取一个窗口 = 采集量
    对不上目标，最难发现那种）。
    """
    stages = sorted({int(s) for s in all_stages})
    if not stages:
        raise ValueError("stage_rotation 需要至少一个 stage（all_stages 为空）")
    if int(it) <= 0:
        raise ValueError(f"stage_rotation 的 it 必须 >= 1（收到 {it}）")
    mode = str(mode or "").strip()
    if not mode:
        return stages
    if mode not in MODES:
        raise ValueError(f"未知的 rotation_mode={mode!r}（支持 {' / '.join(MODES)}；空 = 关闭）")

    stage_set = set(stages)
    pin = sorted({int(s) for s in pinned})
    unknown = [s for s in pin if s not in stage_set]
    if unknown:
        raise ValueError(f"rotation_pinned 里有不在 --stages 的关号：{unknown}")

    if mode == "even":
        if int(per_it) < 1:
            raise ValueError("rotation_mode=even 需要 rotation_per_it >= 1")
        return sorted(
            _rotate_window(stages, it=int(it), rotate_seed=int(rotate_seed), k=int(per_it))
        )

    # pinned：pinned 恒在 + 余额轮换（per_it=0 ⇒ 只采 pinned，合法）
    rest = [s for s in stages if s not in set(pin)]
    k = int(per_it or 0)
    if k <= 0:
        return pin
    return sorted(pin + _rotate_window(rest, it=int(it), rotate_seed=int(rotate_seed), k=k))


def window_from_args(
    args: Any, it: int, all_stages: Sequence[int], *, rotate_seed: int | None = None
) -> list[int]:
    """从 args 取窗口（三个消费者共用；**关闭 ⇒ 原样全集**，调用方不必写分支）。

    读面 = `rotation_mode` / `rotation_per_it` / `rotation_pinned`（课程键经
    `CourseConfig.flat_overrides` 映射到 argparse dest）+ rotateSeed。

    ★ **窗口键必须与种子流是同一个 rotateSeed**：`rotate_seed=` 显式传循环实例上的
    `self._rotate_seed`（`resolve_rotate_seed` 解析后的**实际**值：显式 > 断点继承 > 抖动）。
    不传时依次退到 `args._rotate_seed`（`step_prepare_iter` 每轮注入的运行期值）→
    `args.rotate_seed`（课程/CLI 声明值，可能是 None）→ 0。**不要**直接读声明值：那是
    「声明什么」而不是「这轮实际用什么」，与种子流的键不是同一个数。
    """
    mode = str(getattr(args, "rotation_mode", "") or "").strip()
    if not mode:
        return sorted({int(s) for s in all_stages})
    if rotate_seed is not None:
        seed = int(rotate_seed)
    else:
        seed = int(
            getattr(args, "_rotate_seed", 0)
            or getattr(args, "rotate_seed", 0)
            or 0
        )
    raw = str(getattr(args, "rotation_pinned", "") or "").strip()
    return window_for(
        mode=mode,
        it=int(it),
        all_stages=all_stages,
        rotate_seed=seed,
        per_it=int(getattr(args, "rotation_per_it", 0) or 0),
        pinned=parse_stages_arg(raw) if raw else (),
    )


def describe(window: Sequence[int], all_stages: Sequence[int]) -> str:
    """一行读数（每轮打一条，不逐局）：`rotation even 12/35 关 [..]`。"""
    return (
        f"rotation {len(window)}/{len(sorted({int(s) for s in all_stages}))} 关"
        f"={list(int(s) for s in window)}"
    )
