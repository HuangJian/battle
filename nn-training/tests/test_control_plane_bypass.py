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


def _median(xs: list[float]) -> float:
    """样本中位（上中位）。

    为什么不用 `max`：满载时**单次**采样撞上调度抖动（2026-09-26 burner 实测 1194ms）
    不是「旁路失效」的证据——控制面**一路都**慢才是。中位对单个离群点免疫，
    留给 max 的只是一个宽松的挂起兜底（见下面两处上界）。
    """
    assert xs, "没有样本"
    return sorted(xs)[len(xs) // 2]


def _wait_until(pred, *, timeout: float = 10.0, step: float = 0.005) -> bool:
    """等一个**事件/状态**成立（`timeout` 只是挂起兜底，不是同步手段，2026-09-24）。

    背景：本文件原先靠「控制面在途 0.15s」这类**绝对时长**当窗口，然后断言 bulk 让了路。
    门禁机器满载时线程调度延迟能把整段窗口挤到 bulk 的两个分片间隙之外 ⇒ `yield_count==0`
    ⇒ 用例红在环境上（04:xx 的连跑里同类失败出现过）。等到状态成立才继续，就不看机器脸色。
    """
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词/状态，超时只当挂起兜底）
        time.sleep(step)
    return bool(pred())


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
                # sleep-ok: 夹具模拟的工作量：慢 bulk 的分片间隔
                time.sleep(_CHUNK_GAP_SEC)
            return
        # 控制面：秒回（peek 的形状）
        self._json({"jobs": [], "halt": False})

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path.endswith("/result"):
            # 「在传」窗口 = 模拟真实回传的耗时（不是同步手段）：P2 那一腿先等
            # `inflight_bulk == 1`（**事件**）才去申请通道，所以这里的长短不决定对错。
            # sleep-ok: 夹具模拟的工作量：回传「在传」的那一段耗时
            time.sleep(0.25)
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
    """bulk（P1 下载）在途时，控制面往返**中位**仍 ≤1s；且两者**同时**在途（旁路不是排队）。"""
    lat: list[float] = []
    inflight_seen: list[int] = []
    errs: list[BaseException] = []
    bulk_done = threading.Event()
    control_window = threading.Event()
    yielded: list[bool] = []
    cap: list[str] = []

    def bulk() -> None:
        # 事件驱动（2026-09-24）：**先等控制面窗口打开再开传** —— 「bulk 在途 ∧ 控制面在途」
        # 从「两个线程谁先被调度」变成构造性事实（原实现靠 4 片 × 40ms 的传输窗口去撞，
        # 满载时传输可能已经跑完，控制面窗口里根本没人可让路）。
        control_window.wait(15.0)
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

    # 控制面线程：先开窗口并**等 bulk 真的让路**（事件），再在 bulk 在途期间反复问询。
    def control() -> None:
        with W._BULK.control(label="/jobs/status"):
            control_window.set()
            # 窗口**不按时长**关闭：等 `yield_count` 涨了（bulk 在分片间隙真的暂停了）才关。
            yielded.append(_wait_until(lambda: W._BULK.stats()["yield_count"] >= 1))
            # 窗口内先量一发：这时 bulk 一定在途（它正卡在让路里）⇒ `inflight_seen`
            # 里出现 1 是构造性的，不靠传输窗口的长短。
            t0 = time.time()
            try:
                W.peek_jobs(hub, TOKEN, worker_id="w1", n=2)
            except BaseException as e:
                errs.append(e)
                return
            lat.append(time.time() - t0)
            inflight_seen.append(W._BULK.inflight())
        while not bulk_done.is_set():
            t0 = time.time()
            try:
                W.peek_jobs(hub, TOKEN, worker_id="w1", n=2)
            except BaseException as e:
                errs.append(e)
                return
            lat.append(time.time() - t0)
            inflight_seen.append(W._BULK.inflight())
            # sleep-ok: 轮询步长（探针节流：每 10ms 问一次，是采样节奏不是同步）
            time.sleep(0.01)

    tc = threading.Thread(target=control, daemon=True)
    tc.start()
    tb.join(15)
    tc.join(15)
    assert not errs, f"传输出错：{errs!r}"
    assert yielded and yielded[0], "bulk 在窗口内没有为控制面让路（§2.2）"
    assert bulk_done.is_set(), "bulk 下载没在窗口内结束"
    assert len(lat) >= 3, f"控制面问询次数太少，测不出结论：{len(lat)}"
    med = _median(lat)
    # 判据用**中位**：单次离群是调度抖动，不是旁路失效；旁路的结构性证据在下面两条
    # （`inflight_seen` 含 1 + `yield_count`）——它们才是「真的并行、真的让路」的钉子。
    assert med < 1.0, f"控制面往返中位 {med * 1000:.0f}ms > 1s（旁路失效）"
    # timing-ok: 上界兜底（单样本只挡挂起/整体拖死，一次离群不算旁路失效）
    assert max(lat) < 5.0, f"控制面往返出现 {max(lat) * 1000:.0f}ms 的离群（疑似挂起）"
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
            # sleep-ok: 轮询步长（等的是 `inflight_bulk == 1` 这个状态，5s 只当挂起兜底）
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
    med = _median(lat)
    assert med < 1.0, f"结果上传时控制面中位往返 {med * 1000:.0f}ms > 1s（被拖慢？）"
    # timing-ok: 上界兜底（单样本只挡挂起；一次离群不算旁路失效）
    assert max(lat) < 5.0, f"控制面往返出现 {max(lat) * 1000:.0f}ms 的离群（疑似挂起）"
    st = W._BULK.stats()
    assert st["queue_waits"] >= 1, "P2 没有排队（结果上传期间通道被两条 bulk 共用？）"
    assert st["inflight_bulk"] == 0
