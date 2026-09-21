"""§5 缰绳初值 + 干烧熔断（plan/accident.plan.md §5，2026-09-21）。

事故：C 双臂从收敛权重以 `kk(1)=1` 满 kickstart 复活，锚主导更新连烧 30 轮（it1 kl=0.90），
两臂从 ~34 洗回 ~50，锚释放后平摆、又跑 140 轮没爬出来 ⇒ 两臂 ×12h 只换来「无结论」。
过程熔断全程没响（kl/ent 正常），坏的是**结果**。

本文件钉四件事：
  ① 课程键 `kickstart_init` 走**四条**管道（漏一条就静默失效，`ent_break` 前科）：
     `flat_overrides` 异名映射到 `args.kickstart_kl` / `hot_reload.RESTART_ONLY_FIELDS` /
     **不**进 `corpus_identity_fp` / 初值接到 args 上而不动 `coef_active` 归零链；
  ② 公式写死 `kk(it) = init × decay^(it-1)`（run 原点，resume 不复位）；
  ③ 缺席 = 现状逐字节不变（指纹不变、args 不变）；
  ④ 干烧熔断：it0 行当基线（bc 口径）、连续 `points` 个评估点低于基线 `margin_pp` ⇒ 停腿；
     计数与判定依据落账（可回放）；缰绳关着时零行为。
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

from rl.config import CourseConfig, apply_course, corpus_identity_fp, course_cli_conflicts
from rl.hot_reload import RESTART_ONLY_FIELDS, apply_hot_fields, plan_reload
from rl.kickstart_burn import (
    BURN_MARGIN_PP,
    BURN_POINTS,
    baseline_reading,
    burn_overrides,
    burn_verdict,
)
from rl.loop_guards import TrainingGuards
from rl.loop_steps import kickstart_coef


def _course(**kw) -> CourseConfig:
    """最小课程：只带 kickstart 相关键（其余走缺省，不碰别的管道）。"""
    return CourseConfig(name="t5-kk", mode="per-tick", **kw)


def _args(**kw):
    """近似 argparse 命名空间：kickstart 腿的缺省值 = CLI 缺省（kickstart_kl=1.0）。"""
    d = {
        "kickstart_ref": True,
        "kickstart_kl": 1.0,
        "kickstart_decay": 0.5,
        "warmup_iters": 0,
        "epochs": 4,
        "mode": "per-tick",
        "seed": 7,
        "out": "tmp/out.json",
        "traj": "tmp/traj",
    }
    d.update(kw)
    return SimpleNamespace(**d)


# ────────────────────────── ① 四条管道 ──────────────────────────


def test_kickstart_init_maps_to_argparse_dest_kickstart_kl() -> None:
    """管道 ①+④：课程键 → args.kickstart_kl（异名映射），且公式吃到它。"""
    c = _course(kickstart_ref=True, kickstart_init=0.25)
    assert c.flat_overrides()["kickstart_kl"] == 0.25, "漏映射 = 静默失效（ent_break 前科）"

    args = _args()
    apply_course(args, c)
    assert args.kickstart_kl == 0.25


def test_kickstart_init_formula_is_run_origin_and_keeps_zeroing_chain() -> None:
    """管道 ④：`kk(it)=init×decay^(it-1)`；衰减到阈值以下仍精确归零（coef_active 链不动）。"""
    args = _args()
    apply_course(args, _course(kickstart_ref=True, kickstart_init=0.8))

    assert kickstart_coef(args, 1) == pytest.approx(0.8)
    assert kickstart_coef(args, 2) == pytest.approx(0.4)
    assert kickstart_coef(args, 3) == pytest.approx(0.2)
    # resume 到 it100 也不复位（run 原点：loop_steps.kickstart_coef 恒用 start=1）
    assert kickstart_coef(args, 100) == pytest.approx(0.0), "几何衰减末尾必须精确归零"


def test_kickstart_init_is_restart_only_not_hot() -> None:
    """管道 ②：热改它 = restart-only（响亮记账），不静默吞、也不当语料变更拒绝。"""
    assert "kickstart_init" in RESTART_ONLY_FIELDS
    old = _course(kickstart_ref=True, kickstart_init=0.5)
    new = _course(kickstart_ref=True, kickstart_init=0.9)

    verdict, hot, restart = plan_reload(old, new)
    assert verdict == "apply", "初值不是语料身份——不得整单拒绝"
    assert "kickstart_init" not in hot
    assert "kickstart_init" in restart

    # apply_hot_fields 用 args 当对照：异名字段不映射就会恒报「变了」（假变更行）。
    # 只钉这一个字段（其余字段的 args 对照面与本用例无关）。
    args = _args()
    apply_course(args, old)
    assert "kickstart_init*" not in apply_hot_fields(args, old), "同值不得报变更"
    assert "kickstart_init*" in apply_hot_fields(args, new)


def test_kickstart_init_does_not_enter_corpus_identity() -> None:
    """管道 ③：ref/优化器语义 ≠「一个样本是什么」——进去会让全体课程指纹漂移。"""
    a = _course(kickstart_ref=True, kickstart_init=0.5)
    b = _course(kickstart_ref=True, kickstart_init=0.9)
    assert corpus_identity_fp(a) == corpus_identity_fp(b)


def test_absent_key_is_byte_identical_to_today() -> None:
    """③ 缺席 = 现状：指纹不变、args 不吃覆盖（缺省 1.0 照旧）。"""
    plain = _course(kickstart_ref=True)
    args = _args()
    apply_course(args, plain)
    assert args.kickstart_kl == 1.0, "缺席时交回 rl-config/argparse 的缺省"
    assert corpus_identity_fp(plain) == corpus_identity_fp(
        CourseConfig(name="t5-kk", mode="per-tick")
    )
    # 显式 null 也不进 overrides（model_fields_set 语义：显式 null 会透传，故文档说删键）
    assert "kickstart_kl" not in plain.flat_overrides()


def test_kickstart_init_without_ref_is_refused_loudly() -> None:
    """自相矛盾（声明初值却没开缰绳）在启动期响亮拒——不静默丢弃那个数。"""
    with pytest.raises(SystemExit, match="kickstart_init"):
        apply_course(_args(kickstart_ref=False), _course(kickstart_init=0.5))
    # 显式 0（= 明说不要初值）与缺省一样放行
    apply_course(_args(kickstart_ref=False), _course(kickstart_init=0))


def test_cli_conflict_detector_still_sees_the_mapped_dest() -> None:
    """CLI 显式传 --kickstart-kl 与课程声明冲突时仍要被拦（同源判据）。"""
    c = _course(kickstart_ref=True, kickstart_init=0.5)
    bad = course_cli_conflicts({"kickstart_kl": 0.9}, {"kickstart_kl": 1.0}, c)
    assert bad and "kickstart_kl" in bad[0]


# ────────────────────────── ④ 干烧熔断 ──────────────────────────


def _rows(baseline: float | None, *points: float | None) -> list[dict]:
    out: list[dict] = []
    if baseline is not None:
        out.append({"event": "eval_summary", "iter": 0, "winRate": baseline, "games": 50})
    for i, v in enumerate(points, start=1):
        out.append({"event": "eval_summary", "iter": i, "winRate": v, "games": 50})
    return out


def test_baseline_comes_from_the_it0_row_and_survives_re_open() -> None:
    """基线 = 最后一条 it0 行（重开一腿会重跑 bc 基线评估，取最新）。"""
    rows = [
        {"iter": 0, "winRate": 0.30},
        {"iter": 1, "winRate": 0.31},
        {"iter": 0, "winRate": 0.352},
    ]
    assert baseline_reading(rows) == pytest.approx(0.352)
    assert baseline_reading([{"iter": 1, "winRate": 0.4}]) is None


def test_burn_trips_after_consecutive_points_below_baseline() -> None:
    """连续 3 点低于基线 5pp ⇒ 停腿；中间反弹一次即清零（与 F4 的 CONSEC 语义一致）。"""
    # C 事故形状：起点 35%，门槛 = 基线 −5pp = 30%；三点 29/28/27 全在门槛以下
    v = burn_verdict(_rows(0.35, 0.29, 0.28, 0.27))
    assert v.tripped is True and v.streak == BURN_POINTS
    assert v.baseline == pytest.approx(0.35) and "回锚/塌陷" in v.reason

    # 两点还不够
    assert burn_verdict(_rows(0.35, 0.29, 0.28)).tripped is False
    # 恰好落在门槛上（低 5.0pp 不算「低于 5pp」）
    assert burn_verdict(_rows(0.35, 0.30, 0.30, 0.30)).tripped is False
    # 中间反弹 ⇒ 清零
    v2 = burn_verdict(_rows(0.35, 0.29, 0.36, 0.29, 0.28))
    assert v2.tripped is False and v2.streak == 2
    # 只低 4pp（噪声带内）不判
    assert burn_verdict(_rows(0.35, 0.31, 0.31, 0.31)).tripped is False


def test_missing_readings_neither_count_nor_reset() -> None:
    """读数缺失（0 局的轮）不当 0 也不清零——拿它清零会把真趋势打断。"""
    v = burn_verdict(_rows(0.35, 0.29, None, 0.29, 0.28))
    assert v.tripped is True and v.streak == 3
    # 无基线行 ⇒ 不判（读不到起点就不许停腿）
    v2 = burn_verdict(_rows(None, 0.10, 0.10, 0.10))
    assert v2.tripped is False and v2.baseline is None


def test_overrides_come_from_execution_side_config() -> None:
    """阈值走执行面（rl-config 的 `courses.<课>.kickstart_burn`），缺席 = 常量。"""
    assert burn_overrides(None, "t5-kk") == (BURN_MARGIN_PP, BURN_POINTS)
    cfg = {"courses": {"t5-kk": {"kickstart_burn": {"margin_pp": 12, "points": 2}}}}
    assert burn_overrides(cfg, "t5-kk") == (12.0, 2)
    # 类型不对/非法值 → 回常量（不拿坏配置去停腿）
    bad = {"courses": {"t5-kk": {"kickstart_burn": {"margin_pp": "x", "points": 0}}}}
    assert burn_overrides(bad, "t5-kk") == (BURN_MARGIN_PP, BURN_POINTS)


def _guards(tmp_path: Path, **kw) -> TrainingGuards:
    obj = TrainingGuards.__new__(TrainingGuards)
    obj.args = SimpleNamespace(
        kickstart_ref=True,
        course_path="curricula/t5-kk.jsonc",
        out="tmp/out.json",
        remote_hub_url="",
        remote_token="",
    )
    obj._jsonl_path = tmp_path / "training_log.jsonl"
    obj._traj_root = tmp_path
    obj._ledger = None
    obj._burn_streak = 0
    for k, v in kw.items():
        setattr(obj.args, k, v)
    return obj


def _write_eval_rows(tmp_path: Path, rows: list[dict]) -> None:
    p = tmp_path / "eval_log.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_guard_stops_the_leg_and_writes_a_replayable_ledger_event(tmp_path: Path) -> None:
    """执行面接线：命中 ⇒ 停腿（True）+ 账本可回放；缰绳关着 ⇒ 零行为。"""
    _write_eval_rows(tmp_path, _rows(0.35, 0.29, 0.28, 0.27))
    g = _guards(tmp_path)
    assert g._kickstart_burn(3, None) is True, "连续三点低于基线就该停腿"

    ledger = (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(x) for x in ledger]
    burn = [e for e in events if e["event"] == "kickstart_burn"][-1]
    assert burn["streak"] == BURN_POINTS and burn["baseline"] == pytest.approx(0.35)
    # 回放：同一批 eval 行重算，必须得到同一份 streak（判据只有一个来源）
    assert burn_verdict(_rows(0.35, 0.29, 0.28, 0.27)).streak == burn["streak"]
    assert any(e["event"] == "gate_verdict" and e.get("verdict") == "ABORT" for e in events)


def test_guard_is_inert_without_kickstart_or_baseline(tmp_path: Path) -> None:
    _write_eval_rows(tmp_path, _rows(0.35, 0.10, 0.10, 0.10))
    g = _guards(tmp_path)
    g.args.kickstart_ref = False
    assert g._kickstart_burn(3, None) is False, "没开缰绳就没有「回锚」这回事"
    g.args.kickstart_ref = True
    _write_eval_rows(tmp_path, _rows(None, 0.10, 0.10, 0.10))
    assert g._kickstart_burn(3, None) is False, "读不到基线（无 it0 行）不许停腿"


def test_guard_also_halts_the_cloud_on_the_same_course(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停腿就是停止烧钱：本地停之外，达令也要发给 hub（按课程，不连坐其它课）。"""
    calls: list[tuple[bool, str]] = []

    def _fake_set(hub, token, halt, log=None, course=""):
        calls.append((halt, course))
        return True

    monkeypatch.setattr("rl.loop_guards.set_cloud_halt", _fake_set, raising=True)
    monkeypatch.setattr("rl.loop_guards.dist_common.course_name_of", lambda: "t5-kk", raising=True)
    _write_eval_rows(tmp_path, _rows(0.35, 0.29, 0.28, 0.27))
    g = _guards(tmp_path, remote_hub_url="http://hub", remote_token="tok")
    g._cloud_halted = False

    assert g._kickstart_burn(3, None) is True
    assert calls == [(True, "t5-kk")], "停腿必须同时按课程下发云端停机达令"


def test_guard_counts_down_loudly_before_tripping(tmp_path: Path, capsys=None) -> None:
    """未命中但有计数 ⇒ 落一行账（状态转移才写，不是每轮都写）。"""
    _write_eval_rows(tmp_path, _rows(0.35, 0.29, 0.28))
    g = _guards(tmp_path)
    assert g._kickstart_burn(2, None) is False
    lines = (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    burn = [json.loads(x) for x in lines if json.loads(x)["event"] == "kickstart_burn"]
    assert len(burn) == 1 and burn[0]["streak"] == 2
    # 同值再判一次不再重复写（否则账本被淹）
    assert g._kickstart_burn(2, None) is False
    lines2 = (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines2) == len(lines)
