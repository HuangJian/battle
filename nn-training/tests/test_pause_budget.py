"""test_pause_budget.py — 让路预算的安全裕度（plan/transfer-scheduling §2.2 #5 /【R2-10d】）。

**为什么单独一个文件**：这条不变量的失效方式很隐蔽——预算只要**接近**任一侧超时，就不是
「让路」而是「自判停滞」：worker 侧 `BODY_IDLE_TIMEOUT_SEC=45s` 是「等下一个字节」的空闲判停
（不是「允许暂停 45s」），hub 侧 `SEND_TIMEOUT_SEC=60s` 是分片写超时。撞上任意一侧的后果都是
整份 body 白传重来，而日志上只看到一次「超时」（现场像网络抖动）。所以它要被钉在**常量**上，
不靠「当时测得挺快」。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.worker as W
from remote.bulk_sched import PAUSE_BUDGET_SEC

# 常量从**它住的地方**取（S4 第十六刀把它随 `HubHandler._bytes` 搬到 `hub/http_face.py`）。
# `remote.hub_server.SEND_TIMEOUT_SEC` 仍是同一个对象的 re-export（名字是契约），但它是**读**的
# 入口而非 patch 点 —— 这里要比的就是那个值本身，所以从实现侧取。
from remote.hub.http_face import SEND_TIMEOUT_SEC


def test_pause_budget_is_small_in_absolute_terms() -> None:
    """预算写死且小（≤5s）：让路是「给控制环插队」，不是「停一会儿」。"""
    assert PAUSE_BUDGET_SEC <= 5.0


def test_pause_budget_below_worker_idle_timeout() -> None:
    """低于 worker 的空闲判停（45s）：超了就是自判停滞 → 整份重传。"""
    assert PAUSE_BUDGET_SEC < W.BODY_IDLE_TIMEOUT_SEC, "让路长过 worker 空闲判停 = 自判停滞"


def test_pause_budget_below_hub_send_timeout() -> None:
    """**同时**低于 hub 的分片写超时（60s）：只写 45s 是最容易犯的错——撞上这一侧一样断流。"""
    assert PAUSE_BUDGET_SEC < SEND_TIMEOUT_SEC, "让路长过 hub 分片写超时 = 被 hub 断流"


def test_both_margins_hold_together() -> None:
    """判据合成一句：预算必须**同时**小于两侧，且不是「刚好差一点」。"""
    assert PAUSE_BUDGET_SEC * 5 < W.BODY_IDLE_TIMEOUT_SEC
    assert PAUSE_BUDGET_SEC * 5 < SEND_TIMEOUT_SEC
