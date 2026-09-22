"""毒包熔断（plan/accident.plan.md §4.1）+ 认领可观测（§4.3），2026-09-21。

事故形态：C-0 it58 的 payload 被 served **约 40 次**，同一 job_id 每 5 分钟被重领一次、
每次都失败、从不回传——**零告警**，训练侧只有 3×1800s 超时。三方合谋：worker 把失败
当瞬态重认领、hub 没有失败/重领计数、训练侧只有超时一条路。

本文件钉三件事：
  ① 计数与熔断：同一 job「认领后零回传」满 `FREEZE_AFTER_RECLAIMS` 次 ⇒ 冻结 + 移出可领取池；
     **只在租约过期且无结果/无失败标记**时计数（主动 release = worker 说「我能自愈」，
     不算零回传；已结算的 job 不算）；
  ② 冻结是**独立第二状态**：重发（publish 同 job_id）**不清**冻结（失败标记才清），
     解冻只走 `/admin/unfreeze`；两条路各走各的，互不覆盖；
  ③ 可观测：每次 claim 一行（§4.3）、冻结时一行响亮告警（job/课程/认领者/次数）、
     `/status` 报 `frozen`、`/result` 报 410 + `fail_kind=PoisonFrozen` ⇒ 训练侧**立刻**
     带原因停腿，而不是等 25 分钟超时。

计数点在 store（`_collect_expired_locked`）——租约过期这件事有三个观测入口
（claim / lease_worker / claimable 的资格判定），**必须同一个回收入口**：早先只贴在
claim 里的写法会被 `/admin/queue`（控制台每秒在调 `lease_worker`）抢在前面，计数恒 0。
本文件的 `test_admin_queue_observation_path_also_counts` 就是钉这一条的。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_client import wait_job
from remote.hub_server import FREEZE_AFTER_RECLAIMS, _JobStore, make_server
from remote.protocol import (
    AUTH_HEADER,
    CLAIM_TTL_SEC,
    FAIL_NAME,
    PAYLOAD_NAME,
    WORKER_ID_HEADER,
    JobFailedError,
)
from tests.helpers.hub_poll import hub_poll

TOKEN = "sekret"
JID = "j" * 16


def _manifest(jid: str = JID) -> dict:
    return {
        "proto": 1,
        "runId": "test-run",
        "it": 1,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": "// course jsonc\n{}",
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 1,
        "mb": 512,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }


class _Clock:
    """假时钟（禁止睡真 300s）。"""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _store(tmp_path: Path, clock: _Clock) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl", now_fn=clock)


def _publish(store: _JobStore, jid: str = JID, payload: bytes = b"payload-bytes") -> None:
    store.publish(jid, _manifest(jid), payload)
    assert (store._job_dir(jid) / PAYLOAD_NAME).exists()


def _expire(clock: _Clock) -> None:
    """把租约推过期（TTL + 1s）。"""
    clock.t += CLAIM_TTL_SEC + 1


def _zero_return_cycle(store: _JobStore, clock: _Clock, worker: str) -> str | None:
    """一次「认领 → 不回传 → 租约过期」；返回 token（None = 已被冻结拒发）。

    计数的时点要说清楚：一份过期租约是在**下一次有人碰它**时被回收并计数的
    （claim / lease_worker），所以跑完 N 轮循环时计数是 N-1（最后一轮的那份还没被回收）。
    这不是实现细节的巧合，而是熔断语义本身：**“第 N 次没有人回来交活”** 这件事要等到
    下一个来领的人出现（或观测面扫到）才能确认。
    """
    tok = store.claim(JID, worker_id=worker)
    if tok is None:
        return None
    _expire(clock)
    return tok


# ────────────────────────── ① 计数与熔断 ──────────────────────────


def test_reclaim_expiry_counts_up_to_threshold_then_freezes(tmp_path: Path) -> None:
    """前 N-1 次过期照常回池；第 N 次 ⇒ 冻结（移出可领取池 + 记录次数/最后认领者）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    for n in range(1, FREEZE_AFTER_RECLAIMS + 1):
        assert _zero_return_cycle(store, clock, f"w{n}") is not None, f"第 {n} 次仍应可领"
        assert store.reclaims(JID) == n - 1  # 本轮的那份过期还没被回收
        assert store.frozen_info(JID) is None
        assert store.claimable_job_ids() == [JID], "过期即回池（回池本身不是毒包证据）"
    # 第 4 次来领的人回收掉第 3 份过期租约 ⇒ 计数达阈 ⇒ 熔断，且**不给他**
    assert store.claim(JID, worker_id="w-last") is None
    assert store.reclaims(JID) == FREEZE_AFTER_RECLAIMS
    info = store.frozen_info(JID)
    assert info is not None
    assert info["reclaims"] == FREEZE_AFTER_RECLAIMS
    assert info["worker"] == f"w{FREEZE_AFTER_RECLAIMS}"  # 最后一次零回传的认领者
    assert store.claimable_job_ids() == []  # 已冻结：不再回池
    # 任何入口都不再下发（含直接 claim）
    assert store.claim(JID, worker_id="w-any") is None
    clock.t += CLAIM_TTL_SEC * 10
    assert store.claimable_job_ids() == []


def test_voluntary_release_is_not_a_zero_return(tmp_path: Path) -> None:
    """主动 release（worker 说「瞬时失败，我能自愈」）**不**计数——否则诚实重试会被熔断。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    for _ in range(FREEZE_AFTER_RECLAIMS + 2):
        tok = store.claim(JID)
        assert tok
        assert store.release(JID, tok) is True
        assert store.claimable_job_ids() == [JID]
    assert store.reclaims(JID) == 0
    assert store.frozen_info(JID) is None


def test_resolved_job_expiry_is_not_counted(tmp_path: Path) -> None:
    """已结算（结果落盘 / 失败标记在）的 job，租约过期不算零回传。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    assert store.claim(JID)
    assert store.store_result(JID, {"ok": True}) is True
    _expire(clock)
    assert store.reclaims(JID) == 0  # 有结果 ⇒ 不是毒包
    assert store.frozen_info(JID) is None

    jid2 = "k" * 16
    _publish(store, jid2)
    assert store.claim(jid2)
    assert store.store_job_failure(jid2, {"reason": "bun 未安装", "kind": "ProtocolError"}) is True
    _expire(clock)
    assert store.reclaims(jid2) == 0  # 有失败标记（已回传）⇒ 不是毒包


def test_admin_queue_observation_path_also_counts(tmp_path: Path) -> None:
    """`lease_worker`（/admin/queue 每秒在调）也必须走**同一个**回收入口 —— 否则计数被它吞掉。

    历史形态（本次实现时实测）：计数若只贴在 `claim()` 里，控制台的队列轮询先一步把过期
    租约收掉 ⇒ `claim` 再看不到「过期」这件事 ⇒ 计数恒 0 ⇒ 熔断永不触发。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    for n in range(1, FREEZE_AFTER_RECLAIMS + 1):
        assert store.claim(JID, worker_id=f"w{n}")
        _expire(clock)
        assert store.lease_worker(JID) == ""  # 观测路径：回收过期租约
        assert store.reclaims(JID) == n
    assert store.frozen_info(JID) is not None
    assert store.claimable_job_ids() == []
    assert store.claim(JID, worker_id="w-any") is None


def test_freeze_announcement_fires_once(tmp_path: Path) -> None:
    """告警载荷取一次即清：既不会漏喊，也不会每次轮询重喊。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    for n in range(1, FREEZE_AFTER_RECLAIMS + 1):
        assert _zero_return_cycle(store, clock, f"w{n}") is not None
        assert store.consume_freeze_announcement(JID) is None  # 尚未达阈：无告警
    assert store.claim(JID, worker_id="w-last") is None  # 达阈
    got = store.consume_freeze_announcement(JID)
    assert got is not None and got["reclaims"] == FREEZE_AFTER_RECLAIMS
    assert store.consume_freeze_announcement(JID) is None  # 只喊一次


def test_republish_does_not_clear_freeze_and_unfreeze_wakes_it(tmp_path: Path) -> None:
    """重发同 job_id **不清**冻结（失败标记才清）；解冻是唯一可逆口。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    for n in range(1, FREEZE_AFTER_RECLAIMS + 1):
        assert _zero_return_cycle(store, clock, f"w{n}") is not None
    assert store.claim(JID, worker_id="w-last") is None  # 第 N 份过期租约被回收 ⇒ 熔断
    assert store.frozen_info(JID) is not None

    # 重发（同 job_id，哪怕是新字节）——冻结必须在
    _publish(store, payload=b"repacked-bytes")
    assert store.frozen_info(JID) is not None, "重发不得清冻结（否则熔断当场失效）"
    assert store.claimable_job_ids() == []
    assert store.claim(JID) is None

    # 与「重发即重试」**并存**：失败标记（fail.json）确实被重发清掉（清标记的口在训练侧
    # `hub_client.publish_job`，它只 unlink FAIL_NAME），而冻结在同一份字节上活下来。
    assert store.store_job_failure(JID, {"reason": "坏包", "kind": "ProtocolError"}) is True
    assert store.job_failure(JID) is not None
    (store._job_dir(JID) / FAIL_NAME).unlink()  # 模拟客户侧重发前的清标记
    store.publish(JID, _manifest(), b"after-fail-marker-was-cleared")
    assert store.job_failure(JID) is None, "失败标记该被重发清掉（旧语义不动）"
    assert store.frozen_info(JID) is not None, "冻结与失败标记正交：清标记不得顺手解冻"
    assert store.claimable_job_ids() == []

    # 人工解冻：清冻结 + 清计数 ⇒ 立刻回池
    info = store.unfreeze(JID)
    assert info is not None and info["reclaims"] == FREEZE_AFTER_RECLAIMS
    assert store.frozen_info(JID) is None
    assert store.reclaims(JID) == 0
    assert store.claimable_job_ids() == [JID]
    assert store.unfreeze(JID) is None  # 再解一次：没冻可解


# ────────────────────────── ② HTTP 全链路（含 §4.3 日志） ──────────────────────────


def _boot(
    tmp_path: Path, clock: _Clock
) -> tuple[str, _JobStore, ThreadingHTTPServer, threading.Thread]:
    store = _store(tmp_path, clock)
    srv = make_server(store, 0, TOKEN, host="127.0.0.1")
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{port}", store, srv, th


def _http(base: str, path: str, method: str = "GET", worker: str = "") -> tuple[int, dict]:
    headers = {AUTH_HEADER: f"Bearer {TOKEN}"}
    if worker:
        headers[WORKER_ID_HEADER] = worker
    req = urllib.request.Request(base + path, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def _next(base: str, *, worker: str = "") -> dict:
    """旧轮询面的同形替代（peek + claim；实现见 `tests/helpers/hub_poll`）。"""
    got = hub_poll(base, TOKEN, worker_id=worker)
    if got is None or not got.get("job_id"):
        return {"job_id": None, "halt": bool(got and got.get("halt"))}
    return got


def test_frozen_job_tells_training_side_immediately(tmp_path: Path, capsys) -> None:
    """熔断后：/status=frozen、/result=410+PoisonFrozen、`wait_job` 当场带原因抛（不等超时）。"""
    clock = _Clock()
    base, store, srv, th = _boot(tmp_path, clock)
    try:
        _publish(store)
        # 前 N-1 次：正常重领（每次领走都要推过期，模拟 worker 领了不回传）
        for n in range(FREEZE_AFTER_RECLAIMS):
            body = _next(base, worker=f"w{n}")
            assert body["job_id"] == JID, body
            _expire(clock)
        # 第 N 次过期那一刻冻结：已无活可派
        body = _next(base, worker="w-last")
        assert body["job_id"] is None, body

        st, status = _http(base, f"/jobs/{JID}/status")
        assert st == 200 and status["state"] == "frozen", status
        assert status["reclaims"] == FREEZE_AFTER_RECLAIMS
        # 记的是**最后一份零回传租约的持有人**（w0/w1/w2 中的最后一个）
        assert status["last_worker"] == f"w{FREEZE_AFTER_RECLAIMS - 1}", status

        st2, res = _http(base, f"/jobs/{JID}/result")
        assert st2 == 410 and res["fail_kind"] == "PoisonFrozen", res
        assert "熔断冻结" in res["error"]

        # 训练侧：立刻收兵（带原因），而不是等满 25 分钟
        with pytest.raises(JobFailedError) as ei:
            wait_job(base, TOKEN, JID, timeout_sec=25 * 60, poll_sec=0.05, log=lambda _m: None)
        assert ei.value.kind == "PoisonFrozen"
        assert "熔断冻结" in str(ei.value)
    finally:
        srv.shutdown()
        th.join()


def test_admin_unfreeze_endpoint_returns_job_to_pool(tmp_path: Path) -> None:
    """`POST /admin/unfreeze` = 熔断的可逆口；未冻结时 409（“没冻可解”要说出来）。"""
    clock = _Clock()
    base, store, srv, th = _boot(tmp_path, clock)
    try:
        _publish(store)
        for n in range(FREEZE_AFTER_RECLAIMS):
            _next(base, worker=f"w{n}")
            _expire(clock)
        _next(base, worker="w-last")  # 触发冻结
        assert store.frozen_info(JID) is not None

        st, body = _http(base, f"/admin/unfreeze?job_id={JID}", method="POST")
        assert st == 200 and body["unfrozen"] is True, body
        assert store.frozen_info(JID) is None
        got = _next(base, worker="w-new")
        assert got["job_id"] == JID, got  # 解冻即回池

        # 再解一次：409（不是静默 200）
        st2, body2 = _http(base, f"/admin/unfreeze?job_id={JID}", method="POST")
        assert st2 == 409 and body2["unfrozen"] is False, body2
        # 缺 job_id / 未知 job：400 / 404
        assert _http(base, "/admin/unfreeze", method="POST")[0] == 400
        assert _http(base, "/admin/unfreeze?job_id=" + "x" * 16, method="POST")[0] == 404
    finally:
        srv.shutdown()
        th.join()


def test_hub_logs_every_claim_and_the_freeze(tmp_path: Path, capsys) -> None:
    """§4.3：每次 claim 一行（job/课程/worker/次数）；熔断时一行响亮告警。"""
    clock = _Clock()
    base, store, srv, th = _boot(tmp_path, clock)
    try:
        _publish(store)
        capsys.readouterr()  # 清掉启动噪声
        for n in range(FREEZE_AFTER_RECLAIMS):
            _next(base, worker=f"w{n}")
            _expire(clock)
            out = capsys.readouterr().out
            assert f"claim job={JID}" in out, out
            assert f"worker=w{n}" in out, out
            if n == 0:
                assert "reclaims=" not in out, "首次认领不该带计数"
            else:
                assert f"reclaims={n}" in out, out
        _next(base, worker="w-last")  # 触冻
        out2 = capsys.readouterr().out
        assert "熔断冻结" in out2, out2
        assert f"job={JID}" in out2, out2
        assert f"最后一次认领者=w{FREEZE_AFTER_RECLAIMS - 1}" in out2, out2
        assert f"{FREEZE_AFTER_RECLAIMS} 次认领后零回传" in out2, out2
        # 冻结后不再被派发 ⇒ 也不该再有 claim 行（否则日志在骗人）
        out3 = capsys.readouterr().out
        _next(base, worker="w-last")
        assert "claim job=" not in capsys.readouterr().out
        assert out3 == ""
    finally:
        srv.shutdown()
        th.join()
