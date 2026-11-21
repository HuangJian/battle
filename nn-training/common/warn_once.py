"""`common/warn_once.py` — 「同一个 key 只告警一次」的显式去重桶（P0-5，2026-11-21）。

仓里此前有三份同形状的模块级 `set`：

```
_RETIRED_SOURCE_NOTED: set[tuple[str, str]] = set()
...
if key in _RETIRED_SOURCE_NOTED:
    return
_RETIRED_SOURCE_NOTED.add(key)
log(...)
```

（`trainer/loop_transport` 两份 + `worker/gate_halt` 一份。）它们本身没问题——纯日志去重、
不参与判定、不跨进程；问题在**形状**：`if key in S: return` 与 `S.add(key)` 是散在调用点上的
两步，「查过就一定会加」只能靠人读代码确认，而忘记加就等于每轮刷一行。收成对象后只剩一句
`if not bucket.mark(key): return`，查与加是同一个原子动作。

**为什么不加锁**（`remote/rollout_relay.LiveLinks` 加了）：那张表要 `remove`，两条线程的
「查了再删」会互相抢先抛 `ValueError`；这里只 `add`，最坏结果是两条线程同时通过判定、多打
**一行**日志（`set.add` 本身在 CPython 下是原子的）。为一句重复日志去持有锁，还要回答
「锁内能不能调日志」，不划算——契约就写在 `mark()` 的 docstring 里。

**模块级可变状态**：本模块只放类，不放实例（`common/__init__.py` 契约 4 与
`tests/test_python_shape_budget.py` 的 G2 判据都要求这一点）；实例住在各自的模块里。
"""

from __future__ import annotations

from collections.abc import Hashable

__all__ = ["WarnOnce"]


class WarnOnce:
    """一个「见过哪些 key」的集合：`mark()` 第一次返回 `True`（并记下），之后都返回 `False`。

    用法（`worker/gate_halt` 那份）：

    ```
    warned = WarnOnce()
    if warned.mark(key):        # 第一次
        log(...)
    ```

    也可以先 `seen()`（只查不记）再决定要不要 `mark()`——`worker/gate_halt` 就要这样：文件不存在时
    **不该**烧掉这个 key，否则这门课以后真出现文件也不会再告警。

    **没有 `clear()`**：要重来就换一份新桶（`WarnOnce()`）。与 `HubState` / `LogRouter` 的用例
    隔离同规 ——「整份换新」比「逐个清表」少一种「漏清一张、用例之间偷偷串味」的失败形态；
    也避免 `clear` 这个名字再多散进一个模块（`test_reuse_ledger` 的同名 ≥3 模块判据）。
    """

    def __init__(self) -> None:
        self._seen: set[Hashable] = set()

    def mark(self, key: Hashable) -> bool:
        """`key` 以前没见过 ⇒ 记下并返回 `True`；见过 ⇒ 返回 `False`（**不**重复记）。"""
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def seen(self, key: Hashable) -> bool:
        """只查不记（用于「满足别的条件才烧 key」的调用点）。"""
        return key in self._seen

    def __len__(self) -> int:
        return len(self._seen)
