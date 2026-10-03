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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.paired as paired_mod
from biz.hot_reload import RESTART_ONLY_FIELDS, apply_hot_fields, plan_reload
from trainer.loop_guards import TrainingGuards
from trainer.loop_steps import kickstart_coef
from worker.config import CourseConfig, apply_course, corpus_identity_fp, course_cli_conflicts
from worker.kickstart_burn import (
    BURN_MARGIN_PP,
    BURN_POINTS,
    MODE_AUTO,
    MODE_BASELINE,
    MODE_PAIRED,
    baseline_reading,
    burn_mode,
    burn_overrides,
    burn_verdict,
)


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


# ──────────────── ①.5 止损块迁进课程文件（2026-10-02，plan/burn-rule-in-course-file） ────────────────
# 块由 `biz.course_spec` 解析期强校验；不进 `corpus_fp`；hot-reload 归 restart-only（读 args 快照）。


def test_course_block_validates_dirty_values() -> None:
    """解析期强校验：坏值拒课（不静默拿坏配置去停腿）——与 rl-config 容忍档刻意不同。"""
    from pydantic import ValidationError

    from biz.course_spec import KICKSTART_BURN_MODES, KickstartBurnBlock, PairedKillBlock

    def _bad(**kw: object) -> None:
        with pytest.raises(ValidationError):
            CourseConfig.model_validate({"name": "t5-kk", "mode": "per-tick", "kickstart_burn": kw})

    assert tuple(KICKSTART_BURN_MODES) == (MODE_AUTO, MODE_BASELINE, MODE_PAIRED), "与 worker MODES 对账"
    _bad(mode="whatever")
    _bad(peer="")
    _bad(margin_pp=0)
    _bad(margin_pp=float("nan"))
    _bad(points=0)
    _bad(points=1.5)
    with pytest.raises(ValidationError):
        PairedKillBlock.model_validate({"enabled": "yes"})
    with pytest.raises(ValidationError):
        PairedKillBlock.model_validate({"self_kill": 1})
    ok = KickstartBurnBlock.model_validate({"mode": "paired", "peer": "h4-aim-c0"})
    assert ok.margin_pp is None and ok.points is None, "未指定 = None → worker 模块常量"


def test_burn_block_does_not_enter_corpus_identity() -> None:
    """块不进 corpus_identity_fp（D14 判语料身份，不判止损规则）——可执行的事实。"""
    from biz.course_spec import KickstartBurnBlock, PairedKillBlock

    a = _course(kickstart_ref=True)
    b = _course(kickstart_ref=True, kickstart_burn=KickstartBurnBlock(mode="paired", peer="a0"))
    c = _course(kickstart_ref=True, paired_kill=PairedKillBlock(enabled=True))
    assert corpus_identity_fp(a) == corpus_identity_fp(b) == corpus_identity_fp(c)


def test_burn_block_is_restart_only_and_reported_truthfully() -> None:
    """restart-only 冻结面：块物化进 args（读面快照）、热加载不写回、同值不假报变更。"""
    from biz.course_spec import KickstartBurnBlock

    old = _course(kickstart_ref=True, kickstart_burn=KickstartBurnBlock(mode="paired", peer="a0"))
    new = _course(
        kickstart_ref=True,
        kickstart_burn=KickstartBurnBlock(mode="paired", peer="a0", margin_pp=8.0),
    )
    verdict, hot, restart = plan_reload(old, new)
    assert verdict == "apply", "止损块不是语料身份——不得整单拒绝"
    assert "kickstart_burn" not in hot and "kickstart_burn" in restart

    args = _args()
    apply_course(args, old)
    old_block = old.kickstart_burn
    assert old_block is not None
    assert args.kickstart_burn == old_block.model_dump(), "块必须物化进 args（冻结面）"
    assert "kickstart_burn*" not in apply_hot_fields(args, old), "同值不得报变更（假变更行）"
    assert "kickstart_burn*" in apply_hot_fields(args, new)
    assert args.kickstart_burn == old_block.model_dump(), "restart-only：热加载不写回"


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


def test_overrides_come_from_the_module_constants_when_block_absent() -> None:
    """块缺席 = 模块常量（第二刀起 rl-config 回落已删；止损唯一来源 = 课程文件块）。"""
    assert burn_overrides(None) == (BURN_MARGIN_PP, BURN_POINTS)
    assert burn_mode(None) == (MODE_AUTO, "")


def test_course_block_is_authoritative() -> None:
    """课程文件块存在即权威（半块 ⇒ 模块缺省，无第三态）；块缺席 ⇒ 模块缺省。

    反转优先级 / 复活 rl-config 回落都必须在 CI 红（第二刀 P1 后 `legacy_*` 已删）。
    """
    block = {"margin_pp": 12, "points": 2, "mode": MODE_PAIRED, "peer": "a0"}
    assert burn_overrides(block) == (12.0, 2)
    assert burn_mode(block) == (MODE_PAIRED, "a0")
    # 半块：缺 margin/points ⇒ 常量；缺 mode ⇒ auto（不逐字段回落）
    half = {"peer": "a0"}
    assert burn_overrides(half) == (BURN_MARGIN_PP, BURN_POINTS)
    assert burn_mode(half) == (MODE_AUTO, "a0")
    # 块缺席 ⇒ 模块缺省
    assert burn_overrides(None) == (BURN_MARGIN_PP, BURN_POINTS)
    assert burn_mode(None) == (MODE_AUTO, "")
    # pydantic 块对象也认（读面物化前 / 单测直喂）
    from biz.course_spec import KickstartBurnBlock

    assert burn_mode(KickstartBurnBlock(mode="paired", peer="a0")) == (MODE_PAIRED, "a0")


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


@pytest.fixture(autouse=True)
def _gate_halt_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """门禁停机模式 2026-10-01 起是**平台文件**：本文件一律把它重定向进 tmp_path。

    不重定向就会读仓根 `tmp/gate-halt.json`（那是控制台的活状态：拨到 notify 时
    `test_guard_also_halts_the_cloud_on_the_same_course` 等停机断言会变红），停止腿的回执
    也会写进本机工作区。
    """
    monkeypatch.setenv("NN_GATE_HALT", str(tmp_path / "gate-halt.json"))
    monkeypatch.setenv("NN_GATE_HALT_APPLIED", str(tmp_path / "gate-halt.applied.json"))
    monkeypatch.setenv("NN_GATE_HALT_LEG", "local")


def test_guard_stops_the_leg_and_writes_a_replayable_ledger_event(tmp_path: Path) -> None:
    """执行面接线：命中 ⇒ 停腿（True）+ 账本可回放；缰绳关着 ⇒ 零行为。"""
    _write_eval_rows(tmp_path, _rows(0.35, 0.29, 0.28, 0.27))
    g = _guards(tmp_path)
    assert g._kickstart_burn(3) is True, "连续三点低于基线就该停腿"

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
    assert g._kickstart_burn(3) is False, "没开缰绳就没有「回锚」这回事"
    g.args.kickstart_ref = True
    _write_eval_rows(tmp_path, _rows(None, 0.10, 0.10, 0.10))
    assert g._kickstart_burn(3) is False, "读不到基线（无 it0 行）不许停腿"


def test_guard_also_halts_the_cloud_on_the_same_course(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停腿就是停止烧钱：本地停之外，达令也要发给 hub（按课程，不连坐其它课）。"""
    calls: list[tuple[bool, str]] = []

    def _fake_set(hub, token, halt, log=None, course=""):
        calls.append((halt, course))
        return True

    monkeypatch.setattr("trainer.loop_guards.set_cloud_halt", _fake_set, raising=True)
    monkeypatch.setattr("trainer.loop_guards.common.distribution.course_name_of", lambda: "t5-kk", raising=True)
    _write_eval_rows(tmp_path, _rows(0.35, 0.29, 0.28, 0.27))
    g = _guards(tmp_path, remote_hub_url="http://hub", remote_token="tok")
    g._cloud_halted = False

    assert g._kickstart_burn(3) is True
    assert calls == [(True, "t5-kk")], "停腿必须同时按课程下发云端停机达令"


def test_guard_counts_down_loudly_before_tripping(tmp_path: Path, capsys=None) -> None:
    """未命中但有计数 ⇒ 落一行账（状态转移才写，不是每轮都写）。"""
    _write_eval_rows(tmp_path, _rows(0.35, 0.29, 0.28))
    g = _guards(tmp_path)
    assert g._kickstart_burn(2) is False
    lines = (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    burn = [json.loads(x) for x in lines if json.loads(x)["event"] == "kickstart_burn"]
    assert len(burn) == 1 and burn[0]["streak"] == 2
    # 同值再判一次不再重复写（否则账本被淹）
    assert g._kickstart_burn(2) is False
    lines2 = (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines2) == len(lines)


# ══════════════════ ⑤ 参照物两档（`baseline` vs `paired`，2026-09-30） ══════════════════
#
# 为什么加 `paired`：h4-lane Wave 1 三腿的实测回测
# （`nn-training/tools/backtest-burn-rule.py`，可重跑）——
# 零奖励的对照臂 a0 自己也飘到 streak 2，有可归因效应的 a2 反而挨杀（假阳性 1/3）；
# 零假设 MC 下旧规则从 0.005% 跳到 49.3%，**假阳性来自「自己的起点」这个参照物会飘**。


def test_burn_mode_reads_the_course_block_only() -> None:
    """`{mode,peer}` 只在课程块里读；缺席 → auto + 空 peer；脏值 → 回 auto（不拿坏配置停腿）。"""
    assert burn_mode(None) == (MODE_AUTO, "")
    assert burn_mode({"mode": MODE_PAIRED, "peer": " a0 "}) == (MODE_PAIRED, "a0")
    # 类型不对/非法值 → 回 auto（块已过解析期强校验，这里是兜底纪律）
    assert burn_mode({"mode": "whatever", "peer": 7}) == (MODE_AUTO, "")


def test_paired_mode_ignores_drift_the_control_also_has() -> None:
    """★ 回归（Wave 1 假阳性形态）：本腿落后**自己起点** 3 连点，但对端同步落后 ⇒ 不停腿。

    实测形状（h4-lane）：a0（零奖励）逐点 pattern `..TT.T.`、a2 = `.TTT` ⇒ 旧口径杀 a2。
    """
    own = _rows(0.86, 0.79, 0.78, 0.77)  # 连 3 点低于自己起点 5pp
    peer = _rows(0.86, 0.79, 0.78, 0.77)  # 对端同步落后同样多
    assert burn_verdict(own).tripped is True, "旧口径（参照 = 自己的 it0）会停腿"
    v = burn_verdict(own, peer_rows=peer)
    assert v.tripped is False and v.mode == MODE_PAIRED
    assert v.streak == 0 and v.delta_pp == pytest.approx(0.0)
    assert v.baseline == pytest.approx(0.86), "baseline 格仍填本腿 it0（控制台对账口径不变）"


def test_paired_mode_stops_on_an_attributable_regression() -> None:
    """本腿比**对端**连续 3 点落后 >5pp ⇒ 停腿（Δ 才是可归因的那部分）。"""
    own = _rows(0.86, 0.77, 0.77, 0.77)
    peer = _rows(0.86, 0.86, 0.86, 0.86)
    v = burn_verdict(own, peer_rows=peer, peer_name="a0")
    assert v.tripped is True and v.mode == MODE_PAIRED and v.streak == BURN_POINTS
    assert v.delta_pp == pytest.approx(-9.0) and v.peer == pytest.approx(0.86)
    assert v.last == pytest.approx(0.77) and v.baseline == pytest.approx(0.86)
    assert "a0" in v.reason and "配对差" in v.reason
    # 恰好压在门槛上（落后 5.0pp 不算「超过 5pp」）
    assert burn_verdict(_rows(0.86, 0.81, 0.81, 0.81), peer_rows=peer).tripped is False


def test_paired_mode_only_counts_aligned_its_and_refuses_thin_data() -> None:
    """只比两臂**都**有读数的 it（末条）；it0 不参战；对齐点 < points ⇒ 不判。"""
    own = _rows(0.86, 0.70, 0.70, 0.70)
    v = burn_verdict(own, peer_rows=_rows(0.86, 0.86, 0.86, 0.86))
    assert v.tripped is True and v.streak == 3
    # 对端缺 it1 ⇒ 只剩 it2/it3 两个对齐点 < points=3 ⇒ 不判（数据不足不是证据）
    thin = [
        {"event": "eval_summary", "iter": 0, "winRate": 0.86},
        {"event": "eval_summary", "iter": 2, "winRate": 0.86},
        {"event": "eval_summary", "iter": 3, "winRate": 0.86},
    ]
    v2 = burn_verdict(own, peer_rows=thin)
    assert v2.tripped is False and v2.delta_pp is None, "对齐点不够 ⇒ 不判、不算 streak"
    assert v2.streak == 0


# ── 执行面接线（与 `test_paired_kill.py` 同构：本臂 `<tmp>/own`、对端 `<tmp>/t-peer`）──

BURN_V = 20260930


def _burn_curricula(tmp: Path, *, peer_v: int) -> Path:
    d = tmp / "curricula"
    d.mkdir(parents=True, exist_ok=True)
    (d / "t-own.jsonc").write_text(
        json.dumps({"name": "t-own", "paired_rotate_seed": BURN_V}), "utf-8"
    )
    (d / "t-peer.jsonc").write_text(
        json.dumps({"name": "t-peer", "paired_rotate_seed": peer_v}), "utf-8"
    )
    return d


def _burn_guard(
    tmp_path: Path, *, declared: int | None = BURN_V, burn: dict | None = None
) -> TrainingGuards:
    """本臂 traj = `<tmp>/own`，对端 = `<tmp>/t-peer`（与生产同构）。

    `burn` = args 上的**启动物化快照**（课程文件块；None = 无块 ⇒ 模块缺省）。
    """
    obj = _guards(tmp_path, course_path="curricula/t-own.jsonc")
    raw: dict[str, object] = {"name": "t-own", "mode": "per-tick"}
    if declared is not None:
        raw["paired_rotate_seed"] = declared
    obj.args.course_obj = CourseConfig.model_validate(raw)
    obj.args.course = "t-own"
    obj.args.kickstart_burn = burn
    obj._traj_root = tmp_path / "own"
    return obj


def _burn_write(dir_: Path, rows: list[dict]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "eval_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def _burn_peer_run_start(tmp: Path, seed: int | None) -> None:
    p = tmp / "t-peer"
    p.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        (p / "training_log.jsonl").write_text(
            json.dumps({"event": "run_start", "iter": 0, "rotateSeed": seed}) + "\n",
            "utf-8",
        )


def _burn_events(tmp: Path) -> list[dict]:
    p = tmp / "training_log.jsonl"
    if not p.exists():
        return []
    return [
        json.loads(x) for x in p.read_text(encoding="utf-8").strip().splitlines() if x.strip()
    ]


def test_paired_wiring_uses_the_peer_and_survives_shared_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接线：`mode=paired` + 同 V 对端 ⇒ 参照换成对端，**共享漂移不再停腿**。"""
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _burn_curricula(tmp_path, peer_v=BURN_V), raising=True)
    _burn_write(tmp_path / "own", _rows(0.86, 0.79, 0.78, 0.77))
    _burn_write(tmp_path / "t-peer", _rows(0.86, 0.79, 0.78, 0.77))
    _burn_peer_run_start(tmp_path, BURN_V)
    assert (
        _burn_guard(tmp_path, burn={"mode": MODE_BASELINE})._kickstart_burn(3) is True
    ), "显式 baseline ⇒ 按自己的起点停腿"
    assert _burn_guard(tmp_path)._kickstart_burn(3) is False, "auto 解析出同 V 对端 ⇒ 改走配对读"
    g = _burn_guard(tmp_path, burn={"mode": MODE_PAIRED})
    assert g._kickstart_burn(3) is False
    assert _burn_events(tmp_path)[-1]["event"] == "gate_verdict"


def test_paired_wiring_stops_and_records_the_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接线：本臂落后对端 3 连点 ⇒ 停腿 + 事件带 mode/delta_pp/peer（可回放）。"""
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _burn_curricula(tmp_path, peer_v=BURN_V), raising=True)
    _burn_write(tmp_path / "own", _rows(0.86, 0.77, 0.77, 0.77))
    _burn_write(tmp_path / "t-peer", _rows(0.86, 0.86, 0.86, 0.86))
    _burn_peer_run_start(tmp_path, BURN_V)

    assert _burn_guard(tmp_path, burn={"mode": MODE_PAIRED})._kickstart_burn(3) is True
    events = _burn_events(tmp_path)
    burn = [e for e in events if e["event"] == "kickstart_burn"][-1]
    assert burn["mode"] == MODE_PAIRED and burn["streak"] == BURN_POINTS
    assert burn["delta_pp"] == pytest.approx(-9.0) and burn["peer"] == pytest.approx(0.86)
    assert burn["baseline"] == pytest.approx(0.86)
    verdicts = [e for e in events if e["event"] == "gate_verdict"]
    assert verdicts[-1]["verdict"] == "ABORT" and "配对参照" in verdicts[-1]["reason"]


def test_paired_wiring_refuses_a_mispaired_peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 前提闸：对端不在同一把 V 上 ⇒ 不拿错配读数判 ⇒ 退回 baseline 并响亮提示。"""
    monkeypatch.setattr(
        paired_mod, "CURRICULA_DIR", _burn_curricula(tmp_path, peer_v=BURN_V + 82), raising=True
    )
    _burn_write(tmp_path / "own", _rows(0.86, 0.79, 0.78, 0.77))
    _burn_write(tmp_path / "t-peer", _rows(0.86, 0.86, 0.86, 0.86))
    _burn_peer_run_start(tmp_path, BURN_V + 82)

    assert _burn_guard(tmp_path, burn={"mode": MODE_PAIRED})._kickstart_burn(3) is True, (
        "退回 baseline ⇒ 按本腿起点停腿"
    )
    burn = [e for e in _burn_events(tmp_path) if e["event"] == "kickstart_burn"][-1]
    assert burn["mode"] == MODE_BASELINE


def test_ambiguous_peer_falls_back_to_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """多臂家族（≥2 条同 V 腿）里「谁是控」是实验设计 ⇒ 不猜，退回 baseline。"""
    d = _burn_curricula(tmp_path, peer_v=BURN_V)
    (d / "t-peer2.jsonc").write_text(
        json.dumps({"name": "t-peer2", "paired_rotate_seed": BURN_V}), "utf-8"
    )
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", d, raising=True)
    _burn_write(tmp_path / "own", _rows(0.86, 0.79, 0.78, 0.77))
    for name in ("t-peer", "t-peer2"):
        _burn_write(tmp_path / name, _rows(0.86, 0.86, 0.86, 0.86))
        _burn_peer_run_start(tmp_path, BURN_V)
        (tmp_path / name / "training_log.jsonl").write_text(
            json.dumps({"event": "run_start", "iter": 0, "rotateSeed": BURN_V}) + "\n",
            "utf-8",
        )

    assert _burn_guard(tmp_path, burn={"mode": MODE_PAIRED})._kickstart_burn(3) is True
    burn = [e for e in _burn_events(tmp_path) if e["event"] == "kickstart_burn"][-1]
    assert burn["mode"] == MODE_BASELINE, "对端不唯一 ⇒ 不猜，退回本腿起点"


def test_single_leg_keeps_the_baseline_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未声明 `paired_rotate_seed` ⇒ 没有对端这种事，auto 就是 baseline（零行为）。"""
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _burn_curricula(tmp_path, peer_v=BURN_V), raising=True)
    _burn_write(tmp_path / "own", _rows(0.86, 0.79, 0.78, 0.77))
    _burn_write(tmp_path / "t-peer", _rows(0.86, 0.86, 0.86, 0.86))
    _burn_peer_run_start(tmp_path, BURN_V)

    assert _burn_guard(tmp_path, declared=None)._kickstart_burn(3) is True
    burn = [e for e in _burn_events(tmp_path) if e["event"] == "kickstart_burn"][-1]
    assert burn["mode"] == MODE_BASELINE


def test_guard_uses_the_course_block_and_module_defaults(tmp_path: Path) -> None:
    """执行面接线：args 上有课程块（物化快照）⇒ 块即权威；无块 ⇒ 模块常量（回落读面已删）。"""
    _write_eval_rows(tmp_path, _rows(0.35, 0.29, 0.28, 0.27))

    g = _guards(tmp_path)
    g.args.kickstart_burn = {"margin_pp": 100.0, "points": 3, "mode": MODE_BASELINE}
    assert g._kickstart_burn(3) is False, "课程块把噪声带放大到 100pp ⇒ 不停腿"

    g2 = _guards(tmp_path)
    assert g2._kickstart_burn(3) is True, "无块 ⇒ 模块常量（margin 5pp ⇒ 三点全命中）"
