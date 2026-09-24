"""remote/hub/store_scheduling.py — 调度优先级：**谁在做 / 谁掉队 / 谁该切走**。

`_JobStore` 的六个域混入之一（S4 第十四刀）。事实源是三张表：

| 表 | 含义 | 谁写 |
|---|---|---|
| `_claimed` | 有人**承诺在跑**（exclusive claim 成功 / `/start`） | 认领路径 · `/start` |
| `_computing` | **PPO 真正启动**（掉队阈值的**唯一**时基，R2-C1） | `/start` |
| `_ready` | 已算完、尚未回传成功 | `/ready` |

`_epoch` 是三张表的版本号（任一变化即 +1），只服务 highest 的唯一性闸。为什么不能「只看
租约」：备份副本**不设租约**，只看租约就判不出「别处在做」⇒ 所有 job 都被判成 highest
⇒ 多张卡同抢一份（= race 换个名字）。

## 依赖方向

`store_scheduling → {common.protocol}`（向下）；跨域调用只有 `self._job_dir`（住
`store_ledger`）。**不 import 任何兄弟混入**。
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from common.protocol import (
    FAIL_NAME,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    PRIORITY_NONE,
    job_priority,
)


class SchedulingMixin:
    """域混入：见模块头部。"""

    # ---- 由组合类 / 兄弟簇提供（混入只见 `self`，实现不在本模块）----
    #: 全 store **唯一**的那把锁（`_AuthGuard.__init__` 里建；见 `hub_server._JobStore` 头部）
    _lock: Lock
    #: 时钟（`now_fn` 注入点，住 `_AuthGuard`）
    _now: Any
    #: 兄弟簇 `store_leases` 拥有的状态（掉队判据要看「持有者是谁」）
    _last_heartbeat: dict[str, float]
    _lease_workers: dict[str, str]
    #: 兄弟簇 `store_ledger` 的方法
    _job_dir: Any

    def _init_scheduling(self) -> None:
        # ---- 调度优先级（2026-09-22，plan/transfer-scheduling §2.1/§2.3）----
        #: job_id -> {"worker", "at"}：**有人承诺在跑**（exclusive claim 成功/`/start` 时写）。
        #: 与 `_leases` 的分工：租约管「别人现在不能领」，`_claimed` 管「有人在做这件事」
        #: （优先级表中档的输入）。为啥不只看租约：备份副本**不设租约**，只看租约就判不出
        #: 「别处在做」⇒ 所有 job 都会被判成 highest ⇒ 多张卡同抢一份（= race 换个名字）。
        self._claimed: dict[str, dict] = {}
        #: job_id -> {"worker", "at"}：**PPO 真正启动**（`POST /jobs/{id}/start` 打点）。
        #: 掉队阈值的**唯一**时基（R2-C1）：claim 之后还有下载 + 解包，拿 claim 起算会把
        #: 「下载慢」误判成「算得慢」，反而多开备份把本来就慢的链路压得更死。
        self._computing: dict[str, dict] = {}
        #: 已算完、尚未回传成功（`POST /jobs/{id}/ready`）的 job_id。
        #: volatile：只影响优先级（低档备份），重启丢掉不影响正确性。
        self._ready: set[str] = set()
        #: 调度面版本号（§2.3 / R1-5）：`_claimed`/`_computing`/`_ready` 任一变化即 +1。
        #: 只服务 highest 的唯一性闸（值本身无残留语义，重启归零）。
        self._epoch: int = 0

    # ---- 调度面事实（优先级问询 / 掉队阈值的唯一事实源） ----
    def _bump_epoch_locked(self) -> None:
        """调度面版本 +1（调用方必须持锁）。只在 `_claimed`/`_computing`/`_ready` 变化时调。"""
        self._epoch += 1

    def scheduling_epoch(self) -> int:
        """当前调度面版本（`POST /jobs/priority` 的响应字段）。"""
        with self._lock:
            return int(self._epoch)

    def start_job(self, job_id: str, worker_id: str = "") -> bool:
        """`POST /jobs/{id}/start`：打 **computing_at**（掉队阈值的唯一时基）+ `epoch += 1`。

        「PPO 真正启动」与「claim 成功」是两把时钟（R2-C1）：claim 之后还有整包下载 +
        解包 + 权重装载，拿 claim 起算会把慢链路误判成慢计算。

        ⚠ 本端点**不**校验 `expected_epoch`（R2-C4）：闸只在 claim 一处，两处各自校验
        就是第二个事实源。
        """
        with self._lock:
            wid = str(worker_id or "").strip() or str((self._claimed.get(job_id) or {}).get("worker", ""))
            self._computing[job_id] = {"worker": wid, "at": self._now()}
            self._claimed.setdefault(job_id, {"worker": wid, "at": self._now()})
            self._bump_epoch_locked()
            return True

    def set_ready(self, job_id: str, worker_id: str = "") -> bool:
        """`POST /jobs/{id}/ready`：算完待回传（只降别人的优先级，**永不**触发取消）。"""
        with self._lock:
            self._ready.add(job_id)
            if worker_id:
                self._last_heartbeat[job_id] = self._now()
            self._bump_epoch_locked()
            return True

    def scheduling_facts(self, job_id: str, *, exclude_worker: str = "") -> dict:
        """单份 job 的调度事实（**只看别人**；问询者自己的痕迹被排除，§1.4）。

        `landed` 走盘上的 `result/` 与失败标记——它是「无优先级」的唯一来源（唯一硬闸），
        也是软持有副本的就地丢弃信号。

        ⚠ 它只是 `_facts_locked` 的加锁包——**锁不可重入**，而优先级判定本身就在临界区里
        调事实：直接互调会让第一次 `/jobs/{id}/status` 把 hub 线程永久卡死（本实现的第一版
        就是这么写的，被 test_poison_freeze 当场抓出来）。
        """
        with self._lock:
            return self._facts_locked(job_id, exclude_worker=exclude_worker)

    def _facts_locked(self, job_id: str, *, exclude_worker: str = "") -> dict:
        """事实面的**唯一**实现（调用方必须特锁）——见 `scheduling_facts` 的告警。"""
        jd = self._job_dir(job_id)
        claimed = dict(self._claimed.get(job_id) or {})
        computing = dict(self._computing.get(job_id) or {})
        if exclude_worker:
            if str(claimed.get("worker", "")) == exclude_worker:
                claimed = {}
            if str(computing.get("worker", "")) == exclude_worker:
                computing = {}
        return {
            "landed": (jd / "result").exists() or (jd / FAIL_NAME).exists(),
                "ready": job_id in self._ready,
                "claimed": bool(claimed),
                "computing_at": (float(computing["at"]) if computing.get("at") else None),
                "lease_holder": self._lease_workers.get(job_id, ""),
        }

    def _job_priority_locked(self, job_id: str, *, exclude_worker: str = "") -> str:
        """该 job 当前的优先级（调用方**必须持锁**；用到 `_claimed`/`_computing`/`_ready`）。"""
        facts = self._facts_locked(job_id, exclude_worker=exclude_worker)
        return job_priority(
            landed=bool(facts["landed"]),
            ready_elsewhere=bool(facts["ready"]),
            claimed_elsewhere=bool(facts["claimed"]),
            computing_elsewhere_since=facts["computing_at"],
            now=self._now(),
        )

    def priority_for(self, job_id: str, *, exclude_worker: str = "") -> tuple[str, str]:
        """`(优先级, 一行理由)`——观测面与优先级 RPC 共用。"""
        with self._lock:
            p = self._job_priority_locked(job_id, exclude_worker=exclude_worker)
            facts = self._facts_locked(job_id, exclude_worker=exclude_worker)
        if p == PRIORITY_NONE:
            why = "结果已落盘（唯一硬闸：放弃）"
        elif p == PRIORITY_HIGH:
            why = f"别处在算且超阈值（computing_at 起 {self._now() - float(facts['computing_at']):.0f}s）"
        elif p == PRIORITY_MEDIUM and facts.get("computing_at"):
            # 中档里再分一层：已开算 vs 只承诺（还在下载/装载）。R2-C1 的两把时钟在
            # **观测行**上也要分得出来——否则「卡在下载」与「算得慢」在日志里同一句话。
            why = f"别处在算（computing_at 起 {self._now() - float(facts['computing_at']):.0f}s，未超阈值）"
        elif p == PRIORITY_MEDIUM:
            why = "别处已承诺在跑（尚未开算：还在下载/装载）"
        elif p == PRIORITY_LOW:
            why = "别处算完待回传（低档备份保险）"
        else:
            why = "无人在做（独占）"
        return p, why

    def _drop_commitment_locked(self, job_id: str) -> None:
        """撕掉「有人承诺在跑」的调度面痕迹（调用方**必须持锁**）+ 版本 +1。

        为什么必须与租约同生共死：`_claimed` 是 highest 唯一性闸的**唯一**判据，而它的
        生死有三个入口（主动还租约 / 租约过期熔断 / 合法放弃 abandon）。
        漏一个入口，那份 job 就被自己人永远挡在门外：合法重领变成领不到——本实现被
        `test_poison_freeze`（release）与 `test_priority_schedule`（放弃独占）各抓出一次。
        """
        self._claimed.pop(job_id, None)
        self._computing.pop(job_id, None)
        self._ready.discard(job_id)
        self._bump_epoch_locked()
