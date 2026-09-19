"""R2c-3 余项：`scripts/measure_checkpoint_rss.py` 的回归（真机 RSS 实测表的可复现性）。

这张表是**上线前置条件**（plan/r2-loop-task-queue §6：`checkpointCacheMb` 的默认值此前只能
拍脑袋），所以它自己也要有回归：纯函数（推荐值 / 表格渲染 / 候选解析）逐条钉死，另加一条
**真造一份栈**的集成用例（参数个数与量级合理；torch 缺失即跳过）。

刻意不测的点：绝对 RSS 数值（它随 torch 版本/机器浮动，断言绝对数就是给 CI 埋雷）。
测的是**关系**：每课增量 ≪ torch 基线、栈必须活着（否则累计曲线是假的，用 `keep` 钉住）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.measure_checkpoint_rss import (
    StackRow,
    build_stack,
    main,
    recommend,
    render_table,
    resolve_weights,
)


def _row(**kw: object) -> StackRow:
    base: dict[str, object] = {
        "course": "c4-dodge",
        "mode": "per-tick",
        "weights": "",
        "skipped": "",
        "params": 70216,
        "model_mb": 0.71,
        "grads_mb": 0.0,
        "adam_mb": 0.6,
        "refs_mb": 0.02,
        "rss_delta_mb": 1.33,
    }
    base.update(kw)
    return StackRow(**base)  # type: ignore[arg-type]


# ────────────────────────────── 纯函数 ──────────────────────────────


def test_recommend_rounds_up_to_256_and_keeps_headroom() -> None:
    """实测每课约 1.3MB ⇒ 5 课也远超不出一个 256MB 块：字节顶是**第二道保险**，不是主约束。"""
    mb, text = recommend(5, 1.33)
    assert mb == 256
    assert "1.33MB/课" in text and "checkpointCacheMb = 256" in text
    # 头部余量真的起作用：100MB/课 × 5 × 1.5 = 750 ⇒ 768（向上取块）
    assert recommend(5, 100.0)[0] == 768
    # 256 的整数倍边界：raw = 5 × 1.5 × per ⇒ per=68.26 → 511.95 ⇒ 512；per=68.3 → 512.25 ⇒ 768
    assert recommend(5, 68.26)[0] == 512
    assert recommend(5, 68.3)[0] == 768
    # 0/负值不得算出 0（否则「上限」等于禁用缓存）
    assert recommend(5, 0.0)[0] == 256


def test_stack_row_theory_counts_adam_and_refs() -> None:
    """理论换算：float32 × (权重+梯度+Adam m/v+refs)；它是「别的 arch 自己算」的入口。"""
    no_ref = _row(refs_mb=0.0, params=1000)
    with_ref = _row(refs_mb=0.1, params=1000)
    assert no_ref.theory_mb == pytest.approx(1000 * 4 * 4 / 1e6)
    assert with_ref.theory_mb == pytest.approx(1000 * 4 * 5 / 1e6)
    assert with_ref.theory_mb > no_ref.theory_mb


def test_render_table_carries_the_credibility_lines() -> None:
    """表必须自带可信度信息：测的哪份权重 / 跳过了谁 / 理想值 vs 实测 / 累计曲线。"""
    rows = [
        _row(skipped="rl-weights.it5.json(ValueError)", weights=""),  # 合计 1.33
        _row(  # 合计 0.41+0.30+0.81+0.23 = 1.75（带 ref 的那一档，取最大应当选中它）
            course="intent:默认架构",
            mode="intent",
            params=74227,
            model_mb=0.41,
            grads_mb=0.3,
            adam_mb=0.81,
            refs_mb=0.23,
        ),
    ]
    out = render_table(rows, baseline_mb=294.5, cum=[294.8, 296.5], cache_courses=5)
    assert "（默认架构：无可用权重文件）" in out  # 空权重必须显式说，不能留白
    assert "跳过候选: rl-weights.it5.json(ValueError)" in out
    assert "理论值" in out and "实测" in out
    assert "torch 基线 RSS" in out and "294.5MB" in out
    assert "N=1 本进程累计 RSS：294.8MB" in out and "N=2" in out
    assert "每课增量（取最大）：1.75MB" in out  # 取最大而不是平均（保守方向）
    assert "checkpointCacheMb = 256" in out


def test_resolve_weights_always_offers_the_default_arch_fallback() -> None:
    """候选表末位恒为空串（默认架构）：一个候选都装不上也要能出表，而不是脚本崩掉。

    为什么重要：本机 `weights/` 里躺着 schema_major=2 的旧权重（红线不可装载）——若只取
    「最新一个」，脚本会直接退出，内存表就永远出不来。
    """
    cands = resolve_weights("no-such-course-xyz", None)
    assert cands[-1] == ""
    assert all(isinstance(c, str) for c in cands)
    # 显式路径优先，且仍保留默认架构兜底
    explicit = resolve_weights("no-such-course-xyz", "nn-training/weights")
    # 显式路径会被补成 REPO_ROOT 下的**绝对**路径（脚本要能从任意 cwd 跑，见
    # resolve_weights 的 `p if p.is_absolute() else REPO_ROOT / p`）⇒ 盘符与分隔符都与
    # 平台相关，断言只能比「尾部两段」，不能拿裸字符串 endswith。
    assert explicit[0].replace("\\", "/").endswith("nn-training/weights") and explicit[-1] == ""


def test_cli_rejects_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        main(["--modes", "per-tick,bogus"])


def test_cli_requires_something_to_measure() -> None:
    with pytest.raises(SystemExit):
        main([])


# ────────────────────────────── 真造一份栈（torch） ──────────────────────────────


def test_build_stack_is_cheap_and_keepalive_holds_it() -> None:
    """真栈：参数量合理、每课增量远小于 torch 基线（这正是「缓存上限不是主约束」的结论）。

    `keep` 非空 ⇒ 对象被保命持有（栈被 gc 掉时第 2 课的增量会变成 0，累计曲线就是假的）。
    """
    pytest.importorskip("torch")
    keep: list[object] = []
    row = build_stack("per-tick:默认架构", [""], "per-tick", keep=keep)
    assert len(keep) == 1
    assert 10_000 < row.params < 500_000  # 小 CNN 学生：量级钉住（arch 变了也该在这个带内）
    assert row.model_mb >= 0 and row.adam_mb >= 0 and row.refs_mb >= 0
    assert row.total_mb < 50  # ★ 结论的保护带：每课增量是 MB 级，不是几十 MB
    assert row.weights == "" and row.skipped == ""  # 空候选 ⇒ 默认架构，且没有可跳过的候选
