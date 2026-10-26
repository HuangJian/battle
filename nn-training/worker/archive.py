"""biz/archive.py — RL 权重归档 + 分支 push。

Per-iteration weights archive (user request 2026-08-24)：每轮 PPO 写回后把权重拷贝进
nn-training/weights/，按 <prefix>.it<N>.<YYYYMMDD-HHMMSS>.json 命名（iter-first 方便按训练进度一览，时间戳消歧合同 iter 重跑）。

约束：
  - 命名**故意不**匹配 weights_io 严格 BC 自动发现正则，避免 eval_bridge.latest_weights_path
    选中 RL 归档。
  - **只归档、不自动清理**（2026-09-02 用户指令：生产代码不做删除——沙箱删除保护
    会拦截自动 prune，训练/测试弹窗）。归档目录的有界清理由**手动工具**承担：
    `make weights-prune-apply`（tools/weights_prune.py，用户主动触发时自己确认）。
  - push 失败（离线/无远端）仅告警不中断训练——本地训练不依赖远端。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from common.log import log
from common.proc import run_capture

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 = battle2（nn-training/biz/ 上溯 3 层）

WEIGHTS_BACKUP_DIR = REPO_ROOT / "nn-training" / "weights"
WEIGHTS_BACKUP_KEEP = 20  # 仅作手动 prune（tools/weights_prune.py）的参考配额；backup_weights 不自动删


def find_archive_weights(backup_dir: str, backup_prefix: str, it: int) -> str | None:
    """归档目录里定位 itN 权重（`{prefix}.it{N}.*.json`，取最新）。

    延迟派发读不可变归档而非活指针——wver 与离线复跑同源，且天然免疫 PPO 落盘
    竞态与断点续跑后的指针前移。缺席返回 None（归档失败是非致命的；调用方回落
    活指针 + 响亮日志）。2026-10-26 从 `trainer/eval_dispatch.py` 迁来（LOC 预算）。
    """
    if not backup_dir or not backup_prefix:
        return None
    bdir = Path(backup_dir)
    if not bdir.is_absolute():
        bdir = REPO_ROOT / bdir
    try:
        cands = sorted(
            bdir.glob(f"{backup_prefix}.it{it}.*.json"),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return None
    return str(cands[-1]) if cands else None


def backup_weights(
    weights_path: str,
    it: int,
    prefix: str = "rl-weights",
    backup_dir: str | None = None,
    suffix: str = ".json",
) -> str | None:
    """Archive the just-written RL weights into nn-training/weights/.

    只归档不清理（2026-09-02）：旧归档删除已移除——沙箱删除保护会拦截生产代码的
    自动删除。磁盘有界性由手动 `make weights-prune-apply` 保证。

    `backup_dir`：课程可指定归档目录（`backup_dir` 课程键，D6 课程单一事实来源）。
    相对路径按仓库根解析（课程值形如 "nn-training/weights/<course>"，与
    TrainingLoop 的 cwd 无关）；None = 默认 WEIGHTS_BACKUP_DIR。

    `suffix`：归档名的后缀（2026-10-15，后缀约定）。**缺省 `.json` = 逐字节旧行为**；
    回传腿落**压缩版**时传 `.delta`（`remote.delta_files.named_for` 定的名）—— 归档是给人/控制台
    扫的存储副本，名字必须自述形态（否则 `eval-board` 的 iter 选择器会把一份解不开的容器当可
    装载权重列出来）。只改名字：**归档字节一个不改**（它是那一轮的不可变快照）。

    Returns the archive destination path, or None on any (non-fatal) IO error.
    """
    try:
        bdir = Path(backup_dir) if backup_dir else WEIGHTS_BACKUP_DIR
        if not bdir.is_absolute():
            bdir = REPO_ROOT / bdir
        bdir.mkdir(parents=True, exist_ok=True)
        dst = bdir / f"{prefix}.it{it}.{time.strftime('%Y%m%d-%H%M%S')}{suffix}"
        shutil.copyfile(weights_path, dst)
        return str(dst)
    except OSError as e:
        log(f"[archive] WARN weights backup failed (non-fatal): {e}")
        return None


def ensure_current_branch_pushed(repo_root: Path) -> str | None:
    """Push current branch to origin before starting — remote agents upgrade via
    `git pull` from origin (not local); unpushed commits → agents pull stale code →
    codeHash mismatch → excluded → training runs on local alone.

    Returns the pushed branch name, or None on any (non-fatal) failure.
    """
    try:
        branch_res = run_capture(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, timeout=30
        )
        branch = branch_res.stdout.strip()
        if not branch or branch == "HEAD":
            return None
        r = run_capture(["git", "push", "origin", branch], cwd=repo_root, timeout=120)
        if r.returncode == 0:
            log(f"[archive] pushed {branch} -> origin (agents can git-pull to sync)")
            return branch
        log(
            f"[archive] WARN git push {branch} failed (rc={r.returncode}): "
            f"{(r.stderr or r.stdout)[-200:]} — remote agents may stay stale"
        )
    except Exception as e:
        log(f"[archive] WARN git push skipped: {e}")
    return None
