"""remote/weights_standard.py —— 权重文件的**读侧自动落地**（2026-10-15，用户口径）。

## 为什么有它（事故原文）

离线段（`kind=run`，半/全离线）的逐轮回传走 R1：**int16 delta 容器**（`BWD1`，`remote/weights_delta`）。
hub 只做「原样接收 / 传递 / 分发」——它把容器逐字节落进

  * `<traj>/weights.json`（活动权重指针）
  * `<traj>/deliver/<run>/it-NNN/weights.json`（交付镜像）
  * `nn-training/weights/<课>/<课>.it<N>.<时间戳>.json`（归档）

而**本机消费者**（trainer 推给 sampler 节点、本机 PPO 装载、TS 评估 runner）只认整份
`nn-weights-json` ⇒ 每个 rollout 节点 `JSON.parse` 报
`Unexpected identifier "BWD1"`（用户 2026-10-15 报障），云机下一轮的 init 也成了容器。

**定案（用户 2026-10-15）**：hub 不变（它不需要知道权重是什么格式）；**消费者自己判断**——
读之前调本模块，是 delta 就转回整份再使用；**转一次一直复用**（转换幂等：文件一旦是整份，
后续调用只读 4 字节魔数）。**两个版本都保留**，并用**后缀**区分（用户 2026-10-15 追加口径）：
**整份 = `.json`**、**delta = `.delta`**，同目录同主名成对（`weights.json` + `weights.delta`）
—— 谁看名字就知道手里是哪一版，归档/面板扫描也不会把 delta 当可装载权重。

## 基准解析：**只认精确命中**

容器头的 `base_sha` 是**内容地址**（要重建就必须逐字节拿到那一份）。候选根（按序）：

  ⓪ `blob_root/<base_sha>`（内容寻址缓存 —— **键就是 sha**，O(1) 命中并当场校验）；
  ① 同目录的兄弟 `.json`；② traj 树（`<traj>/*.json`、`<traj>/it*/weights*.json`、
  `<traj>/remote-jobs/*/{blob.init,init_weights.json}`、`<traj>/remote-jobs/offline/*/it-*/weights.json`、
  `<traj>/deliver/*/it-*/weights.json`）；③ 归档 `nn-training/weights/<课>/*.json`；
  ④ 调用方给的 `extra_roots`。

候选**自己**是容器时先递归落地再比 sha —— 于是「上一轮也是 delta」的那条链能整条走通
（链根 = 整份那轮；每个环的 sha 都必须逐字节对上，对不上就抛）。找不到 ⇒ **响亮失败**
（`ProtocolError`，打出 `base_sha`、找过的根、以及两条修法），**绝不用「看起来像」的基准**：
落错基准会产出形状全对、数值全错的权重，那比拒绝危险得多。

## 两种口味（同一套核心）

  * `ensure_standard_weights(path)` —— **本机**：就地转换（+ delta 旁存）。本机没有「把 delta
    当地发出去」的腿（发往云机的 init/锚点、bundle 的 `init_weights.json` 都必须是整份）
    ⇒ 原地转一次，之后所有消费者（含 sampler 节点）看到的就是整份，零改读点。
  * `standard_weights_copy(path, dest_dir)` —— **云机**：store 里那份**必须**保持 delta（它就是
    deliver 腿要发的字节，也是 hub 侧 duplicate/conflict 判据的输入）⇒ 整份写到旁路缓存，
    调用方拿缓存路径去当 init（下一轮 delta 的基准随之变成**落地链**，两侧逐字节一致）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import remote.delta_files as delta_files
import remote.weights_delta as weights_delta
from common.errors import ProtocolError
from common.fs import atomic_write_bytes
from remote.artifacts import weights_archive_root

__all__ = [
    "DELTA_SUFFIX",
    "STANDARD_CACHE_DIR",
    "STANDARD_SUFFIX",
    "ensure_standard_weights",
    "is_delta_weights_file",
    "read_standard",
    "standard_weights_copy",
]

#: **两版的命名约定**（用户 2026-10-15 口径）：整份 = `.json`、delta = `.delta`，
#: 同目录同主名成对（`weights.json` + `weights.delta`）—— 谁看名字就知道手里是哪一版。
#: ⚠ 后缀只是名字：**内容才是权威**（`is_delta_weights_file` 按 4 字节魔数判）。
STANDARD_SUFFIX = ".json"
DELTA_SUFFIX = ".delta"
#: 云机侧「整份副本」的旁路缓存目录名（跟在产物根下；store 本体保持 delta）。
STANDARD_CACHE_DIR = ".weights-standard"
#: 链式回落深度上限（防环/防跑飞；一条段最多几百轮）。
_MAX_CHAIN = 512
#: 单次搜索最多哈希多少个候选（坏盘/巨型目录时不让读侧变成扫描器）。
_MAX_CANDIDATES = 4000

#: 一次**顶层**解析的唯一读字节预算（同一路径只读一次；不是 N² 的重复读）。
#: 现场 232 个候选 × ≤400 KB ≈ 93 MB，2 GiB 留出 20× 余量，同时远离 8.5 TB 的事故量级。
_MAX_TOTAL_READ_BYTES = 2 * 1024**3
#: 一次顶层解析最多读多少个**唯一**候选（坏盘/巨型目录的兜底）。
_MAX_TOTAL_CANDIDATES = 20000
#: 上下文缓存的内存上界（读预算之外的第二道闸；超过就退化成重复读，由预算兜底）。
_MAX_RAW_CACHE_BYTES = 256 * 1024**2
_MAX_LANDED_CACHE_BYTES = 512 * 1024**2


class WeightsStandardError(ProtocolError):
    """权重读侧落地的**确定性**失败：同一份盘 + 同一个空候选池，重试结果逐字相同。

    `round_failure` 按这个属性判「不重试」——不匹配中文文案（文案是第二事实源）。
    """

    retryable = False


class BaseNotFoundError(WeightsStandardError):
    """基准不在本机（候选池里逐字节找不到）。"""


class ChainTooLongError(WeightsStandardError):
    """基准链超过 `_MAX_CHAIN` 环。"""


class CycleDetectedError(WeightsStandardError):
    """基准链成环（同一路径又绕回自己）。"""


class SearchBudgetExceededError(WeightsStandardError):
    """一次顶层解析撞了读字节/候选数预算（不是「找不到」，是「不许再找」）。

    `kind` / `limit` / `used` 是**结构化**读面：运维要能在日志与用例里分辨「撞了哪条预算、
    累计多少」，而不用去匹配中文文案。
    """

    def __init__(self, message: str, *, kind: str = "", limit: int = 0, used: int = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.limit = limit
        self.used = used


@dataclass
class _SearchContext:
    """一次**顶层**解析的共享状态（v2，2026-10-21）。

    为什么必须是共享对象：v1 的 `_MAX_CHAIN`/`_MAX_CANDIDATES` 是**每层调用**各一份，
    232 个互为候选的 delta 会展开成阶乘；`seen` 只挡当前链。v2 在这里把三件事收成一份：
    `raw`（同一路径只读一次）、`failed`（`BaseNotFoundError` 按路径负缓存）、`landed`
    （成功落地按内容 sha 正缓存），并给读字节/候选数一个跨递归的总预算。
    """

    max_bytes: int = _MAX_TOTAL_READ_BYTES
    max_candidates: int = _MAX_TOTAL_CANDIDATES
    bytes_read: int = 0
    candidates_read: int = 0
    raw_bytes: int = 0
    landed_bytes: int = 0
    raw: dict[str, bytes] = field(default_factory=dict)
    landed: dict[str, bytes] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    def read(self, p: Path) -> bytes:
        """读一个候选/根；**同一路径只读一次**，并按唯一字节/候选数计预算。"""
        key = _path_key(p)
        hit = self.raw.get(key)
        if hit is not None:
            return hit
        size = p.stat().st_size
        if self.bytes_read + size > self.max_bytes:
            raise SearchBudgetExceededError(
                f"权重 delta 基准搜索撞**读字节预算**：上限 {self.max_bytes} B，"
                f"已读 {self.bytes_read} B，下一个候选 {p}（{size} B）"
                "——确定性失败，重试结果相同，直接退出",
                kind="bytes",
                limit=self.max_bytes,
                used=self.bytes_read,
            )
        if self.candidates_read >= self.max_candidates:
            raise SearchBudgetExceededError(
                f"权重 delta 基准搜索撞**候选数预算**：上限 {self.max_candidates} 个，"
                f"已读 {self.candidates_read} 个（下一个 {p}）"
                "——确定性失败，重试结果相同，直接退出",
                kind="candidates",
                limit=self.max_candidates,
                used=self.candidates_read,
            )
        raw = p.read_bytes()
        self.bytes_read += len(raw)
        self.candidates_read += 1
        if self.bytes_read > self.max_bytes:
            raise SearchBudgetExceededError(
                f"权重 delta 基准搜索撞**读字节预算**：上限 {self.max_bytes} B，"
                f"实际读到 {self.bytes_read} B（{p} 的 stat 偏小）——确定性失败，不重试",
                kind="bytes",
                limit=self.max_bytes,
                used=self.bytes_read,
            )
        if self.raw_bytes + len(raw) <= _MAX_RAW_CACHE_BYTES:
            self.raw[key] = raw
            self.raw_bytes += len(raw)
        return raw

    def remember(self, raw_sha: str, standard: bytes) -> bytes:
        """成功落地正缓存（内容寻址；内存有界）。"""
        if self.landed_bytes + len(standard) <= _MAX_LANDED_CACHE_BYTES:
            self.landed[raw_sha] = standard
            self.landed_bytes += len(standard)
        return standard


def _path_key(p: Path) -> str:
    """路径缓存键（`resolve` 失败时退回原串——坏盘/悬空链接不配把判据炸掉）。"""
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _archive_root() -> Path:
    """归档根（`nn-training/weights/`）——定义在 `remote/artifacts`（与回传落归档同一处）。"""
    return weights_archive_root()


#: `path` 是不是权重 delta 容器（**只读 4 字节魔数**；不存在/读不动/是整份 ⇒ False）。
#: 只看内容、不看后缀（名字是约定，内容才是权威）；实现是 `delta_files.container_file`
#: ——与 opt 那半**同一份**，且刻意用 `partial` 而不是再包一个 `def`：复用账本连「逐字节同体的
#: 孪生函数」都算重复组（`tools/reuse_audit.py`，纪律见 `tests/test_reuse_ledger.py`）。
is_delta_weights_file = partial(delta_files.container_file, magic=weights_delta.W_DELTA_MAGIC)


#: 同目录同主名的兄弟名（`weights.json` ↔ `weights.delta`）。
def sibling(p: str | Path, suffix: str) -> Path:
    return delta_files.sibling(p, suffix)


#: 每个候选根下的搜索面（顺序 = 命中概率从高到低；`*.json` 只是同目录那一圈）。
#: ⚠ `.weights-standard/*.json`（**云机侧旁路缓存**，`standard_weights_copy` 写的落地件）必须在内：
#: 链根会被清理策略删掉（`biz/course_archive` 对 offline 轮的 `weights.json` 判「转存后删」、
#: 换机只带缓存、段起点被 prune），而云机自己那份落地件**逐字节就是下一轮的 base** —— 扫不到它
#: 每轮都要把整条链重算一遍，链根一没就整条解不出来（`test_store_side_cache_is_a_candidate_root`）。
_SEARCH_PATTERNS = (
    "*.json",
    "*.delta",
    ".weights-standard/*.json",
    "it*/weights*.json",
    "it*/weights*.delta",
    "remote-jobs/*/blob.init",
    "remote-jobs/*/init_weights.json",
    "remote-jobs/offline/*/it-*/weights.json",
    "deliver/*/it-*/weights.json",
)


def _resolve_course(path: Path, course: str, traj: Path | None) -> str:
    """课程名（归档根要用）：调用方给的优先，其次 traj 目录名 / 归档父目录名。"""
    if course:
        return course
    if traj is not None:
        return traj.name
    if path.parent.parent.name == "weights":
        return path.parent.name
    return ""


def _candidates(
    path: Path,
    *,
    course: str,
    traj_root: str | Path | None,
    extra_roots: Iterable[str | Path],
) -> list[Path]:
    """候选基准文件（**不含 path 自己**；顺序 = 命中概率从高到低）。"""
    traj = Path(traj_root) if traj_root else delta_files.traj_of(path)
    course = _resolve_course(path, course, traj)
    roots = delta_files.search_roots(
        path,
        traj_root=traj,
        extra_roots=extra_roots,
        course_dir=(_archive_root() / course) if course else None,
    )
    return delta_files.candidate_files(path, roots, _SEARCH_PATTERNS)


def _cas_hit(blob_root: str | Path | None, base_sha: str) -> bytes | None:
    """内容寻址缓存里的基准（`blob_cache/<sha>` —— **键就是 sha**，读出来再校一遍才算数）。

    为什么单独给它一条快路：worker/云机手上的上一轮整份就在 `blob_cache` 里（`train_core`
    产物段把它写进去，键 = 它的 sha）—— delta 的 `base_sha` 指向的正是这份。命中的话
    是 O(1) 的 stat + 一次校验，而不是去扫目录。
    """
    if not blob_root or not base_sha:
        return None
    p = Path(blob_root) / base_sha
    if not p.is_file():
        return None
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    return raw if hashlib.sha256(raw).hexdigest() == base_sha else None


def _standard_bytes(
    path: Path,
    *,
    course: str,
    traj_root: str | Path | None,
    extra_roots: tuple[str | Path, ...],
    blob_root: str | Path | None = None,
    depth: int = 0,
    seen: set[str] | None = None,
    ctx: _SearchContext | None = None,
) -> bytes:
    """`path` 的**整份**字节（本身就是整份 ⇒ 原样返回；是容器 ⇒ 逐字节找基准后重建）。

    v2（2026-10-21）：所有递归共享一个 `_SearchContext`——同一路径只读一次、
    `BaseNotFoundError` 按路径负缓存、成功落地按内容 sha 正缓存，并受总读字节/候选数预算约束。
    预算撞顶抛 `SearchBudgetExceededError`（**终止性**失败），父层不得把它当「这个候选不行」吞掉。
    """
    ctx = _SearchContext() if ctx is None else ctx
    key = _path_key(path)
    cached_failure = ctx.failed.get(key)
    if cached_failure is not None:
        raise BaseNotFoundError(cached_failure)

    raw = ctx.read(path)
    if not weights_delta.is_container(raw):
        return raw
    seen = set() if seen is None else seen
    if key in seen:
        raise CycleDetectedError(f"权重 delta 基准成环：{path} 又绕回自己——拒收（数据坏了）")
    if depth >= _MAX_CHAIN:
        raise ChainTooLongError(
            f"权重 delta 基准链超过 {_MAX_CHAIN} 环（{path}）——拒收（同名轮次互相引用？）"
        )
    seen = {*seen, key}
    base_sha = weights_delta.base_sha_of(raw)
    if not base_sha:
        raise BaseNotFoundError(f"权重 delta 容器头读不出 base_sha（{path}）——拒收（容器坏了）")
    raw_sha = hashlib.sha256(raw).hexdigest()
    cached_landed = ctx.landed.get(raw_sha)
    if cached_landed is not None:
        return cached_landed

    def rebuilt(base_bytes: bytes) -> bytes:
        # 重建 = 权威字节（delta 是相对**这一份**基准确认过的增量）。
        return ctx.remember(
            raw_sha,
            weights_delta.apply_weights_delta(base_bytes, raw, expect_base_sha=base_sha),
        )

    cas = _cas_hit(blob_root, base_sha)
    if cas is not None:
        return rebuilt(cas)

    searched: list[str] = []
    misses = 0

    def note(cand: Path, reason: str) -> None:
        nonlocal misses
        misses += 1
        if len(searched) < 8:
            searched.append(f"{cand}（{reason[:160]}）")

    for n, cand in enumerate(_candidates(
        path, course=course, traj_root=traj_root, extra_roots=extra_roots
    )):
        if n >= _MAX_CANDIDATES:
            searched.append(f"…（候选超过 {_MAX_CANDIDATES} 个，已截断）")
            break
        cand_key = _path_key(cand)
        cached_failure = ctx.failed.get(cand_key)
        if cached_failure is not None:
            note(cand, f"已失败：{cached_failure}")
            continue
        try:
            base_bytes = ctx.read(cand)
        except OSError:
            continue
        if hashlib.sha256(base_bytes).hexdigest() == base_sha:
            return rebuilt(base_bytes)
        if not weights_delta.is_container(base_bytes):
            note(cand, "不是基准（整份但 sha 不符）")
            continue
        # 候选自己也是 delta：先落地再看 sha（链式往返；落地件才是下一轮的基准）。
        try:
            landed = _standard_bytes(
                cand,
                course=course,
                traj_root=traj_root,
                extra_roots=extra_roots,
                blob_root=blob_root,
                depth=depth + 1,
                seen=seen,
                ctx=ctx,
            )
        except SearchBudgetExceededError:
            # 预算是**终止性**的：父层不许把它当一次候选 miss 吞掉（v1 的 bug）。
            raise
        except BaseNotFoundError as e:
            # 基准缺失与路径无关（同一份容器/同一个空候选池）⇒ 负缓存，树从 N! 降到 N²。
            ctx.failed[cand_key] = str(e)[:400]
            note(cand, str(e))
            continue
        except ProtocolError as e:
            note(cand, str(e))
            continue
        if hashlib.sha256(landed).hexdigest() == base_sha:
            return rebuilt(landed)
        note(cand, "落地件 sha 不符")

    roots_line = "、".join(
        str(r)
        for r in (
            path.parent,
            Path(traj_root) if traj_root else delta_files.traj_of(path),
            _archive_root() / course if course else None,
            *extra_roots,
        )
        if r is not None
    )
    detail = "；".join(searched)
    raise BaseNotFoundError(
        f"权重 delta 的基准不在本机：容器 {path} 要 base_sha={base_sha[:16]}…，"
        f"在候选根里逐字节找不到（考虑 {misses} 个候选，唯一读 {ctx.candidates_read} 个 / "
        f"{ctx.bytes_read} B：{roots_line}）。"
        + (f" 明细：{detail}" if detail else "")
        + " 修法：① 让这份权重所在的**上一轮整份**回到候选根里（归档 / remote-jobs/*/blob.init / "
        "deliver 镜像都算）；② 或让源头重发**整份** nn-weights-json（本机不猜基准——"
        "落错基准会产出形状对、数值错的权重）。"
    )


def ensure_standard_weights(
    path: str | Path,
    *,
    course: str = "",
    traj_root: str | Path | None = None,
    extra_roots: Iterable[str | Path] = (),
    blob_root: str | Path | None = None,
    log=print,
) -> Path:
    """**本机读侧入口**：给出**整份版**（`.json`）的路径，必要时把 delta 落地。

    幂等 + 便宜：整份文件只读 4 字节就返回（热路径上等于「无额外 IO」）。三种输入（见模块头）：

      * 路径本身就是整份 ⇒ 原样返回（同一个路径，零成本）；
      * 路径是 `.json` 却**装着 delta 字节**（hub 原样落位的活动/归档/镜像，现场 it237 的形状）
        ⇒ 先把 delta 写一份兄弟 `.delta`（两版都留，名字自述），再把 `.json` **就地**改成整份
        —— 返回**同一个路径**，于是本机所有既有读点（推给 sampler 的字节、本机 PPO 装载、
        TS 评估、归档扫描）不必改一行就看到整份；
      * 路径本身是 `.delta`（delta 版的正身）⇒ 整份写到兄弟 `.json` 并返回**那个**路径，
        `.delta` 一个字节都不动。

    `blob_root`（可选）= 内容寻址缓存根（`blob_cache`）——基准可能只在那儿（见 `_cas_hit`）。
    """
    p = Path(path)
    if not is_delta_weights_file(p):
        return p
    raw = p.read_bytes()
    standard = _standard_bytes(
        p,
        course=course,
        traj_root=traj_root,
        extra_roots=tuple(extra_roots),
        blob_root=blob_root,
    )
    if p.suffix == DELTA_SUFFIX:
        target, aside = sibling(p, STANDARD_SUFFIX), p  # `.delta` 是正身：只写整份那份
    else:
        target, aside = p, sibling(p, DELTA_SUFFIX)
        try:
            # 配对一定要是**本轮这份** delta（可能是上一轮留下的旧 `.delta`）
            if not aside.exists() or aside.read_bytes() != raw:
                atomic_write_bytes(aside, raw)
        except OSError as e:  # 旁存失败不挡主路（明细仍在 hub 树/归档里）
            log(f"[weights] delta 旁存失败（忽略）：{type(e).__name__}: {e}")
    atomic_write_bytes(target, standard)
    log(
        f"[weights] 读侧落地：{target.name} ← {len(standard)}B 整份"
        f"（delta {len(raw)}B 在 {aside.name}，基准 sha 已逐字节对上）"
    )
    return target


def read_standard(path: str | Path) -> bytes:
    """读侧入口的**字节版**：先把 delta 落地成整份，再返回它的字节（幂等；整份只读 4 字节）。

    谁用：`trainer/batch_runner` 的 nn 腿 —— 那里既要「推给节点的字节」又要「本机指纹」，
    两件都必须在**落地之后**做；原先 `with open(...) as f: weights_bytes = f.read()` 两步读盘，
    换成这一行后**行数不变**（batch_runner 的 LOC 预算卡在 999/1000，见
    `tests/test_python_loc_budget.py::test_no_source_file_exceeds_loc_budget`）。
    """
    return Path(ensure_standard_weights(path)).read_bytes()


def standard_weights_copy(
    path: str | Path,
    dest_dir: str | Path,
    *,
    course: str = "",
    traj_root: str | Path | None = None,
    extra_roots: Iterable[str | Path] = (),
    blob_root: str | Path | None = None,
    log=print,
) -> Path:
    """**云机侧入口**：把 `path` 的整份副本写到 `dest_dir/.weights-standard/<sha16>.json`。

    后缀按约定：缓存件是**整份** ⇒ `.json`；产物本体（delta 版）名字不动（还是 `.json` 或
    `.delta`，看它本来就叫什么）。

    为什么不就地转：云机 store 里那份**就是 deliver 腿要发的字节**（也是 hub 侧
    duplicate/conflict 判据的输入）⇒ 必须保持 delta。整份只给「本地装载/下一轮 init」用。
    缓存键 = delta 的 sha16（delta→整份 是纯函数：基准又要求逐字节对上）⇒ 同一份只转一次。
    """
    p = Path(path)
    if not is_delta_weights_file(p):
        return p
    raw = p.read_bytes()
    cache_dir = Path(dest_dir) / STANDARD_CACHE_DIR
    key = hashlib.sha256(raw).hexdigest()[:16]
    cached = cache_dir / f"{key}.json"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached
    standard = _standard_bytes(
        p,
        course=course,
        traj_root=traj_root,
        extra_roots=tuple(extra_roots),
        blob_root=blob_root,
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(cached, standard)
    return cached
