"""remote/opt_standard.py —— opt 文件的**读侧自动落地**（2026-10-15，与 `remote.weights_standard` 同口径）。

## 为什么有它（与 weights 那次事故同一个成因，opt 这一半当时没修）

离线段（`kind=run`）逐轮回传的 opt 是 **f16 delta 容器**（`BOD1`，`remote/opt_delta`）。现场取证
（`tmp/y5-term/remote-jobs/offline/c2ad414cd2ced1dd/it-237/opt.tar`，300 945 B）：

* 头 4 字节 = `BOD1`，`base_sha=15bea9c1…`；把这份字节喂给消费者（`common.fs.extract_tar_bytes`，
  即 `job_fs.unpack_opt_tar` 的唯一实现）当场 `ReadError: invalid header`；
* 而**基准就在本机**：`tmp/y5-term/it236/ppo_ckpt_remote.tar` 与两个 job 目录的 `blob.opt`
  逐字节 == `base_sha`（脚本 `tmp/_opt-probe.py` 扫 3 407 个候选文件得到的 3 个命中）。

hub 侧**有**落地（`hub_client.verify_and_land` → `land_opt_delta`）—— 那是 **published 腿**的回传
受理；**离线段没有**（hub 只做原样收发）⇒ 那台机器自己的**下一轮**拿到手的 init 就是容器：
`unpack_opt_tar` 解不开，`torch.load(opt.pt)` 更是无从谈起，整轮死在「restore（model/opt 恢复）」；
而且 `manifest.opt_sha` 会指向**本机缓存里没有**的地址（`train_core` 只缓存了本机那份 f32 整份），
离线腿又没有下载通道 ⇒ 那一档更早死。

## 口径（与 weights 同一条，用户 2026-10-15）

读之前在本机落地成整份 tar；**两版都留、用后缀区分**：整份 = `opt.tar`、容器 = `opt.delta`
（同目录同主名成对，`opt` = 主名）；**转一次一直复用**（幂等，缓存键 = 容器 sha16）；
**基准只认精确命中**（`base_sha` 是内容地址 ⇒ 逐字节找到那份整份 tar 才算数），找不到就**响亮拒**
——拿错基准 = 动量悄悄走偏（形状全对、数值全错），比拒收危险得多。

## 两种口味

* `ensure_standard_opt(path)` —— **就地**（本机）：`opt.tar` 里装容器 ⇒ 容器旁存 `opt.delta`、
  `opt.tar` 就地改成整份（返回同一路径，既有读点零改动）；输入本就是 `.delta` ⇒ 整份写到兄弟
  `opt.tar` 并返回它。
* `standard_opt_copy(path, dest_dir)` —— **产物口径（云机/离线段）**：`<store>/it-N/opt.tar` 就是
  deliver 腿要发的字节（也是 hub 侧 duplicate/conflict 判据的输入）⇒ **绝不就地改**，整份写到
  `<dest_dir>/.opt-standard/<容器 sha16>.tar`，调用方拿它当本轮 init。

重建本身走 `opt_delta.rebuilt_opt_tar`（hub 落地与这里**同一份实现**，只有「base 从哪来」不同）。
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Callable, Iterable
from functools import partial
from pathlib import Path

import common.torch_pickle as torch_pickle
import remote.delta_files as delta_files
import remote.opt_delta as opt_delta
from common.errors import ProtocolError
from common.fs import atomic_write_bytes, extract_tar_bytes

__all__ = [
    "OPT_DELTA_SUFFIX",
    "OPT_STANDARD_CACHE_DIR",
    "OPT_STANDARD_SUFFIX",
    "ensure_standard_opt",
    "is_delta_opt_file",
    "land_opt_bytes",
    "landed_opt_sha",
    "rebuild_opt_tar_np",
    "seed_opt_blob",
    "standard_opt_copy",
]

#: **两版的命名约定**：整份 = `.tar`、delta = `.delta`，同主名成对（`opt.tar` + `opt.delta`）。
#: ⚠ 后缀只是名字：**内容才是权威**（`is_delta_opt_file` 按 4 字节魔数判）。
OPT_STANDARD_SUFFIX = ".tar"
OPT_DELTA_SUFFIX = ".delta"
#: 产物口径下「整份副本」的旁路缓存目录名（跟在产物根下；产物本体保持容器）。
OPT_STANDARD_CACHE_DIR = ".opt-standard"
#: 链式回落深度上限（防环/防跑飞；一条段最多几百轮）。
_MAX_CHAIN = 512
#: 单次搜索最多看多少个候选（坏盘/巨型目录时不让读侧变成扫描器）。
_MAX_CANDIDATES = 4000


#: `path` 是不是 opt delta 容器（**只读 4 字节魔数**；不存在/读不动/是整份 ⇒ False）。
#: 只看内容、不看后缀；实现与 `weights_standard.is_delta_weights_file` **共用一份**
#: （`delta_files.container_file`），且刻意用 `partial`：复用账本连「逐字节同体的孪生函数」
#: 都算重复组（`tools/reuse_audit.py`，纪律见 `tests/test_reuse_ledger.py`）。
is_delta_opt_file = partial(delta_files.container_file, magic=opt_delta.OPT_DELTA_MAGIC)


#: 每个候选根下的搜索面（顺序 = 命中概率从高到低；`*.tar` 只是同目录那一圈）。
#: ⚠ `.opt-standard/*.tar`（**云机侧旁路缓存**）与 weights 那半边同理：链根会被清理策略删掉
#: （`biz/course_archive` 对 offline 轮的 `opt.tar` 只留关键轮），而上一轮的落地件就在缓存里、
#: **逐字节就是本轮的 base**（`test_store_side_cache_is_a_candidate_root`）。
_SEARCH_PATTERNS = (
    "*.tar",
    "*.delta",
    ".opt-standard/*.tar",
    "it*/opt.tar",
    "it*/opt.delta",
    "it*/ppo_ckpt_remote.tar",
    "remote-jobs/offline/*/it-*/opt.tar",
    "remote-jobs/*/blob.opt",
    "deliver/*/it-*/opt.tar",
)


def _candidate_files(
    path: Path | None, extra_roots: Iterable[str | Path], traj_root: str | Path | None
) -> list[Path]:
    """候选文件（**不含 `path` 自己**；顺序 = 命中概率从高到低）——落在 `remote.delta_files`。"""
    roots = delta_files.search_roots(path, traj_root=traj_root, extra_roots=extra_roots)
    return delta_files.candidate_files(path, roots, _SEARCH_PATTERNS)


def _base_tar_bytes(
    container: bytes,
    *,
    path: Path | None,
    blob_roots: tuple[str | Path, ...],
    extra_roots: tuple[str | Path, ...],
    traj_root: str | Path | None = None,
    work_dir: str | Path | None = None,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
    log,
    rebuild: Callable[..., bytes] | None = None,
) -> bytes:
    """容器要的**基准整份 tar 字节**（`base_sha` 逐字节对上才算数；找不到 ⇒ `ProtocolError`）。

    顺序：① 内容寻址缓存 `blob_cache/<base_sha>`（键就是 sha，O(1) 命中并当场校验 —— 本机那份
    整份 tar 与上一轮的落地件都在那儿）；② 候选文件（同目录兄弟、产物链、traj 树、调用方给的
    额外根）；候选**自己**是容器时先递归落地再比 sha（一条段从第 2 轮起就只有 delta）。
    """
    base_sha = opt_delta.base_sha_of(container)
    if not base_sha:
        raise ProtocolError(f"opt delta 容器头读不出 base_sha（{path or '<bytes>'}）——拒收")
    if depth >= _MAX_CHAIN:
        raise ProtocolError(f"opt delta 基准链超过 {_MAX_CHAIN} 环（{path or '<bytes>'}）——拒收")
    # 防环（★ 2026-10-15 修：`seen`/`depth` 原先**从不传下去** ⇒ 这两个护栏都是死的，
    # 成环数据只会撞解释器的递归上限、抛出一个调用方 `except ProtocolError` 拦不住的
    # `RecursionError`）。判据与 weights 那半边逐字同口径。
    if path is not None:
        key = str(path.resolve())
        if key in seen:
            raise ProtocolError(f"opt delta 基准成环：{path} 又绕回自己——拒收（数据坏了）")
        seen = seen | {key}
    for root in blob_roots:
        p = Path(root) / base_sha
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(raw).hexdigest() == base_sha:
            log(f"[opt] 基准命中内容寻址缓存：{p}（{len(raw)}B）")
            return raw
    searched: list[str] = []
    key0 = str(path.resolve()) if path is not None else ""
    for n, cand in enumerate(_candidate_files(path, extra_roots, traj_root)):
        if n >= _MAX_CANDIDATES:
            searched.append(f"…（候选超过 {_MAX_CANDIDATES} 个，已截断）")
            break
        try:
            raw = cand.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(raw).hexdigest() == base_sha:
            return raw
        if not opt_delta.is_container(raw):
            searched.append(str(cand))
            continue
        try:
            # 递归：候选**自己**是容器 ⇒ 先把它落地（用**它自己的**目录当候选根：链上的基准
            # 就在上一轮那个目录里），再比 sha。
            landed = _landed_bytes_for(
                raw,
                cand,
                blob_roots=blob_roots,
                extra_roots=extra_roots,
                work_dir=work_dir,
                depth=depth + 1,
                seen=seen,
                log=log,
                rebuild=rebuild,
            )
        except ProtocolError as e:
            searched.append(f"{cand}（{e}）")
            continue
        if hashlib.sha256(landed).hexdigest() == base_sha:
            return landed
        searched.append(str(cand))
    roots_line = "、".join(
        str(r)
        for r in (
            path.parent if path is not None else None,
            traj_root,
            *extra_roots,
            *blob_roots,
        )
        if r is not None
    )
    raise ProtocolError(
        f"opt delta 的基准不在本机：容器 {path or key0 or '<bytes>'} 要 base_sha={base_sha[:16]}…，"
        f"在候选根里逐字节找不到（找了 {len(searched)} 个候选：{roots_line}）。"
        "修法：① 让这份 opt 所在的**上一轮整份**回到候选根里（产物链 it-N/ppo_ckpt_remote.tar、"
        "job 目录 blob.opt、content-addressed 缓存都算）；② 或让源头重发**整份** tar"
        "（本机不猜基准——落错基准会产出形状对、数值错的 Adam 动量）。"
    )


def _rebuild_opt_tar_torch(
    container: bytes, base_p: Path, *, out_dir: Path, log
) -> bytes:
    """`(容器, 基准 opt.pt 路径) → 整份 tar` —— **torch 版**（默认落地器）。"""
    import torch

    return opt_delta.rebuilt_opt_tar(
        container, torch.load(base_p, map_location="cpu"), out_dir=out_dir, log=log
    )


def rebuild_opt_tar_np(
    container: bytes, base_p: str | Path, *, out_dir: str | Path, log=print
) -> bytes:
    """`(容器, 基准 opt.pt 路径) → 整份 tar` —— **numpy 版**（Paddle worker 的读侧落地）。

    与 `_rebuild_opt_tar_torch` 同签名、同语义，只换两端的编解码：`opt.pt` 由
    `common.torch_pickle.load_torch_pickle` 读成 numpy 叶子，`apply_opt_delta_np` 加回 delta
    （消毒点在内），再用 `dump_torch_pickle` 写回 **torch 的 zip 格式** —— hub 机统一 torch
    格式，Paddle worker 自己转，这是那条口径的读侧一半。
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    base = torch_pickle.load_torch_pickle(Path(base_p).read_bytes())
    rebuilt = opt_delta.apply_opt_delta_np(base, container, log=log)
    (d / "opt.pt").write_bytes(torch_pickle.dump_torch_pickle(rebuilt))
    return opt_delta.pack_tar_from_dir(d, ["opt.pt"])


def _landed_bytes_for(
    container: bytes,
    path: Path | None,
    *,
    blob_roots: tuple[str | Path, ...],
    extra_roots: tuple[str | Path, ...],
    work_dir: str | Path | None,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
    log,
    rebuild: Callable[..., bytes] | None = None,
) -> bytes:
    """容器 ⇒ 整份 tar 字节（`path` 给定时用它所在目录当候选根；只读，不落盘）。

    `depth`/`seen` 是**链上防环**那两个计数器（`_base_tar_bytes` 的老问题：它们原先没被传下去，
    成环就是 RecursionError）—— 递归入口一律 `depth+1` + 把当前文件并进 `seen`。
    `rebuild` 是「基准 opt.pt 怎么读、delta 怎么加回」的接缝：默认 torch，Paddle worker 传
    `rebuild_opt_tar_np`（同一段链上递归也要用同一个，故它一路透传）。
    """
    rebuild = rebuild or _rebuild_opt_tar_torch
    scratch = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="opt-land-"))
    made = work_dir is None
    try:
        base_tar = _base_tar_bytes(
            container,
            path=path,
            blob_roots=blob_roots,
            extra_roots=extra_roots,
            work_dir=scratch,
            depth=depth,
            seen=seen,
            log=log,
            rebuild=rebuild,
        )
        base_dir = scratch / "base"
        base_dir.mkdir(parents=True, exist_ok=True)
        extract_tar_bytes(base_tar, base_dir)
        base_p = base_dir / "opt.pt"
        if not base_p.exists():
            raise ProtocolError(f"opt delta 基准 tar 里没有 opt.pt（{base_dir}）——拒收")
        landed = rebuild(container, base_p, out_dir=scratch / "out", log=log)
        log(
            f"[opt] 读侧落地：容器 {len(container)}B → 整份 {len(landed)}B"
            f"（基准 sha 已逐字节对上）"
        )
        return landed
    finally:
        if made:
            shutil.rmtree(scratch, ignore_errors=True)


def land_opt_bytes(
    raw: bytes,
    *,
    path: str | Path | None = None,
    blob_roots: Iterable[str | Path] = (),
    extra_roots: Iterable[str | Path] = (),
    traj_root: str | Path | None = None,
    work_dir: str | Path | None = None,
    log=print,
    rebuild: Callable[..., bytes] | None = None,
) -> bytes:
    """**字节级入口**：容器 ⇒ 整份 tar 字节；本来就是整份 ⇒ 原样返回（零成本）。

    给「手边只有字节、没有文件」的消费者用（`train_core` 的 restore 段：`opt_init` 内联 /
    blob 解析出来的那份都可能是容器）。`path` 可选：容器**所在文件**（知道就传，它所在目录
    会成为候选根之一——链上的基准往往就在隔壁那一轮里）。`rebuild` 见 `_landed_bytes_for`。
    """
    if not opt_delta.is_container(raw):
        return raw
    extra = (*extra_roots, *((traj_root,) if traj_root else ()))
    return _landed_bytes_for(
        raw,
        Path(path) if path is not None else None,
        blob_roots=tuple(blob_roots),
        extra_roots=extra,
        work_dir=work_dir,
        log=log,
        rebuild=rebuild,
    )


def landed_opt_sha(
    path: str | Path,
    dest_dir: str | Path,
    *,
    blob_roots: Iterable[str | Path] = (),
    extra_roots: Iterable[str | Path] = (),
    traj_root: str | Path | None = None,
    work_dir: str | Path | None = None,
    log=print,
) -> str:
    """**产物口径的一步到位**：`<store>/it-N/opt.tar` ⇒ 可**声明**的 opt 地址（`manifest.opt_sha`）。

    容器 ⇒ 落成整份（旁路缓存）+ 种子进内容寻址缓存 ⇒ 返回整份的 sha；已经是整份 ⇒ 原样种子
    后返回它的 sha。文件不在 ⇒ 空串（调用方按「本段没有动量」跑）。**声明什么就必须拿到什么**
    ——`train_core` 只认 `blob_cache/<sha>`，而离线段没有下载通道，所以「先种子再声明」是硬前提。
    """
    p = Path(path)
    if not p.is_file():
        return ""
    if is_delta_opt_file(p):
        p = standard_opt_copy(
            p,
            dest_dir,
            blob_roots=blob_roots,
            extra_roots=extra_roots,
            traj_root=traj_root,
            work_dir=work_dir,
            log=log,
        )
    return seed_opt_blob(p.read_bytes(), blob_roots, log=log)


def ensure_standard_opt(
    path: str | Path,
    *,
    blob_roots: Iterable[str | Path] = (),
    extra_roots: Iterable[str | Path] = (),
    traj_root: str | Path | None = None,
    work_dir: str | Path | None = None,
    log=print,
) -> Path:
    """**本机就地口味**：给出**整份版**（`.tar`）的路径，必要时把容器落地（幂等）。

    三种输入（与 `weights_standard.ensure_standard_weights` 逐条对齐）：

      * 路径本身就是整份 ⇒ 原样返回（同一个路径，零成本）；
      * 路径是 `.tar` 却**装着容器**（hub 原样落位的形状）⇒ 先把容器写一份兄弟 `.delta`
        （两版都留，名字自述），再把 `.tar` **就地**改成整份 —— 返回**同一个路径**，
        于是既有读点不必改一行就看到整份；
      * 路径本身是 `.delta`（delta 版的正身）⇒ 整份写到兄弟 `.tar` 并返回**那个**路径，
        `.delta` 一个字节都不动。
    """
    p = Path(path)
    if not is_delta_opt_file(p):
        return p
    raw = p.read_bytes()
    extra = (*extra_roots, *((traj_root,) if traj_root else ()))
    standard = _landed_bytes_for(
        raw,
        p,
        blob_roots=tuple(blob_roots),
        extra_roots=extra,
        work_dir=work_dir,
        log=log,
    )
    if p.suffix == OPT_DELTA_SUFFIX:
        target, aside = delta_files.sibling(p, OPT_STANDARD_SUFFIX), p  # `.delta` 是正身：只写整份那份
    else:
        target, aside = p, delta_files.sibling(p, OPT_DELTA_SUFFIX)
        try:
            # 配对一定要是**本轮这份**容器（可能是上一轮留下的旧 `.delta`）
            if not aside.exists() or aside.read_bytes() != raw:
                atomic_write_bytes(aside, raw)
        except OSError as e:  # 旁存失败不挡主路（明细仍在产物链/hub 树里）
            log(f"[opt] 容器旁存失败（忽略）：{type(e).__name__}: {e}")
    atomic_write_bytes(target, standard)
    log(f"[opt] 读侧落地：{target.name} ← {len(standard)}B 整份（容器 {len(raw)}B 在 {aside.name}）")
    return target


def standard_opt_copy(
    path: str | Path,
    dest_dir: str | Path,
    *,
    blob_roots: Iterable[str | Path] = (),
    extra_roots: Iterable[str | Path] = (),
    traj_root: str | Path | None = None,
    work_dir: str | Path | None = None,
    log=print,
) -> Path:
    """**产物口径（云机/离线段）**：整份副本写到 `<dest_dir>/.opt-standard/<容器 sha16>.tar`。

    为什么不就地转：`<store>/it-N/opt.tar` **就是 deliver 腿要发的字节**（也是 hub 侧
    duplicate/conflict 判据的输入）⇒ 必须保持容器。整份只给「本轮装载」与「下一轮的基准」用。
    缓存键 = 容器的 sha16（容器 → 整份是纯函数：基准又要求逐字节对上）⇒ 同一份只转一次。
    """
    p = Path(path)
    if not is_delta_opt_file(p):
        return p
    raw = p.read_bytes()
    cache_dir = Path(dest_dir) / OPT_STANDARD_CACHE_DIR
    key = hashlib.sha256(raw).hexdigest()[:16]
    cached = cache_dir / f"{key}{OPT_STANDARD_SUFFIX}"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached
    standard = _landed_bytes_for(
        raw,
        p,
        blob_roots=tuple(blob_roots),
        extra_roots=(*extra_roots, *((traj_root,) if traj_root else ())),
        work_dir=work_dir,
        log=log,
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(cached, standard)
    log(
        f"[opt] 产物侧整份副本：{p.name} → {cached}（{len(standard)}B；"
        f"产物本体保持容器 {len(raw)}B 供 deliver 腿）"
    )
    return cached


def seed_opt_blob(
    tar_bytes: bytes, blob_roots: Iterable[str | Path], *, log=print
) -> str:
    """整份 tar 种子进内容寻址缓存（`blob_cache/<sha>`，键 = 它自己的 sha256）⇒ 返回该 sha。

    为什么必须种子：本轮 `manifest.opt_sha` 声明的是**整份的地址**，而 `train_core` 只认
    `blob_cache/<sha>`（cache / preloaded / download 三源）——不种子就等于声明一个拿不到的地址
    （离线段没有下载通道 ⇒ 整段死在 PPO 之前）。种子过的那份在下一轮又是落地基准的 O(1) 快路
    （`_base_tar_bytes` 的第一顺位）。写不进任何根 ⇒ 记一行并返回**空串**（调用方按「本段没有
    动量」跑：D5 的响亮降级，绝不幽灵声明一个拿不到的 sha）。
    """
    sha = hashlib.sha256(tar_bytes).hexdigest()
    for root in blob_roots:
        try:
            p = Path(root) / sha
            if p.is_file():
                return sha
            Path(root).mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(p, tar_bytes)
            log(f"[opt] 整份种子进内容寻址缓存：{p}（{len(tar_bytes)}B）——本轮 opt_sha 可解析")
            return sha
        except OSError as e:
            log(f"[opt] 种子写缓存失败（换下一个根）：{type(e).__name__}: {e}")
    log("WARN: 整份 opt 种子没写成（没有可写的 blob 根）——本段按无动量跑（Adam 从头）")
    return ""
