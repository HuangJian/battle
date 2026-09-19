"""R2a：`rl/train_ledger.py` 的奇偶/差分测试（plan/r2-loop-task-queue §5.2）。

三层钉法：
1. **与既有五个扫描器奇偶**（`rl/resume.py` / `rl/gate_check.py`）——旧实现即参考语义，
   新视图与它逐字段相等才算「扫账本」没走样；
2. **增量 == 单次扫描**——`apply_event` 与 `load_ledger` 必须给出同一个视图
   （否则「只在开课读一遍 + 之后增量」这条设计会悄悄分叉）；
3. **独立参考实现**复算连击（`breaker_update` 的输入输出关系），
   以及把 2026-09-18 修掉的那个 bug 钉住：提示类 REMEDIATE 计数**不随重启洗白**。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from rl.breaker import ENT_BREAK, ENT_BREAK_MAX_WINRATE, KL_BREAK
from rl.gate_check import (
    count_iteration_events,
    first_iter_end_ts,
    first_run_start_ts,
    sum_train_samples,
    sum_train_sec,
)
from rl.loop_guards import TrainingGuards
from rl.resume import last_completed_iter, last_rotate_seed, peak_entropy
from rl.train_ledger import LedgerSpec, LedgerView, load_ledger

TS = "2026-09-18 12:00:00"


def _run_start(rotate_seed: int = 4242) -> dict:
    return {"event": "run_start", "time": TS, "args": {"mode": "per-tick"}, "rotateSeed": rotate_seed}


def _iter(
    it: int,
    *,
    samples: int = 1000,
    epochs: int = 1,
    kl: float | None = 0.01,
    entropy: float | None = 0.9,
    win_rate: float = 0.2,
    ppo_sec: float = 60.0,
    ppo_cloud_sec: float | None = 40.0,
    ts: str = TS,
) -> dict:
    return {
        "event": "iteration",
        "iter": it,
        "time": ts,
        "winRate": win_rate,
        "samples": samples,
        "epochs": epochs,
        "kl": kl,
        "entropy": entropy,
        "ppo_sec": ppo_sec,
        "ppo_cloud_sec": ppo_cloud_sec,
    }


def _verdict(it: int, verdict: str = "REMEDIATE", kinds: tuple[str, ...] = ("plateau",)) -> dict:
    return {
        "event": "gate_verdict",
        "iter": it,
        "time": TS,
        "verdict": verdict,
        "decider": "loop",
        "readings": [{"kind": k, "released": True} for k in kinds],
    }


def _write(path: Path, events: list[dict]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


# --------------------------------------------------------------------- 1) 奇偶

def test_view_parity_with_legacy_scanners(tmp_path: Path) -> None:
    """新视图与既有五个扫描器逐字段相等（旧实现 = 参考语义）。"""
    p = _write(
        tmp_path / "training_log.jsonl",
        [
            _run_start(777),
            _iter(1, samples=1200),
            _iter(2, samples=800, ppo_cloud_sec=None),  # 旧账本口径：回落 ppo_sec
            {"event": "job_completed", "iter": 2, "job_id": "c4:it2"},  # hub 追加行
            _iter(3, samples=0, epochs=2),  # 零样本 + epochs=2（样本通过量要乘）
            _verdict(3, "REMEDIATE"),
            _iter(4, samples=500),
            {"event": "iter_error", "iter": 5, "time": TS, "error": "boom"},
            _iter(5, samples=500),
        ],
    )
    view = load_ledger(p)

    assert view.next_it == last_completed_iter(p) + 1 == 6
    assert view.rotate_seed == last_rotate_seed(p) == 777
    assert view.ent_peak == peak_entropy(p)
    assert view.train_sec_total == sum_train_sec(p)
    assert view.train_samples_total == sum_train_samples(p)
    assert view.iterations_n == count_iteration_events(p) == 5
    assert view.first_run_ts == first_run_start_ts(p)
    assert view.first_iter_ts == first_iter_end_ts(p)
    # 样本通过量口径 = Σ(samples × epochs)：it3 的 0 与 it2 的口径差异都要能被看见
    assert view.train_samples_total == 1200 + 800 + 0 + 500 + 500


def test_run_start_before_rotation_is_not_written(tmp_path: Path) -> None:
    """空账本 = 首启：视图全零、next_it=1（调用方据此判断「全新开始」）。"""
    view = load_ledger(tmp_path / "missing.jsonl")
    assert (view.next_it, view.rotate_seed, view.iterations_n) == (1, None, 0)
    assert view.train_sec_total == 0.0 and view.kl_streak == 0


# --------------------------------------------------- 2) 增量 == 单次扫描

def test_incremental_equals_load(tmp_path: Path) -> None:
    """事件逐条 apply 出来的视图，必须与一次性 load 完全一致。"""
    events = [
        _run_start(31),
        _iter(1),
        _iter(2, kl=KL_BREAK + 0.01, entropy=0.2, win_rate=0.1),
        _verdict(2),
        {"event": "iter_error", "iter": 3, "time": TS, "error": "x"},
        {"event": "stop_loss", "iter": 3, "time": TS, "streak": 1},
        _iter(3, samples=0),
        _iter(4, kl=KL_BREAK + 0.01, entropy=0.2, win_rate=0.1),
    ]
    p = _write(tmp_path / "training_log.jsonl", events)
    loaded = load_ledger(p)

    incremental = LedgerView(path=p)
    for e in events:
        incremental.apply_event(e)

    for field_name in (
        "next_it",
        "rotate_seed",
        "ent_peak",
        "train_sec_total",
        "train_samples_total",
        "kl_streak",
        "ent_streak",
        "stop_loss_streak",
        "zero_shard_streak",
        "consec_fail",
        "iterations_n",
        "bad_lines",
        "ignored_events",
    ):
        assert getattr(incremental, field_name) == getattr(loaded, field_name), field_name
    assert incremental.rows == loaded.rows
    assert incremental.verdicts == loaded.verdicts


# ------------------------------------------------- 3) 连击：独立参考实现

def _ref_streaks(rows: list[tuple[float, float, float]], ent_peak: float | None) -> tuple[int, int]:
    """`rl/breaker.py` 文档语义的独立复算（不调用生产函数，只按文档写一遍）。"""
    kl_streak = ent_streak = 0
    peak = ent_peak
    for kl, entropy, win_rate in rows:
        kl_streak = kl_streak + 1 if kl >= KL_BREAK else 0
        collapsed = (
            entropy <= ENT_BREAK
            and win_rate < ENT_BREAK_MAX_WINRATE
            and (peak is None or peak > ENT_BREAK or (peak - entropy) >= 0.10)
        )
        ent_streak = ent_streak + 1 if collapsed else 0
        peak = entropy if peak is None else max(peak, entropy)
    return kl_streak, ent_streak


@pytest.mark.parametrize(
    "rows",
    [
        [(0.01, 0.9, 0.2), (0.01, 0.9, 0.2)],
        [(KL_BREAK + 0.01, 0.9, 0.2), (KL_BREAK + 0.01, 0.9, 0.2), (0.01, 0.9, 0.2)],
        [(0.01, 1.0, 0.1), (0.01, 0.3, 0.1), (0.01, 0.3, 0.1)],  # 相对崩塌（峰值曾高于阈值）
        [(0.01, 0.3, 0.7), (0.01, 0.3, 0.7)],  # winRate 护栏：不算崩塌
    ],
)
def test_streaks_match_independent_reference(rows: list[tuple[float, float, float]]) -> None:
    view = LedgerView(path=Path("nonexistent.jsonl"))
    for i, (kl, entropy, win_rate) in enumerate(rows, start=1):
        view.apply_event(_iter(i, kl=kl, entropy=entropy, win_rate=win_rate))
    assert (view.kl_streak, view.ent_streak) == _ref_streaks(rows, ent_peak=None)
    assert view.ent_peak == max(r[1] for r in rows)


def test_agg_missing_round_does_not_advance_streaks(tmp_path: Path) -> None:
    """流式 checkpoint-complete 轮（agg 缺失 → kl/entropy 为 None）不计连击、也不推峰值。"""
    rows = [(KL_BREAK + 0.01, 0.9, 0.2), (None, None, 0.2), (KL_BREAK + 0.01, 0.9, 0.2)]
    p = _write(
        tmp_path / "training_log.jsonl",
        [_iter(i, kl=kl, entropy=ent) for i, (kl, ent, _wr) in enumerate(rows, start=1)],
    )
    view = load_ledger(p)
    # 中间那轮既不**续**也不**断**：连击保持 2（与 `_breaker` 只在 agg 非 None 时被
    # 调用的门控一致——那轮根本没发生梯度步，不该拿它洗掉已有的连击）。
    assert view.kl_streak == 2
    assert view.ent_streak == 0


def test_thresholds_come_from_args_like_the_breaker(tmp_path: Path) -> None:
    """KL 阈值只在 intent/goal 模式取 args 覆盖（per-tick 恒用常量）——与 _breaker 同源。"""
    class _Args:
        mode = "per-tick"
        kl_break = 0.001
        kl_break_consec = 1
        ent_break = 0.1
        ent_break_consec = 1
        ent_break_max_winrate = 0.9

    per_tick = LedgerSpec.from_args(_Args())
    assert per_tick.kl_break == KL_BREAK and per_tick.kl_consec != 1
    assert per_tick.ent_break == 0.1  # ENT 三阈值恒取 args 覆盖

    class _Intent(_Args):
        mode = "intent"

    relaxed = LedgerSpec.from_args(_Intent())
    assert (relaxed.kl_break, relaxed.kl_consec) == (0.001, 1)


# ------------------------------- 4) 提示类 REMEDIATE：重启不洗白（本轮的 bug）

def test_soft_remediate_count_reads_whole_ledger(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "training_log.jsonl",
        [
            _run_start(),
            _iter(1),
            _verdict(1, "REMEDIATE", ("plateau",)),
            _iter(2),
            _verdict(2, "REMEDIATE", ("plateau",)),
            _iter(3),
            _verdict(3, "REMEDIATE", ("plateau", "skill_floor")),  # 非提示类 → 不计
            _iter(4),
            _verdict(4, "REMEDIATE", ()),  # 读不到明细 → 保守不计（同 _is_soft_verdict）
            _iter(5),
            _verdict(5, "HOLD", ("plateau",)),  # 非 REMEDIATE → 不计
        ],
    )
    view = load_ledger(p)
    assert view.soft_remediate_count(TrainingGuards.NO_CLOUD_HALT_KINDS) == 2
    # ★ 重启不再洗白：同一份账本重新扫一遍（= 新进程启动）仍是 2，而旧实现会归零。
    assert load_ledger(p).soft_remediate_count(TrainingGuards.NO_CLOUD_HALT_KINDS) == 2
    assert view.last_verdict == "HOLD"


def test_soft_remediate_count_accepts_object_readings() -> None:
    """调用方也可能直接喂 Reading 对象（非 dict 形态）——两形态同口径。"""

    class _R:
        def __init__(self, kind: str, released: bool) -> None:
            self.kind = kind
            self.released = released

    view = LedgerView(path=Path("nonexistent.jsonl"))
    view.apply_event(
        {"event": "gate_verdict", "iter": 1, "verdict": "REMEDIATE", "readings": [_R("plateau", True)]}
    )
    assert view.soft_remediate_count(TrainingGuards.NO_CLOUD_HALT_KINDS) == 1


# --------------------------------------------- 5) 向前兼容：坏行/未知事件

def test_bad_lines_and_unknown_events_never_change_counts(tmp_path: Path) -> None:
    good = [_run_start(), _iter(1), _iter(2)]
    p = tmp_path / "training_log.jsonl"
    p.write_text(
        "".join(json.dumps(e) + "\n" for e in good)
        + '{"event": "iteration", "iter": 3, "kl": 0.01\n'  # 崩溃留下的半截行
        + "[1, 2, 3]\n"  # 非 dict 行
        + json.dumps({"event": "job_failed", "iter": 2}) + "\n"  # hub 追加 / 未知事件
        + "\n",
        encoding="utf-8",
    )
    view = load_ledger(p)
    assert view.iterations_n == 2 and view.next_it == 3
    assert view.bad_lines == 2
    assert view.ignored_events == 1


# ------------------------------------------- 6) 新事件：止损连击 / 观测项

def test_stop_loss_streak_rebuilt_from_events(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "training_log.jsonl",
        [
            _run_start(),
            _iter(1),
            {"event": "stop_loss", "iter": 1, "time": TS, "streak": 1, "delta": -0.03},
            _iter(2),
            {"event": "stop_loss", "iter": 2, "time": TS, "streak": 0, "delta": None},
        ],
    )
    assert load_ledger(p).stop_loss_streak == 0  # 回落后不再算「已确认一次」
    p2 = _write(
        tmp_path / "b.jsonl",
        [_run_start(), _iter(1), {"event": "stop_loss", "iter": 1, "time": TS, "streak": 1}],
    )
    assert load_ledger(p2).stop_loss_streak == 1  # ★ 重启后仍记得「已确认一次」


def test_observability_fields_but_not_startup_handover(tmp_path: Path) -> None:
    """`consec_fail` / `zero_shard_streak` 只作观测量：口径本身对，但**不**进启动继承。"""
    p = _write(
        tmp_path / "training_log.jsonl",
        [
            _run_start(),
            _iter(1, samples=10),
            _iter(2, samples=0),
            _iter(3, samples=0),
            {"event": "iter_error", "iter": 4, "time": TS, "error": "boom"},
        ],
    )
    view = load_ledger(p)
    assert view.zero_shard_streak == 2  # 连续零样本轮
    assert view.consec_fail == 1  # 末个 iteration 之后的失败数


def test_spec_can_be_injected_per_course(tmp_path: Path) -> None:
    """多课程调度器按课注入 spec（不依赖 args）——每课自己的阈值口径。"""
    p = _write(tmp_path / "training_log.jsonl", [_run_start(), _iter(1, kl=0.05)])
    view = load_ledger(p, LedgerSpec(kl_break=0.04))
    assert view.kl_streak == 1
    assert load_ledger(p, LedgerSpec(kl_break=0.06)).kl_streak == 0
