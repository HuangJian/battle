"""remote/result_upload.py —— 异步结果回传（plan/transfer-scheduling §4 P2.5）。

要解决的问题：串行 `claim → 下载 → rollout/PPO → post_result → 再领活` 里，**回传那一段
占着算力空转**。而下一份活的字节**已经在本地**（P2 软持有预取），上传只吃链路、不吃
CPU/GPU —— 两者是**不相交的资源**，天然可叠：

    PPO_A 结束:  后台回传 result A  ‖  立刻领 B 开算（零下载）

实测账（用户口径，双课程单 worker，plan §1.1）：一轮 `rollout/in/ppo/out` = 45/15/50/25 s。
预取只治 `in`（15s），而 `out` **25s 比它还大**、又恰好全程压在关键路径上 —— 这就是本模块
要摘掉的那一段。

三条纪律（都有用例钉住）：

1. **结果绝不能丢**：回传是**唯一**把最贵产物送出去的路径。所以队列有界、线程常驻到
   `close()`、退出前必须 `drain()`（`--once` / 空闲退出 / 热替换 / 异常路径全都要过）；
   入队超时（上传线程卡死）时**退回同步**回传而不是丢。`post_result` 自带的指数退避
   重试**原样复用**（不另写一套）。
2. **失败必须响亮**：主循环已经往前跑了，日志是**唯一**信号 —— 重试耗尽 / 确定性拒绝
   都要落一行带 jid 的行，并把 `pending` 与失败计数暴露给收尾行。
3. **记账不许撒谎**：`out` 的秒数照样如实记进本 job 的传输账（`post_result` 内部做的），
   但阶段行额外报 `overlap=` —— 判据是「`in + ppo + other ≈ wall`（关键路径），
   `out` 全部落在 `overlap` 里」，而不是把它从账上抹掉。

`mode="sync"` 逐字回退到改造前的行为（回传占关键路径、`overlap=0`），现场出问题时的
逃生口 = `--result-upload sync`。
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: 回传模式（CLI `--result-upload` 同字面量域）。`sync` = 旧行为（回传占关键路径）。
RESULT_UPLOAD_MODES = ("async", "sync")
#: 缺省 = 异步（本模块存在的全部理由）。
RESULT_UPLOAD_MODE_DEFAULT = "async"
#: 在飞上限。**不是随便取的**：双课程交错下同时在飞的回传最多 2 份（一门一份）。
#: 队列满了就**背压**（主循环等一等，而不是无界涨内存或丢结果）——结果是最贵的产物，
#: 「晚一点领下一份」比「丢一份」便宜得多。
RESULT_QUEUE_DEPTH_DEFAULT = 2
#: 入队等待上限（秒）：超时说明上传线程被卡住（链路挂了），那就改**同步**回传（绝不丢）。
RESULT_ENQUEUE_TIMEOUT_SEC = 300.0
#: 收尾 drain 上限（秒）：等所有在飞回传落定。一次回传的天然上界 = `post_result` 的
#: `attempts=5` 指数退避（1+2+4+8+16≈31s 退避）+ 5×`timeout=120s`，故 1800s 足够宽。
RESULT_DRAIN_TIMEOUT_SEC = 1800.0
#: 落定结果保留条数（给 `--once` 查自己那份、给收尾行报账）。
RESULT_OUTCOME_KEEP = 8


@dataclass
class UploadTask:
    """一份待回传的结果（入队的全部参数，上传线程只认它）。"""

    jid: str
    base_url: str
    token: str
    result: dict
    lease_token: str = ""
    claim_mode: str = "ok"
    #: 落定回调（`(jid, outcome) -> None`）——生产侧用它把本 job 的传输账**收在落定那一刻**
    #: （async 下 `out` 的账要到那时才记完，过早 flush 会把回传读成 0s）。
    on_settled: Callable[[str, Outcome], None] | None = None


@dataclass
class Outcome:
    """一次回传的落定结果。

    `ok` 的口径与改造前**逐字相同**：**没抛异常 = 成功**（200/201/409/403-backup 都算
    成功；只有重试耗尽或 4xx 确定性拒绝才是失败）。
    """

    status: int | None = None
    error: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error


class ResultUploader:
    """后台回传队列：`submit()` 立刻返回，POST 在专用线程里做（叠在下一份 job 上）。

    `mode="sync"` 时 `submit()` 就是**同步**发完再返回（逐字等价于改造前的行为）。
    """

    def __init__(
        self,
        *,
        upload: Callable[..., int],
        mode: str = RESULT_UPLOAD_MODE_DEFAULT,
        depth: int = RESULT_QUEUE_DEPTH_DEFAULT,
        drain_timeout: float = RESULT_DRAIN_TIMEOUT_SEC,
        enqueue_timeout: float = RESULT_ENQUEUE_TIMEOUT_SEC,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.mode = mode if mode in RESULT_UPLOAD_MODES else RESULT_UPLOAD_MODE_DEFAULT
        self._upload = upload
        self._drain_timeout = float(drain_timeout)
        self._enqueue_timeout = float(enqueue_timeout)
        self._log = log or (lambda _m: None)
        self._q: queue.Queue[UploadTask | None] = queue.Queue(maxsize=max(1, int(depth)))
        self._thread: threading.Thread | None = None
        self._closed = False
        self._cv = threading.Condition()
        self._pending = 0  # 已受理未落定（队列里 + 在飞）
        self._outcomes: dict[str, Outcome] = {}
        self._order: list[str] = []
        self.submitted = 0
        self.sync_uploads = 0

    # ------------------------------------------------------------------ 对外

    def submit(self, task: UploadTask) -> None:
        """交一份结果：async = 入队即返回；sync = 当场发完再返回。

        入队超时（上传线程卡死）**不丢结果**：退回同步回传并留一行痕。
        """
        self.submitted += 1
        if self.mode == "sync" or self._closed:
            self._send_now(task, why="" if self.mode == "sync" else "上传器已收尾")
            return
        self._ensure_thread()
        with self._cv:
            self._pending += 1
        try:
            self._q.put(task, timeout=self._enqueue_timeout)
        except queue.Full:
            with self._cv:
                self._pending -= 1
                self._cv.notify_all()
            self._send_now(task, why=f"队列 {self._enqueue_timeout:.0f}s 未能消化（上传线程卡住？）")

    def drain(self, timeout: float | None = None) -> bool:
        """等所有在飞回传落定。返回是否等干净（超时 = False，并响亮留痕）。"""
        t = self._drain_timeout if timeout is None else float(timeout)
        with self._cv:
            done = self._cv.wait_for(lambda: self._pending == 0, timeout=t)
        if not done:
            self._log(
                f"★ 回传收尾超时（{t:.0f}s）：仍有 {self._pending} 份结果在飞——"
                "此刻退出会丢结果（训练侧只会在租约过期后才看得出来）"
            )
        return done

    def close(self, timeout: float | None = None) -> bool:
        """收尾：drain（等落定）→ 停线程。**每条退出路径都必须过这里**。"""
        ok = self.drain(timeout)
        self._closed = True
        if self._thread is not None:
            try:
                self._q.put_nowait(None)  # drain 过 ⇒ 队列已空，哨兵必进得去
            except queue.Full:  # drain 超时（还有任务在队列里）：哨兵排在它们后面即可
                self._q.put(None, timeout=1.0)
            self._thread.join(timeout=5.0)
        return ok

    def outcome(self, jid: str) -> Outcome | None:
        with self._cv:
            return self._outcomes.get(jid)

    def stats(self) -> dict[str, Any]:
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
            }

    # ------------------------------------------------------------------ 内部

    def _ensure_thread(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="result-upload")
            self._thread.start()

    def _run(self) -> None:
        while True:
            task = self._q.get()
            if task is None:
                self._q.task_done()
                return
            status, error, secs = self._call(task)
            self._settle(task, status, error, secs)
            self._q.task_done()
            with self._cv:
                self._pending -= 1
                self._cv.notify_all()

    def _send_now(self, task: UploadTask, *, why: str) -> None:
        """同步发一份（sync 模式 / 上传器已收尾 / 入队超时的逃生口）。"""
        self.sync_uploads += 1
        if why:
            self._log(f"结果改**同步**回传 job {task.jid}：{why}（绝不丢）")
        status, error, secs = self._call(task)
        self._settle(task, status, error, secs)

    def _call(self, task: UploadTask) -> tuple[int | None, str, float]:
        """调 `post_result`（重试/409/403 语义全在它里面）→ `(status, error, 秒)`。"""
        t0 = time.time()
        try:
            status = self._upload(
                task.base_url,
                task.token,
                task.jid,
                task.result,
                lease_token=task.lease_token,
                mode=task.claim_mode,
            )
        except BaseException as e:  # 重试耗尽 / 确定性拒绝 / 传输崩：一律算失败
            err = f"{type(e).__name__}: {e}"
            # 响亮：主循环已经走远了，这一行是唯一信号（否则只有 25 分钟后的租约过期）。
            self._log(
                f"★ 结果回传失败 job {task.jid}（{time.time() - t0:.1f}s）：{err} — "
                "本份结果**没有**送达 hub（主循环已往前跑；训练侧要等租约过期才看得出来）"
            )
            return None, err, time.time() - t0
        return (int(status) if status is not None else None), "", time.time() - t0

    def _settle(self, task: UploadTask, status: int | None, error: str, seconds: float) -> None:
        out = Outcome(status=status, error=error, seconds=seconds)
        with self._cv:
            self._outcomes[task.jid] = out
            self._order.append(task.jid)
            while len(self._order) > RESULT_OUTCOME_KEEP:
                self._outcomes.pop(self._order.pop(0), None)
        if task.on_settled is not None:
            try:
                task.on_settled(task.jid, out)
            except BaseException as e:  # 回调（记账/日志）不许拖垮上传线程
                self._log(f"回传落定回调异常 job {task.jid}: {type(e).__name__}: {e}")
