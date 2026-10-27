"""课程字段 → argparse 映射 → 语料身份 的接线（plan/it-stage-ratation §2 / §3.6）。

漏映射 = 静默失效（`ent_break` / `paired_rotate_seed` 前科）：课程里写了 `rotation_mode`，
`flat_overrides` 没进表 ⇒ args 上根本没有这个键 ⇒ `window_from_args` 走「关闭」分支，
表现只是「每关密度没变」——账本上没有任何异常。所以这三条必须钉住：

  1. 字段默认（缺席 = 关闭）；
  2. **写进课程 JSON 就生效**（`flat_overrides` 里有它，且值原样）；
  3. 语料身份**仅激活时**变（老课程指纹零漂移，在跑的腿不断血缘）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from worker.config import CourseConfig, corpus_identity_fp
from worker.stage_rotation import window_from_args
from worker.volume_waves import parse_stages_arg


def _course(**kw: Any) -> CourseConfig:
    return CourseConfig(name="rot", mode="per-tick", stages="0-34", **kw)


def _args(**over: object) -> SimpleNamespace:
    """课程覆盖后的 args 形态（`apply_course` 就是 setattr 这批键）。"""
    base: dict[str, object] = {
        "rotation_mode": "",
        "rotation_per_it": 0,
        "rotation_pinned": "",
        "rotate_seed": 4242,
        "stages": "0-34",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_course_defaults_are_off() -> None:
    c = _course()
    assert (c.rotation_mode, c.rotation_per_it, c.rotation_pinned) == ("", 0, "")


def test_json_keys_reach_args() -> None:
    """★ 必修缝隙（评审：历史前科 `--rotate-stages` 只能从 CLI 给）。"""
    plain = _course()
    assert "rotation_mode" not in plain.flat_overrides(), "缺席不该覆盖 args（老课程逐字节不变）"

    c = _course(rotation_mode="even", rotation_per_it=12)
    ov = c.flat_overrides()
    assert ov["rotation_mode"] == "even"
    assert ov["rotation_per_it"] == 12
    # 写进去就能算出窗口（不是「键存在但没人读」）
    args = SimpleNamespace(**ov, rotate_seed=1)
    w = window_from_args(args, 1, parse_stages_arg("0-34"))
    assert len(w) == 12 and w == sorted(w)
    # 一个 epoch 的最后一窗不满（ceil(35/12) 轮里第 3 轮只有 11 关）——不是 bug，是覆盖算术
    assert len(window_from_args(args, 3, parse_stages_arg("0-34"))) == 11


def test_pinned_accepts_a_stage_spec_string() -> None:
    """pinned 用与 `--stages` 同一个规格串解析器（避免引入 list 型 dest）。"""
    c = _course(rotation_mode="pinned", rotation_per_it=2, rotation_pinned="2033-2034")
    ov = c.flat_overrides()
    # 自定义关的真实形态：`--stages` 里就是 2000+i 这批关号（pinned 必须是它的子集）
    stages = [2000 + i for i in range(35)]
    w = window_from_args(SimpleNamespace(**ov, rotate_seed=1), 1, stages)
    assert {2033, 2034} <= set(w)
    assert len(w) == 4  # 2 pinned + 余额 2
    # pinned 不在关集里 ⇒ 响亮（打错一个关号不该静默变成「每轮只采别的关」）
    with pytest.raises(ValueError, match="pinned"):
        window_from_args(SimpleNamespace(**ov, rotate_seed=1), 1, [0, 1, 2])


def test_identity_changes_only_when_active() -> None:
    plain = _course()
    assert corpus_identity_fp(plain) == corpus_identity_fp(_course())
    active = _course(rotation_mode="even", rotation_per_it=12)
    assert corpus_identity_fp(active) != corpus_identity_fp(plain)
    # 换策略/改窗口 = 新实验（§15.5）：两份都不该与 active 同指纹
    other = _course(rotation_mode="pinned", rotation_per_it=12, rotation_pinned="2033")
    assert corpus_identity_fp(other) not in {
        corpus_identity_fp(plain),
        corpus_identity_fp(active),
    }


def test_negative_per_it_is_rejected_at_config_time() -> None:
    with pytest.raises(ValueError, match="rotation_per_it"):
        _course(rotation_mode="even", rotation_per_it=-1)
