"""配对 rotateSeed 核对（plan/accident.plan.md §2.5，2026-09-21）。

**配对怎么表达**：两门配对课程在各自文件里写**同一把 V**（`paired_rotate_seed`，§2.3）⇒
`(rotateSeed, it)` 种子流逐轮一致 = McNemar 前提。所以「配对」在本仓的机器含义就是
「声明了同一把 V 的那几门课」，本模块按这个口径做核对，不需要第二份配对登记表
（`courses.<课>` 机器旋钮里没有、也不该有「谁是兄弟」——那是课程设计事实，住课程文件）。

**要治的形态**（2026-09-21 凌晨）：两臂靠人手在命令行传 `--rotate-seed`，
第一次就传漏/传错（1789926833 vs 1789926915，相差 82 秒的抖动）⇒ 两臂跑在不同种子流上，
配对失败、返工重开。把 V 写进课程文件之后，这个形态在机制上消失；本模块守的是**残留口子**：

① **本课声明 ≠ 实际生效** ⇒ 拒启（启动期 `SystemExit`）。理论上有 `flat_overrides` 映射就
   不会发生，但那个映射漏过一次（`ent_break`：课程写 0.25、一律跑 0.6），静默失效的代价是
   **一条腿按错误的种子流跑 80 轮**——所以留一道断言式的闸。
② **声明了 V 却没有对端** / **对端上一次不在同一把 V 上** ⇒ 响亮告警（不阻断）。

**为什么跨臂只告警不停止**（与计划原文「不等即停」的偏差，记在这里与落地实录里）：
跨臂比对只能读对端**账本的末条 run_start**，而账本是历史累积——对端刚开课还没写 run_start、
或对端上一腿用的是旧 V，都会让「不等」成立而那些场景**并没有错配**；用陈旧读数杀掉一条
正在跑（或在正确 V 上跑）的腿，比漏报更贵。真正的 fail-fast 闸门放在**开课那一刻**：
控制台开课回执把同 V 课程表 + 各臂账本读数摆在操作员眼前（§5.3 同一屏），
以及上面 ① 的启动拒启。

本模块:读 + 判 + 组装文字（纯函数可断言）；IO 只有两处轻量读（子串预滤）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rl.config import CURRICULA_DIR

#: 课程键名（与 `rl/config.py::CourseConfig.paired_rotate_seed` 同名字面量）。
PAIRED_KEY = "paired_rotate_seed"

#: 账本里要读的事件名（`training_log.jsonl` 的启动事件）。
RUN_START_EVENT = "run_start"


def declared_paired_seed(course_obj: Any) -> int | None:
    """课程**显式声明**的配对 V；未声明（或显式 null）→ None。

    判据与 `flat_overrides` 同源：只看 `model_fields_set`——显式写 `null` 与不写**不同**
    （null 会透传覆盖 CLI 后门），而两者都不是「声明了一把 V」。
    """
    if course_obj is None:
        return None
    if PAIRED_KEY not in getattr(course_obj, "model_fields_set", set()):
        return None
    v = getattr(course_obj, PAIRED_KEY, None)
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return int(v)


def scan_paired_courses(
    declared: int, *, self_name: str = "", curricula_dir: Path | None = None, limit: int = 64
) -> tuple[tuple[str, int], ...]:
    """课程目录里**声明了同一把 V** 的其它课程（`(课程名, V)` 升序）。

    轻量扫描：先看文件文本里有没有 `"paired_rotate_seed"`（绝大多数文件没有，直接跳过），
    再交给 jsonc 解析器取键。坏文件跳过（它的合法性由 `load_course` 在别处响亮失败决定，
    不能因为一个坏文件让核对整体失效）。
    """
    root = Path(curricula_dir) if curricula_dir is not None else CURRICULA_DIR
    out: list[tuple[str, int]] = []
    try:
        files = sorted(root.glob("*.jsonc"))
    except OSError:
        return ()
    for p in files:
        if p.name.endswith(".bc.jsonc"):
            continue
        name = p.name[: -len(".jsonc")]
        if self_name and name == self_name:
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if PAIRED_KEY not in raw:  # 子串预滤：解析成本只在候选文件上
            continue
        try:
            from rl.jsonc import load as _load_jsonc

            data = _load_jsonc(str(p))
        except Exception:  # 坏课程文件不是本核对的责任（`load_course` 在别处响亮失败）
            continue
        v = data.get(PAIRED_KEY)
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if int(v) == int(declared):
            out.append((name, int(v)))
        if len(out) >= limit:
            break
    return tuple(out)


def latest_run_start_seed(traj_dir: Path | str) -> int | None:
    """课 traj 下 `training_log.jsonl` 里**最后一条** `run_start` 的 rotateSeed；无 → None。

    读法与训练侧启动路径同源（`rl/resume.read_last_rotate_seed` 的同一条事件、同一个字段），
    但这里按子串预滤再 parse：账本可以很大，而核对只关心一种事件。
    """
    p = Path(traj_dir) / "training_log.jsonl"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    seed: int | None = None
    for line in text.splitlines():
        if RUN_START_EVENT not in line:
            continue
        try:
            import json

            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("event") != RUN_START_EVENT:
            continue
        v = r.get("rotateSeed")
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        seed = int(v)  # 末条为准（续跑会再写一条，最新的事实才有效）
    return seed


def compose_lines(
    *,
    declared: int | None,
    effective: int,
    source: str,
    siblings: tuple[tuple[str, int], ...],
    sibling_seeds: tuple[tuple[str, int | None], ...],
) -> list[str]:
    """核对行（**纯函数**：数字进、文字出）。返回按顺序的日志行。"""
    lines: list[str] = []
    if declared is None:
        lines.append(
            f"[run_rl] paired seed: 本课未声明 {PAIRED_KEY} ⇒ 本腿 rotateSeed={effective}"
            f"（来源 {source}）——按**单腿**口径（配对课程应各写同一把 V，"
            "见 plan/accident.plan.md §2；写进课程文件后不再经手指传）"
        )
        return lines
    lines.append(
        f"[run_rl] paired seed: V={declared}（课程声明 {PAIRED_KEY}；"
        f"实际生效 {effective}，来源 {source}）"
    )
    if not siblings:
        lines.append(
            f"[run_rl] WARNING paired seed: 声明了 V={declared} 但**没有其它课程**声明同一把 V"
            "——配对无对端（另一臂还没写上？还是本腿被当单腿跑？）"
            "⇒ 在补齐对端之前**不许按配对口径结算**"
        )
        return lines
    names = ", ".join(name for name, _ in siblings)
    lines.append(f"[run_rl] paired seed: 同 V 课程 = {names}")
    for name, seed in sibling_seeds:
        if seed is None:
            lines.append(f"[run_rl] paired seed:   {name}: 账本无 run_start（还没跑过）")
        elif seed == declared:
            lines.append(f"[run_rl] paired seed:   {name}: 账本 run_start.rotateSeed={seed} ✓ 同 V")
        else:
            lines.append(
                f"[run_rl] WARNING paired seed:   {name}: 账本 run_start.rotateSeed={seed} "
                f"≠ V={declared} ——那一臂上一次不在同一把 V 上（陈旧账本 / 未按课程文件起跑？）"
            )
    return lines


def pair_check(
    *,
    declared: int | None,
    effective: int,
    source: str,
    traj_root: Path | str,
    self_name: str = "",
    curricula_dir: Path | None = None,
) -> list[str]:
    """IO 组装：扫同 V 课程 + 读各自账本末条 run_start，返回核实行。

    **永不抛**：核对是观测设施，不得拖垮训练主线（与 §5.3 的对照行同一纪律）。
    """
    try:
        siblings = (
            scan_paired_courses(declared, self_name=self_name, curricula_dir=curricula_dir)
            if declared is not None
            else ()
        )
        root = Path(traj_root)
        seeds = tuple((name, latest_run_start_seed(root / name)) for name, _ in siblings)
        return compose_lines(
            declared=declared,
            effective=effective,
            source=source,
            siblings=siblings,
            sibling_seeds=seeds,
        )
    except Exception as e:  # 观测失败只说一句，绝不阻断（同 §5.3 对照行的纪律）
        return [f"[run_rl] WARN paired seed 核对失败（{type(e).__name__}: {e}）"]
