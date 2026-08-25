"""test_run_rl.py — run_rl.py 常驻回归测试（无 torch 训练、不碰真实节点）。

两层：
  快速层（默认）：纯逻辑 + 磁盘 fixture —— parse_range / build_pairs（确定性、
      sps 变更重叠性质） / combine_reports / completed_pairs+resumed_manifests
      （only/exclude 口径） / last_completed_iter / last_rotate_seed。
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "nn-training"))

import dist_common  # noqa: E402
import run_rl  # noqa: E402
from schema import BOARD, FIRE_DIM, MASK_DIM, MOVE_DIM, OBS_CHANNELS, SCALAR_DIM  # noqa: E402

FAILS: list[str] = []
ITEST = os.environ.get("RUN_RL_ITEST") == "1" or "--itest" in sys.argv
WEIGHTS = REPO / "tmp" / "rl-weights" / "weights.json"


def check(cond: bool, msg: str) -> None:
    global_n = len(FAILS)
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)
        _ = global_n


def bp_args(sps: int, rotate_stages: int = 35, total_stages: int = 35):
    return types.SimpleNamespace(rotate_stages=rotate_stages, seeds_per_stage=sps,
                                 total_stages=total_stages, stages="0-3", seeds="0-3")


# ---------------- 快速层 ----------------

def test_parse_range() -> None:
    print("[fast] parse_range")
    check(run_rl.parse_range("0-3") == [0, 1, 2, 3], "range expansion")
    check(run_rl.parse_range("0,2,5") == [0, 2, 5], "comma list")
    check(run_rl.parse_range("0-1,4") == [0, 1, 4], "mixed")
    check(run_rl.parse_range("7") == [7], "single")


def test_build_pairs() -> None:
    print("[fast] build_pairs")
    a = run_rl.build_pairs(bp_args(3), 60, 1787503550)
    b = run_rl.build_pairs(bp_args(3), 60, 1787503550)
    check(a == b and len(a) == 105, "deterministic per (rotateSeed, it); size=35x3")
    c = run_rl.build_pairs(bp_args(3), 61, 1787503550)
    check(a != c, "different it -> different draw")
    # it60 实测回归：sps 4->3 只改变每关种子索引窗，底流前缀一致，
    # 仅置换前三关相交，重合数恒为 3+2+1=6。
    old = run_rl.build_pairs(bp_args(4), 60, 1787503550)
    inter = set(old) & set(a)
    check(len(inter) == 6, f"sps 4->3 overlap == 6 (got {len(inter)})")
    perm = [24, 1, 33]  # 该 rotateSeed/it 的置换前三关
    check(all(st in perm for st, _sd in inter), "overlap confined to first 3 perm stages")


def test_mirror_scalar_lockstep() -> None:
    """M2 镜像索引锁步：SCALAR_X_INDICES 已迁移为 [15,18]（v2 重编号）——
    mirrorX 前后 (obs, scalars, move) 自洽；旧索引 [20,23] 必须不再翻转（防回归）。"""
    from dataset import mirror_x
    from schema import SCALAR_X_INDICES, SCALAR_DIM
    check(SCALAR_X_INDICES == [15, 18], f"SCALAR_X_INDICES == [15,18] (got {SCALAR_X_INDICES})")
    n = 26
    obs = np.zeros((1, 14, n, n), dtype=np.uint8)
    sc = np.zeros(SCALAR_DIM, dtype=np.float32)
    sc[15] = 0.5   # nearestEnemyRelX → 翻转
    sc[18] = -0.25  # nearestBaseRelX → 翻转
    sc[20] = 0.7   # 旧索引必须已是死位（不再参与翻转）
    sc[23] = 0.7
    obs2, sc2, mv2 = mirror_x(obs, sc, 3)  # left (3) → right (4)
    check(mv2 == 4, "move left<->right flip")
    check(abs(sc2[15] - (-0.5)) < 1e-6, "scalar[15] flips sign under mirrorX")
    check(abs(sc2[18] - 0.25) < 1e-6, "scalar[18] flips sign under mirrorX")
    check(abs(sc2[20] - 0.7) < 1e-6, "legacy scalar[20] is now a dead slot (no flip)")
    check(abs(sc2[23] - 0.7) < 1e-6, "legacy scalar[23] is now a dead slot (no flip)")


def test_curriculum() -> None:
    print("[fast] curriculum_active_count + build_pairs curriculum mode")
    # 纯函数时间驱动扩展：start → 每 every 轮后 +grow → 封顶（首扩发生在 it=every+1）
    check(run_rl.curriculum_active_count(35, 1, 4, 8, 4) == 4, "it1 = start")
    check(run_rl.curriculum_active_count(35, 8, 4, 8, 4) == 4, "it8 still first window")
    check(run_rl.curriculum_active_count(35, 9, 4, 8, 4) == 8, "it9 = start+grow")
    check(run_rl.curriculum_active_count(35, 17, 4, 8, 4) == 12, "it17 = start+2*grow")
    check(run_rl.curriculum_active_count(6, 100, 4, 8, 4) == 6, "caps at order_len")
    check(run_rl.curriculum_active_count(35, 1, 4, 0, 4) == 4, "every=0 never expands")
    # build_pairs 课程模式：激活窗口是排序前缀，且随 it 扩展；种子 (rotateSeed,it) 确定
    ca = types.SimpleNamespace(curriculum_stages="13,1,16,8,21,4", seeds_per_stage=3,
                               curriculum_start=2, curriculum_every=5, curriculum_grow=2,
                               rotate_stages=0, total_stages=35, stages="0-3", seeds="0-3")
    p1 = run_rl.build_pairs(ca, 1, 4242)
    p2 = run_rl.build_pairs(ca, 1, 4242)
    check(p1 == p2 and len(p1) == 2 * 3, "curriculum deterministic; it1 = 2 stages x 3 seeds")
    check({st for st, _sd in p1} == {13, 1}, "it1 active = first 2 stages of ordering")
    p6 = run_rl.build_pairs(ca, 6, 4242)
    check({st for st, _sd in p6} == {13, 1, 16, 8}, "it6 expanded to first 4 stages")
    check(len(p6) == 4 * 3, "it6 = 4 stages x 3 seeds")


def test_combine_reports() -> None:
    print("[fast] combine_reports")
    r1 = {"games": 2, "winRate": 0.5, "outcomes": {"stage_clear": 1, "base_destroyed": 1},
          "totalSamples": 100, "totalTicks": 200, "scoreList": [0.1, 0.3],
          "dimLists": {"progress": [0.2, 0.4]}}
    r2 = {"games": 1, "winRate": 0.0, "outcomes": {"base_destroyed": 1},
          "totalSamples": 50, "totalTicks": 90, "scoreList": [0.2],
          "dimLists": {"progress": [0.1]}, "elapsedSec": 1.0}
    comb = run_rl.combine_reports([r1, r2])
    check(comb["games"] == 3 and comb["totalSamples"] == 150 and comb["totalTicks"] == 290,
          "counts summed")
    check(comb["winRate"] == round(1 / 3, 4), "winRate recomputed across workers")
    check(len(comb["scoreList"]) == 3 and abs(comb["scoreStats"]["mean"] - 0.2) < 1e-9,
          "scoreList merged + stats")
    check(comb["dimMeans"]["progress"] == round((0.2 + 0.4 + 0.1) / 3, 4), "dimMeans merged")


def _mk_shard(traj: Path, stage: int, seed: int, wver: str, *, aggregate: bool = False) -> None:
    d = traj / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    if aggregate:
        mm = {"wver": wver, "stage": stage, "seed": seed, "games": 1,
              "outcomes": {"stage_clear": 1}, "totalSamples": 30, "totalTicks": 900,
              "scoreList": [0.5], "dimLists": {}, "node": "fake"}
    else:
        mm = {"wver": wver, "stage": stage, "seed": seed, "nSamples": 30, "ticks": 900,
              "outcome": "stage_clear", "score": 0.4}
    (d / "obs.npy").write_bytes(b"\x00")
    (d / "manifest.json").write_text(json.dumps(mm), encoding="utf-8")


def test_resume_scope(tmp: Path) -> None:
    print("[fast] completed_pairs + resumed_manifests (plan scope)")
    traj = tmp / "resume"
    shutil.rmtree(traj, ignore_errors=True)
    traj.mkdir(parents=True)
    wver = "a" * 64
    _mk_shard(traj, 0, 111, wver)
    _mk_shard(traj, 3, 222, wver, aggregate=True)
    _mk_shard(traj, 9, 999, wver)           # 计划外残留（跨配置断点）
    _mk_shard(traj, 5, 555, "b" * 64)       # 旧权重代际
    plan = {(0, 111), (3, 222)}
    done_all = run_rl.completed_pairs(traj, wver)
    check(done_all == {(0, 111), (3, 222), (9, 999)}, "done filters by wver only")
    done_plan = done_all & plan
    check(done_plan == {(0, 111), (3, 222)}, "plan intersection")
    rm = run_rl.resumed_manifests(traj, wver, only=plan)
    check({(m["stage"], m["seed"]) for m in rm} == {(0, 111), (3, 222)},
          "resumed honors only=plan (drops off-plan)")
    agg = [m for m in rm if m.get("games") == 1 and m.get("node") == "fake"]
    check(len(agg) == 1, "aggregate-schema manifest passed through")
    legacy = [m for m in rm if m.get("node") != "fake"]
    check(len(legacy) == 1 and legacy[0]["games"] == 1 and legacy[0]["totalSamples"] == 30,
          "legacy schema converted to aggregate shape")
    rm_ex = run_rl.resumed_manifests(traj, wver, only=plan, exclude={(0, 111)})
    check({(m["stage"], m["seed"]) for m in rm_ex} == {(3, 222)}, "exclude=seen honored")


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
    i64 = lambda hi: rng.integers(0, hi, n).astype(np.int64)  # noqa: E731
    f32 = lambda x: rng.standard_normal(x).astype(np.float32)  # noqa: E731
    done = np.zeros(n, dtype=np.int64)
    done[-1] = 1
    return {"obs": rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8),
            "scalars": f32((n, SCALAR_DIM)), "a_move": i64(MOVE_DIM), "a_fire": i64(FIRE_DIM),
            "lp_move": -np.abs(f32(n)) - 0.05,
            "lp_fire": -np.abs(f32(n)) - 0.05,
            "value": f32(n), "reward": f32(n), "done": done,
            "mask": np.ones((n, MASK_DIM), dtype=np.int64)}


def _pack_container(stage: int, seed: int, wver: str) -> bytes:
    manifest = {"wver": wver, "stage": stage, "seed": seed, "games": 1,
                "outcomes": {"stage_clear": 1}, "totalSamples": 30, "totalTicks": 900,
                "scoreList": [0.5], "dimLists": {}, "elapsedSec": 0.01, "node": "fake"}
    header_files, body = [], b""
    for name, arr in _synth_payload().items():
        fname = f"{name}.npy"
        raw = _npy_bytes(arr)
        header_files.append({"name": fname, "len": len(raw)})
        body += struct.pack(">H", len(fname)) + fname.encode() + struct.pack(">Q", len(raw)) + raw
    header = json.dumps({"manifest": manifest, "files": header_files}).encode()
    return gzip.compress(struct.pack(">I", 0x42435632) + struct.pack(">I", len(header)) + header + body)


class FakeAgent(BaseHTTPRequestHandler):
    events: list[tuple[str, float, tuple]] = []

    def log_message(self, *_a) -> None:
        return

    def _json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/v1/ping":
            bun = shutil.which("bun")
            self._json({"codeHash": dist_common.compute_code_hash(),
                        "bunVersion": subprocess.run([bun, "--version"], capture_output=True,
                                                     text=True, timeout=10).stdout.strip(),
                        "cpus": 4})
        elif u.path == "/v1/task":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            FakeAgent.events.append(("dispatch", time.time(), (int(q["stage"]), int(q["seed"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            body = _pack_container(int(q["stage"]), int(q["seed"]), q["wver"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "nf"})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakeAgent.events.append(("weights", time.time(), ()))
        self._json({"cache": "kept"})


def test_integration(tmp: Path) -> None:
    import torch

    print("[itest] queue normal / halt / queue-drained ordering")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    bun = shutil.which("bun")
    cfg = {"nodes": [{"id": "fake", "url": url, "authKey": "", "concurrency": 4, "enabled": True}],
           "policy": {"taskTimeoutSec": 60, "queueWindowSec": 120, "statusTimeoutSec": 3}}
    args = types.SimpleNamespace(workers=4, max_ticks=300, difficulty="hard")

    def fresh(tag: str) -> Path:
        p = tmp / tag
        shutil.rmtree(p, ignore_errors=True)
        p.mkdir(parents=True)
        FakeAgent.events.clear()
        return p

    try:
        # I1 正常流：结算齐全；queue-drained 恰一次，且在首次 dispatch 之后
        traj = fresh("i1")
        drained_ts, result_ts = [], []
        rep = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i1.1",
            local_slots_max=0,
            on_result=lambda _s: result_ts.append(time.time()),
            on_queue_drained=lambda: drained_ts.append(time.time()))
        disp = [t for kind, t, _x in FakeAgent.events if kind == "dispatch"]
        wts = [t for kind, t, _x in FakeAgent.events if kind == "weights"]
        disp_pairs = {p for kind, _t, p in FakeAgent.events if kind == "dispatch"}
        check(rep.get("missing") == [] and rep["games"] == 2, "I1 settled fully")
        check(rep.get("dist_phase_sec") is not None, "I1 dist_phase_sec present")
        check(len(drained_ts) == 1, f"I1 queue-drained fired once (got {len(drained_ts)})")
        # 契约：清空信号晚于权重分发（不得退回 dist-done 时代）；pop 即交接，
        # 最后一个任务的 HTTP 提交允许在信号之后，但必须全部发生。
        check(wts and drained_ts[0] > wts[0], "I1 drained after weight distribution")
        check(disp_pairs == {(0, 111), (3, 222)}, "I1 all pairs still dispatched")
        check(rep["dist"]["offPlanShards"] == 0, "I1 no off-plan shards")

        # I2 预置 halt：零派发、drained 不触发、halt_aborted
        traj = fresh("i2")
        ev = threading.Event()
        ev.set()
        calls = []
        rep2 = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, [(0, 111), (3, 222)], args, cfg, "i2.2",
            local_slots_max=0, halt_event=ev,
            on_queue_drained=lambda: calls.append(1))
        check(rep2.get("halt_aborted") is True, "I2 halt_aborted flagged")
        check(len(rep2["missing"]) == 2 and rep2["games"] == 0, "I2 nothing dispatched/settled")
        check(calls == [], "I2 queue-drained NOT fired under pre-set halt")

        # I3 流式迷你轮：真 PPO 更新 + 评估恰一次（队列清空时）+ 句柄回传
        import ppo as ppo_mod
        traj = fresh("i3")
        model = ppo_mod.build_ppo(None)
        opt = torch.optim.Adam(model.parameters(), lr=1e-4)
        cfg3 = json.loads(json.dumps(cfg))
        cfg3["policy"]["streamWaveGames"] = 2
        cfg3["policy"]["streamKlCap"] = 1e12  # 随机权重首波 KL 天文数字，只验非熔断路径
        fired = []

        def on_collect_done():
            fired.append(time.time())
            th = threading.Thread(target=lambda: None)
            th.start()
            return th

        rep3 = run_rl.run_rollout_stream(
            bun, str(WEIGHTS), traj, [(0, 111), (0, 222), (1, 333), (1, 444)],
            types.SimpleNamespace(**{**vars(args), "epochs": 1, "mb": 64}),
            cfg3, "i3.3", model, opt, torch.device("cpu"), on_collect_done=on_collect_done)
        sm = rep3.pop("_stream")
        check(rep3["games"] == 4 and sm["waves"] >= 1, "I3 streamed round trained")
        check(sm["halted"] is False and sm["dropped_games"] == 0, "I3 no halt (cap high)")
        check(len(fired) == 1, f"I3 eval fired exactly once (got {len(fired)})")
        wts3 = [t for kind, t, _x in FakeAgent.events if kind == "weights"]
        check(wts3 and fired[0] > wts3[0], "I3 eval after weight distribution (queue-drained)")
        check("_eval_thread" in rep3, "I3 eval thread returned via report")

        # I4 流式熔断轮：触顶停训 + 停派发
        traj = fresh("i4")
        model4 = ppo_mod.build_ppo(None)
        opt4 = torch.optim.Adam(model4.parameters(), lr=1e-4)
        cfg4 = json.loads(json.dumps(cfg))
        cfg4["policy"]["streamWaveGames"] = 2
        cfg4["policy"]["streamKlCap"] = 1e-6
        rep4 = run_rl.run_rollout_stream(
            bun, str(WEIGHTS), traj, [(0, 111), (0, 222), (1, 333), (1, 444)],
            types.SimpleNamespace(**{**vars(args), "epochs": 1, "mb": 64}),
            cfg4, "i4.4", model4, opt4, torch.device("cpu"), on_collect_done=None)
        sm4 = rep4.pop("_stream")
        check(sm4["halted"] is True, f"I4 halted (cum_kl={sm4['kl_cum']:.1f})")
        check(sm4["waves"] >= 1 and "_eval_thread" not in rep4, "I4 coherent without eval cb")

        # I5 local_suspend 语义：置位后本机直跑让位（全部落 fake 节点）
        pairs5 = [(0, 111), (3, 222)]
        traj = fresh("i5a")
        susp = threading.Event()
        susp.set()
        rep5a = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, pairs5, args, cfg, "i5.51",
            local_slots_max=2, local_suspend=susp)
        check(rep5a["games"] == 2 and "local" not in rep5a["dist"]["nodes"],
              f"I5 suspended → zero local settlements (byNode={rep5a['dist']['nodes']})")
        # 对照组（不置位）：头部分配使 local 独占前 N 局——确定性断言
        traj = fresh("i5b")
        rep5b = run_rl.run_rollout_queue(
            bun, str(WEIGHTS), traj, pairs5, args, cfg, "i5.52",
            local_slots_max=2)
        check(rep5b["games"] == 2 and rep5b["dist"]["nodes"] == {"local": 2},
              f"I5b no-suspend → local owns head tasks ({rep5b['dist']['nodes']})")
    finally:
        srv.shutdown()


def test_breaker_update() -> None:
    from rl.breaker import breaker_update
    print("[fast] breaker_update (F4 纯逻辑)")
    # KL 规则：连续 3 轮 ≥0.15 触发；中间一轮回落即清零
    s = breaker_update(0, 0, kl=0.16, entropy=1.2, win_rate=0.9)
    check(s[:2] == (1, 0) and s[2] is None, "KL streak advances, no early trip")
    s = breaker_update(s[0], s[1], kl=0.16, entropy=1.2, win_rate=0.9)
    check(s[0] == 2 and s[2] is None, "KL streak 2")
    s = breaker_update(s[0], s[1], kl=0.16, entropy=1.2, win_rate=0.9)
    check(s[0] == 3 and s[2] is not None and "kl>=0.15" in s[2], "KL trips at 3rd consecutive")
    reset = breaker_update(2, 0, kl=0.01, entropy=1.2, win_rate=0.9)
    check(reset[0] == 0 and reset[2] is None, "good iter resets KL streak")
    # ENT 规则：低熵 + 低胜率护栏；高胜率策略永不误停
    e = breaker_update(0, 7, kl=0.01, entropy=0.55, win_rate=0.3)
    check(e[1] == 8 and e[2] is not None and "entropy<=" in e[2], "ENT trips at 8th consecutive")
    guard = breaker_update(0, 0, kl=0.01, entropy=0.4, win_rate=0.9)
    check(guard[1] == 0 and guard[2] is None, "high winRate guards ENT rule")


def test_wave_params() -> None:
    from rl.stream import wave_params
    print("[fast] wave_params (软降档 + 残局上限)")
    check(wave_params(0.0, 0.12, 12, 24) == (12, 24), "normal zone unchanged")
    check(wave_params(0.0839, 0.12, 12, 24) == (12, 24), "below 70% cap unchanged")
    check(wave_params(0.0841, 0.12, 12, 24) == (4, 8), "soft zone shrinks to floor")
    check(wave_params(0.5, 0.12, 3, 3) == (4, 4), "floors at 4 even with tiny config")
    # 残局上限（it63 教训：只剩 2 局却要等满阈值 4 → 静默空等收官）
    check(wave_params(0.0, 0.12, 12, 24, remaining=2) == (2, 2), "remaining caps threshold+cap")
    check(wave_params(0.0, 0.12, 12, 24, remaining=50) == (12, 24), "larger remaining ignored")
    check(wave_params(0.0841, 0.12, 12, 24, remaining=2) == (2, 2), "soft zone + tiny remaining")
    check(wave_params(0.0841, 0.12, 12, 24, remaining=6) == (4, 6), "soft zone capped by remaining")


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
    print("[fast] backup_weights (归档 + 有界清理)")
    bdir = tmp / "weights-archive"
    shutil.rmtree(bdir, ignore_errors=True)
    src = tmp / "src-weights.json"
    src.write_text("{}", encoding="utf-8")
    old_dir, old_keep = run_rl.WEIGHTS_BACKUP_DIR, run_rl.WEIGHTS_BACKUP_KEEP
    run_rl.WEIGHTS_BACKUP_DIR, run_rl.WEIGHTS_BACKUP_KEEP = bdir, 2
    try:
        for it in (1, 2, 3):
            out = run_rl.backup_weights(str(src), it)
            check(out is not None, f"backup it{it} returned path")
        remain = sorted(p.name.split(".")[1] for p in bdir.glob("rl-weights.it*.json"))
        check(remain == ["it2", "it3"], f"bounded prune keeps newest KEEP (got {remain})")
    finally:
        run_rl.WEIGHTS_BACKUP_DIR, run_rl.WEIGHTS_BACKUP_KEEP = old_dir, old_keep


def test_eval_local_gate(tmp: Path) -> None:
    """R6 补丁：eval 本地参与——gate 放行后本机直跑全部/尾局；gate 不放行则让位。"""
    import rl.eval_dispatch as ed

    work = tmp / "eval-local"
    shutil.rmtree(work, ignore_errors=True)  # 台账按 wver 去重——残留会让 todo 清空走 skip 早退
    work.mkdir(parents=True, exist_ok=True)
    rl = work / "w.json"
    rl.write_text("{\"arch\":{}}", encoding="utf-8")

    def mk_args(window: float):
        return types.SimpleNamespace(eval_games_per_stage=2, total_stages=3,
                                     eval_window_sec=window, max_ticks=10,
                                     difficulty="hard")

    cfg = {"nodes": [], "policy": {"evalLocalSlots": 2}}
    calls: list[tuple[int, int]] = []

    def fake_runner(bun, snap, stage, seed, out_dir, max_ticks, difficulty,
                    timeout_sec, wver):
        calls.append((stage, seed))
        assert Path(snap).read_text(encoding="utf-8") == "{\"arch\":{}}"
        return {"stage": stage, "seed": seed, "outcome": "timeout", "ticks": 10,
                "win": 0, "score": 0.1, "quality": 0.2, "dims": {},
                "elapsedSec": 0.001, "wver": wver, "mode": "eval"}

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
        ed.dispatch_eval_round("bun", str(rl), traj_a, mk_args(30), cfg,
                               "rid.1", 1, local_gate=gate)
        rows = [json.loads(l) for l in
                (work / "eval_log.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
        eval_rows = [r for r in rows if r.get("event") == "eval"]
        summ = [r for r in rows if r.get("event") == "eval_summary"]
        check(len(calls) == 6 and len(eval_rows) == 6,
              f"gate set → all 6 games ran locally (calls={len(calls)}, rows={len(eval_rows)})")
        check(all(r.get("node") == "local" for r in eval_rows),
              "ledger rows attributed to node=local")
        check(bool(summ) and summ[-1]["games"] == 6 and summ[-1]["nodes"] == {"local": 6}
              and summ[-1]["dropped"] == 0, "summary aggregates local-only round")
        check((traj_a / "_eval_frozen_weights.json").exists(),
              "frozen weights snapshot written into traj_dir")
        # 采样机健康账本：eval 局同册入账（mode:"eval"），喂「采样机健康」表指标
        meta_rows = [json.loads(l) for l in
                     (work / "dist-agent-meta.jsonl").read_text(encoding="utf-8").splitlines()
                     if l.strip()]
        check(len(meta_rows) == 6 and all(r.get("mode") == "eval" and r.get("ok")
                                          and r.get("node") == "local"
                                          for r in meta_rows),
              f"meta ledger records 6 eval games ({len(meta_rows)} rows)")
        # B：gate 从不放行 → runner 零新增调用，窗口到期自然收场（不挂死、不越权训练侧）。
        # 用不同权重文件（不同 wver）确保 todo 非空，真正进入关门等待路径。
        baseline_calls = len(calls)
        traj_b = work / "trajB"
        rl_b = work / "w2.json"
        rl_b.write_text("{\"arch\":{\"h\":32}}", encoding="utf-8")
        gate_open_never = threading.Event()
        t0 = time.time()
        ed.dispatch_eval_round("bun", str(rl_b), traj_b, mk_args(1), cfg,
                               "rid.2", 2, local_gate=gate_open_never)
        took = time.time() - t0
        check(len(calls) == baseline_calls, "gate closed → local runner never invoked")
        check(took < 8, f"closed-gate round exits at window ({took:.1f}s)")
        rows_b = [json.loads(l) for l in
                  (work / "eval_log.jsonl").read_text(encoding="utf-8").splitlines()
                  if l.strip()]
        summ_b = [r for r in rows_b if r.get("event") == "eval_summary"
                  and r.get("iter") == 2]
        check(bool(summ_b) and summ_b[-1]["games"] == 0 and summ_b[-1]["dropped"] == 6,
              "closed-gate round settles nothing, all dropped")
    finally:
        ed.run_local_eval_game = orig


def main() -> None:
    if not WEIGHTS.exists():
        print(f"[skip-integration] missing weights fixture: {WEIGHTS}")
    tmp = REPO / "tmp" / "test-run-rl"
    tmp.mkdir(parents=True, exist_ok=True)
    test_parse_range()
    test_build_pairs()
    test_curriculum()
    test_combine_reports()
    test_resume_scope(tmp)
    test_jsonl_anchors(tmp)
    test_breaker_update()
    test_wave_params()
    test_compute_gae()
    test_chunk_episodes()
    test_backup_weights(tmp)
    test_eval_local_gate(tmp)
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
