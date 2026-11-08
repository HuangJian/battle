"""bc_round_job —— BC `remote-jobs/` 目录的读与收敛（G4：隐藏目录 + hub 对账）。

**为什么单独成家**：`trainer/bc_loop.py` 的有效 LOC 已顶到预算（<1000），而这两段
都不是编排本身：`find_round_job` 只回答「这一轮盘上还有没有能认领的 job」，
`prune_remote_jobs` 只按 mtime 收敛旧目录。引擎只留一个薄门面，
`tests/trainer/test_bc_ingest_split.py` 仍能看到 `find_round_job` 定义在 `bc_loop.py`
（名字是契约，位置不是）。

**两条判据，缺一不可**：

1. `.` 开头目录**不参与**扫描（Python 的 `glob("*")` 会匹配点开头目录；`.deleted-*`
   因此会把一个已清场的 job 读成「未收口」）。
2. 认领前问 hub：`unknown` / `closed` ⇒ 允许重发并让调用方记账（该 jid 的 per-jid
   resume 已不可用，新 job 从课程级索引续训）；`unreachable` ⇒ **保守认领**，不重发。
   `done` 归 alive：结果已经在 hub 上，认领后落位；重发只会撞旧 result 永远等不到新结果。

**不写账本、不发布**：这里只做只读判定，回调 `on_missing` 由引擎决定记什么。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote.hub_http import JOB_CLOSED, JOB_UNKNOWN, JOB_UNREACHABLE, job_reconcile_state

MissingHook = Callable[[str, dict, str], None]


def train_tuning_meta(train: Any) -> dict[str, float | int | str]:
    """B2/B3 两个训练旋钮的 manifest 片段（bc_loop 的 LOC 预算：单点在此）。"""
    return {
        "weight_exponent": float(train.weight_exponent),
        "patience": int(train.patience),
        # P2-B（2026-11-08）：可训参数范围；旧课程缺键 ⇒ all
        "train_heads": str(getattr(train, "train_heads", "all")),
    }


def train_tuning_argv(train: Any) -> list[str]:
    """同上，本地 `train/bc.py` 的 CLI 片段（顺序/格式只此一份）。"""
    return [
        "--weight-exponent",
        str(train.weight_exponent),
        "--policy-coef",
        str(train.policy_coef),
        "--train-heads",
        str(getattr(train, "train_heads", "all")),
        "--patience",
        str(train.patience),
    ]


def find_round_job(
    job_root: Path,
    it: int,
    *,
    completed_jids: set[str] | None = None,
    hub_url: str = "",
    token: str = "",
    on_missing: MissingHook | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[str, dict] | None:
    """盘上找**这一轮已发布、还没收口**且 hub 仍可等的 job → `(jid, manifest)`。"""
    done = completed_jids or set()
    cands: list[tuple[float, str, dict]] = []
    try:
        entries = sorted(
            p
            for p in Path(job_root).glob("*/manifest.json")
            if not p.parent.name.startswith(".")
        )
    except OSError:
        return None
    for mp in entries:
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or int(m.get("it", -1)) != int(it):
            continue
        if str(m.get("kind", "")) != "bc":
            continue
        jid = str(m.get("job_id") or mp.parent.name)
        if jid in done:
            continue
        try:
            mt = mp.stat().st_mtime
        except OSError:
            mt = 0.0
        cands.append((mt, jid, m))
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if len(cands) > 1:
        others = ", ".join(j for _t, j, _m in cands[1:])
        log(
            f"[run_bc] it{it}: 盘上有 {len(cands)} 个未收口 bc job，按 manifest 时间从新到旧"
            f" 对账（其余不回收，可能仍在云机上跑：{others}）"
        )
    if not hub_url or not token:
        return cands[0][1], cands[0][2]
    for _mt, jid, m in cands:
        state = job_reconcile_state(hub_url, token, jid)
        if state in (JOB_UNKNOWN, JOB_CLOSED):
            why = "hub 侧不存在" if state == JOB_UNKNOWN else "hub 已终局"
            log(
                f"[run_bc] it{it}: 盘上 job {jid[:12]} {why}（{state}）——不认领，允许重发布"
                "（该 jid 的 per-jid resume 不可用；新 job 从课程级索引续训）"
            )
            if on_missing is not None:
                on_missing(jid, m, state)
            continue
        if state == JOB_UNREACHABLE:
            log(
                f"[run_bc] it{it}: 盘上 job {jid[:12]} 的 hub 状态不可达——保守认领，不重发"
            )
        return jid, m
    return None


def prune_remote_jobs(job_root: Path, keep: int = 3) -> None:
    """remote-jobs 旧 job 目录收敛（按 mtime 保留最近 keep 个；与 worker 侧同策略）。

    `.` 开头目录（`.deleted-*` / `.extra_tmp`）**既不计数也不删除**：它们是清场标记，
    不是 job；把它们算进 keep 会挤掉真正的在飞 job（G4 隐藏目录纪律）。
    """
    if not job_root.exists():
        return
    dirs = sorted(
        (d for d in job_root.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for d in dirs[keep:]:
        try:
            shutil.rmtree(d)
        except OSError:
            pass
