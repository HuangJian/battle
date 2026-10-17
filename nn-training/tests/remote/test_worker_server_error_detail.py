"""test_worker_server_error_detail.py — push 云机把**异常现场**带回家（plan/component-error-registry §4.3-S3a）。

缺口（评审把这条腿从「上报新端点」改成「复用既有 410 通道」的直接理由）：worker_server 的
410 体只带 `error`（一行字）+ `fail_kind`，于是 hub 的 `remote/push_dispatch.py` 只能把
`detail` 写成常量「hub push dispatch（hub 代发后取回）」——云机上的 traceback **永远回不来**，
控制台那个错误登记表只能显示那一句话（=「只有翻日志才知道原因」原样保留）。

修法是**纯加法三步**（无新端点、无新凭据、无阻塞）：`set_error(detail=...)` / 两个 except 传
`common.text.exc_tail(e)` / 410 体加 `fail_detail`；hub 侧缺字段回退原常量（旧 worker 不受影响）。

本文件钉四件事：
  ① 410 体带 `fail_detail`（`set_error` 存什么就回什么）；
  ② 没传 detail（旧调用方）⇒ **键在场且为空串**（不是缺键：旧 hub 读不到也不编）；
  ③ `_execute_job` 的 except 路径**真的**把 traceback 填进去（monkeypatch `run_job` 抛）；
  ④ hub 侧映射（`push_failure_record` 纯函数）：有 `fail_detail` 用它、没有回退常量。

判据全部用**全等**（`==`）或结构比较（取 traceback 的首/末行），不写成「某段文本里有没有某段
文本」——那种判据挂在措辞上，改一个标题就红（`tests/helpers/text_asserts.py` 口径 A 的理由）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

# 复用现有测试基建（`_http` / `_mini_manifest` / `_boot_server`）
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import normalize_manifest
from remote.push_dispatch import PUSH_FAILURE_DETAIL_FALLBACK, push_failure_record
from remote.worker_server import WorkerServerState, make_worker_server
from tests.remote.test_remote_ppo import (  # type: ignore
    _http,
    _mini_manifest,
)

_DETAIL = (
    "Traceback (most recent call last):\n"
    '  File "/kaggle/working/remote/worker.py", line 412, in run_job\n'
    "    raise ProtocolError('bun 未安装')\n"
    "common.protocol.ProtocolError: bun 未安装"
)


def _boot(tmp_path: Path) -> tuple[WorkerServerState, object, threading.Thread, str]:
    """起一个 push 云的 worker_server（随机端口）；返回 (state, srv, thread, base)。"""
    state = WorkerServerState(tmp_path / "work")
    srv = make_worker_server(state, 0, "tok")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return state, srv, th, f"http://127.0.0.1:{srv.server_address[1]}"  # type: ignore[attr-defined]


def test_410_body_carries_fail_detail(tmp_path: Path) -> None:
    """① `set_error(detail=…)` 存什么，410 体就回什么（hub 侧那条腿的全部依据）。"""
    state, srv, th, base = _boot(tmp_path)
    try:
        state.set_state("j1", "running")
        state.set_error("j1", "ProtocolError: bun 未安装", kind="ProtocolError", detail=_DETAIL)
        st, body = _http(base, "tok", "/job/j1/result")  # type: ignore[arg-type]
        assert st == 410, body
        assert body["failed"] is True
        assert body["fail_kind"] == "ProtocolError"
        assert body["fail_detail"] == _DETAIL  # 逐字节原样（截断已在 exc_tail 里做过）
        # 终局仍可重复读（幂等）——现场不会在第二次读时消失
        st2, body2 = _http(base, "tok", "/job/j1/result")  # type: ignore[arg-type]
        assert (st2, body2["fail_detail"]) == (410, _DETAIL)
    finally:
        srv.shutdown()  # type: ignore[attr-defined]
        th.join()


def test_legacy_set_error_has_the_key_but_empty(tmp_path: Path) -> None:
    """② 旧调用方（不传 detail）⇒ 键在场、值为空串：旧 hub 的 `or` 回退照样生效，且不出现 KeyError。"""
    state, srv, th, base = _boot(tmp_path)
    try:
        state.set_error("j2", "RuntimeError: boom", kind="RuntimeError")
        st, body = _http(base, "tok", "/job/j2/result")  # type: ignore[arg-type]
        assert st == 410, body
        assert body["fail_detail"] == ""
    finally:
        srv.shutdown()  # type: ignore[attr-defined]
        th.join()


def test_execute_job_failure_really_fills_detail(tmp_path: Path, monkeypatch) -> None:
    """③ 真跑一遍 except 路径：`run_job` 抛出的异常 ⇒ 410 体里的现场是**那条 traceback**。

    这条是「三处改动必须成套」的守门：只加 410 字段而 except 不传 detail（或反过来）都红。
    """
    import remote.worker_server as ws

    def _boom(*_a: object, **_k: object) -> dict:
        raise RuntimeError("boom-现场")

    monkeypatch.setattr(ws, "run_job", _boom)
    state, srv, th, base = _boot(tmp_path)
    try:
        payload = b"PK\x03\x04not-a-real-zip"
        code = b"PK\x03\x04fake-code"
        manifest = normalize_manifest(
            _mini_manifest(
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                code_sha256=hashlib.sha256(code).hexdigest(),
            )
        )
        jid = manifest["job_id"]
        st, _ = _http(  # type: ignore[arg-type]
            base,
            "tok",
            "/job",
            "POST",
            data=json.dumps(
                {
                    "manifest": manifest,
                    "payload_b64": base64.b64encode(payload).decode("ascii"),
                    "code_b64": base64.b64encode(code).decode("ascii"),
                }
            ).encode("utf-8"),
        )
        assert st == 202, st
        deadline, st2 = time.time() + 20.0, 202
        body2: dict = {}
        while time.time() < deadline:
            st2, body2 = _http(base, "tok", f"/job/{jid}/result")  # type: ignore[arg-type]
            if st2 == 410:
                break
            # sleep-ok: 轮询步长（等的是谓词：job 是否已落 failed；20s 只兜挂起）
            time.sleep(0.02)
        assert st2 == 410, body2
        assert body2["fail_kind"] == "RuntimeError"
        lines = body2["fail_detail"].splitlines()
        assert lines[0] == "Traceback (most recent call last):"
        assert lines[-1] == "RuntimeError: boom-现场"
    finally:
        srv.shutdown()  # type: ignore[attr-defined]
        th.join()


def test_push_failure_record_prefers_fail_detail_over_fallback() -> None:
    """④ hub 侧映射（纯函数）：新 worker 的现场进 `fail.json.detail`；旧 worker 回退常量。"""
    rec = push_failure_record(
        {"error": "ProtocolError: bun 未安装", "fail_kind": "ProtocolError", "fail_detail": _DETAIL},
        "gpu-1",
        1234,
    )
    assert rec["detail"] == _DETAIL
    assert rec["reason"] == "ProtocolError: bun 未安装"
    assert rec["kind"] == "ProtocolError"
    assert (rec["worker"], rec["ts"]) == ("gpu-1", 1234)

    legacy = push_failure_record({"error": "worker 判定跑不成"}, "gpu-1", 1)
    assert legacy["detail"] == PUSH_FAILURE_DETAIL_FALLBACK
    # 超长现场按 4000 截断（与 `hub/result.py` / 控制台 `ERROR_DETAIL_MAX` 同量）
    clipped = push_failure_record({"fail_detail": "x" * 9000}, "w", 1)
    assert len(clipped["detail"]) == 4000
