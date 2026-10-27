"""tests/worker/test_volume_plan_block.py —— 动态采集与**离线计划**的接线（2026-09-22 用户指令）。

需求原文（用户）：**「离线课程，请确保云端是按照 target_transitions 来产生足够样本，与本地
集群 rollout 一致」**。

缺陷：`target_transitions` 的采样量由 `biz.volume_waves` 的配额反解决定（本地集群靠
`biz.volume_quota` 的连续派发逼近达标线），而全离线/半离线腿（kind=run）的逐轮语料来自
`biz/plan.pairs_for` → 老的 `build_pairs`，它**只认 `seeds_per_stage` / `seed_rotate`**，
完全不认 `target_transitions`。后果有两个，都是静默的：

  * 采多少局 = 课程里那个 `seed_rotate` 数字（与目标脱钩）；配得比 `G0` 小就**少采**，
    而云机没有补波机制（一轮一个 job、PPO 在 job 里跑完）；
  * 种子与本地集群那一轮**没有关系**（不同的流构造），所以「同课程同轮」的两台机器
    拿不到同一批语料做对照。

本文件钉住修法后的三条契约：

  1. **同函数**：`plan.pairs_for`（节点重放）与 `TrainingLoop._iteration_pairs`（本地预排表）
     都走 `volume_waves.initial_wave_pairs` —— 每关 `G0 = ceil(ceil(target/关数)/est)` 局；
  2. **同种子**：该初波就等于**连续配额流的前缀**（`biz.volume_quota.continuous_pairs`
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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import ProtocolError, normalize_manifest
from worker.plan import (
    build_plan,
    check_plan_against_args,
    dump_plan,
    pairs_for,
    planned_iters,
    validate_plan,
)
from worker.volume_quota import continuous_pairs, target_per_stage
from worker.volume_waves import (
    initial_wave_pairs,
    validate_volume_block,
    volume_block,
    volume_pairs_from_args,
    wave_pairs,
)


def _args(**over: object) -> SimpleNamespace:
    """最小 args（与 tests/worker/test_plan.py 同形）+ 动态采集三个旋钮。"""
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
    a = volume_block(
        _args(**{**X20, "stages": "2002,2000"}), est_samples_per_game=X20["est_samples_per_game"]
    )
    b = volume_block(
        _args(**{**X20, "stages": "2000,2002"}), est_samples_per_game=X20["est_samples_per_game"]
    )
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
    ],
)
def test_volume_block_refuses_incomplete_knobs(over: dict, frag: str) -> None:
    """缺关集 ⇒ 响亮报错（静默降级 = 采集量与目标脱钩，正是要防的事）。

    ⚠ 「缺估计值」**不再**是错误（2026-10-12）：est 已成盘上派生量，无历史 = `0` = 无估计，
    计划里钉 `G_s = 1`（不猜档，见下一条用例）。
    """
    with pytest.raises(ValueError, match=frag):
        volume_block(_args(**{**X20, **over}), est_samples_per_game=X20["est_samples_per_game"])


def test_volume_block_no_estimate_pins_one_game_per_stage() -> None:
    """★ 冷启动（无估计）：计划钉 `G_s = 1` + 补差批数上界换防跑飞档；分关字段同样每关 1 局。

    这是 plan/rollout-estfree-worksteal §4.1 冷启动表的可执行形式：**不猜**，缺口交给云机侧
    的逐局流式调度（`worker/stream_collect`：没有估计就先每关一局，回来一局就有估计）。
    """
    from worker.volume_alloc import (
        DEFAULT_TOPUP_MAX_BATCHES,
        DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN,
    )

    blk = volume_block(_args(**X20), est_samples_per_game=0, ests_by_stage={s: 0 for s in X20_STAGES})
    assert blk is not None
    assert blk["est_samples_per_game"] == 0
    assert blk["games_per_stage"] == 1
    assert blk["games_per_stage_by_stage"] == {s: 1 for s in X20_STAGES}
    assert blk["topup"]["max_batches"] == DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN
    assert validate_volume_block(blk) == blk  # 0 = 无估计 是**合法**块（两侧同源仍成立）
    # 有估计时上界照旧（不被冷启动档顺手改掉）
    keys_blk = volume_block(
        _args(**X20),
        est_samples_per_game=X20["est_samples_per_game"],
        ests_by_stage={s: X20["est_samples_per_game"] for s in X20_STAGES},
    )
    assert keys_blk is not None and keys_blk["topup"]["max_batches"] == DEFAULT_TOPUP_MAX_BATCHES


def test_volume_block_ignores_the_dead_course_key() -> None:
    """★ 反探针⑤：课程键 `est_samples_per_game` **已死** —— 就算 args 上还带着它，块也必须当
    无估计处理（否则「课程写一个数就能改采集量」会静默复活，而它已不再进 `flat_overrides`）。
    """
    blk = volume_block(_args(target_transitions=48000, stages="2000-2003", est_samples_per_game=147))
    assert blk is not None
    assert blk["est_samples_per_game"] == 0
    assert blk["games_per_stage"] == 1


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
    blk = volume_block(_args(**X20), est_samples_per_game=X20["est_samples_per_game"])
    assert blk is not None
    with pytest.raises(ValueError):
        validate_volume_block({**blk, **patch})


# ────────────────────────── 2. 种子：初波 == 连续流前缀 ──────────────────────────


def test_initial_wave_pairs_are_continuous_prefix() -> None:
    """★「与本地集群 rollout 一致」的实现基础：初波就是连续配额流的前 G0 局。

    本地集群本轮走 `biz.volume_quota`（连续派发）：逐关从 `(rotate_seed, it, stage)` 独立流
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
    blk = volume_block(args, est_samples_per_game=X20["est_samples_per_game"])
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
    from biz.course import build_pairs

    assert pairs_for(plan, 4) == build_pairs(args, 4, 4242)


def test_check_plan_against_args_catches_target_drift() -> None:
    """发布期自检：换一门课/改目标 ⇒ 计划重放对集与 args 重解不一致，拒发。"""
    args = _args(**X20)
    plan = build_plan(
        args,
        it=3,
        iters_total=6,
        rotate_seed=4242,
        volume=volume_block(args, est_samples_per_game=X20["est_samples_per_game"]),
        log=lambda _m: None,
    )
    check_plan_against_args(args, plan)  # 一致 ⇒ 静默通过

    with pytest.raises(ProtocolError, match="重放对集"):
        check_plan_against_args(_args(**{**X20, "target_transitions": 96000}), plan)
    # 关集漂移（args 比块少一关）现在在**更早**一步就被拦下（2026-10-27 逐轮采样子集：
    # 重解用计划块里的关集，于是「块 ⊄ args」是直接的子集断言，而不是算完两套对集再比）。
    # 判据（拒发 + 点出哪一关）与以前一致，只是文案从「重放对集」换成更具体的「不自洽」。
    with pytest.raises(ProtocolError, match="不自洽"):
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
                volume=volume_block(args, est_samples_per_game=X20["est_samples_per_game"]),
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
    from common.protocol import encode_opt_tar, encode_weights_json
    from remote.artifacts import sha256_bytes

    args = _args(**X20)
    plan = build_plan(
        args,
        it=0,
        iters_total=2,
        rotate_seed=4242,
        volume=volume_block(args, est_samples_per_game=X20["est_samples_per_game"]),
        log=lambda _m: None,
    )
    manifest = normalize_manifest(
        {
            "proto": 1,
            "kind": "run",
            "runId": "run-volume",
            # ★ 真实契约：`manifest.it == plan.start_it + 1`（plan.start_it = 0 ⇒ 本 job 自己那一轮 = 1）
            "it": 1,
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


# ────────────────────── 5. 逐轮采样子集（窗口）的接线点（2026-10-27） ──────────────────────


def test_volume_block_window_override_matches_plan_side() -> None:
    """窗口交给 `volume_block` ⇒ 块里的 stages/配额/分关局数**全按窗口**反解。

    这是评审 A1 的修法点：轮换**不能**在 `worker/plan.py:pairs_for` 里改（那行是「老五字段
    回退」分支，当前分关形态根本不经过它）。唯一的缝是块构造处 —— 块自动按 `len(stages)`
    派生 `per_stage_quota` / `games_per_stage`，所以只要 stages 是窗口，三者天然自洽。
    """
    args = SimpleNamespace(target_transitions=1200, stages="0-34", max_games_per_stage=0)
    window = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

    block = volume_block(
        args, est_samples_per_game=200, ests_by_stage={s: 200 for s in window}, stages_override=window
    )
    assert block is not None
    assert block["stages"] == window
    assert block["per_stage_quota"] == target_per_stage(1200, len(window))
    assert sorted(block["games_per_stage_by_stage"]) == window
    # 计划侧的形状校验（同源等式）必须认这个块
    assert validate_volume_block(block)["per_stage_quota"] == target_per_stage(1200, len(window))


def test_volume_block_window_must_be_a_subset() -> None:
    """窗口里打错一个关号 ⇒ 响亮 `ValueError`（静默采纳 = 采一关不存在的关，局局零样本）。"""
    args = SimpleNamespace(target_transitions=1200, stages="0-34", max_games_per_stage=0)
    with pytest.raises(ValueError, match="不在 --stages"):
        volume_block(args, est_samples_per_game=0, stages_override=[99])
    with pytest.raises(ValueError, match="不能为空"):
        volume_block(args, est_samples_per_game=0, stages_override=[])


def _windowed_plan() -> tuple[SimpleNamespace, dict, list[int]]:
    """窗口计划（4 关的课、每轮 2 关）+ 它的窗口。"""
    from worker.plan import build_plan as _build_plan
    from worker.stage_rotation import window_from_args
    from worker.volume_waves import parse_stages_arg

    args = _args(**{**X20, "rotation_mode": "even", "rotation_per_it": 2, "rotation_pinned": ""})
    args.rotate_seed = 4242
    window = window_from_args(args, 1, parse_stages_arg(str(args.stages)), rotate_seed=4242)
    assert len(window) == 2
    block = volume_block(
        args,
        est_samples_per_game=X20["est_samples_per_game"],
        ests_by_stage={s: X20["est_samples_per_game"] for s in window},
        stages_override=window,
    )
    assert block is not None
    plan = _build_plan(
        args, it=0, iters_total=1, rotate_seed=4242, volume=block, log=lambda _m: None
    )
    return args, plan, window


def test_check_plan_against_args_accepts_a_rotated_window() -> None:
    """★ 窗口计划必须过发布期自检（实测的坑：自检按 `args.stages` **全集**重解）。

    旧形状：`pairs_for` 用块里的窗口（12 关）重放，`volume_pairs_from_args` 却按 `--stages`
    全集（35 关）重解 ⇒ `mine != real` ⇒ **每份窗口计划在发布期被拒**（而且文䈈是「重放与
    args 不一致」，指向完全错误的方向）。修法 = 重解用**计划块里的关集**（块是权威）。
    """
    args, plan, window = _windowed_plan()
    check_plan_against_args(args, plan)  # 一致 ⇒ 静默通过
    assert {s for s, _ in pairs_for(plan, 1)} == set(window)


def test_check_plan_against_args_rejects_a_window_outside_stages() -> None:
    """块里的关集不是 `args.stages` 的子集 ⇒ ProtocolError（不是静默采一个不存在的关）。"""
    args, plan, window = _windowed_plan()
    plan["volume"]["stages"] = [*window[:1], 99]
    with pytest.raises(ProtocolError, match="不自洽"):
        check_plan_against_args(args, plan)
