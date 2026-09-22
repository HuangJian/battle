"""§7 复现 + 锁行为：取活面有可领任务时必须 200，且首次下发 touch claimed。
回归：self._now()（handler 无此属性）→ 500，云 worker 全取不到 job。

P3b（supersede §343）：分发改独占加超时——首次下发带 lease_token 并设租约，
第二次轮询（租约期内）不再重发同一 job（job_id None），直到 TTL 过期或 release。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 复用现有测试基建
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from test_remote_ppo import (  # type: ignore
    _boot_server,
    _mini_manifest,
    _next,
    normalize_manifest,
)


def test_jobs_next_touches_claimed_and_returns_200(tmp_path: Path) -> None:
    base, store, srv, th = _boot_server(tmp_path)
    try:
        manifest = normalize_manifest(_mini_manifest())
        jid = manifest["job_id"]
        store.publish(jid, manifest, b"PK\x03\x04fake")

        # 修复前：handler 调 self._now() → AttributeError → 500
        body = _next(base)
        assert body["job_id"] == jid
        assert body.get("lease_token"), "独占发放必须下发 lease_token（P3b）"

        claimed = store._job_dir(jid) / "claimed"
        assert claimed.exists(), "首次下发必须 touch claimed（console 排队超时依赖）"
        ts = float(claimed.read_text(encoding="utf-8"))
        assert ts > 0

        # 再次轮询：claimed 保持唯一，不重写导致 mtime 重置（排队时钟不被轮询打断）
        mtime1 = claimed.stat().st_mtime_ns
        body2 = _next(base)
        # P3b 独占：租约期内同一 job 不重发——排队时钟同样不被打断
        assert body2["job_id"] is None, body2
        assert claimed.stat().st_mtime_ns == mtime1
    finally:
        srv.shutdown()
        th.join()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
