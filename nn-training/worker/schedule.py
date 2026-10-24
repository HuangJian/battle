"""ppo_schedule —— 超参分段表查表（plan/rl-training-config.md §8 / M1c）。

口径（评审 R1-6 + LC §4.7）：
  * **按绝对 iter 查表**（不是相对 resume 偏移）——断点续跑后 lr 由 schedule
    从绝对 iter 重算，行为一致；
  * 每段 `{until_iter?, lr?, epochs?, mb?, kl_coef?}`：`until_iter` 为该段生效的
    最后迭代（升序）；末段可省略 `until_iter`（兜底，永远生效）；
  * `lr` 改 `opt.param_groups[0]['lr']`（Adam 动量在 opt.state，改 lr 不重置）；
    `epochs/mb` 每轮查表；`kl_coef` 是 `ppo_update` 新增的 update 期形参（默认
    0.0 向后兼容），schedule 显式传值；`ent_coef`（2026-09-11）同为 update 期形参，
    **默认 None = 引擎常量 ENT_COEF**（缺省路径逐字节不变）；
  * 缺省走固定 6 超参（schedule 缺省路径不触碰 engine.py 模块常量求值路径）。

另有 `kickstart_coef`（BC 缰绳的迭代衰减）——同样是一条「按 iter 算系数」的
调度：`update_kwargs` 与「缰绳是否还活着」的守卫（`worker/loop_guards_leg.py`）
共用它，公式只此一份。它住 L2 是因为后者在 worker 包内，不得反向 import trainer。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from common.protocol import coef_active

_SCHED_KEYS = ("lr", "epochs", "mb", "kl_coef", "kl_cap", "ent_coef")


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


def kickstart_coef(args: Any, it: int, start_it: int = 1) -> float:
    """BC 缰绳系数：按 run 原点衰减，低于 `coef_active` 阈值直接归零。

    唯一事实源：`trainer/run_rl.update_kwargs`（PPO 更新参数）与本包
    `loop_guards_leg._rein_active`（锚已退火则不判回锚）都调这里，不自写第二份公式。

    锚点：`update_kwargs` 的衰减锚点是 `start_it`，而 resume 会把 start_it
    推到断点——直接复用导致 kk 在每次重启回到满额（c4-margin it31 manifest
    kk=1.0 实证；kb1 复跑 it53 KL 0.315 同源）。kickstart 腿的 warmup 恒 0
    （validate_args 强制配对），故 run 原点恒为 1，与 intent/goal 的 warmup 语义无交集。

    几何衰减永远到不了精确 0（实测 kl=2^-36=1.455e-11 时判据仍放行，白付 ref 权重
    传输 + worker 每轮 3 s 预计算，而数学贡献 ≈1.8e-12 可忽略）⇒ 低于阈值直接归零。
    这是**唯一**的衰减源：归零后 loop_steps 不再附 ref 字节 → worker 不再加载 →
    engine 的 `> 0` 自然为假。
    """
    policy_iter = (it - start_it) - args.warmup_iters + 1
    kl_coef = (
        args.kickstart_kl * (args.kickstart_decay ** max(0, policy_iter - 1))
        if args.kickstart_kl > 0 and policy_iter >= 1
        else 0.0
    )
    if not coef_active(kl_coef):
        kl_coef = 0.0
    return float(kl_coef)
