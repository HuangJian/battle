"""common/scratch.py — 节点本地盘的「scratch 工作区」：落点解析 / 布局 / 原子安装 / 有界搬回。

**为什么有它**（`plan/rollout-local-scratch.plan.md` 的现场，2026-10-08）：云机 rollout 与 eval
两条腿的 85/94 个 bun 进程，原来把 TS 运行时树（**读**）、每局 shard（**写**）、初始权重（**读**）
全放在 `/kaggle/working`（持久化网络挂载点）上。本机同样 85 并发从不生病——因为它写的是本地盘
⇒「85 并发」不是病，「85 并发 × 网络挂载点」才是。本模块只把这几样换到节点本地盘，
**协议一个字节不改**（`--out`/`--weights` 在协议层仍是 job 相对路径，只在节点侧换根绝化）。

四件事：

* `resolve_scratch_root()` —— 候选链 `NN_ROLLOUT_SCRATCH`（env，显式） > `/dev/shm` > `/tmp`
  > **`None`**（= 调用方**逐字**走今天的行为：不搬、不 drain、不换根 —— 评审 F9）。
  判据两条：**容量**（free ≥ need × 比值；`need` 本身不含比值 —— 只乘一次，评审 F8②）与
  **速度**（顺序写 + fsync 粗测；不比 job 目录显著快就不用手 —— 评审 F6：Kaggle 的 `/tmp`
  是否真是本地介质在仓内无取证，宁可回退也不制造「零收益 + 多两条失败模式」）。
  env 给了但不可写 ⇒ **响亮失败**（不静默回落：操作员以为在测 scratch 是最坏的形状）。
* `ScratchLayout` —— `<根>/nn-rollout-<作业名>-<hash8(作业根)>/`，**按作业标识命名**（评审 F5：
  同机多会话不互删），内部 `r/`（rollout 的 job 相对镜像）与 `eval/`（云机评估的 game 目录）
  互不相干、各清各的。TS 家**不在**作业目录里：它按内容寻址（`ts_home()`），两条腿/跨轮共用一份。
* `install_tree` / `install_file` —— **临时目录 + `os.replace`** 的原子安装（评审 R9：两腿并发拷
  同一个 sha 不会看见半截树）。
* `InstallGate` / `drain_tree` —— 搬回：copytree → 逐文件对账 → **闸内改名**。暂存目录放在
  目标 job 目录**之外**（评审 F1：`scan_shard_dirs` 是 `job_dir.rglob`，半截拷贝住在 job 目录里
  会被当成产出，而 `data_fp` 只读 manifest 三项字段、拦不住）；改名前的票据复核与 `os.replace`
  **在同一把锁下**（评审 F4：`call_bounded` 只弃线程不杀，被放弃的 copier 不许把旧数据装进去）；
  确定性失败（无空间/只读/校验不过）一律 `ProtocolError` 响亮上抛（评审 F3：不许进「无限重投」）。

依赖：stdlib + `common.platform_utils.rmtree_best_effort` + `common.protocol.ProtocolError`
（叶子层；`worker/` 与 `remote/` 都能 import —— 这也是它**不住** `remote/job_fs.py` 的原因：
那里会造出 worker → remote 的边，`tests/remote/test_job_fs_split.py` 的 DAG 守卫钉着）。
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from common.platform_utils import rmtree_best_effort
from common.protocol import ProtocolError

#: 操作员显式指定落点（最高优先；给了就用，不再纠正速度/容量 —— 操作员说了算）。
#: ⚠ 给了但不可写 ⇒ **响亮失败**（见 `resolve_scratch_root`）：静默回落会让操作员
#: 以为在测 scratch、其实在跑原地（评审 F9）。
SCRATCH_ENV = "NN_ROLLOUT_SCRATCH"
#: 作业 scratch 目录前缀（人眼辨识用；清理只动**自己那一份**，名字里带作业 hash）。
SCRATCH_DIR_PREFIX = "nn-rollout-"
#: TS 运行时树的家前缀（**内容寻址**、跨轮/两腿共用、永不清理）。
SCRATCH_TS_PREFIX = "nn-rollout-ts-"
#: 容量余量比值（**只乘一次**：`need` 是原始估算，判据在 `resolve_scratch_root` 里乘）。
SCRATCH_MIN_FREE_RATIO = 1.5
#: 落点至少要比 job 目录快这么多倍才值得换（评审 F6；测不出速度 ⇒ 不设闸）。
SCRATCH_PROBE_MIN_SPEEDUP = 5.0
#: 速度探针的写入量（顺序写 + fsync）。每轮一次：健康挂载点上亚秒级，换「不把零收益当收益」。
SCRATCH_PROBE_BYTES = 8 * 1024 * 1024
#: 首轮缺省的单局产出估算（实测 obs.npy 2.0–3.3 MB，取 ~3× 当上界）。
#: ⚠ 评审 F8①：「上一轮实测 p90」在仓内**没有生产者**（轮报告只有墙钟）⇒ 先用缺省，实测生产者留 P1。
SCRATCH_EST_DEFAULT_BYTES = 8 * 1024 * 1024
#: eval 单局产出估算（`_eval_report.json` ≈ 3.4 KB，取 ~20×）。
SCRATCH_EST_EVAL_BYTES = 64 * 1024
#: 搬回的暂存目录后缀（在**job 目录之外**：`<job_dir>__drain__/`，见模块头 F1）。
DRAIN_STAGE_SUFFIX = "__drain__"
#: 确定性失败（响亮上抛、不进重投）的 errno：没空间 / 只读 / 没权限。
FATAL_DRAIN_ERRNOS = frozenset(
    e
    for e in (
        errno.ENOSPC,
        errno.EROFS,
        errno.EACCES,
        errno.EPERM,
        getattr(errno, "EDQUOT", 0),
    )
    if e
)
#: 必须镜像进 exec 根的**输入** flag（job 相对）。输出（`--out`）不镜像 —— 那是本轮要产的东西，
#: 把上一轮的半截产出镜像进 scratch 恰恰是 R1 要防的形状。
INPUT_FLAGS: tuple[str, ...] = ("--weights",)


@dataclass(frozen=True)
class ScratchLayout:
    """一份作业的 scratch 布局（`root` = 介质根，`job` = 本作业私有目录）。"""

    root: Path
    job: Path

    @property
    def exec_dir(self) -> Path:
        """rollout 的 job 相对镜像（`--out`/`--weights` 换根到这里）。"""
        return self.job / "r"

    @property
    def eval_dir(self) -> Path:
        """云机评估的 game 目录（与 rollout 的 `r/` 互不相干、各清各的）。"""
        return self.job / "eval"

    def ts_for(self, name: str) -> Path:
        """TS 树的家（内容寻址 ⇒ 与作业名无关 ⇒ 两条腿/跨轮命中同一个）。"""
        return ts_home(self.root, name)

    def exec_for(self, rel: str) -> Path:
        return self.exec_dir / rel

    def eval_weights_for(self, it: int) -> Path:
        """云机评估当轮的权重落点（逐字节拷贝 ⇒ 账本里的 `wver`/key16 不变）。"""
        return self.job / "w-eval" / f"it{int(it)}.json"


def _slug(name: str) -> str:
    """目录名安全化（只留字母数字与 `._-`，其余换 `-`；空 ⇒ `x`）。"""
    out = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(name or "")).strip(".-")
    return out[:48] or "x"


def _digest8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]


def scratch_job_dir(root: Path, job_root: Path) -> Path:
    """本作业的 scratch 目录：`<root>/<前缀><作业名>-<hash8(作业根绝对路径)>`（**确定性**）。

    为什么要 hash（评审 F5）：作业名可能重复（`run`）或含奇怪字符，而同机多会话（两个 notebook /
    两次 run）必须各自独立 —— 一次「按前缀清旧 scratch」就会把另一个**正在跑**的 job 的 scratch
    删掉（它下一局写入落空 / drain 找不到源 ⇒ 静默少一局或 `data_fp` 对不上而整轮拒收）。
    hash 取作业根的绝对路径（小写归一、反斜杠归正）⇒ 同作业跨轮/跨进程恒定、不同作业必不同。
    """
    key = str(Path(job_root).resolve()).replace("\\", "/").lower()
    return Path(root) / f"{SCRATCH_DIR_PREFIX}{_slug(Path(job_root).name)}-{_digest8(key)}"


def ts_home(root: Path, name: str) -> Path:
    """TS 运行时树在 scratch 里的家（`<根>/nn-rollout-ts-<sha>`；内容寻址 ⇒ 两腿共用）。"""
    return Path(root) / f"{SCRATCH_TS_PREFIX}{_slug(name or 'ts')}"


def layout_for(root: Path, job_root: Path) -> ScratchLayout:
    return ScratchLayout(root=Path(root), job=scratch_job_dir(Path(root), Path(job_root)))


def need_bytes_for(games: int, per_game: float = SCRATCH_EST_DEFAULT_BYTES) -> float:
    """本轮 scratch 需要多少字节（`games × 单局估算`；**不含**余量比值 —— 评审 F8②）。"""
    return float(max(0, int(games))) * float(per_game)


def free_bytes(path: Path) -> float | None:
    """`path` 所在文件系统的可用字节（读不到 ⇒ `None`，不抛）。"""
    try:
        return float(shutil.disk_usage(path).free)
    except OSError:
        return None


def probe_write_speed_mbps(root: Path, *, bytes_: int = SCRATCH_PROBE_BYTES) -> float | None:
    """顺序写 + fsync + 回读的粗测（MB/s）；任何失败 ⇒ `None`（不设闸，不抛）。"""
    probe = Path(root) / f".nn-scratch-probe-{os.getpid()}"
    chunk = b"\0" * (1 << 20)
    n = max(1, int(bytes_) // len(chunk))
    t0 = time.time()
    try:
        with open(probe, "wb") as f:
            for _ in range(n):
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        dt = max(1e-6, time.time() - t0)
        with open(probe, "rb") as f:
            while f.read(1 << 20):
                pass
    except OSError:
        return None
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
    return (n * len(chunk) / (1024 * 1024)) / dt


def _is_writable_dir(path: Path) -> bool:
    """能不能在它下面写文件（`mkdir -p` 后试写一个探针；失败 ⇒ False，不抛）。"""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        probe = Path(path) / f".nn-scratch-writable-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def resolve_scratch_root(
    job_root: Path,
    *,
    need_bytes: float,
    candidates: tuple[str, ...] | None = None,
    log=lambda msg: None,
    probe=None,
) -> Path | None:
    """挑一个节点本地盘落点；全不够/不够快 ⇒ `None`（调用方**逐字**走今天的行为）。

    `probe` 可注入（`root -> MB/s | None`），用例因此不必真写 8MB。
    """
    env = os.environ.get(SCRATCH_ENV, "").strip()
    if env:
        p = Path(env)
        if not _is_writable_dir(p):
            raise ProtocolError(
                f"{SCRATCH_ENV}={env} 不可写（mkdir/写探针失败）——显式指定的 scratch 落点"
                "必须可用（不静默回落：那是「以为在测 scratch、其实在跑原地」的最坏形状）"
            )
        return p
    cands = SCRATCH_CANDIDATES if candidates is None else tuple(candidates)
    if not cands:
        # 没有候选 ⇒ 立刻回落：**连 job 目录的基准探针都不跑**（runtime-opt §34.2 对回退档的
        # 承诺原话）。非 POSIX 的缺省链、测试里钉死回退档的通用用例都走这里——每轮白写
        # 8MB + fsync 买不到任何决策（2026-10-09；探针读数还随负载漂移，见根 conftest）。
        return None
    measure = probe or probe_write_speed_mbps
    base_speed = measure(Path(job_root))
    for name in cands:
        cand = Path(name)
        if not _is_writable_dir(cand):
            continue
        free = free_bytes(cand)
        if free is None or free < float(need_bytes) * SCRATCH_MIN_FREE_RATIO:
            log(
                f"scratch 候选 {cand} 容量不够：free={0.0 if free is None else free / 1e6:.0f}MB"
                f" < need×{SCRATCH_MIN_FREE_RATIO:g}="
                f"{float(need_bytes) * SCRATCH_MIN_FREE_RATIO / 1e6:.0f}MB"
            )
            continue
        speed = measure(cand)
        if base_speed and speed and speed < base_speed * SCRATCH_PROBE_MIN_SPEEDUP:
            log(
                f"scratch 候选 {cand} 不比 job 目录快（{speed:.0f} vs {base_speed:.0f} MB/s"
                f" < {SCRATCH_PROBE_MIN_SPEEDUP:g}×）——不用它（评审 F6：宁可原地跑，"
                "也不制造「零收益 + 多两条失败模式」）"
            )
            continue
        return cand
    return None


#: 候选链（顺序即优先级；`/dev/shm` 在 Docker 默认只有 64MB ⇒ 多数时候由容量判据弹掉）。
#: ⚠ 非 POSIX 上**为空**：Windows 的 `/tmp` 会落成当前盘根的 `\tmp`，那不是「节点本地盘」
#: 而是噪音（用例靠显式注入候选/env 才走 scratch ⇒ 回退档是默认被回归覆盖的那一档）。
SCRATCH_CANDIDATES: tuple[str, ...] = ("/dev/shm", "/tmp") if os.name == "posix" else ()


def open_scratch(
    job_root: Path,
    *,
    need_bytes: float,
    candidates: tuple[str, ...] | None = None,
    log=lambda msg: None,
    probe=None,
) -> ScratchLayout | None:
    """解析根 + 装配布局；`None` = 回退原地（调用方逐字走今天的行为）。"""
    root = resolve_scratch_root(
        Path(job_root), need_bytes=need_bytes, candidates=candidates, log=log, probe=probe
    )
    return None if root is None else layout_for(root, Path(job_root))


# ---------------------------------------------------------------- 原子安装 / 清理


def _remove_tree(path: Path) -> bool:
    """删目录/文件（尽力；沙箱删除守卫会打死线程 ⇒ 兜 BaseException）。True = 已删掉/本就不在。

    为什么两种都收：`shutil.rmtree` **删不了文件**（`NotADirectoryError`）——清理面（`r/`）
    里既有 `w{i}/` 目录，也可能有导出器顺手落的散文件。
    """
    try:
        p = Path(path)
        if p.is_dir():
            return rmtree_best_effort(p, ignore_errors=True)
        if p.exists():
            p.unlink(missing_ok=True)
        return True
    except BaseException:  # 含 SystemExit（沙箱删除守卫会打死调用线程）
        return False


def install_tree(src: Path, dst: Path, *, log=lambda msg: None) -> bool:
    """把目录装到 `dst`（临时候选 + `os.replace`；已存在 ⇒ 直接复用 ⇒ 内容寻址只付一次）。

    原子性（评审 R9）：先拷到 `<dst>.tmp-<pid>` 再改名 ⇒ 同机两条腿并发拷同一个 sha 时，
    读者**永远看不到半截树**（后到的那个发现 `dst` 已在就丢掉自己的临时目录）。
    失败 ⇒ `False`（调用方回落），不抛。
    """
    dst = Path(dst)
    if dst.is_dir():
        return True
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log(f"scratch 目录建不出来 {dst.parent}: {type(e).__name__}: {e}")
        return False
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}")
    _remove_tree(tmp)
    try:
        shutil.copytree(src, tmp, dirs_exist_ok=True)
    except OSError as e:
        _remove_tree(tmp)
        log(f"scratch 复制失败 {src} -> {dst}: {type(e).__name__}: {e}")
        return False
    if dst.exists():
        _remove_tree(tmp)
        return True
    try:
        os.replace(tmp, dst)
    except OSError as e:
        _remove_tree(tmp)
        if dst.is_dir():
            return True
        log(f"scratch 安装失败 {tmp} -> {dst}: {type(e).__name__}: {e}")
        return False
    return True


def install_file(src: Path, dst: Path, *, log=lambda msg: None) -> bool:
    """把单文件装到 `dst`（同样是临时文件 + `os.replace`）。失败/源缺 ⇒ `False`。"""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        return False
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log(f"scratch 目录建不出来 {dst.parent}: {type(e).__name__}: {e}")
        return False
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except OSError as e:
        _remove_tree(tmp)
        log(f"scratch 复制失败 {src} -> {dst}: {type(e).__name__}: {e}")
        return False
    return True


def mirror_inputs(
    job_dir: Path,
    exec_dir: Path,
    argvs: list[list[str]],
    *,
    flags: tuple[str, ...] = INPUT_FLAGS,
    log=lambda msg: None,
) -> bool:
    """把 argv 里**存在的输入**（job 相对路径）镜像进 exec 根；全成功 ⇒ True。

    装不过去 ⇒ 调用方回落（不半途而废：宁可不搬，也不要「权重一半在本地盘一半在网络盘」）。
    """
    for argv in argvs:
        for flag in flags:
            try:
                rel = argv[argv.index(flag) + 1]
            except (ValueError, IndexError):
                continue
            src = Path(job_dir) / rel
            dst = Path(exec_dir) / rel
            if src.is_dir():
                ok = install_tree(src, dst, log=log)
            elif src.is_file():
                ok = install_file(src, dst, log=log)
            else:
                continue  # 缺席的输入不镜像（今天它也不在盘上，失败形状与今天相同）
            if not ok:
                return False
    return True


def drop_drain_stage(job_dir: Path) -> int:
    """删掉搬回暂存根（轮末收尾；**只碰这个后缀**，不在 job 目录里面）。返回 1 = 删掉了。"""
    root = drain_stage_root(job_dir)
    if not root.is_dir():
        return 0
    _remove_tree(root)
    return 0 if root.exists() else 1


def clear_dir(path: Path, *, keep: tuple[str, ...] = ()) -> int:
    """清空一个目录（只删**里面**的子项，保留 `keep` 名单）；返回删除个数。

    ⚠ 只清**自己那一份**（评审 F5）：调用方传进来的永远是本作业的 scratch 目录，
    绝不做「按前缀扫一遍删旧的」——那会删到同机另一个会话在跑的 scratch。
    有界语义由调用方套（`call_bounded`）。
    """
    p = Path(path)
    if not p.is_dir():
        return 0
    n = 0
    try:
        children = sorted(p.iterdir())
    except OSError:
        return 0
    for child in children:
        if child.name in keep:
            continue
        if _remove_tree(child):
            n += 1
    return n


# ---------------------------------------------------------------- 搬回（drain）


def drain_stage_root(job_dir: Path) -> Path:
    """搬回的暂存根：**job 目录的隔壁**（评审 F1：`scan_shard_dirs` 只扫 job 目录内部）。

    ⚠ 调用方（`remote/job_fs.prune_job_dirs`）必须豁免这个后缀：它就在 `work_dir` 下，
    会被当成一个 job 目录，把 keep-2 窗口挤掉一个真 job。
    """
    jd = Path(job_dir)
    return jd.parent / f"{jd.name}{DRAIN_STAGE_SUFFIX}"


class InstallGate:
    """一局一张**安装票**：被放弃的 copier 不许把数据装进 `job_dir`（评审 F4）。

    `call_bounded` 只「放弃等待」——它是个 daemon 线程 + `Event.wait`，**不杀线程**；被放弃的
    copier 会在挂载点恢复后继续拷完并改名。若那时整轮已重投（或已进下一轮），`w{i}` 就会被一份
    **过期**的数据占位（内容自洽、`data_fp` 也匹配 stage/seed ⇒ 静默错数据）。

    修法：票据 + 一把锁。主线程超界时 `ticket.cancel()`（作废这一局的票），copier 在**同一把锁下**
    复核自己的票再 `os.replace` ⇒ 「判据」与「安装」原子地串在一起，没有「查完票、还没改名就被
    作废」的窗口。票据号单调递增 ⇒ 上一轮/上一次尝试的票永远作废。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gen: dict[str, int] = {}

    def ticket(self, name: str) -> InstallTicket:
        with self._lock:
            gen = self._gen.get(str(name), 0) + 1
            self._gen[str(name)] = gen
            return InstallTicket(self, str(name), gen)

    def _valid(self, name: str, gen: int) -> bool:
        with self._lock:
            return self._gen.get(name) == gen

    def _cancel(self, name: str) -> None:
        with self._lock:
            self._gen[name] = self._gen.get(name, 0) + 1

    def _install(self, name: str, gen: int, stage: Path, dst: Path) -> bool:
        with self._lock:
            if self._gen.get(name) != gen:
                return False
            if Path(dst).exists():
                _remove_tree(dst)
            os.replace(stage, dst)
            return True


class InstallTicket:
    """一局一次的安装票（见 `InstallGate`）。"""

    __slots__ = ("_gate", "_gen", "_name")

    def __init__(self, gate: InstallGate, name: str, gen: int) -> None:
        self._gate = gate
        self._name = name
        self._gen = gen

    def cancel(self) -> None:
        """作废这张票（被放弃的 copier 从此装不进去）。"""
        self._gate._cancel(self._name)

    @property
    def valid(self) -> bool:
        return self._gate._valid(self._name, self._gen)

    def install(self, stage: Path, dst: Path) -> bool:
        """锁内复核 + 安装；票已作废 ⇒ `False`（**没有安装**）。"""
        return self._gate._install(self._name, self._gen, Path(stage), Path(dst))


def _dir_stat(path: Path) -> tuple[int, int]:
    """(文件数, 总字节) —— 递归、不跟随符号链接。"""
    files = 0
    total = 0
    stack = [Path(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for ent in it:
                    if ent.is_dir(follow_symlinks=False):
                        stack.append(Path(ent.path))
                    elif ent.is_file(follow_symlinks=False):
                        files += 1
                        total += ent.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return files, total


def drain_tree(
    src: Path,
    dst: Path,
    *,
    ticket: InstallTicket | None = None,
    log=lambda msg: None,
) -> bool:
    """把一局的产出从本地盘搬到 `dst`：copytree → 对账 → **闸内原子改名**。

    返回 `True` = 已安装；`False` = 票已作废（**没安装**，暂存已清）。
    确定性失败抛 `ProtocolError`（校验不过 / 无空间 / 只读 —— 评审 F3：不进重投）；
    其余 `OSError` 原样上抛（调用方按瞬态处置）。跨设备安全：**绝不用 `os.rename`**
    （跨设备 `EXDEV`，而「半截搬过去」比「没搬」更坏）。
    """
    src, dst = Path(src), Path(dst)
    stage = drain_stage_root(dst) / dst.name
    _remove_tree(stage)
    try:
        shutil.copytree(src, stage)
        if _dir_stat(stage) != _dir_stat(src):
            raise ProtocolError(
                f"搬回校验不过（{src} -> {stage} 的文件集/字节数不一致）——不安装半截拷贝"
            )
    except ProtocolError:
        _remove_tree(stage)
        raise
    except OSError as e:
        _remove_tree(stage)
        if e.errno in FATAL_DRAIN_ERRNOS:
            raise ProtocolError(f"搬回失败（确定性：{e.strerror or e}）: {src} -> {dst}") from e
        raise
    try:
        if ticket is not None:
            installed = ticket.install(stage, dst)
        else:
            if dst.exists():
                _remove_tree(dst)
            os.replace(stage, dst)
            installed = True
    except OSError as e:
        _remove_tree(stage)
        if e.errno in FATAL_DRAIN_ERRNOS:
            raise ProtocolError(f"搬回安装失败（确定性：{e.strerror or e}）: {stage} -> {dst}") from e
        raise
    if not installed:
        _remove_tree(stage)
        log(f"搬回已放弃（安装票作废，不安装）：{src} -> {dst}")
        return False
    _remove_tree(src)
    return True
