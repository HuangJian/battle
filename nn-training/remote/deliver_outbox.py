"""remote/deliver_outbox.py —— 补传腿的**本地 outbox**（2026-10-23，plan/push-mode-notebooks §6.4a）。

> 为什么要有它：push 模式下**没有**「节点 → hub」的 POST（hub 自己来拉）。补传那条腿的全部产物
> （权重体 / 段末摘要 / 统计）必须落成 hub 能读的文件，而且要与 HTTP 腿**同字节**。
> 语义与判据见 `plan/push-mode-notebooks.plan.md` §6.4a（评审 P0-1/P0-3/P1-1/P1-3/P1-10）。

形态（`<outbox>/`）：

    state.json               # rounds{it:{rev,file,bytes,sha256,acked_rev,oversize}}, final_ready
    rounds/it0001.json       # 完整 wire body（与 HTTP 补传同字节 —— 同一个装配器）
    metrics/it0001.json      # 统计体（hub 用 `store_offline_metrics` 那条**同一个**入口 ingest）
    final.json               # 段末 result body

三条纪律（都不是口味问题）：

  * **装配不在这里**：body 由 `deliver_parts.build_round_body` / `build_result_body` 产出（唯一
    实现），本模块只负责「写盘 / 记账 / rev / ack」。`parts` 是**注入**的模块对象 —— 本模块因此
    停在 L1（不向上依赖 `deliver_parts`(L1) 之上任何东西），`deliver_proc`(L3) 不必升层。
  * **ack 带 rev**（P0-3）：只删「本次 ack 的 rev == 当前 rev」的 body。迟到的评估行
    （`submit_eval_round` 在权重体已被 ack 之后 rev+1）会重新出现在 `state.rounds[it]` 里，hub 以
    「`it <= cursor` 但 rev 变了」为必须重拉。**无条件删 `it<=upto_it` 已否决**：那会把刚建好的
    「迟到评估行」通道整个吃掉（HTTP 腿靠 `_degraded/owed_reposts` + `?parts=metrics` 撑着，
    push 腿没有那两条）。
  * **进程内降级是常态**（P1-10）：任务包里的 `code.zip` 是导出那一刻的快照，旧 `deliver_worker`
    不认 `--outbox` ⇒ `unrecognized arguments` ⇒ sticky 降级。所以同一个类必须在 `background=True`
    （进程内线程）下完整工作 —— 同一份实现、同一个 outbox 目录。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

import remote.delta_files as delta_files
from common.fs import atomic_write_bytes, atomic_write_json
from common.logutil import tag_logger
from common.protocol import OFFLINE_ARTIFACT_BODY_MAX, OFFLINE_PUSH_OUTBOX_WARN_BYTES
from remote.artifacts import ArtifactStore

try:  # pragma: no cover - 平台分支
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
try:  # pragma: no cover - 平台分支
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]

#: POSIX 文件锁入口，**先取出来**再用（2026-10-24）：`fcntl` 在 win32 下不存在，新版 typeshed
#: 也不给它 `flock`/`LOCK_*` ⇒ 直接 `fcntl.flock(...)` 会让跨平台 mypy 报「模块没有这个属性」
#: （本仓门禁在 Windows 上跑）。取不到就走 msvcrt 分支，运行时语义不变。
_FLOCK = getattr(fcntl, "flock", None) if fcntl is not None else None
_LOCK_EX = getattr(fcntl, "LOCK_EX", 0) if fcntl is not None else 0
_LOCK_UN = getattr(fcntl, "LOCK_UN", 0) if fcntl is not None else 0

#: Windows 那一半同理（2026-10-27）：`msvcrt.locking` / `LK_LOCK` / `LK_UNLCK` 在 typeshed 里
#: 住在 stub 的 `if sys.platform == "win32":` 分支内 ⇒ 在 Linux 上跑 mypy 时整段被剪掉、报
#: 「模块没有这个属性」（`fcntl` 半边是反方向同一个坑）。取出来再用，运行时语义不变——
#: POSIX 上 `_FLOCK` 恒非空、这半边本来就到不了；Windows 上 `msvcrt` 在 ⇒ 三个名字都在。
_MS_LOCKING = getattr(msvcrt, "locking", None) if msvcrt is not None else None
_MS_LK_LOCK = getattr(msvcrt, "LK_LOCK", 0) if msvcrt is not None else 0
_MS_LK_UNLCK = getattr(msvcrt, "LK_UNLCK", 0) if msvcrt is not None else 0

#: 状态文件（hub 的 `/state` 读面就是它 + `rounds/` 目录）。
STATE_NAME = "state.json"
#: 段末摘要的体（`result=true` 的 ack 才删）。
FINAL_NAME = "final.json"
#: 逐轮 body 的目录。
ROUNDS_DIR = "rounds"
#: 统计体的目录（每轮一个文件；hub 逐个 POST 到 `store_offline_metrics`）。
METRICS_DIR = "metrics"
#: 逐轮文件名（`it0001.json`）——**定宽**，`sorted()` 即按 it 升序。
ROUND_FILE_FMT = "it{it:04d}.json"
#: 一拍最多物化多少轮（子进程 tick / 线程 tick 共用）。体 ~1.8MB ⇒ 8 轮/拍与 HTTP 腿的
#: `deliver_worker.TICK_ROUNDS` 同一量级（把追赶摊到几拍，别一次吃满磁盘 IO）。
SYNC_BATCH_ROUNDS = 8
#: 进程内线程模式的拍长（秒）。
OUTBOX_TICK_SEC = 2.0
#: 单轮评估读数上界（与 `OfflineDeliverer.EVAL_ROWS_CAP` 同值；正常一轮 = 关数×种子数，双轨 100）。
EVAL_ROWS_CAP = 400
#: outbox 账本的**跨进程**闸文件（`ack_outbox` 与训练子进程各开一个 fd 抢它）。
STATE_LOCK_NAME = ".state.lock"

#: 每个 outbox 一把进程内可重入锁（跨线程）+ 持有深度/文件 fd（跨进程）。
#: 深度 >0 时同线程再进闸不再 flock —— 同进程对同一文件再 flock 一个新 fd 会自锁。
_GATES: dict[str, dict[str, Any]] = {}
_GATES_LOCK = threading.Lock()


def _lock_state_file(path: Path) -> int | None:
    """打开并锁住闸文件；锁不上 ⇒ `None`（**降级不抛**：账本一致性不该换来训练停摆）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return None
    try:
        if _FLOCK is not None:
            _FLOCK(fd, _LOCK_EX)
        elif _MS_LOCKING is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            _MS_LOCKING(fd, _MS_LK_LOCK, 1)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None
    return fd


def _unlock_state_file(fd: int) -> None:
    try:
        if _FLOCK is not None:
            _FLOCK(fd, _LOCK_UN)
        elif _MS_LOCKING is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            _MS_LOCKING(fd, _MS_LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@contextmanager
def _outbox_gate(root: str | Path) -> Any:
    """outbox 账本/body 的写闸（进程内可重入 + 跨进程 `flock`）。

    为什么必须有它（评审 F8）：训练子进程（`LocalOutboxDeliverer`）与 worker 的 HTTP 面
    （`/offline/push/ack`）是**两个进程**，都在写 `state.json`、都在删 body：

    * 子进程整份覆盖写会**复活** ack 刚删掉的 body（hub 永远 404 ⇒ 那一轮永远拉不到 ⇒ 永不收尾）；
    * ack 可能在「body 已换成新 rev、账本还是旧 rev」之间把**新** body 删掉。

    所以「换入 body + 写账本」与「读账本 + 删 body + 写账本」必须进同一把闸。
    """
    key = str(Path(root).resolve())
    with _GATES_LOCK:
        gate = _GATES.get(key)
        if gate is None:
            gate = {"lock": threading.RLock(), "depth": 0, "fd": None}
            _GATES[key] = gate
    with gate["lock"]:
        if gate["depth"] == 0:
            gate["fd"] = _lock_state_file(Path(root) / STATE_LOCK_NAME)
        gate["depth"] += 1
        try:
            yield
        finally:
            gate["depth"] -= 1
            if gate["depth"] == 0 and gate["fd"] is not None:
                fd = gate["fd"]
                gate["fd"] = None
                _unlock_state_file(int(fd))

#: 缺省日志（与 `remote/*` 其余模块同一个工厂：`tag_logger` + 本模块自己的 `time` 引用，假钟才拦得到）。
#: 刻意**不**再写一份 `print` —— `tests/test_reuse_ledger.py` 会把逐字节重复的实现当场标红。
_log_default = tag_logger("outbox", lambda: time)


class OutboxParts(Protocol):
    """`remote.deliver_parts` 的**注入面**（本模块对它的全部依赖，逐条列在这里）。

    `parents` 注入而不是 import：`deliver_parts` 是 L1，本模块若 import 它就也是 L1 —— 而层号是
    拓扑秩（`tests/test_remote_dag.py`），两个 L1 互相依赖不成立；升到 L2 又会把 `deliver_proc`
    顶到 L4（连带 `plan_handoff`/`run_loop` 全体重排）。注入是仓里已经用过的两条正当做法之一
    （另一条 = 下沉共同依赖）。
    """

    def rounds_with_rows(self, root: str | Path) -> set[int]: ...

    def eval_rows_for(
        self,
        root: str | Path,
        it: int,
        *,
        cap: int,
        log: Callable[[str], None] | None = None,
        throttled_log: Callable[[str, str], None] | None = None,
    ) -> tuple[list[dict], int, int]: ...

    def row_for(
        self,
        root: str | Path,
        it: int,
        weights_fp: str,
        *,
        log: Callable[[str], None] | None = None,
    ) -> dict | None: ...

    def build_round_body(
        self,
        root: str | Path,
        run_id: str,
        it: int,
        *,
        course: str = "",
        hub_lease: str = "",
        worker_name: str = "",
        eval_rows: list[dict] | None = None,
        row_for: Callable[[int, str], dict | None] | None = None,
        lineage: Mapping | None = None,
        pergame: bool = True,
        now: float = 0.0,
        log: Callable[[str], None] | None = None,
    ) -> tuple[dict, bytes, str, str, int] | None: ...

    def build_result_body(
        self,
        *,
        run_id: str,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
        seg_end_kind: str = "",
        delivered: int = 0,
        lineage: Mapping | None = None,
        source_dir: str = "",
        course: str = "",
        now: float = 0.0,
    ) -> tuple[dict, bytes]: ...

    def build_metrics_body(
        self,
        root: str | Path,
        run_id: str,
        its: list[int],
        *,
        row_for: Callable[[int], Mapping | None],
        cap_rounds: int,
        cap_bytes: int,
        course: str = "",
    ) -> tuple[dict, int]: ...


def round_path(outbox: str | Path, it: int) -> Path:
    """某轮 body 的落点（**唯一**拼法；hub 侧按同一规则找文件）。"""
    return Path(outbox) / ROUNDS_DIR / ROUND_FILE_FMT.format(it=int(it))


def metrics_path(outbox: str | Path, it: int) -> Path:
    """某轮统计体的落点。"""
    return Path(outbox) / METRICS_DIR / ROUND_FILE_FMT.format(it=int(it))


def read_state(outbox: str | Path) -> dict:
    """读 `state.json`（缺/坏 ⇒ 空字典；**永不抛**——读面不该因半截写入炸掉）。"""
    try:
        doc = json.loads((Path(outbox) / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def outbox_bytes(outbox: str | Path) -> int:
    """outbox 里所有文件的总字节（容量告警 / 派发闸的输入；读不到的文件跳过）。"""
    total = 0
    root = Path(outbox)
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += int(p.stat().st_size)
        except OSError:
            continue
    return total


def ack_outbox(
    outbox: str | Path,
    *,
    upto_it: int,
    result: bool = True,
    revs: Mapping[int, int] | None = None,
    log: Callable[[str], None] = _log_default,
) -> dict:
    """按 rev 收下并删 body（**唯一实现**；`LocalOutboxDeliverer.ack` 与服务端端点共用）。

    为什么是模块函数、不只留类方法：ack 的**调用方是 worker 的 HTTP 服务进程**（W4 的
    `/offline/push/ack`）——那个进程不跑补传腿（腿住在训练子进程里），只能读同一份
    `state.json`。两个进程各写一份「删哪些」的判据，就是两套 ack 语义（P0-3 要防的正是这个）。

    整个「读账本 → 删 body → 写账本」在 `_outbox_gate` 里（评审 F8）：训练子进程换入新 body
    时也拿同一把闸，否则 ack 会在「body 已换新 rev、账本还是旧 rev」之间删掉新 body。
    """
    root = Path(outbox)
    with _outbox_gate(root):
        return _ack_outbox_locked(root, upto_it=upto_it, result=result, revs=revs, log=log)


def _ack_outbox_locked(
    root: Path,
    *,
    upto_it: int,
    result: bool,
    revs: Mapping[int, int] | None,
    log: Callable[[str], None],
) -> dict:
    doc = read_state(root)
    raw_rounds = doc.get("rounds")
    rounds: dict = dict(raw_rounds) if isinstance(raw_rounds, Mapping) else {}
    removed: list[int] = []
    kept: list[int] = []
    metrics_removed = 0
    for key in sorted(rounds, key=lambda k: int(k)):
        it = int(key)
        if it > int(upto_it):
            continue
        ent = dict(rounds[key]) if isinstance(rounds[key], Mapping) else {}
        rev = int(ent.get("rev", 0) or 0)
        seen = rev if revs is None else int(revs.get(it, -1))
        if seen != rev:
            kept.append(it)
            continue
        ent["acked_rev"] = rev
        if ent.get("file"):
            try:
                round_path(root, it).unlink()
            except OSError:
                pass
            ent["file"] = ""
        if metrics_path(root, it).exists():
            try:
                metrics_path(root, it).unlink()
                metrics_removed += 1
            except OSError:
                pass
        removed.append(it)
        rounds[key] = ent
    final_removed = False
    if result:
        # ★W6：`result=true` = 「这单 hub 已全部收下」的**终态**（failed/cancelled 没有
        # final 也走这条 ack）。在盘上留痕，face 重启时据此跳过已收尾的目录、不把废弃单
        # 复活成 busy；与删 body 写在**同一次**原子落盘里（不存在中间崩溃窗口）。
        doc["result_acked"] = True
        doc["result_acked_at"] = float(time.time())
        if doc.get("final_ready"):
            try:
                (root / FINAL_NAME).unlink()
                final_removed = True
            except OSError:
                final_removed = False
            doc["final_acked"] = True
            doc["final_ready"] = False
    doc["rounds"] = rounds
    doc["updated_at"] = float(time.time())
    try:
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(root / STATE_NAME, doc)
    except OSError as e:
        log(f"WARN outbox ack 账本写失败（{type(e).__name__}: {e}）——下一拍会重试同一批")
    if kept:
        log(
            f"outbox ack upto it{int(upto_it)}：{len(removed)} 轮收下，"
            f"{len(kept)} 轮 rev 已前进（hub 需重拉 it{kept[0]}…it{kept[-1]}）"
        )
    return {
        "removed": removed,
        "kept": kept,
        "metrics_removed": metrics_removed,
        "final_removed": final_removed,
    }


class LocalOutboxDeliverer:
    """把补传产物**落成本地文件**的腿（`DelivererLike` 的同形面，不连任何网络）。

    `background=False`（子进程模式 / 同步调试）：`submit_*` 当场物化；
    `background=True`（进程内降级）：`start()` 起一条 daemon 线程按 `tick_sec` 扫盘物化，
    `submit_*` 只置位+唤醒（**不阻塞训练线程** —— 旧 outbox 什么都不写的那种「降级成功、outbox
    空」正是 P1-10 要钉住的形态）。
    """

    def __init__(
        self,
        *,
        outbox_dir: str | Path,
        artifacts_dir: str | Path,
        run_id: str,
        parts: OutboxParts,
        course: str = "",
        hub_lease: str = "",
        worker_name: str = "",
        eval_cap: int = EVAL_ROWS_CAP,
        cap_bytes: int = OFFLINE_ARTIFACT_BODY_MAX,
        background: bool = False,
        tick_sec: float = OUTBOX_TICK_SEC,
        #: ★ **地板**（`it <= floor_it` 的轮次一律不物化）：push 任务包里已经**在 hub 上**的那些轮。
        #: 值由任务元数据给（hub 的 cursor = 派发时的 resume 锚点），**缺省 0 = 全部物化**。
        #: 为什么默认是「不省」：`floor_it` 给错（高于 hub 真正的 cursor）就是**永久丢那几轮**，
        #: 而给 0 的代价只是多发几份 hub 会按内容去重的重复体——错的方向必须选不丢数据的那个。
        floor_it: int = 0,
        now: Callable[[], float] = time.time,
        log: Callable[[str], None] = _log_default,
    ) -> None:
        self.outbox = Path(outbox_dir)
        self.root = Path(artifacts_dir)
        self.run_id = str(run_id or "")
        self.course = str(course or "").strip()
        self.worker_name = str(worker_name or "").strip()
        self.hub_lease = str(hub_lease or "").strip()
        #: 日志与"这条腿现在在哪"的可读面（`deliver_worker` 的启动行读它）。
        self.base_url = f"outbox:{self.outbox}"
        self.enabled = True
        self.log = log
        self.background = bool(background)
        self._parts = parts
        self._eval_cap = int(eval_cap)
        self._cap_bytes = int(cap_bytes)
        self._tick = max(0.05, float(tick_sec))
        self._now = now
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ticks = 0
        self._floor_it = int(floor_it or 0)
        self._last_log: dict[str, float] = {}
        self._manifest_cache: dict | None = None
        self._warned_bytes = False
        self._size_check_at = 0.0
        #: 物化过的轮次（`{it: 条目}`，条目 = state.rounds 里那一份）。
        self._rounds: dict[int, dict] = {}
        #: 还没物化的重投（迟到评估行；与 `state.owed_reposts` 同源）。
        self._owed: set[int] = set()
        self._final_obj: dict | None = None
        self._final_ready = False
        self._final_acked = False
        #: hub 已对整单说了 result=true（含没有 final 的 failed/cancelled 收摊）——跨重启的
        #: 终态记号，只由 `ack_outbox` 写、这里负责在整份覆盖写里保留它。
        self._result_acked = False
        self._load_state()

    # ------------------------------------------------------------ 状态面（落盘 / 装载）

    def _state_doc(self) -> dict:
        doc: dict = {
            "run_id": self.run_id,
            "course": self.course,
            "updated_at": float(self._now()),
            "rounds": {str(it): dict(ent) for it, ent in sorted(self._rounds.items())},
            "owed_reposts": sorted(self._owed),
            "final_ready": bool(self._final_ready),
            "final_acked": bool(self._final_acked),
            "result_acked": bool(self._result_acked),
            #: 本 outbox 的**地板**落盘（重启后不许因为「这次没传参」而把已跳过的历史轮重新物化）。
            "floor_it": int(self._floor_it),
        }
        return doc

    def _save_state(self) -> None:
        """整份覆盖写（原子；**先与磁盘上的 ack 结果合并**）。**永不抛**。

        合并是 F8 的另一半：ack 端点（另一个进程）会把 `acked_rev` / `file=""` /
        `final_acked` 写进同一份 `state.json`。不先读回来合并，这次整份写就会把那些删除
        「复活」——hub 会去拉一个已经不存在的 body，永远 404，pump 永不收尾。
        """
        try:
            with _outbox_gate(self.outbox):
                with self._lock:
                    self._merge_disk_acks()
                    doc = self._state_doc()
                atomic_write_json(self.outbox / STATE_NAME, doc)
        except OSError as e:
            self.log(f"WARN outbox 状态写失败（{type(e).__name__}: {e}）——本轮记账未落盘")
            return
        self._note_size()

    def _merge_disk_acks(self) -> None:
        """把另一个写者（ack 端点）已收下的部分并回内存（调用方必须持有闸）。"""
        disk = read_state(self.outbox)
        raw = disk.get("rounds")
        rounds = raw if isinstance(raw, Mapping) else {}
        for key, ent in rounds.items():
            try:
                it = int(key)
            except (TypeError, ValueError):
                continue
            mine = self._rounds.get(it)
            if mine is None or not isinstance(ent, Mapping):
                continue
            d_rev = int(ent.get("rev", 0) or 0)
            d_acked = int(ent.get("acked_rev", 0) or 0)
            mine["acked_rev"] = max(int(mine.get("acked_rev", 0) or 0), d_acked)
            m_rev = int(mine.get("rev", 0) or 0)
            if d_rev and d_rev == m_rev and d_acked >= d_rev:
                # ack 收下的就是内存里这一版：body/统计已被它删掉，不能再登记 file（否则
                # hub 永远 404）。rev 更新（迟到评估行重投）时走 else：body 还在。
                mine["file"] = ""
        if (
            self._final_ready
            and not self._final_acked
            and bool(disk.get("final_acked"))
            and not (self.outbox / FINAL_NAME).exists()
        ):
            # 段末摘要已被 ack 收下并删除；内存不许把它复活成 ready（hub 会一直来拉）。
            self._final_ready = False
            self._final_acked = True
        if disk.get("result_acked"):
            self._result_acked = True

    def _note_size(self) -> None:
        """超 `OFFLINE_PUSH_OUTBOX_WARN_BYTES` 打**一行** WARN（每进程一次；检查本身也节流）。"""
        if self._warned_bytes:
            return
        now = float(self._now())
        if now < self._size_check_at:
            return
        # 每轮落盘都会 save，rglob 整个 outbox 不能跟着变成每轮一次。
        self._size_check_at = now + 60.0
        size = outbox_bytes(self.outbox)
        if size <= OFFLINE_PUSH_OUTBOX_WARN_BYTES:
            return
        self._warned_bytes = True
        self.log(
            f"WARN outbox 已 {size / 1024**3:.2f}GiB（> {OFFLINE_PUSH_OUTBOX_WARN_BYTES / 1024**3:.0f}GiB）"
            "——hub 一直没来拉？产物不丢（都在文件里），但磁盘在涨"
        )

    def _load_state(self) -> None:
        doc = read_state(self.outbox)
        rounds = doc.get("rounds")
        if isinstance(rounds, Mapping):
            for key, ent in rounds.items():
                try:
                    it = int(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(ent, Mapping):
                    self._rounds[it] = dict(ent)
        owed = doc.get("owed_reposts")
        if isinstance(owed, list):
            self._owed = {
                int(it) for it in owed if isinstance(it, int) and not isinstance(it, bool)
            }
        self._final_ready = bool(doc.get("final_ready"))
        self._final_acked = bool(doc.get("final_acked"))
        self._result_acked = bool(doc.get("result_acked"))
        # 地板**第一次写入就定终身**（§6.4a）：构造参数只在 state 里还没有值时生效。
        saved = doc.get("floor_it")
        if isinstance(saved, int) and not isinstance(saved, bool):
            self._floor_it = int(saved)

    def reload(self) -> None:
        """重读 `state.json`（父侧的只读镜像用：子进程是这份文件的**唯一写者**）。

        与 `OfflineDeliverer.refresh_from_ledger` 同一个意图：不重读就会拿着父进程启动那一刻的
        快照去回答 `pending()`（那个数字只会越来越错）。
        """
        with self._lock:
            self._rounds.clear()
            self._owed.clear()
            self._final_ready = False
            self._final_acked = False
            self._load_state()

    def _manifest(self) -> dict:
        """产物目录里的 manifest 快照（血缘字段；读失败 ⇒ 空，与 HTTP 腿同口径）。"""
        if self._manifest_cache is None:
            try:
                doc = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
                self._manifest_cache = doc if isinstance(doc, dict) else {}
            except (OSError, ValueError):
                self._manifest_cache = {}
        return self._manifest_cache

    def _throttled_log(self, key: str, msg: str) -> None:
        now = float(self._now())
        if now - self._last_log.get(key, 0.0) < 60.0:
            return
        self._last_log[key] = now
        self.log(msg)

    # ------------------------------------------------------------ 待落盘集

    def _disk_rounds(self) -> list[int]:
        """磁盘上**真轮次**（账本里有行 + 有权重文件），升序。判据与 HTTP 腿逐字同源。"""
        rows = self._parts.rounds_with_rows(self.root)
        out: list[int] = []
        for it in sorted(rows):
            if int(it) <= self._floor_it:
                continue  # 已经在 hub 上的轮（见 `floor_it`）
            # 目录名两态都认：`ArtifactStore.IT_PREFIX` + 三位宽（`it-001`，全仓约定的唯一拼法）。
            d = self.root / f"{ArtifactStore.IT_PREFIX}{int(it):03d}"
            if delta_files.find_existing(d, "weights.json") is not None:
                out.append(int(it))
        return out

    def pending(self) -> list[int]:
        """还没物化成 body 的轮次（`state.rounds` 里没有 ⇒ 没写过）。"""
        with self._lock:
            return [it for it in self._disk_rounds() if it not in self._rounds]

    def pending_anchors(self) -> list[int]:
        """`pending()` 全量 —— **push 腿不做锚点瘦身**（每轮都要一个完整 body 给 hub 拉）。

        名字保留是为了与 `OfflineDeliverer` 同形：`deliver_worker` 的 idle 判据与状态面都用它，
        两者语义在各自腿上都是「这一拍真会写的轮次」。
        """
        return self.pending()

    def unsent_reposts(self) -> list[int]:
        """还没物化的重投（迟到评估行）——与 HTTP 腿的 `owed_reposts` 同意图。"""
        with self._lock:
            return sorted(self._owed)

    def mark_repost(self, it: int) -> None:
        """记一次重投（`rev+1` 在物化时发生；幂等）。"""
        with self._lock:
            self._owed.add(int(it))

    def result_done(self) -> bool:
        """段末摘要是否已被 hub ack（`result=true`）。"""
        with self._lock:
            return bool(self._final_acked)

    # ------------------------------------------------------------ 物化

    def _write_metrics(self, it: int) -> None:
        """写这一轮的统计体（`store_offline_metrics` 的同一个入口）。

        为什么要它（P1-3）：`oversize` / 无权重体的轮若连 row 一起丢，控制台就看不到那一轮
        （2026-10-23 的 y31-null 教训：28 轮走权重体、0 条 `offline_metrics`）。
        """
        body, n = self._parts.build_metrics_body(
            self.root,
            self.run_id,
            [int(it)],
            row_for=lambda i: self._parts.row_for(self.root, int(i), "", log=self.log),
            cap_rounds=1,
            cap_bytes=64 * 1024,
            course=self.course,
        )
        if not n:
            return
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_write_bytes(metrics_path(self.outbox, it), raw)

    def _write_round(self, it: int) -> bool:
        """物化一轮：`True` = 落盘（或按 `oversize` 记了账），`False` = 本拍写不了。"""
        eval_rows, _raw_b, _wire_b = self._parts.eval_rows_for(
            self.root,
            int(it),
            cap=self._eval_cap,
            log=self.log,
            throttled_log=self._throttled_log,
        )
        built = self._parts.build_round_body(
            self.root,
            self.run_id,
            int(it),
            course=self.course,
            hub_lease=self.hub_lease,
            worker_name=self.worker_name,
            eval_rows=eval_rows,
            row_for=lambda i, fp: self._parts.row_for(self.root, int(i), fp, log=self.log),
            lineage=self._manifest(),
            # ★ 与 HTTP 腿同一个值（`plan/anchor-full-rounds` S6）：body 不摘逐局明细。
            pergame=True,
            now=float(self._now()),
            log=self.log,
        )
        if built is None:
            return False  # 权重读不到/空：瞬时状态，不记账（下一拍再试）
        _body, raw, weights_fp, opt_form, _pergame = built
        old = self._rounds.get(int(it), {})
        rev = int(old.get("rev", 0) or 0) + 1
        entry: dict = {
            "rev": rev,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "weights_fp": weights_fp,
            "opt_form": opt_form,
            "acked_rev": int(old.get("acked_rev", 0) or 0),
            "oversize": False,
        }
        path = round_path(self.outbox, int(it))
        # ★ 换入 body + 写账本在同一把闸里（评审 F8）：否则 ack 会在「body 已换成新 rev、
        # 账本还是旧 rev」之间把新 body 删掉 —— hub 永远 404，这一轮永远拉不到。
        with _outbox_gate(self.outbox):
            if len(raw) > self._cap_bytes:
                # 超限的轮：**不写 body**（hub 拿不走），但账与统计照记 ⇒ 控制台仍有这一轮。
                entry["oversize"] = True
                entry["file"] = ""
                try:
                    path.unlink()
                except OSError:
                    pass
                self._throttled_log(
                    f"oversize:{int(it)}",
                    f"outbox it{int(it)} 体 {len(raw) / 1024 / 1024:.1f}MB 超上限 "
                    f"{self._cap_bytes / 1024 / 1024:.0f}MB ⇒ 本轮只留统计（hub 会跳过 body）",
                )
            else:
                atomic_write_bytes(path, raw)
                entry["file"] = str(Path(ROUNDS_DIR) / ROUND_FILE_FMT.format(it=int(it)))
            try:
                self._write_metrics(int(it))
            except OSError as e:
                self.log(f"WARN outbox it{int(it)} 统计写失败（{type(e).__name__}: {e}）——权重体照落")
            with self._lock:
                prev_acked = int(old.get("acked_rev", 0) or 0)
                if prev_acked and rev == prev_acked + 1 and not entry["oversize"]:
                    # 迟到评估行（或重投）让 rev 前进了一格：这正是 hub 要重拉的那一格（P0-3）。
                    self.log(f"outbox it{int(it)} 重投：rev {rev - 1} → {rev}（迟到评估行已并入）")
                self._rounds[int(it)] = entry
                self._owed.discard(int(it))
                self._save_state()
        if path.exists() and entry["file"]:
            self.log(f"outbox it{int(it)} 已落盘（rev{rev}，{len(raw) / 1024:.0f}KB）→ {path}")
        return True

    def _write_final(self) -> bool:
        """把段末摘要写进 `final.json`（幂等：同内容重写无害，hub 覆盖读）。"""
        obj = self._final_obj
        if obj is None:
            return False
        _body, raw = self._parts.build_result_body(
            run_id=self.run_id,
            it_end=int(obj.get("it_end", 0) or 0),
            state=str(obj.get("state", "") or ""),
            summary=obj.get("summary"),
            end_it_reached=bool(obj.get("end_it_reached")),
            seg_end_kind=str(obj.get("seg_end_kind", "") or ""),
            delivered=len(self._rounds),
            lineage=self._manifest(),
            source_dir=str(self.root),
            course=self.course,
            now=float(self._now()),
        )
        # 与 `_write_round` 同理（评审 F8）：body 与「ready」必须在同一把闸里，否则 ack 会
        # 在两者之间删掉刚写的 final，而账本仍说它 ready —— hub 永远拉不到，永不收尾。
        with _outbox_gate(self.outbox):
            atomic_write_bytes(self.outbox / FINAL_NAME, raw)
            with self._lock:
                self._final_ready = True
                self._final_obj = None
                self._save_state()
        return True

    def _sync_once(self) -> int:
        """物化一拍：新轮次 + 重投 + 段末摘要。返回**写成的轮数**。**永不抛**。"""
        wrote = 0
        try:
            with self._lock:
                owed = sorted(self._owed)
                fresh = [it for it in self._disk_rounds() if it not in self._rounds]
            # 重投排前面：它们是「已经到岸过一次、只差读数」的那几轮。
            for it in (owed + [i for i in fresh if i not in set(owed)])[:SYNC_BATCH_ROUNDS]:
                if self._write_round(int(it)):
                    wrote += 1
            self._write_final()
            self._ticks += 1
            self._save_state()
        except Exception as e:  # 落盘腿的任何意外都不得波及训练
            self.log(f"outbox 物化异常（忽略，训练继续）：{type(e).__name__}: {e}")
        return wrote

    def sync(self, *, force_probe: bool = False, budget_sec: float | None = None) -> int:
        """`deliver_worker` 的 tick 接口（`force_probe`/`budget_sec` 在这里无意义：没有网络）。

        保留签名是为了让**同一个子进程循环**同时服务两条腿（HTTP / outbox）——那正是「旧
        `deliver_worker` 不认 `--outbox` 就整段降级」的另一面：新子进程只多一个分支，循环不变。
        """
        return self._sync_once()

    # ------------------------------------------------------------ 生命周期（DelivererLike）

    def start(self) -> None:
        """后台模式起线程（同步模式 = 空操作，与 `OfflineDeliverer` 同义）。"""
        if not self.background or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="deliver-outbox", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sync_once()
            self._wake.wait(self._tick)
            self._wake.clear()

    def close(self, timeout: float = 10.0) -> None:
        """收线：停线程 → 最后一拍 → **响亮核对**（还有没物化的轮/摘要就说出来）。"""
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout=max(0.0, float(timeout)))
            self._thread = None
        self._sync_once()
        left = self.pending()
        if left:
            self.log(
                f"outbox 收线：还有 {len(left)} 轮没物化（it{left[0]}…it{left[-1]}）"
                "——下一段（同一产物目录）会接着物化"
            )
        if self._final_ready and not self._final_acked:
            self.log("outbox 收线：段末摘要已落盘但 hub 还没 ack（final.json 留着，hub 会来拉）")

    def submit_round(self, it: int) -> None:
        """一轮落盘后请求物化（后台模式非阻塞；同步模式当场写）。"""
        if self.background:
            self._wake.set()
            return
        self._sync_once()

    def submit_eval_round(self, it: int) -> None:
        """迟到评估行落账后请求 `rev+1` 重写这一轮（幂等）。"""
        self.mark_repost(int(it))
        if self.background:
            self._wake.set()
            return
        self._sync_once()

    def submit_final(
        self,
        *,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
        seg_end_kind: str = "",
    ) -> None:
        """段末摘要：落 `final.json`（等 hub ack）。"""
        self._final_obj = {
            "it_end": int(it_end),
            "state": str(state),
            "summary": dict(summary or {}),
            "end_it_reached": bool(end_it_reached),
            "seg_end_kind": str(seg_end_kind or ""),
        }
        with self._lock:
            self._final_acked = False
        if self.background:
            self._wake.set()
            return
        self._write_final()
        self._save_state()

    deliver_result = submit_final  # 与 `OfflineDeliverer.deliver_result` 同义（子进程循环用它）

    def backup_pending(self) -> int:
        """outbox **本身就是**耐久副本（hub 来拉的那一份），不再另立抽屉 ⇒ 恒 0。"""
        return 0

    # ------------------------------------------------------------ hub 侧 ack

    def ack(
        self, upto_it: int, *, result: bool = True, revs: Mapping[int, int] | None = None
    ) -> dict:
        """hub 确认已收到：删掉「本次 ack 的 rev == 当前 rev」的 body（**带 rev**，P0-3）。

        `revs` = hub 看到并收下的 `{it: rev}`。某轮的 `revs[it]` 落后于当前 rev ⇒ **不删**
        （hub 拿的是旧版：它下次必须按新 rev 重拉）。`revs=None` = 老/宽松调用（按当前 rev 收），
        只给测试与人工清理用 —— 生产路径的派发器（W5）必须带 rev。

        返回 `{"removed": [...], "kept": [...], "metrics_removed": n, "final_removed": bool}`。
        """
        # 不在这里持 `self._lock`：`ack_outbox` 要拿 outbox 闸，而物化腿的顺序是「闸 → 账本锁」
        # （评审 F8）——反着拿会与 `_write_round` 互相等死。
        out = ack_outbox(
            self.outbox, upto_it=int(upto_it), result=bool(result), revs=revs, log=self.log
        )
        # 内存面必须跟 `state.json` 对齐：ack 是**另一个可能的写者**（服务进程）也会
        # 触发的动作，拿旧快照回答 `pending()` 会把已收下的轮回物化一遍。
        self.reload()
        return out

    # ------------------------------------------------------------ 读面

    def status(self) -> dict:
        """状态面（与 `DelivererLike.status()` 同形；`deliver_worker` 的 `_write_status` 读它）。"""
        with self._lock:
            done = [
                it
                for it, ent in self._rounds.items()
                if ent.get("file") == "" and ent.get("acked_rev")
            ]
            rounds = {str(it): dict(ent) for it, ent in sorted(self._rounds.items())}
            owed = sorted(self._owed)
            pending = [it for it in self._disk_rounds() if it not in self._rounds]
            final_ready = bool(self._final_ready)
            final_acked = bool(self._final_acked)
        return {
            "enabled": True,
            "hub_url": self.base_url,
            "outbox": str(self.outbox),
            "mode": "outbox",
            "background": self.background,
            "delivered": len(done),
            "pending": len(pending),
            "pending_anchors": len(pending),
            "reposts_unsent": len(owed),
            "reposts": owed,
            "result_done": final_acked,
            "final_ready": final_ready,
            "outbox_bytes": outbox_bytes(self.outbox),
            "ticks": int(self._ticks),
            "rounds": rounds,
        }


if __name__ == "__main__":  # pragma: no cover - 手工看一眼前台 state
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法：python -m remote.deliver_outbox <outbox 目录>")
    print(json.dumps(read_state(sys.argv[1]), ensure_ascii=False, indent=1, sort_keys=True))
