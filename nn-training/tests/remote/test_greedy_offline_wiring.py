"""自主云机轮内 greedy 的接线面（plan/greedy-offline-autonomous.plan.md）。

钉四件事：
1. spec 白名单收 `greedy_quota` / `greedy_round_target`（缺席的旧包输出逐字节不变）；
2. `remote/plan_run` 的 B 口径（普通减量、逐关配额保持整轮值、分母是放大后 target）；
3. `remote/worker` 的轮内阶段真的会跑（普通 `(stage, seed)` 不撞）；
4. `remote/artifacts.LEDGER_FIELDS` 把三个 greedy 键搬进课程账本。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.manifest import ROLLOUT_SPEC_DEFAULTS, validate_rollout_spec
from common.protocol import ProtocolError
from remote.artifacts import LEDGER_FIELDS, ledger_row_from_metrics
from remote.offline_greedy import greedy_stage as _offline_greedy_stage
from remote.offline_greedy import merge as _merge_greedy_stage
from remote.plan_run import _greedy_budget, _volume_with_target


def _row(stage: int = 0, seed: int = 1, out: str = "w0") -> list[str]:
    return [
        "tools/sim/export-rl-rollout.ts",
        "--stages",
        str(stage),
        "--seeds",
        str(seed),
        "--out",
        out,
        "--weights",
        "init_weights.json",
        "--wver",
        "w0",
    ]


def _spec(**extra: object) -> dict:
    return {"argv": [_row()], "wver": "w0", "workers": 4, "game_timeout_sec": 30.0, **extra}


# --------------------------------------------------------------- spec 白名单


def test_spec_accepts_greedy_keys() -> None:
    block = {"win": 0.1, "loss": 0.0, "timeout": 0.02, "soft": True}
    out = validate_rollout_spec(_spec(greedy_quota=block, greedy_round_target=12288))
    assert out["greedy_quota"] == block
    assert out["greedy_round_target"] == 12288


def test_spec_absence_changes_nothing() -> None:
    """缺席 ⇒ 归一化后的**键集**与旧契约逐键相同（旧包逐字节不变的可测形态）。"""
    out = validate_rollout_spec(_spec())
    assert set(out) == set(ROLLOUT_SPEC_DEFAULTS) | {"argv"}
    assert {"greedy_quota", "greedy_round_target"}.isdisjoint(ROLLOUT_SPEC_DEFAULTS)


def test_spec_rejects_half_contract_and_bad_types() -> None:
    with pytest.raises(ProtocolError, match="greedy_round_target"):
        validate_rollout_spec(_spec(greedy_quota={"win": 0.1}))
    with pytest.raises(ProtocolError, match="孤键"):
        validate_rollout_spec(_spec(greedy_round_target=100))
    with pytest.raises(ProtocolError, match="必须是对象"):
        validate_rollout_spec(_spec(greedy_quota=0.12, greedy_round_target=100))
    with pytest.raises(ProtocolError, match="正整数"):
        validate_rollout_spec(_spec(greedy_quota={"win": 0.1}, greedy_round_target=0))


# --------------------------------------------------------------- B 口径


def _volume(target: int = 1200) -> dict:
    return {
        "target_transitions": target,
        "stages": [0, 1],
        "per_stage_quota": 600,
        "games_per_stage_by_stage": {0: 30, 1: 40},
        "est_s_by_stage": {0: 20, 1: 15},
        "est_hi_factor": 1.2,
        "widen_width": 8,
        "topup": {"max_games_per_stage": 60},
    }


def test_volume_with_target_keeps_block_self_consistent() -> None:
    vol = _volume(1200)
    out = _volume_with_target(vol, 1056)
    assert out["target_transitions"] == 1056
    assert out["per_stage_quota"] == 528  # ceil(1056/2)，同一族纯函数
    assert set(out["games_per_stage_by_stage"]) == {0, 1}
    assert sum(out["games_per_stage_by_stage"].values()) <= sum(
        vol["games_per_stage_by_stage"].values()
    )
    assert vol["target_transitions"] == 1200  # 输入不被就地改


def test_greedy_budget_uses_full_target_and_dumps_block() -> None:
    course = SimpleNamespace(greedy_quota={"win": 0.09, "loss": 0.02, "timeout": 0.01})
    got = _greedy_budget(course, _volume(1200))
    assert got is not None
    plan, target, block = got
    assert target == 1200
    assert plan.total_target == round(0.12 * 1200)
    assert block["win"] == 0.09 and block["timeout"] == 0.01


def test_greedy_budget_absent_course_or_zero_ratios() -> None:
    assert _greedy_budget(SimpleNamespace(greedy_quota=None), _volume()) is None
    assert _greedy_budget(None, _volume()) is None
    zero = SimpleNamespace(greedy_quota={"win": 0.0, "loss": 0.0, "timeout": 0.0})
    assert _greedy_budget(zero, _volume()) is None


# --------------------------------------------------------------- 轮内接线


def _manifest(block: dict | None = None) -> dict:
    rollout = _spec()
    rollout["volume"] = {"stages": [0], "rotate_seed": 99, "est_s_by_stage": {0: 100}}
    if block is not None:
        rollout["greedy_quota"] = block
        rollout["greedy_round_target"] = 1200
    return {"it": 5, "rollout": rollout}


def test_offline_stage_is_a_noop_without_quota(tmp_path: Path) -> None:
    out = _offline_greedy_stage(
        manifest=_manifest(),
        iter_info={"shards_root": str(tmp_path)},
        shard_dirs=[],
        job_dir=tmp_path,
        rctx=SimpleNamespace(workers=2),
        ts_dir=tmp_path,
        log=lambda _m: None,
    )
    assert out is None
    assert not (tmp_path / "greedy").exists()


def test_offline_stage_runs_waves_and_skips_ordinary_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import remote.offline_greedy as offline_greedy

    job_dir = tmp_path / "job"
    ppo_root = tmp_path / "scratch-round"  # 换根档：PPO 装载根 ≠ job 目录
    ppo_root.mkdir(parents=True)
    ordinary_dir = ppo_root / "w3" / "rl_s0_seed5"
    ordinary_dir.mkdir(parents=True)
    seen: list[list[tuple[int, int]]] = []

    def fake_run(job_dir, spec, *, ts_dir=None, log=lambda _m: None, ctx=None, it=0, **_kw):
        pairs: list[tuple[int, int]] = []
        dirs: list[str] = []
        for row in spec["argv"]:
            stage = int(row[row.index("--stages") + 1])
            seed = int(row[row.index("--seeds") + 1])
            out_name = row[row.index("--out") + 1]
            assert row[-1] == "--teacher-argmax"
            pairs.append((stage, seed))
            d = Path(job_dir) / out_name / f"rl_s{stage}_seed{seed}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.json").write_text(
                json.dumps(
                    {
                        "labelMode": "greedy",
                        "stage": stage,
                        "seed": seed,
                        "nSamples": 600,
                        "outcome": "stage_clear",
                    }
                ),
                encoding="utf-8",
            )
            dirs.append(str(d))
        seen.append(pairs)
        return {
            "report": {
                "games": len(pairs),
                "winRate": 1.0,
                "totalSamples": 600 * len(pairs),
                "outcomes": {"stage_clear": len(pairs)},
            },
            "shard_dirs": dirs,
        }

    # 该模块顶层 import 了 `run_iter_rollout` ⇒ 必须 patch **它的**命名空间（patch 源模块打不着）。
    monkeypatch.setattr(offline_greedy, "run_iter_rollout", fake_run)

    out = _offline_greedy_stage(
        manifest=_manifest({"win": 0.12}),
        iter_info={
            "shards_root": str(ppo_root),
            "report": {"games": 3, "winRate": 0.5, "totalSamples": 900, "outcomes": {}},
        },
        shard_dirs=[str(ordinary_dir)],
        job_dir=job_dir,
        rctx=SimpleNamespace(workers=2),
        ts_dir=tmp_path,
        log=lambda _m: None,
    )
    assert out is not None
    stats = out["greedy_quota_stats"]
    assert stats["target"] == 144  # round(0.12 × 1200)，分母是放大后的整轮 target
    assert stats["collect_target"] == 144  # B 口径：不吃 oversample_cap
    assert stats["sampling_report"]["samples"] == 900
    assert seen, "greedy 波一次都没跑"
    assert all((0, 5) not in wave for wave in seen), "普通采样的 (stage, seed) 被 greedy 重采了"
    assert out["shard_dirs"], "greedy shard 必须被列出来（PPO 装载根要对得上）"
    for d in out["shard_dirs"]:
        Path(d).resolve().relative_to(ppo_root.resolve())  # 全在 PPO 装载根里（否则采了没训）
        assert Path(d, "manifest.json").exists()  # 搬动之后文件还在


def test_merge_greedy_stage_matches_trainer_keys() -> None:
    iter_info: dict[str, object] = {
        "report": {"games": 3, "totalSamples": 900, "winRate": 0.5, "outcomes": {}},
        "volume_stage_stats": {"rule": 1, "stages": {}},
    }
    out = {
        "report": {"games": 2, "totalSamples": 600, "winRate": 1.0, "outcomes": {}},
        "greedy_quota_stats": {"rule": 2, "total": 600, "degraded": False},
        "by_stage": {"samples": 600, "games": 2, "stages": {"0": {"samples": 600, "games": 2}}},
        "waves": 1,
        "shard_dirs": ["x"],
    }
    _merge_greedy_stage(iter_info, out, log=lambda _m: None)
    stats = iter_info["greedy_quota_stats"]
    assert isinstance(stats, dict) and stats["rule"] == 2
    vss = iter_info["volume_stage_stats"]
    assert isinstance(vss, dict)
    assert vss["greedy"]["games"] == 2
    assert vss["rule"] == 1  # 普通口径不被覆盖
    rep = iter_info["report"]
    assert isinstance(rep, dict) and rep["games"] == 5  # 主 report 是全量（普通 + greedy）


# --------------------------------------------------------------- 账本搬运


def test_ledger_fields_carry_greedy_keys() -> None:
    names = {name for name, _src, _key in LEDGER_FIELDS}
    assert {"greedy_quota_stats", "sampling_samples", "sampling_games"} <= names
    row = {
        "it": 5,
        "agg": {},
        "greedy_quota_stats": {"rule": 2, "total": 600},
        "sampling_samples": 900,
        "sampling_games": 3,
    }
    ev = ledger_row_from_metrics(row, run_id="r1", source="offline_backfeed")
    assert ev is not None
    assert ev["greedy_quota_stats"] == {"rule": 2, "total": 600}
    assert ev["sampling_samples"] == 900
    assert ev["sampling_games"] == 3
