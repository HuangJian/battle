"""`remote/worker.py` 的**转发名不得是死名**（P1-11 的「转发名」半边）。

`worker.py` 在历次拆分后靠 `from remote.X import (name as name)` 保持对外名字不变（656 处私有
断言 + 大量 `worker_mod.name` 的测试接缝都靠它）。这条纪律的代价是：**名字搬走了、调用方也迁走
了，别名却一直留着** —— 2026-11-15 的 `remote/job_fs._git_head` 就是这样：它自己的
`git rev-parse` 包装全仓零调用（`remote/code_snapshot.py` 有自己的、防 Windows 闪窗的版本），
却因为 worker 的转发名活着而一直存在，直到有人差点给它加日志才发现该删。

判据：每个转发名在**除定义模块与它自己那行 import 之外**的 `.py` 里还至少被提到一次。
只用 `.py` 面（不留情面地看代码）：只在注释/文档里出现的名字不算活 —— 那是「删了再改注释」
的活，不是「必须留着」的理由。

反过来的漏不是这条管的：转发一个源模块里并不存在的名字，`import` 当场就炸。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.helpers.budget import sanity_floor_of

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "remote" / "worker.py"

#: 扫描面：仓库内 `.py`（不含虚拟环境/权重/临时目录/前端）。`dashboard` 是独立 bun 项目，
#: 不消费 python 名字。
SKIP_PARTS = frozenset(
    {".venv", "venv", "tmp", "weights", "ipynb", "__pycache__", "node_modules", "dashboard"}
)

#: 外部消费者（仓库外）仍可能在用、所以必须留着的名字。**今天为空** —— 加名字要写理由。
ALLOWED: frozenset[str] = frozenset()

#: 转发**声明行**（`    name as name,`）：只此一种形状，剥掉后剩下的才是真引用。
_FORWARD_DECL = re.compile(r"^\s*(\w+) as \1,\s*$", re.MULTILINE)


def _forwarded() -> dict[str, str]:
    """`{名字: 定义模块的路径}` —— 只认 `name as name` 这种转发（`as` 改了名的不算）。"""
    out: dict[str, str] = {}
    for node in ast.parse(WORKER.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        mod_path = ROOT / (node.module.replace(".", "/") + ".py")
        for alias in node.names:
            if alias.asname == alias.name:
                out[alias.name] = mod_path.as_posix()
    return out


def _py_texts() -> dict[str, str]:
    """仓库内 `.py` 的全文，**含 `worker.py` 本身但剔掉它的转发行**。

    为什么留着 `worker.py`：不少转发名是给它自己用的（`PPO_BACKENDS` 喂 argparse、
    `YIELD_AFTER_SEC_DEFAULT` 喂让路判据）—— 那些是活的。要剔的只有 `NAME as NAME,` 这一种
    声明行，否则每个名字都会「在 import 里提到过自己」而永远判活。
    """
    out: dict[str, str] = {}
    for p in ROOT.rglob("*.py"):
        if SKIP_PARTS.intersection(p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if p == WORKER:
            text = _FORWARD_DECL.sub("", text)
        out[p.as_posix()] = text
    return out


def test_the_forward_table_is_not_empty() -> None:
    """扫描入口自检：名字数有地板（解析器坏了 ⇒ 空表 ⇒ 下面那条恒绿）。"""
    forwarded = _forwarded()
    assert len(forwarded) >= sanity_floor_of(100), f"只解析到 {len(forwarded)} 个转发名"


def test_every_forwarded_name_is_still_used() -> None:
    forwarded = _forwarded()
    texts = _py_texts()
    dead: dict[str, str] = {}
    for name, mod_path in forwarded.items():
        if name in ALLOWED:
            continue
        if not any(name in t for p, t in texts.items() if p != mod_path):
            dead[name] = mod_path
    assert dead == {}, (
        "以下转发名在整个仓库的 `.py` 里已经没有任何引用（定义模块自身除外）——"
        f"名字搬走、调用方也迁走后别名就该删：{sorted(dead)}\n"
        "  确实有仓库外的消费者时，把名字加进本文件的 ALLOWED 并写明理由。"
    )


@pytest.mark.parametrize("name", ["JOB_DIR_KEEP", "pack_opt_tar"])
def test_a_known_live_forward_is_recognised(name: str) -> None:
    """口径自检：这两个名字有真实调用方，必须被判成「活着」（否则上面的门禁恒绿）。"""
    forwarded = _forwarded()
    assert forwarded.get(name), f"{name} 不在转发表里 —— 表解析可能只读到了半截"
    texts = _py_texts()
    assert any(name in t for p, t in texts.items() if p != forwarded[name])
