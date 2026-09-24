"""test_eval_a_once — 手动 evalA 必须与 in-loop eval **同一条派发路**（2026-09-22 用户指令）。

缺口：`rl/eval_a_once.py` 曾自带一个「本机串行」循环 —— 400 局 × ~0.85s ≈ 340s
（实测 x20-noexplore it177 = 339.2s，账本 `nodes` 只有 `{"local-evalA": 400}`），
而 in-loop 同一份语料 50–64s（三台节点分摊）。手动 evalA 是**同一次评估的手动触发**：
语料 / 节点池 / 账本 schema 都必须与 in-loop 同源，否则指标表里两种行不可比。

本文件钉住：① 确实走 in-loop 派发器（不再自己跑局）；② summary 读回契约（含
「同 wver 已评完 → 按账本回填」那条 in-loop 没有的手动兜底）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from rl import eval_a_once

ROOT = Path(__file__).resolve().parent.parent


def test_eval_a_once_dispatches_through_in_loop_dispatcher() -> None:
    """回归：手动 evalA 必须经 `rl/eval_dispatch` 派到节点，不得自己串行跑局。

    源码级断言（同 test_eval_loot_fields.py 的接线口径）——它抓的正是那 339s 的根因：
    本地-only 的 `for stage, seed in todo: run_local_eval_game(...)`。
    """
    src = (ROOT / "rl" / "eval_a_once.py").read_text(encoding="utf-8")
    assert "from rl.eval_dispatch import dispatch_eval_round" in src
    assert "dispatch_eval_round(" in src
    # 不再直接调本地 runner（自己跑局 = 又把节点池甩掉了）
    assert "run_local_eval_game" not in src
    # 本机份额来自 policy（与 in-loop 同一旋钮），不是脚本自造的并发数
    assert "evalLocalSlots" in src
    # in-loop 的调用点也用同一模块（两边同源，不是各写一套）
    loop_src = (ROOT / "rl" / "loop_steps.py").read_text(encoding="utf-8")
    assert "eval_dispatch import dispatch_eval_bg" in loop_src


def _row(**kw) -> dict:
    base = {
        "event": "eval",
        "iter": 105,
        "wver": "a" * 16,
        "stage": 2000,
        "seed": 1,
        "node": "self",
        "win": 1,
        "cleared": 1,
        "outcome": "stage_clear",
    }
    base.update(kw)
    return base


def test_read_summary_returns_last_row_for_iter_and_wver(tmp_path: Path) -> None:
    """只认本 (iter, wver) 的 eval_summary，且取**最后一条**（重复触发以最新为准）。"""
    p = tmp_path / "eval_log.jsonl"
    rows = [
        {"event": "eval_summary", "iter": 105, "wver": "a" * 16, "games": 100, "sec": 60.0},
        _row(),  # 逐局行不该被当成 summary
        {"event": "eval_summary", "iter": 106, "wver": "a" * 16, "games": 100, "sec": 1.0},
        {"event": "eval_summary", "iter": 105, "wver": "b" * 16, "games": 100, "sec": 2.0},
        {"event": "eval_summary", "iter": 105, "wver": "a" * 16, "games": 100, "sec": 47.0},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    got = eval_a_once._read_summary(p, "a" * 16, 105)
    assert got is not None and got["sec"] == 47.0
    assert eval_a_once._read_summary(p, "a" * 16, 999) is None
    assert eval_a_once._read_summary(tmp_path / "missing.jsonl", "a" * 16, 105) is None


def test_write_summary_for_wver_backfills_reused_readout(tmp_path: Path) -> None:
    """同 wver 已在别轮评完（派发器早退不写 summary）时，按账本回填本 iter 的读数。"""
    p = tmp_path / "eval_log.jsonl"
    rows = [
        _row(seed=1, node="self", win=1, cleared=1),
        _row(seed=2, node="mac", win=0, cleared=0, outcome="gameover"),
        _row(seed=3, node="self", win=1, cleared=1),
        # source 行 = B/C evalboard 写入，不得混进 A 层读数
        _row(seed=4, node="self", win=1, cleared=1, source="B"),
        # 别的 wver 不得混进
        _row(seed=5, wver="c" * 16, win=1, cleared=1),
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    n = eval_a_once._write_summary_for_wver(p, "a" * 16, 177, time.time())
    assert n == 3

    got = eval_a_once._read_summary(p, "a" * 16, 177)
    assert got is not None
    assert got["games"] == 3 and got["wins"] == 2
    assert got["winRate"] == round(2 / 3, 4)
    assert got["nodes"] == {"mac": 1, "self": 2}
    assert got["reused_wver"] is True
    assert got["rolloutWinRate"] is None  # 手动补写没有 rollout 读数，不许编


def test_write_summary_for_wver_no_rows_returns_zero(tmp_path: Path) -> None:
    """账本里根本没有该 wver 的局 → 0（调用方据此响亮失败，不写空 summary）。"""
    p = tmp_path / "eval_log.jsonl"
    p.write_text(json.dumps(_row(wver="d" * 16)) + "\n", encoding="utf-8")
    assert eval_a_once._write_summary_for_wver(p, "a" * 16, 177, time.time()) == 0
    assert eval_a_once._read_summary(p, "a" * 16, 177) is None


# ── it0 基线（`--baseline`；离线开课由控制台补派，2026-09-24） ─────────────────────
# 为什么钉：离线课「本机不跑训练」（`rollout_src:'run'`）⇒ 主循环的基线派发不在场上，
# 云机又恒不评 it<1 ⇒ 这一格只能由**手动 evalA 这条同路**补。缺口表现是静默的：控制台
# 的配对基线会退化成「第一条 eval 轮」（随 run 起点漂移）。


def _course_file(tmp_path: Path, out: Path, traj: Path) -> Path:
    """最小课程 jsonc（只喂 eval_a_once 真正读的键；`extra=forbid` ⇒ 键必须合法）。"""
    p = tmp_path / "t-baseline.jsonc"
    body = {
        "name": "t-baseline",
        "out": str(out).replace("\\", "/"),
        "traj": str(traj).replace("\\", "/"),
        "eval_stages": "2000",
        "eval_games_per_stage": 2,
        "eval_every": 1,
    }
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _ledger_rows(traj: Path) -> list[dict]:
    p = traj / "eval_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["eval_a_once.py", *argv])
    return eval_a_once.main()


def test_main_baseline_defaults_ckpt_to_course_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--baseline` 不给 `--ckpt` ⇒ 取课程活动权重 `out`（与任务包 init_weights 同字节），
    派发照旧走 in-loop 那条路（baseline=True / iter=0），读数按账本回填。"""
    out = tmp_path / "weights.json"
    out.write_text('{"w": 1}', encoding="utf-8")
    traj = tmp_path / "traj"
    course_p = _course_file(tmp_path, out=out, traj=traj)

    import dist_common
    from rl import eval_dispatch

    seen: dict = {}

    def _fake_dispatch(bun, rl_path, traj_dir, args, cfg, iter_id, it, **kw):
        seen.update(rl_path=rl_path, it_dir=Path(traj_dir), it=it, iter_id=iter_id, **kw)
        # 落一条逐局行（与真派发器同册同 schema）——把 main 的读回/回填路径真的走一遍。
        fp = dist_common.weights_fingerprint(rl_path)[:16]
        with open(Path(traj_dir).parent / "eval_log.jsonl", "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "event": "eval",
                        "iter": it,
                        "wver": fp,
                        "stage": 2000,
                        "seed": 1,
                        "node": "test",
                        "win": 1,
                        "cleared": 1,
                        "outcome": "stage_clear",
                    }
                )
                + "\n"
            )

    monkeypatch.setattr(dist_common, "load_dist_config", lambda: {})
    monkeypatch.setattr(eval_dispatch, "dispatch_eval_round", _fake_dispatch)

    rc = _run_main(monkeypatch, ["--course", str(course_p), "--iter", "0", "--baseline"])

    assert rc == 0
    assert seen["rl_path"] == str(out)  # 起点权重 = 课程 out，不是别的
    assert seen["it"] == 0 and seen["baseline"] is True
    assert seen["iter_id"] == "evalA.0"
    fp = dist_common.weights_fingerprint(str(out))[:16]
    summ = [r for r in _ledger_rows(traj) if r.get("event") == "eval_summary"]
    assert summ and summ[-1]["iter"] == 0 and summ[-1]["wver"] == fp


def test_main_baseline_skips_when_summary_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同 wver 的 it0 summary 已落账 ⇒ 早退 0 且**不派发**（离线课「停课→重开」不重评）。"""
    out = tmp_path / "weights.json"
    out.write_text('{"w": 2}', encoding="utf-8")
    traj = tmp_path / "traj"
    traj.mkdir(parents=True, exist_ok=True)
    course_p = _course_file(tmp_path, out=out, traj=traj)

    import dist_common
    from rl import eval_dispatch

    fp = dist_common.weights_fingerprint(str(out))[:16]
    (traj / "eval_log.jsonl").write_text(
        json.dumps({"event": "eval_summary", "iter": 0, "wver": fp, "games": 2, "wins": 1}) + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.setattr(dist_common, "load_dist_config", lambda: {})
    monkeypatch.setattr(eval_dispatch, "dispatch_eval_round", lambda *a, **k: calls.append("x"))

    assert _run_main(monkeypatch, ["--course", str(course_p), "--iter", "0", "--baseline"]) == 0
    assert calls == []


def test_main_requires_ckpt_without_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 baseline 仍要显式权重路径（旧行为：缺了就响亮退 2，不猜）。"""
    out = tmp_path / "weights.json"
    out.write_text('{"w": 3}', encoding="utf-8")
    traj = tmp_path / "traj"
    course_p = _course_file(tmp_path, out=out, traj=traj)

    import dist_common
    from rl import eval_dispatch

    calls: list[str] = []
    monkeypatch.setattr(dist_common, "load_dist_config", lambda: {})
    monkeypatch.setattr(eval_dispatch, "dispatch_eval_round", lambda *a, **k: calls.append("x"))

    assert _run_main(monkeypatch, ["--course", str(course_p), "--iter", "27"]) == 2
    assert calls == []


def test_main_baseline_rejects_nonzero_iter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--baseline` 的 iter 必为 0：落进别的 iter 槽会被当成那一轮的读数（响亮拒）。"""
    out = tmp_path / "weights.json"
    out.write_text('{"w": 4}', encoding="utf-8")
    course_p = _course_file(tmp_path, out=out, traj=tmp_path / "traj")

    assert _run_main(monkeypatch, ["--course", str(course_p), "--iter", "3", "--baseline"]) == 2
