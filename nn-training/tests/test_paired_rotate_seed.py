"""配对 rotateSeed 进课程文件（2026-09-21，plan/accident.plan.md §2）。

现象：配对要求两臂同 rotateSeed，但值靠人手在命令行传 —— 第一次就传漏/传错
（1789926833 vs 1789926915，差 82 秒抖动）⇒ 配对失败、返工重开。

契约：
  ① 课程键 `paired_rotate_seed` → argparse dest `rotate_seed`（异名映射；
     漏进 `flat_overrides` = 静默失效 —— `ent_break` 前科）；
  ② 三段不断：**文件值 → args → 同种子流**（`resolve_rotate_seed` 判 explicit）；
  ③ 缺席 = 老行为逐字节不变（CLI `--rotate-seed` 调试后门仍可用）；
     显式写 `null` **≠** 不写（`model_fields_set` 语义：null 会透传覆盖后门）；
  ④ 进 `corpus_identity_fp` 但**仅在激活时**（无条件加 = 全体既有课程指纹漂移）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.config import CourseConfig, apply_course, corpus_identity_fp, load_course
from rl.course import build_pairs, resolve_rotate_seed

V = 20260921


def _write_course(tmp_path: Path, patch: dict) -> Path:
    d = {"name": "pair-a", "mode": "per-tick"}
    d.update(patch)
    p = tmp_path / "pair-a.jsonc"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_key_maps_to_rotate_seed_dest() -> None:
    """① 课程键与 argparse dest 异名映射（`paired_rotate_seed` → `rotate_seed`）。"""
    o = CourseConfig(name="pair-a", paired_rotate_seed=V).flat_overrides()
    assert o["rotate_seed"] == V
    # 缺席 = 不覆盖：CLI `--rotate-seed` 调试后门逐字节存活
    assert "rotate_seed" not in CourseConfig(name="pair-a").flat_overrides()


def test_explicit_null_is_not_the_same_as_absent() -> None:
    """③ 显式 null 会透传覆盖（堵死后门）—— 后门用户要**删键**，不是写 null。"""
    c = CourseConfig(name="pair-a", paired_rotate_seed=None)
    assert c.flat_overrides()["rotate_seed"] is None


def test_file_value_reaches_args_and_beats_inheritance(tmp_path: Path) -> None:
    """② 三段不断：文件值 → args（apply_course）→ `resolve_rotate_seed` 判 explicit。"""
    p = _write_course(tmp_path, {"paired_rotate_seed": V})
    course = load_course(p)
    args = types.SimpleNamespace(seed=7, mode="per-tick")
    apply_course(args, course)
    assert args.rotate_seed == V
    # 续跑继承（prev_rs=999）不得压过课程显式值 —— 这正是配对断掉的那条缝
    got, src = resolve_rotate_seed(args.seed, args.rotate_seed, 999, 1700000000)
    assert (got, src) == (V, "explicit")
    # 第三段：同 V 的两臂（另一门课程文件写同一个 V）种子流逐字相同 = 配对前提；
    # V 变则流变（防写死假绿）。
    for k, v in {
        "stages": "2000-2003",
        "seeds": "0-3",
        "seed_rotate": 150,  # >0 = 轮转分支（种子流按 (rotateSeed, it) 键控）
        "seeds_per_stage": 10,
        "rotate_stages": 0,
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
    }.items():
        setattr(args, k, v)
    p1 = build_pairs(args, 76, V)
    assert p1 and p1 == build_pairs(args, 76, V)
    assert build_pairs(args, 76, V + 1) != p1


def test_corpus_fp_unchanged_when_absent_and_tracked_when_active() -> None:
    """④ 无键课程指纹逐字节不变；激活时才进身份（防全体指纹漂移）。"""
    base = corpus_identity_fp(CourseConfig(name="pair-a"))
    assert corpus_identity_fp(CourseConfig(name="pair-a", paired_rotate_seed=None)) == base
    assert corpus_identity_fp(CourseConfig(name="pair-a", paired_rotate_seed=V)) != base
