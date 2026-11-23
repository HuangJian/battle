"""`WaitJobSpec` / `HubFailSpec` 的形状守卫（`python.refactor.md` §P0-3，批 F4，2026-11-22）。

批 F4：`remote/hub_http.wait_job`(3 位置 + 6 关键字) 与 `remote/hub_http.report_job_failure`
(4 位置 + 5 关键字) 各收一份 spec —— 同族的 job 控制面薄壳，但**各给一份**（字段表不同：
`job_lifecycle.FailReportSpec` 用 `worker_id` + `worker_tag()` 兜底，这里用显式 `worker`）。

两处坏点都是「互换后什么都不抛」：

* `wait_job` 的 `timeout_sec` / `poll_sec` / `poll_max_sec` / `report_every_sec` 是**四个相邻的
  float，单位还全是秒** —— 超时与轮询步长互换 ⇒ 5 秒总预算、25 分钟一次探测（job 永远等不到）；
  退避封顶与报告间隔互换 ⇒ 退避封顶成 900s 而「还在等什么」每 60s 一行（或反之：刷屏且永不封顶）。
  `now` 与 `log` 是两个**相邻回调**且都可调用 —— 互换后时间戳进日志、日志对象当时钟。
* `report_job_failure` 的 `kind` / `detail` / `worker` / `lease_token` 是四个相邻字符串，去向却
  分三处：`kind` 进拒因分类、`detail` 进 body 详情、`worker` 进 body 归属、`lease_token` 进
  `X-Lease-Token` **请求头**。互换后请求照样 200/被拒，只是「谁报的」与「凭什么报」换了位置。

⚠ 行为面用**真发一次 HTTP**（`_RecordingHub`）而不是打 `_request` 的补丁：`_request` 是私有名，
`test_monkeypatch_targets_budget` 只给「确实必须打桩」的第三方边界留额度；本文件既没有那个必要，
也就没有那笔额度可用。
"""

from __future__ import annotations

import dataclasses
import inspect
import itertools
import json
import sys
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import hub_http as hh
from tests.helpers.http_server import serve_fast

MISSING = dataclasses.MISSING


def _table(cls: type) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: 旧 `wait_job` 的 9 个形参（3 个位置在前）。
WAIT_FIELDS: list[tuple[str, object]] = [
    ("base_url", MISSING),
    ("token", MISSING),
    ("jid", MISSING),
    ("timeout_sec", 25 * 60),
    ("poll_sec", 5.0),
    ("poll_max_sec", 60.0),
    ("report_every_sec", hh.WAIT_REPORT_SEC),
    ("now", time.time),
    ("log", hh._log),
]

#: 旧 `report_job_failure` 的 9 个形参（4 个位置在前）。
FAIL_FIELDS: list[tuple[str, object]] = [
    ("base_url", MISSING),
    ("token", MISSING),
    ("jid", MISSING),
    ("reason", MISSING),
    ("kind", ""),
    ("detail", ""),
    ("worker", ""),
    ("lease_token", ""),
    ("log", hh._log),
]


def test_wait_field_table_matches_the_old_signature() -> None:
    assert _table(hh.WaitJobSpec) == WAIT_FIELDS


def test_fail_field_table_matches_the_old_signature() -> None:
    assert _table(hh.HubFailSpec) == FAIL_FIELDS


def test_specs_are_frozen_and_keyword_only() -> None:
    wait = hh.WaitJobSpec(base_url="http://hub", token="tok", jid="j1")
    fail = hh.HubFailSpec(base_url="http://hub", token="tok", jid="j1", reason="boom")
    for spec in (wait, fail):
        rows = dataclasses.fields(spec)
        assert all(f.kw_only for f in rows), (
            type(spec).__name__,
            [f.name for f in rows if not f.kw_only],
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(spec, rows[0].name, object())


def test_entries_take_exactly_one_spec() -> None:
    for entry in (hh.wait_job, hh.report_job_failure):
        assert list(inspect.signature(entry).parameters) == ["spec"], entry.__name__


def test_old_forms_are_rejected() -> None:
    wait = cast(Callable[..., object], hh.wait_job)
    fail = cast(Callable[..., object], hh.report_job_failure)
    with pytest.raises(TypeError):
        wait("http://hub", "tok", "j1", timeout_sec=1)
    with pytest.raises(TypeError):
        fail("http://hub", "tok", "j1", "boom")


def test_the_four_clock_knobs_stay_distinct() -> None:
    """四个秒级旋钮各占一位：一个都不能冒充另一个（错位只会让等待策略静默换档）。"""
    spec = hh.WaitJobSpec(
        base_url="http://hub",
        token="tok",
        jid="j1",
        timeout_sec=11.0,
        poll_sec=0.5,
        poll_max_sec=7.0,
        report_every_sec=3.0,
    )
    assert (spec.timeout_sec, spec.poll_sec, spec.poll_max_sec, spec.report_every_sec) == (
        11.0,
        0.5,
        7.0,
        3.0,
    )


class _HubServer(ThreadingHTTPServer):
    """带路由表的 hub（字段显式声明：用例里不打 `# type: ignore[attr-defined]` 的桩）。"""

    routes: dict[str, list[bytes]]
    calls: list[tuple[str, dict[str, str], bytes]]
    status: int = 200


class _Handler(BaseHTTPRequestHandler):
    """最小 hub：按路径发**排队好的**应答，并把每次请求的路径/头/体记下来。"""

    protocol_version = "HTTP/1.1"

    def _record(self) -> bytes:
        srv = cast(_HubServer, self.server)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        srv.calls.append((self.path, dict(self.headers), raw))
        queue = srv.routes.get(self.path.split("?")[0]) or []
        body = queue.pop(0) if queue else b""
        # 空体 = 这个端点「还没回」（404）；探针把它读成「正常排队」。
        self.send_response(srv.status if body else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return body

    def do_GET(self) -> None:  # http.server 的命名约定
        self._record()

    def do_POST(self) -> None:  # http.server 的命名约定
        self._record()

    def log_message(self, *_a: object) -> None:  # 静音（本文件只起一两个）
        pass


class _RecordingHub:
    """`with` 里跑一个真 hub；`base` = `http://127.0.0.1:<端口>`。"""

    def __init__(self, routes: dict[str, list[bytes]]) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self._srv = _HubServer(("127.0.0.1", 0), _Handler)
        self._srv.routes = {k: list(v) for k, v in routes.items()}
        self._srv.calls = self.calls
        self._th = serve_fast(self._srv)

    def __enter__(self) -> _RecordingHub:
        self._th.start()
        return self

    def __exit__(self, *_a: object) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._th.join()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def paths(self) -> list[str]:
        return [p for p, _h, _b in self.calls]


def test_lease_token_goes_to_the_header_and_worker_to_the_body() -> None:
    """身份位各回各家：`lease_token` 进请求头、`worker` 进 body（互换后请求照样成立）。"""
    with _RecordingHub({"/jobs/j1/fail": [b"{}"]}) as hub:
        ok = hh.report_job_failure(
            hh.HubFailSpec(
                base_url=hub.base,
                token="tok",
                jid="j1",
                reason="boom",
                worker="kaggle-1:42",
                lease_token="lease-abc",
                log=lambda _m: None,
            )
        )
        assert ok is True
        assert hub.paths() == ["/jobs/j1/fail"]
        _path, headers, raw = hub.calls[0]
        assert headers["X-Lease-Token"] == "lease-abc"
        assert json.loads(raw.decode("utf-8"))["worker"] == "kaggle-1:42"


def test_leased_status_reenters_with_the_same_knobs() -> None:
    """`leased` ⇒ 再给一个预算：重入必须**原样**带着那份 spec（以前是手工重传 8 个旋钮）。"""
    # 时钟每次跳 0.4s、预算 0.5s ⇒ 每次进入只探一拍就以「超时」走到状态确认：
    # 第一次拿到 leased（重入），重入后拿到 done —— 重入路径真被走到。
    # `/result` 第一次给 404（还没回 = 正常排队）、第二次才给结果。
    ticks = itertools.count(0.0, 0.4)
    lines: list[str] = []
    routes = {
        "/jobs/j1/status": [b'{"state": "leased"}', b'{"state": "done"}'],
        "/jobs/j1/result": [b"", b'{"ok": true}'],
    }
    with _RecordingHub(routes) as hub:
        got = hh.wait_job(
            hh.WaitJobSpec(
                base_url=hub.base,
                token="tok",
                jid="j1",
                timeout_sec=0.5,
                poll_sec=0.0,
                now=lambda: float(next(ticks)),
                log=lines.append,
            )
        )
        assert got == {"ok": True}
        # 一次状态确认（leased）之后就重入：重入那一拍才拿到结果。
        assert hub.paths().count("/jobs/j1/status") == 1
        assert hub.paths().count("/jobs/j1/result") == 2
    # 重入那个预算原样生效：日志说「再等 0.5s」（缺省是 1500s）——
    # 手工重传漏一个旋钮就抓不住。
    assert sum(ln.count("再等 0.5s") for ln in lines) == 1


def test_spec_carries_the_identity_it_was_built_with() -> None:
    """三个身份位是同一个 hub/同一份活：spec 不是「一个可变袋子」，转发用的都是原值。"""
    spec = hh.WaitJobSpec(base_url="http://hub:8787", token="tok", jid="j-7")
    assert (spec.base_url, spec.token, spec.jid) == ("http://hub:8787", "tok", "j-7")
    fail = hh.HubFailSpec(base_url="http://hub:8787", token="tok", jid="j-7", reason="boom")
    assert (fail.base_url, fail.token, fail.jid, fail.reason) == (
        "http://hub:8787",
        "tok",
        "j-7",
        "boom",
    )
