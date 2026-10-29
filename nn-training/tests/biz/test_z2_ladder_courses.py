"""z2 阶梯（docs/nn/training-stack.md §50）—— **同一份奖励，只加任务**。

这组数据文件的设计不变量只有一条：**各级之间唯一的自变量是任务**（敌数 × 命数 × 关集 ×
max_ticks）。任何一级偷偷改了奖励/价格，跨级读数就不可比，而这是 z0/z1 判死之后整个重设计
成立的前提。

2026-10-29 的两处改判（用户拍板「r1 判负」）：
  · **r1 退出同价断言**：它停在 it33（it5/it10 是同一份 eval 读数、it15 起 clearRate 0%），
    价格表里**没有**移动项 ⇒ 与 r2–r6 不再是同一套价。判负腿保留在 `NAMES` 里受任务侧断言
    约束（敌数/命数/max_ticks 仍然要成立），但不参与「逐字相同」。
  · **`wWalk` 从 r2 起固化**：r1 在无移动项的价格下学成「站桩一格不动」（cellsVisited 1.00，
    80/80 局）；`z2-r1walk` 补上 `+wWalk*min(cellsVisited,20)` 后在同一任务上 cells 涨到 30±5、
    clearRate 98.8% ⇒ 「给移动付钱」不致病，**无封顶才致病**（z0 的"逛"）。封顶 20 是本条的
    结构性判据：满额 +20 = 1.33 个击杀 = 清关奖的 1/3，走得再远同价。

这里钉住的是：
  · **r2–r6** 奖励逐字相同（formula / params / terminal / scheme）；
  · 价格表引用**哪些**指标列 = 结构判据（不是措辞判据）：移动项必须在场**且带封顶**
    （`walkCap` ≤ 20、`wWalk*walkCap` ≤ 清关奖/3），基地护墙与 pace 在场（反苟），命价/血价在场（反莽）；
  · 任务侧递增（敌数 1→2→4→8→20→20、命 1→1→2→3→3→3、max_ticks 递增）；
  · 前五级 = 预注册的 4 张图（首敌 basic、无 water/ice）；第六级 = classic35 原题零覆盖；
  · 六级语料身份各不相同（§15.5：换任务 = 新实验）；
  · 权重链：r1 从 scratch-init（随机权重）起，**r2 从 `z2-r1walk` 的冻结归档起**（r1 判负），
    rN（≥3）从 r(N-1) 的 `out` 起；谁都不许指 `tmp/*/weights.json` 这类活指针。
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from biz.corpus_fp import corpus_identity_fp
from biz.course_resolve import load_course
from biz.course_spec import CourseConfig, StageSpec
from biz.reward_library import METRIC_INDEX, METRICS_DIM, build_reward_fn, parse_formula
from common.jsonc import load as load_jsonc

ROOT = Path(__file__).resolve().parents[2]
CURRICULA = ROOT / "curricula"

NAMES = [f"z2-r{i}" for i in range(1, 7)]

#: 判负腿（2026-10-29）：r1 停在 it33（it5/it10 同一份 eval 读数、it15 起 clearRate 0%），
#: 价格表里也没有移动项 ⇒ 它不是 r2–r6 那套价。任务侧断言仍对它成立，同价断言不。
RETIRED = frozenset({"z2-r1"})
#: 现役阶梯（r2–r6）：奖励必须逐字相同。
LIVE = [n for n in NAMES if n not in RETIRED]

#: r2 的冻结起点：`z2-r1walk` it170 的归档（r1 判负后它才是事实上的 r1）。
#: 判据是**形态**（`nn-training/weights/<课>/<课>.itN.<YYYYMMDD-HHMMSS>.json`），不断言文件在盘上
#: ——`weights/` 未入库，换机器跑测试时归档可以不在。
FROZEN_START = re.compile(
    r"^nn-training/weights/z2-r1walk/z2-r1walk\.it\d+\.\d{8}-\d{6}\.json$"
)

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
    # 2026-10-29：`aimHits`（命中敌人）—— 把「打中」从稀疏（只有击杀）变密集。
    "aimHits",
    "clearTick",
    "baseWallIntact",
    "playerDamageTaken",
    "playerDeaths",
    "ticks",
    "aimBricks",
    # 2026-10-29：`cellsVisited`（走过的不同格数）—— r1 站桩的病根就是这一格没价。
    # 它**必须**配 `walkCap` 封顶（见 test_movement_is_paid_but_capped）。
    "cellsVisited",
}

#: 奖励引用的参数（价格表全文）。
REWARD_PARAMS = {
    "wKill",
    "wHit",
    "wWin",
    "wBase",
    "wDmg",
    "wDeath",
    "wPace",
    "paceTicks",
    "paceWindow",
    "paceCap",
    "wBrick",
    "brickCap",
    "wWalk",
    "walkCap",
}


def _raw(name: str) -> dict:
    return load_jsonc(str(CURRICULA / f"{name}.jsonc"))


def _metrics_row(**kw: float) -> np.ndarray:
    m = np.zeros((1, METRICS_DIM), dtype=np.float64)
    for k, v in kw.items():
        m[0, METRIC_INDEX[k]] = v
    return m


def _pay(*, outcome: str, **kw: float) -> float:
    """一局的净奖励 Σr = Φ 逐行差分 + terminal —— 与训练循环同一条路径（`scheme: toy`）。

    `baseWallIntact` 是**块数**（0–8，见 `reward_validation` 的 range），不是比例。
    价格表用 **r2**（现役阶梯的价，含 `wWalk`；r1 已判负）。
    """
    fn = build_reward_fn(load_course("z2-r2").reward_spec())
    start = _metrics_row(baseWallIntact=8.0, clearTick=-1.0)
    end = _metrics_row(**kw)
    return float(fn(np.concatenate([start, end]), outcome, 0.0, 1).sum())


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


def test_reward_is_identical_across_all_live_rungs() -> None:
    """唯一变量 = 任务：**r2–r6** 奖励逐字相同（含 terminal 与 scheme）。

    r1 是判负腿（见模块 docstring），它的价格表没有 `wWalk`，不参与这条断言。
    """
    blocks = []
    for name in LIVE:
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


def test_movement_is_paid_but_capped() -> None:
    """价格表的结构（2026-10-29 改判后的版本）：

    · 移动项**必须在场**（cellsVisited 有价）—— r1 的病根是它没价；
    · 但**必须封顶**：`walkCap ≤ 20` 且满额 ≤ 清关奖的 1/3 ⇒ 走得再远不能比一杀更值钱，
      否则就是 z0 的"逛"（无封顶探索款）；
    · 基地护墙 + pace + 命/血价仍须在场（反苟 / 反莽）。
    """
    r = load_course("z2-r2").reward
    parsed = parse_formula(r.formula, r.params)
    assert parsed.metric_names == REWARD_METRICS
    assert parsed.param_names == REWARD_PARAMS
    # 封顶的结构判据（不是措辞判据）：单价 < 一杀，满额 ≤ 清关的 1/3。
    assert r.params["walkCap"] <= 20
    assert r.params["wWalk"] < r.params["wKill"]
    assert r.params["wWalk"] * r.params["walkCap"] <= r.params["wWin"] / 3


def test_timeout_is_the_priciest_loss() -> None:
    """**更要避免苟**：超时（敌人还活着 = 你从没把仗打完）必须比其它输法贵。

    命尽/基毁都是「被打死」，已经被命价与墙价计过；只有超时是「选择不接触」
    —— z0/z1 冻住/贴墙/站桩的病根就在这一格的价格。
    """
    for name in NAMES:
        t = load_course(name).reward.terminal
        assert t["timeout"] < t["lives_exhausted"] == t["base_destroyed"] < t["stage_clear"]


def test_price_table_keeps_progress_rewarding_and_turtling_the_worst() -> None:
    """价格表的排序不变量（§50.3 的算例；走真 RewardFn，不是手算）：

    · 赢 ≫ 一切输法；
    · 任何**带击杀**的输法 > 苟到超时（进度必须有梯度）；
    · 苟（超时满墙）**贵于**莽（1 杀后死）—— 用户口径「避免莽，更要避免苟」；
    · 走满 20 格 > 站桩（净 +19 分），但 60 格与 20 格**同价**（封顶生效）。
    """
    win = _pay(
        outcome="stage_clear", kills=1, clearTick=900, ticks=900,
        baseWallIntact=8, playerDamageTaken=30, aimBricks=5, cellsVisited=12,
    )
    turtle = _pay(outcome="timeout", kills=0, clearTick=-1, ticks=6000, baseWallIntact=8)
    reckless = _pay(
        outcome="lives_exhausted", kills=1, clearTick=-1, ticks=700,
        baseWallIntact=4, playerDamageTaken=200, playerDeaths=1, aimBricks=2,
    )
    no_kill_death = _pay(
        outcome="lives_exhausted", kills=0, clearTick=-1, ticks=900,
        baseWallIntact=6, playerDamageTaken=200, playerDeaths=1,
    )
    base_lost_with_kills = _pay(
        outcome="base_destroyed", kills=3, clearTick=-1, ticks=3000,
        baseWallIntact=0, playerDamageTaken=150, aimBricks=6,
    )

    assert win > 60
    for loss in (turtle, reckless, no_kill_death, base_lost_with_kills):
        assert win - loss > 100
    assert turtle < reckless  # 苟比莽更贵
    assert base_lost_with_kills > turtle  # 带击杀的输法优于苟
    assert reckless > no_kill_death  # 击杀让同样的死更值钱

    # 移动项：站桩 vs 走满 20 格 = 差 19 分（清关奖的 32% ⇒ 平局一定翻）；
    # 60 格与 20 格同价 —— 封顶就是「绕圈刷格子」的止损线。
    walk_win = _pay(
        outcome="stage_clear", kills=1, clearTick=900, ticks=900,
        baseWallIntact=8, playerDamageTaken=30, aimBricks=5, cellsVisited=20,
    )
    still_win = _pay(
        outcome="stage_clear", kills=1, clearTick=900, ticks=900,
        baseWallIntact=8, playerDamageTaken=30, aimBricks=5, cellsVisited=1,
    )
    far_win = _pay(
        outcome="stage_clear", kills=1, clearTick=900, ticks=900,
        baseWallIntact=8, playerDamageTaken=30, aimBricks=5, cellsVisited=60,
    )
    assert walk_win - still_win == pytest.approx(19.0, abs=0.01)
    assert far_win == pytest.approx(walk_win, abs=0.01)


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
    """r1 随机起点；r2 从 **r1walk 的冻结归档**起（r1 判负）；rN≥3 从 r(N-1) 的 out 起。

    判据的重点是「起点必须是冻结产物」：`tmp/*/weights.json` 是活动输出、每轮被覆盖，
    指向它的腿事后无法回答「从哪一版开始」（2026-10-29 r1 现场就是这么丢的）。
    """
    assert _raw("z2-r1")["bc"] == "nn-training/weights/z0-scratch/scratch-init.json"
    r2_bc = _raw("z2-r2")["bc"]
    assert FROZEN_START.match(r2_bc), f"r2 起点必须是 r1walk 的冻结归档，实际 {r2_bc!r}"
    assert not r2_bc.startswith("tmp/")
    for prev, cur in pairwise(NAMES[1:]):
        assert _raw(cur)["bc"] == _raw(prev)["out"]
