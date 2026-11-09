"""greedy 分层采集的**全节点透传**回归（2026-11-10）。

事故：o1-gq-* 前 9 轮 `[greedy] ... bins 全 0 / total=0`——greedy 阶段派了局，
但节点侧没拼 `--teacher-argmax` ⇒ manifest 无 `labelMode="greedy"` ⇒
`greedy_progress` 认不出任何一条，训练包里 0 条 greedy 轨迹。

两条通道都要钉住：
  · 队列/self 节点：`fetch_task` 的 URL 参数 `labelMode=greedy`（该键同时进 agent
    taskKey 与异步轮询配方，缓存天然隔离）；
  · `kind=iter` / 半离线模板：hub 拼给节点执行的 argv 必须带 `--teacher-argmax`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.distribution as dist
from worker.iter_job import build_iter_spec


class _BoomError(Exception):
    pass


def _args(**over) -> SimpleNamespace:
    a = SimpleNamespace(
        goal_rollout=False,
        intent_rollout=False,
        max_ticks=6000,
        difficulty="hard",
        dodge="",
        course_obj=None,
        course_path="",
        course_frozen_bytes=None,
    )
    for k, v in over.items():
        setattr(a, k, v)
    return a


def _capture_url(monkeypatch: pytest.MonkeyPatch, **kw) -> str:
    seen: dict[str, str] = {}

    def fake_request(url, auth_key, timeout, headers=None):
        seen["url"] = url
        raise _BoomError()

    monkeypatch.setattr(dist, "_request", fake_request)
    with pytest.raises(dist.DistError):
        dist.fetch_task(
            "http://x/", "k", iter_id="i", wver="w", stage=2000, seed=7,
            max_ticks=100, difficulty="hard", timeout=5.0, **kw,
        )
    return seen["url"]


def test_fetch_task_carries_greedy_label_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """队列节点：仅 greedy 波带 `labelMode=greedy`；普通局 URL 逐字节不变。"""
    assert _capture_url(monkeypatch, label_mode="greedy").count("labelMode=greedy") == 1
    assert _capture_url(monkeypatch).count("labelMode") == 0


def test_poll_url_keeps_greedy_label_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """异步轮询端同配方（漏传 ⇒ /v1/result 的 taskKey 对不上，永远 404）。"""
    seen: dict[str, str] = {}

    def fake_request(url, auth_key, timeout=30.0, **kw):
        seen["url"] = url
        return 202, b'{"status": "running"}'

    monkeypatch.setattr(dist, "_request", fake_request)
    monkeypatch.setattr(dist, "POLL_MIN_BUDGET_SEC", 0.05)
    with pytest.raises(dist.DistError):
        dist._poll_result(
            "http://node", "tok",
            {"iterId": "r.1", "stage": 2000, "seed": 7, "labelMode": "greedy"},
            budget=0.05, poll_s=0.02,
        )
    assert seen["url"].count("labelMode=greedy") == 1


def test_iter_spec_argv_carries_teacher_argmax() -> None:
    """kind=iter 云节点：节点执行的 argv 必须带 flag；普通轮不带。"""
    spec = build_iter_spec(
        _args(rollout_argmax=True), [(2000, 7)], wver="W" * 64,
    )
    argv = spec["argv"][0]
    assert argv.count("--teacher-argmax") == 1
    plain = build_iter_spec(_args(), [(2000, 7)], wver="W" * 64)["argv"][0]
    assert plain.count("--teacher-argmax") == 0


def test_node_capability_gate_fails_closed() -> None:
    """旧 agent 缺 greedyRolloutSupport ⇒ 拒派；新 agent 放行；未激活不查。"""
    greedy = _args(rollout_argmax=True)
    with pytest.raises(dist.DistError, match="greedyRolloutSupport"):
        dist.check_node_capabilities(greedy, "old", {"ping": {}}, stage_json="")
    dist.check_node_capabilities(
        greedy, "new", {"ping": {"greedyRolloutSupport": True}}, stage_json=""
    )
    # 普通采样波：旧节点照派（老行为逐字节不变）。
    dist.check_node_capabilities(_args(), "old", {"ping": {}}, stage_json="")
    # 三个既有能力位仍由同一个入口守着。
    with pytest.raises(dist.DistError, match="decisionKSupport"):
        dist.check_node_capabilities(
            _args(decision_k=5), "old", {"ping": {}}, stage_json=""
        )
