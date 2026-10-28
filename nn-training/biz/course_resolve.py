"""biz/course_resolve —— **课程/关卡的查找与加载**（S5 第十刀，2026-09-27）。

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


#: level 引用持有后、课程侧**仍禁止**重复声明的环境键（关卡文件 = 环境语义唯一来源）。
#: `max_ticks` 自 2026-10-28 起允许课程覆盖（docs/nn/training-stack.md §49）——
#: 它没有「另一份事实」的风险：覆盖值直接落到 `CourseConfig.max_ticks`，读方只看那一个。
_LEVEL_ENV_KEYS = ("stages", "difficulty", "player")

#: 只对 `"level": ...` 课程生效的**环境覆盖键**（§49）。四个都在 `_apply_level` 里**被消费**：
#: 挑关 → 写回 `stages`；敌数 → 写回每个 stage 的 `count`；命/星 → 写回 `player`。
#: 消费后从 dict 里 pop 掉 ⇒ 解析出的 `CourseConfig` 里没有第二份事实。
_LEVEL_OVERRIDE_KEYS = ("stage_select", "enemies", "lives", "star")


def _parse_stage_select(spec: str, n_stages: int, name: str) -> list[int]:
    """`"23"` / `"23,9,18,10"` / `"2-4"` → **0-based** 索引列表（输入是 1-based 位置，顺序保留）。

    为什么用位置不用 id：关卡文件里的关是**按顺序**决定 id 的（`CUSTOM_STAGE_BASE+i`），
    位置才是稳定的那个量；写 id 会在挑了子集之后自相矛盾（子集的第一关 id 又是 2000）。
    """
    raw = str(spec).strip()
    if not raw:
        return list(range(n_stages))
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                lo, hi = int(a), int(b)
            except ValueError as e:
                raise ValueError(f"课程 '{name}' 的 stage_select 段 {part!r} 不是区间（要 a-b）") from e
            if lo > hi:
                raise ValueError(f"课程 '{name}' 的 stage_select 段 {part!r} 区间倒序")
            seq = list(range(lo, hi + 1))
        else:
            try:
                seq = [int(part)]
            except ValueError as e:
                raise ValueError(f"课程 '{name}' 的 stage_select 段 {part!r} 不是整数") from e
        for k in seq:
            if not (1 <= k <= n_stages):
                raise ValueError(
                    f"课程 '{name}' 的 stage_select 越界：{k}（关卡只有 1..{n_stages} 关）"
                )
            out.append(k - 1)
    if not out:
        raise ValueError(f"课程 '{name}' 的 stage_select={spec!r} 解析出 0 关")
    return out


def _coerce_override_int(key: str, v: object, *, lo: int, name: str) -> int:
    """覆盖键 → int（脏值响亮拒；bool 不算整数）。

    为什么不能靠 pydantic 的 `Field(ge=...)`：这些键在 `CourseConfig` 构造**之前**就被
    `_apply_level` 消费掉，模型校验永远看不到它们——写 `lives: 0` 会静默变成 0 命。
    """
    iv: int | None = None
    if isinstance(v, bool):
        iv = None
    elif isinstance(v, int):
        iv = v
    elif isinstance(v, float) and v.is_integer():
        iv = int(v)
    elif isinstance(v, str) and v.strip().lstrip("-").isdigit():
        iv = int(v.strip())
    if iv is None:
        raise ValueError(f"课程 '{name}' 的 {key}={v!r} 必须是整数")
    if iv < lo:
        raise ValueError(f"课程 '{name}' 的 {key}={iv} 必须 ≥ {lo}")
    return iv


def _apply_level(d: dict, lvl: dict, name: str) -> None:
    """关卡注入 + 四类环境覆盖（§49）。原地改 `d`，覆盖键消费后 pop。"""
    for k in _LEVEL_ENV_KEYS:
        if k in d:
            raise ValueError(
                f"课程 '{name}' 引用 level='{d['level']}' 后不得再声明 "
                f"`{k}`（环境语义归关卡文件唯一持有）；"
                f"要挑关/改敌数/命/星请用 {list(_LEVEL_OVERRIDE_KEYS)}"
            )
        if k in lvl:
            d[k] = lvl[k]
    # max_ticks：课程显式给了就用课程的（它不是「另一份事实」，覆盖值就是最终值）
    if "max_ticks" not in d and "max_ticks" in lvl:
        d["max_ticks"] = lvl["max_ticks"]

    # `stages` 只在**真要用**它的时候才是必需（旧行为：没有 stages 的关卡文件只注入它有的键）
    injected = d.get("stages")

    sel = str(d.pop("stage_select", "") or "").strip()
    if sel:
        if not isinstance(injected, list) or not injected:
            raise ValueError(
                f"课程 '{name}' 用了 stage_select，但 level='{d.get('level')}' 的 stages 不是内联关列表"
                f"（收到 {injected!r}）——stage_select 只挑 list 型 stages 里的关，"
                "范围串型关卡直接写 stages 范围即可"
            )
        d["stages"] = [injected[i] for i in _parse_stage_select(sel, len(injected), name)]

    n_enemies = d.pop("enemies", None)
    if n_enemies is not None:
        n_enemies = _coerce_override_int("enemies", n_enemies, lo=1, name=name)
        stages_now = d.get("stages")
        if not isinstance(stages_now, list) or not stages_now:
            raise ValueError(
                f"课程 '{name}' 用了 enemies，但 level='{d.get('level')}' 的 stages 不是内联关列表"
                f"（收到 {stages_now!r}）"
            )
        for st in stages_now:
            forces = st.get("forces") or ""
            if not forces:
                raise ValueError(
                    f"课程 '{name}' 设了 enemies={n_enemies}，但关卡关 {st.get('name')!r} 没有 forces 串"
                    "（自定义 grid 关不吃敌数覆盖）"
                )
            if n_enemies > len(forces):
                raise ValueError(
                    f"课程 '{name}' 的 enemies={n_enemies} 超过关 {st.get('name')!r} 的兵力 "
                    f"{len(forces)}"
                )
            st["count"] = n_enemies

    lives = d.pop("lives", None)
    star = d.pop("star", None)
    if lives is not None or star is not None:
        player = dict(d.get("player") or {})
        if lives is not None:
            player["lives"] = _coerce_override_int("lives", lives, lo=1, name=name)
        if star is not None:
            player["level"] = _coerce_override_int("star", star, lo=0, name=name)
        d["player"] = player


def apply_level_file(d: dict, name: str) -> None:
    """课程 dict 的关卡注入 + 四类环境覆盖（§49；**RL 与 BC 两条加载链的同一份规则**）。

    有 `level` ⇒ 读关卡文件、注入环境键、消费四个覆盖键（原地改 `d`）；
    无 `level` ⇒ 覆盖键没有消费方（它们是「从关卡取环境」的修饰语，不是独立事实）⇒ 响亮拒。
    """
    from common.jsonc import load as _load_jsonc

    if d.get("level"):
        lvl = _load_jsonc(str(resolve_level(str(d["level"]))))
        _apply_level(d, lvl, name)
        return
    stale = [k for k in _LEVEL_OVERRIDE_KEYS if k in d]
    if stale:
        raise ValueError(
            f"课程 '{name}' 没有 `level`，却声明了 {stale} —— 这些覆盖只对引用关卡文件的课程生效"
            "（自建 `stages` 的课程直接写自己的 stages/player 即可）"
        )


def load_course(path: str | Path) -> CourseConfig:
    """读 JSONC 课程配置 → `CourseConfig`（pydantic 校验，非法即 raise）。

    `"level": "<name|path>"` 引用关卡文件（levels/*.jsonc）：stages/difficulty/player
    由关卡文件注入；课程侧显式声明其中任一键 = 配置冲突 raise
    （关卡 = 环境语义唯一来源，DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25）。

    四类**环境覆盖**（§49，2026-10-28）允许出现在课程里，由 `apply_level_file` 消费：
    `stage_select`（1-based 挑关）/ `enemies`（敌数）/ `lives` / `star`，外加 `max_ticks`。
    ⇒ 换关集/敌数/命/星**不必再写一份关卡 jsonc**。
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
    apply_level_file(d, str(d.get("name", path)))
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
