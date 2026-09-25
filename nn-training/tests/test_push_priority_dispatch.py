"""R1-7 / R2-4b：push 腿接入同一张优先级表 + 1 主 + N 备份 + landed 取消。

pull 与 push 的差别只在**谁发起**：优先级表、取消信号、首写裁决**同一套**（§2.9）。
本文件钉三件事：

  ① 主副本走课程内硬序（独占 claim），备份**只在主副本已「别处在做」时**由 hub 显式
     授权派发（`mode="backup"`，无租约）——空闲的卡不再靠「无排序硬抢」；
  ② 每课程上限 1 主 + N 备份（缺省 1）：第三张空闲卡不该再拿到同一份；
  ③ landed（结果入账）⇒ 给同 job 的其它在途副本推取消帧；推不到不算失败。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.push_client as PC
from remote.push_dispatch import PushDispatcher
from remote.worker_server import WorkerServerState
from tests.test_hub_push_dispatch import (
    _hub,
    _publish,
    _pump,
    _quiet,
    _wait_until,
    _workers_with,
)

# `worker_factory` 夹具由 `tests/conftest.py` 提供（无需 import，见那里的说明）。


def _disp(hub, ws, **kw) -> PushDispatcher:
    return PushDispatcher(
        hub,
        ws,
        "sekret",
        poll_sec=0.02,
        timeout_sec=30.0,
        log=_quiet,
        **kw,
    )


def test_primary_then_one_backup_then_cap(tmp_path: Path, worker_factory) -> None:
    """主副本 → 备份副本 → 到上限停（缺省 1 主 + 1 备份）。"""
    w1 = worker_factory(complete=False)
    w2 = worker_factory(complete=False)
    hub = _hub(tmp_path, ["x2"])
    m = _publish(hub, "x2", "j" * 16)
    jid = m["job_id"]
    ws = _workers_with(
        tmp_path, worker_factory, (w1, {"id": "g1"}), (w2, {"id": "g2"})
    )
    disp = _disp(hub, ws)
    try:
        # 主副本：独占 claim（`_claimed` 在持 ⇒ 下一次问询的 priority 不再是 highest）
        #
        # **拍点看派发器自己的同步状态**（`_inflight`），不看异步上传（2026-09-24 修 CPU 满载
        # 下的门禁 flake）：测试是**唯一**打拍的人 ⇒「主副本已派、备份还没派」是个可以停下来
        # 检查的状态。原来等 `received` 计数：满载时上传线程慢，20ms 打拍间隔内没落地就会被打
        # 第二拍 ⇒ 备份先落地 ⇒ `== [jid]` 红。那不是「双主副本」，是把**快慢**当成了**先后**
        # ——「主副本在途后备份立刻获授权」本身是 §2.9 的既定语义（同 `_backup_target`）。
        assert _pump(
            disp, lambda: sum(1 for r in disp._inflight.values() if r["mode"] != "backup") >= 1
        ), "主副本没被派出去"
        assert _wait_until(
            lambda: len(w1.received) + len(w2.received) >= 1
        ), "主副本没送到 worker（等状态，不打拍）"
        assert sorted(w1.received + w2.received) == [jid], "主副本只该推给一台"
        # 备份：同一份活的副本落到另一张空闲卡（hub 显式授权，无租约）
        assert _pump(disp, lambda: len(w1.received) + len(w2.received) >= 2)
        assert sorted(w1.received + w2.received) == [jid, jid], "备份应该是**同一份**活"

        # 上限：第三张卡闲着也不该再拿到同一份
        w3 = worker_factory(complete=False)
        ws.add({"id": "g3", "url": w3.url, "authKey": "sekret"})
        for _ in range(8):
            disp.tick()
        assert w3.received == [], "1 主 + N 备份 的上限被突破"
    finally:
        disp.stop()


def test_landed_cancels_the_other_copy(tmp_path: Path, worker_factory, monkeypatch) -> None:
    """landed（结果入账）⇒ 向**同 job 的其它在途副本**推取消帧，赢家自己不收帧。

    直接构造在途状态而不是靠并发时序：`_cancel_others` 的判据是「同 jid ∧ 不同 worker」，
    让它由两个 rec 的字典决定，测试就与「谁先跑完」无关（那条链路已由上面的主/备份用例
    与 `test_hub_push_dispatch` 覆盖）。
    """
    calls: list[tuple[str, str, str]] = []

    def _fake_cancel(url: str, key: str, jid: str, **kw) -> bool:
        calls.append((url, key, jid))
        return True

    monkeypatch.setattr(PC, "cancel_job", _fake_cancel, raising=True)

    hub = _hub(tmp_path, ["x2"])
    _publish(hub, "x2", "j" * 16)
    ws = _workers_with(tmp_path, worker_factory, (worker_factory(), {"id": "g1"}))
    disp = _disp(hub, ws)
    try:
        jid = "j" * 16
        with disp._lock:
            disp._inflight["primary"] = {
                "job_id": jid, "slot": "primary", "worker": "g1", "url": "u1", "key": "k1",
            }
            disp._inflight["backup"] = {
                "job_id": jid, "slot": "backup", "worker": "g2", "url": "u2", "key": "k2",
            }
            disp._inflight["other-job"] = {
                "job_id": "z" * 16, "slot": "other-job", "worker": "g3", "url": "u3", "key": "k3",
            }
        disp._cancel_others(jid, "g1")
        assert calls == [("u2", "k2", jid)], f"只该向 g2 推、且只推一次：{calls}"
        # 赢家自己不收帧
        assert all(c[0] != "u1" for c in calls)
    finally:
        disp.stop()


def test_backup_result_is_accepted_not_reported_as_failure(tmp_path: Path, worker_factory) -> None:
    """备份副本的回传走 `_backup_authorized` ⇒ 200（不是 403 ⇒ 不是 report_job_failure）。"""
    w1 = worker_factory(complete=False)
    w2 = worker_factory()  # 备份先赢（有结果）
    hub = _hub(tmp_path, ["x2"])
    m = _publish(hub, "x2", "j" * 16)
    jid = m["job_id"]
    ws = _workers_with(
        tmp_path, worker_factory, (w1, {"id": "g1"}), (w2, {"id": "g2"})
    )
    disp = _disp(hub, ws)
    try:
        # 主副本给 g1（独占、无结果），备份给 g2（有结果）——顺序不保证，所以等两份都收下。
        assert _pump(disp, lambda: len(w1.received) + len(w2.received) >= 2, timeout=30.0)
        # 结果最终入账（首写锁定，谁先到都行）
        assert _pump(disp, lambda: hub.get_result(jid) is not None, timeout=30.0)
        assert hub.job_failure(jid) is None, "备份回传不得被报成确定性失败（403 ⇒ fail）"
    finally:
        disp.stop()


# ---------------------------------------------------------------- 取消帧的节点侧

def test_worker_server_cancel_is_idempotent(tmp_path: Path) -> None:
    """`POST /job/{id}/cancel` 的节点侧语义：幂等置 Event；未知 jid 也置位（先到先得）。

    返回值 `known` 描述的是「服务端有没有这份 job」（取消帧常常比 job body 早到/晚到），
    不是「是否已经取消过」——所以本用例分开钉这两件事。
    """
    st = WorkerServerState(tmp_path / "w")
    assert st.is_cancelled("j1") is False
    assert st.request_cancel("j1") is False, "未知 jid：置位但回 known=False"
    assert st.is_cancelled("j1") is True
    assert st.request_cancel("j1") is False, "仍未登记 ⇒ known 还是 False（幂等置位）"
    assert st.is_cancelled("j1") is True
    # 已提交的 job：known=True；两个不同 jid 互不串扰
    st.set_state("j9", "running")  # 已登记（无需 starter 的轻量写法）
    assert st.request_cancel("j9") is True
    assert st.is_cancelled("j2") is False


def test_cancel_job_unreachable_is_false_not_raise(monkeypatch) -> None:
    """取消帧推不到 ⇒ `False`（**不抛**）：让它跑完，409 丢弃（§2.4 的口径）。"""
    def _boom(*a, **k):
        raise OSError("隧道断了")

    monkeypatch.setattr(PC, "_request", _boom, raising=True)
    assert PC.cancel_job("http://x", "t", "j1") is False


def test_cancel_job_200_is_true(monkeypatch) -> None:
    monkeypatch.setattr(PC, "_request", lambda *a, **k: (200, b"{}"), raising=True)
    assert PC.cancel_job("http://x", "t", "j1") is True
