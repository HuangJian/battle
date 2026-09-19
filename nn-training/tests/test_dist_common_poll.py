"""test_dist_common_poll.py — _poll_result 放弃语义回归（竞速收尾洞修复，2026-09-06）。

场景：fetch_task 的竞速输家副本在 all_settled 置位后必须立即放弃轮询——实测输家副本
在慢节点上跑完注定被丢弃的局，拖住 PPO job 发布 4.5 分钟（trainer 等 slow-node）。
"""

import threading
import time

import pytest

import dist_common


def test_poll_result_abandon_fires_immediately(monkeypatch) -> None:
    """abandon_event 置位后必须立刻抛出放弃异常，不得继续轮询到 budget 耗尽。"""

    def fake_request(url: str, auth_key: str, timeout: float = 30.0, **kw):
        time.sleep(0.4)  # 模拟每次 HTTP 往返
        return 202, b'{"status": "running"}'

    monkeypatch.setattr(dist_common, "_request", fake_request)
    monkeypatch.setattr(dist_common.time, "sleep", lambda _s: None)

    ev = threading.Event()
    threading.Timer(0.9, ev.set).start()
    t0 = time.monotonic()
    with pytest.raises(dist_common.DistError) as ei:
        dist_common._poll_result(
            "http://node",
            "tok",
            {"iterId": "r.1", "stage": 0, "seed": 1},
            budget=600.0,
            poll_s=0.2,
            abandon_event=ev,
        )
    dt = time.monotonic() - t0
    assert "abandoned" in str(ei.value)
    assert dt < 5.0, f"放弃应在 ~1s 内发生，实际 {dt:.1f}s（budget=600 远未耗尽）"


def test_poll_result_no_abandon_keeps_polling(monkeypatch) -> None:
    """未置位时行为不变：持续轮询直到 budget 耗尽抛 deadline exceeded。"""

    def fake_request(url: str, auth_key: str, timeout: float = 30.0, **kw):
        return 202, b'{"status": "running"}'

    monkeypatch.setattr(dist_common, "_request", fake_request)
    monkeypatch.setattr(dist_common.time, "sleep", lambda _s: None)

    with pytest.raises(dist_common.DistError) as ei:
        dist_common._poll_result(
            "http://node",
            "tok",
            {"iterId": "r.1", "stage": 0, "seed": 1},
            budget=1.0,
            poll_s=0.2,
        )
    assert "deadline exceeded" in str(ei.value)


def _fetch(url: str = "http://node") -> dict:
    m, _ = dist_common.fetch_task(
        url,
        "tok",
        iter_id="r.1",
        wver="w",
        stage=2000,
        seed=1,
        max_ticks=100,
        difficulty="hard",
        timeout=1.0,
        mode="eval",
    )
    return m


def test_fetch_task_marks_transport_reset_transient(monkeypatch) -> None:
    """连接被重置（WinError 10054）必须标 transient —— 调度侧据此背压而非熔断节点。"""

    def boom(*a, **k):
        raise ConnectionResetError(10054, "An existing connection was forcibly closed")

    monkeypatch.setattr(dist_common, "_request", boom)
    with pytest.raises(dist_common.DistError) as ei:
        _fetch()
    assert ei.value.transient is True
    assert "task fetch failed" in ei.value.reason


def test_fetch_task_marks_5xx_and_429_transient_but_not_4xx(monkeypatch) -> None:
    """5xx/429 = 节点忙/抖动（transient）；409/404 = 语义错误（不重试背压）。"""
    import urllib.error

    def http(code: int):
        def boom(url, auth_key, timeout=30.0, **kw):
            raise urllib.error.HTTPError(url, code, "err", None, None)  # type: ignore[arg-type]

        return boom

    for code in (503, 429, 502):
        monkeypatch.setattr(dist_common, "_request", http(code))
        with pytest.raises(dist_common.DistError) as ei:
            _fetch()
        assert ei.value.status == code and ei.value.transient is True, code
    for code in (409, 404):
        monkeypatch.setattr(dist_common, "_request", http(code))
        with pytest.raises(dist_common.DistError) as ei:
            _fetch()
        assert ei.value.status == code and ei.value.transient is False, code


def test_transient_judgement_single_source_delegated() -> None:
    """背压/瞬断判据：单一实现在 dist_common，B 层薄转发（A/C 层直调同一实现）。"""
    from rl.batch_eval import is_transient_error as be_is_transient

    assert be_is_transient(dist_common.DistError(0, "busy")) is True
    assert be_is_transient(ConnectionResetError(10054, "x")) is True
    assert be_is_transient(dist_common.DistError(409, "wver not cached")) is False
    assert be_is_transient(dist_common.DistError(0, "validate: wver mismatch")) is False
    for probe in (
        dist_common.DistError(0, "busy"),
        dist_common.DistError(502, ""),
        dist_common.DistError(409, "x"),
        TimeoutError("read timed out"),
    ):
        assert be_is_transient(probe) == dist_common.is_transient_error(probe), probe


def test_transient_classification_table() -> None:
    """分类表：隧道/背压/瞬断 = True；409 wver-not-cached 与确定性失败 = False。"""
    it = dist_common.is_transient_error
    for code in (408, 425, 429, 500, 502, 503, 504):
        assert it(dist_common.DistError(code, "")) is True, code
    for code in (400, 404, 409, 422):
        assert it(dist_common.DistError(code, "")) is False, code
    assert it(dist_common.DistError(0, "busy")) is True
    assert it(dist_common.DistError(0, "node busy: slot saturated")) is True
    assert it(ConnectionResetError(10054, "forcibly closed")) is True
    assert it(dist_common.DistError(0, "validate: wver mismatch")) is False
    assert it(ValueError("corrupt container")) is False


def test_refresh_weights_reposts_and_forgets_cache(monkeypatch) -> None:
    """409 自愈：清 reuse 缓存 → 就地重发 → 重新入账（同一节点继续用）。"""
    dist_common.weights_push_cache_reset()
    node = {"id": "a97", "url": "http://node-a97", "key": "k"}
    seen: list[tuple] = []

    def ok_post(url, auth_key, iter_id, sha, weights_bytes, timeout=120.0, kind="rollout"):
        seen.append((url, auth_key, iter_id, sha, kind))
        return "purged"

    monkeypatch.setattr(dist_common, "post_weights", ok_post)
    dist_common.note_weights_pushed("w1", "a97")  # 脏缓存：客户端以为节点还持有
    assert dist_common.weights_already_pushed("w1", "a97") is True

    logs: list[str] = []
    assert (
        dist_common.refresh_weights(
            node,
            iter_id="it1",
            wver="w1",
            weights_bytes=b"{}",
            kind="rollout",
            log=logs.append,
        )
        is True
    )
    assert seen == [("http://node-a97", "k", "it1", "w1", "rollout")]
    assert dist_common.weights_already_pushed("w1", "a97") is True  # 重发成功 ⇒ 重新入账
    assert logs and "wver not cached" in logs[0]


def test_refresh_weights_failure_stays_cold(monkeypatch) -> None:
    """重发也失败 ⇒ 返回 False（调用方按真失败处理）且缓存必须保持清空。"""
    dist_common.weights_push_cache_reset()
    node = {"id": "a97", "url": "http://node-a97", "authKey": "k2"}  # authKey 兼容键
    seen: list[str] = []

    def boom(url, auth_key, *_a, **_kw):
        seen.append(auth_key)
        raise dist_common.DistError(503, "busy")

    monkeypatch.setattr(dist_common, "post_weights", boom)
    dist_common.note_weights_pushed("w1", "a97")
    logs: list[str] = []
    assert (
        dist_common.refresh_weights(
            node, iter_id="it1", wver="w1", weights_bytes=b"{}", log=logs.append
        )
        is False
    )
    assert seen == ["k2"]
    assert dist_common.weights_already_pushed("w1", "a97") is False  # 下次必须重新握手
    assert logs and "重发失败" in logs[0]


def test_transient_judgement_defined_once_and_wired() -> None:
    """源码级守卫：判据只有一份实现，A/C 层真的接线（409 自愈也不得各写一套）。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    definers = []
    for path in [root / "dist_common.py", *sorted((root / "rl").glob("*.py"))]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "is_transient_error":
                definers.append(path.name)
    # 只有 dist_common 是真实实现；batch_eval 允许同名但必须是纯转发。
    assert set(definers) <= {"dist_common.py", "batch_eval.py"}, definers
    assert "dist_common.py" in definers and len(definers) == 2, definers
    b_src = (root / "rl" / "batch_eval.py").read_text(encoding="utf-8")
    assert "return dist_common.is_transient_error(e)" in b_src
    assert "TRANSIENT_HTTP_STATUS" not in b_src  # 判据逻辑不得复制回 B 层
    assert "_BUSY_HINT" not in b_src

    a_layer = (root / "rl" / "dispatch.py").read_text(encoding="utf-8")
    c_layer = (root / "rl" / "eval_dispatch.py").read_text(encoding="utf-8")
    for src in (a_layer, c_layer):
        assert "dist_common.is_transient_error(" in src
        assert "dist_common.refresh_weights(" in src
        assert "409" in src  # 409 是可刷新条件，必须显式分支
    b_layer = (root / "rl" / "batch_eval.py").read_text(encoding="utf-8")
    assert "return dist_common.is_transient_error(e)" in b_layer


def test_trace_enabled_env_contract(monkeypatch) -> None:
    """事件级追踪开关：缺省由调用方定，`EVAL_TRACE_EVENTS` 可强制开/关。"""
    monkeypatch.delenv("EVAL_TRACE_EVENTS", raising=False)
    assert dist_common.trace_enabled() is False
    assert dist_common.trace_enabled(default=True) is True
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("EVAL_TRACE_EVENTS", truthy)
        assert dist_common.trace_enabled(default=False) is True
    for falsy in ("0", "false", "off", ""):
        monkeypatch.setenv("EVAL_TRACE_EVENTS", falsy)
        assert dist_common.trace_enabled(default=True) is False


def test_post_weights_parallel_logs_per_node_and_ready(monkeypatch) -> None:
    """权重阶段必须留下可判读的事件：逐节点耗时 + 「ready on N/M … in Xs」总计。"""
    lines: list[str] = []
    monkeypatch.setattr(dist_common, "post_weights", lambda *a, **kw: "kept")

    nd = {"id": "n1", "url": "http://n1"}
    ok = dist_common.post_weights_parallel(
        [nd], "it1", "ab" * 32, b"{}", timeout=5.0, kind="eval", log=lines.append
    )

    assert ok == [nd]
    assert any("weights[eval] -> n1 (kept," in line for line in lines), lines
    assert any("ready on 1/1 nodes" in line and "sha abababababab" in line for line in lines), lines

def test_abort_active_requests_never_blocks_and_gates_new_requests() -> None:
    """收工断连必须「立即！马上！」：close() 可能被响应体的读线程持锁阻塞。

    实测（2026-09-19 800 局探针）：主线程在 `abort_active_requests` 里卡了 **81 秒**
    —— 该批 181s 就跑完 800 局，收工白占 32% 墙钟。契约：置位 + 交 daemon 线程关连接，
    本函数只登记与计数，绝不等待；且置位后同作用域的新请求直接抛（transient）。
    """

    class SlowResp:
        def close(self) -> None:
            time.sleep(5.0)  # 模拟「等读线程让出内部锁」

    dist_common.set_request_tag("t-abort")
    key = ("t-abort", SlowResp())
    with dist_common._ACTIVE_LOCK:
        dist_common._ACTIVE.add(key)
    try:
        t0 = time.monotonic()
        n = dist_common.abort_active_requests("t-abort")
        dt = time.monotonic() - t0
        assert n == 1, n
        assert dt < 0.5, f"abort 阻塞了 {dt:.2f}s（收工路径不许等 close）"
        assert dist_common.abort_scope("t-abort") is True
        with pytest.raises(dist_common.DistError) as ei:
            dist_common._request("http://127.0.0.1:9/never", "", 1.0)
        assert ei.value.transient is True, "收工态拒绝必须按瞬断分类（调用方丢弃/背压）"
        # 别的作用域不受影响（rollout 与 eval 同进程并发，绝不能误伤）
        assert dist_common.abort_scope("rollout") is False
    finally:
        with dist_common._ACTIVE_LOCK:
            dist_common._ACTIVE.discard(key)
        dist_common.clear_abort()
        dist_common.set_request_tag("")
        assert dist_common.abort_scope("t-abort") is False  # clear_abort 复位（下一单元可用）
