"""common/text.py —— 异常现场的渲染（`exc_tail`）。

异常文本在本仓有三个用途，需求不同、**渲染必须一致**：人看的日志、回传给 hub 的
`failure detail`、e2e/门禁里的失败定位。只留 `str(e)` 时，形如
`[Errno 2] No such file or directory: '…/i3/w2/rl_s0_seed111'` 的单行错**无从定位抛点**
（`docs/nn/engineering.md` §12 实测：一次 `-n 12` 下的 ENOENT 只能靠翻日志猜）。

截**尾**段而非头段：栈顶几帧是 transport 样板，真正的原因是最后一帧的
`ProtocolError: bun 未安装 …`。

本函数原有两个副本（`remote/worker.py::_failure_detail` 与 `rl/stream.py::_exc_tail`），
后者 docstring 写着「与前者同口径…故就地保留同款小助手」——而 `rl/stream.py` 不 import
`remote.worker` 的理由（别把 torch 拖进采样路径）对本模块**不成立**：
`common` 只依赖 stdlib，谁都能 import。于是那条「就地保留」的豁免不再需要。
"""

from __future__ import annotations

__all__ = ["exc_tail"]

#: 缺省的现场长度上限。4000 字符 ≈ 40 帧，够定位也不至于把回报体撑爆。
DEFAULT_LIMIT = 4000


def exc_tail(e: BaseException, limit: int = DEFAULT_LIMIT) -> str:
    """当前异常的 traceback **尾段**（`limit` 字符），用于不能抛的回报路径。

    `e` 只用于兜底文案（`format_exc()` 读的是 `sys.exc_info()`，因此本函数必须在
    `except` 块内调用，或在被 `except` 捕获的调用栈里）——保留该形参是为了签名与
    历史一致，也为了在 `format_exc` 本身不可用时还能说清「是什么异常」。

    绝不抛：极端情况下 `format_exc` 可能不可用（解释器关停中、`sys.stderr` 被换掉），
    此时退化为单行 `类型: 消息`。回报路径上的函数不允许因为「打日志失败」而失败。
    """
    import traceback

    try:
        return traceback.format_exc()[-limit:]
    except Exception:  # 极端情况下 format_exc 本身不可用——退回落单行
        return f"{type(e).__name__}: {e}"[:limit]
