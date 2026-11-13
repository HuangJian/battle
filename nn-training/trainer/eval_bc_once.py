"""eval_bc_once — 手动补一个 BC epoch 的多地图干净评估（控制台 BC Epoch 表的 evalA 按钮）。

与 `run_bc` 的 `eval.every_epochs` 边界**同一条路**，不是另一套本机评估：

  hub `/jobs/{jid}/epoch-weights/{epoch}`（不可变快照）
    → `trainer.bc_ingest.run_epoch_eval`
    → `worker.bc_eval.dispatch_bc_eval`
    → 账本 `bc_eval`

为什么必须按 `(it, epoch)` 手动触发：自动 eval 只在边界 epoch 跑；补评某个漏掉的
epoch 时不能拿 `/resume`（它可能已经是更晚的权重），否则读数会被贴到错的 epoch。

job id 从课程账本的 `job_pending` 事件取（同一 it 可能有多条历史 job，按从新到旧尝试）；
已有 `bc_eval(it, epoch)` ⇒ 幂等早退。失败只返回非零，错误细节留给 `tmp/<课>/evalBc.log`。

用法：
  bash tools/githook/nn-py-safe.sh nn-training/trainer/eval_bc_once.py \
    --course g2-classic35 --it 2 --epoch 17 --hub-url http://127.0.0.1:8787
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from common.cli import house_parser, run_main
from common.distribution import load_dist_config
from common.log import log
from common.platform_utils import force_utf8_stdio
from trainer.bc_ingest import run_epoch_eval
from trainer.dispatch_queue import REPO_ROOT
from worker.bc_config import BC_SUFFIX, load_bc_course, resolve_bc_course
from worker.bc_ledger import JOB_PENDING_EVENT, read_events


def _ledger_path(course_path: Path, course) -> Path:
    """课程账本路径（相对路径一律按仓库根解析；dashboard 传的是课程 key）。

    2026-11-13 事故：`resolve_traj` 返回的是课程目录（`tmp/<course>`），不是账本文件；
    直接 `read_text()` 会拿目录去开 ⇒ `PermissionError`，evalA 进程秒退、账本零 `bc_eval`。
    账本文件名是课程树的固定契约（hub 也按 `<traj>/training_log.jsonl` 派生）。
    """
    key = course_path.name[: -len(BC_SUFFIX)]
    p = Path(course.resolve_traj(key)) / "training_log.jsonl"
    return p if p.is_absolute() else REPO_ROOT / p


def _job_ids(events: list[dict], it: int, explicit: str = "") -> list[str]:
    """该 it 的候选 job id（新→旧，去重）。显式 --job-id 优先，不查账本。"""
    if explicit:
        return [explicit]
    out: list[str] = []
    for e in reversed(events):
        if e.get("event") != JOB_PENDING_EVENT or e.get("it") != it:
            continue
        jid = e.get("job_id")
        if isinstance(jid, str) and jid and jid not in out:
            out.append(jid)
    return out


def _eval_done(events: list[dict], it: int, epoch: int) -> bool:
    """账本里是否已有该 (it, epoch) 的 bc_eval（幂等/成功后确认的唯一判据）。"""
    return any(
        e.get("event") == "bc_eval" and e.get("it") == it and e.get("epoch") == epoch
        for e in events
    )


def main() -> int:
    force_utf8_stdio()
    ap = house_parser(
        "手动执行一个 BC epoch 的多地图干净评估（dashboard BC Epoch 表 evalA）",
        prog="eval_bc_once.py",
    )
    ap.add_argument("--course", required=True, help="BC 课程名或课程文件路径")
    ap.add_argument("--it", type=int, required=True, help="课程轮号（job_pending 的 it）")
    ap.add_argument("--epoch", type=int, required=True, help="要补评的 epoch")
    ap.add_argument("--job-id", default="", help="显式 hub job id（缺省从账本 job_pending 找）")
    ap.add_argument("--hub-url", default="", help="hub 基址（缺省读 rl.remote_hub_url / rl.hub_port）")
    args = ap.parse_args()

    it, epoch = int(args.it), int(args.epoch)
    if it <= 0 or epoch <= 0:
        log("[evalBc] --it/--epoch 必须为正整数")
        return 2

    course_path = resolve_bc_course(args.course)
    course = load_bc_course(str(course_path))
    ledger = _ledger_path(course_path, course)
    if not ledger.exists():
        log(f"[evalBc] 课程账本不存在：{ledger}")
        return 2

    events = read_events(ledger)
    if _eval_done(events, it, epoch):
        log(f"[evalBc] it{it} ep{epoch} 已有 bc_eval——幂等跳过")
        return 0

    if not course.eval.levels and not course.eval.course_env:
        log("[evalBc] 课程 eval 块没有 levels，也未开启 course_env——无法评估")
        return 2
    if int(course.eval.games_per_stage) <= 0:
        log("[evalBc] eval.games_per_stage 必须 > 0")
        return 2

    job_ids = _job_ids(events, it, args.job_id.strip())
    if not job_ids:
        log(f"[evalBc] 账本里没有 it{it} 的 job_pending——该轮没有 hub 快照，无法按 epoch 补评")
        return 2

    cfg = load_dist_config()
    rl = cfg.get("rl") if isinstance(cfg, dict) else None
    rl = rl if isinstance(rl, dict) else {}
    hub_url = (args.hub_url or str(rl.get("remote_hub_url") or "")).strip()
    if not hub_url:
        port = int(rl.get("hub_port") or 0)
        if port > 0:
            hub_url = f"http://127.0.0.1:{port}"
    token = str(rl.get("remote_token") or "").strip()
    if not hub_url:
        log("[evalBc] 缺 hub URL：传 --hub-url，或在 rl-config.json 配 rl.remote_hub_url / rl.hub_port")
        return 2
    if not token:
        log("[evalBc] 缺 rl.remote_token（hub 鉴权）")
        return 2

    for jid in job_ids:
        log(f"[evalBc] it{it} ep{epoch}: 取 hub job {jid[:12]} 的 epoch 权重并派发多地图 eval")
        run_epoch_eval(hub_url, token, jid, ledger, it, epoch, course, cfg, log=log)
        if _eval_done(read_events(ledger), it, epoch):
            log(f"[evalBc] it{it} ep{epoch}: eval 完成并已入账")
            return 0

    log(f"[evalBc] it{it} ep{epoch}: 所有候选 job 都未能完成 eval（详情见上）")
    return 1


if __name__ == "__main__":
    run_main(main)
