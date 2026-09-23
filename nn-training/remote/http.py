"""remote/http.py — worker 的 **HTTP 传输核心**（S4 拆分，2026-09-23）。

从 `remote/worker.py` 整簇搬出来的「怎么把一次请求发出去、读回来、失败了怎么重试」：
`_opener`（显式 ProxyHandler 的懒建单例）· `BODY_*` 阈值 · `_read_body`（分块读 + 停滞/超预算
判停）· `_request` · `_sched_headers` · `_get_with_retry`（退避重试 + 低速重抽）· `_POLL_WARN_AT`
+ `_warn_non_200`（非 200 节流告警）。

## 为什么先拆它（而不是先拆业务簇）

它是 worker 里**所有**业务功能的公共底座：作业生命周期（peek/claim/start/ready/abandon/status/
heartbeat/release/fail）· 下载（payload/code/ts_code/blob）· 结果回传 · BC 作业，全都站在
`_request` 上。底座不动，任何业务簇搬出去都会与 `worker.py` 成环（业务簇要 `_request`，
`worker` 又要 import 业务簇）。搬完这个，`worker` 只剩编排 + 作业生命周期。

## 依赖方向

`http ← wire ← worker`（DAG）：本模块只依赖 `remote.wire`（bulk 让路 / 重抽判据 / 传输账）与
`remote.bulk_sched` / `common.protocol`，**不** import `remote.worker`。`worker.py` 用
`from remote.http import … as …` 显式转发（自别名 = ruff 认可的 re-export 写法）。

## 注入点（拆分会**静默**坏掉的东西）

* `_opener` / `_POLL_WARN_AT` 是**重绑 / 原地可变**的模块级状态，只在本模块被读写；
  `worker._POLL_WARN_AT` 是同一 dict 的转发（`.clear()` 有效），`worker._opener` 则会停在
  转发那一刻的旧值——测试要读懒建结果就得进本模块。
* `_request` 被大量测试 monkeypatch。**按调用点定档**：patch 后调宿主函数（`claim_job` /
  `heartbeat` / `post_result` / `download_*`）的，解析在 `worker` 命名空间 ⇒ 继续 patch
  `worker`；patch 后调 `_get_with_retry` / `_read_body`（在本模块）的 ⇒ 必须 patch `remote.http`。
* `BODY_PROGRESS_MIN_SEC` 同理：`_read_body` 读的是**本模块**的全局，改它要进本模块。
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from typing import Any

from common.protocol import (
    AUTH_HEADER,
    OFFLINE_CAP_HEADER,
    OFFLINE_CAP_VALUE,
    WORKER_ID_HEADER,
    ProtocolError,
    RetryableError,
)
from remote import net_http
from remote.bulk_sched import BULK_P1_CRITICAL, BulkPreemptError, control_path
from remote.wire import (
    _BULK,
    WIRE_REROLL_MAX,
    WireSlowError,
    _bulk_pace,
    _min_rate,
    _note_rate,
    _reroll_decision,
    _wire_add,
    _wire_note_reroll,
)

__all__ = [
    "BODY_CHUNK",
    "BODY_IDLE_TIMEOUT_SEC",
    "BODY_PROGRESS_MIN_SEC",
    "BODY_TOTAL_TIMEOUT_SEC",
    "_POLL_WARN_AT",
    "_get_opener",
    "_get_with_retry",
    "_opener",
    "_read_body",
    "_request",
    "_sched_headers",
    "_warn_non_200",
]

# ------------------------------------------------------------------ HTTP 客户端

#: 显式 ProxyHandler（Colab userspace 模式下 urlopen() 不读 HTTP_PROXY 环境变量，2026-09-16 实测）
_opener: urllib.request.OpenerDirector | None = None


def _get_opener() -> urllib.request.OpenerDirector:
    global _opener
    if _opener is None:
        proxies: dict[str, str] = {}
        for k in ("http_proxy", "HTTP_PROXY"):
            v = os.environ.get(k)
            if v:
                proxies["http"] = v
                break
        for k in ("https_proxy", "HTTPS_PROXY"):
            v = os.environ.get(k)
            if v:
                proxies["https"] = v
                break
        _opener = (
            urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
            if proxies
            else urllib.request.build_opener()
        )
    return _opener


#: 大 body 下载的**空闲超时**（秒）：这么久没有新字节 = 判停滞，立刻放弃并重试。
#:
#: 为什么不能只靠一把 `timeout=300`（2026-09-20 事故）：云机领到第二个 job 后卡在
#: `resp.read()` 里 **5 分钟一行日志都没有**。操作员分不清「在下」还是「死了」，
#: 而 socket 超时抛出的裸 `TimeoutError()` 连原因都写不出来（`_get_with_retry` 只把
#: `repr(e)` 写进日志）。隧道/代理中途停滞正是这种形状：小 POST（心跳、轮询）照常，
#: 大 body 卡死——同机 cloudflared 当时每 5min 一条 `DNS i/o timeout`。
BODY_IDLE_TIMEOUT_SEC = 45.0
#: 单次 body 下载的**墙钟上限**（秒）：空闲超时管「停滞」，这条管「永远在滴水」。
BODY_TOTAL_TIMEOUT_SEC = 300.0
#: 进度行最小间隔（秒）与读块（字节）。
BODY_PROGRESS_MIN_SEC = 5.0
BODY_CHUNK = 256 * 1024


def _read_body(
    resp: Any,
    *,
    idle_timeout: float,
    total_timeout: float | None,
    progress: Any = None,
    allow_reroll: bool = False,
    pace: Any = None,
) -> bytes:
    """分块读 body：报进度 + **停滞/超预算即抛**（异常正文带已收字节数与原因）。

    `pace`（2026-09-22，P0）：每个分片间隙调一次的回调 —— 控制面在途时它会让路暂停，
    P2 预取被挤时它抛 `BulkPreemptError` 丢掉半截。**在读之前**调（停读 = TCP 窗口回填
    暂停，正是让控制面小包挤过去的方式）。

    停滞异常必须**有正文**：上游 `_get_with_retry` 只把 `repr(e)` 写进日志，裸
    `TimeoutError()` 打出来是 `TimeoutError()`——等于没写（2026-09-20 事故现场）。
    """
    total = 0
    try:
        total = int(resp.headers.get("Content-Length") or 0)
    except Exception:  # 非标准响应 / 假响应（测试）没有 headers
        total = 0
    buf = bytearray()
    t0 = time.time()
    last = t0
    probed = False
    while True:
        if pace is not None:
            pace()
        try:
            block = resp.read(BODY_CHUNK)
        except (TimeoutError, OSError) as e:
            raise TimeoutError(
                f"body 停滞：{idle_timeout:.0f}s 内没有新字节（已收 {len(buf)} bytes"
                + (f" / 共 {total}" if total else "")
                + "）——隧道或代理侧的问题，重试"
            ) from e
        if not block:
            return bytes(buf)
        buf += block
        now = time.time()
        if allow_reroll and not probed:
            # 只在**首块**判一次（probed 一次性）：这样每次重抽的浪费 ≤ 一块（BODY_CHUNK），
            # 绝不会退化成「再整份重传一遍」（plan §7.2 的硬要求）。
            probed = True
            should, rate, remain = _reroll_decision(len(buf), total, now - t0)
            if should:
                raise WireSlowError(len(buf), rate, remain)
        if total_timeout is not None and now - t0 > total_timeout:
            raise TimeoutError(
                f"body 超时：总耗时 > {total_timeout:.0f}s（已收 {len(buf)} bytes"
                + (f" / 共 {total}" if total else "")
                + "）——链路太慢，重试"
            )
        if progress is not None and now - last >= BODY_PROGRESS_MIN_SEC:
            last = now
            progress(len(buf), total, now - t0)


#: "base_url:status" -> 上次告警墙钟（节流：非 200 时每分钟最多一条，别刷屏）
_POLL_WARN_AT: dict[str, float] = {}


def _warn_non_200(base_url: str, status: int, log: Any) -> None:
    """非 200 的节流告警（（url, status）每分钟最多一条）。

    为什么要区分「队列空」与「被拒」（2026-09-16 x3-step 事故）：非 200 一律静默的话，
    被 403 ip blocked 的 worker 日志与空队列完全一样（只有 "no job yet"），现场无法判断。
    """
    if log is None:
        return
    now = time.time()
    key = f"{base_url}:{status}"
    if now - _POLL_WARN_AT.get(key, 0.0) <= 60:
        return
    _POLL_WARN_AT[key] = now
    hint = (
        "鉴权失败或该 IP 已被 hub 封禁——检查 --token 与 hub 日志 AUTH FAIL/BLOCKED"
        if status in (401, 403)
        else "hub 异常，请检查 hub 进程与隧道"
    )
    log(f"调度请求 {base_url}: HTTP {status} — {hint}（这不是「队列空」）")


def _request(
    base_url: str,
    token: str,
    path: str,
    timeout: float = 30.0,
    data: bytes | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
    progress: Any = None,
    idle_timeout: float | None = None,
    total_timeout: float | None = None,
    allow_reroll: bool = False,
    pace: Any = None,
) -> tuple[int, bytes]:
    """单发 GET/POST。缺省（不给 `idle_timeout`/`progress`）行为与改造前逐字节相同。

    控制面旁路（2026-09-22，P0）：`control_path(path)` 命中的路径（peek/priority/claim/
    start/ready/abandon/status/heartbeat/release/fail）只做一件事——**通知 bulk 让路**；
    它们本身永不进 bulk 队列（`urllib` 每次请求新开连接，天然是独立 socket）。

    给了 `idle_timeout`/`progress`（大 body 下载路径）才走**分块读**：socket 超时用
    `idle_timeout` 而不是 `timeout`——`timeout` 在这些路径上是**总预算**语义，拿它当
    socket 超时就是回到「静默 5 分钟」。

    `allow_reroll=True`（幂等 GET 的大 body）时，首块就慢得离谱就抛 `WireSlowError`
    交上层换连接重抽（调用方 = `_get_with_retry`，它负责计数与结束条件）。
    """
    url = f"{base_url.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={AUTH_HEADER: f"Bearer {token}", **(headers or {})},
        method=method,
    )
    stream = idle_timeout is not None or progress is not None
    sock_timeout = idle_timeout if idle_timeout is not None else timeout
    # 控制面标记（P0）：命中即让 bulk 在分片间隙让路；控制面自己**不进** bulk 队列。
    ctl = _BULK.control(label=path) if control_path(path) else nullcontext()
    with ctl:
        try:
            # 回环（本机 hub）绕开代理；非回环走进程内的显式 ProxyHandler（Colab 实测需要）。
            open_fn = net_http.urlopen if net_http.is_loopback(url) else _get_opener().open
            with open_fn(req, timeout=sock_timeout) as resp:
                if not stream or resp.status != 200:
                    return resp.status, resp.read()
                return resp.status, _read_body(
                    resp,
                    idle_timeout=sock_timeout,
                    total_timeout=total_timeout,
                    progress=progress,
                    allow_reroll=allow_reroll,
                    pace=pace,
                )
        except urllib.error.HTTPError as e:
            return e.code, e.read()


# ---- 新调度面（2026-09-22，plan/transfer-scheduling）：peek / priority / claim ----

def _sched_headers(worker_id: str, *, offline_ok: bool = False) -> dict[str, str]:
    """新面的公共头：worker 身份（必须） + 可选能力声明（离线课）。

    身份必须自报：隧道回源把全流量归成 127.0.0.1，源 IP 分不出 worker；而 hub 的
    `active_worker_count()`（避让链的唯一输入）就靠它计数——缺它避让链静默失效。
    """
    h: dict[str, str] = {}
    if worker_id:
        h[WORKER_ID_HEADER] = worker_id
    if offline_ok:
        h[OFFLINE_CAP_HEADER] = OFFLINE_CAP_VALUE
    return h


def _get_with_retry(
    base_url: str,
    token: str,
    path: str,
    *,
    timeout: float,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
    progress: Any = None,
    idle_timeout: float | None = BODY_IDLE_TIMEOUT_SEC,
    total_timeout: float | None = None,
    wire_jid: str = "",
    wire_seg: str = "",
    reroll: bool = False,
    bulk_prio: str = BULK_P1_CRITICAL,
) -> bytes:
    """GET + 瞬时失败退避重试：网络异常/5xx → 指数退避重试；4xx → ProtocolError。

    传输级抖动（连接重置/读超时/边缘 5xx）在租约窗口内就地消化，不再付
    「放弃本次 → 30min 租约过期 → 重领」的惩罚（2026-09-05，DECISIONS §340）。

    2026-09-20：body **分块读 + 空闲超时**（缺省 45s，见 BODY_IDLE_TIMEOUT_SEC）+
    可选进度回调 ⇒ 下载停滞从「静默等到 socket 超时」变成「45s 一条带字节数的
    停滞日志 + 退避重试」，且过程可见（进度行）。

    2026-09-20 追加（plan §4.0 / M1）：① `reroll=True` 时启用**低速重抽**——首块速率远低于
    阈值就断开重发（**不退避**：重抽的全部价值就在快）；② 成功即把 `(段名, bytes, sec)`
    记进本 job 的传输账（`_wire_flush` 打一行）。POST 不走这里（不重抽）。

    2026-09-22（P0 bulk 单通道）：每次尝试整体占一个 bulk 槽位（`bulk_prio` 缺省 P1 = 关键
    下载；预取路径传 `BULK_P2_PREFETCH`）。被 P1 挤走时 P2 抛 `BulkPreemptError`——**不背
    退避、立刻重排**，重试次数用完就让上层丢弃（预取是提前量，不是必须品）。槽位**不含**
    退避睡眠：绝不抱着唯一通道睡觉。
    """
    last: str = ""
    rerolls = 0
    for attempt in range(1, attempts + 1):
        # 重抽只在前几次尝试上开放：**最后一次必然老老实实传完**（否则慢链路就变成
        # 「永远下不完」——6 KB/s 的坏签确实存在，重抽是赌，不能把赌注全压在赌上）。
        allow_reroll = reroll and attempt < attempts and rerolls < WIRE_REROLL_MAX
        t_req = time.time()
        try:
            with _BULK.slot(bulk_prio, label=wire_seg or path) as _tok:
                status, body = _request(
                    base_url,
                    token,
                    path,
                    timeout=timeout,
                    progress=progress,
                    idle_timeout=idle_timeout,
                    total_timeout=total_timeout,
                    allow_reroll=allow_reroll,
                    pace=_bulk_pace(_tok, bulk_prio),
                )
        except BulkPreemptError as e:
            if attempt < attempts:
                log(f"bulk {wire_seg or path}: {e} —— 立即重排（{attempt}/{attempts}）")
                continue
            log(f"bulk {wire_seg or path}: {e} —— 重试次数用完，放弃这份提前量")
            raise
        except WireSlowError as e:
            rerolls += 1
            _wire_note_reroll(wire_jid, e.bytes_read)
            floor = _min_rate()
            log(
                f"wire: re-roll #{rerolls}/{WIRE_REROLL_MAX} {wire_seg or path}: "
                f"实测 {e.rate / 1024:.0f} KB/s < 阈值 {floor / 1024:.0f} KB/s"
                f"（按此速率剩余 {e.remain_sec:.0f}s）——断开重发（已收 {e.bytes_read} bytes 作废）"
            )
            continue  # 立即换连接重抽（不退避）
        except Exception as e:  # 网络层抖动（URLError/timeout/reset）
            status, body = None, repr(e).encode()
        if status == 200:
            elapsed = time.time() - t_req
            _wire_add(wire_jid, wire_seg, len(body), elapsed)
            if elapsed > 0:
                _note_rate(len(body) / elapsed, len(body))
            return body
        if status is not None and 400 <= status < 500:
            raise ProtocolError(f"{path} failed: HTTP {status}")
        last = (
            f"HTTP {status}" if status is not None else repr(body.decode("utf-8", "replace")[:120])
        )
        if attempt < attempts:
            backoff = min(2**attempt, 8)
            log(f"{path}: 瞬时失败({last}) — {backoff}s 后第 {attempt + 1}/{attempts} 次重试")
            time.sleep(backoff)
    raise RetryableError(f"{path} 重试 {attempts} 次仍失败: {last}")
