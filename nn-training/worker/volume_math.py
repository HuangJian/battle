"""volume_math.py —— `volume_*` 三兄弟共用的最小数学层（plan/nn-training-reuse-and-deps.plan.md §S3）。

**为什么有它**：`_ceil_div` 曾逐字节存在**三份**（`volume_alloc` / `volume_quota` / `volume_waves`），
`games_total` **两份**（`ContinuousPlan` / `TopUpPlan`）—— 同一份口径抄三处，改一处漏两处就是本仓
最贵的 bug 类型（取整边界 / 量纲，见 `volume_waves.py` 文件头的量纲节）。

**零内部依赖**（只 import stdlib）⇒ 三个调用方 import 它都是**向下边**，分层不动。

口径统一的两条（R4 的例外**不**成立：旧副本的差别是笔误级噪声，不是有意的行为差异）：

1. `ceil_div` 取**最严**那份（带 `int()` 强转的 alloc / quota 版）：浮点入参是「本该报错却静默
   算错」的温床，而 waves 版的 `-(-a // b)` 会把浮点原样带出去。
2. `games_total` 只有一种语义（`sum(games_by_stage.values())`）⇒ 收到基类，两个计划类共用一份。

调用方**保留原有的私有名**（`from worker.volume_math import ceil_div as _ceil_div`）——
名字是模块内的局部细节，位置不是：调用点一行不改，diff 只看得见「三份变一份」。
"""

from __future__ import annotations

from collections.abc import Mapping


def ceil_div(a: int, b: int) -> int:
    """`ceil(a / b)`（正整数；**不**引入浮点 ⇒ 不吞 `20 × 1.15` 那类界值误差）。"""
    return -(-int(a) // int(b))


def games_total(games_by_stage: Mapping[int, int]) -> int:
    """本批要派的总局数（空 = 本轮不派）。"""
    return sum(games_by_stage.values())


class GamesByStageMixin:
    """`games_by_stage` 的共用视图（`ContinuousPlan` / `TopUpPlan` 的 `games_total` 唯一实现）。

    只提供**属性**、不提供字段：两个子类各自声明 `games_by_stage`（自己的 dataclass 字段）。
    非 dataclass 基类的注解不进 `__dataclass_fields__` ⇒ 字段顺序 / 默认值 / `frozen` 语义都不变。
    """

    games_by_stage: dict[int, int]

    @property
    def games_total(self) -> int:
        return games_total(self.games_by_stage)
