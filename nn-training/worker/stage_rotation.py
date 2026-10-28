"""worker/stage_rotation.py —— 逐轮采样子集（**窗口**）的 args 门面（账本 L4）。

plan/it-stage-ratation.plan.md §3.1。为什么要单独一个模块（2026-10-27 评审 A3）：

窗口的消费者有**三个**，而且分属两个进程/两套链：

  * `trainer/loop_export`（导出计划里的 `volume` 块 → 云机按它采）；
  * `trainer/loop_volume`（本机连续配额链：配额 / 硬顶 / unmet / 分关账）；
  * `trainer/loop_steps._per_stage_quota`（打包前裁剪 + PPO 装载截断）。

三处各算一次 = 三条腿可能各用一个窗口，而错法是**静默**的：采集按 12 关、裁剪按 35 关
⇒ 约 2/3 的样本被裁掉还照常打「丢 N 局未传」。所以窗口只在这里算，三处都调它。

## 分层（2026-10-28，云机轮换）

窗口的**数学**（`window_for` / `window_from_recipe` / `event_from_recipe`）已下沉到
`worker/volume_waves.py`（L3）：`worker/plan.py`（L4）也要按轮解窗口，而计划里重放窗口
正是云机轮换的基础 —— 留着 L4 门面会让 L4→L4 变成上向边。本模块保留 args 面
（课程/CLI 的 `rotation_*` 键 → 配方/窗口/账本读数），并以**同名转发**暴露核心
（`window_for` / `window_from_recipe` / `event_from_recipe` / `MODES` / `EPOCH_TAG`），
旧读者一行不改；窗口仍然**只有一个定义**。

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

# 窗口核心在本层之下（`worker/volume_waves`，L3）：同名转发（`X as X` = 显式 re-export）。
# 这里**不重写**任何窗口数学 —— 两份实现 = 两条真相。
from worker.volume_waves import EPOCH_TAG as EPOCH_TAG
from worker.volume_waves import ROTATION_MODES as MODES
from worker.volume_waves import event_from_recipe as event_from_recipe
from worker.volume_waves import parse_stages_arg
from worker.volume_waves import window_for as window_for
from worker.volume_waves import window_from_recipe as window_from_recipe


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


def recipe_from_args(args: Any) -> dict | None:
    """args → 计划里的**轮换配方**（`rotation_mode` 为空 ⇒ None = 不落键）。

    为什么配方要进计划（2026-10-28，云机轮换）：全离线/半离线腿的逐轮语料由计划重放，
    而窗口是 `(rotate_seed, it)` 的函数 —— 计划只带**一份** `volume` 块（导出那一轮的窗口）
    时，云机跑到第 2 轮就没有「这一轮该采哪些关」的可信来源了。配方进计划 ⇒ 两侧用
    同一个 `window_for` 解出每一轮的窗口（`worker/plan.volume_for_round`）。

    只带三个标量（`mode` / `per_it` / `pinned`）；种子键用计划既有的 `rotate_seed`
    （与种子流同键，不另立一个）。
    """
    mode = str(getattr(args, "rotation_mode", "") or "").strip()
    if not mode:
        return None
    raw = str(getattr(args, "rotation_pinned", "") or "").strip()
    return {
        "mode": mode,
        "per_it": int(getattr(args, "rotation_per_it", 0) or 0),
        "pinned": parse_stages_arg(raw) if raw else [],
    }


def event_from_args(args: Any, it: int, *, rotate_seed: int | None = None) -> dict | None:
    """`iteration` / `rollout_end` 事件的 `rotation` 子字典（plan §9.1；关闭 ⇒ None）。

    为什么要有这一格（2026-10-28，用户口径「每轮窗口与各关进度可见」）：窗口此前只活在
    进程日志的 `describe()` 那**一行**里（滚走就没了）与 `volume_stage_stats` 的**键**上 ⇒
    控制台里「开着轮换的课」与「全关课程」长得一模一样，采了多少/差多少要人翻 jsonl。

    口径：
      * 与窗口本身**同一个** `window_from_args`（不可能两说）；`rotate_seed` 语义同它
        （调用方传循环实例上解析后的 `self._rotate_seed` = 种子流那把钥匙）。
      * `window` 排序（与 `volume.stages` 同规）；`all` = `--stages` 全关数（显示 `12/35`）。
      * `rotation_mode` 缺席 ⇒ None（调用方**不写键**，老账本行逐字节不变）。
      * 配置非法（`--stages` 缺失/不可解析、未知 mode、pinned 不在关集）**照旧响亮**
        `ValueError`（本模块的统一纪律）；落账点的处理见 `trainer/loop_steps._rotation_event`
        （采集侧 `_volume_stages` 已经 SystemExit 过同一份配置）。
    """
    mode = str(getattr(args, "rotation_mode", "") or "").strip()
    if not mode:
        return None
    stages = parse_stages_arg(str(getattr(args, "stages", "") or ""))
    window = window_from_args(args, int(it), stages, rotate_seed=rotate_seed)
    raw = str(getattr(args, "rotation_pinned", "") or "").strip()
    return {
        "mode": mode,
        "per_it": int(getattr(args, "rotation_per_it", 0) or 0),
        "pinned": parse_stages_arg(raw) if raw else [],
        "all": len(sorted({int(s) for s in stages})),
        "window": [int(s) for s in window],
    }


def describe(window: Sequence[int], all_stages: Sequence[int]) -> str:
    """一行读数（每轮打一条，不逐局）：`rotation even 12/35 关 [..]`。"""
    return (
        f"rotation {len(window)}/{len(sorted({int(s) for s in all_stages}))} 关"
        f"={list(int(s) for s in window)}"
    )


# 供 `plan.py` 等 L4 读者用的显式再导出（`Mapping`/`Any` 在上面已 import，仅类型面）。
__all__ = [
    "EPOCH_TAG",
    "MODES",
    "describe",
    "event_from_args",
    "event_from_recipe",
    "recipe_from_args",
    "window_for",
    "window_from_args",
    "window_from_recipe",
]
