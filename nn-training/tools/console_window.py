"""console_window.py — 控制台黑窗的**观测与探测**工具（两个子命令，一份实现）。

    nn-training/.venv/Scripts/python.exe nn-training/tools/console_window.py probe
    nn-training/.venv/Scripts/python.exe nn-training/tools/console_window.py watch 90

## `probe` —— 机制探测：无控制台的父进程 spawn 子进程会不会弹窗？

自己用 `CREATE_NO_WINDOW` 起一个**无控制台**的子进程（复刻 `spawnBg` 出来的那种父进程），
在里面逐形态 spawn（python / cmd.exe / `shell=True` / `.bat` / pwsh / bun / git，各自裸与带旗标两档）。
判据 = **可见顶层窗口的差集**（8ms 采样 ⇒ 窗口存在 100ms 级也抓得到）。

**2026-10-17 首次运行的结果**：七种形态**全部无新窗口**。⇒ `docs/nn/console.md`
§2026-10-13 那张「无控制台父进程 + 裸 spawn ⇒ Windows 新建可见控制台」的机制表**不成立**
（`CreateProcess` 的默认是继承；父没有 ⇒ 子也没有）。全部读数与推论 →
§2026-10-17-console-no-black-window-reprobe。

## `watch` —— 抓现行：黑窗到底是哪一块？

按**创建瞬间**打印新窗口（类名/标题/pid/宿主进程）与新建进程（含父进程名）：

    [ +3.2s] WINDOW class=ConsoleWindowClass title='...' pid=1234 proc=conhost.exe
    [ +3.4s] PROC   pid=1234 ppid=5678(parent.exe) name=cmd.exe

跑着它去点「启动 / 重启 hub」即可。窗口采样 8ms、进程表 300ms，两种采样都走下面的
ctypes 实现 —— **本工具一个进程都不起**（观测工具自己每秒 3 次 spawn 会把现场搅浑）。

## 判据口径（与 `docs/agents.details.md` §20 同源）

只看**可见顶层窗口**：conhost **进程数**区分不了「隐藏」与「可见」，那份材料自己就写着
别再用它。本文件是这条口径的**唯一实现**（两个子命令共用，不再各写一份）。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

#: `CREATE_NO_WINDOW`（与 `common.platform_utils.POPEN_NO_WINDOW` 同值）。
CREATE_NO_WINDOW = 0x08000000

_user32 = ctypes.windll.user32  # type: ignore[attr-defined]
_kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

#: `EnumWindows` 回调的工厂函数。Windows 上必须是 **WINFUNCTYPE**（stdcall；x86 上与
#: `CFUNCTYPE` 不通用，而 `EnumWindows` 按 stdcall 回调），但 typeshed 的 `ctypes` 只在
#: win32 分支定义这个名字 —— 门禁在 POSIX 上跑 mypy，裸引用会报 `attr-defined`（2026-10-17
#: 修）。按 `sys.platform` 取：mypy 会把另一平台的分支判为不可达（`--platform` 语义），
#: 两边都零错误，也不需要往真代码上贴 `# type: ignore`。
if sys.platform == "win32":
    _WNDENUMPROC = ctypes.WINFUNCTYPE
else:  # 本工具只在 Windows 有意义；POSIX 上仅需可被 mypy / 导入 分析
    _WNDENUMPROC = ctypes.CFUNCTYPE

T0 = time.time()


# ─────────────────────────────── 观测原语（唯一实现） ───────────────────────────────


def console_window() -> int:
    """本进程的控制台窗口句柄（0 = **没有控制台**；它在 kernel32，不在 user32）。"""
    return int(_kernel32.GetConsoleWindow())


def visible_windows() -> list[tuple[int, str, str, int]]:
    """桌面上**可见**顶层窗口 `[(hwnd, 类名, 标题, pid)]`。"""
    out: list[tuple[int, str, str, int]] = []
    proc = _WNDENUMPROC(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd: int, _lparam: int) -> bool:
        if _user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls, 256)
            title = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, title, 256)
            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((int(hwnd), cls.value, title.value[:60], int(pid.value)))
        return True

    _user32.EnumWindows(proc(cb), 0)
    return out


def proc_name(pid: int) -> str:
    """pid → 可执行文件名（读不到返回 `?`）。"""
    try:
        h = _kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return "?"
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        ok = _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        _kernel32.CloseHandle(h)
        return os.path.basename(buf.value) if ok else "?"
    except Exception:
        return "?"


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def snapshot_procs() -> dict[int, tuple[int, str]]:
    """`pid -> (ppid, name)`（纯 ctypes，不 spawn）。"""
    th32cs_snapprocess = 0x00000002
    res: dict[int, tuple[int, str]] = {}
    snap = _kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if snap == -1:
        return res
    try:
        e = _ProcessEntry32()
        e.dwSize = ctypes.sizeof(_ProcessEntry32)
        ok = _kernel32.Process32First(snap, ctypes.byref(e))
        while ok:
            res[int(e.th32ProcessID)] = (
                int(e.th32ParentProcessID),
                e.szExeFile.decode("mbcs", "replace"),
            )
            ok = _kernel32.Process32Next(snap, ctypes.byref(e))
    finally:
        _kernel32.CloseHandle(snap)
    return res


class _WindowWatcher:
    """后台线程按 `interval` 采样可见窗口，累计**出现过的**窗口（含一闪而过的）。

    为什么必须并发采样（2026-10-17 自检踩的）：「跑前快照 + 跑后快照」两次拍照**必然漏掉**
    只活 100ms 的黑窗 —— 第一版判据就是这么假绿的。
    """

    def __init__(self, interval: float = 0.008) -> None:
        self.interval = interval
        self.seen: dict[tuple[int, str, str, int], int] = {}
        self.samples = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.samples += 1
            for w in visible_windows():
                self.seen[w] = self.seen.get(w, 0) + 1
            time.sleep(self.interval)

    def start(self) -> _WindowWatcher:
        self._thread.start()
        return self

    def stop(self) -> list[list[object]]:
        self._stop.set()
        self._thread.join(1.0)
        for w in visible_windows():  # 收尾再采一次：别漏掉刚出现的
            self.seen[w] = self.seen.get(w, 0) + 1
        return [list(w) for w in self.seen]


def _stamp() -> str:
    return f"[{time.time() - T0:+6.1f}s]"


# ─────────────────────────────── probe 子命令 ───────────────────────────────


def _probe(name: str, argv: list[str] | str, opts: dict | None = None) -> dict:
    """跑一个形态，返回期间**新出现的**可见窗口（`opts` 由矩阵逐例给：带旗标 / 裸 两档）。"""
    base = set(visible_windows())
    watcher = _WindowWatcher().start()
    err = ""
    try:
        subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **(opts or {}),
        )
    except Exception as e:  # 探测：任何失败都记下来，不让自己挂掉
        err = f"{type(e).__name__}: {e}"
    new = [w for w in watcher.stop() if tuple(w) not in base]
    return {"case": name, "samples": watcher.samples, "new_n": len(new), "new": new[:5], "err": err}


def _matrix() -> list[tuple[str, list[str] | str, dict]]:
    """要测的形态（每种都是「父进程无控制台」下的一次 spawn）。"""
    bun = shutil.which("bun") or ""
    pwsh = shutil.which("pwsh") or ""
    git = shutil.which("git") or ""
    cmd = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    bat = Path(os.environ.get("TEMP", ".")) / "probe-black-window.bat"
    bat.write_text("@echo off\r\nping -n 2 127.0.0.1 >nul\r\n", encoding="ascii")

    out: list[tuple[str, list[str] | str, dict]] = [
        ("A_python_bare", [sys.executable, "-c", "import time; time.sleep(1.2)"], {}),
        ("B_cmd_c", [cmd, "/c", "ping -n 2 127.0.0.1 >nul"], {}),
        ("C_shell_true", f'"{sys.executable}" -c "import time; time.sleep(1.2)"', {"shell": True}),
        ("F_bat", [str(bat)], {}),
    ]
    if pwsh:
        out.append(("D_pwsh", [pwsh, "-NoProfile", "-Command", "Start-Sleep -Milliseconds 1200"], {}))
    if bun:
        out.append(("E_bun", [bun, "--version"], {}))
    if git:
        out.append(("G1_git_no_window", [git, "rev-parse", "HEAD"], {"creationflags": CREATE_NO_WINDOW}))
        out.append(("G3_git_bare", [git, "rev-parse", "HEAD"], {}))
        out.append(
            ("G4_git_ls_remote", [git, "ls-remote", "--heads", "."], {"creationflags": CREATE_NO_WINDOW})
        )
    return out


def cmd_probe() -> int:
    if "--direct" in sys.argv:
        res = {
            "console_window": console_window(),
            "mode": "direct",
            "cases": [_probe(*c) for c in _matrix()],
        }
        print("QQQ" + json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    # 默认：自己起一个**无控制台**的子进程，在它里面测（被测前提 = 父进程无控制台）。
    me = str(Path(__file__).resolve())
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import runpy, sys; sys.argv=[{me!r}, 'probe', '--direct'];"
            f"runpy.run_path({me!r}, run_name='__main__')",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        creationflags=CREATE_NO_WINDOW,
        env=dict(os.environ),
    )
    print(f"outer rc={p.returncode}")
    print(p.stdout[-6000:])
    if p.stderr.strip():
        print("STDERR:", p.stderr[-800:])
    return 0 if p.returncode == 0 else 1


# ─────────────────────────────── watch 子命令 ───────────────────────────────


def cmd_watch(seconds: float) -> int:
    win0 = visible_windows()
    consoles = sum(1 for w in win0 if "Console" in w[1] or "CASCADIA" in w[1].upper())
    print(f"{_stamp()} 监控开始（{seconds:.0f}s）—— 现在去控制台点「启动 hub」/ 触发黑窗。")
    print(f"{_stamp()} baseline：可见窗口 {len(win0)} 个（其中控制台类 {consoles} 个）")

    base = {tuple(w) for w in win0}
    watcher = _WindowWatcher().start()
    procs = snapshot_procs()
    stop = threading.Event()
    found: list[str] = []
    lock = threading.Lock()

    def report(line: str) -> None:
        with lock:
            found.append(line)
        print(line, flush=True)

    def watch_windows() -> None:
        reported = set(base)
        while not stop.is_set():
            for key in list(watcher.seen):
                if key in reported:
                    continue
                reported.add(key)
                _hwnd, cls, title, pid = key
                report(
                    f"{_stamp()} WINDOW class={cls} title={title!r} pid={pid} proc={proc_name(pid)}"
                )
            time.sleep(0.02)

    def watch_procs() -> None:
        nonlocal procs
        while not stop.is_set():
            cur = snapshot_procs()
            for pid, (ppid, name) in cur.items():
                if pid in procs:
                    continue
                parent = procs.get(ppid, (0, "?"))[1]
                report(f"{_stamp()} PROC   pid={pid} ppid={ppid}({parent}) name={name}")
            procs = cur
            time.sleep(0.3)

    t1 = threading.Thread(target=watch_windows, daemon=True)
    t2 = threading.Thread(target=watch_procs, daemon=True)
    t1.start()
    t2.start()
    time.sleep(seconds)
    stop.set()
    t1.join(1.0)
    t2.join(30)
    watcher.stop()
    print(
        f"{_stamp()} 结束：抓到 {len(found)} 条事件"
        + (
            "（关键：看第一条 WINDOW 的 class/pid/proc，再看对应 PROC 的 ppid 链）"
            if found
            else "（一条都没有 ⇒ 这次动作没产生新窗口）"
        )
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "probe":
        return cmd_probe()
    if len(argv) >= 2 and argv[1] == "watch":
        return cmd_watch(float(argv[2]) if len(argv) > 2 else 90.0)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
