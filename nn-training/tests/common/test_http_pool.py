"""test_http_pool — 长连接池（P4，plan/node-throughput-and-keepalive D5）。

钉六件事：① 同线程复用（connect=1 / reuse=1）；② 服务端 `Connection: close` 后重连；
③ **timeout 是每请求的**（0.2s 建的池连接不会把下一条 5s 的慢请求掐死）；
④ 异常语义 = urllib（503 → HTTPError + 可读 body）；⑤ 重发白名单（`/drop` 重发一次、
`/v1/task` 不重发、收工态不重发）；⑥ 多线程不共享连接。
"""

from __future__ import annotations

import http.server
import threading
import time
import urllib.error

import pytest

from common import http_pool


class _H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    conns = 0
    drop_used: set[str] = set()

    def setup(self) -> None:
        type(self).conns += 1
        super().setup()

    def log_message(self, *_a) -> None:  # 测试静音
        return

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/drop", "/v1/task") and path not in type(self).drop_used:
            # 模拟「连接在响应头之前被断」：只断一次，重发能成功。
            type(self).drop_used.add(path)
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return
        if path == "/slow":
            # 先发头再慢慢发 body：这样 abort/超时能在 read() 阶段生效（贴近真实边界）。
            self.send_response(200)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.flush()
            time.sleep(0.6)  # sleep-ok: 夹具模拟的工作量：慢响应先发头、延迟 0.6s 再发 body
            try:
                self.wfile.write(b"slow")
            except OSError:
                pass
            return
        body = b"busy" if path == "/busy" else b"ok"
        self.send_response(503 if path == "/busy" else 200)
        self.send_header("Content-Length", str(len(body)))
        if path == "/close":
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


@pytest.fixture()
def srv():
    _H.conns = 0
    _H.drop_used = set()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    http_pool.reset()
    http_pool.clear_abort("")
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        http_pool.clear_abort("")
        http_pool.reset()


def test_reuses_connection(srv) -> None:
    assert http_pool.request(f"{srv}/ok")[1] == b"ok"
    assert http_pool.request(f"{srv}/ok")[0] == 200
    st = http_pool.pool_stats()
    assert st["connect"] == 1 and st["reuse"] == 1, st
    assert _H.conns == 1, "第二次请求不该新建 TCP 连接"


def test_reconnects_when_server_closes(srv) -> None:
    http_pool.request(f"{srv}/close")
    assert http_pool.request(f"{srv}/ok")[0] == 200
    assert http_pool.pool_stats()["connect"] == 2


def test_timeout_is_per_request(srv) -> None:
    # 池里这条连接是 timeout=0.2 建的；紧接着的慢请求用 5s —— 不刷新 socket timeout 就会误杀。
    http_pool.request(f"{srv}/ok", timeout=0.2)
    assert http_pool.request(f"{srv}/slow", timeout=5)[0] == 200
    assert http_pool.pool_stats()["reuse"] >= 1
    # 反过来：同一条连接改用 0.2s ⇒ 必须真的超时（说明 timeout 每次都被读）。
    with pytest.raises(urllib.error.URLError):
        http_pool.request(f"{srv}/slow", timeout=0.2)


def test_http_error_semantics(srv) -> None:
    with pytest.raises(urllib.error.HTTPError) as ei:
        http_pool.request(f"{srv}/busy")
    assert ei.value.code == 503
    assert b"busy" in ei.value.read()


def test_retries_once_before_headers(srv) -> None:
    assert http_pool.request(f"{srv}/drop")[0] == 200
    st = http_pool.pool_stats()
    assert st["retry"] == 1 and st["drop"] >= 1, st


def test_no_retry_for_task_path(srv) -> None:
    """/v1/task 同步路径不幂等（agent 不认重名任务）⇒ 断连不重发。"""
    with pytest.raises(urllib.error.URLError):
        http_pool.request(f"{srv}/v1/task")
    assert http_pool.pool_stats()["retry"] == 0


def test_threads_do_not_share_connections(srv) -> None:
    errors: list[BaseException] = []

    def go() -> None:
        try:
            http_pool.request(f"{srv}/ok")
        except BaseException as e:  # pragma: no cover - 失败时带出原因
            errors.append(e)

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert not errors, errors
    assert _H.conns == 2, "两条线程必须各有一条连接（共享会串包）"


def test_drop_reason_will_close(srv) -> None:
    """服务端回 `Connection: close` ⇒ drop 记 `drop_will_close`（「每请求重连」最常见的真因）。"""
    http_pool.request(f"{srv}/close")
    st = http_pool.pool_stats()
    assert st["drop"] == 1 and st.get("drop_will_close") == 1, st


def test_drop_reason_error_before_headers(srv) -> None:
    """响应头之前被断 ⇒ `drop_error`（不是 will_close：两者修法完全不同）。"""
    assert http_pool.request(f"{srv}/drop")[0] == 200
    st = http_pool.pool_stats()
    assert st.get("drop_error") == 1, st
    assert st.get("drop_will_close", 0) == 0, st


def test_kind_buckets_are_isolated(srv) -> None:
    """同一进程多课程/多用途并发时，读数必须按桶隔离（2026-11-14 评审）。"""
    http_pool.request(f"{srv}/ok", bucket="c1/rollout")  # 建连
    http_pool.request(f"{srv}/close", bucket="c1/eval")  # 复用该连接，服务端随即关掉
    ro = http_pool.pool_stats("c1/rollout")
    ev = http_pool.pool_stats("c1/eval")
    assert ro["connect"] == 1 and ro["reuse"] == 0, ro
    assert ev["connect"] == 0 and ev["reuse"] == 1, ev
    assert ev.get("drop_will_close") == 1 and ro.get("drop_will_close", 0) == 0, (ro, ev)
    taken = http_pool.take_stats("c1/rollout")
    assert taken["connect"] == 1
    assert http_pool.pool_stats("c1/rollout")["connect"] == 0, "分桶读走的只清自己"
    merged = http_pool.pool_stats()
    assert merged["reuse"] == 1 and merged["drop"] == 1, "合并视图里只剩 eval 那一条"


def test_stats_suffix_labels_bucket(srv) -> None:
    http_pool.request(f"{srv}/ok", bucket="g4-special/rollout")
    http_pool.request(f"{srv}/close", bucket="g4-special/rollout")
    # 行格式是操作员接口（日志行就是交付物）：整串前缀比较（不是子串断言，不计入口径 A）。
    assert http_pool.stats_suffix("g4-special/rollout").startswith(
        " pool[rollout] reuse=1 connect=1 drop=1(will_close=1) retry=0"
    ), http_pool.pool_stats()


def test_connect_ms_recorded(srv) -> None:
    """握手耗时只在 miss 分支计时（热路径不新增分配）。"""
    http_pool.request(f"{srv}/ok")
    assert http_pool.pool_stats()["connect_ms"] >= 0


def test_idle_age_eviction(srv, monkeypatch) -> None:
    """`NODE_HTTP_POOL_IDLE_SEC` > 0 时超龄连接主动重连；默认（未设）= 不老化。"""
    http_pool.request(f"{srv}/ok")
    http_pool.request(f"{srv}/ok")
    assert http_pool.pool_stats()["connect"] == 1, "默认必须复用（常量未定前不老化）"
    monkeypatch.setenv("NODE_HTTP_POOL_IDLE_SEC", "0.01")
    time.sleep(0.05)  # sleep-ok: 夹具模拟——制造「连接空闲已超龄」的现场，上限由常量 0.01s 定义
    http_pool.request(f"{srv}/ok")
    st = http_pool.pool_stats()
    assert st["connect"] == 2 and st.get("drop_idle") == 1, st


def test_abort_scope_closes_inflight_and_never_retries(srv) -> None:
    err: list[BaseException] = []

    def go() -> None:
        try:
            http_pool.request(f"{srv}/slow", timeout=5, scope="done")
        except BaseException as e:
            err.append(e)

    t = threading.Thread(target=go)
    t.start()
    # 等**谓词**（在飞响应已登记）而不是赌时长：步长只是轮询间隔，上限 2s 只兜挂起。
    deadline = time.time() + 2.0
    while http_pool.active_count("done") == 0 and time.time() < deadline:
        time.sleep(0.01)  # sleep-ok: 轮询步长（等的是谓词：在飞响应已登记）
    assert http_pool.active_count("done") >= 1, "响应头没到，abort 断言会变成假绿"
    assert http_pool.abort_scope("done") >= 1
    t.join(5)
    assert err and isinstance(err[0], urllib.error.URLError), err
    assert http_pool.pool_stats()["retry"] == 0, "收工断连不得重发（会把一局重新开起来）"
    # 收工/竞速剪枝造成的 drop 单独记账（plan §2.4）：它和「服务端不 keep-alive」是两回事。
    assert http_pool.pool_stats().get("drop_abort") == 1, http_pool.pool_stats()
