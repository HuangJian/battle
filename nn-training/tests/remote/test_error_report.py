"""test_error_report.py — 云机的错误上报腿（plan/component-error-registry §4.3-S3b，2026-10-16）。

云机的两类失败**只在云机**：pull 的机器级停滞 / 瞬时失败（`remote/job_round.py` 三个 except）
与离线段的失败（`remote/run_loop.py` 的 `[run] 失败`，段末摘要只报 `state`）。以前它们连一行
都回不到本机，控制台的错误登记表因此看不见真因（「只有翻日志才知道原因」）。本文件钉住
`remote/error_report.py` 这一条腿的**全部硬约束**（G9：任何一条破了都是「观测腿干扰训练」）：

  ① **形状**：`error_payload` 的键集与 hub 读面 / 控制台投影同源（`it` 缺席 = 不可知，不编 0；
     `severity` 只发两档；`reason`/`detail` 长度封顶）；
  ② **送达**：真打到 `POST /admin/worker-error`（路径常量来自 `common/protocol.py`，与 hub 侧
     分派**同一枚**），token 走 `Authorization`；**端到端**再用真 hub 验一次（防「两边各写各的
     路径字面量」这类静默 404）；
  ③ **失败即丢**：5xx / 连不上 / 没地址没 token ⇒ 返回 False 或静默丢弃，**绝不抛**、不重试；
  ④ **不阻塞**：`report_error` 立即返回（发送在 daemon 线程）——hub 卡住时 pull 轮询间隔与
     `state.kick()` 的时序零影响；这一条是 G9 的核心，用「慢 hub」量出来；
  ⑤ **接线**：四个上报点各自带着现场（traceback）与归属（课程 / job / it / worker_id），
     而**设计内生命周期**（取消 `JobCancelledError` / 热替换 `CodeChangedError` / 已走 E2 的
     `ProtocolError`）**一个都不报**（F15：往列表里灌噪音 = 没人再看这个列表）。

墙钟判据只有一处上界（「立即返回」是契约），已按 `tests/test_no_sleep_as_sync.py` 的口径标注。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.run_loop as run_loop_mod
from common.protocol import (
    ADMIN_WORKER_ERROR_PATH,
    AUTH_HEADER,
    CodeChangedError,
    JobCancelledError,
    ProtocolError,
    RetryableError,
    UnreapableChildError,
)
from remote import job_round as JR
from remote import worker as W
from remote.error_report import (
    WORKER_ERROR_DETAIL_MAX,
    WORKER_ERROR_REASON_MAX,
    default_worker_id,
    error_payload,
    post_error,
    report_error,
)
from remote.job_lifecycle import worker_tag
from tests.remote.test_worker_reap_stall import (  # type: ignore
    _job,
    _patch_hub,
    _run_loop,
)

TOKEN = "tok-err"
REASON = "RetryableError: 传输损坏"


# ────────────────────────── 假 hub（stdlib http.server）──────────────────────────


class _Handler(BaseHTTPRequestHandler):
    """把每次 POST 的 (path, auth, body) 记进 server.calls；状态码与延迟由 server 决定。"""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # http.server 的命名约定
        srv = self.server
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        srv.calls.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "auth": self.headers.get(AUTH_HEADER),
                "body": body,
                "at": time.time(),
            }
        )
        if srv.delay:  # type: ignore[attr-defined]
            time.sleep(srv.delay)  # type: ignore[attr-defined]  # sleep-ok: 夹具模拟（慢 hub 的一次往返）
        payload = b'{"ok":true}'
        self.send_response(srv.status)  # type: ignore[attr-defined]
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a: object) -> None:  # 静音（本文件一口气起好几个）
        pass


class _FakeHub:
    """起一个最小 HTTP hub，`with` 退出时收摊。`base` = `http://127.0.0.1:<随机端口>`。"""

    def __init__(self, *, status: int = 200, delay: float = 0.0) -> None:
        self.calls: list[dict] = []
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._srv.status = status  # type: ignore[attr-defined]
        self._srv.delay = delay  # type: ignore[attr-defined]
        self._srv.calls = self.calls  # type: ignore[attr-defined]
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self) -> _FakeHub:
        self._th.start()
        return self

    def __exit__(self, *_a: object) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._th.join()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}"


def _dead_port() -> int:
    """占一个端口再放掉 ⇒ 这个端口上必然没人监听（= 云机上 hub 不可达那一档）。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = int(srv.server_address[1])
    srv.server_close()
    return port


def _wait_for(pred: Any, timeout: float = 5.0) -> bool:
    """有界等一个谓词（后台线程的送达）。判据是谓词，超时只兜挂起。"""
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            return False
        # sleep-ok: 轮询步长（等的是谓词：上报线程送达 / 落进 ring）
        time.sleep(0.02)
    return True


# ────────────────────────── ① 上报体形状 ──────────────────────────


def test_payload_keys_match_the_console_projection() -> None:
    """键集逐项钉死：`it` 缺席 = 不可知（不编 0）；`severity` 只发两档。"""
    p = error_payload(worker="kaggle-c:1", reason=REASON)
    assert set(p) == {
        "worker",
        "component",
        "course",
        "job_id",
        "severity",
        "reason",
        "detail",
        "ts",
    }
    assert p["worker"] == "kaggle-c:1" and p["reason"] == REASON
    assert p["component"] == "ppo-worker"  # 缺省 = pull 那条腿
    assert (p["severity"], p["course"], p["job_id"], p["detail"]) == ("warn", "", "", "")
    assert isinstance(p["ts"], float)

    full = error_payload(
        worker="w",
        reason="boom",
        component="offline-worker",
        detail="Traceback …",
        course="c1-gae",
        job_id="j" * 16,
        it=7,
        severity="err",
    )
    assert (full["it"], full["component"], full["severity"]) == (7, "offline-worker", "err")
    # `it=None`（显式）= 与缺省同：键不出现
    assert set(error_payload(worker="w", reason="boom", it=None)) == set(p)


def test_payload_coerces_severity_and_caps_lengths() -> None:
    """云机可能塞别的词 ⇒ 只认 `err`，其余一律 `warn`；长度封顶（hub 侧同量，三处对齐）。"""
    p = error_payload(worker="w", reason="r", severity="CRITICAL")
    assert p["severity"] == "warn"

    long_p = error_payload(worker="w", reason="x" * 9999, detail="y" * 9999)
    assert len(long_p["reason"]) == WORKER_ERROR_REASON_MAX
    assert len(long_p["detail"]) == WORKER_ERROR_DETAIL_MAX


def test_default_worker_id_names_the_machine_and_the_process() -> None:
    """离线腿没有 worker 注册表里的 id ⇒ `hostname:pid`（现场必须能回答「哪台机器报的」）。"""
    wid = default_worker_id()
    assert wid == f"{socket.gethostname()}:{os.getpid()}"
    assert default_worker_id() == wid  # 稳定（hub 的登记表靠它去重）


# ────────────────────────── ② 送达（假 hub）──────────────────────────


def test_post_error_reaches_the_endpoint_the_hub_dispatches() -> None:
    """真发一次：路径 = 常量、token 走 `Authorization`、体逐字段到齐 ⇒ 返回 True。"""
    with _FakeHub() as hub:
        payload = error_payload(worker="w1", reason=REASON, job_id="j" * 16, it=3)
        assert post_error(hub.base, TOKEN, payload) is True

    assert len(hub.calls) == 1
    call = hub.calls[0]
    assert call["path"] == ADMIN_WORKER_ERROR_PATH
    assert call["auth"] == f"Bearer {TOKEN}"
    assert call["body"] == payload


def test_post_error_swallows_5xx_and_unreachable_hubs() -> None:
    """失败即丢：5xx / 连不上 / 没地址 / 没 token 一律 False，**绝不抛**（观测面坏掉不拖垮训练）。"""
    with _FakeHub(status=500) as hub:
        assert post_error(hub.base, TOKEN, error_payload(worker="w", reason=REASON)) is False
        assert len(hub.calls) == 1  # 请求真的发出去了，只是被拒

    dead = f"http://127.0.0.1:{_dead_port()}"
    assert post_error(dead, TOKEN, error_payload(worker="w", reason=REASON)) is False
    assert post_error("", TOKEN, error_payload(worker="w", reason=REASON)) is False
    assert post_error("http://127.0.0.1:1", "", error_payload(worker="w", reason=REASON)) is False


def test_report_error_lands_in_a_real_hub_ring(tmp_path: Path) -> None:
    """★ 端到端：真 hub（真分派链）收下上报 ⇒ 读面 `worker_errors` 里有它。

    这一条防的是「上报方与 hub 各写各的路径字面量」：两边只在自己的用例里自洽时，
    线上表现为静默 404（观测腿**失败即丢**，所以连报错都不会有）。
    """
    from hub.server import _HubQueue, make_server

    hubq = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hubq, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        report_error(
            f"http://127.0.0.1:{srv.server_address[1]}",
            TOKEN,
            reason=REASON,
            detail="Traceback …",
            component="offline-worker",
            course="c1-gae",
            severity="err",
            worker_id="kaggle-c:1",
        )
        assert _wait_for(lambda: hubq.worker_errors(0.0)["errors"]), "上报必须落进 hub 的 ring"
    finally:
        srv.shutdown()
        srv.server_close()

    rec = hubq.worker_errors(0.0)["errors"][0]
    assert rec["reason"] == REASON
    assert (rec["worker"], rec["component"], rec["course"], rec["severity"]) == (
        "kaggle-c:1",
        "offline-worker",
        "c1-gae",
        "err",
    )


# ────────────────────────── ③ 不阻塞 / 失败留痕 ──────────────────────────


def test_report_error_returns_immediately_while_the_hub_hangs() -> None:
    """★ G9 核心：hub 卡住 1s，`report_error` 也必须**立刻**返回（发送在 daemon 线程里）。

    破了这条就是把观测面接进了 pull 轮询的关键路径（`state.kick()` 的时序会被网络抖动拖走）。
    """
    with _FakeHub(delay=1.0) as hub:
        t0 = time.time()
        report_error(hub.base, TOKEN, reason=REASON, detail="Traceback …")
        elapsed = time.time() - t0
        # timing-ok: 契约上界（「立即返回」就是契约本身；1s 的慢 hub 只挡同步阻塞）
        assert elapsed < 0.25, f"上报不许占调用方的关键路径：{elapsed:.3f}s"
        assert hub.calls == [], "此刻不该已经送达（发送在线程里，1s 才回）"
        # 后台那一发确实发出去了（不是「不阻塞 = 什么都不发」）
        assert _wait_for(lambda: len(hub.calls) == 1, timeout=5.0), "后台线程必须真的送达"
    assert hub.calls[0]["body"]["reason"] == REASON


def test_report_error_is_silent_without_a_hub_and_leaves_one_line_when_dropped() -> None:
    """没地址/没 token ⇒ 静默丢弃（不是错误：观测面没有不存在的义务）；发失败 ⇒ 云机日志一行。"""
    logs: list[str] = []
    report_error("", "", reason=REASON, log=logs.append)
    assert logs == [], "没有 hub 就没有这条腿——不写日志（否则离线预演全是噪音）"

    with _FakeHub(status=503) as hub:
        report_error(hub.base, TOKEN, reason=REASON, log=logs.append)
        assert _wait_for(lambda: bool(logs), timeout=5.0), "失败即丢也要在云机日志留一行（最后一站）"
    assert len(logs) == 1
    assert REASON[:120] in logs[0]


# ────────────────────────── ④ 四个上报点接线（含 F15 守门）──────────────────────────


def _round_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: BaseException, *, jid: str = "j1"
) -> tuple[dict, list[str]]:
    """真跑一轮（`worker_loop` → `run_one_round`）。

    返回 `(观测腿那一次的调用, 确定性失败回报的 jid 列表)`——前者 `{}` = 这一轮**没报**，
    后者非空 = 这一轮把失败钉成了终局（报告出的那几档都不该这样）。
    """
    job = {**_job(jid), "course": "c1-gae", "manifest": {"job_id": jid, "it": 7}}
    _patch_hub(monkeypatch, [job])
    reports: list[dict] = []
    monkeypatch.setattr(
        JR, "report_error", lambda *a, **k: reports.append({"args": a, **k}), raising=True
    )
    monkeypatch.setattr(JR, "release_job", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(JR, "abandon_job", lambda *a, **k: None, raising=True)
    failures: list[str] = []
    monkeypatch.setattr(
        JR, "report_job_failure", lambda *a, **k: failures.append(str(a[2])), raising=True
    )
    _run_loop(
        tmp_path,
        monkeypatch,
        records=[],
        fail_with={jid: exc},
        lock=threading.Lock(),
    )
    return (reports[0] if reports else {}), failures


@pytest.mark.parametrize(
    "exc",
    [
        UnreapableChildError("rollout 单局超时（5.0s > 硬顶 5s）：s0/d0；且 SIGKILL 之后 5s 内回收不了"),
        RetryableError("连接被重置（code=2）"),
        RuntimeError("unexpected boom"),
    ],
    ids=["machine-level-stall", "transient", "fallback"],
)
def test_the_three_transient_branches_report_with_scene_and_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    """三个分支（机器级停滞 / 瞬时 / 兜底瞬态）各报一条，且**带现场与归属**（severity=warn）。

    现场 = `common.text.exc_tail` 的 traceback 尾段（此前这一档只有云机日志）；归属 = 课程 / job /
    轮次 / worker 身份（控制台详情页要按它们分组与跳转）。
    """
    rep, failures = _round_with(tmp_path, monkeypatch, exc)
    assert rep, f"{type(exc).__name__} 必须上报（此前只有云机日志）"
    assert failures == [], f"这三档都可恢复，不许钉成终局（那会停腿）：{failures}"
    name = type(exc).__name__
    assert rep["reason"] == f"{name}: {exc}"
    assert rep["severity"] == "warn"
    assert (rep["component"], rep["course"], rep["job_id"], rep["it"]) == (
        "ppo-worker",
        "c1-gae",
        "j1",
        7,
    )
    assert rep["worker_id"] == worker_tag(offline=False, hub_url="http://hub")
    lines = rep["detail"].splitlines()
    assert lines[0] == "Traceback (most recent call last):", rep["detail"]
    # 尾段 = 真正的原因那一帧（`类名: 消息`；非 builtin 的类名带定义模块前缀）
    head, _, msg = lines[-1].partition(": ")
    assert head.split(".")[-1] == name and msg == str(exc)
    # 一个 hub/一条 token 走全程（`_run_loop` 的位置参数，见 test_worker_reap_stall.py）
    assert rep["args"] == ("http://hub", "tok")


def test_lifecycle_outcomes_are_not_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ F15 守门：取消（合法放弃）/ 热替换（设计内退出 86）/ 确定性拒绝（已走 E2）**都不报**。

    把设计内生命周期报成「错误」正是「错误列表常年有噪音 ⇒ 没人看」的成因 —— 这一条红了
    说明有人的「顺手加上去」把噪音灌回了登记表。
    """
    for exc in (
        JobCancelledError("job 被更高优先级抢占"),
        CodeChangedError("a" * 64, "b" * 64),
        ProtocolError("bun 未安装"),
    ):
        rep, failures = _round_with(tmp_path / type(exc).__name__, monkeypatch, exc)
        assert rep == {}, f"{type(exc).__name__} 是设计内生命周期/已走 E2，不许报成错误：{rep}"
        if isinstance(exc, ProtocolError):
            assert failures == ["j1"], "确定性拒绝仍走 E2（观测腿不许挤掉它）"
        else:
            assert failures == [], f"{type(exc).__name__} 不许报确定性失败：{failures}"


def test_offline_leg_failure_reports_an_err_with_the_segment_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ E5：离线段失败（`run_loop.main` 的 `[run] 失败`）报一条 `err` —— 段末摘要只报 `state`。

    用**真入口**（`main` 的 argv 形状）触发：空产物目录 ⇒ `ProtocolError` ⇒ 那一条 except ——
    判据落在「调用方真的接上了」，而不是「模块里有这个函数」。
    """
    reports: list[dict] = []
    monkeypatch.setattr(
        run_loop_mod, "report_error", lambda *a, **k: reports.append({"args": a, **k}), raising=True
    )
    rc = run_loop_mod.main(
        [
            "--artifacts",
            str(tmp_path),
            "--hub-url",
            "http://hub",
            "--hub-token",
            "tok",
            "--hub-course",
            "c1-gae",
        ]
    )
    assert rc == 1, "失败路径必须仍然是退出码 1（观测腿不许改任何训练时序）"
    assert len(reports) == 1
    rep = reports[0]
    assert rep["args"] == ("http://hub", "tok")
    assert rep["severity"] == "err" and rep["component"] == "offline-worker"
    assert rep["reason"].startswith("ProtocolError: ")
    assert rep["course"] == "c1-gae"
    lines = rep["detail"].splitlines()
    assert lines[0] == "Traceback (most recent call last):"
    head, _, msg = lines[-1].partition(": ")
    assert head.endswith("ProtocolError")
    # 现场与那一行摘要同源：同一条异常、同一句原因（只差类名前缀）
    assert msg == rep["reason"].split(": ", 1)[1]
