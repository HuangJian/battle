"""test_eval_scratch_rotation — eval 私有 scratch 的轮转契约（plan/eval-gap-honesty §2b，2026-10-18）。

事故形状（y22-lr it5）：eval 的冻结快照与本机落点原先住 `<traj>/it<N>/`，而 `_rotate_cleanup`
按 `n_old <= it - keep_iters` 删 `it*` 目录 —— 评估比训练慢几轮时就会被连锅端（79 局读失败 /
4 局写失败 = 删目录撞上 4 条本机线程）。

本文件钉两条：
① `it<N>/` 的轮转**不碰** `_eval-it<N>/`（D1b 的落点分离）；
② `_eval-it<N>/` 只在「已有完整 summary（`dropped == 0`）且超出 keep_iters」时被删
   —— 在飞的评估（无 summary / 有缺口）永不删。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import worker.loop_guards_sweep as sweep_mod


class _Sweep(sweep_mod.TrainingGuardsSweep):
    """最小 mixin 宿主：只提供 `_rotate_cleanup` 读的三个属性。"""

    def __init__(self, root: Path, keep_iters: int) -> None:
        self.args = SimpleNamespace(keep_iters=keep_iters)
        self._traj_root = root
        self._traj_dir = root / "it1"


def _mk_eval_scratch(root: Path, it: int) -> Path:
    d = root / f"_eval-it{it}" / "local-eval" / f"rl_s2000_seed86{it:04d}"
    d.mkdir(parents=True)
    (d / "_eval_report.json").write_text("{}", encoding="utf-8")
    return d


def _write_summaries(root: Path, rows: list[dict]) -> None:
    (root / "eval_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def test_eval_scratch_survives_when_the_round_is_not_settled(tmp_path: Path) -> None:
    """`_eval-it6/` 有缺口（dropped=7）⇒ 即使超出 keep_iters 也不得删（在飞/坏读数）。"""
    root = tmp_path / "traj"
    root.mkdir()
    it5 = _mk_eval_scratch(root, 5)
    it6 = _mk_eval_scratch(root, 6)
    _write_summaries(
        root,
        [
            {"event": "eval_summary", "iter": 5, "dropped": 0},
            {"event": "eval_summary", "iter": 6, "dropped": 7},
        ],
    )

    _Sweep(root, keep_iters=3)._rotate_cleanup(9)  # cutoff = 6

    assert not it5.exists(), "已结算（dropped=0）且超窗的轮可以删"
    assert it6.exists(), "有缺口的轮可能还在飞/待补 —— 不得删（事故形状）"


def test_eval_scratch_without_summary_is_never_deleted(tmp_path: Path) -> None:
    """没有任何 summary ⇒ 一律不删（保守：读不到就当在飞）。"""
    root = tmp_path / "traj"
    root.mkdir()
    d = _mk_eval_scratch(root, 2)
    _Sweep(root, keep_iters=1)._rotate_cleanup(99)
    assert d.exists()


def test_legacy_it_dir_rotation_stays_as_is(tmp_path: Path) -> None:
    """旧行为不动：`it<N>/` 仍按 keep_iters 轮转（`_eval-*` 的例外不外溢）。"""
    root = tmp_path / "traj"
    root.mkdir()
    (root / "it3").mkdir()
    (root / "it8").mkdir()
    _write_summaries(root, [{"event": "eval_summary", "iter": 3, "dropped": 0}])

    _Sweep(root, keep_iters=3)._rotate_cleanup(8)  # cutoff = 5

    assert not (root / "it3").exists()
    assert (root / "it8").exists()
