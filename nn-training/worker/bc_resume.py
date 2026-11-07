"""bc_resume —— BC 课程级 resume 的**只读判据**（G3：预算扩大后重开已完成轮）。

**为什么单独一层**：这两个读法同时被引擎（`trainer/bc_loop.py::BcLoop.ledger_next_it`）与
supervisor 的入队（`trainer/loop_serve.py::_enqueue`）使用。放回引擎模块会让入队为了一个指针
import 整台引擎；两边各写一份则必然分叉（入队按 `bc_round_completed` 算出 `iters+1`，
`add_course` 直接开一轮不存在的训练，重开永远不发生）。

**不写盘、不建 runtime**：只读账本与已完成 job 的 manifest，判据与重开事件共用一份实现。
住在 `worker/`（纯读法）：`trainer/` 只放编排，且这里不反向 import 引擎——用 `Protocol` 描述
所需字段，`BcRuntime` 结构化满足它。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from worker.bc_ledger import bc_progress, completed_jobs, recorded_epochs


class BcResumeRuntime(Protocol):
    """`bc_next_it` 需要的运行时字段（`BcRuntime` 与测试替身都满足）。

    `course` 用 `Any`：这里只需要 `course.train.epochs`；把 Pydantic 的嵌套模型搬进 Protocol
    会因可变属性的不变性导致 `BcCourseConfig` 反而匹配不上（mypy 已实测）。
    """

    jsonl_path: Path
    iters: int
    smoke: bool
    job_root: Path
    course: Any


def completed_epochs(jsonl_path: str | Path, it: int, job_root: Path) -> int | None:
    """该轮**已经跑完**的最大 epoch；无法判定 ⇒ None。

    账本优先（`bc_epoch` / G3 起完成事件里的 `epochs`）；旧账本没有这两者时，回退到
    「该轮已收口 job 的 manifest.epochs」——这正是 §16.3-B2 写的第二判据。
    **None 与 0 必须分开**：没有证据时重开会把一次正常的已结算轮误判成预算扩大。
    """
    recorded = recorded_epochs(jsonl_path, it)
    if recorded > 0:
        return recorded
    done = completed_jobs(jsonl_path)
    best = 0
    try:
        manifests = sorted(
            p
            for p in Path(job_root).glob("*/manifest.json")
            if not p.parent.name.startswith(".")
        )
    except OSError:
        return None
    for mp in manifests:
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or str(m.get("kind", "")) != "bc":
            continue
        try:
            if int(m.get("it", -1)) != int(it):
                continue
        except (TypeError, ValueError):
            continue
        jid = str(m.get("job_id") or mp.parent.name)
        if jid not in done:
            continue
        epochs = m.get("epochs")
        if isinstance(epochs, int) and not isinstance(epochs, bool) and epochs > best:
            best = epochs
    return best or None


def bc_next_it(runtime: BcResumeRuntime, fallback: int = 1) -> int:
    """BC 课的下一轮：账本第一个未完成轮；预算扩大时回到**最后一个已完成轮**。

    `BcLoop.ledger_next_it` 与 supervisor 的入队共用这一份读法。入队若只读
    `bc_progress.next_it`，全轮完成后会拿到 `iters+1`；队列拿着这个显式任务直奔
    `_start_round`，40→80 的重开永远没机会发生。
    """
    prog = bc_progress(runtime.jsonl_path, runtime.iters)
    if prog.finished and prog.completed and not runtime.smoke:
        last = max(prog.completed)
        done = completed_epochs(runtime.jsonl_path, last, runtime.job_root)
        if done is not None and int(runtime.course.train.epochs) > done:
            return last
    return prog.next_it
