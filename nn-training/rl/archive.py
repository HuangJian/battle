"""rl/archive.py — RL 权重归档 + 分支 push。

Per-iteration weights archive (user request 2026-08-24)：每轮 PPO 写回后把权重拷贝进
nn-training/weights/，按 <prefix>.it<N>.<YYYYMMDD-HHMMSS>.json 命名（iter-first 方便按训练进度一览，时间戳消歧合同 iter 重跑）。

约束：
  - 命名**故意不**匹配 weights_io 严格 BC 自动发现正则，避免 eval_bridge.latest_weights_path
    选中 RL 归档。
  - **只归档、不自动清理**（2026-09-02 用户指令：生产代码不做删除——沙箱删除保护
    会拦截自动 prune，训练/测试弹窗）。归档目录的有界清理由**手动工具**承担：
    `make weights-prune-apply`（weights_prune.py，用户主动触发时自己确认）。
  - push 失败（离线/无远端）仅告警不中断训练——本地训练不依赖远端。
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.log import log

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 = battle2（nn-training/rl/ 上溯 3 层）

WEIGHTS_BACKUP_DIR = REPO_ROOT / "nn-training" / "weights"
WEIGHTS_BACKUP_KEEP = 20  # 仅作手动 prune（weights_prune.py）的参考配额；backup_weights 不自动删


def backup_weights(weights_path: str, it: int, prefix: str = "rl-weights") -> str | None:
    """Archive the just-written RL weights into nn-training/weights/.

    只归档不清理（2026-09-02）：旧归档删除已移除——沙箱删除保护会拦截生产代码的
    自动删除。磁盘有界性由手动 `make weights-prune-apply` 保证。

    Returns the archive destination path, or None on any (non-fatal) IO error.
    """
    try:
        WEIGHTS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = WEIGHTS_BACKUP_DIR / f"{prefix}.it{it}.{time.strftime('%Y%m%d-%H%M%S')}.json"
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
        branch_res: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            **_POPEN_NO_WINDOW,
        )
        branch = branch_res.stdout.strip()
        if not branch or branch == "HEAD":
            return None
        r = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
            **_POPEN_NO_WINDOW,
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
