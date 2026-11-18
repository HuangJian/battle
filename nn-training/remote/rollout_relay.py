"""remote/rollout_relay.py — 采样节点的「主动会合」通道（2026-11-18）。

**它解决的问题**（真机现场，ModelScope / 阿里云 8C32G）：

    预检: api.trycloudflare.com:443 inherit=fail direct=fail ｜ 边缘7844 tcp=fail
    cloudflared 停在 "Requesting new quick Tunnel on trycloudflare.com..." 16s 后 rc=1

这台机器建不出任何 quick tunnel（建隧道那次 443 与边缘 7844 都不通），但 `github.com` /
`registry.npmmirror.com` / **hub 的 `*.trycloudflare.com`** 都通 —— 缺的只是「hub 打进来」这半条。
于是把方向翻过来：节点**出站**长轮询 hub，把本该发给它的请求在本机执行，再把回执交回来。
hub 侧实现与协议见 `hub/relay.py` 头部。

**三条纪律**：

1. **本模块只移动字节，不解释业务**：`/v1/*` 的语义、鉴权头、幂等键全部原样透传 —— 解释
   第二遍就是第二份真相（agent 与 trainer 之间的线上契约不经过这里）；
2. **本地执行只打 `127.0.0.1`，且绕开代理**（`common.net_http.urlopen`）：本机环境常带
   `HTTP_PROXY`，塞进代理会把 loopback 请求送出去再转回来（`net_http` 头部就是这条事故）；
3. **轮询失败只退避，不重注册**：这条通道的存活由 `rollout_node` 的监护循环判（线程全停 ⇒
   重建），这里不碰 `reg_state`/`url` —— 那两份状态归 supervisor。
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from common import net_http

#: 单条长轮询的挂起时长（秒）；hub 侧 `POLL_WAIT_MAX=60` 是它的上限。
POLL_WAIT_SEC = 20.0
#: 轮询线程数：agent 侧同时可能有多条请求（权重 POST / 取任务 / 取结果 / 探活），
#: 少了会出现队头阻塞（一条 20s 的 `_poll` 会把下一次 ping 排到后面）。
POLL_WORKERS = 6
#: 轮询失败后的退避（秒）：hub 短暂 5xx / 本机抖动都不该打满 CPU。
BACKOFF_SEC = 2.0
#: 单条请求体的上限（权重包 ~几 MB；超过它一定是把别的什么东西塞进来了）。
BODY_MAX = 32 * 1024 * 1024
#: 本地 agent 的执行上限（秒）：比 hub 侧最长的调用等待（weights 150s）略短，
#: 这样「agent 卡住」会变成一条 502 回执而不是让调用方等到 504。
EXEC_TIMEOUT_SEC = 140.0
#: 回执投递的预算（秒）：小请求，抖动重试留给下一轮。
REPLY_TIMEOUT_SEC = 30.0

#: 不该透传给本地 agent 的逐跳头（`Host` 必须重算成本机地址；长度由 urllib 自己算）。
_HOP_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "te", "trailer"}
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw or b"").decode("ascii")


def _forward(port: int, req: dict[str, Any]) -> dict[str, Any]:
    """在本机 agent 上执行一条被转发的请求；**绝不抛**（失败也回一条可读的回执）。"""
    method = str(req.get("method") or "GET").upper()
    path = str(req.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    try:
        body = base64.b64decode(str(req.get("body_b64") or ""))
    except (ValueError, TypeError):
        return _reply_dict(400, {"error": "body_b64 非法"})
    if len(body) > BODY_MAX:
        return _reply_dict(413, {"error": f"请求体超上限（{len(body)} > {BODY_MAX}）"})
    headers = {str(k): str(v) for k, v in (req.get("headers") or {}).items() if v}
    target = f"http://127.0.0.1:{port}{path}"
    try:
        r = urllib.request.Request(
            target, data=(body if method not in ("GET", "HEAD") else None), method=method
        )
        for key, val in headers.items():
            if key.lower() in _HOP_HEADERS:
                continue
            r.add_header(key, val)
        with net_http.urlopen(r, timeout=EXEC_TIMEOUT_SEC) as resp:
            raw = resp.read(BODY_MAX)
            return {
                "status": int(getattr(resp, "status", 200)),
                "headers": dict(getattr(resp, "headers", {}).items()),
                "body_b64": _b64(raw),
            }
    except urllib.error.HTTPError as e:  # 4xx/5xx 是**应答**，不是转发失败
        try:
            raw = e.read(BODY_MAX)
        except Exception:
            raw = b""
        return {
            "status": int(e.code),
            "headers": dict(e.headers.items()) if e.headers else {},
            "body_b64": _b64(raw),
        }
    except Exception as e:  # agent 没起/端口不通/超时
        return _reply_dict(502, {"error": f"relay 本地执行失败: {type(e).__name__}"})


def _reply_dict(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": int(status),
        "headers": {"Content-Type": "application/json"},
        "body_b64": _b64(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
    }


class RelayLink:
    """节点侧的会合通道：N 个长轮询线程 + 一组计数器。

    `local_port` 由 hub 的 `_poll` 应答告知（节点只知道 hub 的**公网**地址，而注册 URL 要填
    **本机回环**端口 —— 那两者可以不同）。supervisor 用它拼注册 URL。
    """

    def __init__(
        self,
        *,
        hub_url: str,
        token: str,
        node_id: str,
        agent_port: int,
        log: Callable[[str], None],
        workers: int = POLL_WORKERS,
        poll_wait: float = POLL_WAIT_SEC,
    ) -> None:
        self.hub_url = str(hub_url or "").rstrip("/")
        self.token = str(token or "")
        self.node_id = str(node_id or "")
        self.agent_port = int(agent_port)
        self.log = log
        self.poll_wait = max(1.0, float(poll_wait))
        self._workers = max(1, int(workers))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self.local_port = 0
        # 计数器住 dict 而不是 `setattr`：锁内用 `getattr/setattr` 的目标是静态扫描看不清的
        # 「锁内不透明调用」（`tests/test_lock_reentrancy.py` 会红）——而这里恰好每拍都在锁内加一。
        self._counters: dict[str, int] = {"polls": 0, "served": 0, "errors": 0}
        self._started_at = 0.0

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        self._started_at = time.time()
        for i in range(self._workers):
            t = threading.Thread(
                target=self._worker, name=f"relay-{i}", daemon=True
            )
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()

    def alive(self) -> bool:
        """至少一个轮询线程还活着（全停 = 通道已死，supervisor 据此重建）。"""
        return any(t.is_alive() for t in self._threads) and not self._stop.is_set()

    @property
    def polls(self) -> int:
        return int(self._counters.get("polls", 0))

    @property
    def served(self) -> int:
        return int(self._counters.get("served", 0))

    @property
    def errors(self) -> int:
        return int(self._counters.get("errors", 0))

    def wait_local_port(self, timeout: float = 40.0) -> int:
        """等 hub 告知本机端口；拿不到 ⇒ 0（本轮注册不可用，别拿错端口硬填）。"""
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            if self.local_port:
                return self.local_port
            if not self.alive():
                return 0
            time.sleep(0.2)
        return self.local_port

    def registration_url(self, port: int | None = None) -> str:
        """注册 URL：**本机回环 + hub 端口**（trainer 与 hub 同机，这个地址对它恒可达）。"""
        p = int(port or self.local_port)
        if not p or not self.node_id:
            return ""
        return f"http://127.0.0.1:{p}/relay/{self.node_id}"

    def stats_line(self) -> str:
        uptime = int(time.time() - self._started_at) if self._started_at else 0
        return (
            f"relay polls={self.polls} served={self.served} errors={self.errors} "
            f"workers={self._workers} 秒={uptime}"
        )

    # ---------------------------------------------------------------- 轮询

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                answer = self._poll()
            except Exception as e:
                self._bump("errors")
                if self._stop.is_set():
                    return
                self.log(f"relay 轮询失败（{type(e).__name__}）——{BACKOFF_SEC:g}s 后重试")
                self._stop.wait(BACKOFF_SEC)
                continue
            self._bump("polls")
            if not isinstance(answer, dict):
                continue
            port = int(answer.get("local_port") or 0)
            if port:
                self.local_port = port
            req = answer.get("req")
            if not isinstance(req, dict) or not req.get("rid"):
                continue  # 空拍（长轮询超时）= 正常
            resp = _forward(self.agent_port, req)
            # 计「已执行」先于投递回执：回执丢了也算服务过一次（否则观测面会把干过的活报成 0）。
            self._bump("served")
            try:
                self._post_reply(str(req["rid"]), resp)
            except Exception as e:
                self._bump("errors")
                self.log(f"relay 回执投递失败（{type(e).__name__}）—— 调用方会看到超时")

    def _poll(self) -> dict[str, Any] | None:
        url = f"{self.hub_url}/relay/{self.node_id}/_poll?wait={self.poll_wait:g}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.poll_wait + 15.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, dict) else None

    def _post_reply(self, rid: str, resp: dict[str, Any]) -> None:
        url = f"{self.hub_url}/relay/{self.node_id}/_reply/{rid}"
        payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=REPLY_TIMEOUT_SEC) as r:
            r.read()

    def _bump(self, name: str) -> None:
        with self._lock:
            self._counters[name] = int(self._counters.get(name, 0)) + 1


#: 起好后等 hub 告知本机端口的预算（秒）。hub 未升级（`/relay/` 404）或出网断 ⇒ 到点放弃。
PORT_WAIT_SEC = 40.0


class RelayChannel:
    """relay 通道的**所有者**：连接参数 + 生命周期 + 监护判据。

    `rollout_node` 只持有它、只问两件事：「起得来吗（`open`）」与「还活着吗（`ensure`）」。
    为什么不留个 `_open_relay` 在 supervisor 里：通道状态（线程 / 端口 / URL）一旦有两份写者，
    「重建后忘清注册 URL」就是下一个事故；而且 supervisor 文件本来就顶在 1000 行预算上。
    """

    def __init__(
        self,
        *,
        hub_url: str,
        token: str,
        node_id: str,
        agent_port: int,
        log: Callable[[str], None],
        wait_sec: float = PORT_WAIT_SEC,
    ) -> None:
        self._args: dict[str, Any] = {
            "hub_url": hub_url,
            "token": token,
            "node_id": node_id,
            "agent_port": agent_port,
            "log": log,
        }
        self.log = log
        self._wait_sec = max(0.0, float(wait_sec))
        self.link: RelayLink | None = None

    def open(self, reason: str) -> str:
        """起通道并等端口；成功返回注册 URL，失败返回 `""`（已 stop、已打日志）。"""
        self.log(f"relay 通道（{reason}）：节点出站长轮询 hub，不做入站、不建隧道")
        link = RelayLink(**self._args)
        link.start()
        url = link.registration_url(link.wait_local_port(self._wait_sec))
        if not url:
            link.stop()
            self.log(
                f"relay 未就绪（{self._wait_sec:g}s 内 hub 未应答长轮询）—— 检查 hub 地址/令牌与出网；"
                "hub 需已升级到带 /relay/ 的版本"
            )
            self.link = None
            return ""
        self.link = link
        self.log(f"relay 就绪：url={url}（hub 调本机回环，节点侧不需要任何入站端口）")
        return url

    @property
    def url(self) -> str:
        return self.link.registration_url() if self.link is not None else ""

    def alive(self) -> bool:
        return self.link is not None and self.link.alive()

    def supervise(self, reason: str = "relay 轮询线程已停") -> str:
        """监护一拍：活着 ⇒ 当前 URL；死了 ⇒ 停旧、重开（返回新 URL 或 `""`）。"""
        if self.alive():
            return self.url
        if self.link is not None:
            self.log(f"★ {reason} —— 重建通道")
            self.link.stop()
            self.link = None
        return self.open(reason)

    def stop(self) -> None:
        if self.link is not None:
            self.link.stop()
            self.link = None

    def stats_line(self) -> str:
        return self.link.stats_line() if self.link is not None else "relay 未启动"


__all__ = [
    "BODY_MAX",
    "POLL_WAIT_SEC",
    "POLL_WORKERS",
    "PORT_WAIT_SEC",
    "RelayChannel",
    "RelayLink",
]
