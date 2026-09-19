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

## 仓储根（本文件在 dashboard/src/evalboard/ 下 ⇒ 上溯 4 层；2026-09-15 从
# tools/training/ 迁到 dashboard/ 后这里少算了两层，脚本一直 import 不到
# nn-training（parent[1] = dashboard/src）——回归 `tests/test_kick_once_paths.py`。
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "nn-training"))

from rl.batch_eval import (  # noqa: E402
    claim_pending,
    consume_requests,
    data_root,
    dispatch_batch_bg,
    select_next_unit,
    units_for_batch,
)
from rl.log import log  # noqa: E402
import dist_common  # noqa: E402
from rl.queue import RUN_ID  # noqa: E402


def main() -> int:
    root = data_root()
    # 先消费 console/CLI 请求（requests.jsonl → pending 批，含 kind='verdict'），
    # 否则 kick 一个刚入队的判决批会「看不到批」而直接空跑。
    consumed = consume_requests(root)
    if consumed.get("enqueued"):
        log(f"[kick] consume_requests: {consumed}")
    batch = claim_pending(root)
    if batch is None:
        log("[kick] no pending/incomplete batch")
        return 0
    bid = batch.get("batch_id")
    policy = str(batch.get("policy", "nn"))
    course = str(batch.get("course", ""))
    log(f"[kick] claimed {bid} course={course} policy={policy} units={batch.get('units')}")
    try:
        # ladder 批走 ladder.json；判决批（P2）走语料注册表——与 maybe_dispatch_batch
        # 同一份展开，判决批因此也能一次性 kick（不必等训练空窗）。
        units = units_for_batch(batch)
    except Exception as e:
        log(f"[kick] plan failed: {e}")
        return 1
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
    # 判决批的权重在 unit 上（多 ckpt 同批）；ladder 批回落批次课程权重。
    weight_path = str(unit.get("ckpt") or "") or args.out
    rl_path = None if policy == "god" else weight_path
    if policy == "nn" and not rl_path:
        log("[kick] nn unit without weights")
        return 1
    if policy == "nn" and not Path(weight_path).exists():
        log(f"[kick] nn weights missing: {weight_path}")
        return 1
    # 批次没有 course（判决批以语料 id 为身份）时，账本落在 tmp/<语料 id>/ 下。
    eval_log = ROOT / "tmp" / (course or str(batch.get("corpus") or "verdict")) / "eval_log.jsonl"
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
