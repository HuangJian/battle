"""task_key / cancel_task —— 与 agent 的取消键契约（2026-11-10）。"""

from __future__ import annotations

import json

import common.distribution as dist
import common.task_client as task


def test_task_key_matches_agent_vectors() -> None:
    """固定向量与 tools/agent/tests 的 taskKey 用例逐字节一致。"""
    assert dist.task_key(
        dist.TaskKeySpec(iter_id="it1", mode="rollout", kind="rollout", stage=2000, seed=7)
    ) == "it1:rollout:rollout:2000:7"
    assert dist.task_key(
        dist.TaskKeySpec(
            iter_id="it1",
            mode="rollout",
            kind="rollout",
            stage=2000,
            seed=7,
            stage_json_hash="abcdef1234567890",
        )
    ) == "it1:rollout:rollout:2000:7:abcdef1234567890"
    assert dist.task_key(
        dist.TaskKeySpec(
            iter_id="it1",
            mode="rollout",
            kind="rollout",
            stage=2000,
            seed=7,
            course_fp="c0ffee1234567890",
        )
    ) == "it1:rollout:rollout:2000:7:cc0ffee1234567890"
    assert dist.task_key(
        dist.TaskKeySpec(
            iter_id="it1",
            mode="rollout",
            kind="rollout",
            stage=2000,
            seed=7,
            stage_json_hash="abcdef1234567890",
            course_fp="c0ffee1234567890",
        )
    ) == "it1:rollout:rollout:2000:7:abcdef1234567890:c0ffee1234567890"
    assert dist.task_key(
        dist.TaskKeySpec(
            iter_id="it1",
            mode="bc-v6",
            kind="rollout",
            stage=2000,
            seed=7,
            decision_events="de1",
            decision_k="5",
            label_mode="argmax",
        )
    ) == "it1:bc-v6:rollout:2000:7:de1:k5:lmargmax"
    # course_fp 只取前 16 位，且 course-only 形状多一个 `c` 前缀。
    assert dist.task_key(
        dist.TaskKeySpec(
            iter_id="it1",
            mode="rollout",
            kind="rollout",
            stage=2000,
            seed=7,
            course_fp="0123456789abcdefXYZ",
        )
    ) == "it1:rollout:rollout:2000:7:c0123456789abcdef"
    # 9 个推理/语料开关：逐个**单独**激活都要出现在键尾（同型位置位的失误面）。
    def bare(**over: str) -> str:
        return dist.task_key(
            dist.TaskKeySpec(
                iter_id="it1",
                mode="rollout",
                kind="rollout",
                stage=2000,
                seed=7,
                **over,
            )
        )

    for key, tail in (
        (bare(skip_steel_only_shots="1"), ":skipSteelShots"),
        (bare(break_bricks_on_path="1"), ":breakBricks"),
        (bare(suppress_useless_fire="1"), ":suppressUselessFire"),
        (bare(suppress_lead_miss_fire="1"), ":suppressLeadMissFire"),
        (bare(opening_connect="1"), ":hybridOpen"),
        (bare(corpus_fmt="replay"), ":cfr"),
    ):
        assert key == f"it1:rollout:rollout:2000:7{tail}", (key, tail)


def test_cancel_task_posts_key(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_request(url, auth_key, timeout, data=None, headers=None, method=None):
        calls.append((url, auth_key, timeout, data, headers, method))
        return 200, b"{}"

    monkeypatch.setattr(dist, "node_request", fake_request)
    assert task.cancel_task("http://node:1/", "secret", "it1:rollout:rollout:0:1", 2.0)
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

    monkeypatch.setattr(dist, "node_request", boom)
    assert task.cancel_task("http://node:1", "secret", "key") is False

    monkeypatch.setattr(dist, "node_request", lambda *_a, **_kw: (404, b""))
    assert task.cancel_task("http://node:1", "secret", "key") is False
