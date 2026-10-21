"""remote/payload_proc.py —— 下行腿（预取）的**进程模式**：`PayloadProcess` + 工厂 `make_prefetcher`。

> 设计：`plan/ppo-worker-transfer-isolation.plan.md`。子进程入口 = `remote/payload_worker.py`。

**它解决的通道**：预取的 `peek` + `download_payload` + `unpack_payload` 此前住在训练进程的
后台线程里（`job_round._prefetch_fill`）。它**刻意**与上一轮 PPO 重叠（`prefetch.py` 头部 §0），
而重叠的代价是抢同一个 GIL、同一颗核 —— 单份 payload 解包 300MB 级（`lzma`，单线程）就是
白压训练线程的那一段时间。回传腿 2026-10-20 已搬进子进程（`upload_proc`），本条把**另一条腿**
搬出去：重叠保持、竞争消失。

**为什么另立一个模块**（而不是塞进 `remote/prefetch.py`）：那个文件是**纯内存/纯磁盘**的
暂存实现（L0），被 `job_round` / 测试直接 import；进程这一面要 `subprocess` / 管道 / 应答兑现，
性质不同。与 `deliver_proc` / `upload_proc` 的拆法逐字同形。

**形态**（父侧）：一个会话一个子进程（`python -m remote.payload_worker`），命令行走 stdin、
应答/日志走 stdout。父侧只做四件事：

  · **按轮下 `fill` / `stopfill`**（与今天的填充线程同生命周期：job 结束就停，绝不留常驻填充）；
  · **`take`/`drop`/`release`/`prune` 走请求-应答**：暂存区的**唯一写者**是子进程，父侧不许
    自己动那些目录（两个进程同时 prune 会把刚下好的暂存删掉）；
  · **转发子进程日志**（普通行原样进 worker 日志）；
  · **sticky 降级**：子进程起不来/中途死 ⇒ 本段改回**进程内** `PrefetchStore` + 填充线程
    （旧行为，响亮一行）。预取是提前量 —— 它坏掉的唯一后果是「少一份命中」，训练永不受影响。

**`take` 的字节形状为什么走 base64**：`PrefetchStore.take()` 对未解包的候选返回整份 zip
（3–10MB）。子进程把字节写成文件再让父侧读会引入「父侧读、子进程 prune」的竞态（读一半被删
= 莫名其妙的 miss）；base64 走管道则是一次无竞态的搬运，代价（~10–30ms 解码，只发生在命中时）
远小于它替下来的那次 300MB 解包。

**跨进程 P1 协同**：`note_p1()` / `{"op":"p1"}` —— 父进程的关键下载/回传（P1）在跑时，子进程
的 P2 必须**不开工 + 当场退出**（`bulk_sched.foreign_p1`）。没有这条，搬进程就等于让预取从
「可被 P1 挤走」退化成「和 P1 抢带宽」（§2.2 的三条纪律里最难自己发现的一条）。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote.child_proc import join_all, pump_lines, reap_child, repo_root, spawn_module
from remote.prefetch import (
    PREFETCH_BUDGET_BYTES_DEFAULT,
    PREFETCH_LINE_PREFIX,
    PREFETCH_MODE_DEFAULT,
    PREFETCH_MODE_ENV,
    PREFETCH_MODES,
    PrefetchStore,
)

#: 子进程入口模块名（测试注入一个不存在的名字即可模拟「在飞的代码树里没有它」）。
PAYLOAD_WORKER_MODULE = "remote.payload_worker"
#: `Popen` 之后等 `init` 应答的上限（秒）。比回传腿的 5s 宽：这里要 import `job_round`
#: （连带 `download` / `numpy`），冷启动在慢云机上实测秒级；握手只发生在会话开头。
PROC_BOOT_TIMEOUT_SEC = 15.0
#: 控制命令（`fill`/`drop`/`release`/`prune`/`p1`）的应答上限：命令线程永远是空闲的，
#: 应答只可能被「子进程整个卡死」拖住。
PROC_CONTROL_TIMEOUT_SEC = 5.0
#: `take` 的应答上限：只能被卡死的子进程拖住；超时按**未命中**处理（走关键下载，绝不卡训练）。
PROC_TAKE_TIMEOUT_SEC = 30.0
#: `stopfill` 的应答上限 = 子进程侧的 join 预算（30s）+ 余量（它等一轮下载自然退出）。
PROC_STOP_TIMEOUT_SEC = 40.0
#: 收线时等子进程退出的余量（秒）。
PROC_CLOSE_GRACE_SEC = 5.0


class PayloadProcess:
    """下行腿的**独立进程**代理（父侧）。

    **接口**：进程模式与 `PrefetchStore` 的「宿主用得到的那几个方法」同名同义
    （`take` / `drop` / `release_handoff` / `prune`），另外加一轮的 `start_fill` / `stop_fill`
    与 `close` —— `job_round` 只看 `fills_out_of_process` 一个判据就够。
    降级之后本类**逐字像** `PrefetchStore`（`__getattr__` 转发给进程内那份），所以
    `job_round` 的降级分支不需要任何特判。
    """

    def __init__(
        self,
        work_dir: str | os.PathLike[str],
        *,
        budget_bytes: int = PREFETCH_BUDGET_BYTES_DEFAULT,
        staged_max: int = 0,
        log: Callable[[str], None] | None = None,
        python: str | None = None,
        worker_module: str = PAYLOAD_WORKER_MODULE,
        spawn: Callable[[list[str]], subprocess.Popen] | None = None,
    ) -> None:
        self._work_dir = str(work_dir)
        self._budget_bytes = int(budget_bytes)
        self._staged_max = int(staged_max)
        self._log = log or (lambda _m: None)
        self._python = str(python or sys.executable)
        self._worker_module = str(worker_module)
        self._spawn_hook = spawn
        self._cwd = repo_root()
        #: 可重入：`_degrade` 会顺手收子进程，而收的过程可能由同一线程的 `_request` 触发。
        self._lock = threading.RLock()
        #: 命令行写入互斥（多个线程都会发命令：训练线程 take、`slot()` 的 p1 广播、收线线程）。
        self._wlock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._req = 0
        #: 在等的应答：`id -> (Event, box)`；`box` 是可变小字典（`_on_reply` 往里写 `rep`）。
        #: 用可变 box 而不是换元组：超时与应答可能同时发生，重插/删表的竞态会漏条目。
        self._waiters: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        #: 收尾中（读者线程据此不把「子进程正常退出」当降级）。
        self._closed = False
        #: **不再写管道**（`close()` 发完最后的命令后置位）：之后来的 `note_p1` 一类命令
        #: 只可能来自还没收干净的旁路线程，它们不许再碰已经关掉的管道（否则会触发降级）。
        self._stopping = False
        self._degrade_reason = ""
        self._local: PrefetchStore | None = None
        #: 子进程 pid（状态面/排障；降级后仍是最后那个 pid）。
        self.pid: int | None = None
        #: 诊断读数：成功发出的命令数（降级后仍保留最后的值）。
        self.commands = 0

    # ------------------------------------------------------------ 生命周期

    @property
    def fills_out_of_process(self) -> bool:
        """下行腿此刻是否真在子进程里（降级后 False ⇒ `job_round` 走旧填充线程）。"""
        return self._local is None

    def start(self) -> None:
        """拉起子进程并完成 `init` 握手；**起不来是预期形态**（在飞的代码树里没有它）。"""
        if self._proc is not None or self._local is not None:
            return
        why = self._spawn()
        if why:
            self._degrade(why)
            return
        proc = self._proc
        self.pid = proc.pid if proc is not None else None
        self._reader = threading.Thread(target=self._read_loop, name="payload-out", daemon=True)
        self._reader.start()
        # `init` 既是建 store 也是握手（`boot ok` 只是一行给人读的日志）：应答到了 = 依赖链
        # 真能 import + `prefetch/` 的孤儿清扫跑完了。
        task = (
            ("id", self._next_id()),
            ("op", "init"),
            ("work_dir", self._work_dir),
            ("budget_bytes", self._budget_bytes),
            ("staged_max", self._staged_max),
        )
        cmd = dict(task)
        rep = self._request(cmd, timeout=PROC_BOOT_TIMEOUT_SEC)
        if rep is None or not rep.get("ok"):
            self._degrade(f"`init` 未兑现（{rep!r}）")
            return
        self._log(
            f"下行腿已独立（子进程 pid={self.pid}，清扫 {int(rep.get('pruned') or 0)} 个孤儿暂存）"
        )

    def _spawn(self) -> str:
        """拉起子进程。返回 `""` = `Popen` 成功（握手另判），否则是降级理由。"""
        proc, why = spawn_module(
            self._worker_module,
            python=self._python,
            cwd=self._cwd,
            spawn=self._spawn_hook,
        )
        if proc is not None:
            self._proc = proc
        return why

    def _stop_child(self, *, join: bool = True) -> None:
        """收子进程（关 stdin → 等余量 → terminate → kill），然后（可选）收读者线程。"""
        proc = self._proc
        self._proc = None
        reap_child(proc, grace=PROC_CLOSE_GRACE_SEC)
        if join:
            join_all((self._reader,))
            self._reader = None

    def close(self) -> None:
        """收尾：停填充 → 关子进程。**不抛**（退出路径上调用）。

        顺序：先置 `_closed`（读者线程别把「子进程正常退出」误读成降级）→ 发 `stopfill` /
        `shutdown` 并等应答 → 才置 `_stopping`（管道要关了）→ 收子进程。
        """
        if self._local is not None:
            return
        self._closed = True
        if self._proc is not None:
            self.stop_fill(quiet=True)
            self._request({"op": "shutdown"}, timeout=PROC_STOP_TIMEOUT_SEC)
        self._stopping = True
        self._stop_child()

    def _degrade(self, why: str) -> None:
        """**sticky 降级**：子进程不可用 ⇒ 本段改用进程内暂存 + 填充线程（不再重试 spawn）。

        ⚠ 调用点可能在 reader / writer 线程里（EOF / 写失败）⇒ `_stop_child(join=False)`：
        线程自己就是被收的对象，join 自己就是自锁。
        """
        with self._lock:
            if self._local is not None:
                return
            self._degrade_reason = str(why)
            self._local = PrefetchStore(
                Path(self._work_dir),
                budget_bytes=self._budget_bytes,
                staged_max=self._staged_max,
                log=self._log,
            )
            self._log(
                f"下行腿子进程不可用（{why}）——**降级为进程内填充线程**"
                "（本段不再重试；预取照常，但重新与训练线程抢 GIL）"
            )
            # 子进程可能已经下好几份 —— 那几份没有注册表（暂存区是软持有），只能当孤儿收掉，
            # 否则它们会一直占着盘直到下一次会话。
            try:
                dropped = self._local.prune()
            except Exception:
                dropped = 0
            if dropped:
                self._log(f"降级：清掉子进程留下的 {dropped} 份无主暂存")
            # 在飞的应答全部按「失败」兑现，别让 `take` 白等到超时（box 保持空 ⇒ rep=None）。
            expired = list(self._waiters.values())
            self._waiters.clear()
        for ev, _box in expired:
            ev.set()
        self._stop_child(join=False)

    # ------------------------------------------------------------ 一轮的填充

    def start_fill(
        self,
        *,
        base_url: str,
        token: str,
        worker_id: str = "",
        role: str = "online",
        depth: int = 0,
        unpack_n: int = 0,
        skip: set[str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> bool:
        """下 `fill`（起一轮填充）。返回 False = 没交出去（已降级 / 子进程没了）。

        `log` 只在降级分支用得上 —— 进程模式的日志由子进程自己写、父侧原样转发。
        """
        if self._local is not None:
            return False
        rep = self._request(
            {
                "op": "fill",
                "base_url": base_url,
                "token": token,
                "worker_id": worker_id,
                "role": role,
                "depth": int(depth),
                "unpack_n": int(unpack_n),
                "skip": sorted(str(x) for x in (skip or set())),
            },
            timeout=PROC_CONTROL_TIMEOUT_SEC,
        )
        if rep is None or not rep.get("ok"):
            self._degrade(f"`fill` 未兑现（{rep!r}）")
            return False
        return True

    def stop_fill(self, *, quiet: bool = False) -> None:
        """停本轮填充：等子进程把那一轮收干净（与旧 `pf_thread.join(timeout=30.0)` 同预算）。"""
        if self._local is not None or self._proc is None:
            return
        rep = self._request({"op": "stopfill"}, timeout=PROC_STOP_TIMEOUT_SEC)
        if rep is None:
            if not quiet:
                self._log(
                    f"★ 预取子进程 {PROC_STOP_TIMEOUT_SEC:.0f}s 内没确认停止——"
                    "下一轮 `fill` 会再等一次（两轮填充器重叠只是白下一次，不是错误）"
                )
            return
        if not rep.get("joined", True) and not quiet:
            self._log("预取填充线程未在预算内退出（仍在跑最后一轮下载）——本轮不追")

    # ------------------------------------------------------------ 与 `PrefetchStore` 同名的方法

    def take(self, jid: str) -> dict | None:
        """取一份命中项（**唯一写者**是子进程：这里只是请求）。超时/子进程不在 ⇒ 未命中。"""
        local = self._local
        if local is not None:
            return local.take(jid)
        rep = self._request({"op": "take", "jid": str(jid)}, timeout=PROC_TAKE_TIMEOUT_SEC)
        if rep is None or not rep.get("ok"):
            self._log(
                f"★ 预取子进程没交出 job {jid} 的命中项（{rep!r}）——按未命中处理，走关键下载"
            )
            return None
        rec = rep.get("rec")
        if not isinstance(rec, dict):
            return None
        out = dict(rec)
        b64 = out.pop("payload_b64", None)
        if isinstance(b64, str):
            try:
                out["payload_zip"] = base64.b64decode(b64)
            except Exception as e:  # 管道被截断（子进程半路死）：当未命中，绝不喂坏字节
                self._log(f"★ 命中项解码失败 job {jid}（{type(e).__name__}: {e}）——按未命中处理")
                return None
        return out

    def drop(self, jid: str) -> None:
        """丢弃一份候选（丢弃不是失败；命令是**同步**的，后续 `take` 必看得到结果）。"""
        local = self._local
        if local is not None:
            local.drop(jid)
            return
        self._request({"op": "drop", "jid": str(jid)}, timeout=PROC_CONTROL_TIMEOUT_SEC)

    def release_handoff(self, jid: str | None = None) -> None:
        """放掉「已交棒但没被搬走」的暂存（job 收尾；幂等）。"""
        local = self._local
        if local is not None:
            local.release_handoff(jid)
            return
        self._request({"op": "release", "jid": str(jid or "")}, timeout=PROC_CONTROL_TIMEOUT_SEC)

    def prune(self) -> None:
        """清孤儿 / 超量淘汰（`init` 时子进程已经跑过一次；这里给显式调用方用）。"""
        local = self._local
        if local is not None:
            local.prune()
            return
        self._request({"op": "prune"}, timeout=PROC_CONTROL_TIMEOUT_SEC)

    # ------------------------------------------------------------ 跨进程 P1 协同

    def note_p1(self, waiting: bool) -> None:
        """父进程的 P1 进出等待 ⇒ 告诉子进程（`bulk_sched.set_p1_observer` 的回调）。

        只写不等：这条回调挂在 `BulkScheduler.slot()` 上，**绝不能**在关键下载里等一个应答
        （子进程卡死时也得让 P1 走）。写失败 = 子进程没了 ⇒ 由 reader 线程的 EOF 去降级。
        降级后它是**空操作**（P2 已住回本进程，`slot()` 自己就是那套口径）。
        """
        if self._local is not None:
            # 降级后 P2 又住回**本进程** ⇒ 什么都不用做：`slot()` 自己的 P1 挡/抢就是那个口径，
            # 再走一遍广播就把同一个 P1 记了两次（计数会漏配）。这条分支是**有意义的不做**。
            return
        self._send({"op": "p1", "waiting": bool(waiting)})

    # ------------------------------------------------------------ 观测

    def status(self) -> dict[str, Any]:
        local = self._local
        return {
            "mode": "process" if local is None else "thread",
            "degraded": local is not None,
            "degrade_reason": self._degrade_reason,
            "pid": self.pid,
            "commands": self.commands,
            "work_dir": self._work_dir,
            "alive": bool(self._proc is not None and self._proc.poll() is None),
        }

    def __getattr__(self, name: str) -> Any:
        """降级后本类必须**逐字像** `PrefetchStore`（填充线程/宿主都按那个形状调）。

        `__getattr__` 只兜「本类没有的名字」（`store` / `held` / `begin` / `end` /
        `note_preempt_wasted` / `stats` / `snapshot` / `has` …）—— 它们全都只有进程内那份才有
        意义。未降级时访问到这些名字是**调用方的错**（进程模式不该在父侧读暂存的内部账），
        所以这里照常抛 `AttributeError`（响亮）。
        """
        local = self.__dict__.get("_local")
        if local is None:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
                "（进程模式下暂存账在子进程里；降级后才可用）"
            )
        return getattr(local, name)

    # ------------------------------------------------------------ 请求-应答

    def _next_id(self) -> int:
        with self._lock:
            self._req += 1
            return self._req

    def _send(self, cmd: dict[str, Any]) -> bool:
        """写一行命令（不等应答）。返回是否写出去了。"""
        proc = self._proc
        if proc is None or proc.stdin is None or self._stopping:
            return False
        try:
            line = json.dumps(cmd, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            self._log(f"WARN 预取命令序列化失败（{type(e).__name__}: {e}）")
            return False
        try:
            with self._wlock:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            self.commands += 1
            return True
        except Exception as e:
            if self._local is None and not self._stopping:
                self._degrade(f"写预取子进程失败（{type(e).__name__}: {e}）")
            return False

    def _request(self, cmd: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
        """发一条命令并等它的应答行（按 `id` 兑现）。超时/无子进程 ⇒ None。"""
        if self._proc is None or self._stopping:
            return None
        rid = int(cmd.get("id") or self._next_id())
        cmd["id"] = rid
        ev = threading.Event()
        box: dict[str, Any] = {}
        with self._lock:
            self._waiters[rid] = (ev, box)
        if not self._send(cmd):
            with self._lock:
                self._waiters.pop(rid, None)
            return None
        if not ev.wait(timeout):
            with self._lock:
                self._waiters.pop(rid, None)
            return None
        with self._lock:
            self._waiters.pop(rid, None)
        return box.get("rep")

    def _read_loop(self) -> None:
        """读子进程输出：普通行 = 日志（转发）；`@@pf@@` = 应答行（按 id 兑现）。"""
        proc = self._proc

        def _on_line(s: str) -> None:
            if s.startswith(PREFETCH_LINE_PREFIX):
                self._on_reply(s[len(PREFETCH_LINE_PREFIX) :])
                return
            self._log(s)

        pump_lines(proc.stdout if proc is not None else None, _on_line)
        # EOF：子进程没了。收线过程中（`_closed`）由 `close()` 负责，不在这里降级。
        if not self._closed and self._local is None:
            code = None if proc is None else proc.poll()
            self._degrade(f"子进程已退出（code={code}）")

    def _on_reply(self, payload: str) -> None:
        try:
            rep = json.loads(payload)
        except ValueError as e:
            self._log(f"WARN 预取应答行解析失败（忽略）：{type(e).__name__}: {e}")
            return
        if not isinstance(rep, dict):
            return
        rid = rep.get("id")
        if not isinstance(rid, int):
            self._log(f"WARN 预取应答行没有 id（忽略）：{payload[:120]}")
            return
        with self._lock:
            item = self._waiters.get(rid)
        if item is None:  # 已超时放弃的那条：没人等，丢（子进程不会重发）
            return
        ev, box = item
        box["rep"] = rep
        ev.set()


def make_prefetcher(
    work_dir: str | os.PathLike[str],
    *,
    mode: str = "",
    budget_bytes: int = PREFETCH_BUDGET_BYTES_DEFAULT,
    staged_max: int = 0,
    log: Callable[[str], None] | None = None,
    python: str | None = None,
    worker_module: str = PAYLOAD_WORKER_MODULE,
    spawn: Callable[[list[str]], subprocess.Popen] | None = None,
) -> PrefetchStore | PayloadProcess:
    """按模式造预取器：`process`（独立子进程）/ `thread`（进程内填充，旧行为/逃生口）。

    解析顺序与补传腿同形：显式参数 > `$NN_PREFETCH_MODE` > `process`（`PREFETCH_MODE_DEFAULT`）。
    未知值 ⇒ 响亮一行 + 按 `thread`（**绝不**静默按 `process`：老包里的 `python -m` 起不来，
    降级虽然兜得住，但那是白试一次）。
    """
    kind = (
        str(mode or "").strip().lower()
        or os.environ.get(PREFETCH_MODE_ENV, "").strip().lower()
        or PREFETCH_MODE_DEFAULT
    )
    if kind not in PREFETCH_MODES:
        if log is not None:
            log(f"未知的预取模式 {kind!r} —— 按 thread 走（可选：{'/'.join(PREFETCH_MODES)}）")
        kind = "thread"
    if kind != "process":
        return PrefetchStore(
            Path(work_dir), budget_bytes=budget_bytes, staged_max=staged_max, log=log
        )
    pf = PayloadProcess(
        work_dir,
        budget_bytes=budget_bytes,
        staged_max=staged_max,
        log=log,
        python=python,
        worker_module=worker_module,
        spawn=spawn,
    )
    pf.start()
    return pf
