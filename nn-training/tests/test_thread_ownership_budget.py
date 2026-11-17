"""G18：裸 `threading.Thread(...)` 的存量**只减不增**（`python.refactor.md` §P1-6 / B6 的最后一半）。

## 为什么需要这道闸

`ThreadPoolExecutor` 的账 2026-11-17 收完了（G16：生产包 0 处就地建池，全都走 `Runtime`）。裸
`threading.Thread(...)` 还剩一批：量到 **51 处 / 36 文件**，问题与池同源 —— 名字各写各的（G15 只管
「有没有名字」，不管「归属于谁」）、没人 join（进程退出靠 daemon 硬切，在飞的活静默没掉）、
转储里也看不出它属于哪一轮。

`Runtime.thread(target, name=…)` 是它们的所有者（统一名 `<scope>-<name>`、登记、`close()` 时 join、
`describe()["threads"]` 报是否还活着）。**但接缝不会让人自动少造线程** —— 所以配这道闸：存量按文件
冻结，新代码想再就地 `Thread(...)` 就得先在这里加额度，而加额度时你会看到「同一族里已经有人搬过了」。

为什么不一次搬完：剩下的站点多数在**模块级函数**里（`_cancel_remote_targets` / 一次性扇出 /
`rearm` 补孵），手上没有 `Runtime` —— 要搬得先把 runtime 从调用方传下来（§P1-4 的接缝），
那是设计题；为了少一行在模块里造个隐藏单例，只是把「无主线程」换成「无主 Runtime」。

## 口径

* 计数 = AST 里任何 `Thread(...)` 调用（`threading.Thread` / `Thread` 两种写法；与 G15 同一识别）。
* 扫描面 = 生产包 `biz common hub remote train trainer worker`；`worker/scripts/` 与 `tools/`
  与 G3/G4/G14/G15 同款豁免（一次性脚本不是产品面）。
* **豁免**：`common/runtime.py`（所有者自己当然要造线程）与 `remote/offline_boot.py`
  （引导期单文件，`code.zip` 之前就要能跑，**不许** import `common.*` ⇒ 它造不了 Runtime）。
* 基线在 `tests/golden/thread_construction_baseline.json`，按文件冻结；另有一条「基线只许调小」、
  一条扫描入口自检，以及一条**两向护栏**：`rt.thread(...)` 的调用点必须真的在用。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

import pytest

from tests.helpers.budget import sanity_floor
from tests.helpers.remote_dag import STANDALONE_BOOT_MODULES

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "thread_construction_baseline.json"

#: 生产包（`tools/` 与 `worker/scripts/` 不数 —— 与 G3/G4/G15 同款）。
FACE = ("biz", "common", "hub", "remote", "train", "trainer", "worker")

#: 线程的所有者：它自己当然要造线程。
OWNER_HOME = "common/runtime.py"

#: 豁免文件（相对 `nn-training/`）：引导期单独拉取，顶层不得 import 仓内。
EXEMPT = frozenset(f"{m.replace('.', '/')}.py" for m in STANDALONE_BOOT_MODULES)

#: 迁移处数下限：`Runtime.thread(...)` 至少还有这么多调用点（0 = 迁移被无声回退）。
MIGRATED_FLOOR = 1


def _iter_face_py() -> list[Path]:
    out: list[Path] = []
    for pkg in FACE:
        for p in sorted((ROOT / pkg).rglob("*.py")):
            if "scripts" in p.parts:
                continue
            out.append(p)
    return out


def _scan_file(src: str) -> tuple[int, int]:
    """一次 AST 里的 `(就地 Thread(...) 处数, Runtime.thread(...) 调用处数)`。"""
    direct = 0
    owned = 0
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if called == "Thread":
            direct += 1
        elif called == "thread" and isinstance(f, ast.Attribute):
            # 只认**点分**调用（`rt.thread(...)`）：模块里的 `thread(...)` 是别的函数。
            owned += 1
    return direct, owned


@cache
def _scan() -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    owned = 0
    for p in _iter_face_py():
        rel = p.relative_to(ROOT).as_posix()
        if rel == OWNER_HOME or rel in EXEMPT:
            continue
        direct, hits = _scan_file(p.read_text(encoding="utf-8", errors="replace"))
        owned += hits
        if direct:
            counts[rel] = direct
    return counts, owned


@cache
def _baseline() -> dict[str, int]:
    raw: dict[str, dict[str, int]] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return raw["thread_construction"]


def test_no_new_bare_threads() -> None:
    actual = _scan()[0]
    baseline = _baseline()
    lines = [
        f"  {rel}: {n} 处（基线 {baseline.get(rel, 0)}）"
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    ]
    if lines:
        pytest.fail(
            "生产面新增了就地 `threading.Thread(...)`：\n"
            + "\n".join(lines)
            + '\n  手边有 `Runtime` 就写 `rt.thread(target, name="worker-3")` —— 名字统一成'
            ' `<scope>-<name>`、登记在本域、`close()` 时 join、`describe()["threads"]` 可读；'
            "确实拿不到 runtime 的（临时扇出 / 一次性 reaper）加到"
            " `tests/golden/thread_construction_baseline.json` 上并在提交信息里写清为什么。"
        )


def test_baseline_only_shrinks() -> None:
    """搬走一处必须同步调小基线，否则旧额度会一直被放行。"""
    actual = _scan()[0]
    baseline = _baseline()
    lines = [
        f"  {rel}: 基线 {base}，实际 {actual.get(rel, 0)}"
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    ]
    if lines:
        pytest.fail(
            "以下文件就地造的线程已经减少，但 `tests/golden/thread_construction_baseline.json` 还是旧数字：\n"
            + "\n".join(lines)
            + "\n  改成实际值（归零的条目直接删掉）。"
        )


def test_scan_floor() -> None:
    """扫描入口自检：AST 没数到东西（walk 坏了 / 包名改了）时立刻红。"""
    total = sum(_scan()[0].values())
    assert total >= sanity_floor(_baseline()), f"只数到 {total} 处就地建线程，扫描入口可能坏了"


def test_the_owner_is_actually_used() -> None:
    """两向护栏：`Runtime.thread(...)` 必须真的在调用点用着（迁移被无声回退 ⇒ 红）。"""
    _, owned = _scan()
    assert owned >= MIGRATED_FLOOR, (
        f"`Runtime.thread(...)` 的调用点只剩 {owned} 处（下限 {MIGRATED_FLOOR}）——"
        "不是所有者坏了，就是有人把裸线程又写回来了"
    )


def test_scan_counts_only_real_constructions() -> None:
    """口径自检：两种写法都算，`rt.thread(...)` 算「已归属」，注释 / 字符串不算。"""
    src = (
        '"""文档里写 threading.Thread(...) 不算（这是 docstring）。"""\n'
        "import threading\n"
        "a = threading.Thread(target=f, name='a')\n"
        'b = "Thread(target=f)"\n'
        "c = Thread(target=f, name='c')\n"
        "d = rt.thread(f, name='d')\n"
    )
    assert _scan_file(src) == (2, 1)
