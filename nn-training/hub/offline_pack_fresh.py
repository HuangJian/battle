"""hub/offline_pack_fresh.py — 接管前的「这份包还新不新」判据（2026-10-24，用户口径第 4 条）。

为什么单独一块：`hub/offline_push.py` 已顶到 LOC 预算（1000 行，`tests/test_python_loc_budget.py`），
而这套判据是**纯函数** —— 输入是 hub 活清单的一行 + assignment 记录，输出是「能不能接管」，
没有线程、没有网络、没有落盘，拆出来还能被单测直接钉住（不必起派发器）。

契约（两条判据，都取 hub 的**活清单**，不做本地推断）：

* `stale_reason` 非空 —— 包的起点（`init_weights`）≠ 课程**当前**活动权重。本机每推进一轮，
  这个字段就会变（`hub.task_pack.task_pack_stale_reason`）；
* 包文件 sha ≠ 提议时记下的 sha —— 中途已经被重导过一份。

清单里**没有**这门课 ⇒ 不在这里下结论（停课/课程消失交给 `claim_offline` 去拒，别编理由）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["pack_still_fresh"]


def pack_still_fresh(rows: Any, rec: Any) -> tuple[bool, str]:
    """`(可接管, 不行的原因)`；原因只在**确定**时给（空串 = 没查出问题）。"""
    course = str((rec or {}).get("course") or "")
    was = str((rec or {}).get("pack_sha256") or "")
    if not course or not isinstance(rows, (list, tuple)):
        return True, ""
    for row in rows:
        if not isinstance(row, dict) or str(row.get("course") or "") != course:
            continue
        why = str(row.get("stale_reason") or "")
        if why:
            return False, why
        sha = str((row.get("pack") or {}).get("sha256") or "")
        if sha and was and sha != was:
            return False, "任务包在被提议之后被重导过（sha 变了）"
        return True, ""
    return True, ""
