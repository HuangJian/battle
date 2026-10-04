"""tests/remote/test_worker_name.py —— worker 可读身份的下传链（plan/worker-name-readable W1）。

钉住的东西：

* `worker_tag(offline, hub_url)` = `common/env_probe` 组合（**纯函数**；这里注入假探测
  ⇒ 精确字符串），本机 = `local`（不带 link 段）；
* 生产下传：`post_result(worker_id=…)` 进请求头、`report_job_failure(worker_id=…)` 进 body
  ——worker_loop 启动时算一次，之后**显式传递**（零模块级状态；缺参回退 `worker_tag()`）；
* 同参两次同值；垃圾 URL 不抛。

判据（env 探测的导入语义）在 `tests/common/test_env_probe.py`；端到端落账在
`e2e/test_worker_name_ledger_e2e.py`。本文件只管**接线**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_lifecycle as JL
from common.protocol import WORKER_ID_HEADER

JID = "j" * 16


def _pin_env(monkeypatch, env: str) -> None:
    """把 worker_tag 的环境探测钉成固定值（只注入探测函数，link 判据走真实现）。"""
    monkeypatch.setattr(JL.env_probe, "probe_cloud_env", lambda **_k: env)


# ────────────────────────── ① 名字 = env + link ──────────────────────────


def test_worker_tag_is_env_link(monkeypatch) -> None:
    _pin_env(monkeypatch, "kaggle")
    assert JL.worker_tag(hub_url="http://100.64.0.1:8787") == "kaggle-t"
    assert JL.worker_tag(offline=True, hub_url="") == "kaggle-o"
    assert JL.worker_tag(hub_url="https://x.trycloudflare.com") == "kaggle-c"


def test_worker_tag_local_has_no_link(monkeypatch) -> None:
    _pin_env(monkeypatch, "local")
    assert JL.worker_tag(offline=True, hub_url="http://100.64.0.1:8787") == "local"


def test_worker_tag_stable_for_same_args() -> None:
    """纯函数：同参两次同值（G3；进程内稳定由「单次计算 + 显式下传」保证，不靠 memo）。"""
    assert JL.worker_tag(hub_url="http://127.0.0.1:1") == JL.worker_tag(hub_url="http://127.0.0.1:1")


def test_worker_tag_never_raises_on_garbage_url() -> None:
    for url in ("", "::garbage::", "http://[", "http://:8787"):
        got = JL.worker_tag(hub_url=url)
        assert isinstance(got, str) and got, url


# ────────────────────────── ② 下传：post_result ──────────────────────────


def test_post_result_threads_worker_id(monkeypatch) -> None:
    seen: dict = {}

    def fake_request(_base_url: str, _token: str, _path: str, **kw: object):
        headers = kw.get("headers") or {}
        if isinstance(headers, dict):
            seen.update(headers)
        return 200, b"{}"

    monkeypatch.setattr(JL, "_request", fake_request)
    monkeypatch.setattr(JL, "_wire_add", lambda *_a, **_k: None)
    rc = JL.post_result(
        "http://hub", "tok", JID, {"a": 1}, worker_id="kaggle-c", log=lambda _m: None
    )
    assert rc == 200
    assert seen.get(WORKER_ID_HEADER) == "kaggle-c"


# ────────────────────────── ③ 下传：report_job_failure ──────────────────────────


def test_report_job_failure_threads_worker_id(monkeypatch) -> None:
    captured: dict = {}

    def fake_request(_base_url: str, _token: str, _path: str, **kw: object):
        data = kw.get("data")
        if isinstance(data, bytes):
            captured["body"] = json.loads(data.decode("utf-8"))
        return 200, b"{}"

    monkeypatch.setattr(JL, "_request", fake_request)
    ok = JL.report_job_failure(
        "http://hub", "tok", JID, "boom", worker_id="colab-t", log=lambda _m: None
    )
    assert ok is True
    assert captured["body"]["worker"] == "colab-t"
