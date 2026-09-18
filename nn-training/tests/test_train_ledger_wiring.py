"""R2a 接线回归：`_setup_common` 的启动继承**全部来自账本**（2026-09-18）。

这一条钉的是一个真 bug（不是新功能）：门禁的 I2 判据「提示类门 REMEDIATE ×N 即停腿」
原先是纯内存计数 ⇒ **进程重启即洗白**，边际收益枯竭的 N 次确认可以从头再来一遍。
同类问题还有 F4 连击（`_kl_streak`/`_ent_streak`）与止损连击（`_stop_loss_streak`）。

覆盖：
  1. 账本是唯一来源：I2 计数 / 连击 / 累计量 / 续跑指针全部在 `_setup_common` 后复现；
  2. 行为面：继承来的 I2 计数**接着数**（第 4 次软 REMEDIATE 当轮停腿），而旧实现会放行；
  3. 不继承项被刻意排除：`_consec_fail`（重试连击是单腿内的进程护栏，继承会「重启即秒死」）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.breaker import KL_BREAK
from rl.gate_check import sum_train_samples, sum_train_sec
from rl.loop_core import TrainingLoop

TS = "2026-09-18 12:00:00"


def _args(traj: Path, **kw: object) -> types.SimpleNamespace:
    base: dict[str, object] = {
        "mode": "per-tick",
        "traj": str(traj),
        "out": str(traj / "weights.json"),
        "iters": 100,
        "seed": 7,
        "start_it": None,
        "max_hours": 0.0,
        "eval_every": 1,
        "eval_at": "",
        "rotate_stages": 0,
        "stages": "1,2",
        "seeds": 1,
        "total_stages": 2,
        "seeds_per_stage": 1,
        "curriculum_stages": "",
        "curriculum_start": 1,
        "curriculum_every": 0,
        "curriculum_grow": 0,
        "max_ticks": 1000,
        "epochs": 1,
        "mb": 2,
        "lr": 1e-4,
        "workers": 1,
        "keep_iters": 1,
        "kickstart_ref": False,
        "course_obj": None,
        "course_name": "",
        "gate_remediate_stop_after": 4,
        "remote_hub_url": "",
        "remote_token": "",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _ledger(traj: Path) -> Path:
    """一条「已跑两轮 + 三次提示类 REMEDIATE + 一轮失败」的账本。"""
    rows = [
        {"event": "run_start", "time": TS, "args": {"mode": "per-tick"}, "rotateSeed": 555},
        {
            "event": "iteration",
            "iter": 1,
            "time": TS,
            "winRate": 0.1,
            "samples": 1000,
            "epochs": 2,
            "kl": 0.01,
            "entropy": 0.9,
            "ppo_sec": 100.0,
            "ppo_cloud_sec": 60.0,
        },
        {
            "event": "gate_verdict",
            "iter": 1,
            "time": TS,
            "verdict": "REMEDIATE",
            "decider": "loop",
            "readings": [{"kind": "plateau", "released": True}],
        },
        {
            "event": "iteration",
            "iter": 2,
            "time": TS,
            "winRate": 0.1,
            "samples": 1000,
            "epochs": 2,
            "kl": KL_BREAK + 0.01,  # 跨阈值 ⇒ 连击 = 1（跨重启必须记得）
            "entropy": 0.9,
            "ppo_sec": 100.0,
            "ppo_cloud_sec": 60.0,
        },
        {"event": "iter_error", "iter": 3, "time": TS, "error": "transient"},
    ]
    for it in (2, 3):
        rows.append(
            {
                "event": "gate_verdict",
                "iter": it,
                "time": TS,
                "verdict": "REMEDIATE",
                "decider": "loop",
                "readings": [{"kind": "plateau", "released": True}],
            }
        )
    p = traj / "training_log.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_setup_common_inherits_ledger_state(tmp_path: Path) -> None:
    traj = tmp_path / "traj"
    traj.mkdir()
    p = _ledger(traj)

    loop = TrainingLoop(_args(traj), None, "bun", {})
    loop._setup_common()

    assert loop._start_it == 3  # 断点续跑指针
    assert loop._rotate_seed == 555  # 课程连续性
    assert loop._soft_remediate_count == 3  # ★ I2：重启不洗白
    assert loop._kl_streak == 1  # ★ F4 连击
    assert loop._ent_peak == 0.9
    assert loop._train_sec_total == sum_train_sec(p) == 120.0
    assert loop._train_samples_total == sum_train_samples(p) == 4000
    # 刻意不继承：重试连击是单腿内的进程护栏（继承 ⇒ 重启即秒死）
    assert loop._consec_fail == 0
    # 视图本身可用（观测面：末次判决 / 迭代行数）
    assert loop._ledger.last_verdict == "REMEDIATE"
    assert loop._ledger.iterations_n == 2


def test_inherited_soft_remediate_count_stops_leg_on_next_hit(tmp_path: Path) -> None:
    """★ 行为面：继承的 3 次 + 本次第 4 次 ⇒ 当轮停腿（旧实现此时才数到 1，放行）。"""
    traj = tmp_path / "traj"
    traj.mkdir()
    _ledger(traj)

    loop = TrainingLoop(_args(traj), None, "bun", {})
    loop._setup_common()

    r = types.SimpleNamespace(id="G4", kind="plateau", fired=True, released=True, reason="test")
    r.to_dict = lambda: {"id": "G4", "kind": "plateau", "released": True, "reason": "test"}

    res = types.SimpleNamespace(
        verdict="REMEDIATE",
        reason="plateau",
        route="REMEDIATE",
        readings=(r,),
        override=None,
        seeds="unknown",
    )
    stopped = loop._apply_verdict(40, res)

    assert stopped is True
    assert loop._leg_abort is True
    assert loop._soft_remediate_count == 4
    # 停腿判决落账（ABORT 行可被读盘面看见）
    txt = (traj / "training_log.jsonl").read_text(encoding="utf-8")
    assert '"verdict": "ABORT"' in txt
    # 视图随写增量推进（不重扫）：新判决已在视图里
    assert loop._ledger.last_verdict == "ABORT"


def test_fresh_traj_counts_from_zero(tmp_path: Path) -> None:
    """空账本 = 新纪元：I2 计数从 0 起（换 traj 重新开课 = 重新计数，与「重启不清零」不冲突）。"""
    traj = tmp_path / "traj"
    traj.mkdir()
    loop = TrainingLoop(_args(traj), None, "bun", {})
    loop._setup_common()
    assert loop._soft_remediate_count == 0
    assert loop._start_it == 1
    assert loop._ledger.next_it == 1 and loop._ledger.verdicts == []
