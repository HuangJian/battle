#!/usr/bin/env python3
"""tmp-clean.py — 仓库根 tmp/ 统一收敛脚本（日志/缓存/临时目录/运行目录保留策略）。

2026-09-08（DECISIONS §376 后续）：双 tmp 统一后仓库根 tmp/ 成为唯一临时根，
本脚本提供按分类的保留策略，dry-run 默认、--apply 才真正删除。

用法：
    python tools/tmp-clean.py                # dry-run：列出将删除内容与可回收空间
    python tools/tmp-clean.py --apply        # 实际删除
    python tools/tmp-clean.py --keep-runs 3 --keep-days 14 --apply   # 覆盖默认策略
    # `--keep-runs 0 --keep-days 0`（零保留）只用于 dry-run / 用例断言；--apply 需要 --force
    #   ——实测事故：1907 项 / 38GB 一次抹掉，见 refuses_zero_retention 的注释。

保留策略（分类器 = 目录内是否有 it<N> 子目录 → 运行目录；否则按名字分类）：
  - 缓存（.uv-cache / .ruff-cache / .mypy-cache）：一律可删（可再生，重复下载即可）。
  - 日志/一次性文件（tmp 根 *.log *.out *.err *.txt *.jsonl）：保留最近 --keep-days 天。
  - 工具临时目录（perf-cmp.* / smoke-* / probe-* / pytest-tmp 子目录 / ep* / m2 /
    split 等无 it<N> 的非保护目录）：按最后写入保留最近 --keep-days 天。
  - 运行目录（含 it<N> 子目录的 traj 根，如 p3-vk1/ p4-fast/）：保留最近 --keep-runs
    个（按目录内最新文件 mtime 排序）+ 最近 --keep-days 天内有写入的——正在写的运行
    必然新鲜，天然豁免。
  - **长期资产豁免**（2026-10-17，plan/offline-backfeed-resilience §7-3）：要删的目录里若有
    `deliver/`，只删它的**兄弟**，绝不删它——`tmp/<课程>/deliver/<run_id>/` 是导入/回灌
    落下的**课程产物目录**（课程的长期资产，不是临时物；课停之后正是它最值钱的时候）。
    ⚠ 这门课一旦停训，`training_running()` 的闸就放开了，而 `tmp/<课程>/` 是个 run root
    （内含 `it<N>/`）⇒ 正是「课已停、人正在导入」这个窗口里最容易误删。
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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
TMP = REPO_ROOT / "tmp"
RUN_LOCK = REPO_ROOT / "nn-training" / ".run_rl.lock"

LOG_SUFFIXES = (".log", ".out", ".err", ".txt", ".jsonl")
CACHE_NAMES = {".uv-cache", ".ruff-cache", ".mypy-cache"}
# 永不触碰（各自有主）：agent 工作目录自管理；git 事故备份；控制台账本/状态
PROTECTED = {"dist-agent", "git-repair-backup", "training-start"}
# 长期资产（§7-3）：收到删除判决的目录里，这些子目录**原样留下**（只收它的兄弟）
LONG_TERM_KEEP = ("deliver",)
RUN_IT_RE = r"it\d+"


def _pid_alive(pid: int) -> bool:
    """跨平台进程存活探测（Windows 用 GetExitCodeProcess，与 `nn-training/common/pid_probe.py` 同口径）。

    **不复用 `nn-training/common/pid_probe.py`**（2026-09-17 收口时的选择）：本脚本是**仓根**开发工具，
    不能依赖 nn-training 的包/路径布局（那里是可独立打包发运的训练代码），故按契约重复一份
    Windows 安全分支；一致性由 `nn-training/tests/common/test_pid_probe_windows_safe.py` 守住——本副本
    按文件路径加载进与 `common.pid_probe` **相同的逐例行为断言**（不需依赖 nn 包布局），另有源码门禁
    `test_tmp_clean_copy_keeps_guard_and_windows_branch`（`pid <= 0` 护栏与 Windows 分支缺一即红）。

    `pid <= 0` 一律判「不活」（POSIX 上 `os.kill(0, 0)` / `os.kill(-1, 0)` 命中的是**进程组**
    语义、实测成功）：残缺/损坏的 `.run_rl.lock` 里若是 0/-1，会被当成「训练还在跑」⇒
    `training_running()` 恒真 ⇒ 运行目录**永远不再收敛**。
    """
    if pid <= 0:
        return False
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
    seen = False
    for root, _dirs, files in os.walk(p):
        for name in files:
            try:
                mtime = os.stat(os.path.join(root, name)).st_mtime
            except OSError:
                continue
            seen = True
            latest = max(latest, mtime)
    # ⚠ 「有没有文件」与「最新 mtime 是多少」是**两件事**：全零 mtime 的文件（mtime=0）
    #   让 `latest` 停在 0.0，而 `0.0 or 目录mtime` 会**兜底到目录 mtime**（那是「现在」）⇒
    #   一批明明很旧的文件被当成「刚刚写过」⇒ 那个目录永远回收不掉。
    #   判据改成「见到过文件就用文件的时间」（`seen`），目录 mtime 只在**一个文件都没有**时兜底。
    return latest if seen else p.stat().st_mtime


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


def _tree_has_asset(d: Path) -> bool:
    """这棵子树（**任意深度**）里有长期资产（`deliver/`）吗。散文件 → False。"""
    if not d.is_dir():
        return False
    for _root, dirs, _files in os.walk(d):
        if any(n in LONG_TERM_KEEP for n in dirs):
            return True
    return False


def _deletion_targets(d: Path) -> list[Path]:
    """目录收敛的**实际**删除目标：自己（整棵子树无资产）或刨掉资产后的各块。

    为什么要拆到子项这一层（§7-3）：`rmtree(整个 traj 根)` 会把 `tmp/<课程>/deliver/` 一起
    带走——那是导入落下的课程产物目录，删了就是**不可再生的读数消失**（hub 上的那份是另一
    条腿的副本，且只到末段）。收它的兄弟（`it<N>/`、日志）就恢复了「临时物该清、资产留下」
    的分界，而删除动作仍是同一种（rmtree / unlink，见 `main`）。

    ⚠ **递归到资产所在那一层**（2026-10-17 实测教训）：只刨一层不够——`tmp/__probe__/x1/`
    这种多一层嵌套会让「删整个 `tmp/__probe__/x1/`」把里面的 `deliver/` 连坐删掉。
    含资产的分支逐层下降，不含资产的兄弟**整块删**（不把 rmtree 拆成几千次 unlink）。

    读不了目录（权限/已消失）→ 退回整目录：收敛脚本不因一个读不开的目录改变判决。
    """
    if not _tree_has_asset(d):
        return [d]  # 没有资产要留 ⇒ 一次 rmtree 删干净（与旧行为逐字节一致）
    try:
        kids = list(d.iterdir())
    except OSError:
        return [d]
    out: list[Path] = []
    for c in kids:
        if c.name in LONG_TERM_KEEP:
            continue
        out.extend(_deletion_targets(c) if c.is_dir() else [c])
    return out


# ────────────────── 指标转存（plan/course-metrics-persistence §3.5） ──────────────────
# 删除前先把四个课程读数文件镜像到 dashboard/data/course-metrics（转存成功 ⇒ 照常删；
# 不覆盖 ⇒ 只跳含账本的目标）。引擎/游标/锚点只有一份实现（dashboard CLI），这里只调用它。

MIRROR_FILES = (
    "training_log.jsonl",
    "eval_log.jsonl",
    "dist-agent-meta.jsonl",
    ".pool-actuals-cache.json",
)
MIRROR_EXCLUDE_DIRS = {"pytest-tmp", "backups", "deliver"}


def mirror_root() -> Path:
    """备份库根（与 `dashboard/src/core/paths.ts::courseMetricsDir` 同口径）。"""
    env = os.environ.get("BCITY_COURSE_METRICS")
    return Path(env) if env else REPO_ROOT / "dashboard" / "data" / "course-metrics"


def is_course_name(name: str) -> bool:
    """与 `course-metrics/meta.ts::isCourseName` 同一白名单（ASCII、不以 `_` 开头）。"""
    if not name or len(name) > 128 or name.startswith("_"):
        return False
    if name in MIRROR_EXCLUDE_DIRS:
        return False
    if not (name[0].isascii() and name[0].isalnum()):
        return False
    return all(c.isascii() and (c.isalnum() or c in "._-") for c in name)


def _course_ledger_files(target: Path) -> list[tuple[str, str, Path]]:
    """目标里直接包含的备份集文件：`(课程, 相对路径, 路径)`。

    只看**目标这一层**（+ `traj/`）：删 `it<N>/` 不涉及账本，不需要覆盖检查。
    非课程目录（`pytest-tmp/**` 等）⇒ 空（它们的临时账本不进备份库，也不该把清理卡死）。
    """
    if not target.is_dir():
        return []
    try:
        rel = target.relative_to(TMP)
    except ValueError:
        return []
    parts = rel.parts
    if not parts or not is_course_name(parts[0]):
        return []
    course = parts[0]
    course_dir = TMP / course
    out: list[tuple[str, str, Path]] = []
    for name in MIRROR_FILES:
        for cand in (target / name, target / "traj" / name):
            if cand.is_file():
                out.append((course, cand.relative_to(course_dir).as_posix(), cand))
    return out


def uncovered_reason(target: Path, root: Path | None = None) -> str | None:
    """目标里的账本是否**已完整转存**。None = 无账本或已覆盖；字符串 = 原因。"""
    files = _course_ledger_files(target)
    if not files:
        return None
    root = mirror_root() if root is None else root
    metas: dict[str, dict | None] = {}
    for course, relp, p in files:
        if course not in metas:
            try:
                metas[course] = json.loads(
                    (root / course / "mirror-meta.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                metas[course] = None
        meta = metas[course]
        if not isinstance(meta, dict):
            return f"{course}: 无 mirror-meta"
        fm = (meta.get("files") or {}).get(relp)
        if not isinstance(fm, dict):
            return f"{course}/{relp}: 未转存"
        try:
            size = p.stat().st_size
        except OSError as e:
            return f"{course}/{relp}: 源不可读（{e}）"
        if fm.get("mode") == "rewrite":
            if int(fm.get("bytes", -1)) != size:
                return f"{course}/{relp}: 尺寸不等"
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest != fm.get("sha256"):
                return f"{course}/{relp}: sha256 不符"
        elif int(fm.get("bytes", -1)) != size:
            return f"{course}/{relp}: 落后 {size - int(fm.get('bytes', 0))}B"
    return None


def transload(
    runner: Callable[[list[str]], tuple[bool, str]] | None = None,
) -> tuple[bool, str]:
    """调 dashboard CLI 做一次全量转存。`runner` 是单测接缝（不真起子进程）。"""
    bun = shutil.which("bun")
    cli = REPO_ROOT / "dashboard" / "scripts" / "course-metrics.ts"
    if not bun:
        return False, "bun 不在 PATH（用 --no-mirror 显式放弃转存）"
    if not cli.is_file():
        return False, f"CLI 缺失：{cli}"
    cmd = [bun, str(cli), "sync", "--course", "*", "--budget", "0"]
    if runner is not None:
        return runner(cmd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"转存子进程失败：{e}"
    out = (proc.stdout or "").strip().splitlines()
    detail = out[-1] if out else ""
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or detail
        return False, err[:400]
    return True, detail


def refuses_zero_retention(keep_runs: int, keep_days: float, apply: bool, force: bool) -> bool:
    """「零保留 + --apply」= 把 tmp/ 整片抹掉（`rmtree` **不进回收站**，不可逆）⇒ 拒绝执行。

    为什么需要这条闸（2026-10-17，事故真实发生过）：`--keep-runs 0 --keep-days 0 --apply`
    不是任何人**有意**要的策略（它的语义是「连刚写的都删」），却是一次不小心传错参数的
    默认结果——实测把仓库 tmp/ 下 1907 项、38GB（含各课本地 `it<N>/` 与账本）一次抹掉，
    而 tmp/ 不是版本控制的，没有第二种副本可恢复。零保留作为**dry-run 的断言工具**仍然
    合法（用例就是这么用的）⇒ 只闸 `--apply`，且留 `--force` 给人明写意图。
    """
    return bool(apply) and keep_runs <= 0 and keep_days <= 0 and not force


def _target_size(p: Path) -> int:
    """删除目标的字节数（目录走 `_dir_size`，散文件走 stat）。"""
    try:
        return _dir_size(p) if p.is_dir() else p.stat().st_size
    except OSError:
        return 0


def plan(
    now: float,
    keep_runs: int,
    keep_days: float,
    tmp_dir: Path | None = None,
    running: bool | None = None,
) -> tuple[list[tuple[Path, str, int]], dict[str, int]]:
    """返回 (计划删除列表, 统计)。每项 = (路径, 原因, 大小字节)。

    tmp_dir 可注入（单测/探针用）；缺省 = 仓库根 tmp/。
    `running` 同样是**测试接缝**：缺省按 `.run_rl.lock` 现推（生产语义）——注入是为了让
    「课已停 + 早于保留窗」这个窗口可判（否则用例的结果取决于跑测试时本机有没有在训练）。

    ⚠ **`tmp_dir` 的缺省不写成 `= TMP`**（2026-10-17 实测教训）：默认参数在**函数定义时**
    绑定，于是「加载模块后改 `mod.TMP` 再调 主流程」这种注入**根本不生效**——探针自以为
    在临时目录上跑，实际在真 tmp/ 上跑（后果见 `refuses_zero_retention` 的注释）。
    """
    tmp_dir = TMP if tmp_dir is None else tmp_dir
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
        if not (training_running() if running is None else running):
            ranked = sorted(
                ((_last_write(d), d) for d in run_roots), key=lambda x: x[0], reverse=True
            )
            for mtime, d in ranked[keep_runs:]:
                if mtime < cut:
                    for t in _deletion_targets(d):
                        to_delete.append((t, "run>keep_runs", _target_size(t)))
                    stats["run"] += 1
        else:
            sys.stderr.write("[tmp-clean] training active (run_rl.lock alive) - run dirs skipped\n")

        # 工具临时目录：按最后写入年龄
        for d in temp_dirs:
            mtime = _last_write(d)
            if mtime < cut:
                # 同一套豁免：只落过 `deliver/` 的课程目录（本地没跑过训练 ⇒ 不算 run root）
                # 同样是长期资产——旧行为会在 keep_days 之后把它整个删掉（§7-1③）。
                for t in _deletion_targets(d):
                    to_delete.append((t, "tempdir>keep_days", _target_size(t)))
                stats["tempdir"] += 1
    return to_delete, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="仓库根 tmp/ 统一收敛（dry-run 默认）")
    ap.add_argument("--apply", action="store_true", help="真正删除（缺省只 dry-run）")
    ap.add_argument("--keep-runs", type=int, default=3, help="保留最近 N 个运行目录（默认 3）")
    ap.add_argument("--keep-days", type=float, default=14.0, help="日志/临时目录/运行目录新鲜阈值天数（默认 14）")
    ap.add_argument(
        "--force",
        action="store_true",
        help="确认「零保留 + --apply」的整片抹除（不可逆；dry-run 不需要它）",
    )
    ap.add_argument(
        "--no-mirror",
        action="store_true",
        help="跳过指标转存与覆盖核对，按旧行为直接删（无 bun 机器/极端回收的逃生门）",
    )
    args = ap.parse_args(argv)

    # 零保留 + --apply ⇒ 拒绝（dry-run 仍可用它做断言）：见 refuses_zero_retention 的事故注释。
    if refuses_zero_retention(args.keep_runs, args.keep_days, args.apply, args.force):
        print(
            "[tmp-clean] 拒绝执行：--keep-runs 0 --keep-days 0 --apply 会把 tmp/ **整片**抹掉\n"
            "             （含各课本地 it<N>/ 与账本；rmtree 不进回收站，不可恢复）。\n"
            "             要看计划就去掉 --apply；确实要抹掉再加 --force。"
        )
        return 2

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

    mirror_state = "off(--no-mirror)"
    if not args.apply and not args.no_mirror:
        # dry-run 只预报（不转存、不删）：哪些目标在 --apply 时会先转存等待核对。
        pending = [(p, uncovered_reason(p)) for p, _w, _s in items]
        pending = [(p, why) for p, why in pending if why]
        if pending:
            print(f"[tmp-clean] 镜像预览: {len(pending)} 个目标未覆盖（--apply 会先转存）")
            for p, why in pending[:10]:
                print(f"  未覆盖 {p.relative_to(REPO_ROOT)}: {why}")

    if args.apply:
        if not args.no_mirror:
            ok, detail = transload()
            if detail:
                print(f"[tmp-clean] {detail}")
            mirror_state = "ok" if ok else "failed"
            if not ok:
                # 转存失败不是策略性拒绝（退出码仍 0）：只跳「含账本且未覆盖」的目标。
                print(f"[tmp-clean] 指标转存失败: {detail}")
        removed = 0
        skipped_uncovered: list[tuple[Path, str]] = []
        for p, why, _size in items:
            if not args.no_mirror:
                gap = uncovered_reason(p)
                if gap:
                    skipped_uncovered.append((p, gap))
                    print(f"[tmp-clean] skipped (未转存): {p.relative_to(REPO_ROOT)} — {gap}")
                    continue
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                removed += 1
            except OSError as e:
                print(f"[tmp-clean] skipped ({e}): {p.relative_to(REPO_ROOT)}")
        print(
            f"[tmp-clean] removed {removed}/{len(items)} item(s), freed {total / 1024 / 1024:.1f} MB"
            + f" ｜ 指标转存 {mirror_state} ｜ 跳过 {len(skipped_uncovered)}（未覆盖）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())