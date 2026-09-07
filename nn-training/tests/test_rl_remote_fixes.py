"""test_rl_remote_fixes —— 2026-09-06 p4-onset 监控四修复的回归锚（nn.progress §19/§20）。

① lr 三段表在 remote 模式生效（_course_iter 折算 args.lr → job manifest → worker
   Adam）② 同 seed shard 竞速输家退场 + 发布端同名去重 ③ EVAL_SEEDS 扩 100
④ 课程 backup_prefix/backup_dir 生效。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.config import CourseConfig, PpoScheduleEntry, RewardBlock
from rl.loop_steps import TrainingSteps

# 与 p4-onset 课程同形的分段表 + 已知可编译的奖励公式（build_reward_fn 白名单内）。
SCHEDULE = [
    PpoScheduleEntry(until_iter=15, kl_coef=0.6, lr=3e-4, kl_cap=1e9),
    PpoScheduleEntry(until_iter=35, kl_coef=0.2, lr=1.5e-4, kl_cap=0.2),
    PpoScheduleEntry(kl_coef=0.0, lr=5e-5),
]
FORMULA = (
    "wKill*kills + wHit*enemyHits + wPickup*powerUpsCollected + wStar*starsCollected"
    " - wDmg*playerHits - wStuck*min(max(0, stuckTicks-300), 900) - wShot*playerShots"
)


def _course() -> CourseConfig:
    return CourseConfig(
        name="lr-fold-test",
        ppo_schedule=SCHEDULE,
        reward=RewardBlock(
            formula=FORMULA,
            params={
                "wKill": 3.0,
                "wHit": 0.3,
                "wPickup": 1.5,
                "wStar": 1.0,
                "wDmg": 1.0,
                "wStuck": 0.02,
                "wShot": 0.01,
            },
            terminal={"stage_clear": 2.0, "lives_exhausted": -1.0, "timeout": -2.0},
            scheme="toy",
        ),
    )


def _steps(opt=None) -> TrainingSteps:
    ts = TrainingSteps()
    ts.args = SimpleNamespace(course_obj=_course(), gamma=0.995, lam=0.97)
    if opt is not None:
        ts._opt = opt
    return ts


def test_course_iter_folds_lr_into_args_remote() -> None:
    """remote 模式（hub 无 _opt）：schedule lr 折进 args.lr（publish_job 的来源）。"""
    ts = _steps()
    ts._course_iter(1)
    assert ts.args.lr == 3e-4, "warmup 段（it≤15）lr=3e-4 必须折进 args.lr"
    assert ts.args._kl_coef == 0.6
    assert ts.args._kl_cap == 1e9
    ts._course_iter(20)
    assert ts.args.lr == 1.5e-4
    ts._course_iter(36)
    assert ts.args.lr == 5e-5, "精调段（it≥36）lr=5e-5"


def test_course_iter_syncs_opt_local() -> None:
    """local 模式：args.lr 与 opt.param_groups 同步（保 Adam 动量原语义）。"""
    opt = SimpleNamespace(param_groups=[{"lr": 1e-9}])
    ts = _steps(opt)
    ts._course_iter(1)
    assert ts.args.lr == 3e-4
    assert opt.param_groups[0]["lr"] == 3e-4
    ts._course_iter(36)
    assert opt.param_groups[0]["lr"] == 5e-5


def test_iter_shard_dirs_dedupes_same_name(tmp_path: Path) -> None:
    """同一 seed 两份残留（竞速输家）只保留 manifest 最早一份，退役响亮日志。"""
    from remote.hub_client import iter_shard_dirs

    it = tmp_path / "it7"
    old = it / "w3" / "rl_s2000_seed42"
    new = it / "dist" / "mac" / "rl_s2000_seed42"
    uniq = it / "w4" / "rl_s2000_seed99"
    partial = it / "w5" / "rl_s2000_seed77"  # 无 obs/metrics → 不完整，剔除
    for d in (old, new, uniq, partial):
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}", encoding="utf-8")
    for d in (old, new, uniq):
        (d / "obs.npy").write_bytes(b"x")
    os.utime(old / "manifest.json", (1000, 1000))
    os.utime(new / "manifest.json", (2000, 2000))

    msgs: list[str] = []
    dirs = iter_shard_dirs(str(tmp_path), 7, log=msgs.append)
    assert sorted(d.name for d in dirs) == ["rl_s2000_seed42", "rl_s2000_seed99"]
    kept = next(d for d in dirs if d.name == "rl_s2000_seed42")
    assert kept == old, "先写盘者（= 结算赢家）胜"
    assert any("retire" in m and "dist" in m for m in msgs), "退役目录要响亮日志"
    assert len(dirs) == len({d.name for d in dirs}), "payload 不再含重复 arcname"


def test_eval_seeds_support_100_games() -> None:
    """eval_games_per_stage:100 不再被常量截断；前 2 seed 历史前缀不变。"""
    from rl.eval_local import EVAL_SEEDS

    assert len(EVAL_SEEDS) == 100
    assert EVAL_SEEDS[0] == 860001 and EVAL_SEEDS[1] == 860002
    assert EVAL_SEEDS[-1] == 860100
    assert EVAL_SEEDS[:100] == EVAL_SEEDS


def test_backup_weights_honors_course_dir(tmp_path: Path) -> None:
    from rl.archive import backup_weights

    src = tmp_path / "w.json"
    src.write_text("{}", encoding="utf-8")
    bdir = tmp_path / "p4b"
    out = backup_weights(str(src), 12, prefix="p4-onset", backup_dir=str(bdir))
    assert out is not None
    p = Path(out)
    assert p.parent == bdir and p.name.startswith("p4-onset.it12.")


def test_backup_relative_dir_resolves_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对 backup_dir 按仓库根解析（与 TrainingLoop cwd 无关）。"""
    import rl.archive as archive

    src = tmp_path / "w.json"
    src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(archive, "REPO_ROOT", tmp_path)
    out = archive.backup_weights(str(src), 1, prefix="x", backup_dir="nn-training/weights/t")
    assert out is not None
    assert Path(out).parent == tmp_path / "nn-training" / "weights" / "t"


def test_course_normalize_ret_override() -> None:
    """R5：课程显式 normalize_ret 才进 flat_overrides（默认缺席，保持现状）。"""
    assert "normalize_ret" not in _course().flat_overrides()
    c = CourseConfig(name="nr-test", normalize_ret=True)
    assert c.flat_overrides()["normalize_ret"] is True


def test_course_kickstart_overrides() -> None:
    """§363：kickstart_ref/warmup_iters 显式才进 flat_overrides（默认缺席）。"""
    assert "kickstart_ref" not in _course().flat_overrides()
    assert "warmup_iters" not in _course().flat_overrides()
    c = CourseConfig(name="ks-test", kickstart_ref=True, warmup_iters=0)
    assert c.flat_overrides()["kickstart_ref"] is True
    assert c.flat_overrides()["warmup_iters"] == 0


def test_course_ent_break_overrides() -> None:
    """§339 回归：F4 三阈值课程可配（此前漏映射，课程值从未落地）。"""
    assert "ent_break" not in _course().flat_overrides()
    c = CourseConfig(
        name="ent-test", ent_break=0.25, ent_break_consec=5,
        ent_break_max_winrate=0.4,
    )
    o = c.flat_overrides()
    assert o["ent_break"] == 0.25
    assert o["ent_break_consec"] == 5
    assert o["ent_break_max_winrate"] == 0.4
