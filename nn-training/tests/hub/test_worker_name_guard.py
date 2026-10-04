"""tests/hub/test_worker_name_guard.py —— 同名异来源观测守卫（plan/worker-name-readable W2/G7）。

前提（用户裁决 ⑤）= 同一环境不会同时有两台云机 ⇒ `{env}-{link}` 天然唯一。前提一旦被打破，
hub 的 `_workers` 会把两台互相覆盖而没有任何痕迹——本守卫就是那一条痕迹：

* 同名 + **两个来源**（`CF-Connecting-IP` 不同）⇒ 一行告警（capsys）；
* 同名 + 同来源（同一台机器反复轮询）⇒ 静默（否则日志被刷爆）；
* 告警**不改名、不入账**（守现状：`_workers` 一个键、盘上零账本）。

用真 hub 服务器（真路由 + 真鉴权）走 `/jobs/peek`，与 `test_worker_attribution.py` 同规。
"""

from __future__ import annotations

import sys
import threading
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import AUTH_HEADER, WORKER_ID_HEADER
from hub.http_face import CF_SOURCE_HEADER, HubHandler
from hub.server import _HubQueue, make_server

TOKEN = "sekret"
WARN_FRAGMENT = "前提可能被打破"


@pytest.fixture(autouse=True)
def _clean_guard_state():
    """守卫状态是 HubHandler 的 class 级 dict（进程内共享）——每个用例前后清干净。"""
    HubHandler._seen_worker_sources.clear()
    HubHandler._worker_source_warned.clear()
    yield
    HubHandler._seen_worker_sources.clear()
    HubHandler._worker_source_warned.clear()


@contextmanager
def _hub(tmp_path: Path):
    hub = _HubQueue({}, discover_root=tmp_path)
    hub.discover()
    srv: ThreadingHTTPServer = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", hub
    finally:
        srv.shutdown()
        srv.server_close()


def _peek(base: str, worker: str, cf_ip: str) -> bytes:
    req = urllib.request.Request(
        f"{base}/jobs/peek",
        headers={
            AUTH_HEADER: f"Bearer {TOKEN}",
            WORKER_ID_HEADER: worker,
            CF_SOURCE_HEADER: cf_ip,
        },
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:  # 测试本地回环
        assert resp.status == 200
        raw = resp.read()
        assert isinstance(raw, bytes)
        return raw


def test_same_name_from_two_sources_logs_a_warning(tmp_path: Path, capsys) -> None:
    with _hub(tmp_path) as (base, _hubq):
        _peek(base, "kaggle-c", "203.0.113.7")
        _peek(base, "kaggle-c", "203.0.113.8")
    out = capsys.readouterr().out
    assert WARN_FRAGMENT in out, out
    assert "kaggle-c" in out


def test_same_name_from_same_source_is_silent(tmp_path: Path, capsys) -> None:
    with _hub(tmp_path) as (base, _hubq):
        _peek(base, "kaggle-c", "203.0.113.7")
        _peek(base, "kaggle-c", "203.0.113.7")
        _peek(base, "kaggle-c", "203.0.113.7")
    out = capsys.readouterr().out
    assert WARN_FRAGMENT not in out, out


def test_guard_does_not_alter_names_or_ledger(tmp_path: Path, capsys) -> None:
    """守卫只观测：告警后名字（`_workers` 一个键）与盘上账本逐字不变。"""
    with _hub(tmp_path) as (base, hub):
        first = _peek(base, "kaggle-c", "203.0.113.7")
        second = _peek(base, "kaggle-c", "203.0.113.8")
        assert first == second, "同名异来源不得改变响应"
        assert WARN_FRAGMENT in capsys.readouterr().out
        assert set(hub._workers) == {"kaggle-c"}, hub._workers
    assert not list(tmp_path.rglob("training_log.jsonl")), "守卫不得写账"
