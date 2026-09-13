"""test_baseline_eval.py — it0 bc 权重基线评估（2026-09-12 用户）。

背景：in-loop eval 的配对基准此前恒取日志里**第一条** eval 行，而那条基准随 run
起点漂移（resume 时首条可能是 it50，配对比的是中途两点，不是"学会了多少"）。
新语义：主循环在 rollout 收官后，用课程 bc 权重补派一条 `iter=0` 的干净评估作
恒定基线，落账前每轮重试（2026-09-13 评审修订：首派撞上节点瞬时全挂不再等重启）。

覆盖（纯逻辑 + 注入，不起真 loop、不碰 torch、不发网络）：
  - eval_done_keys 双向隔离：基线按 iter==0（排除同 wver 的 it1 行与 B/C source
    行）；A-eval 按 min_iter=1（排除 it0 行、保留缺 iter 旧行与跨 iter 复用）。
  - baseline_summary_landed：同 wver 的 it0 summary 才算落账。
  - TrainingLoop._baseline_eval_weights 的前置条件（逐条反证）。
  - TrainingLoop._maybe_dispatch_baseline_eval：落账前每轮派、在飞跳过、
    复用当轮 eval_gate、带 it=0/baseline=True/独立 iter_id、失败不抛出。
"""

from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dist_common
from rl.eval_local import BASELINE_EVAL_ITER, baseline_summary_landed, eval_done_keys
from rl.loop_core import TrainingLoop

WVER = "a" * 16


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "iters": 160,
        "out": "tmp/x/weights.json",
        "traj": "tmp/x",
        "mode": "per-tick",
        "course_obj": object(),
        "eval_games_per_stage": 100,
        "eval_every": 5,
        "bc": "tmp/x/bc.json",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


NODES = {"nodes": [{"id": "n1", "url": "http://n1"}]}


def _loop(tmp_path: Path, **kw) -> TrainingLoop:
    loop = TrainingLoop(_args(**kw), None, "bun", {})
    loop._traj_root = tmp_path
    loop._traj_dir = tmp_path / "it1"
    return loop


def test_baseline_eval_iter_constant() -> None:
    assert BASELINE_EVAL_ITER == 0


def test_eval_done_keys_iter_isolation(tmp_path: Path) -> None:
    """账本双向隔离：基线不吞 A-eval（iter 隔离），A-eval 也不吞基线（min_iter）。"""
    log = tmp_path / "eval_log.jsonl"

    def row(iter_: int | None, seed: int, **extra: object) -> str:
        r: dict[str, object] = {
            "event": "eval",
            "wver": WVER,
            "stage": 2000,
            "seed": seed,
        }
        if iter_ is not None:
            r["iter"] = iter_
        r.update(extra)
        return json.dumps(r)

    log.write_text(
        "\n".join(
            [
                row(0, 860001),  # it0 基线局
                row(1, 860001),  # 同 wver 的 A-eval 局（bc 与 it1 指纹偶同时存在）
                row(1, 860002),
                row(None, 860003),  # 缺 iter 的旧行（不过度收口）
                row(0, 860004, source="B"),  # B/C evalboard 行恰好 iter=0（畸形批）
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # 基线账本：只认 iter==0 且非 B/C 的行——it1 行、旧行、B/C 行都不算基线已评估
    assert eval_done_keys(log, WVER, BASELINE_EVAL_ITER) == {(2000, 860001)}
    # A-eval 账本（min_iter=1）：it0 基线行不挡 A-eval；旧行与跨 iter 复用保留
    assert eval_done_keys(log, WVER, min_iter=1) == {
        (2000, 860001),
        (2000, 860002),
        (2000, 860003),
    }
    # 不传过滤 → 旧口径（同 wver 全部局），调用方零行为变化
    assert eval_done_keys(log, WVER) == {
        (2000, 860001),
        (2000, 860002),
        (2000, 860003),
        (2000, 860004),
    }
    assert eval_done_keys(tmp_path / "nope.jsonl", WVER, BASELINE_EVAL_ITER) == set()


def test_baseline_summary_landed(tmp_path: Path) -> None:
    """落账判定：同 wver 的 it0 summary 才算数（bc 换文件 → 重派新基线）。"""
    traj = tmp_path / "it1"  # helper 收 traj_dir，账本在其上一级（与 eval_dispatch 同约定）
    assert baseline_summary_landed(traj, WVER) is False  # 无 eval_log
    log = tmp_path / "eval_log.jsonl"

    def summ(iter_: int, wver: str) -> str:
        return json.dumps(
            {"event": "eval_summary", "iter": iter_, "wver": wver, "games": 1, "wins": 0}
        )

    log.write_text(
        "\n".join([summ(1, WVER), summ(0, "b" * 16)]) + "\n", encoding="utf-8"
    )
    assert baseline_summary_landed(traj, WVER) is False  # 只有 it1 / 异 wver 的 it0
    log.write_text(summ(0, WVER) + "\n", encoding="utf-8")
    assert baseline_summary_landed(traj, WVER) is True


def test_start_it_zero_rejected() -> None:
    """--start-it 0 与基线的 dist 键空间（{runId}.0）撞键 → 启动期响亮拒绝；≥1 放行。"""
    import types

    from rl.config import validate_args

    with pytest.raises(SystemExit):
        validate_args(types.SimpleNamespace(start_it=0))
    validate_args(types.SimpleNamespace(start_it=1))  # 合法：不抛
    validate_args(types.SimpleNamespace())  # 缺省（auto = 末迭代+1）：不抛


def test_baseline_weights_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bc = tmp_path / "bc.json"
    bc.write_text("{}", encoding="utf-8")
    ok = _loop(tmp_path, bc=str(bc))
    assert ok._baseline_eval_weights(NODES) == str(bc)

    # 逐条反证：任一前置条件不满足 → None（不派基线，行为与旧版一致）
    assert _loop(tmp_path, bc=str(bc), mode="intent")._baseline_eval_weights(NODES) is None
    assert _loop(tmp_path, bc=str(bc), course_obj=None)._baseline_eval_weights(NODES) is None
    assert (
        _loop(tmp_path, bc=str(bc), eval_games_per_stage=0)._baseline_eval_weights(NODES) is None
    )
    no_eval = _loop(tmp_path, bc=str(bc))
    no_eval._eval_every = 0  # eval 关闭（--eval-every 0）
    assert no_eval._baseline_eval_weights(NODES) is None
    # nodes=[] / 全 disabled → 纯本地路径本就不派 A-eval，也不派基线
    assert _loop(tmp_path, bc=str(bc))._baseline_eval_weights(None) is None
    assert _loop(tmp_path, bc=str(bc))._baseline_eval_weights({"nodes": []}) is None
    assert (
        _loop(tmp_path, bc=str(bc))._baseline_eval_weights(
            {"nodes": [{"id": "n1", "enabled": False}]}
        )
        is None
    )
    # bc 路径不存在（换机器漏同步）→ 不派，绝不裸奔
    assert _loop(tmp_path, bc=str(tmp_path / "missing.json"))._baseline_eval_weights(NODES) is None
    assert _loop(tmp_path, bc="")._baseline_eval_weights(NODES) is None


class _FakeThread(threading.Thread):
    """is_alive 可注入的假线程（不 start；子类化以类型兼容 _baseline_eval_thread 槽位）。"""

    def __init__(self, alive: bool) -> None:
        super().__init__(daemon=True, name="fake-baseline-eval")
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def test_dispatch_retries_until_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落账前每轮派发（不只 start_it）；在飞跳过；复用当轮 eval_gate；落账即停。"""
    bc = tmp_path / "bc.json"
    bc.write_text("{}", encoding="utf-8")
    loop = _loop(tmp_path, bc=str(bc))
    loop._start_it = 1
    loop._eval_gate = threading.Event()  # 生产路径：基线复用当轮 A-eval 的本地门
    calls: list[dict] = []

    def _fake_bg(bun, rl_path, traj_dir, args, cfg, **kw):
        calls.append({"bun": bun, "rl_path": rl_path, "traj_dir": traj_dir, "cfg": cfg, **kw})
        return _FakeThread(False)  # 线程立刻结束 → 下一轮可重试

    monkeypatch.setattr("rl.eval_dispatch.dispatch_eval_bg", _fake_bg)

    loop._maybe_dispatch_baseline_eval(NODES)  # 首轮（it1）：未落账 → 派
    assert len(calls) == 1
    c = calls[0]
    assert c["rl_path"] == str(bc)  # 用 bc 权重，不是 args.out
    assert c["it"] == BASELINE_EVAL_ITER
    assert c["baseline"] is True
    assert c["iter_id"].endswith(f".{BASELINE_EVAL_ITER}")  # 与 A-eval 的缓存键空间隔离
    assert c["cfg"] == NODES
    assert c["local_gate"] is loop._eval_gate  # 本地兜底可用（生产语义钉死）

    loop._maybe_dispatch_baseline_eval(NODES)  # 尚未落账 → 重试（不再要求 it == start_it）
    assert len(calls) == 2

    # 在飞（线程还活着）→ 跳过
    loop._baseline_eval_thread = _FakeThread(True)
    loop._maybe_dispatch_baseline_eval(NODES)
    assert len(calls) == 2
    loop._baseline_eval_thread = None

    # 落账（同 wver 的 it0 summary）→ 停止派发（缓存命中，连账都不再扫）
    wver = dist_common.weights_fingerprint(str(bc))
    (tmp_path / "eval_log.jsonl").write_text(
        json.dumps(
            {"event": "eval_summary", "iter": 0, "wver": wver[:16], "games": 1, "wins": 0}
        )
        + "\n",
        encoding="utf-8",
    )
    loop._maybe_dispatch_baseline_eval(NODES)
    assert len(calls) == 2
    assert loop._baseline_landed_wver == wver[:16]

    # bc 换文件（重蒸馏）→ 新 wver 未落账 → 重派新基线
    bc2 = tmp_path / "bc2.json"
    bc2.write_text('{"v": 2}', encoding="utf-8")  # 内容必须不同，否则指纹相同走落账缓存
    loop.args.bc = str(bc2)
    loop._maybe_dispatch_baseline_eval(NODES)
    assert len(calls) == 3
    assert calls[2]["rl_path"] == str(bc2)


def test_dispatch_failure_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """派发失败自吞：基线是观测设施，不得拖垮训练主线（调用方在 run() 的 try 内）。"""
    bc = tmp_path / "bc.json"
    bc.write_text("{}", encoding="utf-8")
    loop = _loop(tmp_path, bc=str(bc))

    def _boom(*_a, **_k):
        raise RuntimeError("dist config unreadable")

    monkeypatch.setattr("rl.eval_dispatch.dispatch_eval_bg", _boom)
    loop._maybe_dispatch_baseline_eval(NODES)  # 不抛即过
    assert loop._baseline_eval_thread is None


def test_dispatch_baseline_writes_iter0_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端（无节点 → 本机直跑）：baseline=True 把逐局行与 summary 都写成 iter=0。

    关键回归护栏：预置一条**同 wver**的 it1 逐局行（it1 的 A-eval 覆盖同一批局——
    bc 与 it1 的 args.out 指纹本就可能相同），若按 wver 去重会把基线局吞成"已评估"，
    it0 行永远不落盘。
    """
    import rl.eval_dispatch as ed

    work = tmp_path / "baseline"
    work.mkdir()
    bc = work / "bc.json"
    bc.write_text('{"arch":{}}', encoding="utf-8")
    traj = work / "it1"
    traj.mkdir()
    wver = dist_common.weights_fingerprint(str(bc))
    (work / "eval_log.jsonl").write_text(
        json.dumps(
            {"event": "eval", "iter": 1, "wver": wver[:16], "stage": 2000, "seed": 860001}
        )
        + "\n",
        encoding="utf-8",
    )
    args = types.SimpleNamespace(
        eval_games_per_stage=1,
        total_stages=1,
        eval_window_sec=60,
        eval_stages="2000-2000",
        max_ticks=10,
        difficulty="hard",
    )
    cfg = {"nodes": [], "policy": {"evalLocalSlots": 1}}
    ran: list[tuple[int, int]] = []

    def fake_runner(
        bun, snap, stage, seed, out_dir, max_ticks, difficulty, timeout_sec, wver, **kw
    ):
        ran.append((stage, seed))
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

    monkeypatch.setattr(ed, "run_local_eval_game", fake_runner)
    gate = threading.Event()
    gate.set()
    ed.dispatch_eval_round(
        "bun", str(bc), traj, args, cfg, "rid.0", 0, local_gate=gate, baseline=True
    )
    assert ran == [(2000, 860001)]  # 同 wver 的 it1 行没把它吞掉

    def rows() -> list[dict]:
        return [
            json.loads(line)
            for line in (work / "eval_log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    it0 = [r for r in rows() if r.get("event") == "eval" and r.get("iter") == 0]
    summ = [r for r in rows() if r.get("event") == "eval_summary" and r.get("iter") == 0]
    assert len(it0) == 1 and it0[0]["wver"] == wver[:16]
    assert summ and summ[-1]["games"] == 1 and summ[-1]["wins"] == 0

    # 幂等：再派一次 → it0 键已评估 → 不再多出逐局行（重启/重试轮安全）
    ed.dispatch_eval_round(
        "bun", str(bc), traj, args, cfg, "rid.0", 0, local_gate=gate, baseline=True
    )
    assert len([r for r in rows() if r.get("event") == "eval" and r.get("iter") == 0]) == 1
