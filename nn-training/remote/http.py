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

import json
import os
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from typing import Any

from common import net_http
from common.protocol import (
    AUTH_HEADER,
    ROLE_HEADER,
    ROLE_HEADER_VALUE,
    ROLE_OFFLINE,
    ROLE_ONLINE,
    WORKER_ID_HEADER,
    ProtocolError,
    RetryableError,
)
from remote.bulk_sched import BULK_P1_CRITICAL, BULK_P2_PREFETCH, BulkPreemptError, control_path

# 控制面连接池（B2）：**单独成模块** —— `http.py` 顶层不许再多一份可变容器（护栏见
# `tests/remote/test_http_split.py::test_http_top_level_mutable_container_is_only_the_warn_throttle`）。
from remote.control_pool import (
    CONTROL_ATTEMPTS as CONTROL_ATTEMPTS,
)
from remote.control_pool import (
    CONTROL_POOL_ENABLED as CONTROL_POOL_ENABLED,
)
from remote.control_pool import (
    request as _control_pool_request,
)
from remote.wire import (
    _BULK,
    WIRE_PREEMPT_MAX,
    WIRE_PROBE_SEC,
    WIRE_REROLL_GAIN,
    WIRE_REROLL_MAX,
    WIRE_REROLL_PROBES,
    WireSlowError,
    _bulk_pace,
    _min_rate,
    _note_rate,
    _reroll_decision,
    _wire_add,
    _wire_note_failed,
    _wire_note_preempt,
    _wire_note_reroll,
    _wire_note_resend,
    _wire_note_wait,
)

__all__ = [
    "BODY_CHUNK",
    "BODY_IDLE_TIMEOUT_SEC",
    "BODY_PROGRESS_MIN_SEC",
    "BODY_TOTAL_TIMEOUT_SEC",
    "CONTROL_TIMEOUT_BULK_FACTOR",
    "_POLL_WARN_AT",
    "BodyTimeoutError",
    "_get_opener",
    "_get_with_retry",
    "_opener",
    "_paced_body",
    "_read_body",
    "_recv_some",
    "_request",
    "_sched_headers",
    "_warn_non_200",
]

# ------------------------------------------------------------------ HTTP 客户端

#: 控制面死线在 **bulk 在飞**时的放宽倍数（2026-10-12，抖动容忍 C）。
#: 6s × 2 = 12s 的旧行为在「bulk 占着链路」时是必死判据（真机：两次超时都落在 bulk 窗口内），
#: 放宽到 12s 让那一次有机会排队完成；配套 `_tries=1` ⇒ 最坏阻塞与旧行为持平。
#: ⚠ 放宽的那一次**不进 P0 延迟分位表**（`bulk_sched.control(sample=False)`），理由见那里。
CONTROL_TIMEOUT_BULK_FACTOR = 2.0

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


class BodyTimeoutError(TimeoutError):
    """带正文 + **结构化已收字节**的 body 异常（M3，2026-10-13）。

    `got` / `partial` / `total` 是给上游用的：失败尝试的账（wire 行的 `failed=`）要能读出
    「这一截收了多少」，而不是只拿到一个 `repr(e)`。

    ⚠ 没有 Range 续传（`docs/nn/remote-transport.md:1970` 仍是「先量后裁，未立项」）
    ⇒ 这截字节**仍然作废**，日志与文案不许写「续传起点」。

    挂在 `TimeoutError` 之下（而不是新造一族异常）：调用方与既有用例一律按
    `TimeoutError` 捕（那是一条已经存在 5 年的契约面）。
    """

    def __init__(self, msg: str, partial: bytes, total: int) -> None:
        super().__init__(msg)
        self.partial = partial
        self.got = len(partial)
        self.total = total


def _recv_some(resp: Any, limit: int) -> bytes:
    """**至多一次底层 `recv`** 的读（= `HTTPResponse.read1` 语义；2026-10-13，M1）。

    为什么不能再用 `resp.read(limit)`：`http.client` 的 `read(amt)` 是**循环 `recv` 直到凑满
    `amt` 或 EOF**，而 socket 超时是**每一次 `recv`** 的超时 ⇒ 隧道只要每 < `idle_timeout`
    吐 1 个字节，它既不返回也不超时，45 s 空闲判据**结构上永不触发**（现场：300 s 只凑满 1 块，
    而那一块也被丢掉）。`read1` 把「一次 recv」暴露给调用方，于是：① 空闲判据恢复语义；
    ② 速率判据能在**时间窗**上采样（M2），不再被块边界绑架。

    回退分支只服务**测试替身**（只实现 `read` 的假响应）：真 `HTTPResponse`（py3.3+）一定有
    `read1` ⇒ 生产路径不会静默退回定长读（`tests/remote/test_body_transfer_guard.py` 有一条
    real-socket parity 用例钉它）。
    """
    read1 = getattr(resp, "read1", None)
    block: bytes = read1(limit) if read1 is not None else resp.read(limit)
    return block


def _read_body(
    resp: Any,
    *,
    idle_timeout: float,
    total_timeout: float | None,
    progress: Any = None,
    allow_reroll: bool = False,
    pace: Any = None,
    offset: int = 0,
) -> bytes:
    """分块读 body：报进度 + **停滞/超预算即抛**（异常正文带已收字节数与原因）。

    `pace`（2026-09-22，P0）：每个分片间隙调一次的回调 —— 控制面在途时它会让路暂停，
    P2 预取被 P1 挤时它抛 `BulkPreemptError` 丢掉半截。**在读之前**调（停读 = TCP 窗口回填
    暂停，正是让控制面小包挤过去的方式）。

    净值判据（2026-09-25）：`pace` 的返回值 = 本次让路秒数，速率判据用
    `净值 elapsed = 墙钟 − Σ让路`（让路不是链路的错，不该把预取判成坏签）；
    **总超时与进度行仍按墙钟**（它们回答的是「这份传了多久」，含暂停才是诚实的）。

    迟滞（2026-10-12 / 2026-10-13）：重抽判据按**净时间窗**（`WIRE_PROBE_SEC`）采样，且要
    **连续 `WIRE_REROLL_PROBES` 个窗都慢**才断连 —— 抖动是常态，单个 3s 窗的瞬时速率必然
    误判（真机：首块 79KB/s ⇒ 重抽 ⇒ 换连接后 51KB/s，白扔 256KB）；「一次性 / 累计口径」
    在「快开后再降速」的链路上会把首块的好签相位当定论（现场那条 343.5 s 的行）。
    浪费上界仍是**有界**的（判满就收兵），不给「整份重传」开口子。

    `offset`（2026-10-12，Range 断点续传）：本段在**整份文件**里的起点（206 续传时 > 0）。
    三个口径分开：① 进度与「共 N」用**累计**（`offset + 本段已收`，人看的是整体进度）；
    ② 完整性判据只比本段声明的 `Content-Length`（累计口径会把续传误判成截断）；
    ③ 速率与「预计剩余」用**本段**（前缀不是这一次传的，算进去会虚高）。

    停滞异常必须**有正文**：上游 `_get_with_retry` 只把 `repr(e)` 写进日志，裸
    `TimeoutError()` 打出来是 `TimeoutError()`——等于没写（2026-09-20 事故现场）。

    2026-10-13（plan/body-transfer-guard-v2 §4，两处口径）：
      ① 读走 `_recv_some`（`read1`，M1）：定长读让空闲判据结构上永不触发（见该函数）；
      ② 低速判据改成**时间窗采样 + 连续 N 窗迟滞**（M2，`WIRE_PROBE_SEC` /
         `WIRE_REROLL_PROBES`）：旧形状（每块一次 + `probed` 一次性）在两条链路上都失效
         ——慢链路上样本要等一整块（≈300 s），`read1` 之后首块又太小 ⇒ 判一次
         `should=False` 就永久关掉自己（现场那条 343.5 s 的行就是这个形状）。
    """
    seg_len = 0
    try:
        seg_len = int(resp.headers.get("Content-Length") or 0)
    except Exception:  # 非标准响应 / 假响应（测试）没有 headers
        seg_len = 0
    total = offset + seg_len  # 累计口径（进度行 / 剩余秒数用它）
    buf = bytearray()
    t0 = time.time()
    last = t0
    t_yield = 0.0  # 让路累计（秒）：只喂速率判据，墙钟超时/进度仍用 now - t0
    # 观测窗（M2）：窗宽 ≥ `WIRE_PROBE_SEC`（**净值**：让路不算链路的错），窗内字节另计。
    win_net0 = t0  # 上一窗结束时的净值时刻
    win_got = 0  # 上一窗结束时的已收字节
    slow_streak = 0  # 连续多少个慢窗（迟滞本体；单窗不判，见 WIRE_REROLL_PROBES）
    while True:
        if pace is not None:
            try:
                t_yield += pace() or 0.0
            except BulkPreemptError as e:
                e.bytes_read = len(buf)  # 被挤走的半截要入账（否则预取成本恒等于 0）
                raise
        try:
            block = _recv_some(resp, BODY_CHUNK)
        except (TimeoutError, OSError) as e:
            raise BodyTimeoutError(
                f"body 停滞：{idle_timeout:.0f}s 内没有新字节（已收 {offset + len(buf)} bytes"
                + (f" / 共 {total}" if total else "")
                + "）——隧道或代理侧的问题，重试",
                bytes(buf),
                total,
            ) from e
        if not block:
            if seg_len and len(buf) < seg_len:
                # ★ 2026-10-03（与引导期护栏同一处缺陷的孪生修复）：`http.client` 对
                #   `read(amt)` 的提前 EOF **故意不抛** IncompleteRead（兼容）⇒ 旧行为把半截
                #   body 当成功返回。收满声明长度才算成功；否则当瞬时失败（上层退避重取）。
                #   ⚠ 判据用**本段**声明长度（`seg_len`）而不是累计口径 `total`：续传时
                #   `offset>0`，拿累计口径比会把每一条正常的 206 都误判成「被截断」。
                raise BodyTimeoutError(
                    f"body 提前结束（连接被截断）：已收 {offset + len(buf)} bytes / 共 {total}"
                    "——隧道或代理侧的问题，重试",
                    bytes(buf),
                    total,
                )
            return bytes(buf)
        buf += block
        now = time.time()
        if allow_reroll:
            # 净值 = 墙钟 − 让路：让路不是链路的错，否则「停满让路预算」会被判成坏签。
            # 采样按**净时间窗**（M2）：窗内字节与净值秒是判据的输入，不再被块边界（定长读）绑架。
            net_now = max(0.0, now - t_yield)
            win_elapsed = net_now - win_net0
            if win_elapsed >= WIRE_PROBE_SEC:
                should, rate, remain = _reroll_decision(
                    len(buf) - win_got,
                    total,
                    win_elapsed,
                    # 窗宽与判据用同一把尺（`probe_sec` 是判据自己的「样本够大」门）：
                    # 窗已证明「至少等了这么久」⇒ 两处必须读同一个常量，否则调窗宽时判据会静默失灵。
                    probe_sec=WIRE_PROBE_SEC,
                    remaining=(total - len(buf)) if total else 0,
                )
                win_net0, win_got = net_now, len(buf)
                if should:
                    slow_streak += 1
                    if slow_streak >= WIRE_REROLL_PROBES:
                        # 累计口径（`offset + len(buf)`）+ 本段字节（`partial`）：调用方据此续传与记账。
                        raise WireSlowError(
                            offset + len(buf), rate, remain, partial=bytes(buf)
                        )
                else:
                    slow_streak = 0
        if total_timeout is not None and now - t0 > total_timeout:
            raise BodyTimeoutError(
                f"body 超时：总耗时 > {total_timeout:.0f}s（已收 {offset + len(buf)} bytes"
                + (f" / 共 {total}" if total else "")
                + "）——链路太慢，重试",
                bytes(buf),
                total,
            )
        if progress is not None and now - last >= BODY_PROGRESS_MIN_SEC:
            last = now
            progress(offset + len(buf), total, now - t0)


#: "base_url:status" -> 上次告警墙钟（节流：非 200 时每分钟最多一条，别刷屏）
_POLL_WARN_AT: dict[str, float] = {}


def _reject_reason(body: bytes) -> str:
    """从 hub 的错误体里抠出 `error` 文案（拿不到就空串）。

    2026-09-24 事故：worker 的兜底文案把 409 说成「hub 异常」，而 hub 明明回了
    `{"error": "claim 被拒: held (…)"}` —— 响应体被丢掉了。这里把它捡回来。
    """
    if not body:
        return ""
    try:
        got = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(got, dict):
        return ""
    return str(got.get("error") or "").strip()


def _warn_non_200(
    base_url: str,
    status: int,
    log: Any,
    *,
    body: bytes = b"",
    jid: str = "",
) -> None:
    """非 200 的节流告警（（url, status）每分钟最多一条）。

    为什么要区分「队列空」与「被拒」（2026-09-16 x3-step 事故）：非 200 一律静默的话，
    被 403 ip blocked 的 worker 日志与空队列完全一样（只有 "no job yet"），现场无法判断。

    ★ 文案按状态分派（2026-09-24 job 身份事故）：原实现对**所有**非 200 都写
    「hub 异常，请检查 hub 进程与隧道」，于是 409（调度面拒绝：被持有/冻结/归属歧义）
    被读成 hub 崩了——现场因此查错了方向。**409 是调度面的确定性拒绝，不是故障**；
    同时把 hub 给的 reason 与 jid 打进同一行（否则只有一个状态码，仍然查不动）。
    """
    if log is None:
        return
    now = time.time()
    key = f"{base_url}:{status}"
    if now - _POLL_WARN_AT.get(key, 0.0) <= 60:
        return
    _POLL_WARN_AT[key] = now
    if status in (401, 403):
        hint = "鉴权失败或该 IP 已被 hub 封禁——检查 --token 与 hub 日志 AUTH FAIL/BLOCKED"
    elif status == 409:
        hint = "调度面拒绝（被持有/冻结/归属歧义）——**不是** hub 故障；换一份活或等租约"
    elif status == 404:
        hint = "job 或归属解析不到（unknown）——旧 runId/旧代码留下的同名 job 目录？"
    elif status >= 500:
        hint = "hub 异常，请检查 hub 进程与隧道"
    else:
        hint = "hub 未预期地拒了这次请求"
    why = _reject_reason(body)
    log(
        f"调度请求 {base_url}: HTTP {status} — {hint}（这不是「队列空」）"
        + (f" [job={jid}]" if jid else "")
        + (f" hub 说：{why}" if why else "")
    )


def _paced_body(data: bytes, pace: Any) -> Any:
    """把上传 body 交成**分片迭代器**：每片之间调一次 `pace()`（2026-10-12，抖动容忍 B）。

    为什么用迭代器而不是自己写 socket：urllib 的**代理装配**（CONNECT 隧道 / 绝对 URI）不想
    重写一遍；而 `http.client.send()` 对可迭代对象本来就是一拍一片 `sendall`。前提是**显式给
    `Content-Length`** —— `http.client._set_content_length` 对"算不出长度的可迭代对象"会改用
    `Transfer-Encoding: chunked`，而 hub 只认 Content-Length（它会一直等满声明的字节）。
    """
    view = memoryview(data)
    for i in range(0, len(view), BODY_CHUNK):
        yield bytes(view[i : i + BODY_CHUNK])
        # 片**之后**让路：暂停发生在下一片之前 = 把这一小段带宽让给控制面小包。
        # P1 不会被抢断（`check_preempted` 只对 P2 抛），所以这里不接 `BulkPreemptError`。
        if pace is not None:
            pace()


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
    range_start: int = 0,
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

    `range_start > 0`（2026-10-12，Range 断点续传）⇒ 带 `Range: bytes=N-`，并在服务端回 **206**
    时把 `offset=N` 交给 `_read_body`（三个口径要对齐，见那里）；回 **200** 时按 `offset=0` 读
    （它忽略了 Range ⇒ 这一份是从头传的整份）。**上传也让路**：`data` 大且给了 `pace` 就交成
    分片迭代器（`_paced_body`）。
    """
    hdr_extra = dict(headers or {})
    if range_start > 0:
        hdr_extra["Range"] = f"bytes={int(range_start)}-"
    body: Any = data
    if data is not None and pace is not None and len(data) > BODY_CHUNK:
        # 上传让路（B）：`pace` 原先只接在下载侧（`_read_body`），result POST 的 body 由 urllib
        # 一次 sendall ⇒ 整个上传窗口里 bulk 侧**零让路点**，控制面小包只能硬等（真机：21.6s 的
        # 上传窗口内 acquire 12s 超时）。显式 Content-Length 见 `_paced_body` 的注释。
        hdr_extra["Content-Length"] = str(len(data))
        body = _paced_body(data, pace)
    url = f"{base_url.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={AUTH_HEADER: f"Bearer {token}", **hdr_extra},
        method=method,
    )
    stream = idle_timeout is not None or progress is not None
    sock_timeout = idle_timeout if idle_timeout is not None else timeout
    _is_ctl = control_path(path) and not stream
    # C（2026-10-12，抖动容忍）：**有 bulk 在飞 ⇒ 控制面必然排队**，固定 6s 死线在这类链路上是
    # 必死判据（真机：acquire 12s 超时 + 5s 退避 ≈ 17s 空转，两次都落在 bulk 窗口内）。放宽 =
    # 让这次请求有机会**排在 bulk 之后完成**，省掉整轮重试；配套把尝试次数收成 1，使最坏阻塞
    # 时长与旧行为持平（旧 = 6s×2，新 = 12s×1）。
    # ⚠ 这类请求**不进 P0 延迟分位表**（`sample=False`）：那张表是 `yield_worth_it()` 判「让路
    # 买不回来 ⇒ 关掉让路」的输入，前提是「P0 从不排队 ⇒ 尾部只能来自链路本身」；把一次
    # **我们允许它慢**的等待记进去，只会让自停判据更早关掉让路（自伤）。
    _widened = bool(_is_ctl and _BULK.inflight() > 0)
    if _widened:
        sock_timeout = sock_timeout * CONTROL_TIMEOUT_BULK_FACTOR
    # 控制面标记（P0）：命中即让 bulk 在分片间隙让路；控制面自己**不进** bulk 队列。
    ctl = (
        _BULK.control(label=path, sample=not _widened) if control_path(path) else nullcontext()
    )
    # B2：控制面走连接池（省每请求一次 TLS 握手 / 建连）。**三个条件同时成立**才走池：
    #   ① 池开着；② 这条是控制面（`control_path`）；③ **非回环**（本机 hub 走 net_http.urlopen，
    #   绕代理是它的既有语义，不能改）。bulk（大 body）永远不进池。
    _use_pool = (
        CONTROL_POOL_ENABLED
        and control_path(path)
        and progress is None
        and idle_timeout is None
        and not net_http.is_loopback(url)
    )
    # B3：控制面**短预算 + 立刻换连接重抽一次**（plan §3.5）。只给幂等的控制面小请求
    # （无 body 下载那一套），且**不退避**——退避就是接着干等，与「把 30s 切成 6s×N」相反。
    # `_widened` 时只发一次：那条请求已经拿到 2× 死线，再来一次会把最坏阻塞翻倍。
    _tries = CONTROL_ATTEMPTS if (_is_ctl and not _widened) else 1
    _last: BaseException | None = None
    with ctl:
        for _t in range(_tries):
            try:
                if _use_pool:
                    return _control_pool_request(
                        url,
                        method=method or ("POST" if data is not None else "GET"),
                        headers={AUTH_HEADER: f"Bearer {token}", **hdr_extra},
                        data=data,
                        timeout=sock_timeout,
                    )
                # 回环（本机 hub）绕开代理；非回环走进程内的显式 ProxyHandler（Colab 实测需要）。
                open_fn = net_http.urlopen if net_http.is_loopback(url) else _get_opener().open
                with open_fn(req, timeout=sock_timeout) as resp:
                    if not stream or resp.status not in (200, 206):
                        return resp.status, resp.read()
                    # 206 ⇒ 服务端**接受**了 Range（本段起点 = 请求的 range_start）；
                    # 200 ⇒ 它忽略了 Range（旧 hub / 中间代理剥头）⇒ 这一份是从 0 传的整份，
                    #        必须按 offset=0 读，否则进度/剩余/完整性三个口径全错。
                    return resp.status, _read_body(
                        resp,
                        idle_timeout=sock_timeout,
                        total_timeout=total_timeout,
                        progress=progress,
                        allow_reroll=allow_reroll,
                        pace=pace,
                        offset=int(range_start) if resp.status == 206 else 0,
                    )
            except urllib.error.HTTPError as e:
                return e.code, e.read()
            except Exception as e:  # 网络层：立刻换连接重抽（池内已丢连接；非池本就是新连接）
                _last = e
        if _last is not None:
            raise _last
    raise RuntimeError(f"_request: 控制面 {path} 重试耗尽却无结果（不应到这里）")


# ---- 新调度面（2026-09-22，plan/transfer-scheduling）：peek / priority / claim ----

def _sched_headers(worker_id: str, *, role: str = ROLE_ONLINE) -> dict[str, str]:
    """新面的公共头：worker 身份（必须） + 归属角色（离线盘才带）。

    身份必须自报：隧道回源把全流量归成 127.0.0.1，源 IP 分不出 worker；而 hub 的
    `active_worker_count()`（避让链的唯一输入）就靠它计数——缺它避让链静默失效。

    `role`（2026-09-25）：原来这里叫「能力声明」，现在语义是**归属**（本会话属于哪块盘）；
    头名与取值不变（`X-Battle-Offline: 1`，见 `protocol.ROLE_HEADER` 的理由）。
    ⚠ 调用点必须**都**带它（peek 与 claim 两条 HTTP 面，`acquire_job` 内部两跳都在）：
    只在 peek 上带的话，带标 worker 会在 `claim` 那一步被归属闸
    （`_JobStore._claim_locked`）当成 online 当场拒掉——**自锁**（peek 绿、claim 红）。
    """
    h: dict[str, str] = {}
    if worker_id:
        h[WORKER_ID_HEADER] = worker_id
    if role == ROLE_OFFLINE:
        h[ROLE_HEADER] = ROLE_HEADER_VALUE
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
    退避、立刻重排**；重排有**独立预算** `WIRE_PREEMPT_MAX`（2026-09-25：不消耗 `attempts`，
    上限用完仍抛 `BulkPreemptError`，因为挤走不是失败）。槽位**不含**退避睡眠：绝不抱着唯一
    通道睡觉。
    """
    last: str = ""
    rerolls = 0
    preempts = 0  # 被 P1 挤走的次数（P2 专属）：独立预算，不消耗 attempts
    attempt = 1
    #: 重抽开关（2026-10-12）：一次「抽了没用」即永久关闭（本函数内）。见下面的 `WireSlowError`。
    reroll_ok = reroll
    #: 上一次重抽前的实测速率（判「换连接到底有没有变好」；第一抽没有对比对象，允许抽）。
    prev_rate = 0.0
    #: **跨尝试累积**的字节（2026-10-12，Range 断点续传）：`WireSlowError.partial` 并进来，
    #: 下次请求带 `Range: bytes=<offset>-` 接着传 —— 断连不再把已收的那截扔掉。
    parts = bytearray()
    # ★ 2026-10-04（x20-adv-acc 现场）：base_url 为空 = **确定性配置错误**，重试不会变好 ——
    #   旧行为是 urllib 抛 `ValueError: unknown url type: '/jobs/…/blob?name=opt'`，被当成
    #   瞬时抖动退避重试 3 次（白等 6s+，日志还引导不到根因）。最常见来源：自主段/离线段的
    #   合成轮用 `run_job("", "")` 跑——blob 必须随包或锚点带到节点并种子进 blob_cache
    #   （见 `plan_handoff._seed_opt_blob_cache` / `_seed_ref_blob_cache`，以及 `import_bundle`
    #   对 `opt.tar` 的落位）；在线腿则是 --hub-url/hub_ip 没给。别抱着唯一通道睡觉之前
    #   先把这条判掉：下面进来就会占一个 bulk 槽位。
    if not str(base_url or "").strip():
        raise ProtocolError(
            f"没有 hub 地址（base_url 为空）却要下载 {path} —— 确定性配置错误，重试不会变好："
            "blob/载荷必须已在节点本地（自主段没有下载通道），检查任务包/锚点是否带全"
            "（opt/ref/demo 的种子链）或在线腿的 --hub-url/hub_ip"
        )
    while attempt <= attempts:  # 用 while 而非 for：挤走时要**原地**重排，不推进 attempt
        # 重抽只在前几次尝试上开放：**最后一次必然老老实实传完**（否则慢链路就变成
        # 「永远下不完」——6 KB/s 的坏签确实存在，重抽是赌，不能把赌注全压在赌上）。
        allow_reroll = reroll_ok and attempt < attempts and rerolls < WIRE_REROLL_MAX
        t_req = time.time()  # 本次尝试的**墙钟**（含排队）：只喂 `_note_rate`（保守侧）
        t_xfer = t_req  # 进槽后重取：段账只算**真实传输**（排队归调度账 `wait=`）
        got = 0  # 本次尝试已收字节（M3：网络层异常自带 `.got` 时才非零）
        try:
            # label 带优先级（S3a/W0）：现场日志里 `bulk payload/P1` 与 `bulk payload/P2` 一眼可分
            #（否则「排队 3.9s」到底压在关键下载还是无害预取上，结构上判不出来）。
            with _BULK.slot(bulk_prio, label=f"{wire_seg or path}/{bulk_prio}") as _tok:
                t_xfer = time.time()  # ★ 排队结束、开传那一刻
                status, body = None, b""
                try:
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
                        range_start=len(parts),
                    )
                finally:
                    # S3d：本次排队归到**本 jid** 的账（失败/被挤走的排队也要记——那是真花掉的时间）。
                    _wire_note_wait(wire_jid, wire_seg, _BULK.take_wait(_tok))
        except BulkPreemptError as e:
            _wire_note_preempt(wire_jid, int(getattr(e, "bytes_read", 0) or 0))
            # R6：被挤走也是一次**未成功**的尝试 —— 时间也要进账（字节由 preempt= 单独报）。
            _wire_note_failed(
                wire_jid, int(getattr(e, "bytes_read", 0) or 0), time.time() - t_xfer
            )
            preempts += 1
            if bulk_prio == BULK_P2_PREFETCH:
                # 挤走不是失败：重排有独立预算，**不消耗 attempts**。耗尽时抛的仍是
                # `BulkPreemptError`（不是 `RetryableError`）——`_prefetch_fill` 只吞前者，
                # 落后者会把「丢半截」误报成瞬时故障。
                if preempts <= WIRE_PREEMPT_MAX:
                    log(
                        f"bulk {wire_seg or path}: {e} —— 立即重排"
                        f"（preempt {preempts}/{WIRE_PREEMPT_MAX}；不消耗重试预算）"
                    )
                    continue
                log(
                    f"bulk {wire_seg or path}: {e} —— 抢占重排上限"
                    f"（{WIRE_PREEMPT_MAX}）用完，放弃这份提前量"
                )
                raise
            # P1 不会被抢占（不变量：POST 大 body 没有安全 Range）——保留原语义。
            if attempt < attempts:
                log(f"bulk {wire_seg or path}: {e} —— 立即重排（{attempt}/{attempts}）")
                attempt += 1
                continue
            log(f"bulk {wire_seg or path}: {e} —— 重试次数用完，放弃这份提前量")
            raise
        except WireSlowError as e:
            rerolls += 1
            # 续传（2026-10-12）：这一截**不作废**——并进累积缓冲，下次带 `Range` 接着传。
            # 所以 `wasted` 记 0（真作废只可能来自「服务端忽略 Range」，由 `_wire_note_resend` 记）。
            parts += e.partial
            _wire_note_reroll(wire_jid, 0)
            # R6（2026-10-13）：重抽也是「未成功的一次」——花掉的**时间**归 failed=（作废的**字节**
            # 由 reroll= 报，续传口径下恒 0）。字节用**本段**（`partial`）而非累计 `bytes_read`：
            # 带 Range 时累计口径会把前几次尝试的字节重复计入。
            _wire_note_failed(wire_jid, len(e.partial), time.time() - t_xfer)
            floor = _min_rate()
            log(
                f"wire: re-roll #{rerolls}/{WIRE_REROLL_MAX} {wire_seg or path}: "
                f"实测 {e.rate / 1024:.0f} KB/s < 阈值 {floor / 1024:.0f} KB/s"
                f"（按此速率剩余 {e.remain_sec:.0f}s）——断开续传（续传起点 {len(parts)} bytes）"
            )
            # 无收益即停（2026-10-12，抖动是常态）：换一条连接还是这个速率 ⇒ **不是坏签，是链路
            # 整体降速**。再抽只是把一整块（256KB）一遍遍扔掉 —— 真机证据：重抽前后 79 → 51 KB/s，
            # 越抽越差，而每次重抽白扔 256KB + 一次建连。判据只能放在这里（`_reroll_decision` 手里
            # 只有单次样本，没有「上一次」）。⚠ 只关**本函数内**的重抽，不触碰全局状态。
            if prev_rate > 0.0 and e.rate < prev_rate * WIRE_REROLL_GAIN:
                reroll_ok = False
                log(
                    f"wire: 重抽无收益（{prev_rate / 1024:.0f} → {e.rate / 1024:.0f} KB/s）"
                    "——判定为链路整体降速而非坏签，停止重抽，按当前速率传完"
                )
            prev_rate = max(prev_rate, e.rate)
            attempt += 1  # 重抽仍消耗 attempts（与 for 版逐字等价：continue 会推进）
            continue  # 立即换连接重抽（不退避）
        except Exception as e:  # 网络层抖动（URLError/timeout/reset）
            status, body = None, repr(e).encode()
            # M3/R6：`_read_body` 抛的超时带已收字节（`.got`）——失败账要能读出它。
            got = int(getattr(e, "got", 0) or 0)
        if status == 206:
            # 续传的尾巴（服务端接受了 `Range`）⇒ 拼上去就是整份。
            parts += body
        elif status == 200:
            if parts:
                # 带了 `Range` 却回 200 = 服务端**忽略了 Range**（旧 hub / 中间代理剥头）⇒
                # 这一份是从 0 传的整份，之前那截真的白传了：如实记账（只记浪费，不加重抽次数）。
                _wire_note_resend(wire_jid, len(parts))
                log(
                    f"wire: {wire_seg or path} 服务端不支持 Range（回 200）"
                    f"——已收 {len(parts)} bytes 作废，本次从头传"
                )
                parts = bytearray()
            parts = bytearray(body)
        if status in (200, 206):
            # 两个口径分开（2026-09-25）：
            #   `elapsed`（墙钟，含排队）⇒ 只喂 `_note_rate`（保守侧，不抬高 `_min_rate`）；
            #   段账用**纯传输**秒数 —— 否则与 hub 侧「响应发送完成 … in Xs」无法对账
            #   （现场：worker 侧 `payload=…/321.8s` 里含 `排队 17.6s 才拿到单通道`）。
            elapsed = time.time() - t_req
            _wire_add(wire_jid, wire_seg, len(body), time.time() - t_xfer)
            if elapsed > 0:
                _note_rate(len(body) / elapsed, len(body))
            return bytes(parts)
        if status is not None and 400 <= status < 500:
            # 4xx 是**确定性的答案**（鉴权/归属/队列拒绝），不是传输失败 —— 不进 failed 账。
            raise ProtocolError(f"{path} failed: HTTP {status}")
        # R6（2026-10-13）：这一次尝试没成功（瞬时网络错 / 非 200）——它的**传输墙钟**与已收
        # 字节进账。不入账的话，现场那 300 s 只会以「other 巨大」的形式出现（P0.5 归不了因）。
        _wire_note_failed(
            wire_jid, len(body) if status is not None else got, time.time() - t_xfer
        )
        last = (
            f"HTTP {status}" if status is not None else repr(body.decode("utf-8", "replace")[:120])
        )
        if attempt < attempts:
            backoff = min(2**attempt, 8)
            log(f"{path}: 瞬时失败({last}) — {backoff}s 后第 {attempt + 1}/{attempts} 次重试")
            time.sleep(backoff)
        attempt += 1
    raise RetryableError(f"{path} 重试 {attempts} 次仍失败: {last}")
