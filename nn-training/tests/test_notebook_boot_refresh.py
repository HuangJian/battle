"""tests/test_notebook_boot_refresh.py —— 两个训练 notebook 的引导模块刷新语义必须同级。

两个盘各自从 GitHub raw 拉自己的引导模块（`battle.offline.ipynb` → `offline_boot.py`，
`battle.tailscale.ipynb` → `notebook_boot.py`），**没有共享的 loader 实现**（各处一份内联），
于是两条链各漂各的：2026-09-22 的事故只修了 offline 盘（每次刷新 + sha12），tailscale 盘
还留着「有缓存先用缓存」原型；2026-09-25 的 `sys.modules` 事故最初也只在 offline 盘修。

本文件钉的就是「两个盘必须同级」——关键行为做成一组 needle，两个 cell 各跑一遍。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from remote import notebook_boot, offline_boot

NN = Path(__file__).resolve().parent.parent
OFFLINE_NB = NN / "ipynb" / "battle.offline.ipynb"
TAILSCALE_NB = NN / "ipynb" / "battle.tailscale.ipynb"


def boot_cell(path: Path, module: str) -> str:
    """该盘里**唯一**那个 `import {module}` 的 code cell（就是引导 cell）。"""
    nb = json.loads(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for cell in nb["cells"]:
        if cell.get("cell_type") != "code":
            continue
        src = "".join(cell.get("source", ""))
        if f"import {module}\n" in src:
            hits.append(src)
    assert len(hits) == 1, (
        f"{path.name}: 期望恰好 1 个含 `import {module}` 的 code cell，实际 {len(hits)}"
    )
    return hits[0]


#: 两个盘都必须具备的引导刷新语义（2026-09-22 + 2026-09-25 两次事故的结论）。
SHARED_NEEDLES: list[tuple[str, str]] = [
    ("已刷新", "拉到最新时必须明说刷新了（否则看不出缓存新旧）"),
    ("@ sha12=", "加载日志必须带 sha12（branch 不足以区分新旧）"),
    ("用上一份缓存继续", "回落到缓存必须响亮说明（不能静默用旧版）"),
    (".replace(_dst)", "写回要用原子替换（半截写入不得留下坏模块）"),
    ("sys.modules.pop", "刷新后必须先摘 sys.modules 里的旧引导模块，否则 import 命中缓存"),
    ("BOOT_SELF", "加载日志要带内存指纹（磁盘 sha 区分不出内存里那份的新旧）"),
]

#: (notebook, cell 里的模块名, 仓库里对应文件)
PLAN: list[tuple[str, Path, str, str]] = [
    ("offline", OFFLINE_NB, "offline_boot", "offline_boot.py"),
    ("tailscale", TAILSCALE_NB, "notebook_boot", "notebook_boot.py"),
]


@pytest.mark.parametrize(("label", "nb", "module", "boot_py"), PLAN)
def test_boot_loader_speaks_the_same_language_on_both_notebooks(
    label: str, nb: Path, module: str, boot_py: str
) -> None:
    cell = boot_cell(nb, module)
    for needle, why in SHARED_NEEDLES:
        assert needle in cell, f"[{label}] {nb.name}: {why} —— 缺 {needle!r}"
    assert cell.index("sys.modules.pop") < cell.index(f"        import {module}\n"), (
        f"[{label}] 顺序必须是「先摘 sys.modules 再 import」"
    )
    assert "_branch.txt" not in cell, (
        f"[{label}] 「按分支名失效」的缓存策略已退役（它挡不住同分支的新旧）"
    )
    assert f'"{boot_py}"' in cell, f"[{label}] cell 没拉 {boot_py}"
    assert (NN / "remote" / boot_py).is_file(), f"[{label}] cell 要拉 {boot_py}，仓库里却没有"
    ast.parse(cell)  # cell 必须仍是合法 python


def test_both_boot_modules_carry_a_memory_fingerprint() -> None:
    """磁盘 sha 骗得过（刷新过就是新的），模块对象骗不过 —— 见 2026-09-25 事故。"""
    for mod, py in ((offline_boot, "offline_boot.py"), (notebook_boot, "notebook_boot.py")):
        got = getattr(mod, "BOOT_SELF", None)
        assert isinstance(got, str) and got.strip(), (
            f"{py} 缺 BOOT_SELF —— notebook 的加载日志就只剩磁盘 sha，而那正是骗过 09-25 事故的判据"
        )
