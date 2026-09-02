"""test_run_rl.py — run_rl.py 常驻回归测试（无 torch 训练、不碰真实节点）。

两层：
  快速层（默认）：已迁移到 tests/ 独立文件（test_rl_course / test_rl_reports /
      test_rl_breaker / test_rl_stream / test_rl_resume）。本文件仅保留
      集成层与无法拆出的 fixture-重测试（mirror / resume / jsonl / compute_gae /
      chunk / backup / eval_local_gate / race-tier）。
  集成层（RUN_RL_ITEST=1）：本地假 HTTP agent 节点驱动真 run_rollout_queue /
      run_rollout_stream —— 正常流、halt 流、派发队列清空回调的触发与次序。

运行（经统一启动器，venv/torch 由它保证）：
  bash nn-training/start-training.sh --script test_run_rl.py
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 -Script test_run_rl.py
  集成层追加环境变量 RUN_RL_ITEST=1（需 PATH 上有 bun、tmp/rl-weights/weights.json 存在）。

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
import run_rl
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.stream import run_rollout_stream as _run_rollout_stream  # B7：run_rl 模块级不再 re-export
from schema import BOARD, FIRE_DIM, MASK_DIM, MOVE_DIM, OBS_CHANNELS, SCALAR_DIM

FAILS: list[str] = []
ITEST = os.environ.get("RUN_RL_ITEST") == "1" or "--itest" in sys.argv
WEIGHTS = REPO / "tmp" / "rl-weights" / "weights.json"


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
        "reward": f32(n),
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


class FakeAgent(BaseHTTPRequestHandler):
    events: list[tuple[str, float, tuple]] = []
    # I6 长尾竞速注入：首次 dispatch 该 (stage,seed) 时 sleep（模拟慢节点独占 in-flight）；
    # 之后（含竞速副本）秒回 —— 竞速副本必须绕开慢窗口才会赢。
    slow_first: set[tuple[int, int]] = set()
    _slowed_once: set[tuple[int, int]] = set()
    # I7 eval 最低优先级注入：mode=eval 的每局任务 sleep（模拟慢 eval 后台消化）——
    # 验证它与采集/训练并行时互不阻塞（采集照常、eval 慢慢做）。
    eval_delay = 0.0

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
            bun = shutil.which("bun")
            assert bun is not None, "bun not found"
            self._json(
                {
                    "codeHash": dist_common.compute_code_hash(),
                    "bunVersion": subprocess.run(
                        [bun, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        **_POPEN_NO_WINDOW,
                    ).stdout.strip(),
                    "cpus": 4,
                    "evalSupport": True,
                }
            )
        elif u.path == "/v1/task":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            key = (int(q["stage"]), int(q["seed"]))
            FakeAgent.events.append(("dispatch", time.time(), key))
            if key in FakeAgent.slow_first and key not in FakeAgent._slowed_once:
                FakeAgent._slowed_once.add(key)
                time.sleep(2.0)  # 慢主副本窗口
            if q.get("mode") == "eval" and FakeAgent.eval_delay > 0:
                time.sleep(FakeAgent.eval_delay)  # I7 慢 eval（后台消化模拟）
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
        FakeAgent.events.append(("weights", time.time(), ()))
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
        return [object()]  # 1 个假 episode → chunk_episodes 至少 1 chunk

    def chunk_episodes(self, eps, mb):
        return list(eps)

    def load_episode_from_shard(self, shard_dir):
        # stream._load_wave 只要求 ep["adv"]（做 wave 内归一化）；具体轨迹数值不重要
        # ——rollout 引擎的真实性由 tools/sim/export-rl-rollout.ts 的 TS 测试保证。
        return {"adv": np.zeros(1, dtype=np.float32)}


@pytest.mark.skipif(
    shutil.which("bun") is None or not WEIGHTS.exists(),
    reason="integration: requires bun on PATH + tmp/rl-weights/weights.json",
)
def test_integration(tmp: Path) -> None:
    print("[itest] queue normal / halt / queue-drained ordering")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    bun = shutil.which("bun")
    assert bun is not None, "bun not found"
    cfg = {
        "nodes": [{"id": "fake", "url": url, "authKey": "", "concurrency": 4, "enabled": True}],
        "policy": {
            "taskTimeoutSec": 60,
            "queueWindowSec": 120,
            "statusTimeoutSec": 3,
            "agentRescanSec": 1,
        },
    }  # rescan 默认 120s 轮询 → 每轮白等 120s 才收官
    args = types.SimpleNamespace(workers=4, max_ticks=300, difficulty="hard")

    def fresh(tag: str) -> Path:
        p = tmp / tag
        p.mkdir(parents=True)  # 沙箱零删除适配：tmp 唯一目录，无需预清理
        FakeAgent.events.clear()
        return p

    try:
        # I1 正常流：结算齐全；queue-drained 恰一次，且在首次 dispatch 之后
        traj = fresh("i1")
        drained_ts, result_ts = [], []
        rep = run_rl.run_rollout_queue(
            bun,
            str(WEIGHTS),
            traj,
            [(0, 111), (3, 222)],
            args,
            cfg,
            "i1.1",
            local_slots_max=0,
            on_result=lambda _s: result_ts.append(time.time()),
            on_queue_drained=lambda: drained_ts.append(time.time()),
        )
        _ = [t for kind, t, _x in FakeAgent.events if kind == "dispatch"]
        wts = [t for kind, t, _x in FakeAgent.events if kind == "weights"]
        disp_pairs = {p for kind, _t, p in FakeAgent.events if kind == "dispatch"}
        check(rep.get("missing") == [] and rep["games"] == 2, "I1 settled fully")
        check(rep.get("dist_phase_sec") is not None, "I1 dist_phase_sec present")
        check(len(drained_ts) == 1, f"I1 queue-drained fired once (got {len(drained_ts)})")
        # 契约：清空信号晚于权重分发（不得退回 dist-done 时代）；pop 即交接，
        # 最后一个任务的 HTTP 提交允许在信号之后，但必须全部发生。
        check(bool(wts) and drained_ts[0] > wts[0], "I1 drained after weight distribution")
        check(disp_pairs == {(0, 111), (3, 222)}, "I1 all pairs still dispatched")
        check(rep["dist"]["offPlanShards"] == 0, "I1 no off-plan shards")

        # I2 预置 halt：零派发、drained 不触发、halt_aborted
        traj = fresh("i2")
        ev = threading.Event()
        ev.set()
        calls = []
        rep2 = run_rl.run_rollout_queue(
            bun,
            str(WEIGHTS),
            traj,
            [(0, 111), (3, 222)],
            args,
            cfg,
            "i2.2",
            local_slots_max=0,
            halt_event=ev,
            on_queue_drained=lambda: calls.append(1),
        )
        check(rep2.get("halt_aborted") is True, "I2 halt_aborted flagged")
        check(len(rep2["missing"]) == 2 and rep2["games"] == 0, "I2 nothing dispatched/settled")
        check(calls == [], "I2 queue-drained NOT fired under pre-set halt")

        # I3 流式迷你轮（PPO 桩化版）——只验编排：真 PPO 更新由 _StubPpo 取代，
        # 断言 wave 训练调用真实发生 + 评估恰一次（队列清空时）+ 句柄回传。
        traj = fresh("i3")
        stub3 = _StubPpo(kl=1e-12)
        cfg3 = json.loads(json.dumps(cfg))
        cfg3["policy"]["streamWaveGames"] = 2
        cfg3["policy"]["streamKlCap"] = 1e12  # 桩 kl 极小，只验非熔断路径
        fired = []

        def on_collect_done():
            fired.append(time.time())
            th = threading.Thread(target=lambda: None)
            th.start()
            return th

        rep3 = _run_rollout_stream(
            bun,
            str(WEIGHTS),
            traj,
            [(0, 111), (0, 222), (1, 333), (1, 444)],
            types.SimpleNamespace(**{**vars(args), "epochs": 1, "mb": 64}),
            cfg3,
            "i3.3",
            None,
            None,
            "cpu",
            on_collect_done=on_collect_done,
            backend=stub3,
        )
        sm = rep3.pop("_stream")
        check(rep3["games"] == 4 and sm["waves"] >= 1, "I3 streamed round trained")
        check(stub3.updates >= 1, f"I3 stub PPO invoked for waves ({stub3.updates})")
        check(sm["halted"] is False and sm["dropped_games"] == 0, "I3 no halt (cap high)")
        check(len(fired) == 1, f"I3 eval fired exactly once (got {len(fired)})")
        wts3 = [t for kind, t, _x in FakeAgent.events if kind == "weights"]
        check(bool(wts3) and fired[0] > wts3[0], "I3 eval after weight distribution (queue-drained)")
        check("_eval_thread" in rep3, "I3 eval thread returned via report")

        # I4 流式熔断轮（PPO 桩化版）：桩 kl=1e6 单波触顶 → 停训 + 停派发
        traj = fresh("i4")
        stub4 = _StubPpo(kl=1e6)
        cfg4 = json.loads(json.dumps(cfg))
        cfg4["policy"]["streamWaveGames"] = 2
        cfg4["policy"]["streamKlCap"] = 1e-6
        rep4 = _run_rollout_stream(
            bun,
            str(WEIGHTS),
            traj,
            [(0, 111), (0, 222), (1, 333), (1, 444)],
            types.SimpleNamespace(**{**vars(args), "epochs": 1, "mb": 64}),
            cfg4,
            "i4.4",
            None,
            None,
            "cpu",
            on_collect_done=None,
            backend=stub4,
        )
        sm4 = rep4.pop("_stream")
        check(sm4["halted"] is True, f"I4 halted (cum_kl={sm4['kl_cum']:.1f})")
        check(sm4["waves"] >= 1 and "_eval_thread" not in rep4, "I4 coherent without eval cb")

        # I5 local_suspend 语义：置位后本机直跑让位（全部落 fake 节点）
        pairs5 = [(0, 111), (3, 222)]
        traj = fresh("i5a")
        susp = threading.Event()
        susp.set()
        rep5a = run_rl.run_rollout_queue(
            bun,
            str(WEIGHTS),
            traj,
            pairs5,
            args,
            cfg,
            "i5.51",
            local_slots_max=2,
            local_suspend=susp,
        )
        check(
            rep5a["games"] == 2 and "local" not in rep5a["dist"]["nodes"],
            f"I5 suspended → zero local settlements (byNode={rep5a['dist']['nodes']})",
        )
        # I5b 对照组（不置位）：头部分配使 local 独占前 N 局——确定性断言
        traj = fresh("i5b")
        rep5b = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, pairs5, args, cfg, "i5.52", local_slots_max=2
        )
        check(
            rep5b["games"] == 2 and rep5b["dist"]["nodes"] == {"local": 2},
            f"I5b no-suspend → local owns head tasks ({rep5b['dist']['nodes']})",
        )

        # I6 v3.10 长尾竞速：慢主副本独占 in-flight（2s 窗口）→ 空闲槽复制竞速先返回。
        # 若末尾任务被单 worker 独占后无人竞速（v3.7 盲区），本轮会被 2s 窗口拖住且
        # (0,111) 只会 dispatch 1 次；v3.10 下应 dispatch ≥2（主 + 竞速副本）且快速收官。
        traj = fresh("i6")
        FakeAgent.slow_first = {(0, 111)}
        FakeAgent._slowed_once = set()
        t0 = time.time()
        rep6 = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i6.6", local_slots_max=0
        )
        took6 = time.time() - t0
        n_slow_disp = sum(
            1 for kind, _t, p in FakeAgent.events if kind == "dispatch" and p == (0, 111)
        )
        check(rep6["games"] == 2 and rep6["missing"] == [], "I6 long-tail race settled all")
        check(n_slow_disp >= 2, f"I6 slow task re-raced by idle slot (dispatches={n_slow_disp})")
        check(took6 < 40.0, f"I6 round not stalled on slow copy ({took6:.1f}s)")  # 并行门禁放宽：争抢下不误报
        FakeAgent.slow_first = set()
        FakeAgent._slowed_once = set()

        # I7 eval 最低优先级（v3.12 语义）：eval 慢速在途（后台线程）时，下一轮采集照常
        # 进行、互不阻塞——eval 冻结本轮权重、后台消化，采集/PPO 空档慢慢做，不抢主链。
        import rl.eval_dispatch as ed

        traj = fresh("i7")
        # eval_done_keys 台账在 traj.parent（= tmp/eval_log.jsonl）按 wver 去重——
        # 上次运行残留的同 wver 记录会让本轮 eval 全量 skip 早退、is_alive 断言落空。
        # 隔离：删共享台账（test_run_rl 专用 tmp 目录，无生产影响）。
        shared_eval_log = traj.parent / "eval_log.jsonl"
        if shared_eval_log.exists():
            try:
                shared_eval_log.unlink()
            except BaseException:
                pass  # 沙箱零删除适配：删除被拦截时静默保留（该集成段需 bun，常被 skip）
        FakeAgent.eval_delay = 3.0  # 每局 mode=eval 延迟 3s（6 局/4 并发 ≈ 6s+）≫ 采集 1.4s
        args_eval = types.SimpleNamespace(
            **{**vars(args), "eval_games_per_stage": 2, "eval_stages": "0-2"}
        )
        eval_th = threading.Thread(
            target=lambda: ed.dispatch_eval_round(
                bun, str(WEIGHTS), traj, args_eval, cfg, "i7.e", 10
            ),
            daemon=True,
            name="eval-it10",
        )
        eval_th.start()
        time.sleep(0.3)  # 让 eval 抢先派发、进入在途（模拟"eval 已在跑"）
        t_collect = time.time()
        rep7 = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i7.9", local_slots_max=0
        )
        t_collect = round(time.time() - t_collect, 1)
        check(
            rep7["games"] == 2 and rep7["missing"] == [],
            f"I7 collection completes while slow eval in flight ({t_collect}s)",
        )
        check(eval_th.is_alive(), "I7 slow eval still running afterwards (deferred to background)")
        eval_th.join(timeout=45)  # 放 eval 收尾（不阻塞断言路径）
        FakeAgent.eval_delay = 0.0

        # I8 吞吐 T4 提前预采：上一轮 epoch3 快照（θ_{N,e3}，wver=extra）采的首波
        # shard 已在盘 → 本轮 stream 应①把它注入第一波训练（seed pend）；②collector
        # resume 对账（double-wver）跳过它、只现场采计划内剩余局（不重复 dispatch）。
        traj = fresh("i8")
        plan8 = [(0, 111), (3, 222)]  # 本轮计划 2 局
        pre_pair, cur_pair = (0, 111), (3, 222)
        # 预采首波盘上 shard：wver = "e"*64（模拟 θ_{N,e3} 快照指纹，≠当前权重）
        _mk_shard(traj, *pre_pair, "e" * 64)
        args8 = types.SimpleNamespace(
            **{**vars(args), "max_ticks": 600, "mb": 128, "epochs": 1, "seed": 7}
        )
        cfg8 = json.loads(json.dumps(cfg))
        cfg8["policy"]["streamWaveGames"] = 1  # 每局一波（首波注入立即起训）
        stub8 = _StubPpo(kl=1e-12)
        rep8 = _run_rollout_stream(
            bun,
            str(WEIGHTS),
            traj,
            plan8,
            args8,
            cfg8,
            "i8.8",
            None,
            None,
            None,
            backend=stub8,
            extra_wver="e" * 64,
        )
        disp8 = [p for kind, _t, p in FakeAgent.events if kind == "dispatch"]
        check(
            stub8.updates >= 2,
            f"I8 precollected shard trained as first wave + rest collected (updates={stub8.updates})",
        )
        check(
            pre_pair not in disp8 and cur_pair in disp8,
            f"I8 collector skips precollected pair, dispatches only remaining ({disp8})",
        )
        check(rep8["games"] == 2, f"I8 report covers full plan (games={rep8['games']})")
    finally:
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

    def fake_runner(bun, snap, stage, seed, out_dir, max_ticks, difficulty, timeout_sec, wver):
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


def main() -> None:
    if not WEIGHTS.exists():
        print(f"[skip-integration] missing weights fixture: {WEIGHTS}")
    tmp = REPO / "tmp" / "test-run-rl"
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
    test_pick_tail_race()
    test_race_tier_ok()
    if ITEST:
        if not WEIGHTS.exists() or shutil.which("bun") is None:
            raise SystemExit("RUN_RL_ITEST=1 requires bun on PATH and tmp/rl-weights/weights.json")
        test_integration(tmp)
    else:
        print("[skip] integration tier: set RUN_RL_ITEST=1 to enable")
    print()
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        raise SystemExit(1)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()


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
    """P1-3：启动参数校验——互斥/范围非法值在启动期 fail fast。"""
    import types

    from rl.config import RLConfig, validate_args

    print("[fast] RLConfig 校验（互斥/范围）")
    # 合法默认
    check(RLConfig().validate() == [], "默认配置合法")
    # 互斥：precollect_games + precollect_samples
    c = RLConfig(precollect_games=5, precollect_samples=1000)
    check(any("互斥" in e for e in c.validate()), "games/samples 互斥")
    # 范围
    check(any("workers" in e for e in RLConfig(workers=0).validate()), "workers<1 拦截")
    check(any("mb" in e for e in RLConfig(mb=0).validate()), "mb<1 拦截")
    check(any("lr" in e for e in RLConfig(lr=0).validate()), "lr<=0 拦截")
    check(any("adv_norm" in e for e in RLConfig(adv_norm="bogus").validate()), "adv_norm 非法拦截")
    # validate_args 对 Namespace：非法 → SystemExit
    bad = types.SimpleNamespace(
        mode="per-tick", iters=2, stream=1, double_buffer=0, precollect_games=5,
        precollect_samples=1000, workers=8, local_slots=0, mb=512, epochs=4,
        lr=3e-4, seed=7, keep_iters=3, stop_loss_at=0, stop_loss_delta=0.0,
        adv_norm="auto", eval_seeds=10, eval_at="",
    )
    try:
        validate_args(bad)
        check(False, "互斥组合应 SystemExit")
    except SystemExit:
        check(True, "互斥组合启动期拦截")
