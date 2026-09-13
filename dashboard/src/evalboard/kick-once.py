"""One-shot: claim pending evalboard batch and run first incomplete unit locally.

Use when TrainingLoop is blocked on remote PPO and cannot open an idle window.
God policy needs no weights; nn requires tmp/<course>/weights.json.
"""
from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nn-training"))

from rl.batch_eval import (  # noqa: E402
    claim_pending,
    data_root,
    dispatch_batch_bg,
    load_ladder,
    plan_units,
    select_next_unit,
)
from rl.log import log  # noqa: E402
import dist_common  # noqa: E402
from rl.queue import RUN_ID  # noqa: E402


def main() -> int:
    root = data_root()
    batch = claim_pending(root)
    if batch is None:
        log("[kick] no pending/incomplete batch")
        return 0
    bid = batch.get("batch_id")
    policy = str(batch.get("policy", "nn"))
    course = str(batch.get("course", ""))
    log(f"[kick] claimed {bid} course={course} policy={policy} units={batch.get('units')}")
    try:
        ladder = load_ladder()
    except Exception as e:
        log(f"[kick] ladder load failed: {e}")
        return 1
    units = plan_units(ladder, int(batch.get("ladder_pos", 0) or 0), int(batch.get("k_seq", 0) or 0))
    units, nxt, unit = select_next_unit(
        units, set((batch.get("units") or {}).get("done", [])), batch.get("only_rungs")
    )
    if nxt is None or unit is None:
        log("[kick] no remaining unit")
        return 0
    batch.setdefault("units", {})["of"] = len(units)
    cfg = dist_common.load_dist_config() or {}
    args = SimpleNamespace(
        mode="per-tick",
        out=str(ROOT / "tmp" / course / "weights.json") if course else "",
        eval_window_sec=1500,
        local_slots=4,
    )
    rl_path = None if policy == "god" else args.out
    if policy == "nn" and not Path(args.out).exists():
        log(f"[kick] nn weights missing: {args.out}")
        return 1
    eval_log = ROOT / "tmp" / course / "eval_log.jsonl"
    eval_log.parent.mkdir(parents=True, exist_ok=True)
    window = threading.Event()
    window.set()
    # 必须传 bun 可执行路径，不能把 bun_version() 的版本串回写成路径。
    bun = shutil.which("bun") or "bun"
    log(f"[kick] bun={bun} unit={unit.get('rung')} u{nxt}/{len(units)}")
    epoch = dist_common.compute_engine_epoch()
    t = dispatch_batch_bg(
        bun,
        rl_path,
        eval_log,
        args,
        cfg,
        batch,
        unit,
        nxt,
        len(units),
        RUN_ID,
        epoch,
        policy,
        window,
        str(batch.get("init_sha16", "")),
    )
    t.join(timeout=1800)
    log(f"[kick] unit thread finished alive={t.is_alive()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
