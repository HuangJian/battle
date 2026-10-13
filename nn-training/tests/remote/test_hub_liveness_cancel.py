"""M1/M3 的 hub 面原语（plan/ppo-job-lifecycle-ownership §2/§4）：

* `remote/hub_http.py::job_liveness` —— 「这个 jid 在 hub 侧还在不在」的**只读**判据，
  看门狗唯一的眼睛。分类表写死三条（评审 F1 已核过：未知 jid 的 `/status` 现成回 404）：
  404 = unknown（起表）· 200 = alive（清表）· 其它/异常 = unreachable（**不动表**）。
* `unfreeze_job` —— 撤单重发必须顺带解冻（冻结只住 hub 内存，训练侧清不掉文件）。
* `admin_cancel_job` —— 四态（done/inflight/unknown/unreachable）；**409 = 在飞 ⇒ 不删目录**
  （2026-10-03 用户裁决「不管在算的，只管没领的」）。

为什么这三条要逐档钉：它们全是 **best-effort**（永不抛），而「best-effort」最容易被误读成
「随便返回什么都可以」——判错一档的代价是判 lost 误重发（一整轮重算）或该撤不撤（挂死）。
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.hub_http as http_mod

JID = "j" * 16


def _routes(
    monkeypatch, table: dict[str, tuple[int, bytes] | BaseException]
) -> list[tuple[str, str, float]]:
    """把 `_request` 换成按**路径**分档的假件；返回调用记录 `[(path, method, timeout)]`。

    `table[path]` = `(status, body)` 或异常实例（抛）。
    """
    seen: list[tuple[str, str, float]] = []

    def fake(
        base_url: str,
        token: str,
        path: str,
        timeout: float = 30.0,
        data: bytes | None = None,
        method: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        seen.append((path, method or "", float(timeout)))
        r = table[path]
        if isinstance(r, BaseException):
            raise r
        return r

    monkeypatch.setattr(http_mod, "_request", fake)
    return seen


def test_job_liveness_classification_is_exactly_three_states(monkeypatch) -> None:
    """★ 分类表：404 = unknown；200 = alive；5xx / 其它码 / 网络异常 = unreachable。"""
    seen = _routes(monkeypatch, {f"/jobs/{JID}/status": (404, b'{"error":"unknown job"}')})
    assert http_mod.job_liveness("http://hub.invalid", "t", JID) == http_mod.JOB_UNKNOWN
    assert seen == [(f"/jobs/{JID}/status", "", http_mod.PROBE_TIMEOUT_SEC)]

    for body in (b'{"state":"pending"}', b'{"state":"leased"}', b'{"state":"frozen"}'):
        _routes(monkeypatch, {f"/jobs/{JID}/status": (200, body)})
        assert http_mod.job_liveness("http://hub.invalid", "t", JID) == http_mod.JOB_ALIVE

    _routes(monkeypatch, {f"/jobs/{JID}/status": (503, b"nope")})
    assert http_mod.job_liveness("http://hub.invalid", "t", JID) == http_mod.JOB_UNREACHABLE
    _routes(monkeypatch, {f"/jobs/{JID}/status": OSError("tunnel down")})
    assert http_mod.job_liveness("http://hub.invalid", "t", JID) == http_mod.JOB_UNREACHABLE
    # 401（token 错）也不是「它没了」——把鉴权配错当 job 丢失 = 白烧一次完整重算。
    _routes(monkeypatch, {f"/jobs/{JID}/status": (401, b"bad token")})
    assert http_mod.job_liveness("http://hub.invalid", "t", JID) == http_mod.JOB_UNREACHABLE


def test_unfreeze_is_best_effort_and_never_raises(monkeypatch) -> None:
    """解冻：200 ⇒ True；404/409（未知/未冻结）⇒ False 且**不是错误面**；异常 ⇒ False。"""
    seen = _routes(monkeypatch, {f"/admin/unfreeze?job_id={JID}": (200, b"{}")})
    assert http_mod.unfreeze_job("http://hub.invalid", "t", JID, log=lambda _m: None) is True
    assert seen[0][1] == "POST"

    for status in (404, 409):
        _routes(monkeypatch, {f"/admin/unfreeze?job_id={JID}": (status, b"{}")})
        assert http_mod.unfreeze_job("http://hub.invalid", "t", JID, log=lambda _m: None) is False

    _routes(monkeypatch, {f"/admin/unfreeze?job_id={JID}": RuntimeError("boom")})
    assert http_mod.unfreeze_job("http://hub.invalid", "t", JID, log=lambda _m: None) is False
    # 空 base/jid：不发请求（调用方常在没有 hub 的路径上顺手调它）。
    sent: list[str] = []

    def _count(*_a: object, **_k: object) -> tuple[int, bytes]:
        sent.append("x")
        return 200, b"{}"

    monkeypatch.setattr(http_mod, "_request", _count)
    assert http_mod.unfreeze_job("", "t", JID) is False
    assert http_mod.unfreeze_job("http://hub.invalid", "t", "") is False
    assert sent == []


def test_admin_cancel_job_four_states(monkeypatch) -> None:
    """★ 四态：200 done / 409 **在飞**（调用方不得删目录）/ 404 unknown / 其它 ⇒ unreachable。"""
    logs: list[str] = []
    cases = [
        (200, http_mod.ADMIN_CANCEL_DONE),
        (409, http_mod.ADMIN_CANCEL_INFLIGHT),
        (404, http_mod.ADMIN_CANCEL_UNKNOWN),
        (500, http_mod.ADMIN_CANCEL_UNREACHABLE),
        (302, http_mod.ADMIN_CANCEL_UNREACHABLE),
    ]
    for status, want in cases:
        seen = _routes(monkeypatch, {f"/admin/cancel-job?job_id={JID}": (status, b"{}")})
        got = http_mod.admin_cancel_job("http://hub.invalid", "t", JID, log=logs.append)
        assert got == want, f"HTTP {status} 应判 {want}，实得 {got}"
        assert seen[0][1] == "POST"
    _routes(monkeypatch, {f"/admin/cancel-job?job_id={JID}": OSError("down")})
    assert (
        http_mod.admin_cancel_job("http://hub.invalid", "t", JID, log=logs.append)
        == http_mod.ADMIN_CANCEL_UNREACHABLE
    )
    assert http_mod.admin_cancel_job("", "t", JID) == http_mod.ADMIN_CANCEL_UNREACHABLE


def test_job_id_is_url_quoted(monkeypatch) -> None:
    """jid 进查询串必须转义（它在 hub 的 URL 里；本侧只负责不发坏 URL）。"""
    jid = "a b/../c"  # 形状不受信任：契约在 hub 侧
    seen = _routes(
        monkeypatch,
        {f"/admin/cancel-job?job_id={urllib.parse.quote(jid)}": (200, b"{}")},
    )
    assert http_mod.admin_cancel_job("http://hub.invalid", "t", jid) == http_mod.ADMIN_CANCEL_DONE
    sent = seen[0][0].rsplit("job_id=", 1)[1]
    assert urllib.parse.unquote(sent) == jid  # 转义是可逆的（hub 收到的那串解回来还是原 jid）
    assert jid not in sent  # 未转义的字面串没有漏进去（空格/斜杠都已被转义）


def test_wait_report_sec_is_the_single_beat() -> None:
    """节拍常量只有一份（300 → 120 的理由见常量注释）：细粒度路径与 `wait_job` 共用。"""
    assert http_mod.WAIT_REPORT_SEC == 120.0
