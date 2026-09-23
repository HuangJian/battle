"""分层契约（S3，2026-09-23）—— 依赖方向必须单一，且靠测试钉住而不是靠自觉。

分层（`common/` 是 S1 建立的 L0 基础）：

```
L0  common/ · platform_utils · pid_probe · dist_common · schema     （stdlib-only，无 torch）
L1  models/ · ppo/ · data/ · train/ · rl/(纯逻辑) · scripts/        （纯逻辑）
L2  remote/ · rl/(编排) · 根入口（run_rl.py / run_bc.py …）          （传输 / 应用）
```

允许 `L2 → L1 → L0`；**反向禁止**。

## 为什么需要守卫

本仓的包级循环曾经就是这么长出来的：`rl/` 有 10 个文件 import `remote/*`，`remote/` 有 8 个
文件 import `rl/*`，靠 `rl/queue.py` 里一处**函数内延迟 import** 换来表面的平静——那种平静
下一次改动就会破。2026-09-23 把纯逻辑的 `protocol` / `game_watch` 下沉到 `common/`、并把
TS 导出器路径收进 `common/protocol.py`（`EVAL_SCRIPT`）之后，**`rl` ↔ `remote` 的模块级环
消失了**（本文件由闭环断言守着）。

## 判据口径：编排层是**结构性**的，不是白名单

`rl/` 里天然有两类模块：**纯逻辑**（课程 / 奖励 / 熔断 / 账本 …，不需要传输层）与**编排**
（驱动 rollout / eval / 远端腿，本质是应用层）。判据不用人肉列举「允许 import 谁」，而是：

    RL_ORCHESTRATION := 「rl 中可达 remote 的模块集合」的**声明式快照**

两边对账（`test_rl_orchestration_set_is_exactly_...`）：**多一个即红**（新模块偷偷碰了传输层）、
**少一个也红**（拆分后该模块已回纯逻辑，把它从集合里删掉）。这样这份清单不会腐烂成
「合法的历史遗留」——那正是这类清单最坏的结局。

另外两条是真正的性质（不是记账）：① **纯逻辑不得 import 编排**（否则纯逻辑会经由编排间接
拖入传输层，「纯」就名不副实）；② **remote 不得（传递地）触及任何编排模块**——这就是
「包循环已断」的机械形式。

扫 **AST 的全部 import 节点**（含函数内的延迟 import）——函数内 import 同样是一条依赖边，
只是它把失败推迟到调用时（本仓就有过「延迟 import 掩盖了循环」的先例）。
`tests/` / `e2e/` 不参与分层（测试可以随便 import）。

⚠ **判据自身的坑（2026-09-23 反向探针实测）**：`from <pkg> import <mod>` 这种写法只给出裸包名，
必须展开成 `<pkg>.<mod>` 才看得见那条边。首版只给 `remote` 展开、没给 `rl` 展开 ⇒
「纯逻辑 → 编排」与「remote → 编排」两条断言在探针下**静默不动**（测试全绿但守卫是瞎的）。
本文件用 `_subpackages()`（按实存的包子目录）统一展开，别再逐个包开小灶。
"""

from __future__ import annotations

import ast
from pathlib import Path

NN_ROOT = Path(__file__).resolve().parent.parent

#: L0 的**顶层单文件模块**（stdlib-only；`common/` 是包，单独处理）。
L0_TOP_MODULES = ("dist_common", "schema", "platform_utils", "pid_probe")
#: L1 包（纯逻辑）。
L1_PACKAGES = ("models", "ppo", "data", "train", "scripts")
#: L2 包（传输 / 应用）。
L2_PACKAGES = ("remote",)

#: `rl/` 里属于**编排层**（应用层：驱动 rollout/eval/远端腿）的模块，允许 import `remote`。
#:
#: 这不是「豁免名单」而是**声明式快照**：测试会把它与「rl 中可达 remote 的模块集合」逐项对账，
#: 多一个 / 少一个都红。2026-09-23 下沉 `EVAL_SCRIPT` 后由 17 个收敛到 11 个——
#: `eval_local` / `eval_dispatch` / `gate_check` / `batch_eval` / `eval_a_once` /
#: `eval_replays_once` 原来只是**经由 `eval_local` 间接**碰到传输层，环一断就回了纯逻辑。
#: 2026-09-23（S4）：`loop_steps` 拆出 `loop_transport`（传输/发布策略的独立实现），
#: 后者成为新的一员（它直接 import `remote.push_client`）——由本快照强制登记。
RL_ORCHESTRATION = frozenset(
    {
        "bc_loop",
        "collect_only",
        "loop",
        "loop_core",
        "loop_guards",
        "loop_plan",
        "loop_round_steps",
        "loop_runner",
        "loop_serve",
        "loop_steps",
        "loop_transport",
        "rollout_phase",
    }
)


def _subpackages() -> set[str]:
    """仓内的包子目录名（`rl` / `remote` / `common` …）——`from <pkg> import x` 要展开成
    `<pkg>.x` 才能当作"一条指向子模块的依赖边"对账。
    """
    return {
        p.name
        for p in NN_ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    }


def _imports(path: Path) -> set[str]:
    """该文件里出现的**完整点分模块名**集合（AST，含函数内 import）。

    `from rl.log import log` → `rl.log`；`from remote import hub_client` → `remote` 与
    `remote.hub_client` **两条都记**（裸包与子模块是两种不同的依赖声明）。

    ⚠ 展开必须**对所有子包**生效：只给某个包开小灶，`from rl import loop_steps` 就会被
    记成裸 `rl`，环与切线的断言会**静默失效**（2026-09-23 反向探针实测到，见本文件头部）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    subs = _subpackages()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mod = node.module
            out.add(mod)
            if mod in subs:  # 仅裸包：`from remote import hub_client`
                out.update(f"{mod}.{a.name}" for a in node.names if a.name != "*")
    return out


def _top_modules(path: Path) -> set[str]:
    """顶层模块名（用于「L0 不得依赖 L1/L2」这类粗判）。"""
    return {d.split(".")[0] for d in _imports(path)}


def _py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _rl_modules() -> dict[str, Path]:
    return {p.stem: p for p in _py_files(NN_ROOT / "rl")}


def _rl_reaching_remote() -> set[str]:
    """`rl/` 中**直接或经 rl 内部传递**可达 `remote` 的模块（= 编排层）。"""
    rl = _rl_modules()
    reach = {
        k: any(d.split(".")[0] == "remote" for d in _imports(p)) for k, p in rl.items()
    }
    changed = True
    while changed:
        changed = False
        for k, p in rl.items():
            if reach[k]:
                continue
            if any(
                d.startswith("rl.") and reach.get(d[3:], False) for d in _imports(p)
            ):
                reach[k] = True
                changed = True
    return {k for k, v in reach.items() if v}


def _remote_reaching_rl() -> set[str]:
    """从 `remote/` 出发（含传递）能触达的 `rl` 模块名集合。"""
    out: set[str] = set()
    seen: set[str] = set()
    stack = list(_py_files(NN_ROOT / "remote"))
    while stack:
        p = stack.pop()
        if str(p) in seen:
            continue
        seen.add(str(p))
        for d in _imports(p):
            if d.startswith("remote."):
                f = NN_ROOT / (d.replace(".", "/") + ".py")
                if f.exists():
                    stack.append(f)
            elif d.startswith("rl."):
                out.add(d[3:])
                sub = NN_ROOT / "rl" / (d[3:] + ".py")
                if sub.exists():
                    stack.append(sub)
    return out


def test_l0_never_imports_l1_or_l2() -> None:
    """L0 是最底层：只许依赖 stdlib（外加同层）。"""
    forbidden = set(L1_PACKAGES) | set(L2_PACKAGES) | {"rl"}
    offenders: list[str] = []

    for name in L0_TOP_MODULES:
        p = NN_ROOT / f"{name}.py"
        if not p.exists():
            continue
        offenders += [f"{p.name} -> {m}" for m in sorted(_top_modules(p) & forbidden)]

    for p in _py_files(NN_ROOT / "common"):
        offenders += [
            f"common/{p.name} -> {m}" for m in sorted(_top_modules(p) & forbidden)
        ]

    assert offenders == [], f"L0 不得依赖 L1/L2：{offenders}"


def test_l1_packages_never_import_remote() -> None:
    """L1（纯逻辑）不得依赖 L2（传输）。

    `ppo/` `train/` `models/` `data/` `scripts/` 必须**零**引用；
    `rl/` 只允许 `RL_ORCHESTRATION` 里的编排模块（其余 rl 模块是纯逻辑）。
    """
    offenders: list[str] = []
    for pkg in L1_PACKAGES:
        root = NN_ROOT / pkg
        if not root.is_dir():
            continue
        for p in _py_files(root):
            for d in sorted(x for x in _imports(p) if x.split(".")[0] == "remote"):
                offenders.append(f"{pkg}/{p.name} -> {d}")

    for stem, p in _rl_modules().items():
        if stem in RL_ORCHESTRATION:
            continue
        for d in sorted(x for x in _imports(p) if x.split(".")[0] == "remote"):
            offenders.append(f"rl/{p.name} -> {d}（若它确应驱动传输层，请加进 RL_ORCHESTRATION）")

    assert offenders == [], "L1 不得依赖 remote：\n  " + "\n  ".join(offenders)


def test_l1_packages_never_import_orchestration_rl() -> None:
    """采样器 / 训练器 / 模型不得依赖 `rl/` 的编排模块（否则间接拖入传输层）。"""
    offenders: list[str] = []
    for pkg in L1_PACKAGES:
        root = NN_ROOT / pkg
        if not root.is_dir():
            continue
        for p in _py_files(root):
            for d in sorted(_imports(p)):
                if d.startswith("rl.") and d[3:] in RL_ORCHESTRATION:
                    offenders.append(f"{pkg}/{p.name} -> {d}")
    assert offenders == [], (
        "L1 上层不得 import rl 编排模块（会间接依赖 remote）：\n  " + "\n  ".join(offenders)
    )


def test_pure_rl_never_imports_orchestration() -> None:
    """**真切线**：纯逻辑 rl 模块不得 import 编排 rl 模块。

    否则「纯逻辑」可经由编排间接拿到传输层——分层就成了摆设（这条与
    `test_l1_packages_never_import_orchestration_rl` 一起，才是「切线是真的」的证明）。
    """
    offenders: list[str] = []
    for stem, p in _rl_modules().items():
        if stem in RL_ORCHESTRATION:
            continue
        for d in sorted(_imports(p)):
            if d.startswith("rl.") and d[3:] in RL_ORCHESTRATION:
                offenders.append(f"rl/{p.name} -> {d}")
    assert offenders == [], "纯逻辑 rl 不得 import 编排 rl：\n  " + "\n  ".join(offenders)


def test_rl_orchestration_set_is_exactly_the_modules_reaching_remote() -> None:
    """声明式快照双向对账：**多一个红，少一个也红**（这份清单不许腐烂）。"""
    computed = _rl_reaching_remote()
    declared = set(RL_ORCHESTRATION)
    grew = sorted(computed - declared)
    shrank = sorted(declared - computed)
    assert not grew and not shrank, (
        "RL_ORCHESTRATION 与实测不符：\n"
        f"  新增（偷偷碰了传输层 / 经 rl 传递可达 remote，请确认后加进集合）：{grew}\n"
        f"  失效（已回纯逻辑，请从集合里删掉）：{shrank}"
    )


def test_remote_never_reaches_orchestration_rl() -> None:
    """**包循环已断**的机械形式：`remote/` 不得（传递地）触及任何编排模块。

    S3 之前的形态是双向的（`remote/*` → `rl/eval_local` → `remote/serve_pool`，一条边就够
    把整片 eval 模块拽进环里）。现在 `remote/` 只能触达纯逻辑 rl——它依赖纯逻辑是**合法**方向。
    """
    hit = sorted(_remote_reaching_rl() & set(RL_ORCHESTRATION))
    assert hit == [], (
        "remote 触达了编排层 rl，rl ↔ remote 环回来了：\n  " + "\n  ".join(hit)
    )


def test_common_is_a_leaf_package() -> None:
    """`common/` 不得反向依赖任何上层（要能随 code.zip 解到没有 torch 的云机上）。"""
    forbidden = set(L1_PACKAGES) | set(L2_PACKAGES) | {"rl"}
    offenders: list[str] = []
    for p in _py_files(NN_ROOT / "common"):
        offenders += [
            f"common/{p.name} -> {m}" for m in sorted(_top_modules(p) & forbidden)
        ]
    assert offenders == [], f"common/ 必须自底向上无依赖：{offenders}"


def test_the_moved_modules_are_gone_from_remote() -> None:
    """S3 的机械事实：`protocol` / `game_watch` 已不在 `remote/` 下（别悄悄搬回去）。"""
    assert not (NN_ROOT / "remote" / "protocol.py").exists(), (
        "protocol.py 已下沉到 common/（S3）；若确需搬回，请同时更新本文件与 DECISIONS"
    )
    assert not (NN_ROOT / "remote" / "game_watch.py").exists()
    assert (NN_ROOT / "common" / "protocol.py").exists()
    assert (NN_ROOT / "common" / "game_watch.py").exists()
