"""remote/code_snapshot.py — 集群代码快照：会话级 `code.zip` 锚点。

**它解决什么**（2026-10-10 用户指令）：`code.zip` 原来是「每门课的训练进程首次 publish 时打一份」
（`trainer/loop_remote_job.py` per-instance 去重 + per-course job_root；`trainer/bc_loop.py` 更是
**每轮**重打）。于是「会话内改本机代码 → 控制台再开一门课」会让共享的 worker 池在同一个 hub 上
交替领到**两份不同代码**的 job（shard 行宽 / METRICS_VERSION / 奖励语义都可能不同）。
用户口径：**trainer/hub 启动时打一份，只要不重启，所有课程都用它**——锚点从「课」上移到「会话」。

## 谁在什么时候动它

```
启动路径（ensure）        hub/boot.py · trainer/run_rl_cluster.py · trainer/run_rl.py（含导出进程）
消费路径（read 只读）      trainer/loop_remote_job.py（PPO publish）· trainer/bc_loop.py（BC publish）
                          hub/queue_observe.py::shared_code_zip（GET /code 的取件口）
```

* `ensure_cluster_snapshot` 是**启动路径专用**：锚活着 ⇒ 复用；锚死了/指纹不符/元数据损坏 ⇒ 重打。
  抢锚用 `O_CREAT|O_EXCL` 锁串行化（`common/instance_lock` 的同款原语），抢输的一方**有界等待**
  后复用，超时仍无快照 ⇒ 自己打（宁可多打一次，也不让启动挂住）。
* `read_cluster_snapshot` **绝不打包、绝不删除**：消费侧只用它。读到锚已死 ⇒ 照旧回报
  `anchor_alive=False`，由调用方决定（publish 侧回落 per-course 打包 + WARN；控制台判据回落 mtime 口径）。
  **消费侧 `read` 永不换代**是刻意的：半死集群（hub 死、trainer 活）里开新课不该悄悄换代码。
* `anchor_alive` 两个口径：读面（`read_*`，含 hub `/code`、每轮 BC publish）只看 **pid 存活**
  （廉价——Windows 上命令行核验要起子进程，见 `CodeSnapshot.anchor_alive` 的注释）；
  `ensure_*` 的分岔口再加**命令行指纹**（pid 复用必须重打，误重打在内容寻址下≈免费）。

## 载体：为什么是**内容寻址**

```
<repo>/tmp/.code-snapshot/snapshot.json      # 元数据（最后原子写；指向下面那份 zip）
<repo>/tmp/.code-snapshot/code.<sha12>.zip   # 快照字节（≈1.4MB；文件名 = 内容 sha 前缀）
<repo>/tmp/.code-snapshot/.pack.lock         # 抢锚锁
```

消费者把 `(zip_path, sha256)` **缓存在自己的进程里**（`_code_zip_path` / `_code_sha256`）。若文件名
固定为 `code.zip`，「旧锚死亡 → 下一个启动者重打」就会 `os.replace` 掉**同一个路径**——活着的消费者
下一轮 publish 的 `manifest.code_sha256` 还是旧的、上传的字节却已经是新的：云端 `ensure_code`
逐候选按 manifest 的 sha 选件，必对不上，报错还指向「传输损坏」（`remote-transport.md:1943`）。
内容寻址把「文件身份 = 内容」钉死（旧副本保留 ⇒ 缓存永不失配），顺带消灭「半截 zip 被读到」
（先写临时文件，`snapshot.json` **最后**替换）。

> 注意分工：**内容寻址只用于「字节的身份」**，**不用来决定「是否重打」**。「改代码后开新课」
> 必须继续拿到旧代码——那正是本模块存在的理由。

## 分层与依赖

住 `remote/`（L3）：打包器 `pack_code_zip` 在 `remote/hub_client.py`（L1），而 `common/` 是 L0
（stdlib-only，不得上溯）⇒ 住 common 会造 `common → remote` 的上向边。`hub(L4)/trainer(L4) → remote(L3)`
是既有方向（先例：`hub/boot.py` import `remote.push_dispatch`）。

探活**不新造**：`common.pid_probe.pid_alive`（跨平台，Windows 走 TerminateProcess 语义的探测）与
`common.instance_lock.proc_cmdline`（POSIX `/proc`；Windows 先 `wmic`、被移除的新机回落 PowerShell）。
**读不到命令行 ⇒ 判为「不可核验」⇒ 重打**（fail-closed）：误重打在内容寻址下几乎免费
（`pack_code_zip` 固定时间戳 ⇒ 同源码同 sha ⇒ 什么都不写）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from common.hashing import sha256_file
from common.instance_lock import proc_cmdline, read_lock
from common.pid_probe import pid_alive
from remote.hub_client import pack_code_zip

__all__ = [
    "SNAPSHOT_DIR_NAME",
    "CodeSnapshot",
    "current_code_zip_path",
    "ensure_cluster_snapshot",
    "published_code_zip",
    "read_cluster_snapshot",
    "snapshot_dir",
]

#: 快照目录名（相对 `<repo>/tmp`）——点目录，且**不在**被打包的根（`nn-training/`）之下。
SNAPSHOT_DIR_NAME = ".code-snapshot"
#: 元数据魔数/协议版本：不认识的元数据一律当作「没有快照」（不猜、不迁移）。
SNAPSHOT_MAGIC = "battle2-code-snapshot"
SNAPSHOT_PROTO = 1
META_NAME = "snapshot.json"
LOCK_NAME = ".pack.lock"
_ZIP_PREFIX = "code."
_ZIP_SUFFIX = ".zip"
#: 保留的快照 zip 份数（按 mtime 留最近 N 份；旧锚的副本还可能在活着的消费者手里）。
KEEP_ZIPS = 4
#: 抢锚锁的有界等待（秒）/轮询步长：超过就自己打（启动绝不因为锁而挂住）。
LOCK_WAIT_SEC = 10.0
LOCK_POLL_SEC = 0.1
#: 写进元数据的命令行指纹长度（sha1 前 N 位，只用于「同一个进程吗」这种粗判）。
_CMDLINE_SHA12 = 12


@dataclass(frozen=True)
class CodeSnapshot:
    """一份集群代码快照（`reused` / `anchor_alive` 只用于日志与调用方判据）。"""

    zip_path: Path
    sha256: str
    bytes: int
    packed_at: float
    anchor_kind: str
    anchor_pid: int
    #: 锚进程还活着。**廉价口径**（只看 pid）：`proc_cmdline` 在 Windows 上要起 `wmic`/
    #: PowerShell（Win11 24H2 起 wmic 被移除 ⇒ 一次真子进程），而读面是每轮 BC publish /
    #: 每个 `/code` 请求都会走的热路。完整核验（命令行指纹）只发生在 `ensure` 的重打判定里
    #: ——那里才是「复用还是重打」的真岔口，`read_*` 的调用方都不按它分支。
    anchor_alive: bool
    #: 元数据里记的锚命令行指纹（`ensure` 的严格核验用；`read_*` 不算它）。
    anchor_cmdline_sha12: str
    #: 本次调用是「复用盘上那份」还是「新打了一份」。
    reused: bool


def _repo_root_default() -> Path:
    """`remote/code_snapshot.py` → 仓根（`nn-training/` 的上一级）。"""
    return Path(__file__).resolve().parents[2]


def snapshot_dir(repo_root: str | Path | None = None) -> Path:
    """快照目录：`<repo>/tmp/.code-snapshot`。

    `tmp` 是 hub 与 trainer 共同的绝对根（`--traj-root` = `<repo>/tmp`，控制台 `LOG_DIR` 同），
    也**不在** `pack_code_zip` 扫的根（`<repo>/nn-training`）之下 ⇒ 不自吞。

    `BCITY_CODE_SNAPSHOT_DIR`（环境变量）直接把快照目录指到别处——与仓内既有的
    `BCITY_TMP_LOGS_DIR` / `BCITY_RL_CONFIG` 同一种「单测重定向，默认零变化」约定；
    只在没显式传 `repo_root` 时生效（用例要两个都控时传 `repo_root`）。
    """
    if repo_root is None:
        override = os.environ.get("BCITY_CODE_SNAPSHOT_DIR")
        if override:
            return Path(override)
    root = Path(repo_root) if repo_root is not None else _repo_root_default()
    return root / "tmp" / SNAPSHOT_DIR_NAME


def _pack_root(repo_root: str | Path | None = None) -> Path:
    """被打包的源码根（`pack_code_zip` 的 `nn_root`）。"""
    root = Path(repo_root) if repo_root is not None else _repo_root_default()
    return root / "nn-training"


def _cmdline_sha12(pid: int) -> str:
    """进程命令行的 sha1 前 12 位；读不到 ⇒ 空串（调用方按「不可核验」处理）。"""
    cmd = proc_cmdline(pid)
    if not cmd:
        return ""
    return hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest()[:_CMDLINE_SHA12]


def _fingerprint_matches(pid: int, cmdline_sha12: str) -> bool:
    """命令行指纹是否一致（读不到 ⇒ False，fail-closed；**会起子进程**，见 `anchor_alive`）。"""
    got = _cmdline_sha12(pid)
    return bool(got) and got == cmdline_sha12


def _anchor_alive(pid: int, cmdline_sha12: str, *, strict: bool) -> bool:
    """锚是否仍由**同一个**进程持有。

    `strict=False`（读面默认）：只看 pid 存活（廉价；与 TS 侧判据同口径，见 plan §4.2）。
    `strict=True`（`ensure` 的分岔口）：再加上命令行指纹——pid 复用把锚“顶”给别人时必须重打。
    """
    if pid <= 0 or not pid_alive(pid):
        return False
    if not strict:
        return True
    return _fingerprint_matches(pid, cmdline_sha12)


def _load_meta(path: Path) -> dict | None:
    """读元数据（坏 JSON / 魔数不符 / 协议不认 ⇒ None）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    meta: dict = parsed
    if meta.get("magic") != SNAPSHOT_MAGIC or meta.get("proto") != SNAPSHOT_PROTO:
        return None
    return meta


def read_cluster_snapshot(
    repo_root: str | Path | None = None, *, strict_anchor: bool = False
) -> CodeSnapshot | None:
    """读盘上那份快照（**只读：绝不打包、绝不删除**）。

    元数据不自洽（zip 缺失 / sha 与文件实际字节不符 / 文件名带路径分隔符）⇒ `None`；
    锚已死不影响返回——`anchor_alive=False` 由调用方决定怎么用（见模块头注）。
    `strict_anchor=True` 时才花一次命令行核验（`ensure` 用；读面默认便宜口径）。
    """
    d = snapshot_dir(repo_root)
    meta = _load_meta(d / META_NAME)
    if meta is None:
        return None
    name = str(meta.get("zip") or "")
    # 元数据是盘上文件，可能被手改：只接受同目录内的文件名（拒绝 `../` 与盘符）。
    if not name or "/" in name or "\\" in name or not name.startswith(_ZIP_PREFIX):
        return None
    zp = d / name
    if not zp.is_file():
        return None
    want = str(meta.get("sha256") or "")
    if not want or sha256_file(zp) != want:
        return None
    anchor_raw = meta.get("anchor")
    anchor: dict = anchor_raw if isinstance(anchor_raw, dict) else {}
    pid = int(anchor.get("pid") or 0)
    fp = str(anchor.get("cmdline_sha12") or "")
    return CodeSnapshot(
        zip_path=zp,
        sha256=want,
        bytes=int(meta.get("bytes") or zp.stat().st_size),
        packed_at=float(meta.get("packed_at_epoch") or 0.0),
        anchor_kind=str(anchor.get("kind") or ""),
        anchor_pid=pid,
        anchor_alive=_anchor_alive(pid, fp, strict=strict_anchor),
        anchor_cmdline_sha12=fp,
        reused=True,
    )


def _acquire_pack_lock(lock_path: Path, log: Callable[[str], None]) -> int | None:
    """抢锚锁：`O_CREAT|O_EXCL` 原子创建；锁文件里的持有者已死 ⇒ 接管重试一次。

    返回 fd（调用方负责 `os.close` + 删除）；`None` = 没抢到（别人在打，或锁不可创建）。
    """
    for attempt in range(2):
        try:
            return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder, _exe, _ts = read_lock(str(lock_path))
            if holder is not None and pid_alive(holder):
                return None
            if attempt:
                return None
            try:
                os.remove(lock_path)  # 陈旧锁（崩溃残留）⇒ 接管重试
            except OSError:
                return None
        except OSError as e:
            log(f"[code-snapshot] WARN: 抢锚锁不可创建（{e}）——本次不做串行化")
            return None
    return None


def _count_zip_entries(zp: Path) -> int:
    try:
        with zipfile.ZipFile(zp) as z:
            return len(z.namelist())
    except (OSError, zipfile.BadZipFile):
        return 0


def _write_meta(d: Path, meta: dict, log: Callable[[str], None]) -> None:
    """原子写元数据（临时文件 + `os.replace`）：读者要么看到旧的、要么看到新的。"""
    tmp = d / (META_NAME + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / META_NAME)


def _prune(d: Path, keep: int, log: Callable[[str], None]) -> None:
    """只留最近 `keep` 份快照 zip（best-effort：删不动只 WARN，不影响本次快照）。"""
    try:
        zips = sorted(
            (p for p in d.glob(f"{_ZIP_PREFIX}*{_ZIP_SUFFIX}") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in zips[keep:]:
        try:
            old.unlink()
            log(f"[code-snapshot] 清理旧快照 {old.name}（保留最近 {keep} 份）")
        except OSError as e:
            log(f"[code-snapshot] WARN: 旧快照 {old.name} 删不掉（{e}）")


def _pack_and_publish(
    d: Path, repo_root: str | Path | None, *, anchor_kind: str, log: Callable[[str], None]
) -> CodeSnapshot:
    """打一份新快照并原子发布（内容寻址的文件名 + 最后写元数据）。"""
    d.mkdir(parents=True, exist_ok=True)
    stamp = f"{os.getpid()}.{int(time.time() * 1000)}"
    tmp_zip = d / f"{_ZIP_PREFIX}{stamp}.tmp"
    sha = pack_code_zip(_pack_root(repo_root), tmp_zip, log=log)
    final = d / f"{_ZIP_PREFIX}{sha[:12]}{_ZIP_SUFFIX}"
    # 内容寻址的纪律：同名 ≠ 同字节。同源码重打（打包器固定时间戳 ⇒ 同 sha）时不必写盘；
    # 但同名文件**必须先验 sha**——上一个副本可能是被截断/手改过的（自愈路径就落在这里）。
    if final.exists() and sha256_file(final) == sha:
        try:
            tmp_zip.unlink()
        except OSError:
            pass
    else:
        os.replace(tmp_zip, final)
    now = time.time()
    # 先算成局部量再进元数据：同一次调用的返回值与写盘内容**同源**（不从字面量字典里回读，
    # 免得两个值哪天分叉，也免得 mypy 对 `dict[str, object]` 的索引抱怨）。
    size = final.stat().st_size
    self_pid = os.getpid()
    self_fp = _cmdline_sha12(self_pid)
    meta = {
        "magic": SNAPSHOT_MAGIC,
        "proto": SNAPSHOT_PROTO,
        "zip": final.name,
        "sha256": sha,
        "bytes": size,
        "files_n": _count_zip_entries(final),
        "packed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "packed_at_epoch": now,
        "anchor": {
            "kind": anchor_kind,
            "pid": self_pid,
            "started_at_epoch": now,
            "cmdline_sha12": self_fp,
        },
        "src_root": str(_pack_root(repo_root)),
        "commit": _git_head(repo_root),
    }
    _write_meta(d, meta, log)
    _prune(d, KEEP_ZIPS, log)
    return CodeSnapshot(
        zip_path=final,
        sha256=sha,
        bytes=size,
        packed_at=now,
        anchor_kind=anchor_kind,
        anchor_pid=self_pid,
        anchor_alive=True,
        anchor_cmdline_sha12=self_fp,
        reused=False,
    )


def _git_head(repo_root: str | Path | None) -> str:
    """`git HEAD`（**仅信息性**：快照含未提交修改，不作任何判据）。读不到 ⇒ 空串。"""
    import subprocess

    root = Path(repo_root) if repo_root is not None else _repo_root_default()
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.decode("utf-8", "replace").strip()


def published_code_zip(
    job_root: str | Path,
    *,
    pack_root: str | Path,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[Path, str, bool]:
    """**消费路径**（publish）用的 `(code.zip 路径, sha256, 是否快照)`。

    快照在 ⇒ 用它（会话冻结）；快照缺失（从没起过 hub/trainer，或 `tmp` 被清理过）⇒
    **回落 per-course 打包 + 响亮 WARN**（＝改动前的逐字节行为）。

    ⚠ 这里**绝不建快照**：publish 一轮一次（BC 腿每轮都到），让它成为锚就等于把「改代码中
    开新课拿到不同代码」的旧 bug 从消费路径请回来。
    """
    snap = read_cluster_snapshot()
    if snap is not None:
        return snap.zip_path, snap.sha256, True
    path = Path(job_root) / "code.zip"
    log(
        "WARN: 集群代码快照缺失（remote/code_snapshot）——回落 per-course 打包；"
        "会话中途改代码后新开的课会拿到不同代码，建议重启 hub/trainer"
    )
    return path, pack_code_zip(pack_root, path, log=log), False


def current_code_zip_path(job_root: str | Path) -> Path:
    """当前该用的 `code.zip` 路径（**不打包**）：快照优先，否则 job_root 里那一份。

    给「发布之后还要读字节」的腿用（BC 的 `_submit_push`）：本轮 publish 已经把该有的
    那份摆好了，这里只解析。
    """
    snap = read_cluster_snapshot()
    return snap.zip_path if snap is not None else Path(job_root) / "code.zip"


def _line(snap: CodeSnapshot, *, reused: bool) -> str:
    return (
        f"[code-snapshot] sha12={snap.sha256[:12]} packed_at={snap.packed_at:.0f} "
        f"anchor={snap.anchor_kind}/pid={snap.anchor_pid} reused={1 if reused else 0} "
        f"bytes={snap.bytes} file={snap.zip_path.name}"
    )


def ensure_cluster_snapshot(
    *,
    anchor_kind: str,
    log: Callable[[str], None] = lambda _m: None,
    repo_root: str | Path | None = None,
) -> CodeSnapshot:
    """**启动路径专用**：锚活着就复用，否则打一份并成为新锚（见 §3.3 判定表）。

    失败策略由**调用方**决定（hub / trainer 一律「警告不致命」：起不来代价远比一份旧代码高）。
    本函数只在极端情形（如盘只读）抛 OSError——调用方按需要兜。
    """
    d = snapshot_dir(repo_root)
    # 启动路径：**严格**核验一次（一次命令行读取换「复用还是重打」的正确判定）。
    cur = read_cluster_snapshot(repo_root, strict_anchor=True)
    if cur is not None and cur.anchor_alive:
        log(_line(cur, reused=True))
        return cur

    fd = _acquire_pack_lock(d / LOCK_NAME, log)
    if fd is None:
        # 别人正在打：有界等待后复用（等不到就自己打——启动绝不挂住）。
        # 轮询用廉价口径（只看 pid），只在真拿到候选时花一次指纹核验。
        deadline = time.monotonic() + LOCK_WAIT_SEC
        while time.monotonic() < deadline:
            time.sleep(LOCK_POLL_SEC)
            cur = read_cluster_snapshot(repo_root)
            if (
                cur is not None
                and cur.anchor_alive
                and _fingerprint_matches(cur.anchor_pid, cur.anchor_cmdline_sha12)
            ):
                log(_line(cur, reused=True) + "（等锁后复用）")
                return cur
            if not (d / LOCK_NAME).exists():
                break
        snap = _pack_and_publish(d, repo_root, anchor_kind=anchor_kind, log=log)
        log(_line(snap, reused=False) + "（抢锁超时后自打）")
        return snap
    lock_path = d / LOCK_NAME
    try:
        # 双检：抢到锁后再看一眼（可能刚被并发的启动者写好，而它的锚还活着）。
        cur = read_cluster_snapshot(repo_root, strict_anchor=True)
        if cur is not None and cur.anchor_alive:
            log(_line(cur, reused=True) + "（抢锁后复用）")
            return cur
        snap = _pack_and_publish(d, repo_root, anchor_kind=anchor_kind, log=log)
        log(_line(snap, reused=False))
        return snap
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            lock_path.unlink()
        except OSError:
            pass
