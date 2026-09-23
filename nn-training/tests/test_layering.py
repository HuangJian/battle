"""分层契约（S3，2026-09-23）—— 依赖方向必须单一，且靠测试钉住而不是靠自觉。

分层（`common/` 是 S1 建立的 L0 基础）：

```
L0  common/ · platform_utils · pid_probe · dist_common · schema     （stdlib-only，无 torch）
L1  models/ · ppo/ · data/ · train/ · rl/ · scripts/                （纯逻辑）
L2  remote/ · 根入口（run_rl.py / run_bc.py …）                      （传输 / 应用）
```

允许 `L2 → L1 → L0`；**反向禁止**。

## 为什么需要守卫

本仓的包级循环曾经就是这么长出来的：`rl/` 有 10 个文件 import `remote/*`，`remote/` 有 8 个
文件 import `rl/*`，靠 `rl/queue.py` 里一处**函数内延迟 import** 换来表面的平静——那种平静
下一次改动就会破。2026-09-23 把纯逻辑的 `protocol` / `game_watch` 下沉到 `common/` 之后：

* `ppo/` `train/` `models/` `data/` `scripts/` 对 `remote` 的引用**归零**（本文件断言 == 0）；
* `rl/` 还剩一组**传输客户端**调用（见 `RL_TO_REMOTE_WHITELIST`）——它们是
  `plan/nn-training-refactor.md` §5.2 第 ④ 步（改成注入式接口）的待办。

守卫做两件事：① **不许再长回来**（新增任何未列入白名单的边即红）；
② **逼着删白名单**（白名单里某项已无引用 ⇒ 红，提示把它删掉）——否则白名单会腐烂成
「合法的历史遗留」，那正是这类清单最坏的结局。

## 判据口径

扫 **AST 的全部 import 节点**（含函数内的延迟 import）——函数内 import 同样是一条依赖边，
只是它把失败推迟到调用时（本仓就有过「延迟 import 掩盖了循环」的先例）。
`tests/` / `e2e/` 不参与分层（测试可以随便 import）。
"""

from __future__ import annotations

import ast
from pathlib import Path

NN_ROOT = Path(__file__).resolve().parent.parent

#: L0 的**顶层单文件模块**（stdlib-only；`common/` 是包，单独处理）。
L0_TOP_MODULES = ("dist_common", "schema", "platform_utils", "pid_probe")
#: L1 包（纯逻辑）。
L1_PACKAGES = ("models", "ppo", "data", "train", "rl", "scripts")
#: L2 包（传输 / 应用）。
L2_PACKAGES = ("remote",)

#: `rl/` 里仍在直接调用传输客户端的**过渡期白名单**（plan §5.2 第 ④ 步：改注入式接口）。
#: 每一项都必须**仍被引用**——否则本文件会红，逼你把它删掉（白名单不腐烂）。
RL_TO_REMOTE_WHITELIST = {
    # 任务包导出（rl/loop_steps.py）——本机侧打离线任务包
    "remote.bundle",
    # hub 客户端：poll_job / wait_job / verify_and_land / set_cloud_halt / pack_ts_code_zip …
    "remote.hub_client",
    # push 客户端：submit_job / wait_result / poll_result（hub-push 派发腿）
    "remote.push_client",
    # 节点侧长驻池的脚本名常量（rl/eval_local.py 模块级 `EVAL_SCRIPT`）
    "remote.serve_pool",
}


def _imported_tops(path: Path) -> set[str]:
    """该文件里出现的**顶层模块名**集合（AST，含函数内 import）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                tops.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import（本仓无，保守跳过）
                continue
            if node.module:
                tops.add(node.module.split(".")[0])
    return tops


def _imported_dotted(path: Path) -> set[str]:
    """该文件里出现的**完整点分模块名**（用于取 `remote.xxx` 的子模块）。

    `from remote import hub_client` 记成 `remote.hub_client`（而不是裸 `remote`）——
    否则白名单比对会把一个合法子模块误判成「导入了整个 remote 包」。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mod = node.module
            out.add(mod)
            if mod in L2_PACKAGES:  # 仅裸包：`from remote import hub_client` ⇒ remote.hub_client
                out.update(f"{mod}.{a.name}" for a in node.names if a.name != "*")
    return out


def _py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_l0_never_imports_l1_or_l2() -> None:
    """L0 是最底层：只许依赖 stdlib（外加同层）。"""
    forbidden = set(L1_PACKAGES) | set(L2_PACKAGES)
    offenders: list[str] = []

    for name in L0_TOP_MODULES:
        p = NN_ROOT / f"{name}.py"
        if not p.exists():
            continue
        bad = _imported_tops(p) & forbidden
        offenders += [f"{p.name} -> {m}" for m in sorted(bad)]

    for p in _py_files(NN_ROOT / "common"):
        bad = _imported_tops(p) & forbidden
        offenders += [f"common/{p.name} -> {m}" for m in sorted(bad)]

    assert offenders == [], f"L0 不得依赖 L1/L2：{offenders}"


def test_l1_packages_never_import_remote() -> None:
    """L1（纯逻辑）不得依赖 L2（传输）。

    `ppo/` `train/` `models/` `data/` `scripts/` 必须**零**引用；
    `rl/` 只允许白名单里的传输客户端（过渡期，见模块 docstring）。
    """
    offenders: list[str] = []
    for pkg in L1_PACKAGES:
        root = NN_ROOT / pkg
        if not root.is_dir():
            continue
        for p in _py_files(root):
            hits = {d for d in _imported_dotted(p) if d.split(".")[0] == "remote"}
            # 有具体子模块（`remote.hub_client`）就只看具体的；只有裸 `import remote` 才用 "remote"
            subs = {h for h in hits if h != "remote"}
            remote_hits = subs or hits
            if not remote_hits:
                continue
            for hit in sorted(remote_hits):
                rel = f"{pkg}/{p.name}"
                if pkg == "rl" and hit in RL_TO_REMOTE_WHITELIST:
                    continue
                offenders.append(f"{rel} -> {hit}")
    assert offenders == [], (
        "L1 不得依赖 remote（rl/ 的过渡白名单见 RL_TO_REMOTE_WHITELIST）：\n  "
        + "\n  ".join(offenders)
    )


def test_rl_to_remote_whitelist_has_not_rotted() -> None:
    """白名单里每一项都必须**仍被引用**——否则删掉它（防「合法历史遗留」）。"""
    used: set[str] = set()
    for p in _py_files(NN_ROOT / "rl"):
        used |= {d for d in _imported_dotted(p) if d.split(".")[0] == "remote"}
    stale = RL_TO_REMOTE_WHITELIST - used
    assert stale == set(), (
        f"白名单已失效（没有任何 rl/ 文件再引用）：{sorted(stale)}。"
        " —— 若已完成 plan §5.2 第 ④ 步（注入式接口），把它从白名单删掉。"
    )


def test_common_is_a_leaf_package() -> None:
    """`common/` 不得反向依赖任何上层（要能随 code.zip 解到没有 torch 的云机上）。"""
    forbidden = set(L1_PACKAGES) | set(L2_PACKAGES)
    offenders: list[str] = []
    for p in _py_files(NN_ROOT / "common"):
        bad = _imported_tops(p) & forbidden
        offenders += [f"common/{p.name} -> {m}" for m in sorted(bad)]
    assert offenders == [], f"common/ 必须自底向上无依赖：{offenders}"


def test_the_moved_modules_are_gone_from_remote() -> None:
    """S3 的机械事实：`protocol` / `game_watch` 已不在 `remote/` 下（别悄悄搬回去）。"""
    assert not (NN_ROOT / "remote" / "protocol.py").exists(), (
        "protocol.py 已下沉到 common/（S3）；若确需搬回，请同时更新本文件与 DECISIONS"
    )
    assert not (NN_ROOT / "remote" / "game_watch.py").exists()
    assert (NN_ROOT / "common" / "protocol.py").exists()
    assert (NN_ROOT / "common" / "game_watch.py").exists()
