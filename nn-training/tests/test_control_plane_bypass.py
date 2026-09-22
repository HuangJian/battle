"""test_control_plane_bypass.py — 控制面旁路（plan/transfer-scheduling §2.2 / P0，2026-09-22）。

真 HTTP 的现场判据（不是纯函数的再陈述）：**bulk 在途时，控制面往返仍在 1s 内**。

为什么必须有这条：§104 的现场是「payload 下载中，status/priority 读不回」——
调度环开环 ⇒ 取消、问询、halt 全部迟到分钟级。旁路的实现是两件事（`remote/bulk_sched.py`）：

  · 控制面走**独立连接**（`urllib` 每请求新开，无连接池、无共享锁）——本文件用一个
    真 hub 形状的 HTTP 服务把它测成事实（控制请求在 `inflight_bulk==1` 时照样秒回）；
  · bulk 在**分片间隙**给控制面让路（预算 ≤5s）——用例断言让路确实发生且仍受预算约束。

另有一条不变量顺带钉住：**结果回传（P1）占住唯一通道时，预取（P2）必须排队**——
§2.2「bulk 单通道」的现场意义就是「上传结果与下载预取不得同时在途」。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.worker as W
from remote.bulk_sched import BULK_P1_CRITICAL, BULK_P2_PREFETCH

TOKEN = "sekret"
JID = "j" * 16
#: 慢 bulk 的体量与节奏（4 片 × 256KB，每片 40ms ⇒ 约 0.2s 的可观测窗口）。
_BULK_BODY = b"x" * (W.BODY_CHUNK * 4)
_CHUNK_GAP_SEC = 0.04


class _Handler(BaseHTTPRequestHandler):
    """hub 形状的最小服务：慢 bulk（payload）+ 秒回控制面（peek）+ 慢结果上传。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # 别把用例输出刷满
        pass

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.endswith("/payload"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(_BULK_BODY)))
            self.end_headers()
            for i in range(0, len(_BULK_BODY), W.BODY_CHUNK):
                self.wfile.write(_BULK_BODY[i : i + W.BODY_CHUNK])
                self.wfile.flush()
                time.sleep(_CHUNK_GAP_SEC)
            return
        # 控制面：秒回（peek 的形状）
        self._json({"jobs": [], "halt": False})

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path.endswith("/result"):
            time.sleep(0.25)  # 「在传」窗口：让 P2 有时间来排队
        self._json({"ok": True})


@pytest.fixture()
def hub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    W._BULK.reset()
    W._WIRE.clear()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        W._BULK.reset()
        W._WIRE.clear()


def test_control_round_trip_stays_fast_while_bulk_in_flight(hub: str):
    """bulk（P1 下载）在途时，控制面往返仍 ≤1s；且两者**同时**在途（旁路不是排队）。"""
    lat: list[float] = []
    inflight_seen: list[int] = []
    errs: list[BaseException] = []
    bulk_done = threading.Event()
    cap: list[str] = []

    def bulk() -> None:
        try:
            W.download_payload(
                hub,
                TOKEN,
                JID,
                bulk_prio=BULK_P1_CRITICAL,
                log=cap.append,
            )
        except BaseException as e:
            errs.append(e)
        finally:
            bulk_done.set()

    tb = threading.Thread(target=bulk, daemon=True)
    tb.start()

    # 控制面线程：bulk 在途期间反复问询；其中一次**抱着控制面标记**停 0.15s，
    # 逼出真实的让路（bulk 在分片间隙暂停）。
    def control() -> None:
        held = False
        while not bulk_done.is_set():
            t0 = time.time()
            try:
                W.peek_jobs(hub, TOKEN, worker_id="w1", n=2)
            except BaseException as e:
                errs.append(e)
                return
            lat.append(time.time() - t0)
            inflight_seen.append(W._BULK.inflight())
            if not held:
                held = True
                with W._BULK.control(label="/jobs/status"):
                    time.sleep(0.15)  # 控制面「在途」：bulk 该让路
            time.sleep(0.01)

    tc = threading.Thread(target=control, daemon=True)
    tc.start()
    tb.join(15)
    tc.join(15)
    assert not errs, f"传输出错：{errs!r}"
    assert bulk_done.is_set(), "bulk 下载没在窗口内结束"
    assert len(lat) >= 3, f"控制面问询次数太少，测不出结论：{len(lat)}"
    worst = max(lat)
    assert worst < 1.0, f"控制面往返 {worst * 1000:.0f}ms > 1s（旁路失效）"
    assert 1 in inflight_seen, "控制面从未与 bulk 并行——量到的只是「排队后的空链路」"
    assert W._BULK.stats()["yield_count"] >= 1, "bulk 没有为控制面让路"


def test_result_upload_holds_single_channel(hub: str):
    """结果回传占住唯一通道 ⇒ 预取（P2）排队等它；控制面照样秒回。"""
    p2_waiting = threading.Event()
    result_status: list[int] = []
    errs: list[BaseException] = []
    lat: list[float] = []
    p2_done = threading.Event()

    def upload() -> None:
        try:
            result_status.append(W.post_result(hub, TOKEN, JID, {"a": 1}, log=lambda _m: None))
        except BaseException as e:
            errs.append(e)

    def prefetch() -> None:
        # 等结果上传真的开传（`inflight_bulk==1`）再申请通道：这样「排队」是确定的。
        t0 = time.time()
        while W._BULK.inflight() == 0 and time.time() - t0 < 5:
            time.sleep(0.005)
        p2_waiting.set()
        try:
            W.download_payload(
                hub, TOKEN, JID, bulk_prio=BULK_P2_PREFETCH, log=lambda _m: None
            )
        except BaseException as e:
            errs.append(e)
        finally:
            p2_done.set()

    tu = threading.Thread(target=upload, daemon=True)
    tp = threading.Thread(target=prefetch, daemon=True)
    tu.start()
    tp.start()
    assert p2_waiting.wait(5), "结果上传没有进入在途状态"
    # P2 在等通道的这段时间里，控制面必须仍然秒回。
    t0 = time.time()
    while time.time() - t0 < 0.15:
        tq = time.time()
        W.peek_jobs(hub, TOKEN, worker_id="w1", n=1)
        lat.append(time.time() - tq)
    tu.join(15)
    tp.join(15)
    assert not errs, f"传输出错：{errs!r}"
    assert result_status == [200], f"结果回传没有成功：{result_status}"
    assert max(lat) < 1.0, f"结果在上传时控制面被拖慢：{max(lat) * 1000:.0f}ms"
    st = W._BULK.stats()
    assert st["queue_waits"] >= 1, "P2 没有排队（结果上传期间通道被两条 bulk 共用？）"
    assert st["inflight_bulk"] == 0
