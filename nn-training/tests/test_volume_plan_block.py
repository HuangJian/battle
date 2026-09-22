"""tests/test_volume_plan_block.py —— 动态采集与**离线计划**的接线（2026-09-22 用户指令）。

需求原文（用户）：**「离线课程，请确保云端是按照 target_transitions 来产生足够样本，与本地
集群 rollout 一致」**。

缺陷：`target_transitions` 的采样量由 `rl.volume_waves` 的配额反解决定（本地集群靠
`rl.volume_quota` 的连续派发逼近达标线），而全离线/半离线腿（kind=run）的逐轮语料来自
`rl/plan.pairs_for` → 老的 `build_pairs`，它**只认 `seeds_per_stage` / `seed_rotate`**，
完全不认 `target_transitions`。后果有两个，都是静默的：

  * 采多少局 = 课程里那个 `seed_rotate` 数字（与目标脱钩）；配得比 `G0` 小就**少采**，
    而云机没有补波机制（一轮一个 job、PPO 在 job 里跑完）；
  * 种子与本地集群那一轮**没有关系**（不同的流构造），所以「同课程同轮」的两台机器
    拿不到同一批语料做对照。

本文件钉住修法后的三条契约：

  1. **同函数**：`plan.pairs_for`（节点重放）与 `TrainingLoop._iteration_pairs`（本地预排表）
     都走 `volume_waves.initial_wave_pairs` —— 每关 `G0 = ceil(ceil(target/关数)/est)` 局；
  2. **同种子**：该初波就等于**连续配额流的前缀**（`rl.volume_quota.continuous_pairs`
     start_idx=0 那一路，即本地集群本轮真正派出去的前 G0 局），两支流的键逐位相同
     （`[rotate_seed, 0x5EED, it, stage, 0]`）；
  3. **同训练量**：计划里的 `per_stage_quota` 被逐轮带进节点合成的 manifest（训练侧
     `ppo/engine` 按它逐关截断）——采集量与训练量因此与本地口径一致。

另有形状校验（`validate_volume_block`：per_stage_quota / games_per_stage 必须与反解公式
一致）与发布期漂移自检（`check_plan_against_args`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.protocol import ProtocolError, normalize_manifest
from rl.plan import (
    build_plan,
    check_plan_against_args,
    dump_plan,
    pairs_for,
    planned_iters,
    validate_plan,
)
from rl.volume_quota import continuous_pairs, target_per_stage
from rl.volume_waves import (
    initial_wave_pairs,
    validate_volume_block,
    volume_block,
    volume_pairs_from_args,
    wave_pairs,
)


def _args(**over: object) -> SimpleNamespace:
    """最小 args（与 tests/test_plan.py 同形）+ 动态采集三个旋钮。"""
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": "2000-2003",
        "seed_rotate": 150,
        "seeds": "1-2",
        "total_stages": 4,
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
        "mode": "per-tick",
        # ── 动态采集（x20 口径：48000 samples/轮 ÷ 4 关 ÷ 147 samples/局 = 82 局/关）──
        "target_transitions": 0,
        "est_samples_per_game": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


X20 = {"target_transitions": 48000, "est_samples_per_game": 147}
X20_STAGES = [2000, 2001, 2002, 2003]
X20_G0 = 82  # ceil(ceil(48000/4)/147) = ceil(12000/147) = 82


# ────────────────────────── 1. 配额块：反解与形状 ──────────────────────────


def test_volume_block_off_is_none() -> None:
    """`target_transitions` 缺席/为 0 ⇒ None（老课程一个函数都不调，逐字节不变）。"""
    assert volume_block(_args()) is None
    assert volume_block(_args(target_transitions=0, est_samples_per_game=147)) is None


def test_volume_block_matches_quota_math() -> None:
    """块里的每个数字都由公式反解（两侧同源，不许手填）。"""
    blk = volume_block(_args(**X20), est_samples_per_game=X20["est_samples_per_game"])
    assert blk is not None
    assert blk["stages"] == X20_STAGES
    assert blk["per_stage_quota"] == 12000
    assert blk["games_per_stage"] == X20_G0
    assert validate_volume_block(blk) == blk


def test_volume_block_is_stage_order_insensitive() -> None:
    """`--stages` 的书写顺序不影响任何一局的种子（`wave_pairs` 内部按 stage 升序）。"""
    a = volume_block(_args(**{**X20, "stages": "2002,2000"}))
    b = volume_block(_args(**{**X20, "stages": "2000,2002"}))
    assert a is not None and b is not None
    assert a["stages"] == [2002, 2000]  # 忠于 args 的书写顺序（不悄悄重排）
    assert a["games_per_stage"] == b["games_per_stage"]
    assert initial_wave_pairs(9, 4, stages=a["stages"], games_per_stage=a["games_per_stage"]) == (
        initial_wave_pairs(9, 4, stages=b["stages"], games_per_stage=b["games_per_stage"])
    )


@pytest.mark.parametrize(
    "over, frag",
    [
        ({"stages": ""}, "--stages"),
        ({"est_samples_per_game": 0}, "est_samples_per_game"),
    ],
)
def test_volume_block_refuses_incomplete_knobs(over: dict, frag: str) -> None:
    """缺关集/缺估计值 ⇒ 响亮报错（静默降级 = 采集量与目标脱钩，正是要防的事）。"""
    with pytest.raises(ValueError, match=frag):
        volume_block(_args(**{**X20, **over}))


@pytest.mark.parametrize(
    "patch",
    [
        {"games_per_stage": X20_G0 + 1},
        {"per_stage_quota": 12001},
        {"stages": []},
        {"est_samples_per_game": 0},
        {"target_transitions": True},  # bool 不是整数（JSON 里 true/false 混进来的路）
    ],
)
def test_validate_volume_block_rejects_drift(patch: dict) -> None:
    """手改过的块（数字与公式不符 / 空关集 / bool）一律拒收。"""
    blk = volume_block(_args(**X20))
    assert blk is not None
    with pytest.raises(ValueError):
        validate_volume_block({**blk, **patch})


# ────────────────────────── 2. 种子：初波 == 连续流前缀 ──────────────────────────


def test_initial_wave_pairs_are_continuous_prefix() -> None:
    """★「与本地集群 rollout 一致」的实现基础：初波就是连续配额流的前 G0 局。

    本地集群本轮走 `rl.volume_quota`（连续派发）：逐关从 `(rotate_seed, it, stage)` 独立流
    的第 0 个 seed 开始抽。云机的初波必须与它是**同一批**（不是「差不多」——逐位相同）。
    """
    rs, it = 4242, 17
    mine = initial_wave_pairs(rs, it, stages=X20_STAGES, games_per_stage=X20_G0)
    local = continuous_pairs(rs, it, {s: X20_G0 for s in X20_STAGES}, {s: 0 for s in X20_STAGES})
    assert mine == local
    # 也是 wave 0 那一路（老预排表的构造）——两个模块的流键是同一个
    assert mine == wave_pairs(rs, it, {s: X20_G0 for s in X20_STAGES}, 0)
    assert len(mine) == X20_G0 * len(X20_STAGES)
    # 逐关局数严格 == G0（分关配额的分母不许被别的关挤走）
    assert [s for s, _ in mine].count(2000) == X20_G0
    # 前缀性：多要一局 = 在流的**尾部**追加（前面那批不动）
    more = continuous_pairs(rs, it, {2000: X20_G0 + 3}, {2000: 0})
    assert more[:X20_G0] == [(s, sd) for s, sd in mine if s == 2000]


def test_initial_wave_pairs_keys_by_it_and_stage() -> None:
    """换 it / 换关 ⇒ 换种子；同一 (rotate_seed, it, 关) 跨进程逐位一致（可重放）。"""
    a = initial_wave_pairs(4242, 17, stages=[2000], games_per_stage=3)
    assert a == initial_wave_pairs(4242, 17, stages=[2000], games_per_stage=3)
    assert a != initial_wave_pairs(4242, 18, stages=[2000], games_per_stage=3)
    assert a != initial_wave_pairs(4243, 17, stages=[2000], games_per_stage=3)
    assert a != initial_wave_pairs(4242, 17, stages=[2001], games_per_stage=3)


# ────────────────────────── 3. 计划：带块 / 重放 / 自检 ──────────────────────────


def test_plan_carries_volume_and_replays_initial_wave() -> None:
    """计划带上块以后：逐轮对集 == 初波前缀，且过 dump/load 往返（节点看的是 JSON）。"""
    args = _args(**X20)
    blk = volume_block(args)
    plan = build_plan(args, it=3, iters_total=9, rotate_seed=4242, volume=blk, log=lambda _m: None)
    assert plan["volume"] == blk
    for it in planned_iters(plan):
        assert pairs_for(plan, it) == initial_wave_pairs(
            4242, it, stages=X20_STAGES, games_per_stage=X20_G0
        )
    again = validate_plan(json.loads(dump_plan(plan).decode("utf-8")))
    assert again["volume"] == blk
    assert pairs_for(again, 4) == pairs_for(plan, 4)


def test_plan_without_volume_keeps_build_pairs() -> None:
    """没有块 ⇒ 老口径（`build_pairs`）逐字节不变——已有课程/在飞的包不受影响。"""
    args = _args(seed_rotate=150)
    plan = build_plan(args, it=3, iters_total=6, rotate_seed=4242, log=lambda _m: None)
    assert "volume" not in plan
    from rl.course import build_pairs

    assert pairs_for(plan, 4) == build_pairs(args, 4, 4242)


def test_check_plan_against_args_catches_target_drift() -> None:
    """发布期自检：换一门课/改目标 ⇒ 计划重放对集与 args 重解不一致，拒发。"""
    args = _args(**X20)
    plan = build_plan(
        args, it=3, iters_total=6, rotate_seed=4242, volume=volume_block(args), log=lambda _m: None
    )
    check_plan_against_args(args, plan)  # 一致 ⇒ 静默通过

    with pytest.raises(ProtocolError, match="重放对集"):
        check_plan_against_args(_args(**{**X20, "target_transitions": 96000}), plan)
    with pytest.raises(ProtocolError, match="重放对集"):
        check_plan_against_args(_args(**{**X20, "stages": "2000-2002"}), plan)
    # 计划带块而 args 已经关了动态采集 ⇒ 也是同一类漂移，响亮拒
    with pytest.raises(ProtocolError, match="target_transitions ≤ 0"):
        check_plan_against_args(_args(est_samples_per_game=147), plan)


def test_validate_plan_rejects_tampered_volume() -> None:
    """被改过的 `plan.json`（G0 与公式不符）在**节点侧第一道门**就拒收，一局不跑。"""
    args = _args(**X20)
    raw = json.loads(
        dump_plan(
            build_plan(
                args,
                it=1,
                iters_total=3,
                rotate_seed=7,
                volume=volume_block(args),
                log=lambda _m: None,
            )
        ).decode("utf-8")
    )
    raw["volume"]["games_per_stage"] = X20_G0 + 5
    with pytest.raises(ProtocolError, match="volume"):
        validate_plan(raw)


def test_volume_pairs_from_args_returns_none_when_off() -> None:
    """无动态采集时 `volume_pairs_from_args` 返 None（调用方据此走 `build_pairs` 对照）。"""
    assert volume_pairs_from_args(_args(), 4, 4242) is None
    got = volume_pairs_from_args(_args(**X20), 4, 4242, est_samples_per_game=147)
    assert got == initial_wave_pairs(4242, 4, stages=X20_STAGES, games_per_stage=X20_G0)


# ────────────────────────── 4. 训练侧：配额随 manifest 走到节点 ──────────────────────────


def test_iteration_manifest_carries_per_stage_quota(tmp_path: Path) -> None:
    """逐轮 manifest 带 `per_stage_quota`（训练侧逐关截断），且语料 = 每关 G0 局。

    这条是「同训练量」的那一半：采集量对了但 manifest 丢了配额，训练照样吃掉全量
    （或反过来），两台机器的读数就不可比。
    """
    import remote.run_loop as run_loop_mod
    from remote.artifacts import sha256_bytes
    from remote.protocol import encode_opt_tar, encode_weights_json

    args = _args(**X20)
    plan = build_plan(
        args,
        it=0,
        iters_total=2,
        rotate_seed=4242,
        volume=volume_block(args),
        log=lambda _m: None,
    )
    manifest = normalize_manifest(
        {
            "proto": 1,
            "kind": "run",
            "runId": "run-volume",
            "it": 0,
            "job_id": "j" * 16,
            "commit": "c" * 40,
            "code_sha256": "z" * 64,
            "course": "// course jsonc\n{}",
            "course_fp": "f" * 64,
            "reward_formula": "score",
            "formula_hash": "h" * 40,
            "metrics_version": 1,
            "gamma": 0.995,
            "lam": 0.95,
            "mode": "per-tick",
            "seed": "s" * 64,
            "epochs": 1,
            "mb": 8,
            "lr": 3e-4,
            "init_weights_fp": "w" * 64,
            "data_fp": "d" * 64,
            "payload_sha256": "p" * 64,
            "ts_code_sha256": "t" * 64,
            "rollout": {
                "argv": [["tools/sim/export-rl-rollout.ts", "--out", "w0"]],
                "wver": "w" * 64,
                "workers": 1,
                "game_timeout_sec": 0.0,
                "bun": "bun",
            },
            "plan_sha256": sha256_bytes(dump_plan(plan)),
        }
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "init_weights.json").write_bytes(b'{"anchor":true}')
    (job_dir / "plan.json").write_bytes(dump_plan(plan))
    calls: list[dict] = []

    def _agg() -> dict:
        return {
            "policy": 0.1,
            "value": 0.2,
            "entropy": 0.3,
            "kl": 0.01,
            "mean_ret": 0.5,
            "steps": 100,
            "chunks": 1,
        }

    first = {
        "job_id": manifest["job_id"],
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(b'{"it":0}'),
        "opt_tar_b64": encode_opt_tar(b"opt"),
        "agg": _agg(),
        "report": {
            "games": 328,
            "shards": 328,
            "winRate": 0.5,
            "totalSamples": 328 * 147,
            "totalTicks": 328 * 1470,
            "elapsedSec": 10.0,
            "outcomes": {"win": 164, "loss": 164},
        },
        "commit_echo": manifest["commit"],
        "ppo_sec": 1.0,
        "wire": {"rollout_sec": 1.0, "rollout_bytes": 0},
    }

    def _iter_result(m: dict, weights: bytes) -> dict:
        return {
            "job_id": m["job_id"],
            "data_fp": m["data_fp"],
            "init_weights_fp": m["init_weights_fp"],
            "weights_json": encode_weights_json(weights),
            "opt_tar_b64": encode_opt_tar(b"opt"),
            "agg": _agg(),
            "report": {
                "games": 328,
                "shards": 328,
                "winRate": 0.5,
                "totalSamples": 328 * 147,
                "totalTicks": 328 * 1470,
                "elapsedSec": 10.0,
                "outcomes": {"win": 164, "loss": 164},
            },
            "commit_echo": m["commit"],
            "ppo_sec": 1.0,
            "wire": {"rollout_sec": 1.0, "rollout_bytes": 0},
        }

    def _fake_run_job(base_url: str, token: str, job: dict, **kw: object) -> dict:
        m = normalize_manifest(job["manifest"])
        calls.append(m)
        n = len(calls)
        weights = json.dumps({"it": m["it"], "w": n}).encode("utf-8")
        return _iter_result(m, weights)

    run_loop_mod.run_plan_job(
        job_id=manifest["job_id"],
        manifest=manifest,
        job_dir=job_dir,
        work_dir=tmp_path / "work",
        plan=plan,
        plan_sha256=manifest["plan_sha256"],
        first_result=first,
        artifacts_dir=tmp_path / "art",
        run_job_fn=_fake_run_job,
        deliver=False,
        log=lambda _m: None,
    )
    assert calls, "自主段必须至少合成一轮 job"
    for m in calls:
        assert m["per_stage_quota"] == 12000, "训练侧配额没随 manifest 走到节点"
        per_stage: dict[int, int] = {}
        for row in m["rollout"]["argv"]:
            stage = int(row[row.index("--stages") + 1])
            per_stage[stage] = per_stage.get(stage, 0) + 1
        assert per_stage == {s: X20_G0 for s in X20_STAGES}, per_stage
