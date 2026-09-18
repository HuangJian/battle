"""loop_plan —— 把**盘上事实**翻译成调度器能吃的计划（R2c-1 的 IO 边缘）。

职责刻意单一：`loop_scheduler` 是纯调度（可脱离 torch/网络单测）、`loop_tasks` 是纯判据，
本模块是唯一碰盘的地方——读账本得到指针、读 shard 目录得到采集进度、读 commit journal
得到在飞集，拼成一个 `CourseQueue` 的初始内容。

**判据永远朝「不跳」的方向保守**：算不出来的事实一律留默认值（`False`/`0`），
因为 `already_done` 对未知事实返回 `False` ⇒ 任务留在队列里（宁可重做，不可误跳——
误跳会丢一轮语料，这是本仓库最贵的一类 bug）。
"""

from __future__ import annotations

from pathlib import Path

from rl.commit_journal import CommitJournal
from rl.loop_tasks import RoundFacts, Task, pending_tasks, round_tasks
from rl.train_ledger import LedgerSpec, LedgerView, load_ledger


def course_traj(traj_root: str | Path, course: str) -> Path:
    """课程 traj 目录 = `<traj-root>/<课>`（与训练侧 `tmp/<course>` 同布局）。"""
    return Path(traj_root) / course


def settled_shards(traj: Path, it: int) -> int:
    """`traj/it<N>` 下已结算（manifest 落盘）的 shard 数——采集进度的事实口径。

    `write_shard` 先写 npy 后写 manifest ⇒ 有 manifest 即「这一局完整落盘」，
    与断点续跑用的判据同一个（不引入第二套定义）。
    """
    d = Path(traj) / f"it{it}"
    if not d.exists():
        return 0
    try:
        return sum(1 for _ in d.rglob("rl_s*_seed*/manifest.json"))
    except OSError:
        return 0


def inflight_from_journals(traj: Path, it: int | None = None) -> list[dict]:
    """从 `it<N>/commit_journal.jsonl` 读**在飞集**（已发布未回传的提交 + job_id）。

    不传 `it` 时扫所有迭代目录里的 journal（取并集）——「这门课现在到底在等什么」
    是运维最常问的一句话，而答案今天只散在日志里。
    """
    root = Path(traj)
    out: list[dict] = []
    if not root.exists():
        return out
    dirs = [root / f"it{it}"] if it is not None else sorted(root.glob("it*"))
    for d in dirs:
        jp = d / "commit_journal.jsonl"
        if not jp.exists():
            continue
        for rec in CommitJournal(jp).inflight():
            out.append({**rec, "dir": d.name})
    return out


def plan_course(
    course: str,
    traj: Path,
    *,
    view: LedgerView | None = None,
    spec: LedgerSpec | None = None,
    games_planned: int = 0,
    weights_landed: bool = False,
) -> tuple[int, list[Task], dict]:
    """课程当前的（指针, 待办任务表, 事实）——调度器 `planner` 的生产实现。

    不传 `view` 时自己扫一遍账本（= 开课/续跑读一次，用户口径）。
    `games_planned`/`weights_landed` 由调用方给（它们来自课程计划与权重账本，本模块
    不臆测）；缺省即「未知」⇒ 对应任务不会被当成已完成。
    """
    traj = Path(traj)
    v = view if view is not None else load_ledger(traj / "training_log.jsonl", spec)
    it = int(v.next_it)
    facts = RoundFacts(
        it=it,
        iteration_recorded=any(r.it == it for r in v.rows),
        games_settled=settled_shards(traj, it),
        games_planned=int(games_planned),
        weights_landed=bool(weights_landed),
    )
    tasks = pending_tasks(round_tasks(course, it), facts)
    facts_dump = {
        "it": it,
        "iteration_recorded": facts.iteration_recorded,
        "games_settled": facts.games_settled,
        "games_planned": facts.games_planned,
        "weights_landed": facts.weights_landed,
        "iterations": v.iterations_n,
        "last_verdict": v.last_verdict,
        "train_sec_total": round(v.train_sec_total, 1),
        "soft_remediate_count": v.soft_remediate_count(frozenset({"plateau"})),
        "kl_streak": v.kl_streak,
    }
    return it, tasks, facts_dump


def discover_courses(traj_root: str | Path) -> list[str]:
    """扫 `<traj-root>/*/training_log.jsonl` 得到课程表（与 hub `--discover` 同判据）。

    「有账本 = 这门课在这里跑过」是文件系统事实，不需要注册表（R1 的同一原则）。
    """
    root = Path(traj_root)
    if not root.exists():
        return []
    out: list[str] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if (d / "training_log.jsonl").exists():
            out.append(d.name)
    return out
