#!/usr/bin/env python
"""hub_delta_sweep.py — 把 hub 树里**已经原样落下的 delta 容器**就地补落成整份（2026-10-15）。

## 为什么要这个工具

`hub/deltas.py` 只管**以后**的回传（hub 受理时当场落地）。盘上那些之前的容器**不会自愈**，
而它们每一个都让一个读者当场炸：

  * `tmp/<课>/weights.json`（活动权重指针 = **本机循环下一轮的 init**）⇒ `JSON.parse` 报
    `Unexpected identifier "BWD1"`；
  * `tmp/<课>/remote-jobs/offline/<run>/it-NNN/{weights.json,opt.tar}`（回传树 = 续跑锚点的来源）
    ⇒ 锚点交回云机后，云机下一轮 restore 时 `extract_tar_bytes` 报 `invalid header`；
  * `tmp/<课>/deliver/<run>/it-NNN/weights.json`（交付镜像 = 控制台/TS 读的那份）；
  * `nn-training/weights/<课>/<课>.it<N>.<时间戳>.json`（归档 = 控制台 evalA 的 iter 选择器扫的面）。

本工具用**同一套落地器**（`remote.weights_standard` / `remote.opt_standard`，hub 受理路径
`hub.deltas.land_round` 调的就是这两个）把它们补落 —— 不另写一份重建逻辑（两份必然漂开）。

## 语义（与 `hub/deltas.py` 逐条同口径）

  * **名字跟着内容走**：落地后整份 = `.json`/`.tar`，原件旁存 `.delta`（两版都留，谁看名字就知道
    手里是哪一版）；
  * **基准只认逐字节命中**：找不到 ⇒ **响亮报错并保持原件**（落错基准 = 形状对、数值错，
    比拒绝危险得多）；
  * **只落地，不删任何东西**（删是不可逆的；落地成功后 `.delta` 就是回溯面）。原先「落地做不到
    就删掉那些 delta 轮、退到更早的整份轮续跑」是**兜底**，只在基准真的缺失时才需要人工决定。

## 用法

缺省是**只看不写**（dry-run：把每个容器的基准只读算一遍，报「可落地 / 基准缺失」）：

    bun dashboard/src/launch/cli.ts --script tools/hub_delta_sweep.py -- --traj-root tmp --course y5-term
    bun dashboard/src/launch/cli.ts --script tools/hub_delta_sweep.py -- --traj-root tmp --apply

`--course` 可重复；缺省 = `--traj-root` 下所有带 `remote-jobs/offline` 的课。`--no-archive`
跳过归档那一面。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]  # 仓库根
_NN = _ROOT / "nn-training"
if str(_NN) not in sys.path:
    sys.path.insert(0, str(_NN))

import remote.delta_files as delta_files
from common.cli import house_parser, run_main
from common.errors import ProtocolError
from hub import deltas as hub_deltas
from remote import opt_standard as opt_standard
from remote import weights_standard as weights_standard
from remote.artifacts import weights_archive_root

#: 回传树与交付镜像（两处的形状**同构**：`<kind>/<run>/it-NNN/`）。
_ROUND_KINDS = ("remote-jobs/offline", "deliver")


def _course_dir(traj_root: Path, course: str) -> Path:
    """`<traj>/<课>`（课程树的契约形状：里面就有 `remote-jobs/` 与 `training_log.jsonl`）。"""
    return traj_root / course


def _round_dirs(course_dir: Path) -> list[Path]:
    """要落地的**轮目录**：课程根本身（活动权重指针那份）+ 回传树/镜像里每个 `it-NNN/`。

    课程根也算一轮：`hub.deltas.land_round` 只在那里找 `weights.json`/`opt.tar` 两个名字，
    而 `<traj>/<课>/weights.json` 正是「活动权重指针」那一份。
    """
    out: list[Path] = [course_dir]
    for kind in _ROUND_KINDS:
        base = course_dir / kind
        if not base.is_dir():
            continue
        for run in sorted(p for p in base.iterdir() if p.is_dir()):
            out += sorted(p for p in run.glob("it-*") if p.is_dir())
    return out


def _archive_containers(course: str) -> list[Path]:
    """归档目录里**身子是容器**的那些 `<课>.it<N>.<时间戳>.json`（整份的不管）。"""
    root = weights_archive_root() / course
    if not root.is_dir():
        return []
    return [p for p in sorted(root.glob("*.json")) if delta_files.any_container_file(p)]


def _quiet(*_a: object) -> None:
    """探针用：落地器的逐条日志不打（报告里每个容器已经有一行结论）。"""
    return None


def _can_land(part: str, path: Path, *, course_dir: Path | None, course: str, scratch: Path) -> str:
    """**只读**算一遍：这份容器能不能落地（dry-run 用；一个字节都不写）。

    走的是两个落地器的**读侧内核**（`_standard_bytes` / `land_opt_bytes`）：`ensure_standard_*`
    的前半段就是它们，区别只在于后者会把结果写回盘。成功 ⇒ 返回整份的字节数摘要，失败 ⇒ 报原因。
    """
    if part == "weights":
        full = weights_standard._standard_bytes(
            path, course=course, traj_root=course_dir, extra_roots=()
        )
    else:
        full = opt_standard.land_opt_bytes(
            path.read_bytes(), path=path, traj_root=course_dir, work_dir=scratch, log=_quiet
        )
    return f"整份 {len(full)} B"


def _round_counts() -> dict[str, int]:
    return {"rounds": 0, "landed": 0, "failed": 0, "skipped": 0}


def sweep_rounds(
    course_dir: Path, course: str, *, apply: bool, scratch: Path, log=print
) -> dict:
    """课程树那一面：活动权重指针 + 回传树/镜像里的每个 `it-NNN/`。

    幂等：整份的件只读 4 字节就跳过（`hub.deltas.land_round` 内部那套）；重跑只会再报一遍
    「已经是整份」。
    """
    counts = _round_counts()
    for it_dir in _round_dirs(course_dir):
        counts["rounds"] += 1
        where = it_dir.relative_to(_ROOT)
        if not apply:
            # dry-run：先看这轮有没有容器，再对每个容器只读算一遍基准在不在。
            todo: list[tuple[str, Path]] = []
            for name, part in (("weights.json", "weights"), ("opt.tar", "opt")):
                found = delta_files.find_existing(it_dir, name)
                if found is not None and delta_files.any_container_file(found):
                    todo.append((part, found))
            if not todo:
                counts["skipped"] += 1
                continue
            for part, p in todo:
                try:
                    detail = _can_land(part, p, course_dir=course_dir, course=course, scratch=scratch)
                except ProtocolError as e:
                    counts["failed"] += 1
                    log(f"[sweep] ✗ {part} 基准缺失 {p.relative_to(_ROOT)}\n        {e}")
                    continue
                counts["landed"] += 1
                log(f"[sweep] · {part} 可落地（{detail}）{p.relative_to(_ROOT)}")
            continue
        got = hub_deltas.land_round(it_dir, traj=course_dir, course=course, log=log)
        for part in ("weights", "opt"):
            state = got.get(part)
            if state == "landed":
                counts["landed"] += 1
                log(f"[sweep] ✓ {part} 已落地 → {where}")
            elif state == "failed":
                counts["failed"] += 1
        if note := str(got.get("note") or ""):
            log(f"[sweep] ✗ {where}：{note}")
        if all(got.get(p) in ("absent", "full") for p in ("weights", "opt")):
            counts["skipped"] += 1
    return counts


def sweep_archive(course: str, *, apply: bool, course_dir: Path | None, scratch: Path, log=print) -> dict:
    """归档那一面：`nn-training/weights/<课>/` 里身子是容器的那些 `.json`（整份的不管）。

    为什么也要管：那是控制台 evalA 的 iter 选择器（`eval-board/ckpts.ts`）扫的目录，装 BWD1 的
    `.json` 会让读者 `JSON.parse` 报错（现场 `y5-term.it237.*.json` 正是这个形状）。
    `course_dir` 给 `None`（课已经不在 traj 里了）也照样能动 —— 基准搜归档根与 `traj_of` 推出来的树。
    """
    counts = _round_counts()
    for p in _archive_containers(course):
        where = p.relative_to(_ROOT)
        if not apply:
            try:
                detail = _can_land("weights", p, course_dir=course_dir, course=course, scratch=scratch)
            except ProtocolError as e:
                counts["failed"] += 1
                log(f"[sweep] ✗ 归档权重 基准缺失 {where}\n        {e}")
                continue
            counts["landed"] += 1
            log(f"[sweep] · 归档权重 可落地（{detail}）{where}")
            continue
        try:
            weights_standard.ensure_standard_weights(p, course=course, traj_root=course_dir, log=log)
            counts["landed"] += 1
            log(f"[sweep] ✓ 归档权重 已落地 → {where}")
        except ProtocolError as e:
            counts["failed"] += 1
            log(f"[sweep] ✗ 归档权重 落地失败（原件保留）{where}：{e}")
    return counts


def _courses(traj_root: Path) -> list[str]:
    """缺省扫的课 = `traj-root` 下所有带 `remote-jobs/offline` 的目录。"""
    return sorted(
        p.name
        for p in traj_root.iterdir()
        if p.is_dir() and (p / "remote-jobs" / "offline").is_dir()
    )


def _archive_courses() -> list[str]:
    """缺省扫的归档课 = `nn-training/weights/` 下的每个课程目录（含**已经不跑**的课）。"""
    root = weights_archive_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def main(argv: list[str] | None = None) -> int:
    ap = house_parser(description="把 hub 树里已落下的 delta 容器就地补落成整份（缺省只报告）")
    ap.add_argument("--traj-root", default="tmp", help="训练轨迹根（缺省 tmp）")
    ap.add_argument(
        "--course",
        action="append",
        default=[],
        help="课程名（可重复；缺省 = traj-root 下所有带 remote-jobs/offline 的课）",
    )
    ap.add_argument("--apply", action="store_true", help="真的落盘（缺省 = dry-run，只报告）")
    ap.add_argument("--no-archive", action="store_true", help="不扫 nn-training/weights/<课>/ 那一面")
    args = ap.parse_args(argv)
    # `.resolve()`：报告里的 `relative_to(_ROOT)` 与落地器的候选根都要绝对路径
    #（相对路径会让 `relative_to` 抛 ValueError，且候选根比较会退化成字符串比对）。
    root = Path(args.traj_root).resolve()
    if not root.is_dir():
        raise SystemExit(f"traj-root 不存在：{root}")
    # 课程树那一面：`--course` 点名 / 缺省 = traj-root 下跑过的课。点名但目录不在的课不报错
    #（它可能只剩归档了 —— 归档那一面照样要扫，见下）。
    named = list(args.course)
    traj_courses = [c for c in (named or _courses(root)) if _course_dir(root, c).is_dir()]
    archive_courses = [] if args.no_archive else (named or _archive_courses())
    if not traj_courses and not archive_courses:
        print(f"[sweep] {root} 下没有带 remote-jobs/offline 的课程，归档也没有", flush=True)
        return 0
    mode = "落地" if args.apply else "dry-run（只看不写）"
    print(
        f"[sweep] {mode}｜traj-root={root}｜树课：{', '.join(traj_courses) or '-'}"
        f"｜归档课：{', '.join(archive_courses) or '-'}",
        flush=True,
    )
    total = _round_counts()
    scratch = Path(tempfile.mkdtemp(prefix="delta-sweep-"))
    try:
        for c in traj_courses:
            for k, v in sweep_rounds(root / c, c, apply=args.apply, scratch=scratch).items():
                total[k] += v
        # 归档那一面按**课名**来（与树那一面分开）：课不在了（只剩归档）也照扫。
        for c in archive_courses:
            course_dir = _course_dir(root, c) if (root / c).is_dir() else None
            for k, v in sweep_archive(c, apply=args.apply, course_dir=course_dir, scratch=scratch).items():
                total[k] += v
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(
        f"[sweep] 合计：轮目录 {total['rounds']}｜可落地/已落地 {total['landed']}｜"
        f"基准缺失 {total['failed']}｜无需落地 {total['skipped']}",
        flush=True,
    )
    return 1 if total["failed"] else 0


if __name__ == "__main__":  # pragma: no cover
    run_main(main)
