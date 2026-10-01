"""拆分的**契约守卫**：课程配置类面永住 `biz/course_spec.py`（S5 第十刀，2026-09-27）。

`biz/config.py` **1566 → 598 行**（本刀三面合计）；类面搬走 25 名（`CourseConfig` ·
`GatesSpec` · `StageSpec` · `RewardBlock` · `PpoScheduleEntry` · gates 常量族 ·
`_default_lives` · `STAGE_JSON_MAX_BYTES`/`CUSTOM_STAGE_BASE` 等，逐字节不动）。

本文件钉五件事：

1. **定义唯一**——25 名不许在 `config.py` 里再实现一遍；
2. **依赖面闭集**——stdlib + `pydantic` + `biz.reward_library`；唯一向上的引用是
   `GatesSpec` 跨课门校验对 `biz.course_resolve` 的**函数内**延迟导入（顶层禁止，防成环）；
3. **无反向门面依赖**——顶层不得 import `biz.config` / `biz.course_resolve`；
4. **转发同一对象**——门面每个名与新家是 `is`；
5. **契约语义没变**（功能性）：未知 kind / 跨课引用找不到都响亮拒 · 内联与自定义关的
   `stage_ids` · `stage_json` 形状 · `_default_lives` 兜底 · `terminal` 键词表。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import biz.course_spec as spec_mod
import worker.config as config_mod

CONFIG_FILE = ROOT / "worker" / "config.py"
NEW_FILE = ROOT / "biz" / "course_spec.py"

MOVED_NAMES = {
    "STAGE_JSON_MAX_BYTES",
    "CUSTOM_STAGE_BASE",
    "Spawn",
    "SpawnVariant",
    "StageSpec",
    "ParamSchedule",
    "RewardBlock",
    "PpoScheduleEntry",
    "GATE_KINDS",
    "GATE_VERDICTS",
    "GATE_SPLIT",
    "GATE_SPLIT_KINDS",
    "GATE_PLATEAU_METRICS",
    "GATE_CROSS_COURSE_KINDS",
    "_GATE_FRAC_FIELDS",
    "_GATE_REL_FIELDS",
    "_GATE_PP_FIELDS",
    "GateTeacher",
    "GateRule",
    "_gate_ratio",
    "GatesSpec",
    "PlayerBlock",
    "StateInitBlock",
    "CourseConfig",
    "_default_lives",
}

ALLOWED_IMPORTS = {
    "__future__",
    "json",
    "math",
    "typing",
    "pydantic",
    "biz.reward_library",
    "biz.course_resolve",  # GatesSpec 校验期的**函数内**延迟导入（见下一条）
    "biz.course",  # CourseConfig.stage_ids 的**函数内**延迟导入（parse_range）
}
TOP_LEVEL_BANNED = {"worker.config", "biz.course_resolve"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _imported_modules(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def _top_level_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_moved_names_are_defined_in_course_spec_and_not_in_config() -> None:
    """定义唯一：搬走的名字只在新家实现（门面只许转发）。"""
    assert _defined(NEW_FILE) == MOVED_NAMES
    leftovers = MOVED_NAMES & _defined(CONFIG_FILE)
    assert leftovers == set(), f"config.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_course_spec_import_surface_is_closed() -> None:
    """★ 依赖面闭集：stdlib + pydantic + reward_library + 一条既知延迟边。"""
    mods = _imported_modules(NEW_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    assert extra == [], f"course_spec 引入了允许面之外的依赖：{extra}"
    assert "worker.config" not in mods, "类面反向 import 门面 ⇒ 与 config → course_spec 成环"


def test_cross_face_call_is_function_local_not_top_level() -> None:
    """★ 唯一反向边必须是**函数内**延迟导入：顶层 import 会与解析面的静态边成环。"""
    tops = _top_level_imports(NEW_FILE)
    hit = sorted(tops & TOP_LEVEL_BANNED)
    assert hit == [], f"顶层出现了成环 import：{hit}（跨课门校验必须是函数内延迟）"


def test_course_facade_forwards_every_moved_name() -> None:
    """门面：每个搬走名都还在 `biz.config`，且与新家是**同一个对象**。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(config_mod, name), f"biz.config 丢了转发名 {name}"
        assert getattr(config_mod, name) is getattr(spec_mod, name), (
            f"biz.config.{name} 不是 biz.course_spec.{name}（转发成了副本）"
        )


# ─────────────────────── ⑤ 契约语义没变（功能性） ───────────────────────


def _teacher() -> dict:
    return {"corpus": "c6b-margin", "games": 100}


def test_gates_spec_rejects_unknown_kind_and_accepts_a_minimal_one() -> None:
    ok = spec_mod.GatesSpec.model_validate(
        {
            "teacher": _teacher(),
            "rules": [
                {"id": "g1", "kind": "wins_mastery", "verdict": "ADVANCE", "rel_teacher": 1.0}
            ],
        }
    )
    assert ok.rules[0].base_verdict == "ADVANCE"
    with pytest.raises(ValueError, match="未知 kind"):
        spec_mod.GatesSpec.model_validate(
            {"teacher": _teacher(), "rules": [{"id": "g", "kind": "nope", "verdict": "STOP"}]}
        )


def test_gates_cross_course_ref_is_resolved_through_the_lazy_edge() -> None:
    """跨课门校验走 `biz.course_resolve._resolve_courses`（既知延迟边）——引用不存在即拒。"""
    with pytest.raises(ValueError, match="找不到"):
        spec_mod.GatesSpec.model_validate(
            {
                "teacher": _teacher(),
                "rules": [
                    {
                        "id": "g3",
                        "kind": "transfer",
                        "verdict": "STOP",
                        "course": "__no_such_course_xyz__",
                    }
                ],
            }
        )


def test_stage_ids_cover_inline_and_custom_stages() -> None:
    assert spec_mod.CourseConfig(name="x").stage_ids == [0, 1, 2, 3]
    grid = [[0] * 13 for _ in range(13)]
    custom = spec_mod.CourseConfig.model_validate({"name": "x", "stages": [{"grid": grid}]})
    assert custom.stage_ids == [spec_mod.CUSTOM_STAGE_BASE]
    js = custom.stage_json(spec_mod.CUSTOM_STAGE_BASE)
    assert js is not None and '"grid"' in js
    assert custom.stage_json(0) is None  # 非自定义关


def test_stage_grid_and_default_lives_contracts() -> None:
    grid = [[0] * 13 for _ in range(13)]
    assert spec_mod.StageSpec(grid=grid).forces == "cccccccccccccccccccc"
    with pytest.raises(ValueError, match="13 行"):
        spec_mod.StageSpec(grid=[[0] * 13])
    assert spec_mod._default_lives("hard") == 3


def test_reward_terminal_keys_are_the_outcome_vocabulary() -> None:
    assert spec_mod.RewardBlock(terminal={"stage_clear": 1.0}).terminal == {"stage_clear": 1.0}
    with pytest.raises(ValueError, match="terminal 键非法"):
        spec_mod.RewardBlock(terminal={"__not_an_outcome__": 1.0})
