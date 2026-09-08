#!/usr/bin/env python3
"""tmp-clean.py — 仓库根 tmp/ 统一收敛脚本（日志/缓存/临时目录/运行目录保留策略）。

2026-09-08（DECISIONS §376 后续）：双 tmp 统一后仓库根 tmp/ 成为唯一临时根，
本脚本提供按分类的保留策略，dry-run 默认、--apply 才真正删除。

用法：
    python tools/tmp-clean.py                # dry-run：列出将删除内容与可回收空间
    python tools/tmp-clean.py --apply        # 实际删除
    python tools/tmp-clean.py --keep-runs 3 --keep-days 14 --apply   # 覆盖默认策略

保留策略（分类器 = 目录内是否有 it<N> 子目录 → 运行目录；否则按名字分类）：
  - 缓存（.uv-cache / .ruff-cache / .mypy-cache）：一律可删（可再生，重复下载即可）。
  - 日志/一次性文件（tmp 根 *.log *.out *.err *.txt *.jsonl）：保留最近 --keep-days 天。
  - 工具临时目录（perf-cmp.* / smoke-* / probe-* / pytest-tmp 子目录 / ep* / m2 /
    split 等无 it<N> 的非保护目录）：按最后写入保留最近 --keep-days 天。
  - 运行目录（含 it<N> 子目录的 traj 根，如 p3-vk1/ p4-fast/）：保留最近 --keep-runs
    个（按目录内最新文件 mtime 排序）+ 最近 --keep-days 天内有写入的——正在写的运行
    必然新鲜，天然豁免。
  - 永不触碰：dist-agent（sampler-agent 自管理，§374）、git-repair-backup（git 事故
    安全备份）、training-start（训练控制台账本 console-state/registry/monitor）。
  - 运行检测：nn-training/.run_rl.lock 的 holder 存活时，跳过运行目录收敛并告警
    （日志/缓存/临时目录照常）；锁陈旧（holder 已死）则忽略。

沙箱提示：与 tools/githook/nn-clean-tmp.py 相同，可 `python -S tools/tmp-clean.py
--apply` 绕过 WorkBuddy 沙箱删除保护（用户知情批准；脚本严格限界于 tmp/ 内分类目标，
绝不触碰其他路径）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TMP = REPO_ROOT / "tmp"
RUN_LOCK = REPO_ROOT / "nn-training" / ".run_rl.lock"

LOG_SUFFIXES = (".log", ".out", ".err", ".txt", ".jsonl")
CACHE_NAMES = {".uv-cache", ".ruff-cache", ".mypy-cache"}
# 永不触碰（各自有主）：agent 工作目录自管理；git 事故备份；控制台账本/状态
PROTECTED = {"dist-agent", "git-repair-backup", "training-start"}
RUN_IT_RE = r"it\d+"


def _pid_alive(pid: int) -> bool:
    """跨平台进程存活探测（Windows 用 GetExitCodeProcess，同 run_rl.py 口径）。"""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def training_running() -> bool:
    """run_rl 锁 holder 存活 = 训练主循环在跑。锁不存在/陈旧 → False。"""
    try:
        holder = int(RUN_LOCK.read_text(encoding="utf-8").split("|")[0])
    except (OSError, ValueError):
        return False
    return _pid_alive(holder)


def _last_write(p: Path) -> float:
    """目录最后写入 = 内部最新文件 mtime（目录自身 mtime 不随内容变化，不可靠）。"""
    latest = 0.0
    for root, _dirs, files in os.walk(p):
        for name in files:
            try:
                latest = max(latest, os.stat(os.path.join(root, name)).st_mtime)
            except OSError:
                pass
    return latest or p.stat().st_mtime


def _dir_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _is_run_root(p: Path) -> bool:
    try:
        return any(d.name.startswith("it") and d.name[2:].isdigit() for d in p.iterdir() if d.is_dir())
    except OSError:
        return False


def plan(
    now: float, keep_runs: int, keep_days: float, tmp_dir: Path = TMP
) -> tuple[list[tuple[Path, str, int]], dict[str, int]]:
    """返回 (计划删除列表, 统计)。每项 = (路径, 原因, 大小字节)。

    tmp_dir 可注入（单测用）；默认仓库根 tmp/。
    """
    cut = now - keep_days * 86400
    to_delete: list[tuple[Path, str, int]] = []
    stats: dict[str, int] = {"cache": 0, "log": 0, "tempdir": 0, "run": 0}

    # ---- tmp 根文件：日志/一次性文件按年龄 ----
    if tmp_dir.is_dir():
        for f in tmp_dir.iterdir():
            if f.is_file() and f.name.lower().endswith(LOG_SUFFIXES) and f.stat().st_mtime < cut:
                to_delete.append((f, "log>keep_days", f.stat().st_size))
                stats["log"] += 1

        # ---- 目录：分类 + 策略 ----
        dirs = [d for d in tmp_dir.iterdir() if d.is_dir() and d.name not in PROTECTED]
        caches = [d for d in dirs if d.name in CACHE_NAMES]
        others = [d for d in dirs if d.name not in CACHE_NAMES]

        for d in caches:
            to_delete.append((d, "cache(regenerable)", _dir_size(d)))
            stats["cache"] += 1

        run_roots = [d for d in others if _is_run_root(d)]
        temp_dirs = [d for d in others if not _is_run_root(d)]

        # 运行目录：按最后写入降序，保留最新 keep_runs + keep_days 内新鲜的
        if not training_running():
            ranked = sorted(
                ((_last_write(d), d) for d in run_roots), key=lambda x: x[0], reverse=True
            )
            for mtime, d in ranked[keep_runs:]:
                if mtime < cut:
                    to_delete.append((d, "run>keep_runs", _dir_size(d)))
                    stats["run"] += 1
        else:
            sys.stderr.write("[tmp-clean] training active (run_rl.lock alive) - run dirs skipped\n")

        # 工具临时目录：按最后写入年龄
        for d in temp_dirs:
            mtime = _last_write(d)
            if mtime < cut:
                to_delete.append((d, "tempdir>keep_days", _dir_size(d)))
                stats["tempdir"] += 1
    return to_delete, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="仓库根 tmp/ 统一收敛（dry-run 默认）")
    ap.add_argument("--apply", action="store_true", help="真正删除（缺省只 dry-run）")
    ap.add_argument("--keep-runs", type=int, default=3, help="保留最近 N 个运行目录（默认 3）")
    ap.add_argument("--keep-days", type=float, default=14.0, help="日志/临时目录/运行目录新鲜阈值天数（默认 14）")
    args = ap.parse_args()

    if not TMP.is_dir():
        print(f"[tmp-clean] {TMP} missing - nothing to do")
        return 0

    items, stats = plan(time.time(), args.keep_runs, args.keep_days)
    items.sort(key=lambda x: (-x[2], str(x[0])))

    total = sum(s for _, _, s in items)
    print(
        f"[tmp-clean] tmp/ plan: {len(items)} item(s) / {total / 1024 / 1024:.1f} MB"
        + (" (dry-run; use --apply to delete)" if not args.apply else "")
    )
    for p, why, size in items:
        print(f"  {size / 1024 / 1024:9.1f} MB  {why:<18} {p.relative_to(REPO_ROOT)}")
    if not items:
        print("[tmp-clean] nothing reclaimable")

    if args.apply:
        removed = 0
        for p, why, _size in items:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                removed += 1
            except OSError as e:
                print(f"[tmp-clean] skipped ({e}): {p.relative_to(REPO_ROOT)}")
        print(f"[tmp-clean] removed {removed}/{len(items)} item(s), freed {total / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())