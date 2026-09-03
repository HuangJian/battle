from __future__ import annotations

import json
import secrets
import subprocess
import threading
from pathlib import Path

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 = battle2（nn-training/rl/ 上溯 3 层）
RUN_ID = secrets.token_hex(8)  # runId 使 iterId 全局唯一（跨 relaunch 防混叠）
from rl.queue_local import (
    pick_race_target,  # noqa: F401 — re-exported（v3.16 竞速选靶+节点排除，tests 引用）
    pick_tail_race,  # noqa: F401 — re-exported（tests 引用）
    race_tier_ok,  # noqa: F401 — re-exported（tests 引用）
    register_inflight,  # noqa: F401 — re-exported（tests 引用）
    run_rollout,  # noqa: F401 — re-exported（collect_only/loop 调用方）
)


def bun_version(bun: str) -> str:
    try:
        return (
            subprocess.run(
                [bun, "--version"], capture_output=True, text=True, timeout=10, **_POPEN_NO_WINDOW
            ).stdout.strip()
            or "?"
        )
    except Exception:
        return "?"


def mm(version: str) -> str:
    return ".".join(str(version).split(".")[:2])


def _record_agent_meta(meta_path: Path, rec: dict) -> None:
    """追加一条节点采样元数据到 dist-agent-meta.jsonl（巡检读它聚合进 HTML）。

    rec: {node, it, stage, seed, ok, [win, elapsedSec | reason], ts}。
    放锁内调用保证顺序；单局一次 IO，成本可忽略。
    """
    try:
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_rollout_queue(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    pairs: list[tuple[int, int]],
    args,
    cfg: dict,
    iter_id: str,
    on_result=None,
    local_slots_max: int | None = None,
    tail_dispatch: bool = True,
    halt_event: threading.Event | None = None,
    on_queue_drained=None,
    local_suspend: threading.Event | None = None,
    extra_wver: str | None = None,
) -> dict:
    """中央队列调度（薄包装：RolloutDispatcher 构造 + run，OO 实现在 rl/dispatch.py）。"""
    from rl.dispatch import RolloutDispatcher

    return RolloutDispatcher(
        bun,
        rl_path,
        traj_dir,
        pairs,
        args,
        cfg,
        iter_id,
        on_result,
        local_slots_max,
        tail_dispatch,
        halt_event,
        on_queue_drained,
        local_suspend,
        extra_wver,
    ).run()
