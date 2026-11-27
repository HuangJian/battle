"""test_job_control_visibility.py — 控制面 POST 失败必须**留痕**（2026-11-14，`python.refactor.md` §P0-7）。

`remote/job_lifecycle.py` 里 `job_started` / `job_ready` / `abandon_job` / `release_job` /
`heartbeat` 五个控制面通知此前都是 `except Exception: pass`。它们在语义上确实「不可达不致命」
—— 租约过期是设计好的兜底 —— 但静默把**兜底正在被触发**这件事藏了起来：

  * `start` 没送到 ⇒ hub 侧的 `computing_at` 不起算，这份 job 的掉队阈值晚起算；
  * `ready` 没送到 ⇒ 其他 worker 的优先级不降（P0 小包的全部作用就是这个）；
  * `abandon` 没送到 ⇒ hub 侧的 `claimed/computing/ready` 可见性一直挂着（优先级永远上不去）；
  * `release` 没送到 ⇒ 这份活要干等 30min 租约过期才回池，别的 worker 领不走；
  * `heartbeat` 连续失败 ⇒ 租约在走向过期，最坏是「活干完了 hub 却派给了别人」。

契约（本文件钉死，与 `tests/test_silent_except_budget.py` 的 G9 门禁互补）：
**不抛、但每条失败恰好留一行带 job id 与异常类型的日志**。判据不看措辞全句 —— 只数「有几行提到了
这个 job」并核对行里带的异常类型，改文案不会红。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.ports import PortPair
from remote import job_lifecycle as jl

JID = "jid-visibility-1"

#: 五个控制面通知：函数名 → (调用, 出错时兜底是否靠租约)。签名各不相同，逐个包一层。
_NOTICES = {
    "job_started": lambda: jl.job_started("http://hub", "tok", JID, worker_id="w1"),
    "job_ready": lambda: jl.job_ready("http://hub", "tok", JID, worker_id="w1"),
    "abandon_job": lambda: jl.abandon_job("http://hub", "tok", JID, worker_id="w1", reason="瞬时"),
    "release_job": lambda: jl.release_job("http://hub", "tok", JID, lease_token="lt"),
    "heartbeat": lambda: jl.heartbeat("http://hub", "tok", JID, lease_token="lt"),
}


@pytest.mark.parametrize("name", sorted(_NOTICES))
def test_control_post_failure_is_logged_and_not_raised(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _unreachable(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise OSError("hub 不可达")

    monkeypatch.setattr(jl, "WIRE", PortPair(_unreachable))

    # ① 不致命：调用方不该被控制面的失败拖死（租约兜底是设计好的语义）。
    _NOTICES[name]()

    # ② 可见：恰好一行提到这个 job，且带得上异常类型（归因不能靠猜）。
    lines = [ln for ln in capsys.readouterr().out.splitlines() if JID in ln]
    assert len(lines) == 1, f"{name}: 失败应当只留一行日志（实际 {lines}）"
    _, _, cause = lines[0].partition("（")
    assert cause.startswith("OSError('hub 不可达')"), lines[0]


def test_heartbeat_failure_says_the_lease_is_still_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """心跳失败与「租约已丢」是两件事：行里必须点明租约还在倒计时，否则操作员会误判。

    这条**只**看「有没有提到 job + 异常」，不看整句措辞；后缀用 `endswith` 对账。
    """

    def _unreachable(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise TimeoutError("timed out")

    monkeypatch.setattr(jl, "WIRE", PortPair(_unreachable))
    jl.heartbeat("http://hub", "tok", JID, lease_token="lt")

    lines = [ln for ln in capsys.readouterr().out.splitlines() if JID in ln]
    assert len(lines) == 1, lines
    assert lines[0].endswith("下一个心跳再试"), lines[0]


@pytest.mark.parametrize("name", sorted(_NOTICES))
def test_programmer_error_is_not_disguised_as_a_network_failure(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """窄捕的**另一半**：程序员自己的错不许被读成「hub 不可达」。

    这五条控制面通知原来一律 `except Exception` —— 于是 `AttributeError`（拼错字段）、
    `TypeError`（签名漂了）、`KeyError`（形状变了）全都会被吞成一行「没送到，租约兜底」，
    现场看到的是网络问题，真相在我们自己的代码里。2026-11-15 改成 `NET_ERRORS`（网络层 +
    那两处防御性 RuntimeError）后，这些错**抛出去**才是它该有的形状。
    """
    import remote.http as http_mod

    def _buggy(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise AttributeError("'NoneType' object has no attribute 'job_id'")

    monkeypatch.setattr(jl, "WIRE", PortPair(_buggy))
    assert issubclass(AttributeError, http_mod.NET_ERRORS) is False  # 口径自检
    with pytest.raises(AttributeError):
        _NOTICES[name]()
