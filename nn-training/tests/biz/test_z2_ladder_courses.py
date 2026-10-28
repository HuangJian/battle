"""z2 阶梯（docs/nn/training-stack.md §50）—— **同一份奖励，只加任务**。

这组数据文件的设计不变量只有一条：**六级之间唯一的自变量是任务**（敌数 × 命数 × 关集 ×
max_ticks）。任何一级偷偷改了奖励/价格，跨级读数就不可比，而这是 z0/z1 判死之后整个重设计
成立的前提。

这里钉住的是：
  · 六级奖励逐字相同（formula / params / terminal / scheme）；
  · 价格表引用**哪些**指标列 = 结构判据（不是措辞判据）：没有 cellsVisited 这类移动/探索项，
    基地护墙与 pace 在场（反苟），命价/血价在场（反莽）；
  · 任务侧递增（敌数 1→2→4→8→20→20、命 1→1→2→3→3→3、max_ticks 递增）；
  · 前五级 = 预注册的 4 张图（首敌 basic、无 water/ice）；第六级 = classic35 原题零覆盖；
  · 六级语料身份各不相同（§15.5：换任务 = 新实验）；
  · 权重链：r1 从 scratch-init（随机权重）起，rN 从 r(N-1) 的 `out` 起。
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from biz.corpus_fp import corpus_identity_fp
from biz.course_resolve import load_course
from biz.course_spec import CourseConfig, StageSpec
from biz.reward_library import parse_formula
from common.jsonc import load as load_jsonc

ROOT = Path(__file__).resolve().parents[2]
CURRICULA = ROOT / "curricula"

NAMES = [f"z2-r{i}" for i in range(1, 7)]

#: 预注册的阶梯（敌数, 命数, max_ticks）。r6 = 关卡文件原值（20/3/36000）。
LADDER = {
    "z2-r1": (1, 1, 6000),
    "z2-r2": (2, 1, 8000),
    "z2-r3": (4, 2, 10000),
    "z2-r4": (8, 3, 12000),
    "z2-r5": (20, 3, 20000),
    "z2-r6": (20, 3, 36000),
}

#: 预注册的 4 张图：Crossroads / Oasis / Steel Fortress / Brickworks（classic35 的 16/22/3/6）。
STAGE_SET = ("16,22,3,6",)

#: 奖励引用的指标列（结构判据：多一列 = 多一项定价，必须审）。
REWARD_METRICS = {
    "kills",
    "clearTick",
    "baseWallIntact",
    "playerDamageTaken",
    "playerDeaths",
    "ticks",
    "aimBricks",
}

#: 奖励引用的参数（价格表全文）。
REWARD_PARAMS = {
    "wKill",
    "wWin",
    "wBase",
    "wDmg",
    "wDeath",
    "wPace",
    "paceTicks",
    "wBrick",
    "brickCap",
}


def _raw(name: str) -> dict:
    return load_jsonc(str(CURRICULA / f"{name}.jsonc"))


def _stage_specs(c: CourseConfig) -> list[StageSpec]:
    """`stages` 是 `str | list[StageSpec]`；本阶梯全部是挑关后的内联列表。"""
    assert isinstance(c.stages, list)
    return c.stages


def _stage_counts(c: CourseConfig) -> list[int]:
    """逐关 `count`（`stage_json` 是下发远端的纯 JSON；None 只可能出现在找不到的 id）。"""
    out: list[int] = []
    for sid in c.stage_ids:
        payload = c.stage_json(sid)
        assert payload is not None
        out.append(int(json.loads(payload)["count"]))
    return out


@pytest.mark.parametrize("name", NAMES)
def test_ladders_loads_with_its_declared_task(name: str) -> None:
    c = load_course(name)
    enemies, lives, max_ticks = LADDER[name]
    assert c.player.lives == lives
    assert c.max_ticks == max_ticks
    assert sorted(set(_stage_counts(c))) == [enemies]


def test_enemies_and_lives_only_grow() -> None:
    """阶梯的方向：敌数与命数都不许回退（回退 = 换了一门课，不是同一级）。"""
    envs = [LADDER[n][:2] for n in NAMES]
    assert envs == sorted(envs)


def test_reward_is_identical_across_all_rungs() -> None:
    """唯一变量 = 任务：六级奖励逐字相同（含 terminal 与 scheme）。"""
    blocks = []
    for name in NAMES:
        r = load_course(name).reward
        blocks.append(
            json.dumps(
                {
                    "formula": r.formula,
                    "params": dict(sorted(r.params.items())),
                    "terminal": dict(sorted(r.terminal.items())),
                    "scheme": r.scheme,
                },
                sort_keys=True,
            )
        )
    assert len(set(blocks)) == 1


def test_reward_prices_defence_and_never_pays_for_movement() -> None:
    """价格表的结构：基地护墙 + pace + 命/血价在场；移动/探索类列一个都不许进（z0 的病根）。"""
    r = load_course("z2-r1").reward
    parsed = parse_formula(r.formula, r.params)
    assert parsed.metric_names == REWARD_METRICS
    assert parsed.param_names == REWARD_PARAMS


def test_first_five_rungs_share_the_preregistered_stage_set() -> None:
    """4 张预注册图，顺序即 id 2000..2003（挑关 = 位置串，不是 classic35 的关号）。"""
    for name in NAMES[:5]:
        c = load_course(name)
        assert c.stage_ids == [2000, 2001, 2002, 2003]
        assert [s.name for s in _stage_specs(c)] == [
            "Crossroads",
            "Oasis",
            "Steel Fortress",
            "Brickworks",
        ]
        assert _raw(name)["stage_select"] in STAGE_SET
        # 首敌必须是 basic（forces[0] == 'a'）：`enemies: N` 取 forces 前 N 个
        # ⇒ 1 敌的 r1 必须遇到最弱的那种，而不是 fast/power/armor。
        assert [s.forces[0] for s in _stage_specs(c)] == ["a"] * 4


def test_final_rung_is_the_untouched_classic35() -> None:
    """r6 = 原题：无覆盖键、35 关、20 敌、3 命、36000 拍。"""
    raw = _raw("z2-r6")
    for k in ("stage_select", "enemies", "lives", "star", "max_ticks", "stages", "player"):
        assert raw.get(k) is None
    c = load_course("z2-r6")
    assert c.stage_ids == list(range(2000, 2035))
    assert c.player.lives == 3 and c.max_ticks == 36000


def test_each_rung_is_a_distinct_corpus() -> None:
    """换任务 = 新语料（AGENTS §15.5）：六级身份指纹两两不同。"""
    fps = [corpus_identity_fp(load_course(n)) for n in NAMES]
    assert len(set(fps)) == len(NAMES)


def test_weights_chain_from_scratch_through_every_rung() -> None:
    """r1 随机起点；rN 的 bc = r(N-1) 的 out ⇒ 阶梯只能按序开，不能跳级。"""
    assert _raw("z2-r1")["bc"] == "nn-training/weights/z0-scratch/scratch-init.json"
    for prev, cur in pairwise(NAMES):
        assert _raw(cur)["bc"] == _raw(prev)["out"]
