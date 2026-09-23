from __future__ import annotations

import secrets
import threading
from pathlib import Path

# 以下五个名字是本模块的**公共 re-export 面**（原为本地定义，2026-09-23 收敛到唯一实现）。
# 调用方（`rl/batch_eval.py` / `rl/eval_dispatch.py` / `rl/rollout_phase.py` / e2e）与
# 既有测试都从 `rl.queue` 取这些名字 ⇒ 名字留在原位，定义只留一份。
#   · bun_version / mm  → `common.proc`（版本探测与 major.minor 比对）
#   · _record_agent_meta → `rl.agent_meta`（dist-agent-meta.jsonl 的唯一写面）
from common.proc import bun_version as _bun_version
from common.proc import version_mm as mm  # noqa: F401 — re-export
from rl.agent_meta import record_agent_meta as _record_agent_meta  # noqa: F401 — re-export

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 = battle2（nn-training/rl/ 上溯 3 层）
RUN_ID = secrets.token_hex(8)  # runId 使 iterId 全局唯一（跨 relaunch 防混叠）
from rl.queue_local import (
    pick_race_target,  # noqa: F401 — re-exported（in-flight race 选靶+节点排除，tests 引用）
    pick_tail_race,  # noqa: F401 — re-exported（tests 引用）
    race_tier_ok,  # noqa: F401 — re-exported（tests 引用）
    register_inflight,  # noqa: F401 — re-exported（tests 引用）
    run_rollout,  # noqa: F401 — re-exported（collect_only/loop 调用方）
)


def local_slots_max_of(args) -> int | None:
    """本机直跑槽位上限（传给 run_rollout_queue 的 local_slots_max）。

    语义（2026-09-09 统一；此前各调用方各写一套，collect_only 干脆漏传 ⇒
    rl.local_slots 形同虚设、local 恒按 workers 满额并发）：
      > 0  = 显式槽位数；
      0    = **关闭**本机直跑（全部交给远端节点）——远端集体失联时仍会自动兜底接管；
      None / 负数 = 未设置 → 退回 min(workers, 任务数)（既有 auto 行为）。
    """
    raw = getattr(args, "local_slots", None)
    if raw is None:
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def bun_version(bun: str) -> str:
    """`bun --version`；探测失败/空输出 → `"?"`（唯一实现见 `common.proc.bun_version`）。

    保留本名与 1 参签名（薄包装），因为 `rl/batch_eval.py` / `rl/eval_dispatch.py` 从中
    import、且测试用 `monkeypatch.setattr(mod, "bun_version", …)` 打桩。
    """
    return _bun_version(bun, fallback="?")


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
    course_fp: str | None = None,
    corpus_fp: str | None = None,
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
        course_fp,
        corpus_fp,
    ).run()
