"""`remote/bc_job.py` 两条回传腿捕什么（P0-7 第十九批，`_BC_HTTP_ERRORS`）。

口径：`remote.http._request` 的失败面就三类 —— urllib/socket 的 `OSError`、体半途断/首块太慢的
`RetryableError`、响应体不像话的 `ProtocolError`（ValueError 子类）。这三类 ⇒「这一发没成」，
走原有的重试 / backlog 语义（resume 查不到就 release 再领，epoch 送不到就留 backlog 等隧道）。

**编程错不许落这个集**：`AttributeError` / `TypeError` 说明我们读响应的姿势坏了。旧代码用
`except Exception` 把它记成「网络不通」⇒ 白等两轮退避，再把一个**含一半信息**的失败当成瞬时
失败写进 WARN，现场看就是「hub 时好时坏」。

判据做成行为断言：数 `_request` 被调了几次（`3` = 真的重试过；`1` = 一下就穿出去）。
"""

from __future__ import annotations

import pytest

from common.ports import PortPair
from common.protocol import ProtocolError, RetryableError
from remote import bc_job as mod

URL = "http://127.0.0.1:9"
TOKEN = "tok"
JID = "j1"

#: 这两个名字在 `remote/bc_job.__all__` 里 —— 前导下划线是本仓「搬出去的族用转发别名回
#: `remote.worker`」的命名约定（那边逐名转发同一对象），不是「测试在戳私有实现」。
#: G5 的口径是「断言表达式里不许出现私有属性」⇒ 在此收一次别名、返回值也先收进局部量。
fetch_resume = mod._bc_fetch_resume
post_epoch_raw = mod._bc_post_epoch


def post_epoch(log) -> bool:
    """`_bc_post_epoch` 的本文件别名（理由见上）。"""
    return post_epoch_raw(URL, TOKEN, JID, {"epoch": 3}, "lease", log=log)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """退避不真睡（3 次重试的 2s+4s 不进用例墙钟）。"""
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)


def _dead(monkeypatch: pytest.MonkeyPatch, err: BaseException) -> list[int]:
    """把 `_request` 换成「每次都抛 `err`」，返回调用计数。"""
    calls: list[int] = []

    def boom(*_a, **_k):
        calls.append(1)
        raise err

    monkeypatch.setattr(mod, "WIRE", PortPair(boom))
    return calls


def _quiet(_m: str) -> None:
    return None


def test_a_dead_transport_becomes_retryable_after_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """断连（`OSError`）是瞬时失败：查 3 次再抛 `RetryableError`（一致性优先，见 docstring）。"""
    calls = _dead(monkeypatch, OSError("connection refused"))
    with pytest.raises(RetryableError):
        fetch_resume(URL, TOKEN, JID, log=_quiet)
    assert len(calls) == 3, "resume 未知就开训会白扔已有进度 ⇒ 必须重试满三拍"


def test_a_malformed_response_is_a_transient_failure_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ProtocolError` 也在集里（`_request` 对不像话的响应体就抛它）：同样重试后 RetryableError。"""
    calls = _dead(monkeypatch, ProtocolError("epoch body is not json"))
    with pytest.raises(RetryableError):
        fetch_resume(URL, TOKEN, JID, log=_quiet)
    assert len(calls) == 3


def test_our_own_shape_bug_is_not_a_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向判据：改回 `except Exception` ⇒ 这一条红（`AttributeError` 会被吞成重试）。"""
    calls = _dead(monkeypatch, AttributeError("response lost .headers"))
    with pytest.raises(AttributeError):
        fetch_resume(URL, TOKEN, JID, log=_quiet)
    assert len(calls) == 1, "读响应的姿势坏了 ⇒ 不该白重试"


def test_epoch_post_returns_false_on_transport_and_raises_on_our_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_bc_post_epoch` 的同一口径：传输失败 ⇒ `False`（留 backlog）；我们的 bug ⇒ 穿出去。"""
    lines: list[str] = []
    calls = _dead(monkeypatch, OSError("tunnel 530"))
    delivered = post_epoch(lines.append)
    assert delivered is False, "送不到 ⇒ 调用方要把它留在 backlog 里等隧道恢复后重放"
    assert len(calls) == 2, "epoch 腿只重试一拍（60s 超时那种不许多等）"
    assert len(lines) == 1, "失败要恰留一行 WARN（多一行就是刷屏、零行就是静默）"

    calls = _dead(monkeypatch, TypeError("body is not serialisable the way we assumed"))
    with pytest.raises(TypeError):
        post_epoch(lines.append)
    assert len(calls) == 1, "我们的形状 bug ⇒ 不许被记成「回传失败 → 留 backlog」"
