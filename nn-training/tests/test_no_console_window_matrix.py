"""test_no_console_window_matrix.py — 黑窗的**运行期判据**（2026-10-17）。

## 为什么光有静态守卫不够

`tests/test_no_console_window_py.py` 守的是「**新写的** spawn 带没带旗标」——它证明不了
「**现有的**入口在真机上不弹窗」：旗标写对了但被别的地方吞掉、或某个入口走了别的机制
（`os.startfile` / 第三方库自己 spawn），静态扫描都看不见。

⇒ 本文件的判据是**机制级**的，且与 details §20 的实测口径同源：

  1. 起一个**没有控制台**的子进程（`CREATE_NO_WINDOW` —— 复刻 `spawnBg` 出来的那种进程，
     实测它的 `GetConsoleWindow() == 0`）；
  2. 在那个进程里跑一遍 hub 启动链上的**代表性 spawn 入口**（哨兵 / git / python）；
  3. 期间在**同一个进程**里枚举桌面上的**可见** `ConsoleWindowClass` 窗口
     —— 它必须**一个都不新增**（新窗口正是用户看到的那块黑窗）。

第 3 步的口径是 details §20 明写的那条：「判据 = `GetConsoleWindow()` + `IsWindowVisible()`，
**conhost 计数区分不了隐藏/可见，别再用它**」。所以这里枚举的是**窗口**，不是 conhost 进程数。

## ⚠ 本用例**不能**当"链上无漏"的证据（2026-10-17 复测后如实标注）

同日的机制复测（`docs/nn/console.md` §2026-10-17-console-no-black-window-reprobe）把旧机制表
推翻了：**在无控制台进程里，裸 spawn 七种形态（python / cmd / shell=True / .bat / pwsh / bun /
git）一个都不会产生可见窗口**。所以本用例现在恒绿——它绿说明不了"我们的旗标起作用了"，
只说明"这些入口在当前机器上不产生窗口"。

它的价值 = **真机正向证据 + 机制探针**：若哪天某个入口真开始弹窗（换了 spawn 方式 / 引入
会 `AllocConsole` 的第三方），它会红，并打印新窗口的 hwnd/类名/路径。真正的"新代码不许裸 spawn"
靠 `tests/test_no_console_window_py.py`（静态）+ `dashboard/tests/no-console-window.test.ts`
与 `tests/no-console-window-tools.test.ts`（TS 两面）。

**黑窗真凶仍未定案**，抓现行工具：`nn-training/tools/console_window.py watch <秒数>`（跑着它去点「启动 hub」）；机制复测工具：`nn-training/tools/console_window.py probe`。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # = nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.platform_utils import POPEN_NO_WINDOW

#: 在**无控制台**子进程里跑的那个脚本：枚举可见控制台窗口 → 跑 spawn 矩阵 → 再枚举。
#: 输出一行 JSON（留给主进程断言）。
_CHILD = r'''
import ctypes, json, os, subprocess, sys
from ctypes import wintypes

sys.path.insert(0, os.environ["NN_ROOT"])
from common.platform_utils import POPEN_NO_WINDOW
from common.proc import run_capture


def visible_console_windows():
    """桌面上**可见**的控制台窗口 [(hwnd, title, pid)]。

    口径见 docs/nn/console.md §2026-10-13-console-no-black-window：conhost **进程**数分不出
    隐藏/可见，必须看窗口本身（类名 `ConsoleWindowClass` + `IsWindowVisible`）。
    """
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(wintypes.HWND(hwnd), cls, 256)
        if cls.value == "ConsoleWindowClass" and u.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, title, 512)
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append([int(hwnd), title.value, int(pid.value)])
        return True

    u.EnumWindows(proc(cb), 0)
    return found


# `GetConsoleWindow` 住 **kernel32**（不是 user32）——第一次跑就踩了这个。
res = {"console_window": int(ctypes.windll.kernel32.GetConsoleWindow())}
res["before"] = visible_console_windows()
errs = []

# ★ 采样必须在**跑矩阵的同时**进行：黑窗只在新控制台进程存活期间可见，而哨兵那种子进程
# 可能 100ms 级就退了 —— 「跑前快照 + 跑后快照」两次拍照**必然漏掉**它（自检当场证明过：
# 把哨兵改回裸 spawn，两快照版的判据照样绿）。所以起一个 10ms 采样的线程，累计**出现过的**
# 可见控制台窗口。
seen = set()
stop = __import__("threading").Event()


def _sampler():
    while not stop.is_set():
        for w in visible_console_windows():
            seen.add(tuple(w))
        __import__("time").sleep(0.01)


_sampler_thread = __import__("threading").Thread(target=_sampler, daemon=True)
_sampler_thread.start()

# 矩阵：hub 启动链上真的会被走到的 spawn 入口（三条各自是不同形态：
# 本仓唯一捕获原语 / 外部 exe / 解释器自身）。
try:
    run_capture(["git", "--version"])
except Exception as e:  # git 不在 PATH 也不该让用例红在环境上，但要留证
    errs.append(f"git: {type(e).__name__}: {e}")
try:
    run_capture([sys.executable, "-c", "pass"])
except Exception as e:
    errs.append(f"python: {type(e).__name__}: {e}")
try:
    from common.code_sentinel import run_import_sentinel

    # 真哨兵（2026-10-17 那次报障的真身就在它的子进程上）
    ok, kind, detail = run_import_sentinel(os.environ["NN_ROOT"], modules=("common",))
    res["sentinel"] = [bool(ok), kind, str(detail)[:80]]
except Exception as e:
    errs.append(f"sentinel: {type(e).__name__}: {e}")

stop.set()
_sampler_thread.join(1.0)
for w in visible_console_windows():  # 收尾再采一次（别漏掉刚出现的）
    seen.add(tuple(w))
res["seen"] = [list(w) for w in seen]
res["errs"] = errs
print("QQQ" + json.dumps(res))
'''


@pytest.mark.skipif(sys.platform != "win32", reason="黑窗是 Windows 控制台子系统的行为")
def test_no_visible_console_window_appears_in_a_consoleless_process() -> None:
    """★ 机制判据：无控制台进程里跑 spawn 矩阵 ⇒ 桌面上**不新增可见控制台窗口**。"""
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        env={"NN_ROOT": str(ROOT), "PATH": _path(), "SYSTEMROOT": _sysroot()},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        # 子进程自己也要无控制台 —— 这正是被测的前提（复刻 spawnBg 的形状）
        **POPEN_NO_WINDOW,
    )
    assert proc.returncode == 0, f"子进程失败：rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    line = next((ln for ln in (proc.stdout or "").splitlines() if ln.startswith("QQQ")), "")
    assert line, f"子进程没吐结果（stdout={proc.stdout!r} stderr={proc.stderr!r})"
    res = json.loads(line[3:])

    # ① 前提成立：这个进程真的没有控制台（否则下面测的是「有控制台」那条路径）
    assert res["console_window"] == 0, f"子进程拿到了控制台（前提不成立）：{res['console_window']}"
    assert res["errs"] == [], f"矩阵里有 spawn 直接失败（环境问题）：{res['errs']}"

    # ② 判据：整个矩阵运行期间**出现过的**可见控制台窗口，不得超出 baseline
    #    （`seen` 是 10ms 采样线程的累计 —— 单看跑前/跑后两次快照会漏掉一闪而过的窗口）
    before = {tuple(w) for w in res["before"]}
    after = {tuple(w) for w in res["seen"]}
    new = sorted(after - before)
    assert new == [], (
        "无控制台的进程里跑 spawn 时**新出现了可见控制台窗口**（黑窗）：\n  "
        + "\n  ".join(f"hwnd={h} title={t!r} pid={p}" for h, t, p in new)
        + "\n⇒ 链上还有 spawn 没带 CREATE_NO_WINDOW / windowsHide。"
    )


def _path() -> str:
    import os

    return os.environ.get("PATH", "")


def _sysroot() -> str:
    import os

    return os.environ.get("SYSTEMROOT", r"C:\Windows")
