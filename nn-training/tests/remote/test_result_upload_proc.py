"""tests/remote/test_result_upload_proc.py —— 结果回传的**进程模式**（`plan/ppo-worker-transfer-isolation`）。

为什么有这组用例：PPO worker 的 `out`（结果回传）一直住在训练进程的后台线程里
（`remote/result_upload.py`），`json.dumps`（线上体 ~1.6MB）+ gzip + HTTP 每一步都压在
训练线程的 GIL 上 —— 与自主 worker 补传腿 2026-10-06 那次事故（PPO 单步 0.186 → 5.26 /
7.40 s）是**同一条通道**。自主腿 2026-10-07 已经搬进子进程（`plan/offline-deliver-isolation`），
本文件钉住 PPO worker 这一半：

  ① **真隔离**：`submit()` 只排票，序列化 / 网络都在子进程里；
  ② **绝不丢结果**：子进程起不来 ⇒ sticky 降级到线程，照样送达；半路死掉 ⇒ 父侧补发；
     `close()` 前等干净；
  ③ **记账不撒谎**：子进程交回的 `(bytes, sec, wait)` 在**父侧**重放进每 job 账
     （job 行的 `out=` / `overlap=` 与线程模式同义）。

快层用「假子进程」（可注入 post 行为 / 崩溃点）；真子进程只留一条（1 次 python 启动），
规模刻意压小 —— 慢层 8 次 python 启动会把邻居用例顶红（`test_offline_deliver_proc` 同款教训）。
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.result_upload import (
    OUTCOME_PREFIX,
    RESULT_UPLOAD_MODES,
    ResultUploader,
    UploadTask,
)
from remote.upload_proc import UploaderProcess, make_uploader

JID1 = "a1" * 8
JID2 = "b2" * 8


# ────────────────────────── 假子进程（Popen 兼容面） ──────────────────────────


class _Writer:
    def __init__(self, child: _FakeChild) -> None:
        self._child = child

    def write(self, s: str) -> int:
        self._child._inbox.put(s)
        return len(s)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._child._inbox.put(None)


class _Reader:
    """行队列的只读面（`for line in stream` + `readline`）。空串 = EOF。"""

    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()

    def put(self, s: str) -> None:
        self._q.put(s)

    def readline(self) -> str:
        return self._q.get()

    def __iter__(self):
        while True:
            line = self._q.get()
            if line == "":
                return
            yield line

    def close(self) -> None:
        pass


class _FakeChild:
    """假子进程：stdin 收 JSON 行 → `post(task)` → stdout 写落定行。

    `crash_after=N` = 收到第 N+1 张票时不发落定行直接「崩掉」（模拟 OOM / 被杀）；
    `boot=False` = 立刻退出且不打握手行（模拟「在飞的代码树里没有那个模块」）。
    """

    def __init__(
        self,
        *,
        post=None,
        boot: bool = True,
        crash_after: int | None = None,
        pid: int = 424242,
    ) -> None:
        self.pid = pid
        self.stdin = _Writer(self)
        self.stdout = _Reader()
        self._inbox: queue.Queue[str | None] = queue.Queue()
        self._post = post or (lambda task: {"jid": task["jid"], "status": 200})
        self._crash_after = crash_after
        self._boot = boot
        self._rc: int | None = None
        if boot:
            self.stdout.put(f"[upload] boot ok {pid}\n")
        self._thread = threading.Thread(target=self._run, name=f"fake-upload-{pid}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        if not self._boot:
            # 模拟「模块不在在飞的代码树里」：进程一启动就退（`No module named` 是毫秒级的）
            self._rc = 1
            self.stdout.put("")
            return
        seen = 0
        while True:
            item = self._inbox.get()
            if item is None:
                break
            if self._crash_after is not None and seen >= self._crash_after:
                self._rc = 1
                break
            seen += 1
            rec = self._post(json.loads(item))
            if rec is not None:
                self.stdout.put(OUTCOME_PREFIX + json.dumps(rec) + "\n")
        if self._rc is None:
            self._rc = 0
        self.stdout.put("")

    # ---- Popen 兼容面 ----
    def poll(self) -> int | None:
        return self._rc

    def wait(self, timeout: float | None = None) -> int | None:
        self._thread.join(timeout=timeout)
        return self._rc

    def terminate(self) -> None:
        self._rc = -15
        self._inbox.put(None)

    def kill(self) -> None:
        self._rc = -9


def _task(
    jid: str, *, on_settled=None, result=None, base_url: str = "http://hub.invalid"
) -> UploadTask:
    return UploadTask(
        jid=jid,
        base_url=base_url,
        token="tok",
        result=result or {"job_id": jid},
        on_settled=on_settled,
    )


def _recorder(seen: list[str]):
    """上传桩：记下 jid 并回 200（`append(...) or 200` 会被 mypy 判 func-returns-value）。"""

    def _upload(_base, _token, jid, _result, **_kw) -> int:
        seen.append(jid)
        return 200

    return _upload


def _proc(**kw) -> tuple[UploaderProcess, list[str]]:
    logs: list[str] = []
    upload = kw.pop("upload", lambda *a, **k: 200)
    up = make_uploader(upload=upload, mode="process", log=logs.append, **kw)
    assert isinstance(up, UploaderProcess)
    return up, logs


# ────────────────────────── ① 真隔离（往返 + 不占调用线程） ──────────────────────────


def test_submit_returns_before_the_child_posts() -> None:
    """**核心契约**：`submit()` 立刻返回；POST 在子进程里跑。"""
    started = threading.Event()
    release = threading.Event()
    done: list[str] = []

    def _post(task: dict) -> dict:
        started.set()
        assert release.wait(5.0), "测试自锁：子进程没被放行"
        done.append(task["jid"])
        return {"jid": task["jid"], "status": 200, "seconds": 0.01, "wire": {"segs": {}}}

    up, logs = _proc(spawn=lambda _argv: _FakeChild(post=_post))
    try:
        up.submit(_task(JID1))
        assert started.wait(2.0), f"子进程没拿到票（没有 writer 线程？）：{logs}"
        assert done == [], "此刻子进程**不该**已经发完（否则本用例没测到异步）"
        release.set()
        assert up.drain(timeout=5.0) is True
        assert done == [JID1]
        out = up.outcome(JID1)
        assert out is not None and out.ok
        assert up.stats()["mode"] == "process"
    finally:
        release.set()
        up.close(timeout=5.0)


def test_settled_callback_runs_in_the_parent() -> None:
    """落定回调必须在**父进程**里跑（生产侧靠它把 `out` 的账收在那一刻 + 备份失败体）。"""
    order: list[str] = []
    parent_pid = os.getpid()

    def _post(task: dict) -> dict:
        order.append("child-post")
        return {"jid": task["jid"], "status": 200, "seconds": 0.01, "wire": {"segs": {}}}

    def _settled(jid: str, out: object) -> None:
        same = os.getpid() == parent_pid
        order.append(f"settled:{jid}:{getattr(out, 'status', None)}:{same}")

    up, _logs = _proc(spawn=lambda _argv: _FakeChild(post=_post))
    try:
        up.submit(_task(JID1, on_settled=_settled))
        assert up.close(timeout=5.0) is True
    finally:
        up.close(timeout=1.0)
    assert order[0] == "child-post"
    assert order[1] == f"settled:{JID1}:200:True", f"回调跑错进程了：{order}"


# ────────────────────────── ② 绝不丢结果（降级 + 补发） ──────────────────────────


def test_boot_failure_degrades_to_thread_and_still_delivers() -> None:
    """子进程起不来（在飞的代码树里没有这个模块）⇒ **sticky 降级**，结果照样送达。"""
    delivered: list[str] = []
    up, logs = _proc(upload=_recorder(delivered), spawn=lambda _argv: _FakeChild(boot=False))
    assert up._fallback is not None, "没握手成功就必须降级"
    assert up.mode == "async" and up.stats()["mode"] == "async"
    up.submit(_task(JID1))
    assert up.close(timeout=5.0) is True
    assert delivered == [JID1]
    # 「响亮」的结构化出口：降级理由进 `stats()`（日志面只是同一事实的人读镜像）。
    assert up.stats()["degrade_reason"], f"降级必须可见：{logs}"


def test_child_death_midflight_resends_in_the_parent() -> None:
    """子进程拿到票就崩（OOM/被杀）⇒ 在飞那份**父侧补发**（幂等），绝不丢。"""
    delivered: list[str] = []
    up, logs = _proc(
        upload=_recorder(delivered),
        spawn=lambda _argv: _FakeChild(crash_after=0),
    )
    up.submit(_task(JID1))
    assert up.drain(timeout=5.0) is True, f"在飞的那份没有被补发（pending 卡住）：{logs}"
    assert delivered == [JID1]
    out = up.outcome(JID1)
    assert out is not None and out.ok
    assert up.mode == "async" and up.stats()["degrade_reason"], logs
    up.close(timeout=5.0)


def test_close_waits_for_the_child_to_finish() -> None:
    """收线：`close()` 等子进程把手上那份发完（退出前不丢）。"""
    release = threading.Event()

    def _post(task: dict) -> dict:
        assert release.wait(5.0), "测试自锁"
        return {"jid": task["jid"], "status": 200, "seconds": 0.01, "wire": {"segs": {}}}

    up, _logs = _proc(spawn=lambda _argv: _FakeChild(post=_post))
    up.submit(_task(JID1))
    threading.Timer(0.2, release.set).start()
    assert up.close(timeout=5.0) is True
    out = up.outcome(JID1)
    assert out is not None and out.ok
    st = up.stats()
    assert st["submitted"] == 1 and st["ok"] == 1 and st["pending"] == 0


def test_close_resends_whatever_the_child_never_settled() -> None:
    """子进程在收线时仍没落定 ⇒ 父侧同步补发（而不是「关掉就算了」）。"""
    delivered: list[str] = []
    child = _FakeChild(post=lambda _t: None)  # 收票但一个落定行都不写
    up, logs = _proc(
        upload=_recorder(delivered),
        spawn=lambda _argv: child,
    )
    up.submit(_task(JID1))
    # 立刻收线（子进程还没写完落定行）⇒ 残票同步补发
    up.close(timeout=0.5)
    assert delivered == [JID1], f"残票被丢了：{delivered} {logs}"
    assert up.stats()["sync_fallback"] >= 1, f"补发必须进账：{up.stats()}"


# ────────────────────────── ③ 记账（wire 重放） ──────────────────────────


def test_wire_account_is_replayed_in_the_parent() -> None:
    """子进程交回的 `(bytes, sec, wait, failed)` ⇒ 父侧的每 job 账（job 行 `out=` 的来源）。"""
    got: list[tuple[str, dict]] = []

    def _post(task: dict) -> dict:
        return {
            "jid": task["jid"],
            "status": 200,
            "seconds": 2.5,
            "wire": {
                "segs": {"result": [1024 * 1024, 2.5]},
                "waits": 1.5,
                "failed": {"n": 1, "bytes": 2048, "sec": 0.4},
            },
        }

    up, _logs = _proc(
        spawn=lambda _argv: _FakeChild(post=_post),
        record_wire=lambda jid, wire: got.append((jid, wire)),
    )
    up.submit(_task(JID1))
    assert up.close(timeout=5.0) is True
    assert got and got[0][0] == JID1
    assert got[0][1]["segs"]["result"] == [1024 * 1024, 2.5]
    assert got[0][1]["waits"] == 1.5


def test_worker_wire_replay_matches_the_thread_mode_ledger() -> None:
    """父侧重放与线程模式**同一口径**：`_wire_add` / `_wire_note_wait` 的输入逐字相同。"""
    import remote.worker as W
    from remote.job_lifecycle import record_upload_wire

    W._WIRE.clear()
    record_upload_wire(
        JID2,
        {
            "segs": {"result": [4096, 1.25]},
            "waits": 0.5,
            "failed": {"n": 0, "bytes": 0, "sec": 0.0},
        },
    )
    bucket = W._WIRE.get(JID2) or {}
    assert bucket["segs"]["result"] == [4096, 1.25]
    assert bucket["waits"] == 0.5
    W._WIRE.clear()


# ────────────────────────── ④ 模式域 / 旧行为 ──────────────────────────


def test_default_mode_is_still_async() -> None:
    """**缺省不许变**：云机入口显式开 `process`；库缺省仍是 async（不起子进程）。"""
    up = make_uploader(upload=lambda *a, **k: 200)
    assert isinstance(up, ResultUploader) and up.mode == "async"
    assert set(RESULT_UPLOAD_MODES) == {"process", "async", "sync"}


def test_unknown_mode_is_loud_and_falls_back_to_async() -> None:
    logs: list[str] = []
    up = make_uploader(upload=lambda *a, **k: 200, mode="asink", log=logs.append)
    assert isinstance(up, ResultUploader) and up.mode == "async"
    assert logs, "未知模式必须留痕（不许静默）"


def test_result_uploader_does_not_lie_about_process_mode() -> None:
    """`ResultUploader(mode="process")` = 实现选择没生效 ⇒ 如实报 `async`（不谎报隔离）。"""
    up = ResultUploader(upload=lambda *a, **k: 200, mode="process")
    assert up.mode == "async"


# ────────────────────────── ⑤ 真子进程（慢层，只一条） ──────────────────────────


class _Hub(BaseHTTPRequestHandler):
    seen: list[tuple[str, int]] = []

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        type(self).seen.append((self.path, n))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:  # 静音
        return


@pytest.mark.slow
def test_real_child_process_posts_to_the_hub() -> None:
    """端到端（真子进程 + 真 HTTP）：真正发体的那个进程不是训练进程。"""
    from remote.job_lifecycle import post_result

    _Hub.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    logs: list[str] = []
    up = UploaderProcess(upload=post_result, drain_timeout=30.0, log=logs.append)
    try:
        up.start()
        assert up.stats()["mode"] == "process", f"真子进程没起来：{logs}"
        up.submit(
            _task(
                JID1,
                result={"job_id": JID1, "weights_json": "e30=", "agg": {"steps": 1}},
                base_url=base,
            )
        )
        assert up.close(timeout=30.0) is True, logs
        out = up.outcome(JID1)
        assert out is not None and out.ok, f"{out} {logs}"
    finally:
        up.close(timeout=5.0)
        srv.shutdown()
        srv.server_close()
    assert any(path == f"/jobs/{JID1}/result" for path, _n in _Hub.seen), _Hub.seen
