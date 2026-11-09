"""全量 rollout 胜率口径的**派生量隔离**回归（2026-11-10）。

主 `self._report` 合并 greedy 波后，`iteration.samples` / `outcomes` 是全量口径。
两个下游必须守住：
  · `trailing_samples_per_game`（动态采集 est）只读采样口径的 additive 键，
    否则 timeout 局 600 trans/局会把 est 拉高 ⇒ 下轮首波少派局（欠采）；
  · `write_iteration` 只在两键都非空时写 ⇒ 老账本逐字节不变。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.events import write_iteration
from worker.reports import compact_per_game
from worker.resume import trailing_samples_per_game


def _args() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        mode="per-tick", lr=1e-4, mb=2, epochs=1, reward="", dodge=""
    )


M: dict = {
    "rollout_sec": 1.0,
    "ppo_sec": 2.0,
    "total_steps": 3,
    "chunks_n": 1,
    "agg": None,
    "kl_cum": None,
    "halted": False,
    "dropped_games": None,
    "waves": None,
    "load_sec": None,
    "tail_drain_sec": None,
    "eval_join_sec": None,
}


def _ledger(p: Path, rows: list[dict]) -> None:
    p.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_est_uses_sampling_columns_when_present(tmp_path: Path) -> None:
    """全量 samples 含 greedy timeout 局；est 必须按采样口径的 100 而不是 121。"""
    p = tmp_path / "training_log.jsonl"
    _ledger(p, [
        {
            "event": "iteration", "iter": 1,
            "samples": 40000,
            "outcomes": {"stage_clear": 300, "lives_exhausted": 20, "timeout": 10},
            "sampling_samples": 30000,
            "sampling_games": 300,
        },
        {
            "event": "iteration", "iter": 2,
            "samples": 41000,
            "outcomes": {"stage_clear": 305, "lives_exhausted": 20, "timeout": 10},
            "sampling_samples": 31000,
            "sampling_games": 310,
        },
    ])
    assert trailing_samples_per_game(p) == 100


def test_est_falls_back_to_legacy_columns(tmp_path: Path) -> None:
    """老账本无 sampling_* 键 ⇒ 原路径逐字节不变。"""
    p = tmp_path / "training_log.jsonl"
    _ledger(p, [
        {
            "event": "iteration", "iter": 1,
            "samples": 40000,
            "outcomes": {"stage_clear": 400},
        },
    ])
    assert trailing_samples_per_game(p) == 100


def test_est_skips_incomplete_sampling_columns(tmp_path: Path) -> None:
    """只写了一个键 / 值为 0 ⇒ 不猜，退回全量口径（不能静默用坏值）。"""
    p = tmp_path / "training_log.jsonl"
    _ledger(p, [
        {
            "event": "iteration", "iter": 1,
            "samples": 40000,
            "outcomes": {"stage_clear": 400},
            "sampling_samples": 30000,
        },
        {
            "event": "iteration", "iter": 2,
            "samples": 40000,
            "outcomes": {"stage_clear": 400},
            "sampling_samples": 0,
            "sampling_games": 300,
        },
    ])
    assert trailing_samples_per_game(p) == 100


def test_iteration_event_writes_sampling_columns_only_when_present(
    tmp_path: Path,
) -> None:
    """additive 键：greedy 轮写、普通轮一个键都不多。"""
    p = tmp_path / "training_log.jsonl"
    row = write_iteration(
        p, _args(), 1, {"winRate": 0.8},
        {**M, "sampling_samples": 30000, "sampling_games": 300},
    )
    assert row["sampling_samples"] == 30000
    assert row["sampling_games"] == 300
    plain = write_iteration(p, _args(), 2, {"winRate": 0.8}, dict(M))
    assert set(plain).isdisjoint({"sampling_samples", "sampling_games"})
    # 半个键也不写（防止只带 samples 不带 games 的坏行）。
    half = write_iteration(
        p, _args(), 3, {"winRate": 0.8}, {**M, "sampling_samples": 30000}
    )
    assert set(half).isdisjoint({"sampling_samples", "sampling_games"})


def test_per_game_portrait_keeps_greedy_provenance() -> None:
    """逐局画像（耗时/击杀/残血/道具四列的数据源）必须能区分 greedy 与采样局。"""
    rows = compact_per_game([
        {"stage": 2000, "seed": 1, "nSamples": 600, "labelMode": "greedy"},
        {"stage": 2000, "seed": 2, "nSamples": 90},
    ])
    assert len(rows) == 2
    assert rows[0]["labelMode"] == "greedy"
    assert set(rows[1]).isdisjoint({"labelMode"})
