"""remote/backfill_offline.py — 把**已经落地的回传轮**补做课程侧落位（一次性修复）。

用户 2026-09-23 实测缺口（`x20-demo-mix` 的 seg-2，it49–124）：产物**都在盘上**
（`<traj>/remote-jobs/offline/<run>/it-NNN/{weights,opt,row}`），但

  * `deliver/<run>/` 只有人工导入的 seg-1 ⇒ 两段分居两棵树；
  * `<traj>/weights.json` 整段停在段起点（= 该 run 的 `it0` 指纹）；
  * `nn-training/weights/<课>/` 一个回传轮都没有 ⇒ 控制台 evalA 的 iter 选择器
    （`eval-board/ckpts.ts` 扫那个目录）**看不见回传段的任何一轮**。

修法已进 `hub_server._land_offline_round_extras`（新的回传轮自动做这三件事）。本模块是
**历史轮的补做**：对每个已落地的 `it-NNN` 调**同一个**函数（不复制第二份落位逻辑——
两份必然漂开，而\"两腿同构\"正是这件事的意义）。

幂等与安全性：

  * 镜像：目标已存在就跳过（`_land_offline_round_extras` 内部判定）；
  * 活动权重：只在账本里没有更新的轮次时推进（同一判据，所以补做**不会**把已经跑在前面的
    本机循环权重顶回旧轮）；
  * 归档：先看 `<prefix>.it<N>.*.json` 是否已在 —— 已有就跳过（否则每次补做都再写一份，
    归档目录会堆积同轮副本）。这是本模块**唯一**比 hub 那条路多出来的判断：hub 侧同一轮
    重复投递在入口就被拒了（duplicate），这里没有那道门。

运行（训线操作一律经控制台/启动器，不裸 python；`--` 之后是本脚本自己的参数）：

    bun dashboard/src/launch/cli.ts --script remote/backfill_offline.py -- --traj-root tmp
    bun dashboard/src/launch/cli.ts --script remote/backfill_offline.py -- \
        --traj-root tmp --course x20-demo-mix
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from remote.hub_server import _JobStore, _weights_archive_root

#: 记录三件齐全的判据（与 `_JobStore.RESUME_PARTS` 同源：缺 opt 就是动量归零、缺 row 就是
#: 曲线少一行 —— 两者都是"看起来能跑但读数少一截"）。
_PARTS = _JobStore.RESUME_PARTS


def backfill_course(traj_root: Path, course: str, *, log=print) -> dict:
    """补做一门课的全部已落地回传轮；返回计数（`{rounds, mirrored, archived, skipped}`）。"""
    traj = Path(traj_root) / course
    if not (traj / "remote-jobs").is_dir():
        raise SystemExit(f"课程目录不存在或没有 remote-jobs: {traj}")
    store = _JobStore(traj / "remote-jobs", traj / "training_log.jsonl")
    base = traj / "remote-jobs" / _JobStore.OFFLINE_DIR
    runs = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    # 全部轮次先收集再**按 it 升序**处理：活动权重那条路（"没有更新的轮次才推进"）在
    # 账本还没有 iteration 行的老段上，靠的正是"最后处理的是最大 it"。
    rounds: list[tuple[int, Path]] = []
    for run_dir in runs:
        for it_dir in sorted(run_dir.iterdir()):
            if not it_dir.is_dir() or not it_dir.name.startswith("it-"):
                continue
            try:
                it = int(it_dir.name[3:])
            except ValueError:
                continue
            if all((it_dir / n).is_file() for n in _PARTS):
                rounds.append((it, it_dir))
    counts = {"rounds": 0, "mirrored": 0, "archived": 0, "skipped": 0}
    if not rounds:
        log(f"[backfill] {course}: 没有三件齐全的已落地回传轮（{base}）")
        return counts
    log(f"[backfill] {course}: {len(rounds)} 个已落地轮（{len(runs)} 个 run），开始补做")
    for it, it_dir in sorted(rounds):
        run_id = it_dir.parent.name
        before = _archived_names(it)
        try:
            store._land_offline_round_extras(
                it=it,
                run_id=run_id,
                weights_json=(it_dir / "weights.json").read_bytes(),
                opt=(it_dir / "opt.tar").read_bytes() if (it_dir / "opt.tar").is_file() else b"",
                row=json.loads((it_dir / "row.json").read_text(encoding="utf-8")),
            )
        except (OSError, ValueError) as e:
            log(f"[backfill] WARN it{it} 补做失败（跳过）：{type(e).__name__}: {e}")
            counts["skipped"] += 1
            continue
        counts["rounds"] += 1
        if (traj / _JobStore.DELIVER_DIR / run_id / f"it-{it:03d}" / "weights.json").is_file():
            counts["mirrored"] += 1
        if _archived_names(it) != before:
            counts["archived"] += 1
    log(
        f"[backfill] {course} 完成：轮 {counts['rounds']}｜镜像 {counts['mirrored']}｜"
        f"归档 {counts['archived']}｜跳过 {counts['skipped']}"
    )
    return counts


def _archived_names(it: int) -> set[str]:
    """当前归档根下该轮已存在的归档文件名（用于"归档只写一次"的判断）。"""
    root = _weights_archive_root()
    if not root.is_dir():
        return set()
    return {p.name for p in root.rglob(f"*.it{it}.*.json")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补做已落地回传轮的课程侧落位（一次性修复）")
    ap.add_argument("--traj-root", default="tmp", help="训练轨迹根（缺省 tmp）")
    ap.add_argument(
        "--course",
        action="append",
        default=[],
        help="课程名（可重复；缺省 = traj-root 下所有含 remote-jobs 的课）",
    )
    args = ap.parse_args(argv)
    root = Path(args.traj_root)
    courses = list(args.course)
    if not courses:
        courses = sorted(
            p.name
            for p in root.iterdir()
            if p.is_dir() and (p / "remote-jobs" / _JobStore.OFFLINE_DIR).is_dir()
        )
    if not courses:
        print(f"[backfill] {root} 下没有带 remote-jobs/offline 的课程", flush=True)
        return 0
    total = {"rounds": 0, "mirrored": 0, "archived": 0, "skipped": 0}
    for c in courses:
        got = backfill_course(root, c)
        for k in total:
            total[k] += got[k]
    print(f"[backfill] 合计：{total}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
