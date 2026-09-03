"""test_run_rl.py — run_rl.py 常驻回归测试（无 torch 训练、不碰真实节点）。

两层：
  快速层（默认）：已迁移到 tests/ 独立文件（test_rl_course / test_rl_reports /
      test_rl_breaker / test_rl_stream / test_rl_resume）。本文件仅保留
      集成层与无法拆出的 fixture-重测试（mirror / resume / jsonl / compute_gae /
      chunk / backup / eval_local_gate / race-tier）。
  集成层（默认运行）：本地假 HTTP agent 节点驱动真 run_rollout_queue /
      run_rollout_stream —— 正常流、halt 流、派发队列清空回调的触发与次序。
      v3.14b 编排化重构（2026-09-03）：无关流程全部 mock —— 权重临时哑文件
      （wver=文件指纹），bunVersion 与 dispatch 同源计算（bun 缺失恒匹配），
      本地直跑 run_local_rollout 打桩 —— 不依赖 PATH 上有 bun、不需要真实权重。

运行（经统一启动器，venv/torch 由它保证）：
  bash nn-training/start-training.sh --script test_run_rl.py
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 -Script test_run_rl.py
  （集成层不再需要 RUN_RL_ITEST 门禁与环境 fixture；RUN_RL_ITEST=1 仍可强制 standalone 入口跑集成层）

退出码：全部通过 0，否则 1。新增队列/流式行为时请在此补用例，不要写临时脚本。
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口弹出抢焦点。
import dist_common
import rl.dispatch as _rdispatch  # monkeypatch 目标：run_local_rollout 的查找命名空间
import run_rl
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.stream import run_rollout_stream as _run_rollout_stream  # B7：run_rl 模块级不再 re-export
from schema import BOARD, FIRE_DIM, MASK_DIM, MOVE_DIM, OBS_CHANNELS, SCALAR_DIM

FAILS: list[str] = []
ITEST = os.environ.get("RUN_RL_ITEST") == "1" or "--itest" in sys.argv


def check(cond: bool, msg: str) -> None:
    global_n = len(FAILS)
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)
        _ = global_n


def test_mirror_scalar_lockstep() -> None:
    """M2 镜像索引锁步：SCALAR_X_INDICES 已迁移为 [15,18]（v2 重编号）——
    mirrorX 前后 (obs, scalars, move) 自洽；旧索引 [20,23] 必须不再翻转（防回归）。"""
    from data.dataset import mirror_x
    from schema import SCALAR_DIM, SCALAR_X_INDICES

    check(SCALAR_X_INDICES == [15, 18], f"SCALAR_X_INDICES == [15,18] (got {SCALAR_X_INDICES})")
    n = 26
    # mirror_x expects (C, H, W) — the trailing (1,) batch dim was a historic typo.
    obs = np.zeros((14, n, n), dtype=np.uint8)
    # Buffer must span legacy slot 23 even when SCALAR_DIM (<24) has dropped those
    # indices — mirror_x only mutates SCALAR_X_INDICES=[15,18], so dead-slot
    # assertions are reachable for every SCALAR_DIM.
    sc = np.zeros(max(SCALAR_DIM, 24), dtype=np.float32)
    sc[15] = 0.5  # nearestEnemyRelX → 翻转
    sc[18] = -0.25  # nearestBaseRelX → 翻转
    sc[20] = 0.7  # 旧索引必须已是死位（不再参与翻转）
    sc[23] = 0.7
    obs2, sc2, mv2 = mirror_x(obs, sc, 3)  # left (3) → right (4)
    check(mv2 == 4, "move left<->right flip")
    check(abs(sc2[15] - (-0.5)) < 1e-6, "scalar[15] flips sign under mirrorX")
    check(abs(sc2[18] - 0.25) < 1e-6, "scalar[18] flips sign under mirrorX")
    check(abs(sc2[20] - 0.7) < 1e-6, "legacy scalar[20] is now a dead slot (no flip)")
    check(abs(sc2[23] - 0.7) < 1e-6, "legacy scalar[23] is now a dead slot (no flip)")


def _mk_shard(traj: Path, stage: int, seed: int, wver: str, *, aggregate: bool = False) -> None:
    d = traj / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    if aggregate:
        mm = {
            "wver": wver,
            "stage": stage,
            "seed": seed,
            "games": 1,
            "outcomes": {"stage_clear": 1},
            "totalSamples": 30,
            "totalTicks": 900,
            "scoreList": [0.5],
            "dimLists": {},
            "node": "fake",
        }
    else:
        mm = {
            "wver": wver,
            "stage": stage,
            "seed": seed,
            "nSamples": 30,
            "ticks": 900,
            "outcome": "stage_clear",
            "score": 0.4,
        }
    (d / "obs.npy").write_bytes(b"\x00")
    (d / "manifest.json").write_text(json.dumps(mm), encoding="utf-8")


def test_resume_scope(tmp: Path) -> None:
    print("[fast] completed_pairs + resumed_manifests (plan scope)")
    traj = tmp / "resume"
    traj.mkdir(parents=True)  # 沙箱零删除适配：tmp 唯一目录，无需预清理
    wver = "a" * 64
    _mk_shard(traj, 0, 111, wver)
    _mk_shard(traj, 3, 222, wver, aggregate=True)
    _mk_shard(traj, 9, 999, wver)  # 计划外残留（跨配置断点）
    _mk_shard(traj, 5, 555, "b" * 64)  # 旧权重代际
    plan = {(0, 111), (3, 222)}
    done_all = run_rl.completed_pairs(traj, wver)
    check(done_all == {(0, 111), (3, 222), (9, 999)}, "done filters by wver only")
    done_plan = done_all & plan
    check(done_plan == {(0, 111), (3, 222)}, "plan intersection")
    rm = run_rl.resumed_manifests(traj, wver, only=plan)
    check(
        {(m["stage"], m["seed"]) for m in rm} == {(0, 111), (3, 222)},
        "resumed honors only=plan (drops off-plan)",
    )
    agg = [m for m in rm if m.get("games") == 1 and m.get("node") == "fake"]
    check(len(agg) == 1, "aggregate-schema manifest passed through")
    legacy = [m for m in rm if m.get("node") != "fake"]
    check(
        len(legacy) == 1 and legacy[0]["games"] == 1 and legacy[0]["totalSamples"] == 30,
        "legacy schema converted to aggregate shape",
    )
    rm_ex = run_rl.resumed_manifests(traj, wver, only=plan, exclude={(0, 111)})
    check({(m["stage"], m["seed"]) for m in rm_ex} == {(3, 222)}, "exclude=seen honored")

    # 吞吐 T4 提前预采：double-wver 对账——本轮 θ_N(wver) 之外还需认上一轮 epoch3
    # 快照（extra_wver）采的首波 shard（预采首波 wver=θ_{N,e3}，否则被当未完成清场）。
    _mk_shard(traj, 7, 777, "e" * 64)  # 模拟预采首波：wver=快照指纹（extra）
    e_done = run_rl.completed_pairs(traj, wver, extra_wver="e" * 64)
    check((7, 777) in e_done, "extra-wver precollected shard counted as done")
    rm_e = run_rl.resumed_manifests(traj, wver, only={(7, 777)}, extra_wver="e" * 64)
    check(len(rm_e) == 1 and rm_e[0]["stage"] == 7, "resumed honors extra-wver shard")
    check(
        (7, 777) not in run_rl.completed_pairs(traj, wver),
        "without extra_wver precollected shard NOT treated as current-θ shard",
    )


def test_jsonl_anchors(tmp: Path) -> None:
    print("[fast] last_completed_iter / last_rotate_seed")
    jl = tmp / "log.jsonl"
    rows = [
        {"event": "run_start", "time": "t0", "rotateSeed": 42},
        {"event": "iteration", "iter": 3},
        {"event": "iter_error", "iter": 4, "error": "x"},
        {"event": "iteration", "iter": 7},
        {"event": "run_start", "time": "t1", "args": {}, "rotateSeed": 42},
        {"event": "circuit_break", "iter": 7},
    ]
    jl.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    check(run_rl.last_completed_iter(jl) == 7, "last iteration event wins (errors ignored)")
    check(run_rl.last_rotate_seed(jl) == 42, "rotateSeed inherited from last run_start")


# ---------------- 集成层（RUN_RL_ITEST=1）----------------


def _npy_bytes(arr: np.ndarray) -> bytes:
    bio = io.BytesIO()
    np.lib.format.write_array(bio, arr)
    return bio.getvalue()


def _synth_payload(n: int = 30) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(11)

    def i64(hi):
        return rng.integers(0, hi, n).astype(np.int64)

    def f32(x):
        return rng.standard_normal(x).astype(np.float32)

    done = np.zeros(n, dtype=np.int64)
    done[-1] = 1
    return {
        "obs": rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8),
        "scalars": f32((n, SCALAR_DIM)),
        "a_move": i64(MOVE_DIM),
        "a_fire": i64(FIRE_DIM),
        "lp_move": -np.abs(f32(n)) - 0.05,
        "lp_fire": -np.abs(f32(n)) - 0.05,
        "value": f32(n),
        # plan/rl-training-config.md §4.2：per-tick 奖励由 Python 奖励引擎计算，
        # metrics.npy [N+1,21] f8 存储——TS 侧不再落 reward.npy（intent 时代遗留）。
        "metrics": np.zeros((n + 1, 21), dtype=np.float64),
        "done": done,
        "mask": np.ones((n, MASK_DIM), dtype=np.int64),
    }


def _pack_container(stage: int, seed: int, wver: str, mode: str | None = None) -> bytes:
    if mode == "eval":
        # eval 局：validate_eval_result 校验的清单（wver/mode/outcome/ticks/win/stage/seed）；
        # 无 shards（eval 不落盘），payload 可省略，直接空。
        manifest = {
            "wver": wver,
            "mode": "eval",
            "stage": stage,
            "seed": seed,
            "outcome": "timeout",
            "ticks": 600,
            "win": 0,
            "elapsedSec": 0.01,
            "node": "fake",
        }
        header_files: list[dict] = []
        body = b""
    else:
        manifest = {
            "wver": wver,
            "stage": stage,
            "seed": seed,
            "games": 1,
            "outcomes": {"stage_clear": 1},
            "totalSamples": 30,
            "totalTicks": 900,
            "scoreList": [0.5],
            "dimLists": {},
            "elapsedSec": 0.01,
            "node": "fake",
        }
        header_files, body = [], b""
        for name, arr in _synth_payload().items():
            fname = f"{name}.npy"
            raw = _npy_bytes(arr)
            header_files.append({"name": fname, "len": len(raw)})
            body += (
                struct.pack(">H", len(fname)) + fname.encode() + struct.pack(">Q", len(raw)) + raw
            )
    header = json.dumps({"manifest": manifest, "files": header_files}).encode()
    return gzip.compress(
        struct.pack(">I", 0x42435632) + struct.pack(">I", len(header)) + header + body
    )


class FakeServer(ThreadingHTTPServer):
    """v3.15：把 FakeAgent 的状态从类变量移到 server 实例，使 xdist 按函数分发时
    各测试的 FakeAgent 状态完全隔离（不再跨 worker 竞争）。
    FakeAgent handler 通过 self.server 访问这些实例属性。"""

    def __init__(self, *args, **kwargs) -> None:
        self.events: list[tuple[str, float, tuple]] = []
        self.slow_first: set[tuple[int, int]] = set()
        self._slowed_once: set[tuple[int, int]] = set()
        self.eval_delay: float = 0.0
        self.eval_dispatched: threading.Event = threading.Event()
        super().__init__(*args, **kwargs)


class FakeAgent(BaseHTTPRequestHandler):
    # _ping_cache 留在类变量：codeHash + bunVersion 只计算缓存，跨 server 共享无害
    _ping_cache: dict[str, str] = {}

    @property
    def _srv(self) -> FakeServer:
        """类型收窄：self.server 是 BaseServer，但运行时必为 FakeServer（mypy 收窄）。"""
        return self.server  # type: ignore[return-value]  # FakeServer 在 FakeAgent 之前定义

    def log_message(self, *_a) -> None:
        return

    def _json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/v1/ping":
            cache = FakeAgent._ping_cache
            if "bun" not in cache:
                bun = shutil.which("bun")
                if bun is not None:
                    cache["bun"] = (
                        subprocess.run(
                            [bun, "--version"],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            **_POPEN_NO_WINDOW,
                        ).stdout.strip()
                        or "?"
                    )
                else:
                    # 与 dispatch.bun_version(缺失) 的失败回落同 "?" → mm 门恒匹配
                    cache["bun"] = "?"
            if "codeHash" not in cache:
                cache["codeHash"] = dist_common.compute_code_hash()
            self._json(
                {
                    "codeHash": cache["codeHash"],
                    "bunVersion": cache["bun"],
                    "cpus": 4,
                    "evalSupport": True,
                }
            )
        elif u.path == "/v1/task":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            key = (int(q["stage"]), int(q["seed"]))
            self._srv.events.append(("dispatch", time.time(), key))
            if key in self._srv.slow_first and key not in self._srv._slowed_once:
                self._srv._slowed_once.add(key)
                time.sleep(0.4)  # 慢主副本窗口（v3.15 2.0→0.4，判据不依赖窗长）
            if q.get("mode") == "eval" and self._srv.eval_delay > 0:
                self._srv.eval_dispatched.set()  # I7 栅栏：eval 已派发（首局即置位）
                time.sleep(self._srv.eval_delay)  # I7 慢 eval（后台消化模拟）
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            body = _pack_container(*key, q["wver"], mode=q.get("mode"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "nf"})

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._srv.events.append(("weights", time.time(), ()))
        self._json({"cache": "kept"})


class _StubPpo:
    """I3/I4 集成桩：mock PPO 更新——编排测试（wave/eval 时机/熔断/停派发）不需要真 torch
    权重。只消费 chunks 数量并回可控 agg（kl 可调），stream 的 steps/chunks/waves 计数仍由
    其自身维护（rl/stream.py _drain），本桩只决定「每次更新回什么指标」。

    backend 依赖接口（run_rollout_stream 的 backend 参数即注入点，默认 ppo_mod）：
      update / _ppo_load / load_episodes / chunk_episodes —— 按需实现。"""

    def __init__(self, kl: float = 0.01):
        self.kl = float(kl)
        self.updates = 0

    def update(self, model, opt, chunks, epochs, device, ckpt_path=None, **kw):
        self.updates += 1
        return {
            "kl": self.kl,
            "entropy": 1.0,
            "policy": 0.0,
            "value": 0.0,
            "mean_ret": 0.5,
            "gnorm": 1.0,
        }

    def _ppo_load(self, path, model, opt) -> int:
        return 0  # 无 checkpoint 路径 → 轮内零结算走全盘更新分支

    def load_episodes(self, path):
        # full-disk 回放路径（无 fresh settles 时）→ ep["obs"].shape[0] 即 steps
        return [{"obs": np.zeros(30, dtype=np.uint8)}]

    def chunk_episodes(self, eps, mb):
        return list(eps)

    def load_episode_from_shard(self, shard_dir):
        # stream._load_wave 只要求 ep["adv"]（做 wave 内归一化）；具体轨迹数值不重要
        # ——rollout 引擎的真实性由 tools/sim/export-rl-rollout.ts 的 TS 测试保证。
        return {"adv": np.zeros(1, dtype=np.float32)}


def _stub_local_rollout(
    bun: str, rl_path: str, traj_dir: Path, idx: int, task: tuple[int, int], args, wver: str
) -> dict:
    """本地直跑桩（v3.14b 编排化重构）：返回与 export-rl-rollout 报告同构的最小
    summary（win_of / by_node 消费的字段），并写盘最小 shard 文件使 stream 的
    _shard_dir 能检测到 obs.npy，避免 _load_wave 因目录空而跳过。"""
    si, sd = task
    out_dir = Path(traj_dir) / f"w{idx}" / f"rl_s{si}_seed{sd}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 写最小 obs.npy（30 steps × 14 channels × 26×26 零数组）和 manifest.json
    np.save(str(out_dir / "obs.npy"), np.zeros((30, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(str(out_dir / "scalars.npy"), np.zeros((30, SCALAR_DIM), dtype=np.float32))
    for arr_name in ("a_move", "a_fire", "lp_move", "lp_fire", "value", "done", "mask"):
        arr = _synth_payload(30).get(arr_name)
        if arr is not None:
            np.save(str(out_dir / f"{arr_name}.npy"), arr)
    (out_dir / "manifest.json").write_text(json.dumps({
        "wver": wver, "stage": si, "seed": sd, "nSamples": 30, "ticks": 900,
        "outcome": "stage_clear", "score": 0.4,
    }), encoding="utf-8")
    return {
        "node": "local",
        "wver": wver,
        "stage": si,
        "seed": sd,
        "games": 1,
        "outcomes": {"stage_clear": 1},
        "totalSamples": 30,
        "totalTicks": 900,
        "scoreList": [0.5],
        "dimLists": {},
        "elapsedSec": 0.01,
        "_dir": str(out_dir),
    }


def _itest_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[FakeServer, Path, dict, types.SimpleNamespace, str]:
    """为集成子测试拉起 FakeServer + 本地直跑打桩。调用方负责 try/finally 关闭 server。"""
    weights = tmp_path / "weights.json"
    weights.write_text('{"stub": true}')
    monkeypatch.setattr(_rdispatch, "run_local_rollout", _stub_local_rollout)
    srv = FakeServer(("127.0.0.1", 0), FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {
        "nodes": [{"id": "fake", "url": f"http://127.0.0.1:{srv.server_address[1]}",
                   "authKey": "", "concurrency": 4, "enabled": True}],
        "policy": {"taskTimeoutSec": 60, "queueWindowSec": 120, "statusTimeoutSec": 3, "agentRescanSec": 1},
    }
    args = types.SimpleNamespace(workers=4, max_ticks=300, difficulty="hard")
    bun = shutil.which("bun") or "bun-stub"
    return srv, weights, cfg, args, bun


@pytest.mark.heavy
def test_it_queue_normal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i1"
        traj.mkdir()
        drained_ts, result_ts = [], []
        rep = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i1.1",
            local_slots_max=0,
            on_result=lambda _s: result_ts.append(time.time()),
            on_queue_drained=lambda: drained_ts.append(time.time()),
        )
        wts = [t for kind, t, _x in srv.events if kind == "weights"]
        disp_pairs = {p for kind, _t, p in srv.events if kind == "dispatch"}
        check(rep.get("missing") == [] and rep["games"] == 2, "I1 settled fully")
        check(rep.get("dist_phase_sec") is not None, "I1 dist_phase_sec present")
        check(len(drained_ts) == 1, f"I1 queue-drained fired once (got {len(drained_ts)})")
        check(bool(wts) and drained_ts[0] > wts[0], "I1 drained after weight distribution")
        check(disp_pairs == {(0, 111), (3, 222)}, "I1 all pairs still dispatched")
        check(rep["dist"]["offPlanShards"] == 0, "I1 no off-plan shards")
    finally:
        srv.shutdown()


@pytest.mark.heavy
def test_it_halt_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i2"
        traj.mkdir()
        ev = threading.Event()
        ev.set()
        calls = []
        rep2 = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i2.2",
            local_slots_max=0, halt_event=ev,
            on_queue_drained=lambda: calls.append(1),
        )
        check(rep2.get("halt_aborted") is True, "I2 halt_aborted flagged")
        check(len(rep2["missing"]) == 2 and rep2["games"] == 0, "I2 nothing dispatched/settled")
        check(calls == [], "I2 queue-drained NOT fired under pre-set halt")
    finally:
        srv.shutdown()


@pytest.mark.heavy
def test_it_stream_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i3"
        traj.mkdir()
        stub3 = _StubPpo(kl=1e-12)
        cfg3 = json.loads(json.dumps(cfg))
        cfg3["policy"]["streamWaveGames"] = 2
        cfg3["policy"]["streamKlCap"] = 1e12
        fired = []

        def on_collect_done():
            fired.append(time.time())
            th = threading.Thread(target=lambda: None)
            th.start()
            return th

        rep3 = _run_rollout_stream(
            bun, str(WEIGHTS), traj, [(0, 111), (0, 222), (1, 333), (1, 444)],
            types.SimpleNamespace(**{**vars(args), "epochs": 1, "mb": 64}),
            cfg3, "i3.3", None, None, "cpu",
            on_collect_done=on_collect_done, backend=stub3,
        )
        sm = rep3.pop("_stream")
        check(rep3["games"] == 4 and sm["waves"] >= 1, "I3 streamed round trained")
        check(stub3.updates >= 1, f"I3 stub PPO invoked for waves ({stub3.updates})")
        check(sm["halted"] is False and sm["dropped_games"] == 0, "I3 no halt (cap high)")
        check(len(fired) == 1, f"I3 eval fired exactly once (got {len(fired)})")
        wts3 = [t for kind, t, _x in srv.events if kind == "weights"]
        check(bool(wts3) and fired[0] > wts3[0], "I3 eval after weight distribution (queue-drained)")
        check("_eval_thread" in rep3, "I3 eval thread returned via report")
    finally:
        srv.shutdown()


@pytest.mark.heavy
def test_it_stream_halt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i4"
        traj.mkdir()
        stub4 = _StubPpo(kl=1e6)
        cfg4 = json.loads(json.dumps(cfg))
        cfg4["policy"]["streamWaveGames"] = 2
        cfg4["policy"]["streamKlCap"] = 1e-6
        rep4 = _run_rollout_stream(
            bun, str(WEIGHTS), traj, [(0, 111), (0, 222), (1, 333), (1, 444)],
            types.SimpleNamespace(**{**vars(args), "epochs": 1, "mb": 64}),
            cfg4, "i4.4", None, None, "cpu", on_collect_done=None, backend=stub4,
        )
        sm4 = rep4.pop("_stream")
        check(sm4["halted"] is True, f"I4 halted (cum_kl={sm4['kl_cum']:.1f})")
        check(sm4["waves"] >= 1 and "_eval_thread" not in rep4, "I4 coherent without eval cb")
    finally:
        srv.shutdown()


@pytest.mark.heavy
def test_it_local_suspend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        pairs5 = [(0, 111), (3, 222)]
        traj = tmp_path / "i5a"
        traj.mkdir()
        susp = threading.Event()
        susp.set()
        rep5a = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, pairs5, args, cfg, "i5.51",
            local_slots_max=2, local_suspend=susp,
        )
        check(rep5a["games"] == 2 and "local" not in rep5a["dist"]["nodes"],
              f"I5 suspended -> zero local settlements (byNode={rep5a['dist']['nodes']})")
        traj = tmp_path / "i5b"
        traj.mkdir()
        rep5b = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, pairs5, args, cfg, "i5.52", local_slots_max=2,
        )
        check(rep5b["games"] == 2 and rep5b["dist"]["nodes"] == {"local": 2},
              f"I5b no-suspend -> local owns head tasks ({rep5b['dist']['nodes']})")
    finally:
        srv.shutdown()


@pytest.mark.heavy
def test_it_longtail_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i6"
        traj.mkdir()
        srv.slow_first = {(0, 111)}
        srv._slowed_once = set()
        t0 = time.time()
        rep6 = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i6.6", local_slots_max=0,
        )
        took6 = time.time() - t0
        n_slow_disp = sum(1 for kind, _t, p in srv.events if kind == "dispatch" and p == (0, 111))
        check(rep6["games"] == 2 and rep6["missing"] == [], "I6 long-tail race settled all")
        check(n_slow_disp >= 2, f"I6 slow task re-raced by idle slot (dispatches={n_slow_disp})")
        check(took6 < 40.0, f"I6 round not stalled on slow copy ({took6:.1f}s)")
    finally:
        srv.slow_first = set()
        srv._slowed_once = set()
        srv.shutdown()


@pytest.mark.heavy
def test_it_eval_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rl.eval_dispatch as ed
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i7"
        traj.mkdir()
        srv.eval_delay = 2.0
        args_eval = types.SimpleNamespace(**{**vars(args), "eval_games_per_stage": 2, "eval_stages": "0-2"})
        eval_th = threading.Thread(
            target=lambda: ed.dispatch_eval_round(bun, str(WEIGHTS), traj, args_eval, cfg, "i7.e", 10),
            daemon=True, name="eval-it10",
        )
        eval_th.start()
        srv.eval_dispatched.clear()
        if not srv.eval_dispatched.wait(timeout=3.0):
            raise AssertionError("I7 eval round never dispatched a game")
        t_collect = time.time()
        rep7 = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i7.9", local_slots_max=0,
        )
        t_collect = round(time.time() - t_collect, 1)
        check(rep7["games"] == 2 and rep7["missing"] == [],
              f"I7 collection completes while slow eval in flight ({t_collect}s)")
        check(eval_th.is_alive(), "I7 slow eval still running afterwards (deferred to background)")
        eval_th.join(timeout=45)
    finally:
        srv.eval_delay = 0.0
        srv.shutdown()


@pytest.mark.heavy
def test_it_precollect_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i8"
        traj.mkdir()
        plan8 = [(0, 111), (3, 222)]
        pre_pair, cur_pair = (0, 111), (3, 222)
        _mk_shard(traj, *pre_pair, "e" * 64)
        args8 = types.SimpleNamespace(**{**vars(args), "max_ticks": 600, "mb": 128, "epochs": 1, "seed": 7})
        cfg8 = json.loads(json.dumps(cfg))
        cfg8["policy"]["streamWaveGames"] = 1
        stub8 = _StubPpo(kl=1e-12)
        rep8 = _run_rollout_stream(
            bun, str(WEIGHTS), traj, plan8, args8, cfg8, "i8.8",
            None, None, None, backend=stub8, extra_wver="e" * 64,
        )
        disp8 = [p for kind, _t, p in srv.events if kind == "dispatch"]
        check(stub8.updates >= 2,
              f"I8 precollected shard trained as first wave + rest collected (updates={stub8.updates})")
        check(pre_pair not in disp8 and cur_pair in disp8,
              f"I8 collector skips precollected pair, dispatches only remaining ({disp8})")
        check(rep8["games"] == 2, f"I8 report covers full plan (games={rep8['games']})")
    finally:
        srv.shutdown()


@pytest.mark.heavy
def test_it_early_race_v314(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, WEIGHTS, cfg, args, bun = _itest_env(monkeypatch, tmp_path)
    try:
        traj = tmp_path / "i9"
        traj.mkdir()
        plan9 = [(0, 111)] + [(s % 4, 9000 + i) for i, s in enumerate(range(1, 8))]
        srv.slow_first = {(0, 111)}
        srv._slowed_once = set()
        t0 = time.time()
        rep9 = run_rl.run_rollout_queue(bun, str(WEIGHTS), traj, plan9, args, cfg, "i9.9", local_slots_max=0)
        took9 = time.time() - t0
        n_disp9 = sum(1 for kind, _t, p in srv.events if kind == "dispatch" and p == (0, 111))
        check(rep9["games"] == 8 and rep9["missing"] == [], "I9 all 8 settled")
        check(n_disp9 >= 2, f"I9 early-dispatched slow task re-raced by idle slot (dispatches={n_disp9})")
        race_at = [t for kind, t, p in srv.events if kind == "dispatch" and p == (0, 111)]
        check(len(race_at) >= 2 and race_at[1] - t0 < 0.5,
              f"I9 race copy dispatched inside slow window (+{race_at[-1] - t0:.2f}s < 0.5s)")
        check(took9 < 5.0, f"I9 round terminates ({took9:.2f}s)")
    finally:
        srv.slow_first = set()
        srv._slowed_once = set()
        srv.shutdown()


def test_compute_gae() -> None:
    import numpy as np

    import ppo as ppo_mod

    print("[fast] ppo.compute_gae (手算用例)")
    rewards = np.array([1.0, 0.0])
    values = np.array([0.5, 0.8])
    dones = np.array([0, 1])
    adv, ret = ppo_mod.compute_gae(rewards, values, dones, gamma=0.99, lam=0.95)
    # t=1: delta=-0.8（终止，无 bootstrap）→ adv1=-0.8
    # t=0: delta=1+0.99*0.8-0.5=1.292 → adv0=1.292+0.99*0.95*(-0.8)=0.5396
    check(abs(adv[1] - (-0.8)) < 1e-6, "terminal step advantage == delta")
    check(abs(adv[0] - 0.5396) < 1e-6, f"bootstrapped advantage (got {adv[0]:.6f})")
    # 手算期望：ret = adv + values = [1.0396, 0.0]；显式容差吸收 float32 舍入
    check(np.allclose(ret, [1.0396, 0.0], atol=1e-6), f"returns hand-computed (got {ret})")


def test_chunk_episodes() -> None:
    import ppo as ppo_mod

    print("[fast] ppo.chunk_episodes (ragged 尾巴)")
    eps = [{"obs": np.zeros((1000, 2)), "adv": np.arange(1000)}]
    chs = ppo_mod.chunk_episodes(eps, 600)
    sizes = [c["obs"].shape[0] for c in chs]
    check(sizes == [600, 400], f"ragged tail split (got {sizes})")
    total = sum(c["obs"].shape[0] for c in chs)
    check(total == 1000, "no samples lost")
    check(all(set(c.keys()) == set(eps[0].keys()) for c in chs), "keys preserved per chunk")


def test_backup_weights(tmp: Path) -> None:
    import run_rl
    from rl import archive as rl_archive

    print("[fast] backup_weights (归档；只归档不自动清理——2026-09-02 用户指令)")
    bdir = tmp / "weights-archive"
    src = tmp / "src-weights.json"  # 沙箱零删除适配：tmp 唯一目录，无需预清理
    src.write_text("{}", encoding="utf-8")
    old_dir = rl_archive.WEIGHTS_BACKUP_DIR
    rl_archive.WEIGHTS_BACKUP_DIR = bdir
    try:
        for it in (1, 2, 3):
            out = rl_archive.backup_weights(str(src), it)
            check(out is not None, f"backup it{it} returned path")
        remain = sorted(p.name.split(".")[1] for p in bdir.glob("rl-weights.it*.json"))
        check(remain == ["it1", "it2", "it3"], f"只归档不清理（got {remain}）")
    finally:
        rl_archive.WEIGHTS_BACKUP_DIR = old_dir
def test_eval_local_gate(tmp: Path) -> None:
    """R6 补丁：eval 本地参与——gate 放行后本机直跑全部/尾局；gate 不放行则让位。"""
    import rl.eval_dispatch as ed

    work = tmp / "eval-local"
    work.mkdir(parents=True, exist_ok=True)  # 沙箱零删除适配：tmp 唯一目录，无需预清理
    rl = work / "w.json"
    rl.write_text('{"arch":{}}', encoding="utf-8")

    def mk_args(window: float):
        return types.SimpleNamespace(
            eval_games_per_stage=2,
            total_stages=3,
            eval_window_sec=window,
            max_ticks=10,
            difficulty="hard",
        )

    cfg = {"nodes": [], "policy": {"evalLocalSlots": 2}}
    calls: list[tuple[int, int]] = []

    def fake_runner(
        bun, snap, stage, seed, out_dir, max_ticks, difficulty, timeout_sec, wver,
        stage_json="", lives_override=None, player_level=None,
    ):
        calls.append((stage, seed))
        assert Path(snap).read_text(encoding="utf-8") == '{"arch":{}}'
        return {
            "stage": stage,
            "seed": seed,
            "outcome": "timeout",
            "ticks": 10,
            "win": 0,
            "score": 0.1,
            "quality": 0.2,
            "dims": {},
            "elapsedSec": 0.001,
            "wver": wver,
            "mode": "eval",
        }

    orig = ed.run_local_eval_game
    ed.run_local_eval_game = fake_runner
    try:
        # 预留判定纯函数：gate/宽限期放行、余量边界、零预留回退旧路径
        check(not ed.hold_for_local(6, 2, True, False), "reserve: gate set → no hold")
        check(ed.hold_for_local(2, 2, False, False), "reserve: within tail → hold for local")
        check(not ed.hold_for_local(3, 2, False, False), "reserve: above tail → nodes flow")
        check(not ed.hold_for_local(2, 2, False, True), "reserve: release grace forces flow")
        check(not ed.hold_for_local(0, 2, False, False), "reserve: empty pending → no hold")
        check(not ed.hold_for_local(6, 0, False, False), "reserve: 0 slots → legacy behavior")
        # A：gate 放行（无可用节点）→ 3 关 ×2 种子全部由 local 结算并聚合进 summary
        traj_a = work / "trajA"
        gate = threading.Event()
        gate.set()
        ed.dispatch_eval_round(
            "bun", str(rl), traj_a, mk_args(30), cfg, "rid.1", 1, local_gate=gate
        )
        rows = [
            json.loads(line)
            for line in (work / "eval_log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        eval_rows = [r for r in rows if r.get("event") == "eval"]
        summ = [r for r in rows if r.get("event") == "eval_summary"]
        check(
            len(calls) == 6 and len(eval_rows) == 6,
            f"gate set → all 6 games ran locally (calls={len(calls)}, rows={len(eval_rows)})",
        )
        check(
            all(r.get("node") == "local" for r in eval_rows), "ledger rows attributed to node=local"
        )
        check(
            bool(summ)
            and summ[-1]["games"] == 6
            and summ[-1]["nodes"] == {"local": 6}
            and summ[-1]["dropped"] == 0,
            "summary aggregates local-only round",
        )
        check(
            (traj_a / "_eval_frozen_weights.json").exists(),
            "frozen weights snapshot written into traj_dir",
        )
        # 采样机健康账本：eval 局同册入账（mode:"eval"），喂「采样机健康」表指标
        meta_rows = [
            json.loads(line)
            for line in (work / "dist-agent-meta.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        check(
            len(meta_rows) == 6
            and all(
                r.get("mode") == "eval" and r.get("ok") and r.get("node") == "local"
                for r in meta_rows
            ),
            f"meta ledger records 6 eval games ({len(meta_rows)} rows)",
        )
        # B：gate 从不放行 → runner 零新增调用，窗口到期自然收场（不挂死、不越权训练侧）。
        # 用不同权重文件（不同 wver）确保 todo 非空，真正进入关门等待路径。
        baseline_calls = len(calls)
        traj_b = work / "trajB"
        rl_b = work / "w2.json"
        rl_b.write_text('{"arch":{"h":32}}', encoding="utf-8")
        gate_open_never = threading.Event()
        t0 = time.time()
        ed.dispatch_eval_round(
            "bun", str(rl_b), traj_b, mk_args(1), cfg, "rid.2", 2, local_gate=gate_open_never
        )
        took = time.time() - t0
        check(len(calls) == baseline_calls, "gate closed → local runner never invoked")
        check(took < 40, f"closed-gate round exits at window ({took:.1f}s)")  # 并行门禁放宽
        rows_b = [
            json.loads(line)
            for line in (work / "eval_log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        summ_b = [r for r in rows_b if r.get("event") == "eval_summary" and r.get("iter") == 2]
        check(
            bool(summ_b) and summ_b[-1]["games"] == 0 and summ_b[-1]["dropped"] == 6,
            "closed-gate round settles nothing, all dropped",
        )
    finally:
        ed.run_local_eval_game = orig


def test_race_tier_ok() -> None:
    """v3.11 竞速派档：慢节点不浪费竞速名额（用户"副本落到慢节点=白等"）。"""
    from rl.queue import race_tier_ok

    spd = {"a": 5.0, "b": 20.0, "c": 8.0, "d": 60.0, "e": 12.0}
    # 按耗时升序：a(5) < c(8) < e(12) < b(20) < d(60) → top-3 = {a, c, e}
    check(race_tier_ok(spd, "a", 3) is True, "fastest in top-3")
    check(race_tier_ok(spd, "c", 3) is True, "2nd-fast in top-3")
    check(race_tier_ok(spd, "e", 3) is True, "3rd-fast in top-3")
    check(race_tier_ok(spd, "b", 3) is False, "4th-fast excluded (b=20)")
    check(race_tier_ok(spd, "d", 3) is False, "slowest excluded (d=60)")
    check(race_tier_ok(spd, "local", 3) is True, "local always races (no RPC)")
    check(race_tier_ok({}, "a", 3) is True, "no speed data -> optimistic pass")
    check(
        race_tier_ok({"a": 5.0, "b": 9.0}, "b", 3) is True,
        "node count <= top_n -> all in tier (degenerate no-gate)",
    )


def test_register_inflight_v314() -> None:
    """v3.14 主副本派发一律登记（it6 教训 2026-09-03：v3.7 只登记尾部，早派到
    慢节点的任务对 pick_tail_race 不可见，空闲槽无从竞速，整轮空等）。"""
    from rl.queue import pick_tail_race, register_inflight

    inflight: dict[tuple[int, int], int] = {}
    register_inflight(inflight, (2000, 612570782))  # 早派（pending > tailFanoutN）也登记
    register_inflight(inflight, (2002, 179564083))
    check(inflight[(2000, 612570782)] == 1, "first dispatch registers at count=1")
    register_inflight(inflight, (2000, 612570782))  # 失败 requeue 后再派发 → 计数累加
    check(inflight[(2000, 612570782)] == 2, "re-dispatch accumulates copy count")
    # 登记即竞速候选：早派任务立即能被 pick_tail_race 选中（副本数 dup=2 内）
    check(
        pick_tail_race(inflight, 2) == (2002, 179564083),
        "early-dispatched non-full task is raceable (dup-full one skipped)",
    )
    check(pick_tail_race(inflight, 3) == (2000, 612570782), "under higher dup the smaller key wins")


def test_pick_tail_race() -> None:
    """v3.10 长尾竞速选择纯函数（queue.pick_tail_race，用户裁定"有空槽就派发"）。"""
    from rl.queue import pick_tail_race

    check(pick_tail_race({}, 2) is None, "empty inflight -> None")
    check(pick_tail_race({(1, 2): 1}, 2) == (1, 2), "single raceable task picked")
    check(pick_tail_race({(1, 2): 2}, 2) is None, "dup full -> None")
    check(pick_tail_race({(1, 2): 1}, 1) is None, "dup=1 disables racing")
    check(
        pick_tail_race({(5, 9): 2, (3, 4): 1, (0, 7): 1}, 2) == (0, 7),
        "lexicographically smallest raceable task picked",
    )
    check(
        pick_tail_race({(0, 7): 2, (3, 4): 1}, 2) == (3, 4), "only non-full in-flight task picked"
    )


def test_pick_race_target_v316() -> None:
    """v3.16 竞速选靶：排除当前节点+冷却黑名单（纯函数，单测覆盖）。

    规则验证：
    - 不选当前节点已持有
    - 不选冷却黑名单里的节点
    - 选副本数 < dup，字典序最小
    """
    from rl.queue import pick_race_target

    inflight = {(2000, 1): 1, (2000, 2): 1}
    nodes = {(2000, 1): {"a"}, (2000, 2): {"a"}}
    blocked: dict[tuple[int, int], set[str]] = {(2000, 1): {"b"}}
    cand = pick_race_target(inflight, 2, "b", nodes, blocked)
    check(cand == (2000, 2), "b was blocked on (2000,1), picks (2000,2)")

    cand = pick_race_target(inflight, 2, "a", nodes, blocked)
    check(cand is None, "a is already on inflight for (2000,1), so skip")

    cand = pick_race_target(inflight, 2, "c", nodes, blocked)
    check(cand == (2000, 1), "c is not blocked/picked, picks lex min")

    inflight_empty: dict[tuple[int, int], int] = {}
    cand = pick_race_target(inflight_empty, 2, "a", {}, {})
    check(cand is None, "empty inflight -> None")


def main() -> None:
    import secrets

    # 每次运行子目录（沙箱零删除适配）：standalone 共享 tmp/test-run-rl 会残留旧
    # 子目录，二次运行 mkdir(exist_ok=False，二级
    #  mkdir(exist_ok=False) → FileExistsError。带随机后缀每次新建。
    tmp = REPO / "tmp" / "test-run-rl" / f"run-{secrets.token_hex(4)}"
    tmp.mkdir(parents=True, exist_ok=True)
    test_mirror_scalar_lockstep()
    test_resume_scope(tmp)
    test_jsonl_anchors(tmp)
    test_compute_gae()
    test_chunk_episodes()
    test_backup_weights(tmp)
    test_scan_shards_mtime_cache(tmp)
    test_rl_config_validation()
    test_eval_local_gate(tmp)
    test_register_inflight_v314()
    test_pick_tail_race()
    test_pick_race_target_v316()
    test_race_tier_ok()
    if ITEST:
        itests = [
            test_it_queue_normal, test_it_halt_preset, test_it_stream_smoke,
            test_it_stream_halt, test_it_local_suspend, test_it_longtail_race,
            test_it_eval_deferred, test_it_precollect_resume, test_it_early_race_v314,
        ]
        for fn in itests:
            sub = tmp / fn.__name__
            sub.mkdir(parents=True, exist_ok=True)
            fn(sub, pytest.MonkeyPatch())
    else:
        print("[skip] integration tier: set RUN_RL_ITEST=1 to enable")
    print()
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        raise SystemExit(1)
    print("RESULT: ALL PASS")


def test_scan_shards_mtime_cache(tmp: Path) -> None:
    """P2-2：_scan_shards 目录签名缓存——新 shard 落盘后签名变化 → 重扫；未变 → 复用。"""
    from rl import resume as rl_resume

    print("[fast] _scan_shards mtime cache（热路径零 IO）")
    traj = tmp / "cache-traj"
    traj.mkdir(parents=True)
    wver = "abc123"
    # 空目录：无 shard
    rl_resume._SCAN_CACHE.clear()
    check(rl_resume.completed_pairs(traj, wver) == set(), "空目录无 done")
    check(len(rl_resume._SCAN_CACHE) == 1, "首次扫描写入缓存")
    check(rl_resume.completed_pairs(traj, wver) == set(), "缓存命中结果一致")

    # 写入一个完整 shard（manifest 含 stage/seed/wver）→ 签名变 → 重扫应命中
    shard = traj / "rl_s0_seed111"
    shard.mkdir()
    import json as _json

    (shard / "obs.npy").write_bytes(b"x")
    (shard / "manifest.json").write_text(
        _json.dumps({"stage": 0, "seed": 111, "wver": wver}), encoding="utf-8"
    )
    check(
        rl_resume.completed_pairs(traj, wver) == {(0, 111)},
        "新 shard 落盘后缓存失效重扫",
    )
    check(
        rl_resume.completed_pairs(traj, wver) == {(0, 111)},
        "签名未变缓存命中",
    )
    # wver 不匹配的 shard 不计入
    shard2 = traj / "rl_s1_seed222"
    shard2.mkdir()
    (shard2 / "manifest.json").write_text(
        _json.dumps({"stage": 1, "seed": 222, "wver": "other"}), encoding="utf-8"
    )
    check(
        rl_resume.completed_pairs(traj, wver) == {(0, 111)},
        "wver 不匹配不计入",
    )


def test_rl_config_validation() -> None:
    """P1-3：启动参数校验（pydantic）——互斥/范围非法值构造即抛，启动期 fail fast。"""
    import types

    from pydantic import ValidationError

    from rl.config import RLConfig, validate_args

    print("[fast] RLConfig 校验（pydantic：互斥/范围构造即抛）")

    def _raises(**kw) -> bool:
        try:
            RLConfig(**kw)
            return False
        except ValidationError:
            return True

    # 合法默认
    check(RLConfig().collect_errors() == [], "默认配置合法")
    # 互斥：precollect_games + precollect_samples
    check(_raises(precollect_games=5, precollect_samples=1000), "games/samples 互斥拦截")
    # 范围
    check(_raises(workers=0), "workers<1 拦截")
    check(_raises(mb=0), "mb<1 拦截")
    check(_raises(lr=0), "lr<=0 拦截")
    check(_raises(adv_norm="bogus"), "adv_norm 非法拦截")
    check(_raises(mode="bogus"), "mode 非法拦截")
    check(_raises(reward="toy:"), "reward toy: 缺 arm 拦截")
    # validate_args 对 Namespace：非法 → SystemExit
    bad = types.SimpleNamespace(
        mode="per-tick", iters=2, stream=1, double_buffer=0, precollect_games=5,
        precollect_samples=1000, workers=8, local_slots=0, mb=512, epochs=4,
        lr=3e-4, seed=7, keep_iters=3, stop_loss_at=0, stop_loss_delta=0.0,
        adv_norm="auto", eval_seeds=10, eval_at="", reward="",
    )
    try:
        validate_args(bad)
        check(False, "互斥组合应 SystemExit")
    except SystemExit:
        check(True, "互斥组合启动期拦截")


if __name__ == "__main__":
    main()
