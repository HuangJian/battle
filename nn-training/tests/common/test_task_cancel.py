"""task_key / cancel_task —— 与 agent 的取消键契约（2026-11-10）。"""

from __future__ import annotations

import json

import common.distribution as dist


def test_task_key_matches_agent_vectors() -> None:
    """固定向量与 tools/agent/tests 的 taskKey 用例逐字节一致。"""
    assert dist.task_key("it1", "rollout", "rollout", 2000, 7) == (
        "it1:rollout:rollout:2000:7"
    )
    assert dist.task_key(
        "it1", "rollout", "rollout", 2000, 7, "abcdef1234567890"
    ) == "it1:rollout:rollout:2000:7:abcdef1234567890"
    assert dist.task_key(
        "it1", "rollout", "rollout", 2000, 7, "", "c0ffee1234567890"
    ) == "it1:rollout:rollout:2000:7:cc0ffee1234567890"
    assert dist.task_key(
        "it1", "rollout", "rollout", 2000, 7, "abcdef1234567890", "c0ffee1234567890"
    ) == "it1:rollout:rollout:2000:7:abcdef1234567890:c0ffee1234567890"
    assert dist.task_key(
        "it1", "bc-v6", "rollout", 2000, 7, "", "", "de1", "5", "argmax"
    ) == "it1:bc-v6:rollout:2000:7:de1:k5:lmargmax"
    # course_fp 只取前 16 位，且 course-only 形状多一个 `c` 前缀。
    assert dist.task_key(
        "it1", "rollout", "rollout", 2000, 7, "", "0123456789abcdefXYZ"
    ) == "it1:rollout:rollout:2000:7:c0123456789abcdef"


def test_cancel_task_posts_key(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_request(url, auth_key, timeout, data=None, headers=None, method=None):
        calls.append((url, auth_key, timeout, data, headers, method))
        return 200, b"{}"

    monkeypatch.setattr(dist, "_request", fake_request)
    assert dist.cancel_task("http://node:1/", "secret", "it1:rollout:rollout:0:1", 2.0)
    url, auth_key, timeout, data, headers, method = calls[0]
    assert url == "http://node:1/v1/cancel"
    assert auth_key == "secret"
    assert timeout == 2.0
    assert json.loads(data) == {"key": "it1:rollout:rollout:0:1"}
    assert headers == {"Content-Type": "application/json"}
    assert method == "POST"


def test_cancel_task_is_quiet_on_error(monkeypatch) -> None:
    def boom(*_a, **_kw):
        raise OSError("node down")

    monkeypatch.setattr(dist, "_request", boom)
    assert dist.cancel_task("http://node:1", "secret", "key") is False

    monkeypatch.setattr(dist, "_request", lambda *_a, **_kw: (404, b""))
    assert dist.cancel_task("http://node:1", "secret", "key") is False
