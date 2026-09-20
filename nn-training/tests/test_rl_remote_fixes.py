"""test_rl_remote_fixes —— 2026-09-06 p4-onset 监控四修复的回归锚（nn.progress §19/§20）。

① lr 三段表在 remote 模式生效（_course_iter 折算 args.lr → job manifest → worker
   Adam）② 同 seed shard 竞速输家退场 + 发布端同名去重 ③ EVAL_SEEDS 扩 100
④ 课程 backup_prefix/backup_dir 生效。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.config import CourseConfig, PpoScheduleEntry, RewardBlock
from rl.loop_steps import TrainingSteps, _remote_forward_agg, kickstart_warn_kind

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


def test_remote_forward_agg_carries_kickstart() -> None:
    """R5§363 回归（2026-09-08 vk1 事故）：云 worker agg 的 kickstart 缰绳遥测必须
    透传到训练侧结算——否则 iteration 行 kickstart 恒 None，worker 缰绳明明在跑
    却整根腿被误判「课程配置未起效」而作废。

    样例取自真实 vk1 结果（it8 job 18d73cee：agg.kickstart≈0.63、kl≈0.005）。
    """
    agg = {
        "policy": -0.0023,
        "value": 0.4604,
        "entropy": 0.3479,
        "kl": 0.00496,
        "mean_ret": 0.0016,
        "kickstart": 0.6343,
    }
    out = _remote_forward_agg(agg)
    assert out["kickstart"] == pytest.approx(0.6343)
    assert out["kl"] == pytest.approx(0.00496)
    assert out["value"] == pytest.approx(0.4604)


def test_remote_forward_agg_old_worker_defaults() -> None:
    """旧 worker（agg 无 kickstart 键）→ 0.0 兜底，不破迭代行结构、不抛。"""
    agg = {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "mean_ret": 0.5}
    out = _remote_forward_agg(agg)
    assert out["kickstart"] == 0.0
    assert out["kl"] == 0.4


def test_kickstart_warn_kind() -> None:
    """x2-start it31 误报回归：系数衰减到期后的 agg kickstart=0 是预期行为，
    不得判 warn（只在系数仍活跃却无遥测时告警 worker 未执行缰绳）。"""
    # 系数活跃 + 无遥测 = 真事故（2026-09-08 vk1 案）→ warn
    assert kickstart_warn_kind(kick_on=True, smoke=False, agg_kickstart=0.0, kick_coef=0.5) == "warn"
    assert kickstart_warn_kind(kick_on=True, smoke=False, agg_kickstart=0.0, kick_coef=1.0) == "warn"
    # 系数已到期（x2-start it31 实测 0.5**30=9.3e-10 < NEGLIGIBLE_COEF）→ expired，不告警
    assert kickstart_warn_kind(kick_on=True, smoke=False, agg_kickstart=0.0, kick_coef=0.5**30) == "expired"
    assert kickstart_warn_kind(kick_on=True, smoke=False, agg_kickstart=0.0, kick_coef=0.0) == "expired"
    # 有遥测 / 未要求缰绳 / 冒烟轮 → ok（与旧行为一致：不告警）
    assert kickstart_warn_kind(kick_on=True, smoke=False, agg_kickstart=0.18, kick_coef=0.5) == "ok"
    assert kickstart_warn_kind(kick_on=False, smoke=False, agg_kickstart=0.0, kick_coef=0.5) == "ok"
    assert kickstart_warn_kind(kick_on=True, smoke=True, agg_kickstart=0.0, kick_coef=0.5) == "ok"


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


def _mk_shard(d: Path, manifest: dict) -> Path:
    """建一个「完整」shard（obs.npy + manifest.json）——发布端可收的最小形状。"""
    d.mkdir(parents=True)
    (d / "obs.npy").write_bytes(b"x")
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_iter_shard_dirs_drops_foreign_lineage(tmp_path: Path) -> None:
    """D14（2026-09-20 事故回归）：it{it} 里混入异血缘 shard 时**不进 payload**。

    事故形状：c6-chip it16 打出的 payload 里混了 21 份课程文件编辑前的旧血缘 shard，
    云端 worker 逐 shard 拒收 ⇒ 整份 job 退回（`D14 course_fp 不匹配`），hub 侧永远
    等不到结果、worker 反复领同一份死活。成因是发布端（本函数）没按血缘过滤，而
    对账端（resume._scan_shards）与云端都在过滤——同一个目录两种口径。
    """
    from remote.hub_client import iter_shard_dirs

    job_course, job_corpus = "A" * 64, "C" * 64
    it = tmp_path / "it16"
    # 同血缘（文件血缘 + 无 corpus_fp 的 legacy shard）
    same = _mk_shard(it / "w0" / "rl_s2000_seed1", {"course_fp": job_course})
    # 同**语料身份**、不同文件血缘（课程只改了预算/注释字段）⇒ 必须收
    semantic = _mk_shard(
        it / "w0" / "rl_s2000_seed2", {"course_fp": "B" * 64, "corpus_fp": job_corpus}
    )
    # 真跨语料（旧课程版本）⇒ 剔除
    _mk_shard(it / "w0" / "rl_s2000_seed3", {"course_fp": "B" * 64, "corpus_fp": "D" * 64})

    msgs: list[str] = []
    dirs = iter_shard_dirs(
        str(tmp_path), 16, log=msgs.append, course_fp=job_course, corpus_fp=job_corpus
    )
    assert sorted(d.name for d in dirs) == [same.name, semantic.name], (
        "异血缘 shard 必须被挡在 payload 外（否则云端整份拒收）"
    )
    assert any("D14" in m and "剔除" in m for m in msgs), "剔除去向要响亮日志"

    # 不过滤（旧调用形状）时见到全部 3 份——即修复前会打进 payload 的那个集合
    assert len(iter_shard_dirs(str(tmp_path), 16, log=lambda _m: None)) == 3
    # manifest 不可读 ⇒ 不收（宁可少一份，也不让云端整份退回）
    (it / "w0" / "rl_s2000_seed4").mkdir()
    (it / "w0" / "rl_s2000_seed4" / "obs.npy").write_bytes(b"x")
    (it / "w0" / "rl_s2000_seed4" / "manifest.json").write_text("{", encoding="utf-8")
    dirs2 = iter_shard_dirs(
        str(tmp_path), 16, log=lambda _m: None, course_fp=job_course, corpus_fp=job_corpus
    )
    assert sorted(d.name for d in dirs2) == [same.name, semantic.name]


def test_iter_bc_shard_dirs_drops_foreign_lineage(tmp_path: Path) -> None:
    """BC 同规（plan/bc-cloud-integration）：BC shard 也带 course_fp，同一判据同一过滤。"""
    from remote.hub_client import iter_bc_shard_dirs

    job_course, job_corpus = "A" * 64, "C" * 64
    root = tmp_path / "bc-data" / "it7"
    same = _mk_shard(root / "bc_s0_seed1", {"course_fp": job_course, "corpus_fp": job_corpus})
    _mk_shard(root / "bc_s0_seed2", {"course_fp": "B" * 64, "corpus_fp": "D" * 64})

    dirs = iter_bc_shard_dirs(
        str(tmp_path), 7, log=lambda _m: None, course_fp=job_course, corpus_fp=job_corpus
    )
    assert [d.name for d in dirs] == [same.name]
    # 无血缘参数（旧调用形状）⇒ 不过滤（行为不变）
    assert len(iter_bc_shard_dirs(str(tmp_path), 7, log=lambda _m: None)) == 2


def test_d14_predicate_is_single_implementation() -> None:
    """判据只有一份：发布端用的就是云端拒收用的那一个函数（漂开 = 发布出去的必被拒）。"""
    from remote.protocol import d14_corpus_match as canonical
    from remote.worker import d14_corpus_match as via_worker

    assert via_worker is canonical


def test_eval_seeds_support_200_games() -> None:
    """eval_games_per_stage:200 不再被常量截断；前 100 seed 历史前缀逐字节不变。"""
    from rl.eval_local import EVAL_SEEDS

    assert len(EVAL_SEEDS) == 200
    assert EVAL_SEEDS[0] == 860001 and EVAL_SEEDS[1] == 860002
    assert EVAL_SEEDS[99] == 860100
    assert EVAL_SEEDS[100] == 860101 and EVAL_SEEDS[-1] == 860200
    # 旧口径（≤100）逐字节兼容：前缀切片不变
    assert EVAL_SEEDS[:100] == tuple(range(860001, 860101))


def test_d14_corpus_match_prefers_semantic_identity() -> None:
    """D14 比对规则（§2026-09-13-level-extraction）：双侧有 corpus_fp 比语义身份；
    任一侧缺（legacy shard / 旧 job）回退文件血缘 course_fp。"""
    from remote.worker import d14_corpus_match

    # 双侧 corpus_fp 一致 ⇒ 过（即使 course_fp 因注释/预算字段编辑而不同）
    assert d14_corpus_match("cA", "X", {"corpus_fp": "X", "course_fp": "cB"})
    # 双侧 corpus_fp 不一致 ⇒ 拒（真跨语料）
    assert not d14_corpus_match("cA", "X", {"corpus_fp": "Y", "course_fp": "cA"})
    # legacy shard（无 corpus_fp）⇒ 回退 course_fp 比对
    assert d14_corpus_match("cA", "X", {"course_fp": "cA"})
    assert not d14_corpus_match("cA", "X", {"course_fp": "cB"})
    # legacy job（无 corpus_fp）⇒ 同样回退文件血缘
    assert d14_corpus_match("cC", "", {"course_fp": "cC"})
    assert not d14_corpus_match("cC", "", {"corpus_fp": "Z", "course_fp": "cD"})


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
