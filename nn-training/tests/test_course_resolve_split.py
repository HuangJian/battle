"""拆分的**契约守卫**：课程/关卡解析面永住 `rl/course_resolve.py`（S5 第十刀，2026-09-27）。

`rl/config.py` **1566 → 598 行**（本刀三面合计）；解析面搬走 9 名（`CURRICULA_DIR` ·
`LEVELS_DIR` · `resolve_level` · `load_course` · `resolve_course` · `course_from_args` ·
`resolve_state_init_bank` · `_resolve_courses` · `_LEVEL_ENV_KEYS`，逐字节不动）。

本文件钉五件事：

1. **定义唯一**——9 名不许在 `config.py` 里再实现一遍；
2. **依赖面闭集**——stdlib + `rl.course_spec`；`rl.jsonc` 只准**函数内**延迟导入；
   **不** import `rl.config`（无环）；
3. **模块全局是活的读取点**——`CURRICULA_DIR`/`LEVELS_DIR` 的 monkeypatch 必须打在
   本模块上（门面转发只是同一对象的一份引用，setattr 门面不改变读取点）；
4. **转发同一对象**——门面每个名与新家是 `is`；
5. **契约语义没变**（功能性）：查不到列出可用项 · level 注入 + 课程侧重复声明拒收 ·
   `course_from_args` 冻结字节 / 互斥 / 无参 None。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.config as config_mod
import rl.course_resolve as resolve_mod

CONFIG_FILE = ROOT / "rl" / "config.py"
NEW_FILE = ROOT / "rl" / "course_resolve.py"

MOVED_NAMES = {
    "CURRICULA_DIR",
    "LEVELS_DIR",
    "_resolve_courses",
    "resolve_state_init_bank",
    "resolve_level",
    "_LEVEL_ENV_KEYS",
    "load_course",
    "resolve_course",
    "course_from_args",
}

ALLOWED_IMPORTS = {
    "__future__",
    "pathlib",
    "rl.course_spec",
    "rl.jsonc",  # load_course 的**函数内**延迟导入（读 JSONC 实现）
}
TOP_LEVEL_BANNED = {"rl.config", "rl.jsonc"}


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


def test_moved_names_are_defined_in_course_resolve_and_not_in_config() -> None:
    """定义唯一：搬走的名字只在新家实现（门面只许转发）。"""
    assert _defined(NEW_FILE) == MOVED_NAMES
    leftovers = MOVED_NAMES & _defined(CONFIG_FILE)
    assert leftovers == set(), f"config.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_course_resolve_import_surface_is_closed() -> None:
    """★ 依赖面闭集：stdlib + 类面 + 一条既知延迟边；不反向 import 门面。"""
    mods = _imported_modules(NEW_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    assert extra == [], f"course_resolve 引入了允许面之外的依赖：{extra}"
    assert "rl.config" not in mods, "解析面反向 import 门面 ⇒ 与 config → course_resolve 成环"


def test_lazy_edges_are_function_local_not_top_level() -> None:
    """`rl.jsonc` 只准函数内（顶层会拖重导入）；`rl.config` 任何位置都不准。"""
    tops = _top_level_imports(NEW_FILE)
    hit = sorted(tops & TOP_LEVEL_BANNED)
    assert hit == [], f"顶层出现了不该有的 import：{hit}"


def test_course_resolve_facade_forwards_every_moved_name() -> None:
    """门面：每个搬走名都还在 `rl.config`，且与新家是**同一个对象**。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(config_mod, name), f"rl.config 丢了转发名 {name}"
        assert getattr(config_mod, name) is getattr(resolve_mod, name), (
            f"rl.config.{name} 不是 rl.course_resolve.{name}（转发成了副本）"
        )


# ─────────────────────── ⑤ 契约语义没变（功能性） ───────────────────────


def test_module_globals_are_the_live_read_points(tmp_path, monkeypatch) -> None:
    """★ monkeypatch 打在本模块上必须生效（门面转发不是读取点）——本刀后测试指针的依据。"""
    assert config_mod.CURRICULA_DIR is resolve_mod.CURRICULA_DIR
    tmp = tmp_path / "curricula"
    tmp.mkdir()
    (tmp / "only-here.jsonc").write_text('{"name": "only-here"}', encoding="utf-8")
    monkeypatch.setattr(resolve_mod, "CURRICULA_DIR", tmp)
    assert resolve_mod.resolve_course("only-here") == tmp / "only-here.jsonc"


def test_resolve_course_and_level_list_available_names_on_miss(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(resolve_mod, "CURRICULA_DIR", tmp_path)
    monkeypatch.setattr(resolve_mod, "LEVELS_DIR", tmp_path)
    (tmp_path / "real-course.jsonc").write_text('{"name": "real-course"}', encoding="utf-8")
    (tmp_path / "real-level.jsonc").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="可用"):
        resolve_mod.resolve_course("__nope__")
    with pytest.raises(FileNotFoundError, match="real-level"):
        resolve_mod.resolve_level("__nope__")
    # 路径存在则原样（不再拼目录）
    p = tmp_path / "real-course.jsonc"
    assert resolve_mod.resolve_course(str(p)) == p


def test_load_course_injects_level_keys_and_refuses_duplicates(tmp_path, monkeypatch) -> None:
    levels = tmp_path / "levels"
    levels.mkdir()
    (levels / "arena-x.jsonc").write_text(
        '{"stages": "0-1", "difficulty": "hard", "max_ticks": 12000,'
        ' "player": {"lives": 3}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(resolve_mod, "LEVELS_DIR", levels)

    ok = tmp_path / "lv.jsonc"
    ok.write_text('{"name": "lv", "level": "arena-x"}', encoding="utf-8")
    c = resolve_mod.load_course(ok)
    assert c.difficulty == "hard" and c.player.lives == 3
    assert c.stages_range() == "0,1"  # 关卡注入后解析

    dup = tmp_path / "dup.jsonc"
    dup.write_text(
        '{"name": "dup", "level": "arena-x", "difficulty": "hard"}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="不得再声明"):
        resolve_mod.load_course(dup)


def test_course_from_args_freezes_bytes_and_refuses_mutual_flags(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(resolve_mod, "CURRICULA_DIR", tmp_path)
    (tmp_path / "c1.jsonc").write_text('{"name": "c1"}', encoding="utf-8")

    args = SimpleNamespace(course="c1", course_file="")
    got = resolve_mod.course_from_args(args)
    assert got is not None and got.name == "c1"
    assert args.course_frozen_bytes == (tmp_path / "c1.jsonc").read_bytes()
    assert args.course_path == str(tmp_path / "c1.jsonc")

    with pytest.raises(SystemExit, match="互斥"):
        resolve_mod.course_from_args(SimpleNamespace(course="c1", course_file="x.jsonc"))
    assert resolve_mod.course_from_args(SimpleNamespace(course="", course_file="")) is None


def test_resolve_state_init_bank_accepts_three_bases(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo" / "nn-training"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(resolve_mod, "CURRICULA_DIR", repo / "curricula")
    monkeypatch.chdir(tmp_path)  # cwd 与两个基准都不同 ⇒ 靠仓库根/ nn-training 基准命中
    assert resolve_mod.resolve_state_init_bank("nn-training/data/manifest.json") is not None
    assert resolve_mod.resolve_state_init_bank("data/manifest.json") is not None
    assert resolve_mod.resolve_state_init_bank("") is None
    assert resolve_mod.resolve_state_init_bank("no/such/file.json") is None
