"""拆分的**契约守卫**：rl-config.json 文件面永住 `biz/config_file.py`（S5 第十刀，2026-09-27）。

`biz/config.py` **1566 → 598 行**（本刀三面合计）；文件面搬走 `RL_CONFIG_ENV` ·
`rl_config_path` · `read_rl_config_file`（逐字节不动）。

本文件钉四件事：

1. **定义唯一**——三名不许在 `config.py` 里再实现一遍；
2. **依赖面闭集**——只准 stdlib（`json`/`pathlib`/`typing`）+ `common.distribution`；**不** import `rl.*`；
3. **转发同一对象**——门面每个名与新家是 `is`；
4. **契约语义没变**（功能性）：路径唯一来源 = `common.distribution.rl_config_path()`（env
   `BCITY_RL_CONFIG` 可重定向）· 读不到 / 顶层不是 dict / 坏 JSON ⇒ 空 dict（等价「没配旋钮」）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.distribution
import worker.config as config_mod
import worker.config_file as file_mod

CONFIG_FILE = ROOT / "worker" / "config.py"
NEW_FILE = ROOT / "worker" / "config_file.py"

MOVED_NAMES = {"RL_CONFIG_ENV", "read_rl_config_file", "rl_config_path"}

ALLOWED_IMPORTS = {"__future__", "json", "pathlib", "typing", "common.distribution"}


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


def test_moved_names_are_defined_in_config_file_and_not_in_config() -> None:
    """定义唯一：搬走的名字只在新家实现（门面只许转发）。"""
    assert _defined(NEW_FILE) == MOVED_NAMES
    leftovers = MOVED_NAMES & _defined(CONFIG_FILE)
    assert leftovers == set(), f"config.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_config_file_import_surface_is_closed() -> None:
    """★ 依赖面闭集：stdlib + `common.distribution`；**不得** import `trainer.*`（反向成环）。"""
    mods = _imported_modules(NEW_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    assert extra == [], f"common 之外的依赖：{extra}"
    tops = {m.split(".")[0] for m in mods}
    assert "trainer" not in tops, "config_file 反向 import 了编排树模块"
    assert "rl" not in tops, "rl 包已不存在（2026-09-30 刀 5 改名 trainer）——这行只留着接住旧名回来"


def test_config_facade_forwards_every_moved_name() -> None:
    """门面：每个搬走名都还在 `biz.config`，且与新家是**同一个对象**。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(config_mod, name), f"biz.config 丢了转发名 {name}"
        assert getattr(config_mod, name) is getattr(file_mod, name), (
            f"biz.config.{name} 不是 biz.config_file.{name}（转发成了副本）"
        )


def test_rl_config_path_is_the_single_redirection_point(tmp_path, monkeypatch) -> None:
    """路径唯一来源：env `BCITY_RL_CONFIG` 一改，两处读取点同步（委托 common.distribution）。"""
    fixture = tmp_path / "rl-config.fixture.json"
    fixture.write_text('{"rl": {"stream": 0}}', encoding="utf-8")
    monkeypatch.setenv(file_mod.RL_CONFIG_ENV, str(fixture))
    assert file_mod.rl_config_path() == fixture
    assert file_mod.rl_config_path() == Path(common.distribution.rl_config_path())
    assert file_mod.read_rl_config_file()["rl"]["stream"] == 0


def test_read_rl_config_file_degrades_to_empty_dict_on_bad_inputs(
    tmp_path, monkeypatch
) -> None:
    """读不到 / 顶层不是 dict / 坏 JSON 一律空 dict——「没配旋钮」同一个结果。"""
    p = tmp_path / "rl-config.json"
    monkeypatch.setenv(file_mod.RL_CONFIG_ENV, str(p))
    assert file_mod.read_rl_config_file() == {}  # 不存在
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert file_mod.read_rl_config_file() == {}  # 顶层不是 dict
    p.write_text("{ 坏 json", encoding="utf-8")
    assert file_mod.read_rl_config_file() == {}  # 坏 JSON


def test_read_rl_config_file_returns_dict_verbatim(tmp_path, monkeypatch) -> None:
    p = tmp_path / "rl-config.json"
    p.write_text('{"courses": {"c5-tick": {"workers": 4}}}', encoding="utf-8")
    monkeypatch.setenv(file_mod.RL_CONFIG_ENV, str(p))
    assert file_mod.read_rl_config_file() == {
        "courses": {"c5-tick": {"workers": 4}}
    }
