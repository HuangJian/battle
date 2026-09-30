"""入口脚本「在自家目录里能跑起来」的结构性守卫（2026-09-30 刀 7）。

**为什么需要这条**：刀 7 把 14 个入口从 `nn-training/` 顶层搬进各自的包（`trainer/` ·
`tools/` · `remote/` · `worker/`）。搬完有一个**只在真机启动时才现形**的坑：脚本模式下
`sys.path[0]` 是**脚本目录**（不再是 nn-training），于是

  ① `from common.… import …` 这类仓内顶层导入全部找不到（脚本起不来）；
  ② `trainer/` 里那个 `queue.py` 会遮蔽 stdlib `queue` —— `concurrent.futures` 一导入就
     循环炸（`trainer/eval_a_once.py` 早在 2026-09 就踩过，惯用法写在它文件头）。

修法是每个入口前置「摘掉脚本目录项 + 放回 nn-training 根」。本文件把「修好了」变成可执行
断言：**每个入口都以「脚本方式」真起一次**（`python <path> --help` / `python -m <mod>
--help`），退出码 0 且无 Traceback。单元测试里没有人这样起过它们（用例都用
`importlib`/`-c` 加载模块，`sys.path[0]` 不是脚本目录），所以这条是唯一能挡住「搬了家但
前置没跟上」的守卫。

顺带钉住两件事：
  · `nn-training/` 顶层只剩 `conftest.py`（入口都住包里 —— 有人往顶层丢入口即红）；
  · 两个 `remote/` 入口的**模块名**（`-m remote.remote_worker[_serve]`）—— 控制台
    `specs.ts` / `push.ts` 与 Kaggle notebook 都照这个名字起进程，写错 = 启动器报
    `No module named`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.subproc_util import run_utf8

NN_ROOT = Path(__file__).resolve().parent.parent

#: 按**文件路径**启动的入口（脚本模式 ⇒ 走各自文件头的 sys.path 前置）。
FILE_ENTRIES = (
    "trainer/run_rl.py",
    "trainer/run_bc.py",
    "trainer/run_rl_cluster.py",
    "trainer/train_loop.py",
    "trainer/eval_course_once.py",
    "trainer/eval_m1_once.py",
    "tools/task.py",
    "tools/bootstrap.py",
    "tools/weights_prune.py",
)

#: 按**模块名**启动的入口（`-m`；名字是控制台/notebook 的契约面）。
MODULE_ENTRIES = ("remote.remote_worker", "remote.remote_worker_serve")


def _spawn(argv: list[str]) -> None:
    out = run_utf8(argv, cwd=str(NN_ROOT), timeout=120)
    assert out.returncode == 0, f"{argv} 退出码 {out.returncode}\nstderr:\n{out.stderr[-2000:]}"
    assert "Traceback" not in out.stderr, f"{argv} 抛了栈：\n{out.stderr[-2000:]}"


def test_nn_training_top_level_has_no_entry_scripts() -> None:
    """`nn-training/` 顶层只留 `conftest.py`（刀 7：入口全部住包）。"""
    tops = sorted(p.name for p in NN_ROOT.glob("*.py"))
    assert tops == ["conftest.py"], f"顶层还有其他 .py：{tops}（入口该住包，见刀 7）"


def test_file_entries_run_as_scripts_from_the_repo_root() -> None:
    """`python nn-training/<入口> --help` 必须起得来（脚本目录遮蔽 stdlib 的坑就死在这里）。"""
    missing = [e for e in FILE_ENTRIES if not (NN_ROOT / e).is_file()]
    assert missing == [], f"入口不在约定位置：{missing}"
    for rel in FILE_ENTRIES:
        _spawn([sys.executable, str(NN_ROOT / rel), "--help"])


def test_module_entries_are_importable_by_their_contract_names() -> None:
    """`-m remote.remote_worker[_serve]`：控制台与 notebook 照这个名字起进程。"""
    for mod in MODULE_ENTRIES:
        _spawn([sys.executable, "-m", mod, "--help"])
