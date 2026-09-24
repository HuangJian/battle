"""remote/hub/auth.py — 鉴权原语：来源判定 + D9 闭锁（S4 第十五刀，从 `hub_server` 搬出）。

## 为什么单独成模块

`_HubQueue` 的七个域混入里有一个是**鉴权面**（`remote/hub/queue_auth.py`）：它要做
「单课程借 store、多课程用自己那份」的委派，而两条路都要 `_AuthGuard`。但 `remote/hub/*`
**不得** import `hub_server`（否则与「`hub_server` import 混入」成环，而本仓的环只有
「下沉共同依赖」与「参数注入」两种处理方式，`DEFERRED_CYCLES` 至今是空的）。

⇒ 共用原语**下沉**，依赖方向变成 `hub_server → hub.auth` 与 `hub.store → hub.auth`（都向下）。

## 为什么 `_is_loopback` 住鉴权模块

它是**闭锁规则的一部分**，不是网络小工具：用户口径「本地 127.0.0.1 鉴权失败不要锁地址」
落到实现上就是「回环来源永不计数、永不封禁」（见它的 docstring）。`attributed_source`
（隧道归因，属于 HTTP 层）也调它，方向同样是向下。

## 名字是契约，位置不是

`remote.hub_server` 仍 re-export `_is_loopback` / `_AuthGuard`（`tests/test_hub_auth_d9_order.py`
取 `_is_loopback`，`tests/test_hub_job_store_split.py` 取 `_AuthGuard` 核 `_lock` 的唯一来源）。
"""

from __future__ import annotations

import time
from threading import Lock


def _is_loopback(ip: str) -> bool:
    """回环来源（`127.0.0.0/8` / `::1` / IPv4-mapped `::ffff:127.0.0.1`）。

    **闭锁永不作用于回环**（2026-09-17 用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）：
    回环来源就是本机自己的组件（console 健康检查、训练循环、worker 拉活），而 cloudflared
    回源还会把**隧道流量一并归成 127.0.0.1** —— 对它封禁等于把整台机器的服务面连坐，且
    封禁只住进程内存、只能靠重启清除（2026-09-17 hub-server 重启死锁事故的根因）。
    回环上的**无效**鉴权照常 401（鉴权边界与审计行不变），只是**不计数、不封禁**。
    """
    s = (ip or "").strip().lower()
    if s.startswith("::ffff:"):  # IPv4-mapped IPv6
        s = s[len("::ffff:") :]
    return s == "::1" or s == "localhost" or s.startswith("127.")


class _AuthGuard:
    """D9 鉴权闭锁（提取自 `_JobStore`，2026-09-18）。

    为什么要独立成类：多课程单 hub 之后**鉴权面是进程级的一份**（一个 IP 的失败计数
    不该按课程各算一套，否则“同一来源 5 次无效鉴权”会把封禁阈值变成 5×N）。`_JobStore`
    仍继承它（旧调用/旧测试的 `store.is_blocked(...)` 逐字不变）。"""

    def __init__(self, now_fn=None) -> None:
        self._lock = Lock()
        self._now = now_fn or time.time
        #: 来源 IP -> 无效鉴权计数（满 5 封禁，D9）
        self._auth_fail: dict[str, int] = {}
        self._auth_blocked_until: dict[str, float] = {}

    # ---- 闭锁（D9） ----
    def auth_failure(self, ip: str) -> int:
        """记一次鉴权失败，返回**累计次数**（含本次）；满 5 次封禁 3600s。

        返回值供 handler 打印审计行——2026-09-16 x3-step 事故：401 落在
        `/ping`・`/jobs/peek` 等静默路径上，五次失败把 127.0.0.1 封掉后
        **hub 日志一行痕迹都没有**，训练循环被 cloudflared 回源 IP 连坐后
        连续 403 自杀退出，只能靠猜。故次数必须上浮到调用方记录。

        **回环来源永不计数、永不封禁**（返回 0）——口径见 `_is_loopback`。
        """
        if _is_loopback(ip):
            return 0
        with self._lock:
            n = self._auth_fail.get(ip, 0) + 1
            self._auth_fail[ip] = n
            if n >= 5:
                self._auth_blocked_until[ip] = self._now() + 3600
                self._auth_fail.pop(ip, None)
            return n

    def auth_success(self, ip: str) -> None:
        """一次合法鉴权：清零该 ip 的失败计数（**不改封禁状态**）。

        2026-09-17 改序配套：封禁只拒无效尝试后，合法流量必须能把计数打回零——否则
        与合法组件共用同一个来源 IP 的坏客户端（典型：cloudflared 回源把隧道流量与
        所有本机组件都归成 127.0.0.1）仍会**慢性累积**到 5 次，把整个 IP 拖进封禁。
        封禁本身不在此解除：它已只影响无效尝试，到点自愈，无需合法流量代劳。
        """
        with self._lock:
            self._auth_fail.pop(ip, None)

    def is_blocked(self, ip: str) -> bool:
        if _is_loopback(ip):  # 回环永不被封（即便旧内存态里混进过记录）
            return False
        with self._lock:
            until = self._auth_blocked_until.get(ip, 0.0)
            return until > self._now()

    def blocked_remaining(self, ip: str) -> float:
        """该 ip 剩余封禁秒数（未封禁 = 0）——供审计行提示「还要封多久」。"""
        with self._lock:
            return max(0.0, self._auth_blocked_until.get(ip, 0.0) - self._now())
