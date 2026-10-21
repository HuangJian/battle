"""remote/upload_proc.py —— 结果回传的**进程模式**：`UploaderProcess` + 工厂 `make_uploader`。

> 设计：`plan/ppo-worker-transfer-isolation.plan.md`。子进程入口 = `remote/upload_worker.py`。

**为什么另立一个模块**（而不是塞进 `remote/result_upload.py`）：那个文件是**纯 stdlib 的线程原语**
（L0），被 `job_round` / `worker` / 测试直接 import；进程这一面要 `subprocess` / 环境 / 管道，
性质不同。与 `deliver_proc` / `deliver_worker` 的拆法逐字同形。

**它解决的通道**：PPO worker 的 `out`（结果回传）住在训练进程的后台线程里 —— `json.dumps`
（线上体 ~1.6MB）+ gzip + HTTP 都压在训练线程的 GIL 上。自主 worker 的补传腿 2026-10-07 已经
搬进子进程（`plan/offline-deliver-isolation`），PPO worker 的这条腿此前**没有**搬；本模块补上。

**三条纪律（与 `ResultUploader` 逐条对齐，缺一不可）**：

1. **绝不丢结果**：`submit()` 只排一张票；`close()` 先 drain，再把子进程收掉，最后把**还没有
   落定**的那几份在父侧**同步补发**（hub 侧 409/幂等兜底，重复投递是安全的）。子进程半路死掉
   同样走这条路（sticky 降级 ⇒ 进程内线程接管所有未落定的票）。
2. **失败必须响亮**：子进程起不来 / 中途退出 / 降级，各一行；单份失败的那行由子进程打
   （父侧原样转发，文案与 `ResultUploader._call` 同源）。
3. **记账不许撒谎**：子进程把 `(bytes, sec, wait)` 随落定行交回，父侧 `record_wire=` 注入的
   回调把它们重放进 `remote/wire.py` 的每 job 账 ⇒ job 行的 `out=` / `overlap=` 与线程模式同义。

**降级而不是重启**（与 `DelivererProcess` 的有意差异）：本类**一个会话只起一个子进程**；它死了
⇒ **sticky 降级**到进程内线程。理由：回传的票全在父侧内存里，重启子进程也得把这些票重新发一遍，
而「重新发」本来就是一次幂等 POST —— 降级到线程同样能发，且不必再付一次 boot 握手与管道重建。
代价（诚实记账）：降级那段回传重新与训练线程抢 GIL —— 所以它**响亮一行**，不是静默优化。
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from remote.child_proc import join_all, pump_lines, reap_child, repo_root, spawn_module
from remote.result_upload import (
    OUTCOME_PREFIX,
    RESULT_DRAIN_TIMEOUT_SEC,
    RESULT_ENQUEUE_TIMEOUT_SEC,
    RESULT_OUTCOME_KEEP,
    RESULT_QUEUE_DEPTH_DEFAULT,
    RESULT_UPLOAD_MODE_DEFAULT,
    RESULT_UPLOAD_MODES,
    Outcome,
    ResultUploader,
    UploaderLike,
    UploadTask,
)

#: 子进程入口模块名（测试注入一个不存在的名字即可模拟「在飞的代码树里没有它」）。
UPLOAD_WORKER_MODULE = "remote.upload_worker"
#: `Popen` 之后等 `boot ok` 的上限（秒）：超时/非零退出 ⇒ **降级**。
PROC_BOOT_TIMEOUT_SEC = 5.0
#: 收线时等子进程把手上那份发完的余量（秒）。它只发**一份**，而 `post_result` 自身
#: `attempts=5 × timeout=120s` 是它自己的内聚语义 —— 这里给的是「别再等了」的上界。
PROC_CLOSE_GRACE_SEC = 5.0


class UploaderProcess:
    """回传腿的**独立进程**代理（父侧；与 `ResultUploader` 同接口，见 `UploaderLike`）。

    父侧只做三件事：**写管道**（writer 线程；`submit()` 本身零网络、零大块序列化）、
    **读管道**（reader 线程：普通行 = 子进程日志 → 转发；`@@upload@@` = 落定行 → 记账 + 回调）、
    **有界收线**（`close()`：drain → 关 stdin → 等余量 → 收线程 → 残票同步补发）。
    """

    def __init__(
        self,
        *,
        upload: Callable[..., int],
        mode: str = "process",
        depth: int = RESULT_QUEUE_DEPTH_DEFAULT,
        drain_timeout: float = RESULT_DRAIN_TIMEOUT_SEC,
        enqueue_timeout: float = RESULT_ENQUEUE_TIMEOUT_SEC,
        log: Callable[[str], None] | None = None,
        python: str | None = None,
        worker_module: str = UPLOAD_WORKER_MODULE,
        spawn: Callable[[list[str]], subprocess.Popen] | None = None,
        record_wire: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.mode = mode if mode in RESULT_UPLOAD_MODES else "process"
        self._upload = upload
        self._drain_timeout = float(drain_timeout)
        self._enqueue_timeout = float(enqueue_timeout)
        self._log = log or (lambda _m: None)
        self._record_wire = record_wire or (lambda _j, _w: None)
        self._python = str(python or sys.executable)
        self._worker_module = str(worker_module)
        self._spawn_hook = spawn
        self._cwd = repo_root()
        #: 可重入（`_degrade` 会顺手收子进程，而收的过程可能被同一线程的 `submit()` 触发 ——
        #: 与 `deliver_proc` 2026-10-09 的自锁是同一类形状）。
        self._lock = threading.RLock()
        self._cv = threading.Condition()
        self._q: queue.Queue[UploadTask | None] = queue.Queue(maxsize=max(1, int(depth)))
        self._inflight: dict[str, UploadTask] = {}
        self._proc: subprocess.Popen | None = None
        self._writer: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._boot = threading.Event()
        self._closed = False
        self._degrade_reason = ""
        self._fallback: ResultUploader | None = None
        self._pending = 0  # 已受理未落定（队列里 + 在飞）
        self._outcomes: dict[str, Outcome] = {}
        self._order: list[str] = []
        self.submitted = 0
        self.sync_uploads = 0

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """拉起子进程 + 两条管道线程；**起不来是预期形态**（在飞的代码树里没有这个模块）。

        顺序是有讲究的：先 spawn（拿 `Popen`），再起 reader（`boot ok` 只可能被它读到），
        再等握手。反过来（先等握手再起 reader）在所有平台上都是死等。
        """
        if self._proc is not None or self._fallback is not None:
            return
        why = self._spawn()
        if why:
            self._degrade(why)
            return
        self._writer = threading.Thread(target=self._write_loop, name="upload-in", daemon=True)
        self._reader = threading.Thread(target=self._read_loop, name="upload-out", daemon=True)
        self._reader.start()
        self._writer.start()
        if not self._await_boot():
            return
        self._log(f"回传腿已独立（子进程 pid={self._proc.pid if self._proc else '?'}）")

    def _spawn(self) -> str:
        """拉起子进程。返回 `""` = `Popen` 成功（**握手另判**），否则是降级理由。"""
        proc, why = spawn_module(
            self._worker_module,
            python=self._python,
            cwd=self._cwd,
            spawn=self._spawn_hook,
        )
        if proc is not None:
            self._proc = proc
        self._boot = threading.Event()
        return why

    def _await_boot(self) -> bool:
        """等 `boot ok`（`Popen` 成功 ≠ 子进程活着 ≠ 能 import）。失败 ⇒ 降级并返回 False。"""
        deadline = time.monotonic() + PROC_BOOT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if self._boot.is_set():
                return True
            # reader 线程可能已经先判定「子进程退出」并降级（EOF）⇒ 这里必须立刻收手，
            # 否则白等满 5s（而 `_proc` 已被 downgrade 置 None，下面的 poll 永远判不出退出）。
            if self._fallback is not None:
                return False
            proc = self._proc
            if proc is None or proc.poll() is not None:
                break
            self._boot.wait(0.02)
        if self._fallback is not None:
            return False
        proc = self._proc
        code = None if proc is None else proc.poll()
        self._degrade(f"等 `boot ok` 失败（{PROC_BOOT_TIMEOUT_SEC:g}s，code={code}）")
        return False

    def _stop_child(self, *, join: bool = True) -> None:
        """收子进程（关 stdin → 等余量 → terminate → kill），然后（可选）收两条管道线程。"""
        proc = self._proc
        self._proc = None
        reap_child(proc, grace=PROC_CLOSE_GRACE_SEC)
        if join:
            join_all((self._writer, self._reader))
            self._writer = None
            self._reader = None

    def _degrade(self, why: str) -> None:
        """**sticky 降级**：子进程不可用 ⇒ 本段改用进程内线程（不再重试 spawn）。

        ⚠ 调用点可能在 reader / writer 线程里（EOF / 写失败）⇒ 这里 **join=False**：线程自己
        就是被收的对象，join 自己（或让持锁的一方等它）就是自锁。退出由 `close()` 收尾。
        """
        with self._lock:
            if self._fallback is not None:
                return
            self._fallback = ResultUploader(upload=self._upload, mode="async", log=self._log)
            self._degrade_reason = str(why)
            self.mode = "async"
            self._log(
                f"回传子进程不可用（{why}）——**降级为进程内线程**"
                "（本段不再重试；回传照常送达，但重新与训练线程抢 GIL）"
            )
            leftovers = self._take_unsettled()
            # 叫醒 writer（它在 `_q.get()` 上等）：不叫它就一直挂着，而 `close()` 的 join 会白等。
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
            self._stop_child(join=False)
            # 已排队/在飞的票全部交给线程（子进程可能已发、也可能没发 —— 重复投递是幂等的
            # （hub 侧 409/200），丢一份才是事故）。
            for task in leftovers:
                self._fallback.submit(task)
            with self._cv:
                self._pending = 0
                self._cv.notify_all()

    def _take_unsettled(self) -> list[UploadTask]:
        """取走还没落定的票（队列 + 在飞）。调用方必须持 `_lock`。"""
        leftovers: list[UploadTask] = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                leftovers.append(item)
        leftovers.extend(self._inflight.values())
        self._inflight.clear()
        return leftovers

    # ------------------------------------------------------------ 对外（与 ResultUploader 同接口）

    @property
    def off_critical_path(self) -> bool:
        return True

    def submit(self, task: UploadTask) -> None:
        """交一份结果：**只排票**（序列化与网络都在 writer 线程 / 子进程里）。"""
        fb = self._fallback
        if fb is not None:
            fb.submit(task)
            return
        self.submitted += 1
        if self._closed:
            self._send_now(task, why="上传器已收尾")
            return
        if self._proc is None:
            self.start()  # 调用方没显式 start（直接 new 的旧形状）
            fb = self._fallback
            if fb is not None:
                fb.submit(task)
                return
        with self._cv:
            self._pending += 1
        try:
            self._q.put(task, timeout=self._enqueue_timeout)
        except queue.Full:
            with self._cv:
                self._pending -= 1
                self._cv.notify_all()
            self._send_now(task, why=f"队列 {self._enqueue_timeout:.0f}s 未能消化（子进程卡住？）")
            return
        # 降级与入队的竞态：降级方先取走了队列、我方后放进来的这张票 ⇒ 在这里补一次交接。
        fb = self._fallback
        if fb is not None:
            self._hand_over_to(fb)

    def _hand_over_to(self, fb: ResultUploader) -> None:
        """把**此刻**还在队列/在飞里的票交给降级实现（与 `_degrade` 同形，幂等）。"""
        with self._lock:
            if self._fallback is not fb:
                return
            leftovers = self._take_unsettled()
        for task in leftovers:
            fb.submit(task)
        with self._cv:
            self._pending = 0
            self._cv.notify_all()

    def drain(self, timeout: float | None = None) -> bool:
        fb = self._fallback
        if fb is not None:
            return fb.drain(timeout)
        t = self._drain_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + t
        with self._cv:
            done = self._cv.wait_for(lambda: self._pending == 0, timeout=t)
        # ★ 降级可能发生在等的同时：`_degrade` 会先把票交给回退实现（`fb.submit`）、再把
        # `_pending` 归零 —— 所以「本对象 pending==0」并不等于「票已发完」。这里必须把剩下的
        # 预算交给回退实现继续等（否则 `drain()` 会在回传真正发生前就报「干净」）。
        fb = self._fallback
        if fb is not None:
            return fb.drain(max(0.0, deadline - time.monotonic()))
        if not done:
            self._log(
                f"★ 回传收尾超时（{t:.0f}s）：仍有 {self._pending} 份结果在飞——"
                "此刻退出会丢结果（训练侧只会在租约过期后才看得出来）"
            )
        return done

    def close(self, timeout: float | None = None) -> bool:
        """收尾：drain → 收子进程 → **残票同步补发**。每条退出路径都必须过这里。"""
        t = self._drain_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + t
        fb = self._fallback
        if fb is not None:
            ok = fb.close(max(0.0, deadline - time.monotonic()))
            self._stop_child()
            return ok
        ok = self.drain(timeout)
        self._closed = True
        # 等的过程中降级了（子进程挂了）：剩下的事全归回退实现 —— 它才有那几张票。
        fb = self._fallback
        if fb is not None:
            ok = fb.close(max(0.0, deadline - time.monotonic())) and ok
            self._stop_child()
            return ok

        try:
            self._q.put_nowait(None)  # 收线哨兵：writer 见到它即退出（队列此刻已空）
        except queue.Full:
            pass
        if self._writer is not None:
            self._writer.join(timeout=2.0)
        self._stop_child()
        with self._lock:
            leftover = list(self._inflight.values())
            self._inflight.clear()
        for task in leftover:
            self._log(f"回传子进程退出时仍有未落定 job {task.jid} —— 父侧同步补发（幂等）")
            self._send_now(task, why="")
        with self._cv:
            ok = ok and self._pending == 0
        return ok

    def outcome(self, jid: str) -> Outcome | None:
        fb = self._fallback
        if fb is not None:
            return fb.outcome(jid)
        with self._cv:
            return self._outcomes.get(jid)

    def stats(self) -> dict[str, Any]:
        fb = self._fallback
        if fb is not None:
            st = dict(fb.stats())
            st.update({"mode": st.get("mode", "async"), "degrade_reason": self._degrade_reason})
            return st
        with self._cv:
            kept = list(self._outcomes.values())
            return {
                "mode": self.mode,
                "submitted": self.submitted,
                "sync_fallback": self.sync_uploads,
                "pending": self._pending,
                "ok": sum(1 for o in kept if o.ok),
                "failed": sum(1 for o in kept if not o.ok),
                "kept": len(self._order),
                "degrade_reason": self._degrade_reason,
            }

    # ------------------------------------------------------------ 内部

    def _write_loop(self) -> None:
        """把排队的票写成 JSON 行。大块序列化与阻塞写都发生在这里（**不在训练线程**）。"""
        while True:
            task = self._q.get()
            if task is None:
                return
            proc = self._proc
            # ★ 入账与「是否已降级」必须在**同一把锁**里判：`_q.get()` 与 `_inflight[...]` 之间
            # 存在窗口（降级方在此期间 `_take_unsettled()` 拿到的是空集 ⇒ `pending` 归零、
            # 这张票就再也没人发）。两个顺序都在锁内互斥后，无论谁先，票都有主。
            with self._lock:
                if self._fallback is not None:
                    self._fallback.submit(task)
                    continue
                self._inflight[task.jid] = task
            if proc is None or proc.stdin is None:
                # 子进程已经没了（降级/收线）：这张票交回去，交不成就在父侧同步发。
                self._degrade("子进程已不在（writer 没有可写的管道）")
                return
            try:
                line = json.dumps(
                    {
                        "jid": task.jid,
                        "base_url": task.base_url,
                        "token": task.token,
                        "result": task.result,
                        "lease_token": task.lease_token,
                        "claim_mode": task.claim_mode,
                        "worker_id": task.worker_id,
                    },
                    ensure_ascii=False,
                )
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except Exception as e:
                fb = self._fallback
                if fb is not None:  # 已经降级（这张票还在 `_inflight`，由降级方取走）
                    return
                self._degrade(f"写子进程失败（{type(e).__name__}: {e}）")
                return

    def _read_loop(self) -> None:
        """读子进程输出：普通行 = 日志（转发）；`@@upload@@` = 落定行。"""
        proc = self._proc

        def _on_line(s: str) -> None:
            if s.startswith(OUTCOME_PREFIX):
                self._on_outcome(s[len(OUTCOME_PREFIX) :])
                return
            if "boot ok" in s:
                self._boot.set()
            self._log(s)

        pump_lines(proc.stdout if proc is not None else None, _on_line)
        # EOF：子进程没了。收线过程中（`_closed`）由 `close()` 负责残票，不在这里降级。
        if not self._closed and self._fallback is None:
            code = None if proc is None else proc.poll()
            self._degrade(f"子进程已退出（code={code}）")

    def _on_outcome(self, payload: str) -> None:
        """一条落定行：**先记账再落定**（job 行的 `out=` 依赖它）。"""
        try:
            rec = json.loads(payload)
        except ValueError as e:
            self._log(f"WARN 回传落定行解析失败（忽略）：{type(e).__name__}: {e}")
            return
        if not isinstance(rec, dict):
            return
        jid = str(rec.get("jid") or "")
        wire = rec.get("wire")
        if isinstance(wire, dict):
            try:
                self._record_wire(jid, wire)
            except Exception as e:  # 记账坏掉不许拖垮落定（否则 pending 永不清零）
                self._log(f"WARN 回传传输账重放失败 job {jid}: {type(e).__name__}: {e}")
        with self._lock:
            task = self._inflight.pop(jid, None)
        status = rec.get("status")
        out = Outcome(
            status=int(status) if isinstance(status, int) else None,
            error=str(rec.get("error") or ""),
            seconds=float(rec.get("seconds") or 0.0),
        )
        self._settle(jid, out, task)
        with self._cv:
            self._pending = max(0, self._pending - 1)
            self._cv.notify_all()

    def _send_now(self, task: UploadTask, *, why: str) -> None:
        """同步发一份（收尾后 / 队列满 / 残票补发）。**绝不丢**。"""
        self.sync_uploads += 1
        if why:
            self._log(f"结果改**同步**回传 job {task.jid}：{why}（绝不丢）")
        with self._cv:
            self._pending += 1
        t0 = time.time()
        status: int | None = None
        error = ""
        try:
            status = int(
                self._upload(
                    task.base_url,
                    task.token,
                    task.jid,
                    task.result,
                    lease_token=task.lease_token,
                    mode=task.claim_mode,
                    worker_id=task.worker_id,
                )
            )
        except BaseException as e:  # 重试耗尽 / 确定性拒绝 / 传输崩：一律算失败
            error = f"{type(e).__name__}: {e}"
            self._log(
                f"★ 结果回传失败 job {task.jid}（{time.time() - t0:.1f}s）：{error} — "
                "本份结果**没有**送达 hub（主循环已往前跑；训练侧要等租约过期才看得出来）"
            )
        self._settle(
            task.jid, Outcome(status=status, error=error, seconds=time.time() - t0), task
        )
        with self._cv:
            self._pending = max(0, self._pending - 1)
            self._cv.notify_all()

    def _settle(self, jid: str, out: Outcome, task: UploadTask | None) -> None:
        """落定账 + 回调（保留条数与回调语义与 `ResultUploader._settle` 逐字同义）。"""
        with self._cv:
            self._outcomes[jid] = out
            self._order.append(jid)
            while len(self._order) > RESULT_OUTCOME_KEEP:
                self._outcomes.pop(self._order.pop(0), None)
        if task is not None and task.on_settled is not None:
            try:
                task.on_settled(jid, out)
            except BaseException as e:  # 回调（记账/日志）不许拖垮回传线程
                self._log(f"回传落定回调异常 job {jid}: {type(e).__name__}: {e}")


def make_uploader(
    *,
    upload: Callable[..., int],
    mode: str = "",
    depth: int = RESULT_QUEUE_DEPTH_DEFAULT,
    drain_timeout: float = RESULT_DRAIN_TIMEOUT_SEC,
    enqueue_timeout: float = RESULT_ENQUEUE_TIMEOUT_SEC,
    log: Callable[[str], None] | None = None,
    record_wire: Callable[[str, dict], None] | None = None,
    python: str | None = None,
    worker_module: str = UPLOAD_WORKER_MODULE,
    spawn: Callable[[list[str]], subprocess.Popen] | None = None,
) -> UploaderLike:
    """按模式造回传器：`process`（独立子进程）/ `async`（进程内线程）/ `sync`（老行为）。

    **缺省仍是 `async`**（见 `RESULT_UPLOAD_MODE_DEFAULT` 的理由）：进程模式由入口显式开
    （云机 `remote/notebook_runtime.py::run_pull_worker` 带 `--result-upload process`）。
    未知值 ⇒ 响亮一行 + 按 `async`（绝不静默落到 `sync`：那会把回传重新压回关键路径）。
    """
    kind = str(mode or "").strip().lower() or RESULT_UPLOAD_MODE_DEFAULT
    if kind not in RESULT_UPLOAD_MODES:
        if log is not None:
            log(f"未知的回传模式 {kind!r} —— 按 async 走（可选：{'/'.join(RESULT_UPLOAD_MODES)}）")
        kind = "async"
    if kind != "process":
        return ResultUploader(
            upload=upload,
            mode=kind,
            depth=depth,
            drain_timeout=drain_timeout,
            enqueue_timeout=enqueue_timeout,
            log=log,
        )
    up = UploaderProcess(
        upload=upload,
        mode="process",
        depth=depth,
        drain_timeout=drain_timeout,
        enqueue_timeout=enqueue_timeout,
        log=log,
        python=python,
        worker_module=worker_module,
        spawn=spawn,
        record_wire=record_wire,
    )
    up.start()
    return up
