"""trainer_gate.py — 常驻训练**准入闸** + 逃生舱自报（plan/single-resident-trainer.plan.md §1）。

## 为什么需要它

`trainer/run_rl.py` 今天背四种角色：常驻训练循环 / 冒烟预演 / 导出快照 / 预采子进程；`trainer/run_bc.py`
同理（常驻 BC 编排 / 冒烟）。而常驻写者只允许一个 —— `trainer/run_rl_cluster.py --serve`
（单进程服务所有课程，R3-5）。per-course 常驻进程与共享调度器抢同一批 traj / 账本 / 权重，
正是 2026-09-06「双 trainer 并行写同一 traj 7 分钟」事故的同类面；今天它还会绕过控制台的开课流程
（漏开课标记 / 暂停意图解禁 / hub 三件套，2026-09-21 事故）。

## 判据（纯函数，无 I/O）

**一个一次性标志都没有 ⇒ 视为常驻训练**。与 `--course` 无关：无 `--course` 的老全局路径同样是常驻写者。

一次性标志集合（`ONESHOT_FLAGS`）：`--smoke` / `--collect-only N>0` / `--export-bundle` /
`--echo-config` / `--allow-detached-trainer`。

## 逃生舱

`--allow-detached-trainer` = 运维排障旁路（默认不许用）：放行 + 醒目告警 + 往
`tmp/trainer-detached.json` 记一笔（控制台横幅 `detachedTrainers` 的数据源，§1.5）。
它是 **S1→S2 之间的过渡期设计**，不要在 S2 落地前删掉（plan §4）。

## 为什么住 `worker/` 而不是 `trainer/run_rl.py`

`trainer/` 只许编排（`tests/test_layering.py::test_trainer_holds_only_orchestration_modules`），
而本模块是纯逻辑；`trainer/run_rl.py` 与 `trainer/run_bc.py` 两个入口**同源 import 这一份**
——判据写两份必然 drift（本仓的老 pattern）。

环境口：`NN_TRAINER_DETACHED` 覆盖自报文件路径（用例往 tmp 写，不碰操作员真盘；与
`trainer/loop_control.py` 的 `NN_LOOP_CONTROL*` 同规）。控制台读面用同一份文件的
`BCITY_TRAINER_DETACHED` 口（dashboard `core/paths.ts::trainerDetachedPath`）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from common.log import log

#: 仓库根（与 `worker/archive.py` 同一上溯层数：nn-training/<pkg>/<file> → battle）。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 一次性腿标志（豁免集合）。**判据是「有没有动作」，不是「有没有课」**——
#: 无 `--course` 的裸 `run_rl.py` 同样是常驻写者（老全局锁时代的形态），一样拒。
ONESHOT_FLAGS: tuple[str, ...] = (
    "smoke",  # --smoke：真跑一轮预演，落位前作废
    "collect_only",  # --collect-only N>0：预采子进程（T4 双缓冲）
    "export_bundle",  # --export-bundle <path>：只读快照导出
    "echo_config",  # --echo-config：纯 dump（对拍 oracle 读 stdout）
    "allow_detached_trainer",  # 运维逃生舱：本身就是一个「我知道我在干什么」的动作
)

#: 拒启消息（**必须给出替代**，否则操作员只会去找 `--force` —— 而 `--force` 是锁抢先，
#: 不是准入豁免：闸在锁之前，`--force` 过不了它）。
REFUSE_MESSAGE = (
    "[run_rl] 常驻训练只允许单一调度器：trainer/run_rl_cluster.py --serve\n"
    "  · 控制台：bun run dashboard（:8900）→ 组件卡「TrainingLoop」启动\n"
    "  · 无头：bun dashboard/src/launch/cli.ts --script trainer/run_rl_cluster.py\n"
    "  · 一次性腿（豁免）：--smoke / --export-bundle / --collect-only / --echo-config\n"
    "  · 运维排障逃生舱：--allow-detached-trainer（会打印醒目告警；默认不许用）"
)

#: BC 侧的拒启消息（同一判据，替代路径不同：BC 课由共享 trainer 开课服务）。
REFUSE_MESSAGE_BC = (
    "[run_bc] 常驻 BC 训练只允许单一调度器：trainer/run_rl_cluster.py --serve（serve 已带 BC 课）\n"
    "  · 控制台：bun run dashboard（:8900）→ 课程 → 开课（BC 课由共享 trainer 调度）\n"
    "  · 一次性腿（豁免）：--smoke\n"
    "  · 运维排障逃生舱：--allow-detached-trainer（会打印醒目告警；默认不许用）"
)

#: 逃生舱的醒目告警（放行分支打印；控制台横幅是同一事实的另一个读面）。
ESCAPE_HATCH_WARNING = (
    "[trainer_gate] ⚠⚠ 逃生舱已启用：--allow-detached-trainer —— 本进程是**独立 trainer**，"
    "与共享调度器（run_rl_cluster.py --serve）抢同一批 traj/账本/权重。\n"
    "  · 确认共享 trainer 不在服务这门课；用完就停掉本进程；\n"
    "  · 这一笔会写进 tmp/trainer-detached.json——控制台会挂横幅。"
)

#: 控制台横幅保留的最近条数（多起几次不会把文件刷成日志）。
DETACHED_KEEP = 5

#: 自报文件的 argv 摘要上限（横幅只显示一行；整条命令行可以很长）。
ARGV_SUMMARY_MAX = 400


def oneshot_action_present(args: Any) -> bool:
    """带了**除逃生舱以外**的任一一次性动作标志吗？（纯函数，无 I/O）

    逐字段 `getattr` 带缺省：BC 解析器比 RL 少几个键，「没有这个字段」= 没传这个动作，不是错误。
    """
    if bool(getattr(args, "smoke", False)):
        return True
    if int(getattr(args, "collect_only", 0) or 0) > 0:
        return True
    if str(getattr(args, "export_bundle", "") or ""):
        return True
    return bool(getattr(args, "echo_config", False))


def resident_trainer_wanted(args: Any) -> bool:
    """本进程要跑**常驻训练循环**吗？（= 没有一次性动作，也没带逃生舱）

    ★ 「带了逃生舱」也算「不是常驻」——放行判据归 `refuse_detached_trainer`；
    本函数回答的是「要不要拒」，`escape_hatch_used` 回答「放行是不是靠逃生舱」。
    """
    return not oneshot_action_present(args) and not bool(getattr(args, "allow_detached_trainer", False))


def escape_hatch_used(args: Any) -> bool:
    """放行是**靠逃生舱**吗？（= 带了 `--allow-detached-trainer`，且除此之外没有一次性动作）

    ⇒ 调用点据此：打印醒目告警 + 往 `tmp/trainer-detached.json` 记一笔（控制台横幅数据源）。
    带一次性腿的进程即使迷上也带了逃生舱，也**不算**独立 trainer（它不跑常驻循环）。
    """
    return bool(getattr(args, "allow_detached_trainer", False)) and not oneshot_action_present(args)


def refuse_detached_trainer(args: Any, *, message: str = REFUSE_MESSAGE) -> str | None:
    """拒启消息，或 None（放行）。**纯判据** —— 打印/退出/落盘全在调用点。"""
    return message if resident_trainer_wanted(args) else None


def detached_report_path() -> str:
    """自报文件路径（`NN_TRAINER_DETACHED` 可覆盖——用例重定向用，默认行为零变化）。

    与 `trainer/loop_control.py::control_path` 同一个形状（env 惰性读，不缓存）。
    """
    return os.environ.get("NN_TRAINER_DETACHED") or str(REPO_ROOT / "tmp" / "trainer-detached.json")


def announce_escape_hatch(course: str = "", *, entry: str = "run_rl", log_fn=log) -> None:
    """逃生舱放行的**两件副作用**：醒目告警 + 记一笔（横幅数据源）。写失败只告警。"""
    log_fn(ESCAPE_HATCH_WARNING)
    path = note_detached_trainer(course, entry=entry, log_fn=log_fn)
    if path:
        log_fn(f"[trainer_gate] 已记录：{path}")


def _argv_summary(argv: list[str] | None = None) -> str:
    raw = " ".join(argv if argv is not None else sys.argv)
    return raw[:ARGV_SUMMARY_MAX] + ("…" if len(raw) > ARGV_SUMMARY_MAX else "")


def note_detached_trainer(
    course: str = "",
    *,
    entry: str = "run_rl",
    argv: list[str] | None = None,
    log_fn=log,
) -> str | None:
    """记一笔「有人用逃生舱起了独立 trainer」→ 控制台横幅的数据源。返回写入路径（失败 None）。

    三条纪律：
      · **原子写**（`common/fs.py::atomic_write_json`）：控制台每拍读它，半写文件会让 `/api/state`
        读到坏 JSON（读侧要能容忍，但写侧没有理由制造）；
      · **保留最近 `DETACHED_KEEP` 条**（新的在前）；读旧文件失败（缺失/坏 JSON/形状不对）⇒ 从空表
        重开（不因为一个坏文件让逃生舱失效）；
      · **写失败只告警不阻断**：这一笔的用途是「让控制台看得见」，逃生舱本身的存在性优先于记录
        （排障时刻不该因为磁盘问题拒启）。
    """
    from common.fs import atomic_write_json

    path = detached_report_path()
    rec = {
        "pid": os.getpid(),
        "course": str(course or ""),
        "entry": str(entry),
        "at": time.time(),
        "argv": _argv_summary(argv),
    }
    old: list[Any] = []
    try:
        p = Path(path)
        if p.exists():
            import json

            got = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(got, list):
                old = [x for x in got if isinstance(x, dict)]
    except Exception as e:  # 坏 JSON / 读不到 —— 从空表重开（不阻断）
        log_fn(f"[trainer_gate] WARN: 自报文件不可读（{e}），本次从空表重开")
    try:
        atomic_write_json(path, [rec, *old][:DETACHED_KEEP])
        return path
    except OSError as e:
        log_fn(f"[trainer_gate] WARN: 自报文件写失败（{e}）——逃生舱照常放行，控制台看不到这一笔")
        return None
