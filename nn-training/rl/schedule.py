"""ppo_schedule —— 超参分段表查表（plan/rl-training-config.md §8 / M1c）。

口径（评审 R1-6 + LC §4.7）：
  * **按绝对 iter 查表**（不是相对 resume 偏移）——断点续跑后 lr 由 schedule
    从绝对 iter 重算，行为一致；
  * 每段 `{until_iter?, lr?, epochs?, mb?, kl_coef?}`：`until_iter` 为该段生效的
    最后迭代（升序）；末段可省略 `until_iter`（兜底，永远生效）；
  * `lr` 改 `opt.param_groups[0]['lr']`（Adam 动量在 opt.state，改 lr 不重置）；
    `epochs/mb` 每轮查表；`kl_coef` 是 `ppo_update` 新增的 update 期形参（默认
    0.0 向后兼容），schedule 显式传值；
  * 缺省走固定 6 超参（schedule 缺省路径不触碰 engine.py 模块常量求值路径）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_SCHED_KEYS = ("lr", "epochs", "mb", "kl_coef")


class ScheduleError(ValueError):
    pass


def resolve_ppo_schedule(entries: Sequence[Mapping[str, Any]], it: int) -> dict[str, Any]:
    """`it`（≥1）→ 生效覆盖 `{lr?, epochs?, mb?, kl_coef?}`（空 dict = 固定值路径）。

    段匹配规则：段 i 覆盖 `(prev_until_i, until_i]`；末段（无 until_iter）覆盖
    `(prev_until, ∞)`。命中即返回该段的非空键（段不重叠）。

    全表结构先校验（升序 + 仅末段可省略 until_iter），再按 it 找命中段——
    结构错误在任何 it 下都响亮报错，不随命中段位置隐没。
    """
    prev = 0
    for i, e in enumerate(entries):
        until = e.get("until_iter")
        if until is None:
            if i != len(entries) - 1:
                raise ScheduleError(f"ppo_schedule 段 {i}（非末段）缺少 until_iter——只有末段可省略")
            continue
        until = int(until)
        if until <= prev:
            raise ScheduleError(
                f"ppo_schedule 段 {i} until_iter={until} 未严格递增（上一段截止 {prev}）"
            )
        prev = until
    prev_until = 0
    for e in entries:
        until = e.get("until_iter")
        if until is not None:
            until = int(until)
            lo, hi = prev_until, until
            prev_until = until
        else:
            lo, hi = prev_until, None
        hit = it > lo if hi is None else lo < it <= hi
        if hit:
            return {k: e[k] for k in _SCHED_KEYS if k in e and e[k] is not None}
    return {}
