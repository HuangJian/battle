"""common/hold_window.py — 「接管后多久没进度算掉线」的**唯一**实现（900s，env 可覆盖）。

为什么住 `common/`：这条判据有**两侧读者**——hub 侧判 hold 活性（`hub/task_pack.py` 的
`hold_state` / 协作派发），训练侧读控制文件时就地自判活（`worker/loop_control.parse_held`
的 `held` 缓存）。两侧必须同一把尺子：控制文件里的 `last_progress_at` 是 hub 写的，训练侧
拿它跟自己减，窗口宽一格就会「hub 认为活着、训练侧认为可以抢跑」。

2026-11-16 之前它住 `hub/task_pack.py`，训练侧靠 `from hub.task_pack import …` 的函数内
import 拿这个数 —— 那条边让 `trainer/loop_control` 成了「碰网络的模块」，而它其实只是读
一个阈值。搬到 L0 后两侧都只依赖 `common/`，谁也不再因为一个常量而依赖对方的整个包。

**不要**与 `AUTO_HANDOFF_PENDING_SEC`（导包窗口）合并：两者都是 900s 是巧合，调一个会误伤
另一个（plan/worker-type-dispatch-model §1.5.2-P0-1 明令禁止合并常量）。`hub/store_leases.py`
里还有一个 **BC 进度租约**的判活窗（`HOLD_STALE_ENV`），语义不同，同样不合并。
"""

from __future__ import annotations

import os

#: 接管后连续静默（无任何进度信号）超过它 ⇒ 判「掉线」（惰性判据：谁读谁算，不养清理线程）。
#: env 覆盖 `BCITY_HOLD_PROGRESS_STALE_SEC`（e2e/单测调秒级；合法值必须 > 0）。
HOLD_PROGRESS_STALE_SEC = 900.0

#: env 旋钮名（两侧共用同一个，测试只设一处）。
HOLD_STALE_ENV = "BCITY_HOLD_PROGRESS_STALE_SEC"


def hold_progress_stale_sec() -> float:
    """生效的接管静默阈值（env 覆盖；非法 / 非正数 ⇒ 缺省）。**调用时读 env**（可 monkeypatch）。"""
    raw = os.environ.get(HOLD_STALE_ENV, "").strip()
    try:
        v = float(raw)
    except ValueError:
        return HOLD_PROGRESS_STALE_SEC
    return v if v > 0 else HOLD_PROGRESS_STALE_SEC
