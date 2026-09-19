"""test_rollout_dispatch_resilience — A 层（rollout）的节点失败分类回归。

2026-09-19 审计（tmp/x20-rebirth/training-loop.log，235 个 rollout 轮）实测：
  · 3 条 HTTP 502（cloudflared 隧道，09:48:13）与 5 条 HTTP 409「wver not cached
    here」（09:48:29）都把节点**当故障熔断**（a97 的 7 个槽位整轮闲置），其中 409
    的同一 wver 在 44 秒前刚成功下发过——那是**可刷新条件**，不是节点坏了；
  · 旧实现只豁免 503（busy），502/10054/超时一概计 streak。

修法：判据统一到 dist_common（is_transient_error / refresh_weights）；409 走
「就地重发 + 清 reuse 缓存」；瞬时失败不计节点故障，但连续软失败仍有上界。
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

import dist_common
import rl.dispatch as disp


class _Harness:
    """单节点 rollout 轮的最小脚手架（不碰网络）。"""

    def __init__(self, tmp_path: Path, monkeypatch, games: int, respawn: bool = True) -> None:
        self.mp = monkeypatch
        self.work = tmp_path / "a-layer"
        self.work.mkdir()
        self.weights = self.work / "w.json"
        self.weights.write_text('{"arch":{}}', encoding="utf-8")
        self.traj = self.work / "it1"
        self.traj.mkdir()
        self.logs: list[str] = []
        self.refreshed: list[dict] = []
        self.args = types.SimpleNamespace(
            workers=1,
            max_ticks=10,
            difficulty="hard",
            lives_override=None,
            player_level=None,
            course_obj=None,
        )
        self.cfg: dict[str, Any] = {
            "nodes": [{"id": "a97", "url": "http://a97.local", "concurrency": 1}],
            "policy": {"nodeFailStreak": 3, "queueWindowSec": 30, "tailGraceJoinSec": 0},
        }
        self.games = games
        monkeypatch.setattr(dist_common, "compute_code_hash", lambda: "deadbeef")
        monkeypatch.setattr(disp, "bun_version", lambda _bun: "1.1.0")
        monkeypatch.setattr(
            dist_common,
            "node_ping",
            lambda *a, **k: {
                "codeHash": "deadbeef",
                "bunVersion": "1.1.0",
                "cpus": 1,
                "stageJsonSupport": True,
            },
        )
        monkeypatch.setattr(
            dist_common, "validate_result", lambda manifest, files, wver, pairs, seen: ""
        )
        monkeypatch.setattr(dist_common, "write_shard", lambda *a, **k: {})
        monkeypatch.setattr(disp, "log", self.logs.append)

        def fake_refresh(node: dict, **kw: Any) -> bool:
            self.refreshed.append({"node": node["id"], **kw})
            return True

        monkeypatch.setattr(dist_common, "refresh_weights", fake_refresh)

        def fake_post(
            nodes, iter_id, wver, weights_bytes, timeout, kind="rollout", log=None, on_alive=None
        ):
            out = []
            for n in nodes:
                nd = {
                    "id": n["id"],
                    "url": n["url"],
                    "key": "",
                    "c": int(n.get("c") or 1),
                    "ping": n.get("ping", {}),
                }
                if respawn and on_alive is not None:
                    on_alive(nd)  # 边分发边开采：POST 成功瞬间孵化采样线程
                out.append(nd)
            return out

        monkeypatch.setattr(dist_common, "post_weights_parallel", fake_post)

    def run(self, fetch) -> dict:
        dist_common.weights_push_cache_reset()
        self.mp.setattr(dist_common, "fetch_task", fetch)
        pairs = [(2000, i + 1) for i in range(self.games)]
        return disp.RolloutDispatcher(
            "bun",
            str(self.weights),
            self.traj,
            pairs,
            self.args,
            self.cfg,
            "rid.7",
            local_slots_max=0,
        ).run()

    def manifest(self) -> dict:
        return {
            "outcome": "timeout",
            "ticks": 10,
            "win": 0,
            "score": 0.1,
            "quality": 0.2,
            "dims": {},
            "elapsedSec": 0.001,
            "_dir": str(self.work),
        }


def test_transient_502_does_not_circuit_break_node(tmp_path, monkeypatch) -> None:
    """502 隧道抖动不计节点故障：节点继续供样，全轮 zero missing（旧实现回队即熔断）。"""
    h = _Harness(tmp_path, monkeypatch, games=4)
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise dist_common.DistError(502, "")
        return h.manifest(), {}

    report = h.run(fetch)
    assert report["missing"] == [], report.get("missing")
    assert report["dist"]["nodes"] == {"a97": 4}, report["dist"]
    joined = "\n".join(h.logs)
    assert "circuit-broken" not in joined
    assert "瞬断/背压（不计节点故障）" in joined


def test_hard_failure_still_circuit_breaks(tmp_path, monkeypatch) -> None:
    """确定性真失败（校验不过）仍要熔断——瞬时豁免不得废掉护栏。"""
    h = _Harness(tmp_path, monkeypatch, games=4)
    seen: list[tuple[int, int]] = []

    def fetch(*_a, **kw):
        seen.append((kw["stage"], kw["seed"]))
        raise dist_common.DistError(0, "validate: bad manifest")

    report = h.run(fetch)
    assert report["dist"]["nodes"] == {}, report["dist"]
    assert len(seen) == 3, f"连续 3 次真失败后应熔断停派（实测 {len(seen)} 次取活）"
    assert "circuit-broken for this round" in "\n".join(h.logs)


def test_wver_409_reposts_and_keeps_node(tmp_path, monkeypatch) -> None:
    """409 wver-not-cached = 可刷新：就地重发权重，同一节点继续跑完（不熔断、不丢局）。"""
    h = _Harness(tmp_path, monkeypatch, games=1)
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise dist_common.DistError(409, '{"error":"wver not cached here"}')
        return h.manifest(), {}

    report = h.run(fetch)
    assert report["dist"]["nodes"] == {"a97": 1}, report["dist"]
    assert report["missing"] == []
    assert len(h.refreshed) == 1, "409 必须触发一次就地重发 + 清 reuse 缓存"
    assert h.refreshed[0]["node"] == "a97"
    assert h.refreshed[0]["kind"] == "rollout"
    joined = "\n".join(h.logs)
    assert "circuit-broken" not in joined


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_soft_streak_still_bounded_when_cluster_is_down(tmp_path, monkeypatch, status) -> None:
    """整体脉停（一直是瞬时错误）仍有上界：停派该节点，不把整轮拖到窗口超时。"""
    h = _Harness(tmp_path, monkeypatch, games=4)
    h.cfg["policy"]["nodeSoftFailStreak"] = 2  # 缩短上界便于断言
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        raise dist_common.DistError(status, "")

    h.run(fetch)
    assert calls["n"] == 2, f"软失败上界 2 次后应停派（实测 {calls['n']} 次）"
    assert "连续 2 次瞬时失败" in "\n".join(h.logs)
    assert "circuit-broken" not in "\n".join(h.logs)  # 措辞上必须与真故障区分
