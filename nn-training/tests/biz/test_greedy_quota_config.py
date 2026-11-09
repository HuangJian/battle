"""greedy 结果配额：课程 schema、身份与 hot-reload 分类（plan rev4 §6/§7.3）。

钉四件事：

1. 三档校验（≥0、和 ≤1、cap ≥1）——非法值拒课；
2. `flat_overrides` 把嵌套块原样展开（热加载/CLI 覆盖的读面）；
3. 身份**只在激活时**变，`oversample_cap`/`soft` 不进 `corpus_identity_fp`
   （预算/降级开关，不是「训练集放哪些样本」）；
4. `greedy_quota` 是 restart-only（改比例 = 新语料，不能热切）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.corpus_fp import corpus_identity_fp, episode_identity_fp
from biz.course_spec import CourseConfig, GreedyQuotaBlock
from biz.hot_reload import RESTART_ONLY_FIELDS


def test_greedy_quota_block_parses_and_flat_overrides() -> None:
    block = {
        "win": 0.10,
        "loss": 0.0,
        "timeout": 0.10,
        "oversample_cap": 2.0,
        "soft": True,
    }
    course = CourseConfig(
        greedy_quota=GreedyQuotaBlock(
            win=0.10,
            loss=0.0,
            timeout=0.10,
            oversample_cap=2.0,
            soft=True,
        )
    )
    assert course.greedy_quota is not None
    assert course.greedy_quota.win == 0.10
    assert course.flat_overrides().get("greedy_quota") == block


def test_greedy_quota_rejects_bad_ratios() -> None:
    with pytest.raises(ValidationError):
        GreedyQuotaBlock(win=0.6, timeout=0.5)  # 和 > 1
    with pytest.raises(ValidationError):
        GreedyQuotaBlock(win=-0.1)
    with pytest.raises(ValidationError):
        GreedyQuotaBlock(win=0.1, oversample_cap=0.5)


def test_identity_changes_only_when_active_and_ignores_cap_soft() -> None:
    base = CourseConfig()
    active = CourseConfig(greedy_quota=GreedyQuotaBlock(win=0.1, timeout=0.1))
    same_ratios_other_budget = CourseConfig(
        greedy_quota=GreedyQuotaBlock(
            win=0.1,
            timeout=0.1,
            oversample_cap=3.0,
            soft=False,
        )
    )
    other_ratios = CourseConfig(greedy_quota=GreedyQuotaBlock(win=0.2, timeout=0.1))

    assert corpus_identity_fp(active) != corpus_identity_fp(base), "激活必须换身份"
    assert corpus_identity_fp(active) == corpus_identity_fp(same_ratios_other_budget), (
        "cap/soft 是预算/降级开关，不得进语料身份"
    )
    assert corpus_identity_fp(active) != corpus_identity_fp(other_ratios), "比例变 = 语料变"
    assert episode_identity_fp(active) != episode_identity_fp(other_ratios)


def test_greedy_quota_is_restart_only() -> None:
    assert set(RESTART_ONLY_FIELDS) >= {"greedy_quota"}
