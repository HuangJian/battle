"""test_rollout_dispatch_resilience — A 层（rollout）的节点失败分类与轮内回场回归。

2026-09-19 审计（tmp/x20-rebirth/training-loop.log，235 个 rollout 轮）实测：
  · 3 条 HTTP 502（cloudflared 隧道，09:48:13）与 5 条 HTTP 409「wver not cached
    here」（09:48:29）都把节点**当故障熔断**（a97 的 7 个槽位整轮闲置），其中 409
    的同一 wver 在 44 秒前刚成功下发过——那是**可刷新条件**，不是节点坏了；
  · 旧实现只豁免 503（busy），502/10054/超时一概计 streak；
  · **A4**：中途重探线程（`rescan_nodes`）一次都没跑过——首个 pass 要等满
    `agentRescanSec`（120s），而 234 轮全部 <120s（p50 7s / max 115s），线程每轮都在
    首个 sleep 里被结算事件唤醒并退出；且已熔断的节点因 `spawned_ids` 命中被永久跳过。

修法：判据统一到 dist_common（is_transient_error / refresh_weights）；409 走
「就地重发 + 清 reuse 缓存」；瞬时失败不计节点故障（连续软失败仍有上界）；rescan
首个 pass 提前到 `nodeRecoverFirstSec`、ping 并行、并把**已停派节点**也纳入回场
（重置失败计数 + 强制重握手 + 补孵线程，每节点 `nodeRearmLimit` 次上界）。
"""

from __future__ import annotations

import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

import dist_common
import rl.dispatch as disp
import rl.queue_local as ql

GOOD_PING: dict[str, Any] = {
    "codeHash": "deadbeef",
    "bunVersion": "1.1.0",
    "cpus": 1,
    "stageJsonSupport": True,
}


def _node(nid: str, c: int = 1) -> dict[str, Any]:
    return {"id": nid, "url": f"http://{nid}.local", "concurrency": c}


class _Harness:
    """rollout 轮的最小脚手架（不碰网络）。"""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch,
        games: int,
        *,
        nodes: list[dict[str, Any]] | None = None,
        ping_fn=None,
        policy: dict[str, Any] | None = None,
        respawn: bool = True,
    ) -> None:
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
            "nodes": nodes if nodes is not None else [_node("a97")],
            "policy": {
                "nodeFailStreak": 3,
                "queueWindowSec": 30,
                "tailGraceJoinSec": 0,
                # 测试用小节奏（真实缺省 5s / 20s / 5s）
                "nodeRecoverFirstSec": 0.05,
                "recoverPingSec": 0.05,
                # 瞬断/背压退避拉满 5s×N 次是纯 sleep（不占 CPU），把这几个用例
                # 从 15~26.5s 压回 <1s。断言只看取活次数与日志，不看时长。
                "transientBackoffSec": 0.02,
                **(policy or {}),
            },
        }
        self.games = games
        monkeypatch.setattr(dist_common, "compute_code_hash", lambda: "deadbeef")
        monkeypatch.setattr(disp, "bun_version", lambda _bun: "1.1.0")
        monkeypatch.setattr(
            dist_common,
            "node_ping",
            ping_fn or (lambda *a, **k: dict(GOOD_PING)),
        )
        monkeypatch.setattr(dist_common, "post_weights", lambda *a, **k: "kept")
        monkeypatch.setattr(
            dist_common, "validate_result", lambda manifest, files, wver, pairs, seen: ""
        )
        monkeypatch.setattr(dist_common, "write_shard", lambda *a, **k: {})
        monkeypatch.setattr(disp, "log", self.logs.append)
        # rescan 线程在自己的模块里持有 log 引用（rl.queue_local.log），单 patcher 不够。
        monkeypatch.setattr(ql, "log", self.logs.append)

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

    def run(self, fetch, halt_event=None) -> dict:
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
            halt_event=halt_event,
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


def test_hard_failure_trips_then_bounded_rearm(tmp_path, monkeypatch) -> None:
    """确定性真失败仍熔断（瞬断豁免没废掉护栏）；轮内回场有上界，不会无限重试。

    契约：每熔断一次 → 回场一次（3×`nodeFailStreak` 次取活），回场次数达
    `nodeRearmLimit`(=3) 后不再补孵 ⇒ 4 轮 × 3 = 12 次取活，一局也不结算。
    """
    h = _Harness(tmp_path, monkeypatch, games=4)
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        raise dist_common.DistError(0, "validate: bad manifest")

    report = h.run(fetch)
    assert report["dist"]["nodes"] == {}, report["dist"]
    assert calls["n"] == 12, f"熔断+有界回场应共 12 次取活（实测 {calls['n']}）"
    joined = "\n".join(h.logs)
    assert "circuit-broken for this round" in joined
    assert joined.count("回场（此前停派）") == 3
    # 注：本用例里 4 局被 3 次尝试打光 ⇒ 结算集满了整轮就收工，上界告警不会出现；
    # 上界告警由 test_rescan_rearm_bounded_by_limit 钉住。


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


def test_task_lost_404_requeues_without_tripping_node(tmp_path, monkeypatch) -> None:
    """F1：节点重启导致取包丢失（/v1/result 404）⇒ 不计故障、不耗 attempt、清 reuse 账本。

    旧实现把它当确定性失败记 streak ⇒ 3 条就把**刚重启**的节点熔断整轮（`circuit-broken`），
    第 4 次起连取活都不给 ⇒ 整轮 0 局、missing 全满。
    """
    h = _Harness(tmp_path, monkeypatch, games=2)
    forgotten: list[str] = []
    real_forget = dist_common.forget_weights_node

    def spy_forget(nid: str, kind: str | None = None) -> int:
        forgotten.append(nid)
        return real_forget(nid, kind)

    monkeypatch.setattr(dist_common, "forget_weights_node", spy_forget)
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] <= 4:
            raise dist_common.DistError(
                404,
                f"{dist_common.TASK_LOST_MARKER} (restart/purge): "
                '{"error":"unknown task (expired/purged/restart)"}',
            )
        return h.manifest(), {}

    report = h.run(fetch)
    assert report["missing"] == [], report.get("missing")
    assert report["dist"]["nodes"] == {"a97": 2}, report["dist"]
    joined = "\n".join(h.logs)
    assert "circuit-broken" not in joined
    assert "任务丢失（节点重启/清场，已回队、不计故障）" in joined
    assert forgotten.count("a97") >= 1, "404 必须清该节点这条腿的 reuse 账本"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_soft_streak_still_bounded_when_cluster_is_down(tmp_path, monkeypatch, status) -> None:
    """整体脉停（一直是瞬时错误）仍有上界：停派该节点，不把整轮拖到窗口超时。

    契约：软停一次 → 轮内回场一次（`nodeRearmLimit`=3）⇒ 4 轮 × 2 = 8 次取活。

    2026-09-20（墙钟）：`queueWindowSec` 30 → **3**。本用例是**纯空闲等待**——
    回场上界用尽后已无任何可服务的 worker（candidates 空、alive 空），剩余 4 局谁也
    不会去结算，主循环/worker/rescan 三处全部停在 `all_settled.wait(0.5)` 直到 deadline
    （pytest-timeout 线程栈实测，见 docs/nn/engineering.md §14）；即实测的 26.5s = 30s 窗 − 前置。
    窗口在这里只是**配速**（同 nodeRecoverFirstSec/recoverPingSec 的「小节奏」用法），
    断言只看取活次数与漏局集，与窗口长度无关；配小后本用例 ~1.5s（不再 26.5s）。
    注：回场节奏的 1.0s 地板当轮也会随之暴露（旋钮 0.05s 不生效）——已在
    rl/queue_local.py 改为与 wait 同源地板，故 4 轮在这里只需零点几秒。
    （遗留议题：生产缺省窗 1800s，「全员停派且回场用尽」时整轮会空等到窗口——
     已在 §98 记录，属终止语义变更，未擅自改。）
    """
    h = _Harness(
        tmp_path,
        monkeypatch,
        games=4,
        # 窗只要盖住「4 轮×2 取活」的工时（回场地板修好后 ~0.3s）+ 负载余量。
        policy={"nodeSoftFailStreak": 2, "queueWindowSec": 1.5},
    )
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        raise dist_common.DistError(status, "")

    report = h.run(fetch)
    assert calls["n"] == 8, f"软停 + 有界回场应共 8 次取活（实测 {calls['n']}）"
    joined = "\n".join(h.logs)
    assert "连续 2 次瞬时失败" in joined
    assert "circuit-broken" not in joined  # 措辞上必须与真故障区分
    # 停派后有界收场：4 局全部进 missing（喂 volume 补波 / 下轮 resume），不静默丢。
    assert report["dist"]["nodes"] == {}, report["dist"]
    # missing 经 JSON 往返是 list（报告口径），比较前归一为 tuple。
    assert [tuple(m) for m in report["missing"]] == [(2000, i + 1) for i in range(4)], report[
        "missing"
    ]


def test_rescan_discovers_node_online_mid_round(tmp_path, monkeypatch) -> None:
    """A4：开局 ping 失败被挡在门外的节点，轮内首个 pass（不等 120s）就能上线供样。

    事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：原来是「self 每局 `sleep(0.05)`
    让出窗口」——满载时 self 的慢与 a97 的上线谁先发生就成了掷硬币（a97 可能一局都拿不到）。
    现在 self 的第一局**等 a97 真的供满 2 局**才返回：「a97 中途进场并供样」是构造性的，
    旧形态（rescan 等到 120s）下等不到 ⇒ 兜底 20s 后 `by_node[a97] == 0` ⇒ 响亮地红。
    """
    a97 = {"n": 0}

    def ping(url: str, _auth: str = "", timeout: float = 3.0):
        if "a97" in url:
            a97["n"] += 1
            if a97["n"] == 1:
                return None  # 开局未上线（旧实现：本轮永久出局）
        return dict(GOOD_PING)

    h = _Harness(
        tmp_path,
        monkeypatch,
        games=6,
        nodes=[_node("self"), _node("a97", 4)],
        ping_fn=ping,
    )

    served = {"a97": 0}
    a97_served_twice = threading.Event()
    self_gate: list[bool] = []

    def fetch(url: str, *_a, **_kw):
        if "a97" in str(url):
            served["a97"] += 1
            if served["a97"] >= 2:
                a97_served_twice.set()
        elif not self_gate:
            self_gate.append(a97_served_twice.wait(20.0))
        return h.manifest(), {}

    report = h.run(fetch)
    assert report["missing"] == [], report["missing"]
    by_node = report["dist"]["nodes"]
    assert self_gate and self_gate[0], "a97 没能在 self 让位窗口内供满 2 局"
    assert by_node.get("a97", 0) >= 2, f"中途上线节点必须真的供样: {by_node}"
    assert "online mid-run" in "\n".join(h.logs)


def test_rescan_rearms_tripped_node_within_round(tmp_path, monkeypatch) -> None:
    """A4：被熔断的节点在**轮内**回场（重置失败计数 + 重握手 + 补孵），剩余任务照常结算。"""
    h = _Harness(tmp_path, monkeypatch, games=6)
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise dist_common.DistError(0, "validate: bad manifest")  # 真失败 → 熔断
        return h.manifest(), {}

    report = h.run(fetch)
    assert report["missing"] == [], f"回场后剩余任务必须跑完: {report['missing']}"
    assert report["dist"]["nodes"] == {"a97": 6}, report["dist"]
    joined = "\n".join(h.logs)
    assert "circuit-broken for this round" in joined  # 先熔断
    assert "回场（此前停派）" in joined  # 再回场


def test_rescan_rearm_bounded_by_limit(tmp_path, monkeypatch) -> None:
    """回场有上界：`nodeRearmLimit` 次用尽后不再补孵（防「永远失败的节点」无限起线程）。"""
    h = _Harness(
        tmp_path,
        monkeypatch,
        games=6,
        policy={"nodeRearmLimit": 1, "queueWindowSec": 1.5},
    )
    calls = {"n": 0}

    def fetch(*_a, **_kw):
        calls["n"] += 1
        raise dist_common.DistError(0, "validate: bad manifest")

    h.run(fetch)
    # 熔断 3 次 → 回场 1 次（再 3 次）→ 上界用尽，不再补孵。
    assert calls["n"] == 6, f"回场上界失效（实测 {calls['n']} 次取活）"
    assert "回场上界 1 次已用尽" in "\n".join(h.logs)


def test_halt_stops_round_without_waiting_window(tmp_path, monkeypatch) -> None:
    """KL 熔断后不得白等窗口：halt 置位 → worker 退出、回场线程退出、join 不再等满。"""
    h = _Harness(tmp_path, monkeypatch, games=4, policy={"queueWindowSec": 30})
    halt = threading.Event()

    def fetch(*_a, **_kw):
        # 首局一取活就置位（确定性）：旧写法用 0.3s 定时器，隐含前提是「0.3s 时轮
        # 还在跑」——2026-09-20 回场节奏地板修正后轮在 <0.3s 就跑完了，halt 落空、
        # `halt_aborted` 缺失（测试自身的时间依赖，不是生产行为回归）。
        halt.set()
        raise dist_common.DistError(0, "validate: bad manifest")

    t0 = time.monotonic()
    report = h.run(fetch, halt_event=halt)
    elapsed = time.monotonic() - t0
    assert report.get("halt_aborted") is True, report
    assert elapsed < 3.0, f"halt 后不得白等窗口（实测 {elapsed:.1f}s）"
