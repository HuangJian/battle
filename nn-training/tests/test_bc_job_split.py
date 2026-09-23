"""拆分的**契约守卫**：BC 作业永住 `remote/bc_job.py`（S4 第六步之二，2026-09-23）。

`remote/worker.py` 2915 → 2563 行；搬走的簇（363 行）：`_bc_fetch_resume` · `_bc_local_resume_dir` ·
`_bc_store_local_resume` · `_bc_load_local_resume` · `_bc_post_epoch` · `_bc_device` ·
`normalize_ppo_device` · `resolve_bc_seed` · `_run_bc_job`。

本文件钉六件事：

1. **定义唯一**——不许在 `worker.py` 里再实现一遍；
2. **依赖方向**——`bc_job` 只许依赖 `remote.http` / `remote.job_fs`（都向下），不得 import
   `remote.worker`（`worker` 用自别名转发回来）；
3. **转发的名字是同一对象**（e2e / tests 都直接 `worker_mod.X` / `from remote.worker import X`）；
4. **顶层零 torch**（本仓硬规：hub 侧与协议单测不得拉 torch）——torch 与 `train/bc.py` 必须仍是
   `_run_bc_job` **函数内**的延迟 import；这条守卫是这个不变量第一次被机械钉住；
5. **顶层无新增可变容器**；
6. **`d14_corpus_match` 两边是同一个函数对象**（worker 侧那份转发是给 `run_job` 用的，不随簇走）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.bc_job as bc_job_mod
import remote.worker as worker_mod
from common.protocol import d14_corpus_match

BC_FILE = ROOT / "remote" / "bc_job.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

MOVED_NAMES = {
    "_bc_device",
    "_bc_fetch_resume",
    "_bc_load_local_resume",
    "_bc_local_resume_dir",
    "_bc_post_epoch",
    "_bc_store_local_resume",
    "_run_bc_job",
    "normalize_ppo_device",
    "resolve_bc_seed",
}
#: 允许的向下依赖（L2 内部 + L1 的延迟 import）。
ALLOWED_IMPORTS = {
    "common.protocol",
    "remote.http",
    "remote.job_fs",
    "data.weights_io",
    "train.bc",
}
#: 仓内项目的顶层包/模块名（用来把 stdlib 排除在依赖断言之外）。
PROJECT_ROOTS = {
    "common",
    "remote",
    "rl",
    "data",
    "train",
    "models",
    "ppo",
    "scripts",
    "dist_common",
    "platform_utils",
    "pid_probe",
    "schema",
}


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


def _top_level_imports(path: Path) -> set[str]:
    """**仅模块级** import（不看函数内的延迟 import）。"""
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _all_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def test_moved_names_are_defined_in_bc_job_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `bc_job.py` 里实现。"""
    assert _defined(BC_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(BC_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_bc_job_does_not_import_worker_and_only_depends_downwards() -> None:
    """依赖方向：不得 import `remote.worker`；模块级只许 `common.protocol` / `remote.http` /
    `remote.job_fs`（延迟 import 的 L1 包另算）。"""
    top = _top_level_imports(BC_FILE)
    assert "remote.worker" not in _all_imports(BC_FILE), "bc_job 反向 import worker ⇒ 环"
    extra = {m for m in top if m.split(".")[0] in PROJECT_ROOTS and m not in ALLOWED_IMPORTS}
    assert extra == set(), f"bc_job 顶层出现未登记的仓内依赖：{sorted(extra)}"
    assert "rl" not in {m.split(".")[0] for m in _all_imports(BC_FILE)}


def test_worker_forwards_every_moved_name_as_the_same_object() -> None:
    """e2e / tests 都从 `remote.worker` 取这些名字 ⇒ 必须是同一对象（否则拿到副本）。"""
    for name in MOVED_NAMES:
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(bc_job_mod, name), (
            f"remote.worker.{name} 不是 remote.bc_job.{name}（转发成了副本）"
        )


def test_torch_stays_a_deferred_import_inside_run_bc_job() -> None:
    """**顶层零 torch**：`import torch` 与 `train.bc` 必须仍在函数内（延迟 import）。"""
    top = _top_level_imports(BC_FILE)
    assert "torch" not in top, "bc_job 顶层 import torch（本仓硬规：顶层零 torch）"
    assert "train.bc" not in top, "train.bc 必须在 _run_bc_job 内延迟 import"

    # 而它们确实在 `_run_bc_job` 体内（否则就是漏搬）
    fn = next(
        n
        for n in _tree(BC_FILE).body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_bc_job"
    )
    inner: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            inner.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            inner.add(node.module)
    assert {"torch", "train.bc"} <= inner, sorted(inner)


def test_bc_job_has_no_top_level_mutable_container() -> None:
    """搬走的是函数簇（零模块级状态）——顶层不该多出可变容器。"""
    mutable: set[str] = set()
    for node in _tree(BC_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set)):
                mutable.add(name)
    assert mutable == set(), f"bc_job.py 顶层多了可变容器：{sorted(mutable)}"


def test_d14_corpus_match_is_one_shared_function_object() -> None:
    """`d14_corpus_match` 在两边都是 `common.protocol` 那一个对象（是转发，不是各自实现）。"""
    assert bc_job_mod.d14_corpus_match is d14_corpus_match
    assert worker_mod.d14_corpus_match is d14_corpus_match
