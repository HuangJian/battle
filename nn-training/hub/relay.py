"""hub/relay.py — 反向会合通道（2026-11-18）：让「只能出网」的云机也能被 hub 调用。

**为什么需要**（真机现场 2026-11-18，ModelScope / 阿里云）：

    [rollout-node] 预检: api.trycloudflare.com:443 inherit=fail direct=fail ｜ 边缘7844 tcp=fail
    [rollout-node] cloudflared 第 1/3 次未就绪（进程已退出 rc=1，存活 17s）
    [rollout-node]   | {"level":"info", ... "Requesting new quick Tunnel on trycloudflare.com..."}

这台机器**建不出任何 quick tunnel**：建隧道那次 HTTPS 调用（`api.trycloudflare.com:443`）与
边缘（`region*.v2.argotunnel.com:7844`）都不通，而同一台机器的 `github.com:443` /
`registry.npmmirror.com:443` 是通的（175ms / 20ms），hub 自己的 `*.trycloudflare.com` 也通
⇒ 缺的是**入站那半条**，不是「没网」。换协议（http2/quic/auto）、换 `--edge-ip-version`、
填 `CFG.net_proxy` 都治不了：7844 是写死的唯一端口，而 cloudflared 的边缘拨号不读代理。

**形状**：节点长轮询 hub（出站），hub 把「本该发给节点 URL 的请求」挂进队列，节点执行完把
回执交回来。三条端点住在 `hub/http_face.py::_relay_route`：

    GET  /relay/<node>/_poll?wait=20    节点取一条待办（Bearer = HUB_TOKEN）
    POST /relay/<node>/_reply/<rid>     节点交回执（status / headers / body_b64）
    <ANY>/relay/<node>/<path>           调用方（**只允许本机回环**）等回执

节点把这条面上的 URL 注册成 `http://127.0.0.1:<hub 端口>/relay/<node>`，于是 trainer / eval /
dashboard 的既有调用（`url + "/v1/ping"` 之类）**一个字节都不用改**。

**三条不可交易的性质**：

1. **一切等待有界**：调用方 timeout、`_poll` 的 wait、待办队列上限、回执 TTL —— 节点掉线时
   调用方拿到的是「超时」（与隧道 530 同一类语义），而不是一个永远挂住的线程；
2. **迟到回执必须丢弃**：调用方超时后那一条已经不在 `calls` 里，回执再写进来只会污染状态
   （rid 唯一，`reply` 找不到就返回 False，绝不新建条目）；
3. **纯 stdlib、零 import 依赖**（本模块是叶子）：HTTP 面直接 import 它，拖进任何调度面都会
   让 hub 的 import 图变重。

**被否决**：让节点把结果写进 hub 的既有队列端点（那要改派发器的「谁发起」语义，动的是
rollout/eval 的主链路）· 用第三方隧道服务（把 1/3 算力押在一个免费服务 + 其域名可达性上，
且要新增运行时依赖）· 在 hub 上开 CONNECT 代理给 cloudflared 用（家宽上行带宽与公网暴露面
都更差，且 quick tunnel 创建那一步仍然不通）。
"""

from __future__ import annotations

import base64
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from typing import TypedDict


#: 一条待办请求（`poll` 的应答；字段与节点侧 `RelayRequest` 同形）。
class PollCall(TypedDict):
    rid: str
    method: str
    path: str
    headers: dict[str, str]
    body_b64: str

#: 待办队列上限：节点掉线时调用方仍会不断入队 —— 满了丢最老的（有界内存，不静默增长）。
PENDING_MAX = 32
#: 一条待办/回执最多活多久（秒）：调用方超时后回执即作废，再长就是泄漏。
CALL_TTL_SEC = 180.0
#: `_poll` 的缺省/上限挂起时长（秒）。长轮询的意义：请求到达时立刻唤醒，而不是等下一个节拍。
POLL_WAIT_DEFAULT = 20.0
POLL_WAIT_MAX = 60.0
#: 「通道心跳」多旧就算断了（秒）：节点**每一次** `_poll` 都会刷新它。
#:
#: 为什么拿它当探活判据（2026-11-18 DSW 稳定性）：hub 每轮派发都要 `/v1/ping`（调用方预算
#: 3s），而 relay 一次调用得等节点下一次长轮询（≤`POLL_WAIT_DEFAULT`）⇒ 约一半探活假失败、
#: 且 handler 线程空占到 15s。今夜实测 `/relay/dsw/v1/ping` 186 ok / 192 客户端先走 /
#: 67 超时。「节点最近一次轮询有多久」是本地事实，判它比等一次往返可靠一个量级。
CHANNEL_STALE_SEC = 3 * POLL_WAIT_DEFAULT
#: 缓存 ping 载荷的 TTL（秒）：过这个年龄就**后台刷一次**（照用不误，见 `PING_SERVE_MAX_SEC`）。
#:
#: 取值约束（两头夹）：**下界** ≥ 刷新本身的耗时 —— 后台刷新走一次 relay 往返，得等节点下一次
#: 长轮询（≤`POLL_WAIT_DEFAULT`），TTL 比它短就会「刚刷完立刻又该刷」，等于每拍都发一次调用；
#: **上界** = 这个载荷的「事实」能滞后多久。它不是普通健康快照：`codeHash`（派发门，a98 事故
#: 就是两个代码版本混跑）与 `bootId`（单实例门）都在这份体里 ⇒ 30s 的滞后是能接受的代价，
#: 120s 不是。稳态下真正被顶答的年龄 = TTL 上下（刷新一落地就重新计时）。
PING_TTL_SEC = 30.0
#: 过期载荷最多还能顶多久（秒）：一次隧道级黑洞（今夜实测 30s+，curl 三次 1.26/3.52/6.00s）
#: 不该让每次探活都去等一次注定超时的往返；但超过这个岁数就老实走真调用 ——
#: 宁可把「这轮够不着」诚实地报出来，也不拿化石台账去喂派发门。
PING_SERVE_MAX_SEC = 120.0


class RelayTimeoutError(Exception):
    """调用方在 timeout 内没等到节点回执（语义同隧道超时/530：可重试，不是终局）。"""


class _Call:
    """一次待转发的请求 + 它等的那份回执。"""

    __slots__ = (
        "at",
        "body",
        "event",
        "headers",
        "method",
        "path",
        "rbody",
        "rheaders",
        "rid",
        "status",
    )

    def __init__(
        self,
        rid: str,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        *,
        at: float,
    ) -> None:
        self.rid = rid
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body
        self.at = at
        self.event = threading.Event()
        self.status = 0
        self.rheaders: dict[str, str] = {}
        self.rbody = b""


class _Box:
    """一台节点的待办队列 + 在飞回执。"""

    __slots__ = (
        "calls",
        "cond",
        "dropped",
        "last_poll",
        "late",
        "pending",
        "ping",
        "ping_at",
        "ping_busy",
        "polls",
        "served",
        "timeouts",
    )

    def __init__(self, lock: threading.Lock) -> None:
        self.pending: deque[_Call] = deque()
        self.calls: dict[str, _Call] = {}
        self.cond = threading.Condition(lock)
        self.polls = 0
        self.served = 0
        self.dropped = 0
        self.timeouts = 0
        self.late = 0
        #: 最近一次 `_poll` 的单调钟时刻（0 = 从没轮询过）—— 通道心跳的唯一真相。
        self.last_poll = 0.0
        #: 最近一次**成功**的 `/v1/ping` 载荷（供通道活着时直接顶答）。
        self.ping: dict[str, object] | None = None
        self.ping_at = 0.0
        #: 后台刷新单飞行标记（同一台节点同时只允许一个，见 `claim_ping_refresh`）。
        self.ping_busy = False


class NodeRelay:
    """进程级会合面（一台节点一个 box）。

    线程模型：`call` 在调用方线程里等（`Event.wait`），`poll` 在节点的长轮询线程里等
    （`Condition.wait`）；所有状态都过同一把 `_lock`，没有第二份真相。
    """

    def __init__(
        self,
        *,
        pending_max: int = PENDING_MAX,
        ttl_sec: float = CALL_TTL_SEC,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._boxes: dict[str, _Box] = {}
        self._pending_max = max(1, int(pending_max))
        self._ttl = max(1.0, float(ttl_sec))
        #: 时长/过期的**唯一钟**（P1-6：可注入的单调钟）。缺省 = `time.monotonic`；
        #: 用例注一个假钟即可精确操作 TTL，不必写私有字段，也不再受墙钟跳变影响。
        self._monotonic = monotonic or time.monotonic

    def now(self) -> float:
        """当前单调钟读数（与 TTL/年龄判据同一来源；观测与用例读它）。"""
        return self._monotonic()

    # ---------------------------------------------------------------- 内部

    def _box_locked(self, node: str) -> _Box:
        box = self._boxes.get(node)
        if box is None:
            box = _Box(self._lock)
            self._boxes[node] = box
        return box

    # ---------------------------------------------------------------- 节点侧

    def poll(self, node: str, wait_sec: float = POLL_WAIT_DEFAULT) -> PollCall | None:
        """取一条待办；`wait_sec` 内没有就返回 None（节点据此继续下一轮长轮询）。"""
        started = self._monotonic()
        deadline = started + max(0.0, float(wait_sec))
        with self._lock:
            box = self._box_locked(node)
            box.polls += 1
            box.last_poll = started  # 通道心跳：探活/超时判定都读它
        while True:
            now = self._monotonic()
            with self._lock:
                while box.pending:
                    call = box.pending.popleft()
                    if now - call.at > self._ttl:  # 调用方早走了：这条不再发
                        box.dropped += 1
                        continue
                    box.calls[call.rid] = call
                    box.served += 1
                    return {
                        "rid": call.rid,
                        "method": call.method,
                        "path": call.path,
                        "headers": dict(call.headers),
                        "body_b64": _b64(call.body),
                    }
            remain = deadline - self._monotonic()
            if remain <= 0:
                return None
            with box.cond:
                # 1s 的上限只为周期性复检 TTL；正常唤醒靠 `call()` 的 notify_all。
                box.cond.wait(min(remain, 1.0))

    def reply(
        self,
        node: str,
        rid: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> bool:
        """交回执；`rid` 已作废（调用方超时/队列被丢）⇒ False，绝不新建条目。"""
        with self._lock:
            box = self._boxes.get(node)
            call = box.calls.pop(rid, None) if box is not None else None
            if call is None:
                if box is not None:
                    box.late += 1
                return False
        call.status = int(status)
        call.rheaders = dict(headers or {})
        call.rbody = body or b""
        call.event.set()
        return True

    # ---------------------------------------------------------------- 调用方侧

    def call(
        self,
        node: str,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes = b"",
        *,
        timeout: float = 15.0,
    ) -> tuple[int, dict[str, str], bytes]:
        """把一条请求挂给节点并等回执；超时抛 `RelayTimeoutError`。"""
        rid = uuid.uuid4().hex
        call = _Call(rid, method, path, dict(headers or {}), body or b"", at=self._monotonic())
        with self._lock:
            box = self._box_locked(node)
            if len(box.pending) >= self._pending_max:
                box.pending.popleft()  # 丢最老的：节点掉线时调用方仍会入队
                box.dropped += 1
            box.pending.append(call)
            box.cond.notify_all()
        if not call.event.wait(max(0.1, float(timeout))):
            with self._lock:
                box.calls.pop(rid, None)
                try:
                    box.pending.remove(call)
                except ValueError:
                    pass
                box.timeouts += 1
            raise RelayTimeoutError(f"{method} {path} 在 {timeout:g}s 内无回执")
        return call.status, dict(call.rheaders), call.rbody

    # ---------------------------------------------------------------- 观测

    def channel_age(self, node: str) -> float | None:
        """节点上一次 `_poll` 距今多少秒（`None` = 从没轮询过）。

        **它是「通道活着」的唯一判据**：节点侧任何一次长轮询都会刷新它，而长轮询是节点自己
        发起的 —— 所以「心跳新鲜」= 节点进程在跑且出网正常，与「这一拍能不能拿到往返」
        （受隧道抖动影响）是两件事。
        """
        now = self._monotonic()
        with self._lock:
            box = self._boxes.get(node)
            if box is None or not box.last_poll:
                return None
            return max(0.0, now - box.last_poll)

    def ping_snapshot(self, node: str) -> tuple[dict[str, object] | None, float]:
        """最近一次**成功**的 `/v1/ping` 载荷 + 它的年龄（秒）；没有载荷 ⇒ `(None, 0.0)`。"""
        now = self._monotonic()
        with self._lock:
            box = self._boxes.get(node)
            if box is None or box.ping is None:
                return None, 0.0
            return dict(box.ping), max(0.0, now - box.ping_at)

    def note_ping(self, node: str, body: Mapping[str, object]) -> None:
        """记一条成功的 ping 载荷（真调用与后台刷新都走这里）。

        **不动单飞行标记**：那个开关的所有权属于认领者（`claim_ping_refresh` 的返回者），
        由它自己在 `release_ping_refresh` 放回 —— 两条路都去放会互相把对方的重入权打开。
        """
        now = self._monotonic()
        with self._lock:
            box = self._box_locked(node)
            box.ping = dict(body)
            box.ping_at = now

    def claim_ping_refresh(self, node: str) -> bool:
        """单飞行：同一台节点同时只允许一个后台刷新（`True` = 你负责去刷）。

        没有它的话，每一条过期请求都会各起一个线程 —— 隧道一抖就是几十个线程抢同一条
        带宽更差的链路，正是要避免的「抖动放大」。
        """
        with self._lock:
            box = self._box_locked(node)
            if box.ping_busy:
                return False
            box.ping_busy = True
            return True

    def ping_busy_of(self, node: str) -> bool:
        """该节点是否**正有一个**后台刷新在飞（公开读数）。

        为什么要有它：单飞的契约是「同时只有一个」，测试要断言这件事；直接读
        `relay._boxes[node].ping_busy` 会把私有字段变成事实 API（G5）。
        """
        with self._lock:
            box = self._boxes.get(node)
            return bool(box is not None and box.ping_busy)

    def release_ping_refresh(self, node: str) -> None:
        """刷新失败时放开单飞行（否则这台节点再也不会被刷新）。"""
        with self._lock:
            self._box_locked(node).ping_busy = False

    def stats(self) -> dict[str, dict[str, int]]:
        """每台节点的计数器（`/admin/nodes` 之外的排障读数；零副作用）。"""
        with self._lock:
            return {
                node: {
                    "pending": len(box.pending),
                    "calls": len(box.calls),
                    "polls": box.polls,
                    "served": box.served,
                    "dropped": box.dropped,
                    "timeouts": box.timeouts,
                    "late": box.late,
                }
                for node, box in self._boxes.items()
            }


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw or b"").decode("ascii")


def content_type_of(headers: dict[str, str]) -> str:
    """节点回执里的 `Content-Type`（大小写不敏感）；缺失/空 ⇒ octet-stream。"""
    for key, val in (headers or {}).items():
        if str(key).lower() == "content-type" and val:
            return str(val)
    return "application/octet-stream"


#: 进程级单例：`HubHandler`（ThreadingHTTPServer 每请求新建实例）只能挂类属性/模块状态，
#: 与 `_worker_pulse` / `_blocked_logged` 同规。
RELAY = NodeRelay()

__all__ = [
    "CALL_TTL_SEC",
    "CHANNEL_STALE_SEC",
    "PENDING_MAX",
    "PING_SERVE_MAX_SEC",
    "PING_TTL_SEC",
    "POLL_WAIT_DEFAULT",
    "POLL_WAIT_MAX",
    "RELAY",
    "NodeRelay",
    "RelayTimeoutError",
    "content_type_of",
]
