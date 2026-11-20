"""forensics.py —— I1 第 0 步（hy E4）：OOM/磁盘取证插桩。

「rollout 150/150 settled → PPO 提交返回前进程消失、无堆栈」（c5-tick / c6-bonus 两起
同位置事故）的根因候选是 **OOM killer 与写盘失败**——两者都不留 Python 堆栈，
faulthandler 也不落盘。本模块在提交边界打内存/磁盘快照并落 jsonl `forensics` 事件，
让下一次事故有数可查：**最后一条快照就是临终状态**（RSS 峰值贴顶 = OOM 实锤；
disk_free ≈ 0 = 写盘失败实锤）。

快照字段：
  rss_mb           当前进程工作集（Windows GetProcessMemoryInfo / POSIX statm）
  rss_peak_mb      峰值工作集（Windows PeakWorkingSetSize / POSIX ru_maxrss）
  torch_alloc_mb   torch 分配器当前已分配（无 torch / 无 cuda → 缺省）
  disk_free_mb     关键路径所在分区余量（traj / weights 所在盘）

纪律：纯 stdlib + 可选 torch；**任何失败都降级为 0/缺省，绝不反杀训练**——
取证是诊断手段，不是新的故障面。

★ 2026-11-15（G9 第二种处置）：降级**不等于静默**。原来 `rss_mb()` 探不到就返回 `(0.0, 0.0)`，
disk 探不到就不写这个键 —— 而这份快照的读者恰恰是事故后的人：`rss_mb: 0` 与「探针自己失败了」
在 jsonl 里长得一模一样，于是「上一轮到底有没有 OOM」这个唯一的问题反而答不了。现在每个失败都
记进快照的 `probe_errors`（`{字段: "异常类型: 文本"}`），值与缺省语义全部不变。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from common.log import log

_MB = 1024.0 * 1024.0


def _note(errors: dict[str, str] | None, key: str, exc: BaseException) -> None:
    """把一次探针失败记进快照的 `probe_errors`（`errors=None` ⇒ 调用方不要证据，照旧静默）。

    只用类型名 + 一句话：这些异常来自平台 API（`ctypes` / `/proc` / `shutil` / torch），
    原文可能很长且带换行，落 jsonl 要能一行读完。
    """
    if errors is not None:
        errors[key] = f"{type(exc).__name__}: {exc}"


def _rss_mb_windows(errors: dict[str, str] | None = None) -> tuple[float, float] | None:
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # 仅 Windows 调用
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
        # ★ 签名必须显式声明，伪句柄还必须包成 `c_void_p`（2026-11-20 全量门禁偶发红）。
        #   现场：`probe_errors={'rss': 'ArgumentError: argument 1: OverflowError: int too long
        #   to convert'}`，且同一进程里**保质**（三条快照用例同一个 pid 连红）。根因不在本文件：
        #   `ctypes.windll` 是**每进程一份**的 `LibraryLoader`，**paddle**
        #   （`paddle/base/core.py`）把共享的 `kernel32.GetCurrentProcess.restype` 改成了
        #   `c_void_p` ⇒ 之后谁调 `GetCurrentProcess()` 拿到的都是 `0xFFFFFFFFFFFFFFFF`（而不再是
        #   未声明 restype 时按 `c_int` 截断的 -1）；而未声明 `argtypes` 的
        #   `GetProcessMemoryInfo` 把入参按 `c_int` 转换 ⇒ 溢出 ⇒ RSS 读数恒 0 且被当成「探针自己
        #   失败」。两条同时做才算根除：`argtypes` 让调用按指针宽度走，`c_void_p(handle)` 对「别人
        #   改过 restype」与「没人改过」两种状态都成立（`c_void_p(-1)` 与 `c_void_p(0xFFFF…)`
        #   是同一个值）。同族先例与更详的事故叙述：`hub/admin.py:rss_bytes`。
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_bool
        handle = ctypes.c_void_p(kernel32.GetCurrentProcess())
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.WorkingSetSize / _MB, pmc.PeakWorkingSetSize / _MB
    except Exception as e:  # 平台 API：异常族由 ctypes/PSAPI 决定（噪音不升级成故障）
        _note(errors, "rss", e)
        return None


def _rss_mb_posix(errors: dict[str, str] | None = None) -> tuple[float, float] | None:
    # 首选 /proc/self/status：VmRSS/VmHWM 出自同一 mm 账本快照，peak >= cur 由
    # 内核保证。getrusage 的 ru_maxrss 在部分内核（WSL2 实测）会持续滞后于实际
    # 常驻集，与 statm 混用会出现 peak < cur 的假象（test_rss_mb_sane 曾确定性红）。
    try:
        cur = peak = 0.0
        with open("/proc/self/status", encoding="ascii") as f:
            for ln in f:
                if ln.startswith("VmRSS:"):
                    cur = float(ln.split()[1])  # kB
                elif ln.startswith("VmHWM:"):
                    peak = float(ln.split()[1])
        if cur > 0 and peak > 0:
            return cur / _MB, peak / _MB
    except Exception as e:  # /proc 不可读（容器、非 Linux、权限）—— 下面还有 rusage 兜底
        _note(errors, "rss_proc", e)
    # 降级路径（非 Linux POSIX，如 macOS：无 /proc）
    try:
        import resource

        ru = resource.getrusage(resource.RUSAGE_SELF)  # POSIX 专用
        # ru_maxrss：Linux 单位 KB，macOS 单位字节
        scale = 1024.0 if sys.platform.startswith("linux") else 1.0
        peak = float(ru.ru_maxrss) * scale
        cur = peak
        try:
            page = os.sysconf("SC_PAGE_SIZE")  # POSIX 专用
            with open("/proc/self/statm", encoding="ascii") as f:
                cur = float(int(f.read().split()[1]) * page)
        except Exception as e:  # 没有 statm ⇒ 退回「峰值当当前值」（rush 的已知偏差）
            _note(errors, "rss_statm", e)
        return cur / _MB, peak / _MB
    except Exception as e:  # 连 rusage 都拿不到（非 POSIX / 被禁）⇒ 真·未知
        _note(errors, "rss", e)
        return None


def rss_mb(errors: dict[str, str] | None = None) -> tuple[float, float]:
    """(当前 RSS, 峰值 RSS) 单位 MB；任何失败返回 (0.0, 0.0)——绝不出错。

    `errors` 非 None 时把失败原因记进去（`snapshot()` 用它给事故后的人留证据）。
    """
    try:
        r = _rss_mb_windows(errors) if os.name == "nt" else _rss_mb_posix(errors)
    except Exception as e:  # 探针自身的 bug 也不许把取证变成新的故障面
        _note(errors, "rss", e)
        return (0.0, 0.0)
    return r if r is not None else (0.0, 0.0)


def torch_alloc_mb(errors: dict[str, str] | None = None) -> float | None:
    """torch 分配器当前已分配 MB；无 torch / 无 cuda → None（CPU 训练无分配器池可看）。"""
    try:
        import torch

        if torch.cuda.is_available():
            return float(torch.cuda.memory_allocated()) / _MB
    except Exception as e:  # 无 torch / 驱动没起来：None 是正常答案，但**为什么**要留下
        _note(errors, "torch_alloc", e)
    return None


def reset_torch_peak(errors: dict[str, str] | None = None) -> None:
    """重置 torch 峰值统计——每轮提交序列开头调，让 peak 反映本轮而非全程。"""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception as e:  # 重置失败 ⇒ 下一份快照的 peak 是「全程峰值」（读数偏差要留痕）
        _note(errors, "torch_peak_reset", e)


def snapshot(tag: str, paths: list[str | Path] | None = None) -> dict:
    """采集一份取证快照（不落盘）。任何字段采集失败都降级，绝不出错。

    失败的字段除了降级为缺省，还会在 `probe_errors` 里留一行成因（`{字段: "类型: 文本"}`）——
    事故后读这份 jsonl 的人需要的是「为什么没有」，而不是一个与「真的 0」无法区分的数字。
    """
    errors: dict[str, str] = {}
    rss, peak = rss_mb(errors)
    snap: dict = {
        "event": "forensics",
        "tag": tag,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
        "rss_mb": round(rss, 1),
        "rss_peak_mb": round(peak, 1),
    }
    alloc = torch_alloc_mb(errors)
    if alloc is not None:
        snap["torch_alloc_mb"] = round(alloc, 1)
    disk: dict[str, float] = {}
    for p in paths or []:
        try:
            disk[str(p)] = round(shutil.disk_usage(p).free / _MB, 1)
        except Exception as e:  # 路径不存在 / 盘掉了 / 权限 —— 这正是「写盘失败实锤」的形状
            _note(errors, f"disk:{p}", e)
    if disk:
        snap["disk_free_mb"] = disk
    if errors:
        snap["probe_errors"] = errors
    return snap


def _log_line(tag: str, snap: dict) -> str:
    """快照 → 那行人读日志（`log_snapshot` 与用例共用；措辞就是交付物）。"""
    line = f"[forensics] {tag}: rss={snap.get('rss_mb')}MB (peak {snap.get('rss_peak_mb')}MB)"
    if "disk_free_mb" in snap:
        line += f" disk_free={snap['disk_free_mb']}"
    errors = snap.get("probe_errors") or {}
    if errors:
        # 只点名字，不贴异常全文：这行要在训练控制台上扫读，`rss=0MB` 旁边得立刻看出
        # 「这个 0 是量到的还是量不到的」。全文在 jsonl 的 `probe_errors` 里。
        line += f" probe_errors={','.join(sorted(errors))}"
    return line


def log_snapshot(tag: str, jsonl_path: str | Path, paths: list[str | Path] | None = None) -> dict:
    """采集 + 落 jsonl（event=forensics）+ 打一行人读日志。落盘失败只记日志。"""
    snap = snapshot(tag, paths)
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=True) + "\n")
    except OSError as e:
        log(f"[forensics] {tag}: jsonl 落盘失败（{e}）——rss={snap.get('rss_mb')}MB")
    log(_log_line(tag, snap))
    return snap
