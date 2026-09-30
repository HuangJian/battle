"""rl/course_resolve —— **课程/关卡的查找与加载**（S5 第十刀，2026-09-27）。

从 `biz/config.py` 整块搬出（**逐字节不动**）。这里是「一门课/一个关卡**在盘上哪个文件、
怎么读进来**」的唯一实现：

* 目录常量：`CURRICULA_DIR`（curricula/*.jsonc）/ `LEVELS_DIR`（levels/*.jsonc）；
* 查找：`resolve_level` / `resolve_course`（名字/路径 → 路径；找不到时列出可用项）；
* 加载：`load_course`（JSONC → `CourseConfig`；`level` 引用注入 + 课程侧重复声明拒收）；
* 启动期冻结：`course_from_args`（挂 `args.course_path` + `course_frozen_bytes`）；
* 派生路径：`resolve_state_init_bank`（快照银行三种基准的存在性判断）；
* 跨课门校验：`_resolve_courses`（被类面 `GatesSpec` 校验期**函数内**延迟调用）。

**为什么单独成家**：解析面（**去哪找、怎么读**）与类面（**长什么样**）、文件面（rl-config.json）
分离——换课程目录布局/加载规则只动这一个文件；`biz/config.py` 保留 `X as X` 门面。
静态依赖方向只有一条：本模块 → 类面（`CourseConfig`）；**不** import `biz.config`（无环）。

依赖面 = stdlib（`pathlib`）+ `biz.course_spec` + 函数内 `common.jsonc`（读 JSONC 实现）。
"""

from __future__ import annotations

from pathlib import Path

from biz.course_spec import CourseConfig

#: 课程配置目录（nn-training/curricula/*.jsonc）
CURRICULA_DIR = Path(__file__).resolve().parent.parent / "curricula"

#: 关卡配置目录（nn-training/levels/*.jsonc）——地图/敌人队列/命/星等**环境语义**，
#: 课程以 `"level": "<name>"` 引用（DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25）。
LEVELS_DIR = Path(__file__).resolve().parent.parent / "levels"


def _resolve_courses(names: list[str], where: str) -> None:
    """§3.4-5：跨课门引用的 course 必须找得到文件（复用 resolve_course）。"""
    for nm in names:
        try:
            resolve_course(nm)
        except FileNotFoundError as e:
            raise ValueError(f"{where}: 引用课程 '{nm}' 找不到（{e}）") from e


def resolve_state_init_bank(p: str) -> Path | None:
    """课程里的银行路径 → 盘上的文件（cwd → 仓库根 → nn-training 三种基准都认）；None = 找不到。

    为什么要三种：课程里的数据路径历来混用两种基准（`bc: nn-training/weights/…` 是仓库相对，
    `out: tmp/…` 是 nn-training 相对），而训练进程的 cwd 取决于谁拉起来的（控制台 / notebook /
    裸命令）。这里只做**存在性**判断，不猜「哪个文件对」。

    ⚠ 只保证「它在盘上」：银行**内容**（每局 ticks 是否容得下 `[cut_from, cut_to]` 这个区间）
    要读 manifest 才知道，留给 P0/P3 的读方校验（"切点越界"在那一侧才有判据）。
    """
    raw = str(p or "").strip()
    if not raw:
        return None
    nn_root = CURRICULA_DIR.parent
    for cand in (Path(raw), nn_root.parent / raw, nn_root / raw):
        try:
            if cand.is_file():
                return cand
        except OSError:  # 非法路径（空串/超长/NUL）——等同「不在盘上」
            continue
    return None


def resolve_level(name_or_path: str) -> Path:
    """`level: arena6` → `levels/arena6.jsonc`；路径存在则原样。"""
    p = Path(name_or_path)
    if p.exists():
        return p
    cand = LEVELS_DIR / f"{name_or_path}.jsonc"
    if not cand.exists():
        raise FileNotFoundError(
            f"关卡 '{name_or_path}' 不存在（查找 {cand}）；可用："
            f"{[f.stem for f in sorted(LEVELS_DIR.glob('*.jsonc'))]}"
        )
    return cand


#: level 引用持有后、课程侧禁止重复声明的环境键（关卡文件 = 环境语义唯一来源）
_LEVEL_ENV_KEYS = ("stages", "difficulty", "max_ticks", "player")


def load_course(path: str | Path) -> CourseConfig:
    """读 JSONC 课程配置 → `CourseConfig`（pydantic 校验，非法即 raise）。

    `"level": "<name|path>"` 引用关卡文件（levels/*.jsonc）：stages/difficulty/
    max_ticks/player 由关卡文件注入；课程侧显式声明其中任一键 = 配置冲突 raise
    （关卡 = 环境语义唯一来源，DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25）。
    """
    from common.jsonc import load as _load_jsonc

    p = Path(path)
    if not p.exists():
        cand = CURRICULA_DIR / f"{path}.jsonc"
        if cand.exists():
            p = cand
        else:
            raise FileNotFoundError(f"课程配置不存在：{path}（亦未在 {CURRICULA_DIR} 下找到）")
    d = _load_jsonc(str(p))
    if d.get("level"):
        lvl = _load_jsonc(str(resolve_level(str(d["level"]))))
        for k in _LEVEL_ENV_KEYS:
            if k in d:
                raise ValueError(
                    f"课程 '{d.get('name', path)}' 引用 level='{d['level']}' 后不得再声明 "
                    f"`{k}`（环境语义归关卡文件唯一持有）"
                )
            if k in lvl:
                d[k] = lvl[k]
    return CourseConfig(**d)


def resolve_course(name_or_path: str) -> Path:
    """`--course s-dodge` → `curricula/s-dodge.jsonc`；`--course-file x.jsonc` → 原样。"""
    p = Path(name_or_path)
    if p.exists():
        return p
    cand = CURRICULA_DIR / f"{name_or_path}.jsonc"
    if not cand.exists():
        raise FileNotFoundError(
            f"课程 '{name_or_path}' 不存在（查找 {cand}）；可用："
            f"{[f.stem for f in sorted(CURRICULA_DIR.glob('*.jsonc'))]}"
        )
    return cand


def course_from_args(args) -> CourseConfig | None:
    """argparse Namespace → CourseConfig（未传 --course/--course-file 时返回 None）。

    同时把解析出的课程文件路径挂到 `args.course_path`——远程模式发布 job 时
    需要课程 jsonc 全文快照 + course_fp（sha256 of 文件字节）进 manifest（D13/D14）。
    """
    name = str(getattr(args, "course", "") or "")
    path = str(getattr(args, "course_file", "") or "")
    if name and path:
        raise SystemExit("[run_rl] --course 与 --course-file 互斥，只能给一个")
    if not name and not path:
        return None
    p = resolve_course(path if path else name)
    args.course_path = str(p)
    # 启动期冻结课程文件字节：D13 全文快照 / course_fp / shard --course-fp 一律用
    # 冻结字节——mid-run 的热加载编辑（含被拒绝的语料身份改动）永不进云端 payload
    # （DECISIONS §2026-09-13-hot-reload · 全文 → docs/nn/training-stack.md §25「不要泄漏到云端」）。
    args.course_frozen_bytes = p.read_bytes()
    return load_course(p)
