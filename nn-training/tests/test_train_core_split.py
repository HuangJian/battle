"""拆分的**契约守卫**：`worker.py` 的宿主拆成「作业壳 + 训练核 + 进程生命周期」（2026-09-24，S4 第九刀）。

## 这一刀切了什么

`remote/worker.py` 的两个宿主里，`run_job` 是一段 752 行的直线（下载 → 校验 → **训练** → 产物 →
上报）。按**一条真实调用链**切：中间那段「课程上下文 → torch/种子 → 模型构建 → opt 解析 → 多卡 →
kickstart ref → demo bank → PPO → 产物/result」整块下沉成 `remote/train_core.py`（**427 行**）。
另外两簇也是宿主里与作业无关的东西：

```
run_job（作业壳，留 worker.py）        网络 / 三道校验 / kind 分叉 / 冒烟回显 / 落盘 / 上报 / 半离线尾巴
        └─ run_training_core() ──►  train_core.py（L4）   课程→模型→opt→PPO→产物（427 行）
worker 的进程生命周期 ──────────►  worker_proc.py（L0）   监督 / 热替换(exit 86) / 云机停机
_wire_block（两个调用方都要） ──►  wire.py（L1）          M0 wire 子字典
```

`worker.py` **1814 → 1281** 行。

## 为什么训练核是 L4（而不是跟着宿主同层或更低）

账本（`tests/helpers/remote_dag.py`）把 `LAYERS` 定义为**拓扑秩**：训练核依赖 `download`(L3) /
`job_lifecycle`(L3) / `job_fs`(L1) / `wire`(L1) ⇒ 秩只能 **4**；`worker` 因此升到 **L5**，
它的下游（`run_loop` / `notebook_runtime` / `worker_server` / `offline_boot` / `push_bootstrap` /
`notebook_boot`）各升一层。**这不是口味问题**：`test_remote_dag.py` 的秩断言与分层断言会把
「标错层」当场报出来。`worker_proc` 零 `remote.*` 依赖 ⇒ 秩 0。

## 本文件钉住的东西

1. **定义唯一**——训练核的名字只在 `train_core.py` 实现；作业壳里不许再出现那些调用
   （`_resolve_blob` / `pack_opt_tar` / `job_body_error` / `build_ppo` …）；
2. **★ 接口双向一致**——作业壳的调用点关键字集合 == 训练核的形参集合（**漏传一个参数**
   是这一刀最可能的失误形态，而且 mpy/ruff 只在「核里用到却没传」时才报）；
3. **依赖方向**——`train_core` 不得 import `remote.worker` / `remote.run_loop`（含延迟）；
4. **顶层零 torch / numpy / ppo**（本仓硬规），且它们确实仍在函数体内（漏搬的另一面）；
5. **进程链**：`worker_proc` 顶层零 `remote.*`（秩 0），四个名字在 `worker` 侧是**同一对象**
   （`setattr(W, "supervise_worker", …)` / `setattr(W, "_release_cloud_machine", …)` 这些既有
   注入点照旧有效——宿主读的是自己命名空间里的转发名）；
6. **`_wire_block` 定义唯一且在 `wire`**（两个调用方都从 L1 取）；
7. **账本关系**：`train_core` < `worker` ≤ `run_loop`，且 `worker_proc` 在最低层。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.train_core as core_mod
import remote.worker as worker_mod
import remote.worker_proc as proc_mod
from remote import wire as wire_mod
from tests.helpers import remote_dag as dag

CORE_FILE = ROOT / "remote" / "train_core.py"
PROC_FILE = ROOT / "remote" / "worker_proc.py"
WIRE_FILE = ROOT / "remote" / "wire.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

CORE_FN = "run_training_core"

#: 随训练核搬走的模块级调用（作业壳里**不许**再有调用点）。
MOVED_CALLS = (
    "_resolve_blob",
    "_cache_blob",
    "unpack_opt_tar",
    "pack_opt_tar",
    "job_body_error",
    "job_seed",
    "coef_active",
    "build_ppo",
    "save_weights_json",
    "load_state_into",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defs(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _func(path: Path, name: str) -> ast.FunctionDef:
    return next(
        n
        for n in _tree(path).body
        if isinstance(n, ast.FunctionDef) and n.name == name
    )


def _all_imports(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _top_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


# ───────────────────────── ① 定义唯一：宿主里不许再干训练的活 ─────────────────────────


def test_the_training_pass_is_gone_from_the_job_shell() -> None:
    """★ 作业壳（`worker.run_job`）里不许再有训练核那些**调用点**。

    判据是 AST 的 `Call`（不是文本子串）：注释里提这些名字（解释「它搬去哪了」）不算。
    """
    calls: set[str] = set()
    for node in ast.walk(_func(WORKER_FILE, "run_job")):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                calls.add(f.id)
            elif isinstance(f, ast.Attribute):
                calls.add(f.attr)
    leftovers = sorted(calls & set(MOVED_CALLS))
    assert leftovers == [], (
        f"`worker.run_job` 里还有训练核的调用点：{leftovers} —— "
        "「跑一个轮次」只在 `remote/train_core.py` 里实现（本刀的划分：壳管网络/校验/上报）"
    )


def test_the_core_lives_in_train_core() -> None:
    """训练核是 `train_core.py` 里的一个真函数（不是被谁重新导出成别名）。"""
    assert CORE_FN in _defs(CORE_FILE)
    # 作业壳只是 import 它（`X as X` 显式转发），不得在自己的命名空间里再实现一份
    assert CORE_FN not in _defs(WORKER_FILE)


def test_worker_only_reaches_the_core_through_one_import() -> None:
    """作业壳对训练核的依赖只有一条：`from remote.train_core import run_training_core`。"""
    tops = _top_imports(WORKER_FILE)
    assert "remote.train_core" in tops
    tree = _tree(WORKER_FILE)
    # `X as X` 自别名（ruff 认的 re-export 写法）
    aliases: list[str] = []
    for n in tree.body:
        if not (isinstance(n, ast.ImportFrom) and n.module == "remote.train_core"):
            continue
        for a in n.names:
            assert a.asname is not None, f"{CORE_FN} 必须以 `X as X` 显式转发（ruff 认的 re-export）"
            aliases.append(a.asname)
    assert aliases == [CORE_FN], aliases
    assert worker_mod.run_training_core is core_mod.run_training_core


# ───────────────── ② ★ 接口双向一致：调用点关键字 == 形参集合 ─────────────────


def _core_params() -> tuple[list[str], list[str]]:
    fn = _func(CORE_FILE, CORE_FN)
    pos = [a.arg for a in fn.args.posonlyargs] + [a.arg for a in fn.args.args]
    return pos, [a.arg for a in fn.args.kwonlyargs]


def test_the_call_site_passes_exactly_the_core_interface() -> None:
    """★ **接口双向一致**：调用点的位置实参个数与关键字集合，必须与训练核的签名逐名相符。

    这是本刀最可能的失误形态：搬完之后**漏传一个形参**。运行时不一定立刻炸（只有走到那条
    分支才 NameError），而这条断言在**提交时**就红。反向也钉住：核里删了形参而调用点还传着。
    """
    pos, kwonly = _core_params()
    assert pos == ["base_url", "token"], pos
    calls = [
        n
        for n in ast.walk(_func(WORKER_FILE, "run_job"))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == CORE_FN
    ]
    assert len(calls) == 1, f"作业壳里应只有一处 `{CORE_FN}(...)`，实得 {len(calls)}"
    call = calls[0]
    assert len(call.args) == len(pos), (
        f"位置实参应 {len(pos)} 个（{pos}），实得 {len(call.args)}"
    )
    got: list[str] = []
    for k in call.keywords:
        assert k.arg is not None, "调用点用了 `**` 展开 —— 接口必须逐名显式（否则这条双向一致形同虚设）"
        got.append(k.arg)
    got.sort()
    assert got == sorted(kwonly), (
        "调用点的关键字与训练核形参不一致：\n"
        f"  多传/名字写错：{sorted(set(got) - set(kwonly))}\n"
        f"  漏传：{sorted(set(kwonly) - set(got))}"
    )


def test_no_keyword_is_swallowed_by_kwargs() -> None:
    """训练核**不得**用 `**kwargs` 吞掉接口（吞掉后上面那条双向一致就失去意义）。"""
    fn = _func(CORE_FILE, CORE_FN)
    assert fn.args.kwarg is None, "训练核不该有 **kwargs（接口要显式可见）"


# ───────────────────── ③ 依赖方向：核不许反向 import 宿主 ─────────────────────


def test_the_core_never_imports_worker_or_entry_modules() -> None:
    """★ 训练核不许 import `remote.worker` / `remote.run_loop`（含**延迟** import）。"""
    # ★ 前缀匹配会误伤 `remote.worker_proc`（L0，合法）⇒ 按**模块名**精确比（同 test_job_round_split）
    bad = sorted(
        m
        for m in _all_imports(_tree(CORE_FILE))
        if any(m == u or m.startswith(u + ".") for u in ("remote.worker", "remote.run_loop"))
    )
    assert bad == [], (
        f"remote/train_core.py 反向 import 了 {bad} —— 训练核必须在宿主**下面**（L4 < L5）；"
        "「怎么跑一轮」由宿主注入（`should_cancel` / `on_ppo_start`）"
    )


def test_the_core_touches_only_downward_clusters() -> None:
    """它依赖的业务簇是既定的那几个（多一个就说明又搬漏/搬多了）。"""
    tops = sorted(m for m in _top_imports(CORE_FILE) if m.startswith("remote."))
    assert tops == [
        "remote.download",
        "remote.job_fs",
        "remote.job_lifecycle",
        "remote.wire",
    ], tops


# ───────────────────── ④ 顶层零 torch / numpy / ppo ─────────────────────


def test_torch_stays_a_deferred_import_inside_the_core() -> None:
    """**顶层零 torch / numpy / ppo**：三者都必须仍在**函数体内**延迟 import。

    两面一起钉：顶层**没有**（防回归）+ 函数体**有**（防漏搬）。
    """
    top = _top_imports(CORE_FILE)
    for mod in ("torch", "numpy", "ppo", "ppo.engine", "ppo.common", "torch_xla", "data.weights_io"):
        assert not any(m == mod or m.startswith(mod + ".") for m in top), (
            f"train_core 顶层 import 了 {mod}（本仓硬规：顶层零 torch）"
        )
    fn = _func(CORE_FILE, CORE_FN)
    inner: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            inner.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            inner.add(node.module)
    for mod in ("torch", "numpy", "ppo.engine", "ppo.common", "data.weights_io"):
        assert any(m == mod or m.startswith(mod + ".") for m in inner), (
            f"{mod} 没在训练核体内出现 —— 要么漏搬，要么改成了顶层 import"
        )


def test_core_module_has_no_mutable_global_state() -> None:
    """训练核零模块级可变容器（本刀搬的是函数簇，不是状态；状态类问题见 worker 的状态契约）。"""
    mutable: set[str] = set()
    for node in _tree(CORE_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if isinstance(value, (ast.List, ast.Dict, ast.Set)):
                mutable.add(name)
    assert mutable == set(), sorted(mutable)


# ──────────────────── ⑤ 进程生命周期：worker_proc（L0）────────────────────


def test_worker_proc_is_pure_stdlib() -> None:
    """进程链零 `remote.*` 依赖（含延迟）⇒ 账本里秩为 0，任何模块都能在它下面踩。"""
    bad = sorted(m for m in _all_imports(_tree(PROC_FILE)) if m.startswith(("remote.", "common.")))
    assert bad == [], f"remote/worker_proc.py 依赖了 {bad} —— 它应当是纯 stdlib（秩 0）"


def test_worker_proc_names_are_forwarded_as_the_same_objects() -> None:
    """四个名字在 `worker` 侧是**同一对象**——既有注入点（`setattr(W, …)`）因此照旧有效。"""
    for name in ("HOT_RELOAD_EXIT", "_release_cloud_machine", "_request_reload", "supervise_worker"):
        assert hasattr(worker_mod, name), f"remote.worker.{name} 不见了（既有注入点）"
        assert getattr(worker_mod, name) is getattr(proc_mod, name), (
            f"remote.worker.{name} 不是 remote.worker_proc.{name}（转发成了副本）"
        )
    assert proc_mod.HOT_RELOAD_EXIT == 86, "热替换退出码必须是 86（测试与监督器都按它判）"


def test_supervisor_and_reload_still_react_to_each_other() -> None:
    """功能上真的是一条链：`_request_reload` 抛的正是 `supervise_worker` 认的那个码。"""
    import pytest

    with pytest.raises(SystemExit) as ei:
        proc_mod._request_reload(["--poll", "http://x"], log=lambda _m: None)
    assert ei.value.code == proc_mod.HOT_RELOAD_EXIT


# ──────────────────── ⑥ `_wire_block`：唯一实现在 wire（L1）────────────────────


def test_wire_block_has_exactly_one_implementation() -> None:
    """`_wire_block` 定义在 `wire`，`worker` 只是转发——两个调用方（壳的 echo + 核）都从 L1 取。"""
    assert "_wire_block" in _defs(WIRE_FILE)
    assert "_wire_block" not in _defs(WORKER_FILE)
    assert worker_mod._wire_block is wire_mod._wire_block
    # 训练核也从 wire 取（不是自己抄一份）
    assert core_mod._wire_block is wire_mod._wire_block


# ──────────────────── ⑦ 账本：层号就是拓扑秩 ────────────────────


def test_training_core_sits_below_the_host() -> None:
    """训练核在宿主**下面**、且宿主仍低于入口编排；进程链在最低层。"""
    layers = dag.LAYERS
    assert layers["remote.train_core"] < layers["remote.worker"] <= layers["remote.run_loop"]
    assert layers["remote.worker_proc"] == 0
    assert layers["remote.wire"] < layers["remote.train_core"]
