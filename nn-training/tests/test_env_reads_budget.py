"""G13：生产面直读 `os.environ` 的**次数只减不增**（`python.refactor.md` P1-5 第一批）。

## 为什么需要这道闸

`os.getenv` 是零成本接缝，于是「加个开关」永远比「想想这个旋钮该住哪」便宜 —— 130 处直读就是这样
长出来的。真正的病不是「读环境变量」本身，而是**同一根旋钮在几个模块里各解一次默认值**：值碰巧
相同时两处都绿，谁改一边就悄悄分叉（`EVALBOARD_DATA` 的活例：训练侧台账与心跳各解一次，而
`worker/` 不能 import `trainer/`）。已收进 `common/settings.py` 的旋钮在这里被钉成「单家」：
除那个模块以外，任何地方出现它的**字面读法**都红。

## 口径

* 计数 = AST 里 `os.getenv(...)` / `os.environ.get(...)` / `environ.get(...)` / `os.environ[...]`
  四种形态（各算一次）。函数名必须是这三段点分名之一 —— `getenv` 别名不认，见下方「只认显式形态」。
* `os.environ.get(var)` 这种动态键也计数（它照样在直读），但**不参与**单家判据（键不是字面量，
  判不了属主）。
* 扫描面 = 生产包 `biz common hub remote train trainer worker`；`tools/` 与 `worker/scripts/`
  与 G3/G4 同款豁免（一次性脚本不是产品面）。
* **豁免**：`tests.helpers.remote_dag.STANDALONE_BOOT_MODULES` 那四个从 GitHub raw 单独拉取的引导
  文件 —— 它们在 `code.zip` 之前就要能 import，**不许** import `common.*`（各自 docstring 有论证），
  所以旋钮只能在本地解。这是「约束真实」的例外，不是额度。
* 基线在 `tests/golden/env_reads_baseline.json`，按文件冻结；只拦增长，另有一条「基线只许调小」
  防额度回流（与 G3/G4/G6/G9 同形）。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path
from typing import NamedTuple

from common.settings import MIGRATED_ENV
from tests.helpers import source_scan
from tests.helpers.budget import sanity_floor
from tests.helpers.remote_dag import STANDALONE_BOOT_MODULES

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "env_reads_baseline.json"

#: 生产包（`tools/` 与 `worker/scripts/` 不数 —— 与 G3/G4/G9 同款）。
FACE = ("biz", "common", "hub", "remote", "train", "trainer", "worker")

#: 豁免文件（相对 `nn-training/`）：引导期单独拉取，顶层不得 import 仓内。
EXEMPT = frozenset(f"{m.replace('.', '/')}.py" for m in STANDALONE_BOOT_MODULES)

#: 单家判据里「旋钮合法住址」。
SETTINGS_HOME = "common/settings.py"

_GET_CALLS = ("os.getenv", "os.environ.get", "environ.get")
_ENVIRON_DOTTED = ("os.environ", "environ")


def _iter_face_py() -> list[Path]:
    out: list[Path] = []
    for pkg in FACE:
        for p in sorted((ROOT / pkg).rglob("*.py")):
            out.append(p)
    return out


def _literal_key(node: ast.expr) -> str | None:
    """字面量键（`"FOO"`）；动态键返回 `None`。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _scan_file(src: str) -> list[str | None]:
    """一次 AST 里所有 env 直读的键（动态键为 `None`）。注释 / docstring 天然不算。"""
    keys: list[str | None] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            # `dotted_name` 是 `ast.unparse` 的廉价替身（每次 `Call` 都给 `unparse`
            # 做一遍完整格式化，全仓扫描里是秒级开销；2026-11-20 实测 1.9s → 0.5s）。
            if source_scan.dotted_name(node.func) not in _GET_CALLS:
                continue
            keys.append(_literal_key(node.args[0]) if node.args else None)
        elif (
            isinstance(node, ast.Subscript)
            and source_scan.dotted_name(node.value) in _ENVIRON_DOTTED
        ):
            keys.append(_literal_key(node.slice))
    return keys


class Scan(NamedTuple):
    """扫描结果：`counts` 按文件计次数；`keys` 是「旋钮 → 读它的文件」倒排表。"""

    counts: dict[str, int]
    keys: dict[str, list[str]]


@cache
def _scan() -> Scan:
    """按文件计的直读次数 + 「旋钮 → 读它的文件」倒排表（键只收字面读）。"""
    counts: dict[str, int] = {}
    keys: dict[str, list[str]] = {}
    for p in _iter_face_py():
        rel = p.relative_to(ROOT).as_posix()
        if rel in EXEMPT:
            continue
        # 廉价预筛：四种形态都含 `environ` 或 `getenv` 字样（判据充分）。
        text = source_scan.read_text(str(p), "replace")
        if "environ" not in text and "getenv" not in text:
            continue
        found = _scan_file(text)
        if found:
            counts[rel] = len(found)
        for k in found:
            if k is not None:
                keys.setdefault(k, []).append(rel)
    return Scan(counts, {k: sorted(set(v)) for k, v in keys.items()})


@cache
def _baseline() -> dict[str, int]:
    raw: dict[str, dict[str, int]] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return raw["env_reads"]


def test_no_new_env_reads() -> None:
    actual = _scan().counts
    baseline = _baseline()
    lines = [
        f"  {rel}: {n} 处（基线 {baseline.get(rel, 0)}）"
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    ]
    assert not lines, (
        "生产面新增了 `os.environ` 直读：\n"
        + "\n".join(lines)
        + "\n  能进 `common/settings.py` 就进（尤其当这个旋钮已经有人读过：同一份配置两个默认值"
        " = 谁改一边就分叉）；只此一处、就地判的旋钮加到"
        " `tests/golden/env_reads_baseline.json` 对应文件上并在提交信息里说明。"
    )


def test_baseline_only_shrinks() -> None:
    """读得少了必须同步调小基线，否则旧额度会一直被放行。"""
    actual = _scan().counts
    baseline = _baseline()
    lines = [
        f"  {rel}: 基线 {base}，实际 {actual.get(rel, 0)}"
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    ]
    assert not lines, (
        "以下文件的 env 直读已经减少，但 `tests/golden/env_reads_baseline.json` 还是旧数字：\n"
        + "\n".join(lines)
        + "\n  改成实际值（归零的条目直接删掉）。"
    )


def test_scan_floor() -> None:
    """扫描入口自检：AST 没数到东西（walk 坏了 / 包名改了）时立刻红。"""
    total = sum(_scan().counts.values())
    assert total >= sanity_floor(_baseline()), f"只数到 {total} 处 env 直读，扫描入口可能坏了"


def test_migrated_knobs_have_one_home() -> None:
    """已收进 `common/settings.py` 的旋钮：别处不许再出现它的字面读法。"""
    keys = _scan().keys
    offenders = [
        f"  {var}: {rel}" for var in MIGRATED_ENV for rel in keys.get(var, []) if rel != SETTINGS_HOME
    ]
    assert not offenders, (
        "同一个旋钮被两处解读（默认值 / 归一化迟早分叉）：\n"
        + "\n".join(offenders)
        + f"\n  改为 `from common.settings import …`（真家 {SETTINGS_HOME}）。"
    )


def test_scan_counts_only_real_reads() -> None:
    """口径自检：注释 / docstring / 字符串里的字样不算，动态键算一次但不进单家判据。"""
    src = (
        '"""文档里写 os.getenv("FOO") 不算（这是 docstring）。"""\n'
        "import os\n"
        "A = os.getenv('A')\n"
        "B = os.environ.get('B', '0')\n"
        "C = os.environ.get(name)\n"
        "D = os.environ['D']\n"
        "E = 1  # os.getenv('E') 在注释里\n"
        "F = \"os.environ.get('F')\"\n"
    )
    assert _scan_file(src) == ["A", "B", None, "D"]
