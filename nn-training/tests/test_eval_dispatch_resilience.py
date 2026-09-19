"""test_eval_dispatch_resilience — 训练干净评估（C 层）的节点健壮性回归。

2026-09-19 审计（tmp/x20-rebirth/training-loop.log）实测两个真缺陷：
  1. 153 次 HTTP 503(busy) 全额计入 `streaks` —— 瞬断/背压被当节点故障，3 次即把
     该节点熔断整轮（旧实现只豁免 503 的只有 A 层 rollout，C 层什么都没豁免）；
  2. 409「wver not cached here」同样计 streak —— 而它其实是**可刷新**条件
     （节点侧那份归档权重被桶轮换/别的客户端挤掉了），09:48 a97 因此被熔断、
     7 个槽位整轮闲置。

本文件按行为钉住修法：瞬断不计节点故障且任务继续重排；409 就地重发权重后继续用
同一节点。判据本身（dist_common.is_transient_error / refresh_weights）在
test_dist_common_poll.py 有单测。
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

import pytest

import dist_common
import rl.eval_dispatch as ed


class _Harness:
    """单节点干净评估轮的最小脚手架（节点门全放行，fetch 行为可编排）。"""

    def __init__(self, tmp_path: Path, monkeypatch, games: int) -> None:
        self.mp = monkeypatch
        self.work = tmp_path / "c-layer"
        self.work.mkdir()
        self.weights = self.work / "w.json"
        self.weights.write_text('{"arch":{}}', encoding="utf-8")
        self.traj = self.work / "it1"
        self.traj.mkdir()
        self.wver = dist_common.weights_fingerprint(str(self.weights))
        self.logs: list[str] = []
        self.refreshed: list[dict] = []
        self.tasks: list[tuple[int, int]] = []
        self.args = types.SimpleNamespace(
            eval_games_per_stage=games,
            total_stages=1,
            eval_window_sec=30,
            eval_stages="2000-2000",
            max_ticks=10,
            difficulty="hard",
        )
        self.cfg = {
            "nodes": [{"id": "a97", "url": "http://a97.local", "concurrency": 1}],
            "policy": {"nodeFailStreak": 3},
        }
        monkeypatch.setattr(dist_common, "compute_code_hash", lambda: "deadbeef")
        monkeypatch.setattr(
            dist_common,
            "node_ping",
            lambda *a, **k: {
                "evalSupport": True,
                "stageJsonSupport": True,
                "bunVersion": "1.1.0",
                "codeHash": "deadbeef",
                "cpus": 1,
            },
        )
        monkeypatch.setattr(
            dist_common,
            "post_weights_parallel",
            lambda nodes, *a, **k: [
                {"id": n["id"], "url": n["url"], "key": "", "c": 1} for n in nodes
            ],
        )
        monkeypatch.setattr(ed, "bun_version", lambda _bun: "1.1.0")
        monkeypatch.setattr(ed, "log", self.logs.append)

        def fake_refresh(node: dict, **kw: Any) -> bool:
            self.refreshed.append({"node": node["id"], **kw})
            return True

        monkeypatch.setattr(dist_common, "refresh_weights", fake_refresh)

    def run(self, fetch) -> list[dict]:
        """装好假节点（不碰网络）后跑一轮，返回 eval_log.jsonl 全部行。"""
        dist_common.weights_push_cache_reset()
        self.mp.setattr(dist_common, "fetch_task", fetch)
        ed.dispatch_eval_round("bun", str(self.weights), self.traj, self.args, self.cfg, "rid.c", 5)
        rows = [
            json.loads(line)
            for line in (self.work / "eval_log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        return rows

    def manifest(self, stage: int, seed: int) -> dict:
        return {
            "stage": stage,
            "seed": seed,
            "wver": self.wver,
            "mode": "eval",
            "outcome": "timeout",
            "ticks": 10,
            "win": 0,
            "score": 0.1,
            "quality": 0.2,
            "dims": {},
            "elapsedSec": 0.001,
        }


def test_transient_502_does_not_trip_node_and_games_settle(tmp_path, monkeypatch) -> None:
    """连续 3 次 502（隧道抖动）不得熔断节点，也不得丢局（旧实现：丢局 + 熔断）。"""
    h = _Harness(tmp_path, monkeypatch, games=4)
    calls = {"n": 0}

    def fetch(*_a, **kw):
        calls["n"] += 1
        h.tasks.append((kw["stage"], kw["seed"]))
        if calls["n"] <= 3:
            raise dist_common.DistError(502, "")
        return h.manifest(kw["stage"], kw["seed"]), {}

    rows = h.run(fetch)
    played = [r for r in rows if r.get("event") == "eval"]
    assert len(played) == 4, f"应 4 局全结算（旧实现会丢局/熔断）: {[r.get('seed') for r in played]}"
    assert {r["node"] for r in played} == {"a97"}
    summ = [r for r in rows if r.get("event") == "eval_summary"]
    assert summ and summ[-1]["games"] == 4
    joined = "\n".join(h.logs)
    assert "circuit-broken" not in joined  # 瞬断不是节点故障
    assert "瞬断/背压，不计节点故障" in joined


def test_wver_409_reposts_weights_and_keeps_node(tmp_path, monkeypatch) -> None:
    """409 wver-not-cached = 可刷新：就地重发权重，同一节点继续跑（不丢节点/不丢局）。"""
    h = _Harness(tmp_path, monkeypatch, games=1)
    calls = {"n": 0}

    def fetch(*_a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise dist_common.DistError(409, '{"error":"wver not cached here"}')
        return h.manifest(kw["stage"], kw["seed"]), {}

    rows = h.run(fetch)
    played = [r for r in rows if r.get("event") == "eval"]
    assert len(played) == 1 and played[0]["node"] == "a97"
    assert len(h.refreshed) == 1, "409 必须触发一次就地重发"
    assert h.refreshed[0]["node"] == "a97"
    assert h.refreshed[0]["wver"] == h.wver
    assert "circuit-broken" not in "\n".join(h.logs)


def test_hard_failure_still_trips_node(tmp_path, monkeypatch) -> None:
    """真故障（确定性校验失败）仍要停派——瞬断豁免不得顺手废掉护栏。

    节点在 fail_streak_max(=3) 次连续真失败后 worker 返回（旧行为，未改）；
    4 局语料下因此只发生 3 次 fetch，其余任务永不被取走。
    """
    h = _Harness(tmp_path, monkeypatch, games=4)
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        raise dist_common.DistError(0, "validate: wver mismatch")

    rows = h.run(fetch)
    assert [r for r in rows if r.get("event") == "eval"] == []
    assert calls["n"] == 3, f"真故障连续 3 次后应停派（实测 {calls['n']} 次）"


@pytest.mark.parametrize("status", [502, 503, 504, 429, 408])
def test_shared_classifier_covers_tunnel_and_backpressure(status: int) -> None:
    """三层共用判据：隧道/背压状态码一律 transient（不得计节点故障）。"""
    assert dist_common.is_transient_error(dist_common.DistError(status, "")) is True
