"""`remote/` 内部的**全局无环守卫**（2026-09-23，S4 第八刀后补）。

前八刀把「新模块不得反向 import `remote.worker`」这句话在**六个**拆分守卫里各写了一遍，其中三个
还各带一份 `ALLOWED_IMPORTS` / `PROJECT_ROOTS`。本文件把它收成**一处账本 + 一条分层性质**
（账本与判据都在 `tests/helpers/remote_dag.py`，六个拆分守卫改调 `assert_remote_module`）。

钉住的性质：

1. **账本恰好覆盖** `remote/` 下全部生产模块——新增模块不给层号 ⇒ 红；删了还留着 ⇒ 红
   （这条是防「账本腐烂成合法的历史遗留」的那一半）；
2. **顶层图是严格分层的 DAG**：每条顶层 import 边 `LAYERS[src] > LAYERS[dst]`；顶层 SCC 为空。
   顶层环 = 启动即 `ImportError`，**没有豁免**；
3. **延迟图**：每条函数内 import 边同样严格向下，除非落在 `DEFERRED_CYCLES` 声明的环里；
4. **全图的环恰好等于 `DEFERRED_CYCLES`**：多一个红、少一个也红；
5. **环里不许出现顶层边**——「用延迟 import 掩盖循环」是本仓记过一次的手法
   （`tests/test_layering.py` 头部），所以声明过的环必须**仍然是延迟的**；
6. **三个自包含引导模块顶层不得 import 任何 `remote.*`**（`remote/__init__.py` 写着的
   结构例外——它们要从 GitHub raw 单独拉取）——这条以前只是注释，现在机械钉住；
7. **扫描器不许瞎**：解析不出来的 `remote.*` 目标必须为 0（`from <pkg> import <mod>` 与相对
   import 的展开漏了，边就会静默消失——这正是 `tests/test_layering.py` 首版踩过的坑）；
8. **判据自证活性**：用**合成的源码**（不进仓库）跑一遍检测器，证明它能抓住顶层环 / 反向边 /
   未登记模块——否则「全绿」可能只是它什么都没看。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers import remote_dag as dag

TOP, DEFERRED, UNRESOLVED = dag.graph()


# ───────────────────────── ① 账本恰好覆盖 ─────────────────────────


def test_ledger_covers_exactly_every_remote_module() -> None:
    """账本与 `remote/` 下的真模块**双向**对账：多一个少一个都红。"""
    real = set(dag.remote_modules())
    ledger = set(dag.LAYERS)
    missing = sorted(real - ledger)
    stale = sorted(ledger - real)
    assert missing == [], (
        f"这些模块没在 LAYERS 里登记层号 ⇒ 它们的边不会被对账：{missing}\n"
        "  新模块请按「它依赖谁」落在对应层（见 tests/helpers/remote_dag.py 的分层说明）"
    )
    assert stale == [], f"LAYERS 里这些模块已不存在（账本腐烂）：{stale}"


def test_layer_numbers_are_dense_and_meaningful() -> None:
    """层号必须从 0 起连续（`max+1 == 层数`）——出现空洞说明有人手改过层号而没重排。"""
    values = sorted(set(dag.LAYERS.values()))
    assert values == list(range(len(values))), (
        f"层号不连续：{values}——请按拓扑秩重排（`uv run python -c` 见本文件头部说明）"
    )


# ───────────────────── ② 顶层图：严格分层的 DAG ─────────────────────


def test_top_level_graph_is_a_strictly_layered_dag() -> None:
    """顶层每一条边都必须严格向下，且顶层图无环（后者是前者的推论，但独立再报一次更易读）。"""
    top_bad, _ = dag.layering_violations(TOP, {m: set() for m in TOP})
    assert top_bad == [], "顶层边违反分层：\n  " + "\n  ".join(
        f"{src} -> {dst}：{why}" for src, dst, why in top_bad
    )
    assert dag.cycles(TOP) == [], (
        f"顶层图出现环（启动即 ImportError，没有豁免）：{dag.cycles(TOP)}"
    )


def test_deferred_edges_respect_layering_except_declared_cycles() -> None:
    """延迟边同样要向下（延迟 import 不是「可以往回指」的许可），声明过的环除外。"""
    _, deferred_bad = dag.layering_violations({}, DEFERRED)
    assert deferred_bad == [], "延迟边违反分层：\n  " + "\n  ".join(
        f"{src} -> {dst}：{why}" for src, dst, why in deferred_bad
    )


# ─────────────────── ③④⑤ 环：恰好是声明的那一个 ───────────────────


def test_the_only_cycles_are_the_declared_ones() -> None:
    """全图的环**恰好**等于 `DEFERRED_CYCLES`：多一个红，少一个也红。"""
    undeclared, stale = dag.undeclared_cycles(TOP, DEFERRED)
    assert undeclared == [], (
        f"出现未声明的环：{undeclared}\n"
        "  环只能靠「下沉公共依赖」或「函数内延迟 import」两种方式处理；"
        "后者要登记进 tests/helpers/remote_dag.py 的 DEFERRED_CYCLES 并写明理由"
    )
    assert stale == [], (
        f"这些已声明的环其实不存在了（账本腐烂，删掉那行）：{stale}"
    )


def test_declared_cycles_never_contain_a_top_level_edge() -> None:
    """★ 环里**不许**出现顶层边：那才是「用延迟 import 掩盖循环」的反面判据。

    今天唯一被批准的环（`run_loop ⇄ worker`）两边都是函数内 import——`run_loop` 为了顶层保持
    torch-light，`worker` 为了不把编排入口变成宿主的依赖。哪天有人把其中一条提到顶层，
    这条会红：那时要做的不是改账本，而是先把那条边下沉。
    """
    offenders: list[str] = []
    for src, dsts in sorted(TOP.items()):
        for dst in sorted(dsts):
            if dag._in_declared_cycle(src, dst):
                offenders.append(f"{src} -> {dst}")
    assert offenders == [], (
        f"已声明的延迟环里出现了顶层边（顶层 import 参与环 = 真环）：{offenders}"
    )
    # 而且这两个方向的边**确实存在**（防「环是空话」）
    for src, dst in (("remote.run_loop", "remote.worker"), ("remote.worker", "remote.run_loop")):
        assert dst in DEFERRED.get(src, set()), f"{src} -> {dst} 不见了（账本该删那行）"


# ─────────────────── ⑥ 引导模块：顶层零 remote.* ───────────────────


@pytest.mark.parametrize("module", dag.STANDALONE_BOOT_MODULES)
def test_standalone_boot_modules_have_no_top_level_intra_remote_import(module: str) -> None:
    """三个自包含引导模块顶层不得 import 任何 `remote.*`（要从 GitHub raw 独立拉起）。

    延迟 import 允许——那正是「先拉起自己，再拉别人」的实现方式（`notebook_boot._pull` /
    `offline_boot.run_one_course` 都在函数里）。
    """
    top = TOP[module]
    assert top == set(), (
        f"{module} 顶层 import 了其它 remote 模块：{sorted(top)}——"
        "引导模块必须能独立拉取（`remote/__init__.py` 的结构例外）"
    )


def test_tailscale_boot_is_fully_self_contained() -> None:
    """`tailscale_boot` 更严：**连延迟** import 都不许有（它是最早被拉起的那一个）。"""
    module = "remote.tailscale_boot"
    assert DEFERRED[module] == set(), sorted(DEFERRED[module])


# ─────────────────── ⑦ 扫描器不许瞎 ───────────────────


def test_no_intra_remote_import_target_is_unresolved() -> None:
    """解析不出来的 `remote.*` 目标必须为 0——否则那条边对判据是**不可见**的。

    `from <pkg> import <mod>` 与相对 import 都要正确展开；漏了就会静默丢边
    （`tests/test_layering.py` 首版正是这么瞎的）。
    """
    assert UNRESOLVED == {}, (
        f"这些 remote.* 目标解析不到模块（判据会看不见这条边）：{UNRESOLVED}"
    )


def test_scanner_sees_every_module_it_is_given() -> None:
    """扫描器至少要为**每个**模块产出条目（防「某个模块被静默跳过」）。"""
    real = set(dag.remote_modules())
    assert set(TOP) == real and set(DEFERRED) == real
    # 且确实扫到了边（全零 = 扫描器瞎了）
    assert sum(len(v) for v in TOP.values()) > 30


def test_the_graph_matches_a_second_independent_scan() -> None:
    """**独立重实现对账**（本仓对 codec/数据的老规矩）：另写一遍只扫**顶层 body** 的展开器，
    与账本逐项相同——防 `_collect` 的递归/相对import 解析悄悄漂移。

    这里必须显式展开 `from remote import <mod>`（`remote/http.py` 等 5 处就是这么写的）：
    只取 `node.module` 会得到裸 `remote`，那条边就**看不见**了——当场的第一次运行就抓到了
    这个不对称（它正是本仓记过的那类判据盲点）。
    """
    import ast as _ast

    known = set(dag.LAYERS)
    for module, path in dag.remote_modules().items():
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        seen: set[str] = set()
        for node in tree.body:  # 只看顶层
            if isinstance(node, _ast.Import):
                seen |= {a.name for a in node.names if a.name.startswith("remote.")}
            elif isinstance(node, _ast.ImportFrom) and not node.level and node.module:
                for a in node.names:
                    base = "remote" if node.module == "remote" else node.module
                    seen.add(f"{base}.{a.name}")
                seen.add(node.module)
        seen = {s for s in seen if s in known} - {module}
        assert seen == TOP[module], f"{module}: 两次扫描不一致 {sorted(seen ^ TOP[module])}"


# ─────────────────── ⑧ 判据自证活性（合成源码） ───────────────────


def _synth(tmp_path: Path, files: dict[str, str], monkeypatch) -> None:
    """把合成源码铺成 `tmp_path/remote/*.py` 并让账本指向它。"""
    remote = tmp_path / "remote"
    remote.mkdir()
    for name, src in files.items():
        (remote / name).write_text(src, encoding="utf-8")
    monkeypatch.setattr(dag, "REMOTE_DIR", remote)


def test_detector_catches_a_top_level_cycle(tmp_path: Path, monkeypatch) -> None:
    """合成一对互相顶层 import 的模块 ⇒ 检测器必须报环（并指出是哪两个）。"""
    _synth(
        tmp_path,
        {
            "a.py": "from remote.b import z\n",
            "b.py": "from remote.a import y\n",
        },
        monkeypatch,
    )
    monkeypatch.setattr(dag, "LAYERS", {"remote.a": 0, "remote.b": 0})
    top, deferred, _ = dag.graph()
    assert dag.cycles(top) == [["remote.a", "remote.b"]], dag.cycles(top)
    top_bad, _ = dag.layering_violations(top, deferred)
    assert [b[:2] for b in top_bad] == [("remote.a", "remote.b"), ("remote.b", "remote.a")]


def test_detector_catches_an_upward_deferred_edge(tmp_path: Path, monkeypatch) -> None:
    """合成一条「向上指的延迟边」（不在声明环里）⇒ 必须红，且理由里带上层号。"""
    _synth(
        tmp_path,
        {
            "low.py": "def f():\n    from remote.high import g\n    return g\n",
            "high.py": "def g():\n    return 1\n",
        },
        monkeypatch,
    )
    monkeypatch.setattr(dag, "LAYERS", {"remote.low": 0, "remote.high": 3})
    top, deferred, _ = dag.graph()
    _, deferred_bad = dag.layering_violations(top, deferred)
    assert [b[:2] for b in deferred_bad] == [("remote.low", "remote.high")]
    assert "L0 → L3" in deferred_bad[0][2]


def test_detector_ignores_an_edge_the_ledger_declares_as_a_cycle(
    tmp_path: Path, monkeypatch
) -> None:
    """声明过的环不算违规——但必须**两边都在函数里**（否则顶层那条会单独被抓，见上）。"""
    _synth(
        tmp_path,
        {
            "x.py": "def go():\n    from remote.y import f\n",
            "y.py": "def back():\n    from remote.x import g\n",
        },
        monkeypatch,
    )
    monkeypatch.setattr(dag, "LAYERS", {"remote.x": 1, "remote.y": 0})
    monkeypatch.setattr(
        dag, "DEFERRED_CYCLES", {frozenset({"remote.x", "remote.y"}): "合成探针"}
    )
    top, deferred, _ = dag.graph()
    top_bad, deferred_bad = dag.layering_violations(top, deferred)
    assert top_bad == [] and deferred_bad == []
    undeclared, stale = dag.undeclared_cycles(top, deferred)
    assert undeclared == [] and stale == []


def test_detector_catches_a_module_the_ledger_forgot(tmp_path: Path, monkeypatch) -> None:
    """合成一个新模块而账本没登记 ⇒ 双向对账必须报「缺登记」。"""
    _synth(tmp_path, {"new_leaf.py": "import os\n"}, monkeypatch)
    monkeypatch.setattr(dag, "LAYERS", {"remote.somewhere": 0})
    real = set(dag.remote_modules())
    assert sorted(real - set(dag.LAYERS)) == ["remote.new_leaf"]
    assert sorted(set(dag.LAYERS) - real) == ["remote.somewhere"]


def test_detector_does_not_see_a_relative_import_as_nothing(tmp_path: Path, monkeypatch) -> None:
    """★ 相对 import 必须被展开（否则边静默消失）：`from . import b` 要算 `remote.pkg.b`。"""
    remote = tmp_path / "remote"
    (remote / "pkg").mkdir(parents=True)
    (remote / "pkg" / "a.py").write_text("from . import b\nfrom .b import thing\n", encoding="utf-8")
    (remote / "pkg" / "b.py").write_text("thing = 1\n", encoding="utf-8")
    monkeypatch.setattr(dag, "REMOTE_DIR", remote)
    monkeypatch.setattr(dag, "LAYERS", {"remote.pkg.a": 1, "remote.pkg.b": 0})
    top, _, unresolved = dag.graph()
    assert top["remote.pkg.a"] == {"remote.pkg.b"}, top
    assert unresolved == {}
