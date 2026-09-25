"""test_volume_e2e.py — 按样本量动态采集的端到端验证（plan §3-P2）。

与 `tests/test_rollout_volume.py` 的分工：那边用桩 `_dispatch_volume_wave` 验配额逻辑与接线；
这里**不桩任何采集侧代码**——真 `dispatch_rollout_phase` → 真 `run_rollout_queue`
（`rl/dispatch.py` 的完整调度器）→ 假 sim 节点（HTTP）落真 shard（真 manifest schema）→
真 `settled_stage_totals` 账本 → 真 `_volume_topup` 补波循环。torch 只在 import 层面存在，
PPO 不参与（本验证只覆盖「采多少」）。

三条证据（计划 §3-P2 的①②③）：

  ① 定额达成：est 高估（20 vs 实际 14）⇒ 先欠后补，**2 波内收敛**且过冲 ≤ 1 波；
  ② 跨重启一致：崩在补波中间 ⇒ 重跑初波 pairs 逐字节同一，且补波**不换签**（无新 seed）；
  ③ 短局关不饿死长局关：stage 0 每局 5 帧、stage 1 每局 50 帧 ⇒ 长局关达标后不再被打扰，
     短局关自己被补到波次上限（顺带证明账本对两种落盘 schema 都认：
     一关写单局 `nSamples`、另一关写聚合单局 `totalSamples`）；
  ④ 尾部竞速下的配额算术（tail fan-out + dup 副本）：绝不重复计数；白跑的局（dup_settle
     把共享 shard 目录退休）按掉局处理、被补波补回；补不回必须响亮（未达标清单）。

运行（集成层；2026-09-15 起随 e2e/ 一并进入 python-gate 与 CI）：

    bash tools/githook/nn-py-safe.sh -m pytest e2e/test_volume_e2e.py -n 4 -q

（勿用裸 `python -m pytest`——AGENTS §5：沙箱删除守卫下会静默挂死。）
"""

from __future__ import annotations

import gzip
import io
import json
import shutil
import struct
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dist_common
from platform_utils import rmtree_best_effort
from rl.loop_core import TrainingLoop
from rl.reward_library import METRICS_DIM
from schema import BOARD, FIRE_DIM, MASK_DIM, MOVE_DIM, OBS_CHANNELS, SCALAR_DIM

#: 假节点的 ping 门（与 dispatch.bun_version / compute_code_hash 同源）。
_BUN = shutil.which("bun")


def _synth_payload(n: int) -> dict[str, np.ndarray]:
    """n 步的单局样本包（字段集与真 exporter 同构；n 即本局的 transitions 数）。"""
    rng = np.random.default_rng(11)
    done = np.zeros(n, dtype=np.int64)
    if n:
        done[-1] = 1
    return {
        "obs": rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8),
        "scalars": rng.standard_normal((n, SCALAR_DIM)).astype(np.float32),
        "a_move": rng.integers(0, MOVE_DIM, n).astype(np.int64),
        "a_fire": rng.integers(0, FIRE_DIM, n).astype(np.int64),
        "lp_move": -np.abs(rng.standard_normal(n)).astype(np.float32) - 0.05,
        "lp_fire": -np.abs(rng.standard_normal(n)).astype(np.float32) - 0.05,
        "value": rng.standard_normal(n).astype(np.float32),
        "metrics": np.zeros((n + 1, METRICS_DIM), dtype=np.float64),
        "done": done,
        "mask": np.ones((n, MASK_DIM), dtype=np.int64),
    }


def _npy_bytes(arr: np.ndarray) -> bytes:
    bio = io.BytesIO()
    np.lib.format.write_array(bio, arr)
    return bio.getvalue()


def _pack(stage: int, seed: int, wver: str, n: int, schema: str) -> bytes:
    """单局容器（gzip + `0x42435632` 头）——schema 决定落盘 manifest 的字段形态。

    schema="nSamples"：TS exporter 的正规单局形（`export-rl-rollout.ts`）；
    schema="totalSamples"：远端/队列 path 的聚合单局形（`dist_common.write_shard`
    原样写 agent 返回的 manifest ⇒ 盘上两种都会出现）。账本必须两种都认。
    """
    if schema == "nSamples":
        manifest: dict[str, Any] = {
            "wver": wver,
            "stage": stage,
            "seed": seed,
            "nSamples": n,
            "ticks": n,
            "outcome": "timeout",
            "score": 0.4,
            "node": "fake",
        }
    else:
        manifest = {
            "wver": wver,
            "stage": stage,
            "seed": seed,
            "games": 1,
            "outcomes": {"timeout": 1},
            "totalSamples": n,
            "totalTicks": n,
            "scoreList": [0.4],
            "dimLists": {},
            "node": "fake",
        }
    header_files, body = [], b""
    for name, arr in _synth_payload(n).items():
        fname = f"{name}.npy"
        raw = _npy_bytes(arr)
        header_files.append({"name": fname, "len": len(raw)})
        body += (
            struct.pack(">H", len(fname)) + fname.encode() + struct.pack(">Q", len(raw)) + raw
        )
    header = json.dumps({"manifest": manifest, "files": header_files}).encode()
    return gzip.compress(struct.pack(">I", 0x42435632) + struct.pack(">I", len(header)) + header + body)


class _VolServer(ThreadingHTTPServer):
    """假 sim 节点：每关的「局有多长」与「用哪种 manifest schema」按 stage 配置。"""

    def __init__(self, *a: Any, **kw: Any) -> None:
        self.samples: dict[int, int] = {}  # stage → 本关每局 transitions
        self.schema: dict[int, str] = {}  # stage → manifest 形态
        #: stage → 该关**前 k 局**零样本（模拟掉局：真派发真落盘、transitions=0）。
        self.zero_first: dict[int, int] = {}
        #: 竞速控制（尾部 fan-out 时同一 (stage,seed) 会有多份副本）：
        #: `dup_hang` = 第 2 份及以后的副本延迟秒数。0 = 副本与主副本同速（谁先在
        #: `seen` 里登记由线程调度决定，可能双双通过锁外的 validate_result ⇒ 真
        #: double-settle）；>0 = **主副本稳定赢**（副本后到、静默丢弃）。
        self.dup_hang: float = 0.0
        #: 首次派发即挂起的对（in-flight race 的必要条件：pending 清空后仍有在飞局）。
        self.slow_once: set[tuple[int, int]] = set()
        self.slow_sec: float = 0.0
        self._slow_done: set[tuple[int, int]] = set()
        self.dispatched: list[tuple[int, int]] = []  # 真派发顺序（含重复副本）
        self.fetch_n: dict[tuple[int, int], int] = {}
        self.lock = threading.Lock()
        super().__init__(*a, **kw)

    def note(self, key: tuple[int, int]) -> int:
        """登记一次派发，返回这是该对的第几份副本（0 = 首份）。"""
        with self.lock:
            idx = self.fetch_n.get(key, 0)
            self.fetch_n[key] = idx + 1
            self.dispatched.append(key)
            return idx

    def games_for(self, stage: int) -> int:
        with self.lock:
            return sum(1 for s, _ in self.dispatched if s == stage)

    def dup_copies(self) -> int:
        """重复副本数（真派发次数 - 去重对数）：>0 = 竞速真的发生了。"""
        with self.lock:
            return len(self.dispatched) - len(set(self.dispatched))


class _VolAgent(BaseHTTPRequestHandler):
    _cache: dict[str, str] = {}

    @staticmethod
    def _bun_version() -> str:
        """取本机 bun 版本；失败不缓存（xdist 高负载下曾把 '?' 永久写进类缓存，
        后续轮次节点被判 bun mismatch 整台排除，race 用例退化成单节点必红）。"""
        c = _VolAgent._cache
        got = c.get("bun")
        if got and got != "?":
            return got
        if not _BUN:
            return "?"
        import subprocess

        for _attempt in range(3):
            try:
                ver = (
                    subprocess.run(
                        [_BUN, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    ).stdout.strip()
                    or "?"
                )
                if ver != "?":
                    c["bun"] = ver
                    return ver
            except Exception:
                # sleep-ok: 轮询步长（等的是「bun 版本已可问出」这个状态）
                time.sleep(0.05)
        return "?"

    @property
    def _srv(self) -> _VolServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, *_a: Any) -> None:
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
            c = _VolAgent._cache
            bun = self._bun_version()
            if "codeHash" not in c:
                c["codeHash"] = dist_common.compute_code_hash()
            self._json(
                {
                    "codeHash": c["codeHash"],
                    "bunVersion": bun,
                    "cpus": 4,
                    "evalSupport": True,
                }
            )
            return
        if u.path == "/v1/task":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            stage, seed = int(q["stage"]), int(q["seed"])
            pair = (stage, seed)
            served = self._srv.games_for(stage)
            copy_idx = self._srv.note(pair)
            # 首份慢局：制造 in-flight 尾局，pending 清空后空槽 race
            with self._srv.lock:
                slow_hit = pair in self._srv.slow_once and pair not in self._srv._slow_done
                if slow_hit:
                    self._srv._slow_done.add(pair)
            if slow_hit and self._srv.slow_sec > 0:
                # sleep-ok: 夹具模拟的工作量：慢节点一口 slow_sec 秒
                time.sleep(self._srv.slow_sec)
            # 竞速时序控制：副本慢一拍（延迟在响应前，输赢才可塑）
            if copy_idx >= 1 and self._srv.dup_hang > 0:
                # sleep-ok: 夹具模拟的工作量：竞速副本挂住 dup_hang 秒
                time.sleep(self._srv.dup_hang)
            n = self._srv.samples.get(stage, 30)
            if served < self._srv.zero_first.get(stage, 0):
                n = 0  # 掉局：零样本（真落盘、真计数为 0）
            schema = self._srv.schema.get(stage, "nSamples")
            body = _pack(stage, seed, q["wver"], n, schema)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "nf"})

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._json({"cache": "kept"})


class _Loop:
    """真 TrainingLoop 方法 + 最小 self（采集侧零桩：真调度器、真账本、真补波）。"""

    def _volume_active(self) -> bool:
        return TrainingLoop._volume_active(cast(Any, self))

    def _volume_stages(self) -> list[int]:
        return TrainingLoop._volume_stages(cast(Any, self))

    def _volume_est_samples(self) -> int:
        return TrainingLoop._volume_est_samples(cast(Any, self))

    def _iteration_pairs(self, it: int) -> list[tuple[int, int]]:
        return TrainingLoop._iteration_pairs(cast(Any, self), it)

    def _dispatch_volume_wave(
        self, it: int, pairs: list[tuple[int, int]], dist_cfg: dict | None
    ) -> dict:
        self.waves.append(list(pairs))  # 真调用边界（供逐波对账）
        return TrainingLoop._dispatch_volume_wave(cast(Any, self), it, pairs, dist_cfg)

    def _volume_collect_continuous(self, it: int, dist_cfg: dict | None) -> None:
        TrainingLoop._volume_collect_continuous(cast(Any, self), it, dist_cfg)

    def _volume_topup(self, it: int, dist_cfg: dict | None) -> None:
        TrainingLoop._volume_topup(cast(Any, self), it, dist_cfg)

    def __init__(
        self,
        tmp: Path,
        *,
        stages: str = "0-1",
        target: int = 200,
        est: int = 20,
        max_games_per_stage: int = 0,
        rotate_seed: int = 777,
        it: int = 1,
    ) -> None:
        traj = tmp / "traj"
        out = tmp / "weights.json"
        if not out.exists():
            # 内容带本用例唯一标记：`wver` = 文件指纹，而 `_WEIGHTS_PUSHED` 是**进程内跨用例**
            # 的账本（键 `(kind, wver)`）——全仓库的哑权重若都是 `{"stub": true}`，后一个用例
            # 就会走 “kept / skip POST”（docs/nn/engineering.md §13；门禁里那条随机 flake 的根）。
            out.write_text(json.dumps({"stub": True, "case": tmp.name}), encoding="utf-8")
        self.args = types.SimpleNamespace(
            target_transitions=target,
            est_samples_per_game=est,
            max_games_per_stage=max_games_per_stage,
            stages=stages,
            seeds="0-1",
            seed_rotate=0,
            rotate_stages=0,
            total_stages=2,
            seeds_per_stage=2,
            curriculum_stages="",
            curriculum_start=4,
            curriculum_every=8,
            curriculum_grow=4,
            collect_only=0,
            stream=0,
            workers=4,
            local_slots=0,
            max_ticks=300,
            difficulty="hard",
            mode="per-tick",
            out=str(out),
            traj=str(traj),
            course_name="",
        )
        self.it = it
        self.bun = _BUN or "bun-stub"
        self._rotate_seed = rotate_seed
        self._traj_root = traj
        self._jsonl_path = traj / "training_log.jsonl"
        self._traj_dir = traj / f"it{it}"
        self._traj_dir.mkdir(parents=True, exist_ok=True)
        self._report: dict = {
            "games": 0,
            "winRate": 0.0,
            "outcomes": {},
            "totalSamples": 0,
            "totalTicks": 0,
            "scoreList": [],
            "dimLists": {},
        }
        self._stream_meta: dict | None = None
        self._course_fp: str | None = None
        # D14 语义身份（§2/A）：与 `_course_fp` 成对（生产端在 `_setup` 里一起设）
        self._corpus_fp: str = ""
        # 起始分布护栏（P3.5）：同一处建立（本桩无课程 ⇒ 恒 False）
        self._state_init = False
        self._extra_wver: str | None = None
        self._volume_target: int | None = None
        self._volume_collected: int | None = None
        self._volume_waves: int = 0
        self._volume_g0: int = 0
        self._volume_est: int = 0
        self._volume_capped: bool = False
        # 真补波循环需要的、串行 dist 路径用不到但 dispatch_rollout_phase 会转发的东西
        self._model = None
        self._opt = None
        self._device = None
        self._ref_model = None
        self.ppo_backend = None
        self.update_kwargs = None
        self._start_it = 0
        self._journal: Any = None
        self._collect_child = None
        self._spawned_early = False
        self._eval_thread = None
        self._eval_gate = None
        # 每波真派发的 pairs（真调用边界，供重启一致性断言）
        self.waves: list[list[tuple[int, int]]] = []
        self.wave0: list[tuple[int, int]] = []

    def _commit_journal(self) -> Any:
        if self._journal is None:
            from rl.commit_journal import CommitJournal

            # 与生产同路径（loop_steps._commit_journal = <traj_dir>/commit_journal.jsonl）
            self._journal = CommitJournal(self._traj_dir / "commit_journal.jsonl")
        return self._journal

    def _volume_journal_replay(self, it: int) -> Any:
        return TrainingLoop._volume_journal_replay(cast(Any, self), it)

    # ---- 一轮 = 初波（真派发）+ 补波循环（真循环）----
    # 注意：连续配额 v2 生产路径不再用 _volume_topup；本 e2e 仍驱动 wave 版
    # （volume_waves 纯逻辑保留）。_iteration_pairs 已改为 waves=0，这里手动
    # 记「初波已跑」以兼容 wave 语义的 topup 断言。
    def run_iteration(self, cfg: dict) -> None:
        pairs = self._iteration_pairs(self.it)
        self.wave0 = list(pairs)
        self._volume_waves = 1
        self._report = self._dispatch_volume_wave(self.it, pairs, cfg)
        self._volume_topup(self.it, cfg)

    @property
    def journal_path(self) -> Path:
        return self._traj_dir / "commit_journal.jsonl"

    def crash_mid_wave(self, wave_idx: int) -> None:
        """模拟「采集到第 wave_idx 波中间就进程消失」：

        ① 该波已落盘的 shard 全删（未结算）；② WAL 里该波的 finish 抹掉（只剩 start）
        ——真崩溃留下的就是这两样。"""
        for stage, seed in self.waves[wave_idx]:
            d = self._traj_dir / f"rl_s{stage}_seed{seed}"
            if d.exists():
                shutil.rmtree(d)
        key = f'"round": "{self.it}:w{wave_idx}", "ts"'
        keep = [
            line
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
            if not (key in line and '"op": "finish"' in line)
        ]
        self.journal_path.write_text("\n".join(keep) + "\n", encoding="utf-8")


def _env(
    tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fanout: bool = False,
    **server_kw: int | str,
) -> tuple[_VolServer, dict, Path]:
    """拉起假节点 + dist cfg（与 e2e/test_run_rl.py 的 _itest_env 同形）。

    `fanout=True` 打开尾部竞速（tail fan-out）：同一 (stage,seed) 会同时派多份副本，
    先结算者赢、后到者**连 shard 目录一起退休**。默认关（配额算术不被竞速时序抖动
    影响），专项竞速用例显式打开。
    """
    monkeypatch.setattr(dist_common, "load_dist_config", lambda: None)
    # 预热 bun 版本：避免首 ping 在 xdist 负载下拿到 "?" 而整台节点被排除。
    _VolAgent._bun_version()
    srv = _VolServer(("127.0.0.1", 0), _VolAgent)
    srv.samples = {int(k.split("_")[1]): int(v) for k, v in server_kw.items() if k.startswith("n_")}
    srv.schema = {int(k.split("_")[1]): str(v) for k, v in server_kw.items() if k.startswith("s_")}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {
        "nodes": [
            {
                "id": "fake",
                "url": f"http://127.0.0.1:{srv.server_address[1]}",
                "authKey": "",
                "concurrency": 4,
                "enabled": True,
            }
        ],
        "policy": {
            "taskTimeoutSec": 120,
            "queueWindowSec": 120,
            "statusTimeoutSec": 3,
            "agentRescanSec": 1,
            "tailFanoutN": 4 if fanout else 0,
            "tailFanoutDup": 2 if fanout else 0,
        },
    }
    return srv, cfg, tmp


def _settled(traj: Path, out: Path) -> dict[int, tuple[int, int]]:
    """盘上账本（与 loop 同口径：真 resume 实现 + 真 wver）。"""
    from rl.resume import settled_stage_totals

    return settled_stage_totals(traj, dist_common.weights_fingerprint(str(out)))


# ────────────────────────── ① 定额达成（±1 波） ──────────────────────────


def test_quota_converges_within_one_wave(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """est=20 高估、实际每局 14 transitions ⇒ 5 局初波不够，2 波内补到 ≥ 达标线。

    过冲 ≤ 1 波（最后一个补波的大小）——这就是「收敛误差 ±1 波」的可执行形式。
    """
    srv, cfg, _ = _env(tmp, monkeypatch, n_0=14, n_1=14)
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=20)
        loop.run_iteration(cfg)

        # 每个关的达标线 = ceil(200/2) = 100
        settled = _settled(loop._traj_dir, Path(loop.args.out))
        assert set(settled) == {0, 1}
        for stage in (0, 1):
            games, transitions = settled[stage]
            assert transitions >= 100, f"stage {stage} 未达标: {transitions}"
            # 过冲不超过最后一个补波（±1 波）：最多多采 14 transitions 的整数倍，
            # 且实际过冲必须小于「最后一波局数 × 每局 transitions」
            assert transitions - 100 < 14 * 2, f"stage {stage} 过冲过大: {transitions}"
            assert games <= 5 + 3 + 1, f"stage {stage} 局数超预算: {games}"

        assert loop._volume_waves == 3, "应为 初波 + 2 补波"
        assert loop._volume_collected == sum(t for _g, t in settled.values())
        assert loop._volume_capped is False
        # WAL：每波补波决策都闭环（无 pending）
        assert loop._commit_journal().pending() == []
        # 报告覆盖全部补波（不是只有初波）
        assert loop._report["games"] == sum(g for g, _t in settled.values())
        assert loop._report["totalSamples"] == loop._volume_collected
    finally:
        srv.shutdown()


def test_no_topup_when_initial_wave_meets_quota(
    tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """est/实际都 20 ⇒ 初波（每关 5 局 = 100）恰好达标：一个补波都不派。"""
    srv, cfg, _ = _env(tmp, monkeypatch, n_0=20, n_1=20)
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=20)
        loop.run_iteration(cfg)
        assert loop._volume_waves == 1
        assert len(srv.dispatched) == 10  # 2 关 × 5 局，零补波
        assert loop._volume_collected == 200
    finally:
        srv.shutdown()


# ────────────────────────── ② 跨重启一致（同 pairs） ──────────────────────────


def test_cross_restart_same_pairs_no_reroll(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """崩在补波中间 ⇒ 重启：初波 pairs 逐字节同一，且**重放那一波（不重新抛硬币）**。

    首跑 3 波收敛（w0 5 局 → w1 2 局 → w2 1 局/关）。崩在 w2 中间：删它的 shard + 抹掉
    WAL 里它的 finish。重启后必须：
      ① 初波 pairs 与首跑逐字节同一（同 (rotateSeed, it) 键控）；
      ② 直接重放 w2 的对局表——**不是**按账本重算出一个「新的 w1」（那会用 w1 的种子流
         去补 w2 的缺口 = 同观测史不同波次序列）；
      ③ 波次预算不因重启重领（w2 已用过 ⇒ 重放后即触 wave_cap，不再开新波）；
      ④ 补回后账本 ≥ 达标线。
    """
    srv, cfg, _ = _env(tmp, monkeypatch, n_0=14, n_1=14)
    try:
        first = _Loop(tmp, stages="0-1", target=200, est=20)
        first.run_iteration(cfg)
        assert len(first.waves) == 3, f"首跑应为 3 波，实际 {len(first.waves)}"
        assert first._volume_waves == 3
        interrupted = len(first.waves) - 1  # 最后一波 = w2
        srv.dispatched.clear()
        first.crash_mid_wave(interrupted)

        # 进程重启：新 loop（新内存状态、同 traj/权重/rotateSeed/课程）
        resumed = _Loop(tmp, stages="0-1", target=200, est=20)
        resumed.run_iteration(cfg)

        # ① 初波 pairs 逐字节同一
        assert resumed.waves[0] == first.waves[0]
        # ② 重放被中断的那一波：对局表/种子流逐字节同一
        assert resumed.waves[1] == first.waves[interrupted]
        # ③ 预算不重领 ⇒ 重放后不再开新波
        assert len(resumed.waves) == 2, resumed.waves
        assert resumed._volume_waves == 3
        # ④ 缺口补回，账本回到达标线之上
        settled = _settled(resumed._traj_dir, Path(resumed.args.out))
        assert all(t >= 100 for _g, t in settled.values()), settled
    finally:
        srv.shutdown()


def test_dropped_games_trigger_topup(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """掉局（零样本）不计入配额 ⇒ 天然触发补采（计划的「特性」条款）。

    stage 0 前 2 局零样本（真派发、真落盘、transitions=0）：它们**占局数**（硬顶口径）
    但不进配额 ⇒ 该关必须被补到达标线；正常关（stage 1）不被牵连。
    """
    srv, cfg, _ = _env(tmp, monkeypatch, n_0=14, n_1=14)
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=14)
        # est=14（估准）⇒ 初波 8 局/关；stage 0 前 2 局零样本 ⇒ 6 局×14=84，欠 16 需补
        srv.zero_first = {0: 2}
        loop.run_iteration(cfg)

        settled = _settled(loop._traj_dir, Path(loop.args.out))
        assert settled[0][1] >= 100, f"掉局关未补到达标线: {settled[0]}"
        assert settled[0][0] == 8 + 2, f"掉局应占局数并额外补 2 局: {settled[0]}"
        assert loop._volume_waves == 2, "应只补一波"
        # 正常关：8 局一次到位，零补波，不受 stage 0 掉局牵连
        assert settled[1] == (8, 112)
        assert len([p for p in srv.dispatched if p[0] == 1]) == 8
        assert loop._volume_capped is False
    finally:
        srv.shutdown()


# ────────────────────────── ③ 短局关不饿死长局关 ──────────────────────────


def test_short_stage_does_not_starve_long_stage(
    tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stage 0 每局 5 帧、stage 1 每局 50 帧（且两种 manifest schema 各一）⇒

    · 长局关一次到位后**不再被打扰**（补波只补 shortfall 的关）；
    · 短局关自己被补到波次上限（不无限补、也不拖累长局关）；
    · 两种落盘 schema（`nSamples` / `totalSamples`）账本都认。
    """
    srv, cfg, _ = _env(
        tmp, monkeypatch, n_0=5, n_1=50, s_0="nSamples", s_1="totalSamples"
    )
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=10)
        loop.run_iteration(cfg)

        # 初波每关 ceil(100/10) = 10 局
        games0 = [p for p in srv.dispatched if p[0] == 0]
        games1 = [p for p in srv.dispatched if p[0] == 1]
        # 长局关：初波 10 局 × 50 = 500 ≥ 100 ⇒ 达标，**一个补波都没有**
        assert len(games1) == 10, f"长局关被补波打扰: {len(games1)}"
        # 短局关：10 局 × 5 = 50 < 100 ⇒ 连补到波次上限
        assert len(games0) > 10, "短局关应当被补波"
        assert loop._volume_waves == 3, "补到 max_waves 上限即停"

        settled = _settled(loop._traj_dir, Path(loop.args.out))
        assert settled[1][1] >= 100  # 长局关达标
        assert settled[0][1] < 100  # 短局关仍未达标 ⇒ 停因是波次上限
        assert loop._volume_capped is False  # 不是硬顶（是波次上限）
        # 两种 schema 都进了账本（长局关若漏认 totalSamples 会显示 0 ⇒ 会被反复补波）
        assert settled[1][1] == 500
        assert settled[0][1] == 10 * 5 + 5 * 5 + 3 * 5
    finally:
        srv.shutdown()


# ────────────── ④ in-flight race 下的配额算术 ──────────────
#
# 2026-09-16：去掉 EWMA hold / fan-out / dup 上限。pending 有活谁空谁接；pending
# 清空后空槽复制**其它节点**在跑的尾局，先返回者进账本；败者静默丢弃或 dup_settle
# 退休共享 shard。对配额算术的要求：
#   ① **绝不重复计数**：账本局数 ≤ 该关去重派发对数，且 transitions == 局数 × 每局；
#   ② 白跑的局按掉局处理 ⇒ 补波补回；补不回也必须是**响亮**的（未达标清单），
#      不许把缺口当「达标」糊过去。


def _add_second_node(cfg: dict, srv: _VolServer) -> None:
    """同 FakeServer、独立 node id——race 排除同节点持有，单节点集群永不竞速。"""
    cfg["nodes"].append(
        {
            "id": "fake2",
            "url": f"http://127.0.0.1:{srv.server_address[1]}",
            "authKey": "",
            "concurrency": 4,
            "enabled": True,
        }
    )


def test_racing_duplicates_do_not_distort_the_ledger(
    tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """in-flight race 触发（slow_once 尾局 + 空槽）⇒ 账本不重复计数。"""
    srv, cfg, _ = _env(tmp, monkeypatch, fanout=True, n_0=14, n_1=14)
    _add_second_node(cfg, srv)
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=14)
        # 初波 16 局 / 并发 4：钉一局首派慢 0.4s，其余快局清空 pending 后 race 它。
        srv.slow_once = {(0, loop._iteration_pairs(1)[0][1])}
        srv.slow_sec = 0.4
        srv.dup_hang = 0.05  # race 副本略慢 ⇒ 主副本/先返回者赢
        loop.run_iteration(cfg)

        # 竞速真的发生了（否则本用例什么也没验到）
        assert srv.dup_copies() > 0, "in-flight race 未触发——需要 slow_once 尾局被空槽复制"
        settled = _settled(loop._traj_dir, Path(loop.args.out))
        unique = {(s, sd) for s, sd in srv.dispatched}
        # 不重复计数：每关落盘局数 == 该关去重派发对局数（副本没被多算一局）
        for stage in (0, 1):
            n_unique = len([p for p in unique if p[0] == stage])
            assert settled[stage] == (n_unique, n_unique * 14), f"stage {stage}: {settled[stage]}"
        # est 估准（14）⇒ 初波 8 局/关即达标，零补波（副本不占配额也不拖慢收敛）
        assert loop._volume_waves == 1
        assert all(t >= 100 for _g, t in settled.values())
        assert loop._commit_journal().pending() == []
    finally:
        srv.shutdown()


def test_racing_double_settle_never_inflates_and_quota_stays_sound(
    tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """副本与主副本同速（dup_hang=0）⇒ 可能真的 double-settle（共享 shard 被退休）。

    真 double-settle 的时序不可按需复现（见下一条的说明），所以这里只钉**无论哪种
    结局都必须成立**的性质：不重复计数、账本不长于去重派发数、达标或响亮停、WAL 闭环。
    """
    srv, cfg, _ = _env(tmp, monkeypatch, fanout=True, n_0=14, n_1=14)
    _add_second_node(cfg, srv)
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=14)
        srv.slow_once = {(0, loop._iteration_pairs(1)[0][1])}
        srv.slow_sec = 0.4
        srv.dup_hang = 0.0
        loop.run_iteration(cfg)

        assert srv.dup_copies() > 0, "in-flight race 未触发——本用例什么也没验到"
        settled = _settled(loop._traj_dir, Path(loop.args.out))
        unique = set(srv.dispatched)
        for stage in (0, 1):
            games, transitions = settled[stage]
            dispatched = len([p for p in unique if p[0] == stage])
            assert transitions == games * 14, f"stage {stage} 重复计数: {settled[stage]}"
            assert games <= dispatched, f"stage {stage} 账本局数 > 派发对数: {settled[stage]}"
        # 达标，或响亮停（硬顶 / 波次上限）——不允许静默短采
        met = all(t >= 100 for _g, t in settled.values())
        # 缺口只允许出现在预算耗尽时：预算没用完却未达标 = 静默短采（本用例最硬的一条）
        if not loop._volume_capped and loop._volume_waves < 3:
            assert met, f"波次预算未耗尽却未达标: {settled}"
        assert met or loop._volume_capped or loop._volume_waves >= 3, f"静默短采: {settled}"
        # iteration 事件的采集量与账本一致（缺口如实上报，不假报达标）
        assert loop._volume_collected == sum(t for _g, t in settled.values())
        assert loop._commit_journal().pending() == []
        print(
            f"[race] double-settle 边界: dispatched={len(srv.dispatched)} "
            f"unique={len(unique)} settled={settled} waves={loop._volume_waves}"
        )
    finally:
        srv.shutdown()


def test_dup_settle_shard_loss_is_compensated(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**确定性**复现 dup_settle 的后果（共享 shard 目录被 rmtree）⇒ 补波补回、账务自洽。

    为什么不跑真时序：double-settle 要求两份结果都抢在对方进 `seen` 前通过锁外
    validate——窗口只有一次 `write_shard`，无法按需复现（`e2e/test_run_rl.py` 的 I9
    同样以「结构性证据 OR」替代时序断言）。上一条已覆盖真竞速下的不变式；这里把
    调度器的**那一刀**（`dispatch.py` dup_settle 分支的 `rmtree_best_effort(vdir)`，
    vdir = 该 (stage,seed) 的 shard 目录）显式落下，验我们这一侧的反应：账本变短 ⇒
    逐关补波 ⇒ 达标 + 不重复计数 + WAL 闭环。派发关掉竞速 ⇒ 注入的损失是唯一变量。
    """
    srv, cfg, _ = _env(tmp, monkeypatch, n_0=14, n_1=14)
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=14)
        pairs = loop._iteration_pairs(1)
        loop._report = loop._dispatch_volume_wave(1, pairs, cfg)
        unique = set(srv.dispatched)
        assert len(unique) == 16, f"初波应为 8 局/关: {sorted(unique)}"
        # est 估准 ⇒ 8 局/关 = 112t 本来一次到位
        assert _settled(loop._traj_dir, Path(loop.args.out)) == {0: (8, 112), 1: (8, 112)}

        # 每关退休 2 局（= 竞速后到者把共享 shard 目录删掉）
        for stage in (0, 1):
            for s, seed in [p for p in sorted(unique) if p[0] == stage][:2]:
                shard = loop._traj_dir / "dist" / "fake" / f"rl_s{s}_seed{seed}"
                assert shard.is_dir(), shard
                rmtree_best_effort(shard, ignore_errors=True)
        after_loss = _settled(loop._traj_dir, Path(loop.args.out))
        assert after_loss == {0: (6, 84), 1: (6, 84)}, after_loss

        loop._volume_topup(1, cfg)
        settled = _settled(loop._traj_dir, Path(loop.args.out))
        # 缺口 16t/关 ⇒ 补 ceil(16/14)=2 局/关 ⇒ 6+2=8 局；丢掉的局一局不欠
        assert settled == {0: (8, 112), 1: (8, 112)}, settled
        assert loop._volume_waves == 2
        assert loop._volume_capped is False
        assert loop._volume_collected == 224
        assert loop._commit_journal().pending() == []
    finally:
        srv.shutdown()


def test_racing_with_tight_budget_stops_loud_not_silent(
    tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """竞速 + 短局关 ⇒ 波次预算耗尽 ⇒ **响亮**停（日志给出未达标清单），不静默短采。"""
    # 补丁打在**实现模块**的命名空间上：`_volume_topup` 的日志（「未达标」/`wave_cap`）
    # 住在 `rl/loop_volume.py`（S4 第十八刀从 loop_core 搬来）——打 `rl.loop_core.log`
    # 是**静默空操作**（同名 seam 在两个命名空间里是两个各自真实的注入点）。
    import rl.loop_volume as vol_mod

    lines: list[str] = []
    real_log = vol_mod.log

    def _capture(msg: str) -> None:
        lines.append(str(msg))
        real_log(str(msg))

    monkeypatch.setattr(vol_mod, "log", _capture)
    srv, cfg, _ = _env(tmp, monkeypatch, fanout=True, n_0=5, n_1=50)
    try:
        # stage 0 每局 5t（估 10 ⇒ 初波 10 局只到 50t）⇒ 两波补到 90t 后触波次上限；
        # stage 1 每局 50t ⇒ 初波 500t 一次到位，不被短局关拖累。竞速全程只在尾部复制。
        loop = _Loop(tmp, stages="0-1", target=200, est=10)
        srv.dup_hang = 0.35  # 副本后到、静默丢弃 ⇒ 账本与派发对一一对应
        loop.run_iteration(cfg)

        settled = _settled(loop._traj_dir, Path(loop.args.out))
        assert loop._volume_waves == 3, "补到波次上限即停，不无限补"
        # 不重复计数（竞速副本不得被当成额外一局）
        for stage, (games, transitions) in settled.items():
            assert transitions == games * (5 if stage == 0 else 50), f"stage {stage}: {settled}"
        assert settled[1][1] >= 100, "长局关一次到位"
        assert settled[0][1] < 100, "本用例需要预算耗尽的场景"
        # 停采必须响亮：未达标清单进日志（iteration 事件的 transitions_collected 也如实上报）
        unmet = [ln for ln in lines if "未达标" in ln and "wave_cap" in ln]
        assert unmet, f"未达标清单缺失（响亮性回归）: {lines}"
        assert loop._volume_collected == sum(t for _g, t in settled.values())
        assert loop._commit_journal().pending() == []
    finally:
        srv.shutdown()


def test_game_cap_stops_short_stage_with_marker(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """硬顶：短局关补到 cap 即停 + `_volume_capped` 打标（配额未满）。"""
    srv, cfg, _ = _env(tmp, monkeypatch, n_0=5, n_1=50)
    try:
        loop = _Loop(tmp, stages="0-1", target=200, est=10, max_games_per_stage=12)
        loop.run_iteration(cfg)
        assert loop._volume_capped is True
        assert len([p for p in srv.dispatched if p[0] == 0]) == 12  # 初波 10 + 补 2 触顶
        assert loop._commit_journal().pending() == []
    finally:
        srv.shutdown()
