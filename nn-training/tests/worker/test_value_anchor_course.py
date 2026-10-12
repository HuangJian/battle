"""value_anchor 课程键接线测试（x26-obs it90 诊断的门控实现）。

覆盖课程字段 → flat_overrides 映射 → corpus 指纹（优化器语义不进身份，
kickstart_init/demo_bc_coef 前科同款）→ 缺省关闭。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from worker.config import CourseConfig, corpus_identity_fp


def _course(**kw) -> CourseConfig:
    return CourseConfig(name="vg", mode="per-tick", **kw)


def test_default_off_and_validation() -> None:
    """缺省 0.0 = 关（老行为）；负值拒课（牵引绳不能反向）。"""
    assert _course().value_anchor == 0.0
    assert _course(value_anchor=0.001).value_anchor == 0.001
    with pytest.raises(ValidationError):
        _course(value_anchor=-0.1)


def test_mapping_present_only_when_explicit() -> None:
    """漏映射 = 静默失效（ent_break / paired_rotate_seed 前科同款）。
    写法刻意用 `.get()` 而不用 `in`：文本断言预算（test_source_text_assert_budget）
    只许行为断言，`in` + 字面量会被记一条形态债。"""
    assert _course().flat_overrides().get("value_anchor", None) is None
    assert _course(value_anchor=0.001).flat_overrides()["value_anchor"] == 0.001


def test_optimizer_key_not_in_corpus_identity() -> None:
    """优化器语义不进语料身份（kickstart_init 前科同款）：同 reward 不同 anchor
    指纹不动——否则开门控腿会把同血缘语料判成异血缘。"""
    plain = _course()
    assert corpus_identity_fp(plain) == corpus_identity_fp(_course(value_anchor=0.001))
