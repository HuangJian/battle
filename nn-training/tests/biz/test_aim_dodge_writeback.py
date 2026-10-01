"""aim-dodge 回写感知 oracle（plan/aim-dodge-levers.plan.md §7.1 / §6.2-10）。

单行 oracle 测不到回写：回写的定义是「信用落**开火决策步**」——至少需要两行才能
从差分（r[i] = Φ[i+1] − Φ[i]）看出落点。本文件用 4 行合成 shard 复现导出器
（`tools/sim/export-rl-rollout.ts` §3.4 的区间补丁 + 后续行自然携带）的列形状：

    行 i=0（开火前）      行 i=1（开火决策行）   行 i=2（结算拍）      行 i=3
    0                     wAim·d               wAim·d               wAim·d
    ⇒ r[0] = Φ[1] − Φ[0] = wAim·d（信用落开火步）；r[1] = Φ[2] − Φ[1] = 0（结算拍本身不再付钱）

对照用例把「不做回写」（naive：结算拍才抬列）也钉住：信用会错落到结算步 r[1]、
开火步 r[0] = 0 —— 这就是回写机制存在的理由（也是消费警告「不得当逐窗口速率用」的来源）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.reward_library import METRIC_INDEX, METRICS_DIM, RewardSpec, build_reward_fn
from biz.reward_validation import validate_reward


def _rows(n: int) -> np.ndarray:
    return np.zeros((n, METRICS_DIM), dtype=np.float64)


def _spec() -> RewardSpec:
    # E1 的最小形式（计数形 `wAim·aimHitDistSum`）；只关心每日历信用落点，
    # terminal 全零（timeout 未登记 = 0）以便差分读得干净。
    return RewardSpec(formula="wAim*aimHitDistSum", params={"wAim": 0.5})


def test_aim_hit_dist_column_registered_and_enveloped() -> None:
    """新列可解析、有取值域（DEFAULT_RANGES）、包络放行（加列必须登记域）。"""
    rep = validate_reward(_spec())
    assert rep.ok, rep.errors
    assert rep.warnings == (), rep.warnings
    assert "aimHitDistSum" in METRIC_INDEX


def test_writeback_credit_lands_on_fire_decision_step() -> None:
    """回写后：开火行起列值就位 ⇒ 信用全落在开火决策步，结算拍差分为 0。"""
    fn = build_reward_fn(_spec())
    m = _rows(4)
    # 导出器回写补丁后的列形状：开火行（i=1）起所有行含 d；i=0（开火之前）不含。
    m[:, METRIC_INDEX["aimHitDistSum"]] = [0.0, 3.0, 3.0, 3.0]
    r = fn(m, "timeout", 0.0, 1)
    # Φ = 0.5·col = [0, 1.5, 1.5, 1.5]；r = diff = [1.5, 0.0, 0.0]
    np.testing.assert_allclose(r, [1.5, 0.0, 0.0], atol=1e-12)
    assert float(r[0]) == 1.5  # 开火决策步拿到全部信用
    assert float(r[1]) == 0.0  # 结算拍不再付钱（不是逐拍速率）


def test_naive_no_writeback_would_misplace_credit_on_settle_step() -> None:
    """对照（反例）：不做回写 ⇒ 信用错落到结算步，开火步为 0 —— 回写机制的理由。"""
    fn = build_reward_fn(_spec())
    m = _rows(4)
    # naive 形状：结算拍（i=2）才抬列。
    m[:, METRIC_INDEX["aimHitDistSum"]] = [0.0, 0.0, 3.0, 3.0]
    r = fn(m, "timeout", 0.0, 1)
    np.testing.assert_allclose(r, [0.0, 1.5, 0.0], atol=1e-12)
    assert float(r[0]) == 0.0  # 开火步没有信用（错期）
    assert float(r[1]) == 1.5  # 信用落在结算步（错期）


def test_end_of_episode_in_flight_folds_into_misses_identity() -> None:
    """恒等式与回写正交：misses 也走同一回写机制（开火行补 +1）⇒ 四桶仍守恒。

    合成 4 行：开火（i=1）后局末未结算 ⇒ 导出器把该弹折入 `aimMisses` 并补
    开火行区间；这里断言 misses 列的回写形状给出的差分同样落开火步。
    """
    spec = RewardSpec(formula="wMiss*aimMisses", params={"wMiss": 1.0})
    fn = build_reward_fn(spec)
    m = _rows(4)
    m[:, METRIC_INDEX["aimMisses"]] = [0.0, 1.0, 1.0, 1.0]  # 回写后形状
    r = fn(m, "timeout", 0.0, 1)
    np.testing.assert_allclose(r, [1.0, 0.0, 0.0], atol=1e-12)
