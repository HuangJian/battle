"""tests/retired_contracts.py — **退役契约的单点清单 + 唯一扫描器**（2026-10-09）。

## 为什么要有它

`plan/nn-training-test-debt-cleanup.plan.md` §2-T2：本仓有一族「断言某个东西**不在场**」的守卫
（退役的腿、退役的 mode 键、删掉的 arena 档……）。它们分住在 8 个测试文件里、各写各的措辞与扫描面，
下一次退役必然漏同步。这里把**扫描型**的那一类收成一份数据表：新增退役 ⇒ 只加一行 `RetiredContract`。

## 边界（评审 F2 的结论，写死免得又走回去）

本清单**只收「扫生产源码文本、断言某标识不存在」**的守卫（`tokens` 非空）。**不收行为拒绝**——
「发布咽喉点当场拒收 `kind=run`」「mode POST 回 400」「worker 在下载前拒收」这类是对**现行代码行为**
的断言（实测有 6 条被误并进来过），它们必须留在自己的文件里按行为测；并进源码扫描器是**覆盖降级**
（源码里没这个字 ≠ 这条腿接得回来）。那批在 `docs/nn/test-contract-map.md` 的 T2b 段登记。

## 为什么剥注释与字符串（`code_only`）

生产代码的 docstring **应该**能点名退役的腿（它的历史、它挖出的 8h 白等）。把 docstring 里的提及
当命中 = 「文档写得越诚实越红」。真正的复活只会出现在**名字 token** 上（`def _remote_run_segment`、
`self._run_wait_sec`），注释与字符串到不了那里。廉价预筛（先 `in src` 再 `code_only`）保留：
`tokenize` 是这条的实测热点。

## 单点在哪

**唯一**的扫描器 = 本文件的 `scan()`；`tests/test_retired_contracts.py` 只是 3 行驱动（收集 + 断言
扫描面没缩水）。全仓不该再出现第二份「退役标识清单」。
"""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RetiredContract:
    """一条退役契约：「这个标识回到生产代码里 = 那条腿复活」。"""

    name: str  # 人读的契约名
    retired_on: str  # 退役日期（YYYY-MM-DD）
    decision: str  # 决策锚（DECISIONS 条目 / plan 节）
    dirs: tuple[str, ...]  # 生产扫描面（相对 nn-training/ 的目录，递归 *.py）
    files: tuple[str, ...]  # 额外单件（同级的散件）
    tokens: tuple[str, ...]  # 复活标识：出现在**代码**（剥注释/字符串后）里即红
    why: str  # 为什么只能这样守（写不出 ⇒ 该用行为测试，不该进本表）


#: 扫描面基线（防「glob 面缩水 ⇒ 判据变永真」）：实测 **97** 个文件（2026-10-09 =
#: `trainer/` + `biz/` + `remote/` 递归 `*.py` + `trainer/run_rl.py` + `common/distribution.py`）。
#: 留 ~7% 余量取 90 —— 真正的整族搬家（如 2026-09-30 刀 6 把训练侧搬进 `worker/`）应当
#: **同步改清单的 `dirs` 并抬回这个数**，而不是让它静默缩到地板以下。
#: 背景：刀 4 把 `rl/` 的纯逻辑半搬进 `biz/` 时扫描面漏了 `biz/` ⇒ 判据静默少扫 64 个模块。
MIN_SURFACE_FILES = 90

RETIRED_CONTRACTS: tuple[RetiredContract, ...] = (
    RetiredContract(
        name="离线 = 发一份 kind=run 队列项（本机发段长、随后等 8h）",
        retired_on="2026-09-25",
        decision=(
            "plan/online-offline-role-routing.plan.md §7（口径 §7.0）；"
            "tests/trainer/test_offline_leg_retired.py 头注"
        ),
        dirs=("trainer", "biz", "remote"),
        files=("trainer/run_rl.py", "common/distribution.py"),
        tokens=(
            "_remote_run_segment",
            "RUN_WAIT_DEFAULT_SEC",
            "_run_wait_sec",
            "run_wait_sec",
        ),
        why=(
            "那条腿的**执行者在云机**：它复活时不会有任何本机行为测试变红（本机只发一份队列项、"
            "然后等 8h），唯一能当场发现的信号是「生产代码里又出现了这个发布点/等待参数的名字」。"
            "行为侧另有守卫（发布咽喉点拒收 `kind=run`、worker 拒收、`rollout_src=run` 容错），"
            "但**发端**只有这条名字面。"
        ),
    ),
)


def surface(contracts: tuple[RetiredContract, ...] = RETIRED_CONTRACTS) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """契约集的扫描面 `(dirs, files)`（去重排序）。"""
    return (
        tuple(sorted({d for c in contracts for d in c.dirs})),
        tuple(sorted({f for c in contracts for f in c.files})),
    )


def prod_sources(
    root: Path, contracts: tuple[RetiredContract, ...] = RETIRED_CONTRACTS
) -> dict[str, str]:
    """生产扫描面：`dirs` 下递归 `*.py` + `files` 单件（`{相对路径: 源码}`）。"""
    dirs, files = surface(contracts)
    out: dict[str, str] = {}
    for sub in dirs:
        d = root / sub
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.py")):
            out[str(f.relative_to(root)).replace("\\", "/")] = f.read_text(
                encoding="utf-8", errors="replace"
            )
    for name in files:
        p = root / name
        if p.exists():
            out[name] = p.read_text(encoding="utf-8", errors="replace")
    return out


def code_only(src: str) -> str:
    """剥掉**注释与字符串**后的 token 流（只看「代码里有没有这个名字」，见模块头）。"""
    keep: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        keep.append(tok.string)
    return " ".join(keep)


def scan(root: Path, contracts: tuple[RetiredContract, ...] = RETIRED_CONTRACTS) -> list[str]:
    """返回 `["<rel>: <契约名>: <token>", …]` —— 空列表 = 没有退役契约回流。"""
    wanted = {t for c in contracts for t in c.tokens}
    hits: list[str] = []
    for rel, src in prod_sources(root, contracts).items():
        # 廉价预筛：`code_only` 只**删** token，不会造出新名字 ⇒ 源码文本里一个都不出现
        # 就不可能在 token 流里出现（`tokenize` 是本条的实测热点：174 个文件全量 ~秒级）。
        if not any(tok in src for tok in wanted):
            continue
        code = code_only(src)
        for contract in contracts:
            for tok in contract.tokens:
                if tok in code:
                    hits.append(f"{rel}: {contract.name}: {tok}")
    return hits
