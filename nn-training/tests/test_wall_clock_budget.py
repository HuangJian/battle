"""G17：生产面的墙钟直读**只减不增**（`python.refactor.md` §P1-6 / B6 的 clock 半边，2026-11-17）。

## 为什么需要这道闸

时间读数是全仓最容易加的接缝：`time.time()` 一行就有值，于是「多久算超时」「这一轮跑了多久」
「这个租约是不是过期了」各自在几百个地方就地读一次 —— 量到 **95 个文件 / 507 处**。代价不是
性能，而是**不可测**：一段逻辑要固定「现在几点」只能 `monkeypatch` 模块里的 `time`（改的是全局，
同进程所有用例一起变），或者干脆不测（于是就没人测超时与过期）。

B6 的解法是给 `Runtime` 两个可注入读数（`clock()` 墙钟 / `monotonic()` 单调钟，见
`common/runtime.py` 的「时钟」节）：拿到 runtime 的调用点写 `rt.clock()` / `rt.monotonic()`，
测试注一个手动时钟就完全确定。**但接缝本身不会让人少读时间** —— 所以配这道闸：存量按文件冻结，
新代码想再直读就得先在这里加额度，而加额度时你会看到「旁边那个文件已经迁过了」。

## 口径

* 计数 = AST 里 `time.time()` / `time.monotonic()` / `time.perf_counter()` 三种**调用**
  （只认点分形态；`from time import time` 全仓 0 处，将来若出现，请连同这条口径一起改）。
* 扫描面 = 生产包 `biz common hub remote train trainer worker`（`tools/` 与 `worker/scripts/`
  与 G3/G4/G13 同款豁免：一次性脚本不是产品面）。
* **豁免**：`common/runtime.py`（时钟的家 —— 它当然要读 `time`）与
  `remote/offline_boot.py` 这类引导期单文件（`code.zip` 之前就要能跑，**不许** import `common.*`
  ⇒ 它只能本地读；与 G13 的豁免同一理由，不是额度）。
* 基线在 `tests/golden/wall_clock_baseline.json`，按文件冻结；另有一条「基线只许调小」防额度回流
  （与 G3/G4/G6/G9/G13 同形），一条扫描入口自检，一条**两向护栏**：`Runtime` 的读数必须真的被
  调用点用着（否则「迁移被无声回退」与「扫描入口坏了」都是同一种绿色）。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

from tests.helpers.budget import sanity_floor
from tests.helpers.remote_dag import STANDALONE_BOOT_MODULES

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "wall_clock_baseline.json"

#: 生产包（`tools/` 与 `worker/scripts/` 不数 —— 与 G3/G4/G13 同款）。
FACE = ("biz", "common", "hub", "remote", "train", "trainer", "worker")

#: 时钟的家：`Runtime(clock=, monotonic=)` 的缺省就在这个文件里，它当然直读 `time`。
CLOCK_HOME = "common/runtime.py"

#: 豁免文件（相对 `nn-training/`）：引导期单独拉取，顶层不得 import 仓内 ⇒ 只能本地读。
EXEMPT = frozenset(f"{m.replace('.', '/')}.py" for m in STANDALONE_BOOT_MODULES)

#: 三种直接读钟形态（点分名，见模块 docstring 的口径）。
READS = ("time.time", "time.monotonic", "time.perf_counter")

#: `Runtime` 的两个读数（`rt.clock()` / `ex.monotonic()`）：这条护栏数它们**被调用**的次数。
SEAM_METHODS = ("clock", "monotonic")

#: 迁移处数下限：`common/distribution.post_weights_parallel` 的 4 处计时。低于它 = 有人把注入的
#: 时钟换回了模块级 `time`，而「同 seed 同读数」不会再有人发现。
SEAM_FLOOR = 4


def _iter_face_py() -> list[Path]:
    out: list[Path] = []
    for pkg in FACE:
        out.extend(sorted((ROOT / pkg).rglob("*.py")))
    return out


def _scan_file(src: str) -> tuple[int, int]:
    """一次 AST 里的 `(墙钟直读次数, Runtime 读数被调用次数)`。注释 / docstring 天然不算。"""
    reads = 0
    seam = 0
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name in READS:
            reads += 1
        elif (
            "." in name and name.rsplit(".", 1)[-1] in SEAM_METHODS and not name.startswith("time.")
        ):
            # 只认**点分**调用（`rt.clock()` / `ex.monotonic()`）：裸名 `clock()` 是别的函数
            # （`common/logutil.tag_logger` 的回调就叫这个名字），数进来会把护栏的账算乱。
            seam += 1
    return reads, seam


@cache
def _scan() -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    seam = 0
    for p in _iter_face_py():
        rel = p.relative_to(ROOT).as_posix()
        if rel == CLOCK_HOME or rel in EXEMPT:
            continue
        reads, hits = _scan_file(p.read_text(encoding="utf-8", errors="replace"))
        seam += hits
        if reads:
            counts[rel] = reads
    return counts, seam


@cache
def _baseline() -> dict[str, int]:
    raw: dict[str, dict[str, int]] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return raw["wall_clock_reads"]


def test_no_new_wall_clock_reads() -> None:
    actual = _scan()[0]
    baseline = _baseline()
    lines = [
        f"  {rel}: {n} 处（基线 {baseline.get(rel, 0)}）"
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    ]
    assert not lines, (
        "生产面新增了墙钟直读：\n"
        + "\n".join(lines)
        + "\n  手边有 `Runtime` 就写 `rt.clock()`（时间戳）/ `rt.monotonic()`（时长、超时）——"
        "注入的时钟让这段逻辑可测；确实只能就地读（拿不到 runtime、且只此一处）的，"
        "加到 `tests/golden/wall_clock_baseline.json` 对应文件上并在提交信息里写清为什么。"
    )


def test_baseline_only_shrinks() -> None:
    """读得少了必须同步调小基线，否则旧额度会一直被放行。"""
    actual = _scan()[0]
    baseline = _baseline()
    lines = [
        f"  {rel}: 基线 {base}，实际 {actual.get(rel, 0)}"
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    ]
    assert not lines, (
        "以下文件的墙钟直读已经减少，但 `tests/golden/wall_clock_baseline.json` 还是旧数字：\n"
        + "\n".join(lines)
        + "\n  改成实际值（归零的条目直接删掉）。"
    )


def test_scan_floor() -> None:
    """扫描入口自检：AST 没数到东西（walk 坏了 / 包名改了）时立刻红。"""
    total = sum(_scan()[0].values())
    assert total >= sanity_floor(_baseline()), f"只数到 {total} 处墙钟直读，扫描入口可能坏了"


def test_the_seam_is_actually_used() -> None:
    """两向护栏：`Runtime` 的读数必须真的在调用点用着（迁移被无声回退 ⇒ 红）。"""
    _, seam = _scan()
    assert seam >= SEAM_FLOOR, (
        f"`Runtime.clock()` / `Runtime.monotonic()` 的调用点只剩 {seam} 处（下限 {SEAM_FLOOR}）——"
        "不是引擎坏了，就是有人把注入的时钟换回了模块级 `time`"
    )


def test_scan_counts_only_real_reads() -> None:
    """口径自检：注释 / docstring / 字符串里的字样不算，三种形态各算一次。"""
    src = (
        '"""文档里写 time.time() 不算（这是 docstring）。"""\n'
        "import time\n"
        "A = time.time()\n"
        'B = "time.monotonic()"\n'
        "C = time.monotonic()\n"
        "D = time.perf_counter()  # time.time() 在注释里\n"
        "E = rt.clock()\n"
        "F = ex.monotonic()\n"
    )
    assert _scan_file(src) == (3, 2)
