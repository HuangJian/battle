"""test_no_console_window_py.py — **python 侧**不许再出现「裸 spawn」的黑窗（2026-10-17）。

## 为什么必须补这一面（用户第三次报障）

机制（`docs/nn/console.md` §2026-10-13-console-no-black-window 的本机实测）：

| 父进程的控制台 | 子进程 spawn | 子进程拿到的控制台 |
|---|---|---|
| 有 | 裸 spawn | 继承 ⇒ 无新窗口 |
| **无**（`spawnBg` 的 detached + windowsHide 出来的都是这种） | 裸 spawn | **Windows 新建一块可见控制台 ⇒ 黑窗** |
| 无 | 带 `CREATE_NO_WINDOW` | 无控制台 ✓ |

2026-10-13 那次只补了 TS 两个面（`dashboard/src/**` + `tools/**`）与 6 个 python 文件，
**没有守卫**。2026-10-17 用户再报「启动 hub 时黑窗一闪而过」，真身 = `common/code_sentinel.py`
的哨兵子进程（`python -c`，跑 import 检查，秒级）——它就在**打包代码快照**那条启动必走的链上，
而同文件的 `_git_head` 早就补过了（漏一处就复发，正是「没有守卫」的形状）。

⇒ 本文件 = python 侧的**结构守卫**：`nn-training/**` 的每个子进程调用都必须「带旗标」或
「走本仓已知的安全包装」，否则红。

## 判据（三层，全部是 AST；不读源码文本）

1. **带旗标**：调用实参里出现 `POPEN_NO_WINDOW` / `CREATE_NO_WINDOW` / `creationflags` /
   `windowsHide`。
2. **走安全包装**：`**popen_kwargs(...)` / `**popen_own_group()` / `run_capture(...)` 等
   —— 名单写死（它们各自在 `common/platform_utils.py` / `common/proc.py` 里带旗标）。
3. **模块内的本地包装**：被调用的名字在**同一模块**里有 `def`（如 `tools/task.py::run`、
   `tools/bootstrap.py::run`），且**递归**（≤3 层）能在其函数体里找到第 1/2 层的证据。

## 白名单与豁免

* 白名单（**旗标的定义处**）：`common/platform_utils.py`、`common/proc.py`、`tools/bootstrap.py`
  （后者刻意零依赖、自带 `_popen_kwargs`，见它自己的 docstring）。
* 豁免清单 `EXEMPT` 逐条列出**为什么这一处不该带**（目前只有云机/GitHub-raw 拉取的模块，
  它们在 Linux 上跑且刻意零 `common` 依赖）。豁免是**有账的**：每条都要写理由，
  且下面的 `test_exemptions_are_still_real_files` 防「文件改名后豁免变成幽灵」。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # = nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKIP_DIRS = {".venv", "__pycache__", ".mypy_cache", "node_modules", ".ruff_cache"}

#: 扫描面 = **生产代码**（`tests/` 与 `tmp/` 不扫）。
#:
#: 为什么不扫 `tests/`：那里满是 `h.run(...)` / `runner.run(...)` 这类**对象方法的同名调用**
#: （子进程只是其中一小撮），逐条辨认的收益远小于把守卫变成噪音的成本。守卫要守的是
#: 「**运行时会跑到**的 spawn」——测试自己起子进程时，父进程通常有控制台（pytest 在终端里跑）。
#: `tmp/` 同理（临时脚本、未跟踪）。
SCAN_DIRS = ("hub", "common", "biz", "worker", "trainer", "tools", "e2e", "remote")

#: 子进程 API：`subprocess.X` 与裸名（`from subprocess import run` 形态）。
CALLS = {
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "os.system",
    "os.popen",
}
BARE = {
    "Popen",
    "run",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
    "system",
    "popen",
}

#: 第 1 层证据：实参里出现这些（**大小写不敏感**）就说明旗标在。
#:
#: 判据刻意取「`NO_WINDOW` 这个**词根**」而不是几个具体常量名：本仓的载体已经有三代
#: （`POPEN_NO_WINDOW` / `popen_kwargs()` / `remote/offline_boot` 就地定义的 `_NT_NO_WINDOW`），
#: 枚举常量名必然漏（漏的那次就是 `_NT_NO_WINDOW`）。`CREATE_NEW_CONSOLE` 刻意**不算**安全
#: ——它是「新开一块**可见**控制台」，与 `NO_WINDOW` 相反。
FLAG_MARKERS = ("NO_WINDOW", "CREATIONFLAGS", "WINDOWSHIDE")


def _has_flag(span: str) -> bool:
    up = span.upper()
    return any(m in up for m in FLAG_MARKERS)

#: 第 2 层证据：本仓已知的安全包装（各自实现里带旗标，有单测）。
SAFE_WRAPPERS = (
    "run_capture",
    "popen_kwargs",
    "popen_own_group",
    "_popen_kwargs",
    "popen_no_window",  # 未来若再加别名
)

#: 白名单：**旗标的定义处**，它们本来就该直接碰 subprocess。
ALLOWED_FILES = {
    "common/platform_utils.py": "POPEN_NO_WINDOW / popen_kwargs / popen_own_group 的定义处",
    "common/proc.py": "run_capture 的定义处（唯一捕获入口，自带旗标）",
    "tools/bootstrap.py": "装依赖之前跑，刻意零依赖 ⇒ 自带 _popen_kwargs（见其 docstring）",
}

#: 豁免（**逐条写理由**；都是云机 / GitHub-raw 单独拉取的模块：Linux 上跑、刻意不依赖 common）。
EXEMPT: dict[str, str] = {
    #: ★ 机制探测工具：它的**被测变量**就是「带旗标 vs 裸 spawn」两档（`opts` 由矩阵逐例给）。
    #: 给它加旗标等于把对照组删掉 —— 这是唯一一处「裸」是**故意**的地方。
    "tools/console_window.py": "黑窗观测/探测工具（probe 子命令里裸 spawn 是被测变量）",
    "remote/tailscale_boot.py": "云机-only（tailscale 只存在于 Linux 云环境）",
    "remote/notebook_boot.py": "notebook 引导，从 GitHub raw 单独拉取（拿不到 common）",
    "remote/push_bootstrap.py": "同上（Colab/Kaggle 引导）",
    "remote/notebook_runtime.py": "同上（notebook 内运行时）",
    "remote/colab_bc.py": "同上（Colab 专用）",
    "remote/rollout_node.py": "同上（云机 rollout 节点）",
}


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _subprocess_faces(tree: ast.Module) -> tuple[set[str], set[str]]:
    """`(子进程模块别名, 从 subprocess 导入的裸名)` —— 裸名判据必须绑定 import。

    没有这一层时，`trainer/loop.py` 里一个叫 `run()` 的**局部/对象方法**会被误判成
    `subprocess.run`（首版 121 条违规里绝大多数是这么来的）。
    """
    mods = {"subprocess"}
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    mods.add(a.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and (node.module or "") == "subprocess":
            for a in node.names:
                bare.add(a.asname or a.name)
    return mods, bare


def _span(node: ast.Call) -> str:
    """调用实参文本（`ast.unparse`：结构化、不受注释/字符串影响）。"""
    parts = [ast.unparse(a) for a in node.args]
    parts += [ast.unparse(k.value) for k in node.keywords]
    return " ".join(parts)


def _has_evidence(body: list[ast.stmt], local: dict[str, ast.FunctionDef], depth: int = 0) -> bool:
    """函数体里有没有「旗标 / 安全包装 / 再一层本地包装」的证据（递归 ≤3 层）。"""
    if depth > 3:
        return False
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        span = _span(node)
        if _has_flag(span):
            return True
        if any(w in span for w in SAFE_WRAPPERS):
            return True
        short = name.split(".")[-1]
        if short in local and _has_evidence(local[short].body, local, depth + 1):
            return True
    return False


def collect(virtual: str | None = None) -> tuple[list[str], int]:
    """扫描 `nn-training/**`，返回 `(违规清单, 扫描到的调用数)`。

    `virtual`：注入一段假源码（自检用，见文件末的用例）——不走盘。
    """
    offenders: list[str] = []
    calls = 0
    files: list[tuple[str, str]] = []
    if virtual is not None:
        files.append(("__virtual__.py", virtual))
    else:
        for p in sorted(ROOT.rglob("*.py")):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            rel = p.relative_to(ROOT).as_posix()
            files.append((rel, p.read_text(encoding="utf-8")))
    for rel, src in files:
        if rel in ALLOWED_FILES or rel in EXEMPT:
            continue
        if virtual is None and not any(rel.startswith(d + "/") for d in SCAN_DIRS):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        sp_mods, sp_bare = _subprocess_faces(tree)
        local: dict[str, ast.FunctionDef] = {
            n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            short = name.split(".")[-1]
            head, _, tail = name.rpartition(".")
            is_sub = (head in sp_mods and name in CALLS) or (
                not head and short in sp_bare
            )
            if not is_sub:
                continue
            calls += 1
            span = _span(node)
            if _has_flag(span):
                continue
            if any(w in span for w in SAFE_WRAPPERS):
                continue
            if short in local and _has_evidence(local[short].body, local):
                continue
            offenders.append(f"{rel}:{node.lineno}  {name}({span[:150]})")
    return offenders, calls


def test_no_bare_subprocess_in_nn_training() -> None:
    """★ 主判据：`nn-training/**` 的每个子进程调用都带旗标 / 走安全包装。"""
    offenders, calls = collect()
    assert calls > 10, f"扫描器失效了（只看到 {calls} 个调用）——别静默通过"
    assert offenders == [], (
        "以下调用在**没有控制台**的父进程下会新建可见控制台（黑窗）：\n  "
        + "\n  ".join(offenders)
        + "\n修法：`**POPEN_NO_WINDOW`（或走 `common.proc.run_capture` / `popen_kwargs`）。"
    )


def test_scanner_catches_a_bare_call_and_passes_a_flagged_one() -> None:
    """★ 夹具自检（守门测试必须能被证明「会红」）：裸调用要被抓，带旗标的不能被抓。"""
    bare = "import subprocess\nsubprocess.run(['git', 'rev-parse', 'HEAD'])\n"
    bad, n = collect(virtual=bare)
    assert n == 1 and len(bad) == 1, f"裸调用没被抓到：{bad}"
    good = "import subprocess\nfrom common.platform_utils import POPEN_NO_WINDOW\nsubprocess.run(['git'], **POPEN_NO_WINDOW)\n"
    ok, _ = collect(virtual=good)
    assert ok == [], f"带旗标的被误报：{ok}"
    wrapped = "import subprocess\nfrom common.proc import run_capture\nrun_capture(['git'])\n"
    ok2, _ = collect(virtual=wrapped)
    assert ok2 == [], f"安全包装被误报：{ok2}"
    local_wrap = (
        "import subprocess\n"
        "from common.platform_utils import POPEN_NO_WINDOW\n"
        "def run(cmd):\n    return subprocess.run(cmd, **POPEN_NO_WINDOW)\n"
        "run(['git'])\n"
    )
    ok3, _ = collect(virtual=local_wrap)
    assert ok3 == [], f"本地包装被误报：{ok3}"


def test_exemptions_are_still_real_files() -> None:
    """豁免不许变成幽灵（文件改名/搬走后，清单必须跟着改——否则守的是空气）。"""
    for rel in EXEMPT:
        assert (ROOT / rel).is_file(), f"豁免清单里的 {rel} 不存在了（改名/搬走？把这条删掉）"
    for rel in ALLOWED_FILES:
        assert (ROOT / rel).is_file(), f"白名单里的 {rel} 不存在了"
