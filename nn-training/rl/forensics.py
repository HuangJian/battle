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
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from rl.log import log

_MB = 1024.0 * 1024.0


def _rss_mb_windows() -> tuple[float, float] | None:
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
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.WorkingSetSize / _MB, pmc.PeakWorkingSetSize / _MB
    except Exception:
        return None


def _rss_mb_posix() -> tuple[float, float] | None:
    try:
        import resource

        ru = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[attr-defined]
        # ru_maxrss：Linux 单位 KB，macOS 单位字节
        scale = 1024.0 if sys.platform.startswith("linux") else 1.0
        peak = float(ru.ru_maxrss) * scale
        cur = peak
        try:
            page = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined]  # POSIX 专用
            with open("/proc/self/statm", encoding="ascii") as f:
                cur = float(int(f.read().split()[1]) * page)
        except Exception:
            pass
        return cur / _MB, peak / _MB
    except Exception:
        return None


def rss_mb() -> tuple[float, float]:
    """(当前 RSS, 峰值 RSS) 单位 MB；任何失败返回 (0.0, 0.0)——绝不出错。"""
    r = _rss_mb_windows() if os.name == "nt" else _rss_mb_posix()
    return r if r is not None else (0.0, 0.0)


def torch_alloc_mb() -> float | None:
    """torch 分配器当前已分配 MB；无 torch / 无 cuda → None（CPU 训练无分配器池可看）。"""
    try:
        import torch

        if torch.cuda.is_available():
            return float(torch.cuda.memory_allocated()) / _MB
    except Exception:
        pass
    return None


def reset_torch_peak() -> None:
    """重置 torch 峰值统计——每轮提交序列开头调，让 peak 反映本轮而非全程。"""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def snapshot(tag: str, paths: list[str | Path] | None = None) -> dict:
    """采集一份取证快照（不落盘）。任何字段采集失败都降级，绝不出错。"""
    rss, peak = rss_mb()
    snap: dict = {
        "event": "forensics",
        "tag": tag,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
        "rss_mb": round(rss, 1),
        "rss_peak_mb": round(peak, 1),
    }
    alloc = torch_alloc_mb()
    if alloc is not None:
        snap["torch_alloc_mb"] = round(alloc, 1)
    disk: dict[str, float] = {}
    for p in paths or []:
        try:
            disk[str(p)] = round(shutil.disk_usage(p).free / _MB, 1)
        except Exception:
            pass
    if disk:
        snap["disk_free_mb"] = disk
    return snap


def log_snapshot(tag: str, jsonl_path: str | Path, paths: list[str | Path] | None = None) -> dict:
    """采集 + 落 jsonl（event=forensics）+ 打一行人读日志。落盘失败只记日志。"""
    snap = snapshot(tag, paths)
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=True) + "\n")
    except OSError as e:
        log(f"[forensics] {tag}: jsonl 落盘失败（{e}）——rss={snap.get('rss_mb')}MB")
    log(
        f"[forensics] {tag}: rss={snap.get('rss_mb')}MB (peak {snap.get('rss_peak_mb')}MB)"
        + (f" disk_free={snap['disk_free_mb']}" if "disk_free_mb" in snap else "")
    )
    return snap
