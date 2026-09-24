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
import threading
import types
from pathlib import Path
from typing import Any

import pytest

import dist_common
import rl.eval_dispatch as ed

#: 慢节点的模拟单局耗时（秒）——用例断言一律与它比，不写死别的绝对数。
SLOW_NODE_SEC = 3.0


def _wait_until(pred: Any, *, timeout: float = 20.0, step: float = 0.02) -> bool:
    """等一个**事件/状态**成立（`timeout` 只是挂起兜底，不是同步手段，2026-09-24）。

    为什么要统一成这个形状：门禁在满载机器上跑时，任何「睡 N 秒后假设对方已经到了」
    都是把调度延迟当失败（04:xx 的 8 次连跑里 4 个不同的用例都因此红过）。等到状态
    成立才继续，既不用赌机器，也不会让断言变成恒真。
    """
    import time

    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


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
    # 重发必须与下发/请求同 kind——否则权重又进 rollout 桶，等于白修（B6）
    assert h.refreshed[0]["kind"] == ed.EVAL_WEIGHTS_KIND
    assert "circuit-broken" not in "\n".join(h.logs)


def test_task_lost_404_requeues_without_tripping_node(tmp_path, monkeypatch) -> None:
    """F1：节点重启导致取包丢失（/v1/result 404）⇒ 不丢局、不熔断、清 reuse 账本。

    旧实现：404 不计瞬断 ⇒ attempt 打光即 `dropped`（丢局）+ 3 次熔断整轮。
    """
    h = _Harness(tmp_path, monkeypatch, games=2)
    forgotten: list[str] = []
    real_forget = dist_common.forget_weights_node

    def spy_forget(nid: str, kind: str | None = None) -> int:
        forgotten.append(nid)
        return real_forget(nid, kind)

    monkeypatch.setattr(dist_common, "forget_weights_node", spy_forget)
    calls = {"n": 0}

    def fetch(*_a, **kw):
        calls["n"] += 1
        h.tasks.append((kw["stage"], kw["seed"]))
        if calls["n"] <= 4:
            raise dist_common.DistError(
                404,
                f"{dist_common.TASK_LOST_MARKER} (restart/purge): "
                '{"error":"unknown task (expired/purged/restart)"}',
            )
        return h.manifest(kw["stage"], kw["seed"]), {}

    rows = h.run(fetch)
    played = [r for r in rows if r.get("event") == "eval"]
    assert len(played) == 2, f"404 不得丢局（旧实现 attempt 打光即 dropped）: {rows}"
    assert {r["node"] for r in played} == {"a97"}
    joined = "\n".join(h.logs)
    assert "circuit-broken" not in joined
    assert "任务丢失·节点重启，已回队、不计故障" in joined
    assert forgotten.count("a97") >= 1, "404 必须清该节点这条腿的 reuse 账本"
    assert h.refreshed == [], "404 不是 409：不得走权重重发路径"


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

# ---------------------------------------------------------------------------
# 2026-09-19 审计 B1–B5（门/收工形态）行为钉：
#   B2 门串行 ping（Σ 每台延迟，两台超时即 ~7s）→ 改并行；
#   B4 ping 失败静默丢弃（日志里看不出是谁/为什么）→ 逐条留痕；
#   B5 权重 POST 全败时无条件 return（本机槽位可用却整轮 0 局）→ 走 local-only；
#   B3 门/权重是屏障：本机槽位干等（本地权重本就在盘上）→ 本机先开工；
#   B1 收工 join(window + taskTimeoutSec)：卡在 HTTP 的线程要等请求自己结束
#      （实测 4–76s/轮，行早已落盘）→ settled 满即断连 + 只等写行的赢家。
# ---------------------------------------------------------------------------


class _LaneHarness:
    """多节点 + 本机槽位脚手架（门/下发/本机直跑均可编排，时间线可断言）。"""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch,
        *,
        games: int,
        nodes: list[dict],
        local_slots: int = 0,
        ping_delay: float = 0.0,
        ping_hook: Any = None,
        ping_fail: tuple[str, ...] = (),
        post_ok: bool = True,
        post_delay: float = 0.0,
        gate_open: bool = True,
        window_sec: float = 60.0,
    ) -> None:
        import threading
        import time

        self.mp = monkeypatch
        self.time = time
        self.work = tmp_path / "lanes"
        self.work.mkdir()
        self.weights = self.work / "w.json"
        self.weights.write_text('{"arch":{}}', encoding="utf-8")
        self.traj = self.work / "it1"
        self.traj.mkdir()
        self.wver = dist_common.weights_fingerprint(str(self.weights))
        self.logs: list[str] = []
        self.pinged: list[str] = []
        self.local_games: list[int] = []
        self.local_seen_at: list[float] = []
        self.gate_done_at: float | None = None
        #: `post_until` 的等待结果（`None` = 该用例没挂条件）；False = 条件在兜底时间内没成立。
        self.post_wait_ok: bool | None = None
        #: 权重门返回的那一刻已经跑起的本机局数（结构事实，与时钟粒度无关）。
        self.post_saw_local = 0
        #: 可选：权重门要**等它成立**再返回（例：等本机首局真的开跑）。构造之后挂，
        #: 这样 lambda 里引用 `h` 自己的属性时类型是确定的（mypy 友好）。
        self.post_until: Any = None
        self.args = types.SimpleNamespace(
            eval_games_per_stage=games,
            total_stages=1,
            eval_window_sec=window_sec,
            eval_stages="2000-2000",
            max_ticks=10,
            difficulty="hard",
        )
        self.cfg = {
            "nodes": nodes,
            "policy": {"evalLocalSlots": local_slots, "nodeFailStreak": 3},
        }
        self.gate = threading.Event()
        self.window_sec = window_sec
        #: `play()` 开跑时刻 —— 用例可以用它表达「窗口已过期」这类**事件**，而不是猜时长。
        self.play_t0 = 0.0
        if gate_open:
            self.gate.set()
        id_of = {str(n["url"]): str(n["id"]) for n in nodes}

        def ping(url: str, _key: str, timeout: float = 3.0) -> dict | None:
            if ping_hook is not None:
                # 事件驱动编排点（例：并行性用 Barrier 证明，而不是比墙钟）
                ping_hook(id_of.get(url, url))
            if ping_delay:
                time.sleep(ping_delay)
            self.pinged.append(id_of.get(url, url))
            if id_of.get(url, url) in ping_fail:
                return None
            return {
                "evalSupport": True,
                "stageJsonSupport": True,
                "bunVersion": "1.1.0",
                "codeHash": "deadbeef",
                "cpus": 1,
            }

        def post(nodes_: list, *a: Any, **k: Any) -> list:
            if self.post_until is not None:
                # 事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：权重门**等条件成立**
                # 再返回（例：等本机首局真的开跑）——「本机不等门」从「机器够快」变成
                # 构造性事实。没等到也照样往下走：`post_wait_ok=False` 让用例响亮地红。
                self.post_wait_ok = _wait_until(self.post_until)
            self.post_saw_local = len(self.local_games)
            if post_delay:
                time.sleep(post_delay)
            self.gate_done_at = time.monotonic()
            if not post_ok:
                return []
            return [
                {"id": n["id"], "url": n["url"], "key": "", "c": n.get("concurrency", 1)}
                for n in nodes_
            ]

        monkeypatch.setattr(dist_common, "compute_code_hash", lambda: "deadbeef")
        monkeypatch.setattr(dist_common, "node_ping", ping)
        monkeypatch.setattr(dist_common, "post_weights_parallel", post)
        monkeypatch.setattr(dist_common, "refresh_weights", lambda *a, **k: True)
        monkeypatch.setattr(ed, "bun_version", lambda _bun: "1.1.0")
        monkeypatch.setattr(ed, "log", self.logs.append)

        def runner(
            _bun, _snap, stage, seed, _out, max_ticks, difficulty, timeout_sec, wver, **kw
        ) -> dict:
            # 本机直跑签名与生产一致（位置 5 + 关键字）：名字必须对齐，否则只会
            # 静默变成「本机局全失败」，门/收工的断言就测不到真东西。
            del max_ticks, difficulty, timeout_sec
            self.local_games.append(seed)
            self.local_seen_at.append(time.monotonic())
            return self.manifest(stage, seed)

        monkeypatch.setattr(ed, "run_local_eval_game", runner)

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

    def play(self, fetch) -> tuple[list[dict], float]:
        """跑一轮（假节点，不碰网络），返回 (全部日志行, 墙钟秒)。"""
        dist_common.weights_push_cache_reset()
        self.mp.setattr(dist_common, "fetch_task", fetch)
        t0 = self.time.monotonic()
        self.play_t0 = t0
        ed.dispatch_eval_round(
            "bun", str(self.weights), self.traj, self.args, self.cfg, "rid.lane", 7,
            local_gate=self.gate,
        )
        elapsed = self.time.monotonic() - t0
        ledger = self.work / "eval_log.jsonl"
        # 整轮跳过（无节点也无本机）时不建账本文件——退回空行集。
        rows = (
            [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if ledger.exists()
            else []
        )
        return rows, elapsed


def _fast_fetch(h):
    def fetch(*_a, **kw):
        return h.manifest(kw["stage"], kw["seed"]), {}

    return fetch


def test_gate_pings_nodes_in_parallel(tmp_path, monkeypatch) -> None:
    """B2：门必须并行探测——3 台各 0.3s，串行 = 0.9s（实测两台超时即 ~7s）。

    事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：判据从「墙钟 < 0.7s」换成
    **`Barrier(3)`** —— 三台必须**同时**进入 ping 才可能一起通过。串行实现里第一个
    ping 永远等不到同伴 ⇒ 屏障超时（`broken` 非空）⇒ 响亮地红。原判据在满载机器上
    会把「并行但被抢占」误判成「串行」（04:xx 连跑里同类墙钟断言红过）。
    """
    nodes = [
        {"id": f"n{i}", "url": f"http://n{i}.local", "concurrency": 1} for i in range(3)
    ]
    barrier = threading.Barrier(len(nodes))
    broken: list[str] = []

    def _hook(nid: str) -> None:
        try:
            barrier.wait(timeout=20.0)  # 兜底：串行时 20s 后 BrokenBarrierError
        except threading.BrokenBarrierError:
            broken.append(nid)

    h = _LaneHarness(
        tmp_path, monkeypatch, games=1, nodes=nodes, local_slots=0, ping_hook=_hook
    )
    rows, _ = h.play(_fast_fetch(h))
    assert not broken, f"门是串行的：这些 ping 没等到同伴（{broken}）"
    assert sorted(h.pinged) == ["n0", "n1", "n2"], "每台都要探到"
    assert any(r.get("event") == "eval" for r in rows)


def test_ping_failure_is_logged_with_node_id(tmp_path, monkeypatch) -> None:
    """B4：ping 失败的节点必须逐条留痕（旧实现静默 continue，排查黑洞）。"""
    nodes = [
        {"id": "a95", "url": "http://a95.local", "concurrency": 1},
        {"id": "a96", "url": "http://a96.local", "concurrency": 1},
    ]
    h = _LaneHarness(
        tmp_path,
        monkeypatch,
        games=1,
        nodes=nodes,
        ping_fail=("a96",),
        local_slots=1,
    )
    h.play(_fast_fetch(h))
    joined = "\n".join(h.logs)
    assert "node a96" in joined and "ping 失败/超时" in joined, joined
    assert "node a95" not in joined


def test_all_weight_posts_failed_falls_back_to_local(tmp_path, monkeypatch) -> None:
    """B5：POST 全败但本机槽位可用 ⇒ 走 local-only（旧实现整轮 0 局 + return）。"""
    nodes = [{"id": "a97", "url": "http://a97.local", "concurrency": 1}]
    h = _LaneHarness(
        tmp_path, monkeypatch, games=2, nodes=nodes, local_slots=2, post_ok=False
    )
    rows, _ = h.play(_fast_fetch(h))
    played = [r for r in rows if r.get("event") == "eval"]
    assert len(played) == 2 and {r["node"] for r in played} == {"local"}, played
    joined = "\n".join(h.logs)
    assert "all weight POSTs failed — local-only eval this round" in joined
    assert "— skipped this round" not in joined


def test_weight_post_failure_without_local_still_skips(tmp_path, monkeypatch) -> None:
    """B5 的反面：本机也不可用时仍须响亮跳过（护栏不许被顺带拆掉）。"""
    nodes = [{"id": "a97", "url": "http://a97.local", "concurrency": 1}]
    h = _LaneHarness(
        tmp_path,
        monkeypatch,
        games=2,
        nodes=nodes,
        local_slots=0,
        post_ok=False,
        gate_open=False,
    )
    rows, _ = h.play(_fast_fetch(h))
    assert [r for r in rows if r.get("event") == "eval"] == []
    assert "all weight POSTs failed — skipped this round" in "\n".join(h.logs)


def test_local_slots_start_before_weight_gate(tmp_path, monkeypatch) -> None:
    """B3：本机槽位不等权重门（本地权重本就在盘上；旧形态里本地首个结果晚于门）。

    事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：原来是 `post_delay=1.0`（假装
    权重下发要 1s）+ `local_seen_at < gate_done_at` —— 满载时本机线程还没被调度，1s
    就过去了 ⇒ 用例红在环境上。现在门**等到本机首局真的开跑**才返回（`post_until`），
    「本机不等门」是构造性的；旧形态（门后才孵化本机线程）会让等待超时 ⇒
    `post_wait_ok=False` ⇒ 红。
    """
    nodes = [{"id": "a97", "url": "http://a97.local", "concurrency": 1}]
    h = _LaneHarness(tmp_path, monkeypatch, games=2, nodes=nodes, local_slots=2)
    h.post_until = lambda: bool(h.local_games)
    rows, _ = h.play(_fast_fetch(h))
    assert len([r for r in rows if r.get("event") == "eval"]) == 2
    assert h.post_wait_ok, "权重门没等到本机首局开跑（旧形态：门后才孵化本机线程）"
    assert h.gate_done_at is not None, "权重门必须跑过"
    assert h.local_seen_at, "本机槽位必须真跑了局"
    # 不用 `local_seen_at < gate_done_at` 判次序：Windows 的 `time.monotonic()` 粒度 ~15.6ms，
    # 事件驱动后两者只差几微秒 ⇒ 两次取样会落到**同一个** tick、`<` 变成掷硬币（2026-09-24
    # 实测：两个值逐位相等）。契约本身（门返回前本机已开跑）由上面的 `post_wait_ok` 钉住。
    assert h.post_saw_local >= 1, "权重门返回时本机还没开跑（旧形态：门后才孵化本机线程）"


def test_settled_full_teardown_does_not_wait_for_slow_node(tmp_path, monkeypatch) -> None:
    """B1：settled 满 → 立即收工（旧实现 join(window + taskTimeoutSec) 会等慢节点）。"""
    nodes = [
        {"id": "fast", "url": "http://fast.local", "concurrency": 1},
        {"id": "slow", "url": "http://slow.local", "concurrency": 1},
    ]
    h = _LaneHarness(tmp_path, monkeypatch, games=4, nodes=nodes, local_slots=0)

    slow_done: list[float] = []
    slow_started = threading.Event()
    fast_gate: list[bool] = []

    def fetch(_url, _key, **kw):
        if _url == "http://slow.local":
            slow_started.set()
            h.time.sleep(SLOW_NODE_SEC)  # 慢节点：回包对结果无用（tail-race 已由快节点结算）
            slow_done.append(h.time.monotonic())
            return h.manifest(kw["stage"], kw["seed"]), {}
        # 快节点的**第一口**先等慢节点真的在跑：否则快节点可能在慢节点线程被调度前
        # 就把 4 局全吃掉（实测如此）——用例的前提（慢节点拿着一局在途）会随机消失。
        if not fast_gate:
            fast_gate.append(slow_started.wait(20.0))
        return h.manifest(kw["stage"], kw["seed"]), {}

    rows, elapsed = h.play(fetch)
    returned_at = h.time.monotonic()
    summ = [r for r in rows if r.get("event") == "eval_summary"]
    assert summ and summ[-1]["games"] == 4, summ
    assert fast_gate and fast_gate[0], "慢节点没能在 20s 内开工（用例前提失效）"
    # 事件驱动（2026-09-24）：判据是**次序**（慢节点那局的回包在收工之后才到 ⇒ 收工没等它；
    # 通常收工时它还在飞，列表直接为空），不是「墙钟 < 2.0s」——那个数只反映机器快慢，
    # 反映不了「等没等」。
    assert not slow_done or min(slow_done) > returned_at, (
        "收工等了慢节点：它的局在 play 返回前就结束了（旧实现 join(window + taskTimeoutSec)）"
    )
    assert elapsed < SLOW_NODE_SEC, f"settled 满后不该等慢节点（实测 {elapsed:.2f}s）"
    assert "settled 满（4/4）" in "\n".join(h.logs)


def test_window_expiry_still_lands_inflight_games(tmp_path, monkeypatch) -> None:
    """B1 的反面：墙钟到点**不砍在飞**——窗口内起跑的局照样落账（有界宽限）。

    事件驱动（2026-09-24）：慢节点的回包现在**等窗口真的过期**才落地（原来是 `sleep(0.6)`
    vs 窗口 1.0s —— 那局其实在窗口内就结算了，「到点仍在飞」这个契约根本没被测到）。
    宽限常量是 120s，所以就算机器满载，`elapsed` 也该、且只该在窗口后一点点就返回。
    """
    nodes = [{"id": "a97", "url": "http://a97.local", "concurrency": 1}]
    h = _LaneHarness(
        tmp_path, monkeypatch, games=1, nodes=nodes, local_slots=0, window_sec=1.0
    )
    past_window: list[bool] = []

    def fetch(*_a, **kw):
        past_window.append(
            _wait_until(
                lambda: h.time.monotonic() - h.play_t0 >= h.window_sec + 0.05, timeout=30.0
            )
        )
        return h.manifest(kw["stage"], kw["seed"]), {}

    rows, elapsed = h.play(fetch)
    played = [r for r in rows if r.get("event") == "eval"]
    assert past_window and past_window[0], "回包没能等到窗口过期（用例前提失效）"
    assert len(played) == 1, f"窗口到点的在飞局必须落账: {rows}"
    assert elapsed < h.window_sec + 5.0, f"宽限不该失控（实测 {elapsed:.2f}s）"


# ---------------------------------------------------------------------------
# B6（2026-09-19）：干净评估的权重 kind 独立成 'eval'，不再蹭训练 rollout 桶。
# 节点按 (kind, wver) 分桶缓存权重；同桶时训练每轮刷 churn 与 eval 那份互相驱逐/清场，
# eval 局随即 409「wver not cached here」，客户端只能靠 409 自愈重发兜底。
# ---------------------------------------------------------------------------


def test_eval_leg_uses_its_own_weights_kind(tmp_path, monkeypatch) -> None:
    """三处 kind 必须同源：权重 POST 的 x-kind、局请求的 ?kind=、409 重发的 kind。

    任一漏传都会取错桶（POST 进 eval 桶、请求去 rollout 桶取 ⇒ 整轮 409；反之亦然）。
    旧实现三处都是 'rollout'（与训练 churn 同桶）。
    """
    h = _Harness(tmp_path, monkeypatch, games=1)
    posted: list[dict] = []
    seen: list[dict] = []

    def spy_post(nodes, *_a, **kw):
        posted.append(kw)
        return [{"id": n["id"], "url": n["url"], "key": "", "c": 1} for n in nodes]

    def fetch(*_a, **kw):
        seen.append(kw)
        return h.manifest(kw["stage"], kw["seed"]), {}

    h.mp.setattr(dist_common, "post_weights_parallel", spy_post)
    rows = h.run(fetch)

    assert ed.EVAL_WEIGHTS_KIND == "eval"  # 节点侧桶名（协议的一部分）
    assert [r for r in rows if r.get("event") == "eval"], "该轮应有远端结算的局"
    assert posted and posted[0]["kind"] == ed.EVAL_WEIGHTS_KIND, posted
    assert seen and seen[0]["kind"] == ed.EVAL_WEIGHTS_KIND, seen


def test_eval_dispatch_kind_is_single_sourced() -> None:
    """源码守卫：C 层不得残留 `kind="rollout"` 字面量（防半途改回 rollout 桶）。"""
    src = Path(ed.__file__).read_text(encoding="utf-8")
    assert 'kind="rollout"' not in src
    assert src.count("EVAL_WEIGHTS_KIND") >= 4  # 定义 1 + 三处使用（POST/请求/重发）
