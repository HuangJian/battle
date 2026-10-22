"""tests/worker/test_plan.py —— 半离线计划（`biz/plan.py`）：云上是**重放**而不是重新发明。

plan/remote-wire-remediation 的 M3 记了「命令拼装只有 `build_rollout_cmd` 一份，节点重算
等于把这份知识复制到协议里」。整段自主（kind=run）面对同一个张力：云机必须能自己决定
「下一轮跑哪些局、argv 长什么样」，但又不能把 hub 的逻辑抄一份（抄了就漂移）。

本文件钉的就是这份**重放契约**：

  * 对集：`pairs_for`（`build_pairs` 纯函数 + 计划里的 pair_args）必须与真 `args` 逐位一致，
    包括 curriculum / rotate / seed-rotate 三种模式（`PAIR_ARG_FIELDS` 漏字段 = 计划自检
    在**发布期**就红）；
  * argv：模板 + `retarget_argv` 必须覆盖所有逐局变化的 flag（含自定义关的 `--stage-json`
    增/删/改），且在模板自身是**恒等**（发布期自检）；
  * 指纹：`pairs_fp` / `argv_fp` 两边算同一个数（交接时节点复算，不符即拒收、一局不跑）。
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

from biz.course import build_pairs
from common.protocol import RUN_MAX_ITERS_HARD_CAP, ProtocolError
from worker.plan import (
    argv_fp,
    build_plan,
    check_plan_against_args,
    dump_plan,
    iter_spec,
    pairs_for,
    pairs_fp,
    plan_pairs_fp,
    planned_iters,
    retarget_argv,
    validate_plan,
)


def _args(**over: object) -> SimpleNamespace:
    """最小 args：默认 rotate 模式（`--rotate-stages`），可覆盖成 curriculum / 固定 seeds。"""
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": "0-3",
        "seed_rotate": 0,
        "seeds": "1-2",
        "total_stages": 4,
        # build_rollout_cmd 需要（argv 模板走的是真函数）
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ────────────────────────── 对集：纯函数重放 ──────────────────────────


def test_pairs_replay_matches_real_build_pairs_rotate() -> None:
    """rotate 模式：计划重放 == 真 args 的 build_pairs（逐位）。"""
    args = _args()
    plan = build_plan(args, it=3, iters_total=9, rotate_seed=4242, log=lambda _m: None)
    for it in planned_iters(plan):
        assert pairs_for(plan, it) == build_pairs(args, it, 4242)


def test_pairs_replay_matches_real_build_pairs_curriculum() -> None:
    """课程模式：激活窗口随 it 扩展 —— `curriculum_*` 少一个字段就会被抓（这是最险的模式）。"""
    args = _args(curriculum_stages="0-5", rotate_stages=0, seeds_per_stage=1, curriculum_every=2)
    plan = build_plan(args, it=1, iters_total=7, rotate_seed=99, log=lambda _m: None)
    assert [len(pairs_for(plan, it)) for it in planned_iters(plan)] == [
        len(build_pairs(args, it, 99)) for it in planned_iters(plan)
    ]
    for it in planned_iters(plan):
        assert pairs_for(plan, it) == build_pairs(args, it, 99)


def test_pairs_replay_matches_real_build_pairs_seed_rotate() -> None:
    """seed 轮转模式（`--seed-rotate N`）同样必须逐位重放。"""
    args = _args(seed_rotate=3, rotate_stages=0)
    plan = build_plan(args, it=2, iters_total=4, rotate_seed=7, log=lambda _m: None)
    for it in planned_iters(plan):
        assert pairs_for(plan, it) == build_pairs(args, it, 7)


def test_plan_self_check_catches_missing_pair_field() -> None:
    """`PAIR_ARG_FIELDS` 漏字段 = 云机算出的语料与 hub 不同——必须在**发布期**红。"""
    args = _args(curriculum_stages="0-5", rotate_stages=0)
    plan = build_plan(args, it=1, iters_total=5, rotate_seed=3, log=lambda _m: None)
    plan["pair_args"].pop("curriculum_stages", None)  # 模拟漏字段 / 计划被改
    with pytest.raises(ProtocolError) as ei:
        check_plan_against_args(args, plan)
    assert "自检失败" in str(ei.value)


def test_plan_fp_is_stable_and_order_sensitive() -> None:
    """`pairs_fp`：同计划两次调用同值；轮次顺序不同则不同（不许把轮序当无序集合）。"""
    args = _args()
    plan = build_plan(args, it=1, iters_total=4, rotate_seed=11, log=lambda _m: None)
    assert plan_pairs_fp(plan) == plan_pairs_fp(dict(plan))
    assert plan["pairs_fp"] == plan_pairs_fp(plan)
    shuffled = dict(plan)
    shuffled["end_it"] = plan["end_it"]
    h = pairs_fp(pairs_for(plan, 2)) + pairs_fp(pairs_for(plan, 3))
    h2 = pairs_fp(pairs_for(plan, 3)) + pairs_fp(pairs_for(plan, 2))
    assert h != h2  # 顺序敏感的前提（拼接后再 hash）


# ────────────────────────── argv：模板重定向 ──────────────────────────

_TMPL = [
    "tools/sim/export-rl-rollout.ts",
    "--weights",
    "init_weights.json",
    "--out",
    "w0",
    "--stages",
    "3",
    "--seeds",
    "123",
    "--max-ticks",
    "700",
    "--difficulty",
    "hard",
    "--wver",
    "aa",
    "--node-label",
    "run",
]


def _val(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_retarget_replaces_dynamic_flags_only() -> None:
    """只动动态 flag：其余字节原样（导出器的其它参数不许被重排/丢字）。"""
    out = retarget_argv(_TMPL, stage=7, seed=999, out="w5", wver="bb")
    assert _val(out, "--stages") == "7"
    assert _val(out, "--seeds") == "999"
    assert _val(out, "--out") == "w5"
    assert _val(out, "--wver") == "bb"
    assert _val(out, "--max-ticks") == "700" and _val(out, "--difficulty") == "hard"
    assert out.count("tools/sim/export-rl-rollout.ts") == 1
    # 除四个动态值外逐字节相同（逐位对比：只允许那四个值变）
    expect = list(_TMPL)
    for flag, val in (("--stages", "7"), ("--seeds", "999"), ("--out", "w5"), ("--wver", "bb")):
        expect[expect.index(flag) + 1] = val
    assert out == expect


def test_retarget_is_identity_for_template_values() -> None:
    """模板自身的值重定向一次 = 恒等（发布期自检用的就是这条性质）。"""
    out = retarget_argv(list(_TMPL), stage=3, seed=123, out="w0", wver="aa")
    assert out == _TMPL


def test_retarget_adds_missing_dynamic_flags() -> None:
    """模板缺 `--wver`（hub 侧占位）→ 追加，而不是静默丢掉这一轮的权重指纹。"""
    tmpl = [t for t in _TMPL if t != "--wver"]
    tmpl = [t for t in tmpl if t != "aa"]
    out = retarget_argv(tmpl, stage=1, seed=2, out="w9", wver="cc")
    assert _val(out, "--wver") == "cc"
    assert _val(out, "--seeds") == "2"


def test_retarget_stage_json_add_replace_remove() -> None:
    """`--stage-json`：自定义关要带上；换到非自定义关必须**删掉**（留着 = 拿错关卡的 JSON）。"""
    tmpl = [*_TMPL, "--stage-json", '{"a":1}']
    # 1) 换一个自定义关：值被替换
    out = retarget_argv(tmpl, stage=2001, seed=5, out="w1", wver="d", stage_json='{"a":2}')
    assert _val(out, "--stage-json") == '{"a":2}'
    assert out.count("--stage-json") == 1
    # 2) 换到非自定义关（stage_json 为空）：整对删除
    out2 = retarget_argv(tmpl, stage=3, seed=5, out="w1", wver="d", stage_json="")
    assert "--stage-json" not in out2 and '{"a":1}' not in out2
    # 3) 目标关有自定义 JSON 而模板没有：追加
    out3 = retarget_argv(list(_TMPL), stage=2002, seed=5, out="w1", wver="d", stage_json='{"b":1}')
    assert _val(out3, "--stage-json") == '{"b":1}'


def test_argv_template_covers_every_game_and_fp_matches() -> None:
    """模板逐局一条 + `argv_fp` 与两侧算法一致；`iter_spec` 复用模板（局数/关卡/种子对得上）。"""
    args = _args()
    plan = build_plan(args, it=1, iters_total=3, rotate_seed=5, log=lambda _m: None)
    tmpl = plan["argv_template"]
    tmpl_it = plan["start_it"] + 1
    pairs = pairs_for(plan, tmpl_it)
    assert len(tmpl) == len(pairs)
    assert plan["argv_fp"] == argv_fp(tmpl)
    for row, (stage, seed) in zip(tmpl, pairs, strict=True):
        assert _val(row, "--stages") == str(stage) and _val(row, "--seeds") == str(seed)

    # 第 2 轮（往后一格）：模板重定向 → 关卡/种子/输出目录都是该轮自己的
    pairs2 = pairs_for(plan, tmpl_it + 1)
    spec = iter_spec(plan, tmpl_it + 1, pairs2, wver="W" * 64, course=None)
    assert len(spec["argv"]) == len(pairs2)
    assert spec["wver"] == "W" * 64
    for row, (stage, seed) in zip(spec["argv"], pairs2, strict=True):
        assert _val(row, "--stages") == str(stage)
        assert _val(row, "--seeds") == str(seed)
        assert _val(row, "--wver") == "W" * 64
        assert _val(row, "--out").startswith("w")


# ────────────────────────── 计划形状 ──────────────────────────


def test_plan_shape_and_clamps() -> None:
    """区间/上限：`max_iters` 与课程预算取交集，硬上界钳制，无轮次可跑则拒发。"""
    args = _args()
    plan = build_plan(args, it=2, iters_total=10, rotate_seed=1, max_iters=3, log=lambda _m: None)
    assert (plan["start_it"], plan["end_it"]) == (2, 5)
    assert planned_iters(plan) == [3, 4, 5]
    full = build_plan(args, it=2, iters_total=10, rotate_seed=1, log=lambda _m: None)
    assert full["end_it"] == 10
    over = build_plan(args, it=2, iters_total=10, rotate_seed=1, max_iters=999, log=lambda _m: None)
    assert over["end_it"] == 10  # 课程预算是天花板
    with pytest.raises(ProtocolError) as ei:
        build_plan(args, it=10, iters_total=10, rotate_seed=1, log=lambda _m: None)
    assert "没有可自主跑的轮次" in str(ei.value)


def test_infinite_course_plan_is_an_open_ended_segment() -> None:
    """J1（`plan/offline-open-ended-iters-and-loud-export` §3.1）：`iters_total<=0` = **无限**。

    旧代码把「无限」硬塞进「有限预算」的模子里（`end_it = iters_total` ⇒ `end_it=0` ⇒
    “没有可自主跑的轮次” 拒发）⇒「离线包支持 iters 0」在**协议层不可表达**（用户
    2026-10-14 报障的第一条）。新口径：本段是**开区间段**，终点由**段长**定，
    `iters_total` 保持 `0` 原值（否则云机会把段末当成课末）。
    """
    args = _args()
    plan = build_plan(args, it=0, iters_total=0, rotate_seed=1, log=lambda _m: None)
    assert plan["open_ended"] is True
    assert plan["iters_total"] == 0
    assert plan["end_it"] == RUN_MAX_ITERS_HARD_CAP
    assert len(planned_iters(plan)) == RUN_MAX_ITERS_HARD_CAP
    assert validate_plan(json.loads(json.dumps(plan)))["open_ended"] is True
    # 段长可降（`--run-iters 10`）：仍是开区间，终点 = it + 10
    short = build_plan(args, it=7, iters_total=0, rotate_seed=1, max_iters=10, log=lambda _m: None)
    assert (short["start_it"], short["end_it"], short["open_ended"]) == (7, 17, True)
    # 段长 > 硬上界仍拒（这条闸两个分支共用：抬阀值不是表达无限轮的手法）
    with pytest.raises(ProtocolError, match="硬上界"):
        build_plan(
            args,
            it=0,
            iters_total=0,
            rotate_seed=1,
            max_iters=RUN_MAX_ITERS_HARD_CAP + 1,
            log=lambda _m: None,
        )
    # 负数预算与 0 同义（“无限”的另一种写法）
    neg = build_plan(args, it=3, iters_total=-5, rotate_seed=1, log=lambda _m: None)
    assert (neg["open_ended"], neg["end_it"], neg["iters_total"]) == (True, 3 + RUN_MAX_ITERS_HARD_CAP, -5)


def test_run_max_iters_lowers_the_segment_cap_only() -> None:
    """D1（plan §3.1/§3.4）：`--run-max-iters` = 段长上界的**再降档**（只能降不能抬）。

    为什么必须真能降：硬上界 500 ≈ 3 小时 / 96 核 —— 而「这一趟只跑 50 轮就交回来」是导出
    时的人的决定（节点会话预算是有限的）。它同时是控制台静态预判读的那个常量：两边
    漂开就是「预判说能导、真闸拒发」。
    """
    args = _args()
    # 开区间段的缺省段长跟着降档走（否则「降了档」只是拒发线更低，而段还是一样的长）
    plan = build_plan(
        args, it=4, iters_total=0, rotate_seed=1, max_segment_iters=50, log=lambda _m: None
    )
    assert (plan["end_it"], plan["open_ended"]) == (54, True)
    # 真闸也读同一个生效值：51 轮在降档后拒（在硬上界下本可以过）
    with pytest.raises(ProtocolError, match="--run-max-iters 已降档"):
        build_plan(
            args,
            it=4,
            iters_total=0,
            rotate_seed=1,
            max_iters=51,
            max_segment_iters=50,
            log=lambda _m: None,
        )
    # **抬不动**：盯上界的旋钮给一个更大的值也只会被硬上界钳住（抬 = 拆闸，已否决）
    clipped = build_plan(
        args,
        it=0,
        iters_total=0,
        rotate_seed=1,
        max_segment_iters=RUN_MAX_ITERS_HARD_CAP + 500,
        log=lambda _m: None,
    )
    assert clipped["end_it"] == RUN_MAX_ITERS_HARD_CAP
    # 有限分支同样跟降档走：算术仍按 `max_iters` 与预算取交集（降档只管「上界」，不重定向
    # 终点），而 `it + 8 = 9` 越过了降档后的 3 ⇒ 拒（旧行为：8 ≤ 500 本可以过）
    with pytest.raises(ProtocolError, match="--run-max-iters 已降档"):
        build_plan(
            args,
            it=1,
            iters_total=10,
            rotate_seed=1,
            max_iters=8,
            max_segment_iters=3,
            log=lambda _m: None,
        )


def test_finite_course_plan_is_unchanged_and_not_open_ended() -> None:
    """有限模式**逐字节不变**（J1 是分支加宽，不是重写）：同参两遍同字节 + 新键恒 `False`。"""
    args = _args()
    a = build_plan(args, it=2, iters_total=10, rotate_seed=1, max_iters=3, log=lambda _m: None)
    b = build_plan(args, it=2, iters_total=10, rotate_seed=1, max_iters=3, log=lambda _m: None)
    assert dump_plan(a) == dump_plan(b)
    assert a["open_ended"] is False
    assert (a["start_it"], a["end_it"], a["iters_total"]) == (2, 5, 10)
    # `max_iters=0` 在有限课仍然是「跑到课程末尾」
    whole = build_plan(args, it=2, iters_total=10, rotate_seed=1, log=lambda _m: None)
    assert (whole["end_it"], whole["open_ended"]) == (10, False)


def test_validate_plan_open_ended_exemption_is_tight() -> None:
    """P1-7（评审）：豁免面**只许刚好盖住“课程无限”**——否则它就是一道后门。

    v1 的设计是「`open_ended` 就不查预算」，于是 `open_ended=true ∧ iters_total=2000 ∧
    end_it=2500` 的（写错/伪造）计划能越过课程预算——而那正是这条校验存在的理由。
    """
    args = _args()
    finite = build_plan(args, it=1, iters_total=4, rotate_seed=1, log=lambda _m: None)
    # 旧计划（无 `open_ended`）照旧拒 —— 向后兼容的那一半
    legacy = {k: v for k, v in finite.items() if k != "open_ended"}
    with pytest.raises(ProtocolError, match="超出课程预算"):
        validate_plan({**legacy, "end_it": 99})
    # 伪造：`open_ended=true` 但课程有预算 ⇒ 拒
    with pytest.raises(ProtocolError, match="open_ended=true 但课程预算"):
        validate_plan({**finite, "open_ended": True, "end_it": 99})
    # 类型错也拒（未知字段是忽略，已声明字段就得合法）
    with pytest.raises(ProtocolError, match="open_ended 必须是布尔"):
        validate_plan({**finite, "open_ended": "yes"})
    # 真开区间段（无限课）照旧过，且预算仍不得被越过（段长那条闸另管）
    inf = build_plan(args, it=0, iters_total=0, rotate_seed=1, log=lambda _m: None)
    validate_plan({**inf, "end_it": 100})
    with pytest.raises(ProtocolError, match="硬上界"):
        validate_plan({**inf, "end_it": RUN_MAX_ITERS_HARD_CAP + 1})


def test_validate_plan_rejects_malformed() -> None:
    """畸形计划一律拒收（不半信半疑地跑）：proto/区间/预算越界/argv 形状。"""
    args = _args()
    plan = build_plan(args, it=1, iters_total=4, rotate_seed=1, log=lambda _m: None)
    assert validate_plan(json.loads(json.dumps(plan)))["end_it"] == 4
    for patch, kw in (
        ({"proto": 99}, "proto"),
        ({"end_it": 1}, "区间非法"),
        ({"end_it": 99}, "超出课程预算"),
        ({"argv_template": []}, "argv_template"),
        ({"game_timeout_sec": -1}, "不得为负"),
    ):
        bad = {**plan, **patch}
        with pytest.raises(ProtocolError) as ei:
            validate_plan(bad)
        assert kw in str(ei.value), (kw, str(ei.value))
    with pytest.raises(ProtocolError):
        validate_plan([1, 2, 3])  # type: ignore[arg-type]


# ────────────────────── 自主机首批铺满（2026-10-22 事故回归）──────────────────────


def _widened_plan() -> dict:
    """y30-stack it156 的**逐字**形状：est 628/644/616/637 ⇒ 9 局/关，本机 94 槽 ⇒ 加宽到 94 局。

    现场（`[14:34:16]` 日志）：`widen_by_width` 只改了 `games_per_stage_by_stage`，
    `iter_spec` 的同源校验按 est 反解出 9 ⇒ `ProtocolError` 整段死（345 轮一局没跑）。
    """
    from worker.volume_alloc import widen_by_width

    ests = {2000: 628, 2001: 644, 2002: 616, 2003: 637}
    vol: dict = {
        "target_transitions": 24576,
        "stages": [2000, 2001, 2002, 2003],
        "per_stage_quota": 6144,  # ceil(24576/4)
        "est_s_by_stage": ests,
        "est_hi_factor": 1.15,
        "games_per_stage_by_stage": {s: 9 for s in ests},
        "topup": {
            "enabled": True,
            "max_batches": 3,
            "last_lo_factor": 0.85,
            "max_games_per_stage": 0,
        },
    }
    wide = widen_by_width(vol["games_per_stage_by_stage"], 94)
    assert wide == {2000: 24, 2001: 24, 2002: 23, 2003: 23}, wide
    return {
        "argv_template": [list(_TMPL)],
        "workers": 94,
        "game_timeout_sec": 0.0,
        "rotate_seed": 20270208,
        "volume": {**vol, "games_per_stage_by_stage": wide, "widen_width": 94},
    }


def test_iter_spec_accepts_width_widened_volume_block() -> None:
    """加宽后的块必须能拼成 spec：`games` 与 `widen_width` 一起声明 ⇒ 同源仍成立。"""
    plan = _widened_plan()
    pairs = pairs_for(plan, 156)
    assert len(pairs) == 94, "首批 = 加宽后的局数（逐关满额即停，多的候选不跑）"
    spec = iter_spec(plan, 156, pairs, wver="W" * 64, course=None)
    assert spec["volume"]["games_per_stage_by_stage"] == {2000: 24, 2001: 24, 2002: 23, 2003: 23}
    assert spec["volume"]["widen_width"] == 94
    assert len(spec["argv"]) == sum(spec["volume"]["games_per_stage_by_stage"].values())

    # 反例（同一个块、只在拼 spec 前把宽度声明抹掉）：必须响亮失败 —— 「加宽」放行的前提
    # 是块里能复算出宽度，不是「谁都能把首批局数改大」。
    from worker.volume_alloc import validate_runtime_volume

    bare = {k: v for k, v in plan["volume"].items() if k != "widen_width"}
    with pytest.raises(ValueError, match="同源"):
        validate_runtime_volume({**bare, "it": 156, "rotate_seed": 20270208})
