"""tests/remote/test_rollout_tunnel.py —— quick tunnel 的日志与健康工具。

从 `tests/remote/test_rollout_node.py` 拆出（2026-11-18，G5「建接缝」）：被测对象一直住在
`remote/rollout_tunnel.py`（2026-11-08 为 LOC 预算从 `rollout_node` 拆出），用例却经由
`rollout_node` 的私有别名调用它 —— 于是「隧道怎么判死」的测试看起来像在测节点的私有实现。
这里直接对公开函数（`retry_due` / `tunnel_dead` / `public_ping` / `dead_reason` / `reset_log`）
断言，与 `rollout_node` 的监护循环解耦。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from remote import rollout_tunnel as rt


class _Proc:
    """最小进程替身：`poll()` 的返回值是判死的唯一进程侧证据。"""

    def __init__(self, rc: int | None) -> None:
        self.rc = rc
        self.terminated = False

    def poll(self) -> int | None:
        return self.rc

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_tunnel_retry_due_only_when_empty_and_due() -> None:
    assert rt.retry_due("https://a.trycloudflare.com", 100.0, 0.0) is False
    assert rt.retry_due("", 100.0, 101.0) is False
    assert rt.retry_due("", 101.0, 101.0) is True


def test_tunnel_dead_requires_cloudflared_and_evidence() -> None:
    live, dead = _Proc(None), _Proc(7)
    kw: dict[str, Any] = {
        "is_cloudflared": True,
        "url": "https://a.trycloudflare.com",
    }
    assert rt.tunnel_dead(**kw, cfp=dead, agent_ok=True, probe_fails=0) is True
    assert rt.tunnel_dead(**kw, cfp=None, agent_ok=True, probe_fails=0) is True
    # 公网探针：连续 2 次不算、3 次算；本地 agent 不健康时不判。
    assert rt.tunnel_dead(**kw, cfp=live, agent_ok=True, probe_fails=2) is False
    assert rt.tunnel_dead(**kw, cfp=live, agent_ok=True, probe_fails=3) is True
    assert rt.tunnel_dead(**kw, cfp=live, agent_ok=False, probe_fails=9) is False
    # tailscale 通道 cfp=None 是正常态；空 URL 走补拿闸。
    assert (
        rt.tunnel_dead(
            is_cloudflared=False, url="http://100.64.0.5:8443", cfp=None,
            agent_ok=True, probe_fails=9,
        )
        is False
    )
    assert (
        rt.tunnel_dead(
            is_cloudflared=True,
            url="",
            cfp=None,
            agent_ok=True,
            probe_fails=9,
        )
        is False
    )


def test_public_ping_runs_in_platform_net_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公网探针必须在 `_public_net()` 里跑（否则 tailnet 代理会把它恒打死）。"""
    seen: list[str] = []

    class _Ctx:
        def __enter__(self) -> None:
            seen.append("in")

        def __exit__(self, *_a: object) -> None:
            seen.append("out")

    monkeypatch.setattr(rt.tailscale_boot, "platform_net_env", lambda: _Ctx())
    assert rt.public_ping("https://x.trycloudflare.com", "ak", lambda *_a: {"ok": 1}) == {
        "ok": 1
    }
    assert seen == ["in", "out"]

    def _boom(*_a: object) -> dict | None:
        raise RuntimeError("down")

    assert rt.public_ping("https://x.trycloudflare.com", "ak", _boom) is None


def test_cf_log_is_reset_between_attempts(tmp_path: Path) -> None:
    cf = tmp_path / "cf.log"
    cf.write_text("https://a.trycloudflare.com", encoding="utf-8")
    rt.reset_log(cf, 1)
    assert cf.read_text(encoding="utf-8") == ""
    assert (tmp_path / "cf.log.1").read_text(encoding="utf-8").startswith(
        "https://a.trycloudflare.com"
    )

    missing = tmp_path / "missing.log"
    rt.reset_log(missing, 2)
    assert missing.read_text(encoding="utf-8") == ""


def test_dead_reason_extracted_from_log(tmp_path: Path) -> None:
    cf = tmp_path / "cf.log"
    cf.write_text(
        '{"level":"info","message":"Requesting new quick Tunnel..."}\n'
        '{"level":"error","message":"failed to dial: no such host"}\n',
        encoding="utf-8",
    )
    assert rt.dead_reason(cf) == (
        '{"level":"error","message":"failed to dial: no such host"}'
    )

    cf.write_text('{"level":"info","message":"still connecting"}\n', encoding="utf-8")
    assert rt.dead_reason(cf) == "未能从日志提取"
