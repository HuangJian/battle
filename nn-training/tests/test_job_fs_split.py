"""拆分的**契约守卫**：作业工作区 / TAR / git 物化永住 `remote/job_fs.py`（S4 第六步之一，2026-09-23）。

搬走的组：`REPO_ROOT` · `JOB_DIR_KEEP` · `_persist_result` · `prune_job_dirs` · `unpack_opt_tar` ·
`pack_opt_tar` · `unpack_payload_or_fail` · `_git_head`。

本文件钉五件事：

1. **定义唯一**——不许在 `worker.py` 里再实现一遍；
2. **无环**——`job_fs.py` 不得 import `worker.py`（`worker` 已经 import 它做转发）；
3. **转发同一对象**；
4. **`REPO_ROOT` 仍指向 nn-training 根**（它是**由 `__file__` 推导**的常量，换目录后必须推导出
   同一个值——这是搬这类常量唯一会出的错，而且静默）；
5. **无 monkeypatch 接缝**——本组全仓都是**直接调用**，所以 `worker` 的显式转发就够；
   一旦有人开始 `setattr(worker, "prune_job_dirs", …)` 再指望已搬走的调用点看见，就会静默失效。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_fs as job_fs_mod
import remote.worker as worker_mod
from tests.helpers import remote_dag as dag

JOBFS_FILE = ROOT / "remote" / "job_fs.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

MOVED_NAMES = {
    "JOB_DIR_KEEP",
    "REPO_ROOT",
    "_git_head",
    "_persist_result",
    "pack_opt_tar",
    "prune_job_dirs",
    "unpack_opt_tar",
    "unpack_payload_or_fail",
}
#: 只在 `worker` 转发、宿主不再需要（无外部读者）的名字。
FORWARDED_NAMES = MOVED_NAMES - {"REPO_ROOT"}


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


def test_moved_names_are_defined_in_job_fs_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `job_fs.py` 里实现。"""
    assert _defined(JOBFS_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(JOBFS_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_job_fs_sits_below_its_remote_dependencies() -> None:
    """无环 + 分层：对账走**全局账本**（`tests/helpers/remote_dag.py`），不再各自写一份。

    原先这里是「不得 import `remote.worker`」的特指断言；现在是一般化的
    「顶层 intra-remote 边必须严格向下」+「任何 import 不得碰 `rl`」。
    """
    dag.assert_remote_module("remote.job_fs")


def test_worker_forwards_every_moved_name() -> None:
    """`worker` 的命名空间里每个需要转发的名字都在，且是**同一个对象**。"""
    for name in FORWARDED_NAMES:
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(job_fs_mod, name), (
            f"remote.worker.{name} 不是 remote.job_fs.{name}（转发成了副本）"
        )


def test_repo_root_still_points_at_the_nn_training_root() -> None:
    """`REPO_ROOT` 由 `__file__` 推导 ⇒ 搬目录后必须推导出**同一个**根（错了是静默的）。"""
    assert (job_fs_mod.REPO_ROOT / "remote" / "worker.py").exists()
    assert (job_fs_mod.REPO_ROOT / "remote" / "job_fs.py").exists()
    assert job_fs_mod.REPO_ROOT == ROOT


def test_job_fs_has_no_top_level_mutable_container() -> None:
    """本组只搬常量与纯函数——顶层不该多出可变容器（那是共享状态，得有主的）。"""
    mutable: set[str] = set()
    for node in _tree(JOBFS_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set)):
                mutable.add(name)
    assert mutable == set(), f"job_fs.py 顶层多了可变容器：{sorted(mutable)}"


def test_job_dir_keep_default_is_wired_to_the_constant() -> None:
    """`prune_job_dirs` 的 `keep` 缺省值必须**仍是** `JOB_DIR_KEEP`（改常量即改行为）。

    ⚠ 那个**显式调用点**自 S4 第十二刀起住 `remote/download.py`（payload 落地的清场那一行，
    随物料落地一起搬走）——本断言按「文本在谁那儿」改指，而不是放开不查：「找不到就红」正是
    这条源码守卫的价值（改指新家 vs 静默放过，差的是下一个读的人知不知道它有真调用者）。
    """
    import inspect

    sig = inspect.signature(job_fs_mod.prune_job_dirs)
    assert sig.parameters["keep"].default == job_fs_mod.JOB_DIR_KEEP == 2
    src = (ROOT / "remote" / "download.py").read_text(encoding="utf-8")
    assert src.count("prune_job_dirs(work_dir, JOB_DIR_KEEP, log=log)") == 1
    worker_src = WORKER_FILE.read_text(encoding="utf-8")
    assert "prune_job_dirs(" not in worker_src, (
        "worker.py 里又冒出 prune_job_dirs 调用点了——清场归物料落地（remote/download.py）"
    )
