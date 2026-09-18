"""run_rl_cluster.py —— 单进程多课程调度器的入口（R2c，plan/r2-loop-task-queue §4）。

**默认 = 只读计划视图**：一个进程读**所有**并行课程的账本 + commit journal，把「每门课下一步
该做什么、在等什么、被什么资源挡住」打成一张表。它不训练、不发布、不等待——是调度器的
**只读一半**，也是运维视图（今天要开 N 个终端看 N 份日志才知道这些）。

**`--serve` = 写的一半**（R2d）：真跑一个 supervisor（`rl/loop_serve.py`），N 门课共用一份
资源池与一份 torch 引擎池（`rl/engine_pool.py`）。课程必须显式列出（`--courses`）——
「该训哪几门课」是运维决定，不猜。

用法：
  python run_rl_cluster.py                          # 自动发现 tmp/*/training_log.jsonl
  python run_rl_cluster.py --courses c4-dodge,c5-tick
  python run_rl_cluster.py --traj-root tmp --json    # 机器可读（控制台/CI 用）
  python run_rl_cluster.py --serve --courses c4-dodge,c5-tick   # 真跑多课（R2d）

**`--json` 是控制台「调度器」卡片的契约面**（dashboard `server/api/loop-queue.ts` 消费，
TTL 缓存）：改 `--json` 的字段名/语义 = 改控制台，两边必须在同一次改动里对齐（`waiting`
那一列就是「每课在等什么」，`tests/test_loop_plan_waiting.py` 盯住它的取值域）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from rl.loop_plan import (
    course_traj,
    discover_courses,
    inflight_from_journals,
    plan_course,
    waiting_state,
)
from rl.loop_scheduler import CourseQueue, Supervisor
from rl.loop_tasks import Task, TaskResult


def _never(task: Task, queue: CourseQueue) -> TaskResult:
    """只读模式的执行体：**永不被调用**（被调用即说明计划视图变成了真执行——必须响）。"""
    raise AssertionError(f"dry-run 不应执行任务 {task.task_id}")


def build_rows(courses: list[str], traj_root: str, sup: Supervisor) -> list[dict]:
    """每课一行（= `--json` 的主体，也是控制台调度器卡片的唯一数据源）。

    只读盘：账本（指针 + 事实）+ `commit_journal`（在飞集）+ shard 目录（采集进度）。
    「在等什么」由 `loop_plan.waiting_state` 单点计算——CLI 表与控制台卡片是**同一份**
    语义的两个渲染面（控制台不得自己从 facts 重算：那是第二份真相）。
    """
    rows: list[dict] = []
    for course in courses:
        traj = course_traj(traj_root, course)
        it, tasks, facts = plan_course(course, traj)
        inflight = inflight_from_journals(traj)
        current = tasks[0].kind if tasks else ""
        kind, text = waiting_state(
            inflight=inflight,
            games_settled=int(facts["games_settled"]),
            games_planned=int(facts["games_planned"]),
            pending=len(tasks),
            current=current,
        )
        # 队列状态取自调度器本身（`add_course` 的 ready/done 判定），不在这里再写一遍
        # 「有任务 = ready」——两处各写一遍就是第一个分叉点。
        q = sup.add_course(course, it, tasks)
        rows.append(
            {
                "course": course,
                "it": it,
                "state": q.state,
                "current": current,
                "pending": [t.kind for t in tasks],
                "inflight": inflight,
                "facts": facts,
                "waiting": {"kind": kind, "text": text},
            }
        )
    return rows


def _fmt_table(rows: list[dict]) -> str:
    """人读表：课程 / 轮次 / 状态 / 当前任务 / 待办 / 在飞（含 job_id）/ 在等什么 / 关键事实。"""
    out: list[str] = []
    hdr = f"{'course':<22} {'it':>4} {'state':<8} {'next task':<18} {'pending':>7} {'inflight':>8}"
    out.append(hdr)
    out.append("-" * len(hdr))
    for r in rows:
        out.append(
            f"{r['course']:<22} {r['it']:>4} {r['state']:<8} {r['current'] or '-':<18} "
            f"{len(r['pending']):>7} {len(r['inflight']):>8}"
        )
        out.append(f"    waiting: {r['waiting']['text']}")
        if r["pending"]:
            out.append(f"    pending: {', '.join(r['pending'])}")
        for rec in r["inflight"]:
            out.append(
                f"    inflight: {rec.get('phase', '?')}@{rec.get('round', '?')} "
                f"jid={rec.get('jid', '-')} via {rec.get('dispatch', '-')}（{rec.get('dir', '-')}）"
            )
        f = r["facts"]
        out.append(
            f"    facts: iterations={f['iterations']} last_verdict={f['last_verdict']} "
            f"train_sec={f['train_sec_total']} soft_remediate={f['soft_remediate_count']} "
            f"kl_streak={f['kl_streak']} shards(it)={f['games_settled']}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    # CLI 入口钉 UTF-8（见 tests/subproc_util.py 的契约：每个 CLI 入口都要调）
    from platform_utils import force_utf8_stdio

    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="单进程多课程训练调度器（R2c-1：只读计划视图）")
    ap.add_argument("--traj-root", default="tmp", help="课程 traj 根（每课 = <root>/<课>）")
    ap.add_argument("--courses", default="", help="逗号分隔；空 = 扫 --traj-root 自动发现")
    ap.add_argument("--json", action="store_true", help="输出 JSON（控制台/CI 消费）")
    ap.add_argument(
        "--local-ppo-slots", type=int, default=1, help="本机 PPO 池容量（定案：跨课排队 = 1）"
    )
    ap.add_argument("--local-eval-slots", type=int, default=1, help="本机 eval 池容量（同上）")
    ap.add_argument(
        "--rollout-slots", type=int, default=4, help="本机 rollout 池容量（子进程并行）"
    )
    ap.add_argument(
        "--serve",
        action="store_true",
        help="单进程**真跑** N 门课（R2d 写的一半：默认只读计划视图，--serve 才驱动 supervisor）",
    )
    ap.add_argument(
        "--iters", type=int, default=0, help="--serve：每课跑满多少轮（0 = 吃课程配置）"
    )
    ap.add_argument("--poll-sec", type=float, default=15.0, help="--serve：全员等外部时的再问间隔")
    ap.add_argument(
        "--max-seconds", type=float, default=0.0, help="--serve：墙钟上限（0 = 不限；调试/CI 用）"
    )
    args = ap.parse_args(argv)

    if args.serve:
        # 惰性导入：只读路径（控制台每 10s 跑一次 --json）不得为 torch/网络付导入代价。
        from rl.loop_serve import serve

        # `--mode` 是**课程级**附加参数（rl-config 默认按模式取）——只有它需要透传给开课。
        raw = list(argv) if argv is not None else sys.argv[1:]
        extra: list[str] = []
        if "--mode" in raw:
            i = raw.index("--mode")
            extra = ["--mode", raw[i + 1] if i + 1 < len(raw) else ""]
        courses = [c.strip() for c in args.courses.split(",") if c.strip()]
        if not courses:
            print("[serve] 必须显式给 --courses（单进程 supervisor 不猜「该训哪几门课」）")
            return 2
        rep = serve(
            courses,
            argv=extra,
            traj_root=args.traj_root,
            iters=args.iters,
            poll_sec=args.poll_sec,
            capacities={
                "local_ppo": args.local_ppo_slots,
                "eval_local": args.local_eval_slots,
                "local_rollout": args.rollout_slots,
            },
            max_seconds=args.max_seconds,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "stop_reason": rep.stop_reason,
                        "steps": rep.steps,
                        "courses": rep.courses,
                        "skipped": rep.skipped,
                        "engines": rep.engines,
                    },
                    ensure_ascii=False,
                )
            )
        return 0

    traj_root = args.traj_root
    courses = [c.strip() for c in args.courses.split(",") if c.strip()] or discover_courses(
        traj_root
    )
    if not courses:
        print(f"[cluster] {traj_root} 下没有任何课程账本（--courses 或先跑一门课）")
        return 0

    # 调度器只用来算「每课下一步是谁」+ 展示资源池读面；本轮不执行任何任务体。
    sup = Supervisor(
        executor=_never,
        planner=lambda course, it, q: plan_course(course, course_traj(traj_root, course))[1],
        capacities={
            "local_ppo": args.local_ppo_slots,
            "eval_local": args.local_eval_slots,
            "local_rollout": args.rollout_slots,
        },
    )
    rows = build_rows(courses, traj_root, sup)

    if args.json:
        print(json.dumps({"courses": rows, "pools": sup.snapshot()["pools"]}, ensure_ascii=False))
    else:
        print(_fmt_table(rows))
        pools = sup.snapshot()["pools"]
        print(
            "\n本机资源池（任一时刻可持有票数）："
            + "  ".join(f"{k}={v['capacity']}" for k, v in pools.items())
        )
        print(
            "[cluster] 只读计划视图（本入口不训练、不发布、不等待）。单进程 supervisor 的"
            "执行体已就位（rl/loop_runner.py：任务体↔引擎的唯一桥，回退路径逐字节同旧行为），"
            "“入队/暂停/单例 trainingLoop 卡片”的操作面见 plan/r2-loop-task-queue.md §8 的 R2d。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
