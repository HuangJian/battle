"""**门面的转发名不得是死名**（P1-11 的「转发名」半边，全仓口径）。

拆分搬家的纪律是「旧家留 `from X import (name as name)`，名字对外不变」。代价是：**名字搬走、
调用方也迁走了，别名却一直留着**。两个真实例子：

* `remote/job_fs._git_head` —— 全仓零调用（`remote/code_snapshot.py` 有自己的、防 Windows
  闪窗的版本），只因 `remote/worker.py` 的转发名还在就没人删，直到有人差点给它加日志。
* `common/protocol.GOAL_SCRIPT` / `INTENT_SCRIPT` —— 定义模块自己还在用（`common/manifest.py`
  的词表），但门面上这两个别名没有任何读者。

判据：对每个含转发块的文件 F、每个转发名 N（定义为模块 M）—— N 必须还活在
（a）F 自己剔掉声明行之后的正文里，或（b）F 与 M 之外任一 `.py` 里。否则判红，提示删掉别名
（或加进 `ALLOWED` 并写理由 —— 今天为空）。

只扫 `.py`：只在注释/文档里出现的名字不算活 —— 那是「删了顺手改注释」的活，不是保留的理由。
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

from tests.helpers.budget import sanity_floor_of

ROOT = Path(__file__).resolve().parents[1]

#: 扫描面：仓库内 `.py`（不含虚拟环境/权重/临时目录/前端）。`dashboard` 是独立 bun 项目，
#: 不消费 python 名字。
SKIP_PARTS = frozenset(
    {".venv", "venv", "tmp", "weights", "ipynb", "__pycache__", "node_modules", "dashboard"}
)

#: 外部消费者（仓库外）仍可能在用、所以必须留着的 `(文件, 名字)`。**今天为空** —— 加要写理由。
ALLOWED: frozenset[tuple[str, str]] = frozenset()

#: 转发**声明行**（`    name as name,`）：只此一种形状，剥掉后剩下的才是真引用。
_FORWARD_DECL = re.compile(r"^\s*(\w+) as \1,\s*$", re.MULTILINE)

#: 标识符（判活用的倒排索引键）。
_IDENT = re.compile(r"\w+")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


#: 本文件自己：它在说明里点名了几个死名（示例），**不能算作「还有人引用」的证据** ——
#: 否则每点名一个名字，那条门禁就自己把自己弄绿（第一版正是这么栽的）。
SELF = Path(__file__).resolve()


@cache
def _py_files() -> tuple[Path, ...]:
    return tuple(
        p
        for p in sorted(ROOT.rglob("*.py"))
        if not SKIP_PARTS.intersection(p.parts) and p.resolve() != SELF
    )


@cache
def _texts() -> dict[str, str]:
    """`{相对路径: 全文}`（读一次，三个用例共用 —— 全仓 `.py` 约 30MB）。"""
    return {_rel(p): p.read_text(encoding="utf-8", errors="replace") for p in _py_files()}


@cache
def _index() -> dict[str, frozenset[str]]:
    """`{标识符: 出现它的文件集合}` —— 一次分词建好，逐名字判活就从 O(文件数) 变成 O(1)。"""
    idx: dict[str, set[str]] = {}
    for rel, text in _texts().items():
        for tok in _IDENT.findall(text):
            idx.setdefault(tok, set()).add(rel)
    return {k: frozenset(v) for k, v in idx.items()}


def _forwards(path: Path) -> dict[str, str]:
    """`{名字: 定义模块的相对路径}` —— 只认 `name as name`（改名转发不是本条管辖）。"""
    out: dict[str, str] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        mod = _rel(ROOT / (node.module.replace(".", "/") + ".py"))
        for alias in node.names:
            if alias.asname == alias.name:
                out[alias.name] = mod
    return out


@cache
def _facades() -> dict[str, dict[str, str]]:
    """`{门面文件: {名字: 定义模块}}`；不到 3 个转发名的不算门面（`as` 别名偶发现象）。"""
    out: dict[str, dict[str, str]] = {}
    for p in _py_files():
        fwd = _forwards(p)
        if len(fwd) >= 3:
            out[_rel(p)] = fwd
    return out


@cache
def _own_uses(facade: str) -> str:
    """门面自身剔掉 `NAME as NAME,` 声明行之后的正文（免得「在 import 里提到过自己」判活）。"""
    return _FORWARD_DECL.sub("", _texts()[facade])


def _dead() -> dict[tuple[str, str], str]:
    """`{(门面, 名字): 定义模块}` —— 只在转发声明和定义模块里出现过的名字。"""
    idx = _index()
    dead: dict[tuple[str, str], str] = {}
    for facade, fwd in _facades().items():
        for name, mod in fwd.items():
            if (facade, name) in ALLOWED:
                continue
            if name in _own_uses(facade):
                continue
            if any(f not in (facade, mod) for f in idx.get(name, ())):
                continue
            dead[(facade, name)] = mod
    return dead


def test_the_forward_table_is_not_empty() -> None:
    """扫描入口自检：门面数与转发名总数都有地板（解析器坏了 ⇒ 空表 ⇒ 下面的门禁恒绿）。"""
    facades = _facades()
    total = sum(len(v) for v in facades.values())
    assert len(facades) >= sanity_floor_of(15), f"只解析到 {len(facades)} 个门面文件"
    assert total >= sanity_floor_of(300), f"只解析到 {total} 个转发名"


def test_every_forwarded_name_is_still_used() -> None:
    dead = _dead()
    pretty = {f"{f}:{n}": m for (f, n), m in sorted(dead.items())}
    assert dead == {}, (
        "以下转发名在整个仓库的 `.py` 里已经没有任何引用（门面自身与定义模块除外）——"
        f"名字搬走、调用方也迁走后别名就该删：{pretty}\n"
        "  确实有仓库外的消费者时，把 (文件, 名字) 加进本文件的 ALLOWED 并写明理由。"
    )


def test_a_known_live_forward_is_recognised() -> None:
    """口径自检：这几个名字有真实调用方，必须被判成「活着」（否则上面的门禁恒绿）。"""
    facades = _facades()
    for facade, name in (
        ("remote/worker.py", "JOB_DIR_KEEP"),
        ("remote/worker.py", "pack_opt_tar"),
        ("common/protocol.py", "ProtocolError"),
    ):
        assert name in facades[facade], f"{facade} 的转发表里没有 {name}（表解析只读到半截？）"
        mod = facades[facade][name]
        assert any(f not in (facade, mod) for f in _index().get(name, ())), (
            f"{name} 被判成死名了 —— 它有真实调用方"
        )
