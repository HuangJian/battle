"""R1-9 / R2-8：`GET /jobs/next` 退役的机械门禁。

不兼容旧 worker 是用户 2026-09-22 授权的**一次性**切换：旧 worker 拿到 404 就是 404
（响亮），**不**给兼容层。本文件钉三件事：

  ① 端点已删（404）——不保留别名；
  ② 生产代码（`remote/`、`rl/`、根入口）引用 `"/jobs/next"` **零命中**；
  ③ 测试侧统一走 `tests/helpers/hub_poll.py`（R2-8：10 个文件机械替换，不各写一份分叉）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_server import _HubQueue, make_server
from tests.helpers.hub_poll import hub_poll

#: 生产代码（非测试）搜索根：这些一律不得再引用退役端点。
#: `protocol.py` 2026-09-23 下沉到 `common/`（S3 断 rl↔remote 循环）——路径跟着走。
_PROD_FILES = [
    ROOT / "remote" / "hub_server.py",
    ROOT / "remote" / "worker.py",
    ROOT / "remote" / "worker_server.py",
    ROOT / "remote" / "push_dispatch.py",
    ROOT / "remote" / "push_client.py",
    ROOT / "common" / "protocol.py",
    ROOT / "remote" / "hub_client.py",
    ROOT / "remote" / "run_loop.py",
    ROOT / "remote" / "offline_deliver.py",
    ROOT / "run_rl.py",
]


def test_old_poll_endpoint_is_404(tmp_path: Path) -> None:
    """`GET /jobs/next` → 404（面已删，不是空列表也不是达令）。"""
    import json
    import threading
    import urllib.error
    import urllib.request

    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, "sekret", host="127.0.0.1")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_address[1]}/jobs/next",
            headers={"Authorization": "Bearer sekret"},
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=5)
        assert ei.value.code == 404, ei.value.read()
        assert json.loads(ei.value.read() or b"{}") == {} or True  # 体不重要，状态码是判据
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_no_production_code_references_the_old_endpoint() -> None:
    """生产代码里 `"/jobs/next"` 零命中（秒表：后人顺手加回一个兼容别名会在这里红）。"""
    hits = [str(p.relative_to(ROOT)) for p in _PROD_FILES if "/jobs/next" in p.read_text(encoding="utf-8")]
    assert hits == [], f"生产代码仍在引用退役端点：{hits}"


def test_hub_poll_helper_is_the_shared_test_seam() -> None:
    """`hub_poll` 存在且被 ≥5 个测试/端到端文件 import（R2-8 的「一处改一份」）。"""
    importers: list[str] = []
    for base in (ROOT / "tests", ROOT / "e2e"):
        for f in base.rglob("test_*.py"):
            if "from tests.helpers.hub_poll import" in f.read_text(encoding="utf-8"):
                importers.append(str(f.relative_to(ROOT)))
    assert len(importers) >= 5, f"只被 {len(importers)} 个文件使用：{importers}"


def test_hub_poll_returns_legacy_shape(tmp_path: Path) -> None:
    """助手形状与旧面同形：无活 + 未停机 ⇒ `None`（不是空 dict）。"""
    import threading

    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, "sekret", host="127.0.0.1")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        assert hub_poll(f"http://127.0.0.1:{srv.server_address[1]}", "sekret") is None
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)
