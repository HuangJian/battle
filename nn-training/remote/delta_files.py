"""remote/delta_files.py —— 两份**读侧落地器**（`remote.weights_standard` / `remote.opt_standard`）的共用零件。

## 为什么有它（不是「对称好看」）

两版文件的落地是同一条口径（用户 2026-10-15）：**内容才是权威**（4 字节魔数判形）、
**同目录同主名成对**（`.json`/`.delta`、`.tar`/`.delta`）、基准**只认逐字节精确命中**。抽出来
之前，`weights_standard` 与 `opt_standard` 各留了一份逐字节相同的 `_head4` / `_glob` /
`_traj_of` / 候选扫描闭包 `add` —— 复用账本（`tools/reuse_audit.py`）当场多出 4 个重复组
（14 → 18），而它的纪律是「抽到共用模块，别停在再抄一份」（`tests/test_reuse_ledger.py`）。

## 住 `remote/` 的哪一层

顶层只 import stdlib（`collections.abc` + `pathlib`）⇒ **L0 叶子**：两个 L1 的落地器都站在它上面，
边严格向下（层号账本 `tests/test_remote_dag.py`）。这里也**不许**长业务知识（`json` / `tarfile`
/ torch 一概不碰）：它只管「文件在哪、叫什么、头 4 字节是什么」。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "DELTA_MAGICS",
    "DELTA_SUFFIX",
    "OPT_DELTA_MAGIC",
    "WEIGHTS_DELTA_MAGIC",
    "any_container_file",
    "candidate_files",
    "container_file",
    "delta_name",
    "find_existing",
    "glob_sorted",
    "head4",
    "named_for",
    "search_roots",
    "sibling",
    "traj_of",
    "variants",
]

#: 「同一件东西两个形态」的后缀（整份 → 压缩版）：`weights.json` → `weights.delta`、
#: `opt.tar` → `opt.delta`。**内容说了算**（`named_for` 按魔数定名）。
DELTA_SUFFIX = ".delta"
#: 两个容器魔数的**字面量登记处**（2026-10-15）：`hub/*` 落位时要按形态给名字，而 hub
#: **不许** import `remote.weights_delta` / `remote.opt_delta`（那两个模块吃 numpy/torch，
#: 进 hub 的 import 闭包就等于 hub 起不来——`tests/remote/test_hub_import_graph.py` 钉死），
#: 所以魔数得有一份不下沉到它们的取处。与容器模块常量的逐字节一致由
#: `tests/remote/test_delta_files.py` 对账（对不上就红，不是“两份真相”）。
WEIGHTS_DELTA_MAGIC = b"BWD1"
OPT_DELTA_MAGIC = b"BOD1"
#: 任一魔数命中 ⇒ 这份字节是压缩版（weights/opt 共用一个判据；两份格式不会同名）。
DELTA_MAGICS = (WEIGHTS_DELTA_MAGIC, OPT_DELTA_MAGIC)


def delta_name(standard_name: str) -> str:
    """整份名 → 压缩版名（`weights.json` → `weights.delta`；`opt.tar` → `opt.delta`）。"""
    return Path(standard_name).with_suffix(DELTA_SUFFIX).name


def variants(standard_name: str) -> tuple[str, ...]:
    """同一件东西**两个形态的候选名**（整份名在前：它才是能直接用的那份）。"""
    return (standard_name, delta_name(standard_name))


def named_for(standard_name: str, raw: bytes) -> str:
    """按**内容**定名：`raw` 是压缩版（任一容器魔数）⇒ 压缩版名，否则整份名。

    这是落位端（hub 的回传树/镜像/归档）用的唯一口径：“名字跟着内容走”——一个装着
    压缩版的 `weights.json` 会让人（与控制台扫描面）把它当可装载权重。后缀只是名字，
    内容仍然才是权威（读侧两种名字都认，见 `variants`/`find_existing`）。
    """
    return delta_name(standard_name) if raw[:4] in DELTA_MAGICS else standard_name


def find_existing(root: str | Path, standard_name: str) -> Path | None:
    """`root` 下这份东西**存在的那一份**（整份名优先），两个名字都不在 ⇒ `None`。"""
    r = Path(root)
    for name in variants(standard_name):
        p = r / name
        if p.is_file():
            return p
    return None


def head4(path: str | Path) -> bytes:
    """文件的前 4 字节（不存在/读不动 ⇒ 空串）。**只读 4 字节** —— 热路径上的形态判据。"""
    try:
        with open(path, "rb") as f:
            return f.read(4)
    except OSError:
        return b""


def container_file(path: str | Path, magic: bytes) -> bool:
    """`path` 是不是**这个** delta 容器（头 4 字节 == `magic`）。

    只看内容、不看后缀：名字是约定，内容才是权威 —— 一个叫 `weights.json` / `opt.tar` 而身子是
    容器的文件（hub 原样落位的那些）必须被认出来，否则它会被当整份权重/整份 tar 用。
    """
    p = Path(path)
    if not p.is_file():
        return False
    return head4(p) == magic


def any_container_file(path: str | Path) -> bool:
    """`path` 是**任一族的** delta 容器（头 4 字节命中 `DELTA_MAGICS`；不存在/读不动 ⇒ False）。

    与 `container_file(path, magic)` 的分工：那个回答「是不是**这一族**」（本体解包前必须问清）；
    这一个只回答「是不是压缩版」（「要不要落地 / 要不要当锚点交出去」那类判据）。
    2026-10-15 收成一份：`hub/deltas` · `hub/store_offline` · `remote/run_loop` 曾各写一遍同一行
    `head4(p) in DELTA_MAGICS`（复用账本把其中两份逐字节相同的函数当场标成一组重复）。
    """
    return head4(path) in DELTA_MAGICS


def glob_sorted(root: str | Path, pattern: str) -> list[Path]:
    """`root` 下按名排序的 glob（坏盘/不是目录 ⇒ 空列表，绝不抛）。"""
    try:
        return sorted(Path(root).glob(pattern))
    except OSError:
        return []


def traj_of(path: str | Path) -> Path | None:
    """`<traj>` = 本文件的祖先里第一个「有 `remote-jobs/` 或 `training_log.jsonl`」的目录。

    为什么靠这两个锚：`tmp/<课>/` 是 traj 的**契约形状**（账本 + remote-jobs 是 hub 认课的两个
    判据，见 `hub/queue_discover`）——比「上溯到 tmp/」稳（`tmp/dev-x/...` 这类深路径也能认出来），
    也不用调用方把 traj 传进来。
    """
    for p in Path(path).resolve().parents:
        if (p / "remote-jobs").is_dir() or (p / "training_log.jsonl").is_file():
            return p
    return None


def sibling(p: str | Path, suffix: str) -> Path:
    """同目录同主名的兄弟名（`weights.json` ↔ `weights.delta`；`opt.tar` ↔ `opt.delta`）。"""
    return Path(p).with_suffix(suffix)


def search_roots(
    path: str | Path | None,
    *,
    traj_root: str | Path | None = None,
    extra_roots: Iterable[str | Path] = (),
    course_dir: str | Path | None = None,
) -> list[Path]:
    """候选根（顺序 = 命中概率从高到低）：同目录 → 兄弟轮目录 → traj 树 → [课归档] → 额外根。

    `path` 给 `None` 表示「手里只有字节、没有文件」（`land_opt_bytes` 那种入口）：那时只能靠
    `traj_root` 与 `extra_roots`，同目录那两格自然缺席。
    """
    roots: list[Path] = []
    if path is not None:
        p = Path(path)
        roots.append(p.parent)
        # 兄弟轮目录（产物形状：`<store>/it-237/weights.json`；本机 traj：`<traj>/it236/…`）。
        roots.append(p.parent.parent)
    traj = Path(traj_root) if traj_root else (traj_of(path) if path is not None else None)
    if traj is not None:
        roots.append(traj)
    if course_dir is not None:
        roots.append(Path(course_dir))
    roots.extend(Path(r) for r in extra_roots)
    return roots


def candidate_files(
    path: str | Path | None, roots: Iterable[str | Path], patterns: Iterable[str]
) -> list[Path]:
    """按 `patterns` 在各 `roots` 下扫候选文件（**不含 `path` 自己**，按内容去重，保序）。

    `root` 本身是文件 ⇒ 直接算一个候选（调用方拿单个文件当额外根时用得上）；是目录 ⇒ 逐 pattern。
    返回的是**路径**，不看内容：基准是否真的对上由调用方逐字节比 sha。
    """
    own = str(Path(path).resolve()) if path is not None else ""
    seen: set[str] = set()
    out: list[Path] = []

    def add(cand: Path) -> None:
        if not cand.is_file():
            return
        try:
            key = str(cand.resolve())
        except OSError:
            return
        if key == own or key in seen:
            return
        seen.add(key)
        out.append(cand)

    for root in roots:
        r = Path(root)
        if r.is_file():
            add(r)
            continue
        for pattern in patterns:
            for cand in glob_sorted(r, pattern):
                add(cand)
    return out
