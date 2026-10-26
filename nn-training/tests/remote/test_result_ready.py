"""test_result_ready —— worker 的「结果就绪」通知（2026-10-26 用户口径）。

为什么单开一组：这条通知是**延迟优化，不是交付路径**，判据有两条且互相拉扯：
  ① 成功时路径/鉴权/空体必须逐字节对（hub 端点只认这个形状）；
  ② 任何失败都**不许影响 job 的结果与状态**（丢了就回落 `PUSH_POLL_SEC` 轮询）。
外加一条顺序律：通知到达时 `GET /job/{id}/result` 必须**已经**读得到结果——
先敲 hub 再落盘的竞态只会让 hub 白问一次（还是 202），但把「就绪」说早了一拍。
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from common.protocol import AUTH_HEADER, RESULT_READY_PATH
from remote import push_bootstrap as pb
from remote import worker_server as ws


class _HubStub:
    """只收 `/jobs/{id}/result-ready` 的最小 hub（记录 path / token / body）。"""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.seen: list[tuple[str, str, bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.seen.append(
                    (self.path, self.headers.get(AUTH_HEADER, ""), body)
                )
                payload = b"{}"
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)


def test_notify_posts_the_ready_path_with_the_push_token() -> None:
    hub = _HubStub()
    try:
        # 尾斜杠要吞掉（registry_url 常带 `/`）；jid 要过 quote。
        assert ws.notify_result_ready(hub.url + "/", "tok", "abc 123") is True
        path, auth, body = hub.seen[0]
        assert path == RESULT_READY_PATH.format(jid="abc%20123")
        assert auth == "Bearer tok"
        assert body == b""
    finally:
        hub.close()


def test_notify_is_silent_when_hub_url_is_missing() -> None:
    """旧部署/手动起服务没有 `--hub-url` ⇒ 一个请求都不发，也不报错。"""
    assert ws.notify_result_ready("", "tok", "abc") is False


def test_notify_never_raises_on_dead_hub() -> None:
    """连接被拒 = 通知丢失：返回 False，绝不把 job 标成失败。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # 立刻释放：这个端口上必然没有监听者
    assert ws.notify_result_ready(f"http://127.0.0.1:{port}", "tok", "abc") is False


def test_notify_treats_hub_rejection_as_lost_notice() -> None:
    """hub 拒收（409 = 没开派发器）⇒ 返回 False 且**不重试**（每个 job 至多一次）。"""
    hub = _HubStub(status=409)
    try:
        assert ws.notify_result_ready(hub.url, "tok", "abc") is False
        assert len(hub.seen) == 1
    finally:
        hub.close()


def test_execute_job_notifies_only_after_the_result_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """顺序不可颠倒：hub 被叫醒后立刻 GET，那一刻结果必须已经落表。"""
    state = ws.WorkerServerState(tmp_path)
    calls: list[tuple[str, str, str]] = []

    def fake_run_job(*_a: Any, **_kw: Any) -> dict:
        return {"job_id": "j1", "agg": {}}

    def fake_notify(hub_url: str, token: str, jid: str, **_kw: Any) -> bool:
        # 通知发出时 `GET /job/{id}/result` 必须读得到 ⇒ state 已是 done。
        assert state.jobs[jid]["state"] == "done"
        calls.append((hub_url, token, jid))
        return True

    monkeypatch.setattr(ws, "run_job", fake_run_job)
    monkeypatch.setattr(ws, "notify_result_ready", fake_notify)
    ws._execute_job(
        state,
        {"job_id": "j1"},
        b"",
        None,
        None,
        None,
        tmp_path,
        "cpu",
        0,
        False,
        hub_url="http://hub/",
        push_token="tok",
        log=lambda _m: None,
    )
    assert calls == [("http://hub/", "tok", "j1")]
    assert state.jobs["j1"]["state"] == "done"


def test_bootstrap_passes_hub_url_only_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引导服务升级到完整 worker_server 时，`--hub-url` 只在非空时出现（去尾斜杠）。"""
    seen: list[list[str]] = []

    class _Proc:
        pid = 4242

    def fake_popen(argv: list[str], **_kw: Any) -> _Proc:
        seen.append(list(argv))
        return _Proc()

    monkeypatch.setattr(pb.subprocess, "Popen", fake_popen)
    kw: dict[str, Any] = dict(port=1234, token="tok", work_dir=tmp_path, device="cpu",
                              code_dir=tmp_path, log_path=tmp_path / "serve.log")
    pb.spawn_full_worker_server(**kw, hub_url="http://hub.example/")
    assert seen[-1][seen[-1].index("--hub-url") + 1] == "http://hub.example"
    pb.spawn_full_worker_server(**kw, hub_url="")
    assert seen[-1].count("--hub-url") == 0
