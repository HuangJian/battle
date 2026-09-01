"""rl/archive.py — RL 权重归档轮转 + 分支 push。

Per-iteration weights archive (user request 2026-08-24)：每轮 PPO 写回后把权重拷贝进
nn-training/weights/，按 <prefix>.it<N>.<YYYYMMDD-HHMMSS>.json 命名（iter-first 方便按训练进度一览，时间戳消歧合同 iter 重跑）。

约束：
  - 命名**故意不**匹配 weights_io 严格 BC 自动发现正则，避免 eval_bridge.latest_weights_path
    选中 RL 归档。
  - prune 按 mtime（真实新旧）+ 文件名兜底排序——2026-08-27 §30 修复：之前纯 filename
    字典序（'it2' < 'it20' < 'it3'）导致多样最新 checkpoint 被误删。
  - push 失败（离线/无远端）仅告警不中断训练——本地训练不依赖远端。
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from rl.log import log

REPO_ROOT = Path(__file__).resolve().parent.parent

WEIGHTS_BACKUP_DIR = REPO_ROOT / "nn-training" / "weights"
WEIGHTS_BACKUP_KEEP = 20  # bounded archive: prune oldest it-backups beyond this


def backup_weights(
    weights_path: str, it: int, prefix: str = "rl-weights"
) -> str | None:
    """Archive the just-written RL weights into nn-training/weights/.

    Returns the archive destination path, or None on any (non-fatal) IO error.
    """
    try:
        WEIGHTS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = (
            WEIGHTS_BACKUP_DIR
            / f"{prefix}.it{it}.{time.strftime('%Y%m%d-%H%M%S')}.json"
        )
        import shutil

        shutil.copyfile(weights_path, dst)
        # glob removed (unused) keeps module-load side-effects minimal

        baks = sorted(
            WEIGHTS_BACKUP_DIR.glob(f"{prefix}.it*.json"),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        if len(baks) > WEIGHTS_BACKUP_KEEP:
            for old in baks[: len(baks) - WEIGHTS_BACKUP_KEEP]:
                old.unlink(missing_ok=True)
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
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if not branch or branch == "HEAD":
            return None
        r = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
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
