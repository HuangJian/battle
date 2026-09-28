"""拆分的**契约守卫**：进程内权重下发账本永住 `dist_weights_ledger.py`（S5 第十一刀，2026-09-27）。

`dist_common.py` **1503 → 1373 行**；搬走**两段跨度共 6 名（逐字节不动）**：
`_WEIGHTS_PUSHED`（键 `(kind, wver)` → 成功 POST 过的 node id）· `weights_push_cache_reset` ·
`note_weights_pushed` · `forget_weights_node` · `weights_already_pushed` · `partition_weights_nodes`。

本文件钉五件事：

1. **定义唯一**——搬走名不许在 `dist_common.py` 里再实现一遍；
2. **依赖面闭集**——零仓内依赖（连 stdlib 都不用）；**不** import `dist_common`（无环）；
3. **门面对象恒等 + 账本是同一个 dict 对象**（两模块共享状态，不是副本）；
4. **契约语义没变**（功能性）：kind 分桶 · note 幂等 · forget 范围与计数 · partition 拆 reuse/need；
5. **模块全局是活读取点**——驻 `dist_common` 的调用方（`post_weights_parallel` / `refresh_weights`）
   与 `rl.*` 的既有调用点读的都是**门面**的全局 ⇒ 打桩必须打在门面上（打新家是静默空操作）。
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dist_common as common_mod
import dist_weights_ledger as ledger_mod
from tests.helpers import source_scan

LEDGER_FILE = ROOT / "dist_weights_ledger.py"
COMMON_FILE = ROOT / "dist_common.py"

MOVED_NAMES = {
    "_WEIGHTS_PUSHED",
    "weights_push_cache_reset",
    "note_weights_pushed",
    "forget_weights_node",
    "weights_already_pushed",
    "partition_weights_nodes",
}

ALLOWED_IMPORTS = {"__future__"}


@pytest.fixture(autouse=True)
def _clean_ledger() -> Iterator[None]:
    """账本是**进程内跨用例**状态：进出一律清零（否则断言退化成对用例顺序的依赖）。"""
    ledger_mod.weights_push_cache_reset()
    yield
    ledger_mod.weights_push_cache_reset()


def _tree(path: Path) -> ast.Module:
    return source_scan.parse(str(path))


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


def test_moved_names_are_defined_in_the_ledger_and_not_in_dist_common() -> None:
    """定义唯一：搬走的名字只在新家实现（原家只留门面转发）。"""
    assert _defined(LEDGER_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(LEDGER_FILE))
    leftovers = MOVED_NAMES & _defined(COMMON_FILE)
    assert leftovers == set(), f"dist_common.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_ledger_has_no_project_imports() -> None:
    """★ 依赖面闭集：连 stdlib 都不需要；**不得** import `dist_common`（门面反向 ⇒ 成环）。"""
    mods = _imported_modules(LEDGER_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    assert extra == [], f"dist_weights_ledger.py 引入了依赖：{extra}"
    assert "dist_common" not in mods, "账本反向 import 了门面 ⇒ 顶层互引成环"


def test_dist_common_forwards_every_moved_name() -> None:
    """门面：每个搬走名都还在 `dist_common`，且与新家是**同一个对象**；源码里是 `X as X`。"""
    src = source_scan.read_text(str(COMMON_FILE))
    assert "from dist_weights_ledger import (" in src
    for name in sorted(MOVED_NAMES):
        assert hasattr(common_mod, name), f"dist_common 丢了转发名 {name}"
        assert getattr(common_mod, name) is getattr(ledger_mod, name), (
            f"dist_common.{name} 不是 dist_weights_ledger.{name}（转发成了副本）"
        )
        assert f"    {name} as {name},\n" in src, f"dist_common 的转发不是自别名形态：{name}"


# ─────────────────────── ⑤ 契约语义没变（功能性） ───────────────────────


def test_note_is_idempotent_and_kind_scoped() -> None:
    """同 (kind, wver) 的重复 note 幂等；不同 kind 各自成桶（rollout / eval 互不驱逐）。"""
    ledger_mod.note_weights_pushed("w1", "a97", kind="rollout")
    ledger_mod.note_weights_pushed("w1", "a97", kind="rollout")  # 幂等
    ledger_mod.note_weights_pushed("w1", "a97", kind="eval")
    assert ledger_mod.weights_already_pushed("w1", "a97", kind="rollout") is True
    assert ledger_mod.weights_already_pushed("w1", "a97", kind="eval") is True
    assert ledger_mod.weights_already_pushed("w1", "mac", kind="rollout") is False
    assert ledger_mod.weights_already_pushed("w2", "a97", kind="rollout") is False
    # 空 wver / 空 node id 不入账（脏键会让后来真实的下发被误判 reuse ⇒ 整轮 409）
    ledger_mod.note_weights_pushed("", "a97")
    ledger_mod.note_weights_pushed("w1", "")
    assert ledger_mod.weights_already_pushed("", "a97") is False
    assert ledger_mod.weights_already_pushed("w1", "") is False


def test_forget_scope_and_counts() -> None:
    ledger_mod.note_weights_pushed("w1", "a97", kind="rollout")
    ledger_mod.note_weights_pushed("w1", "a97", kind="eval")
    ledger_mod.note_weights_pushed("w1", "mac", kind="rollout")

    assert ledger_mod.forget_weights_node("a97", kind="rollout") == 1  # 只摘那一条腿
    assert ledger_mod.weights_already_pushed("w1", "a97", kind="eval") is True
    assert ledger_mod.weights_already_pushed("w1", "mac", kind="rollout") is True  # 不误伤别的节点

    assert ledger_mod.forget_weights_node("a97") == 1  # 缺省 = 该节点全 kind
    assert ledger_mod.weights_already_pushed("w1", "a97", kind="eval") is False
    assert ledger_mod.forget_weights_node("a97") == 0  # 再摘一次 = 0 条
    assert ledger_mod.forget_weights_node("") == 0  # 空 id 不炸


def test_partition_splits_reuse_and_need_per_kind() -> None:
    """reuse/need 与账本同一判据：缺省 kind='rollout'；id 缺省回落 url；保序且无遗漏。"""
    nodes: list = [{"id": "a"}, {"url": "http://b"}, {}]
    reuse, need = ledger_mod.partition_weights_nodes(nodes, "w1")
    assert reuse == [] and need == nodes  # 冷账本：全 need

    ledger_mod.note_weights_pushed("w1", "a", kind="rollout")
    ledger_mod.note_weights_pushed("w1", "http://b", kind="eval")
    reuse, need = ledger_mod.partition_weights_nodes(nodes, "w1", kind="rollout")
    assert reuse == [nodes[0]], "eval 桶的记账不得让 rollout 腿误判 reuse"
    assert need == nodes[1:]

    reuse, need = ledger_mod.partition_weights_nodes(nodes, "w1", kind="eval")
    assert reuse == [nodes[1]]
    assert need == [nodes[0], nodes[2]]
    assert {id(nd) for nd in [*reuse, *need]} == {id(nd) for nd in nodes}  # 无遗漏、无重复


def test_the_two_modules_share_one_ledger_object() -> None:
    """★ 不是副本：门面记的账，新家看得见；新家忘的账，门面也看得见。"""
    assert common_mod._WEIGHTS_PUSHED is ledger_mod._WEIGHTS_PUSHED
    common_mod.weights_push_cache_reset()
    common_mod.note_weights_pushed("w1", "a97")
    assert ledger_mod.weights_already_pushed("w1", "a97") is True
    assert ledger_mod.forget_weights_node("a97") == 1
    assert common_mod.weights_already_pushed("w1", "a97") is False
    common_mod.weights_push_cache_reset()
    assert ledger_mod._WEIGHTS_PUSHED == {}


def test_patching_the_facade_reaches_its_in_module_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 活读取点：`post_weights_parallel` 读的是 `dist_common` 的全局。

    驻本模块的调用方（`post_weights_parallel` / `refresh_weights`）与 `rl.*` 的既有调用点
    都按门面解析名字 ⇒ 打桩打门面才生效（打 `dist_weights_ledger` 是静默空操作——
    S5 第七/十一刀同款坑，patch 目标随实现走）。
    """
    seen: list[tuple] = []
    monkeypatch.setattr(common_mod, "note_weights_pushed", lambda *a, **k: seen.append((a, k)))
    monkeypatch.setattr(common_mod, "post_weights", lambda *a, **k: "kept")
    nd = {"id": "n1", "url": "http://n1"}

    ok = common_mod.post_weights_parallel([nd], "it1", "ab" * 32, b"{}", timeout=5.0)

    assert ok == [nd]
    assert seen == [(("ab" * 32, "n1"), {"kind": "rollout"})], seen
