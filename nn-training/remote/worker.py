"""remote/worker.py — 云端 PPO worker（无状态、可重连；`python -m remote_worker`）。

循环语义（D1/D8/D12）：轮询 hub-server → 下载 payload → 解包 → 校验
（payload_sha256 / commit / mode）→ 复用 ppo backend 链路
（load_episodes → chunk_episodes → ppo_update）→ 同 commit 调
`save_weights_json` 产出 weights_json（D12/G1 产出方锁死）→ `_ppo_save`
打 tar（model/opt/RNG，D5）→ POST 结果。断线重连 / 幂等重拉由本模块自理。

所有 torch 依赖**延迟到 run_job 内**导入——本模块顶层零 torch（hub 侧
（hub_server / hub_client）与协议单测均不拉 torch；云端才真正加载）。

确定性（D5）：per-job 种子 = hash(runId, it, init_weights_fp)，load/chunk/update
前重新播种——同 job 重发 chunk 逐字节一致。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from remote import net_http
from remote.iter_rollout import run_iter_rollout
from remote.protocol import (
    AUTH_HEADER,
    BLOB_OPT,
    BLOB_REF,
    HEARTBEAT_SEC,
    HUB_SCOPE_HEADER,
    OFFLINE_CAP_HEADER,
    OFFLINE_CAP_VALUE,
    PAYLOAD_NAME,
    WIRE_V2_CONTENT_TYPE,
    WORKER_ID_HEADER,
    CodeChangedError,
    ProtocolError,
    RetryableError,
    coef_active,
    decode_opt_tar,
    decode_weights_json,
    encode_opt_tar,
    encode_weights_json,
    job_seed,
    normalize_manifest,
    pack_result_v2,
    unpack_payload,
    validate_result,
)
from remote.protocol import (
    d14_corpus_match as protocol_d14_corpus_match,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


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

# ── 低速重抽（2026-09-20；plan/minimize-payload.plan.md §4.0 / M1）───────
#: 坏签（连接抽签抽到慢连接）时**主动断开重发**：重抽成本 ≈1 s 建连，收益 ≈100 s。
#: 实测依据（同机同 hub）：code GET 13.5 KB/s，而 3 秒后的 blob GET ≥120 KB/s；
#: 12:59 那次 payload 以 6–9 KB/s 烧完 300 s 预算（到 75% 被总预算判死），
#: **重试换连接后 354 KB/s** 跑完剩下 1.75 MB——那次重试其实就是一次「意外重抽」。
WIRE_MIN_RATE = 80 * 1024.0
#: 判据所需的最小观测：样本太小不下结论。
WIRE_PROBE_BYTES = 128 * 1024
WIRE_PROBE_SEC = 3.0
#: 按当前速率**预计剩余**超过它才值得折腾（快跑完了就不动）。
WIRE_REROLL_BUDGET_SEC = 20.0
#: 单次下载的重抽上限（**只给幂等 GET**；POST result 永不重抽）。
WIRE_REROLL_MAX = 3
#: 会话最好速率的采样最小体量（小 body 的瞬时速率不代表链路，不参与判据）。
WIRE_RATE_SAMPLE_MIN_BYTES = 256 * 1024
#: 未 flush 的 job 传输账上限（push 模式没有 pull 循环的 flush 点）。
WIRE_MAX_JOBS = 4

#: 本会话已观测到的最好大 body 速率（bytes/s）——相对判据的参照。
_BEST_RATE = 0.0

#: 每 job 的传输账：jid -> {segs: {段名: [bytes, sec]}, hits: {段名: 说明},
#: wasted: 重抽作废字节, rerolls: 次数}。跑完由 `_wire_flush` 打一行摘要并清空。
_WIRE: dict[str, dict] = {}


def _note_rate(rate: float, nbytes: int) -> None:
    """记下本会话的**大 body** 最好速率（相对判据的参照）。"""
    global _BEST_RATE
    if nbytes >= WIRE_RATE_SAMPLE_MIN_BYTES and rate > _BEST_RATE:
        _BEST_RATE = rate


def _min_rate() -> float:
    """坏签判据 = max(`WIRE_MIN_RATE`, 本会话最好速率 / 4)——只在「明显偏离」时动手。"""
    return max(WIRE_MIN_RATE, _BEST_RATE / 4.0)


def _reroll_decision(
    got: int,
    total: int,
    elapsed: float,
    *,
    min_rate: float | None = None,
    budget_sec: float = WIRE_REROLL_BUDGET_SEC,
    probe_bytes: int = WIRE_PROBE_BYTES,
    probe_sec: float = WIRE_PROBE_SEC,
) -> tuple[bool, float, float]:
    """是否该断开重抽 → `(决定, 实测速率, 按此速率的预计剩余秒数)`。

    **纯函数**：判据只有一处实现（改阈值只改这里），也就能被单测直接钉住。
    """
    if total <= 0 or got <= 0 or got >= total:
        return False, 0.0, 0.0  # 长度未知 / 已收完 / 零字节：都不判
    if elapsed < probe_sec and got < probe_bytes:
        return False, 0.0, 0.0  # 样本太小，不下结论
    rate = got / elapsed if elapsed > 0 else float("inf")
    floor = _min_rate() if min_rate is None else min_rate
    remain_sec = (total - got) / rate if rate > 0 else float("inf")
    return (rate < floor and remain_sec > budget_sec), rate, remain_sec


class WireSlowError(Exception):
    """慢连接（速率远低于阈值）：**放弃本次传输、换连接重抽**。

    带正文（已收字节数 / 速率 / 预计剩余）——与裸 `TimeoutError()` 的教训同规：日志里必须
    能读出「为什么断」，否则运维只看到「又重试了」。
    """

    def __init__(self, bytes_read: int, rate: float, remain_sec: float) -> None:
        super().__init__(
            f"慢连接：实测 {rate / 1024:.0f} KB/s（已收 {bytes_read} bytes，"
            f"按此速率剩余 {remain_sec:.0f}s）"
        )
        self.bytes_read = bytes_read
        self.rate = rate
        self.remain_sec = remain_sec


def _wire_bucket(jid: str) -> dict:
    """取（或建）某 job 的传输账——并**封顶**未 flush 的 job 数（防 push 模式无界增长）。"""
    w = _WIRE.get(jid)
    if w is None:
        while len(_WIRE) >= WIRE_MAX_JOBS:
            _WIRE.pop(next(iter(_WIRE)), None)  # 最旧的直接丢（它的账已过期）
        w = _WIRE[jid] = {"segs": {}, "hits": {}, "wasted": 0, "rerolls": 0}
    return w


def _wire_add(jid: str, seg: str, nbytes: int, sec: float) -> None:
    """记一段**成功传输**（`(endpoint, bytes, sec)` 的原始账）。"""
    if not jid or not seg or nbytes <= 0:
        return
    cur = _wire_bucket(jid)["segs"].setdefault(seg, [0, 0.0])
    cur[0] += int(nbytes)
    cur[1] += float(sec)


def _wire_hit(jid: str, seg: str, why: str = "cache") -> None:
    """记一次**零字节**命中（内容寻址缓存 / preloaded）——摘要里也要看得见。"""
    if not jid or not seg:
        return
    _wire_bucket(jid)["hits"][seg] = why


def _wire_note_reroll(jid: str, wasted: int) -> None:
    """记一次重抽（及其作废字节）——坏签比例就靠它统计。"""
    if not jid:
        return
    w = _wire_bucket(jid)
    w["rerolls"] += 1
    w["wasted"] += int(wasted)


def _wire_flush(jid: str, log) -> None:
    """打**一行**本 job 的传输账并清掉：`wire payload=… code=cache-hit reroll=1 合计=…`。

    为什么必须有一行：逐条进度行看不出全局，而「坏签比例 / 命中比例 / 哪一段在吃时间」
    只能从每 job 一行的账里读（与 hub 侧 `_bytes` 的完成行对账即可定位慢腿）。
    """
    w = _WIRE.pop(jid, None)
    if not w:
        return
    mb = 1024.0 * 1024.0
    parts: list[str] = []
    tot_b = 0
    tot_s = 0.0
    for seg, (n, sec) in w["segs"].items():
        tot_b += n
        tot_s += sec
        rate = n / sec / 1024.0 if sec > 0 else 0.0
        parts.append(f"{seg}={n / mb:.2f}MB/{sec:.1f}s({rate:.0f}KB/s)")
    for seg, why in w["hits"].items():
        parts.append(f"{seg}={why}-hit")
    if w["rerolls"]:
        parts.append(f"reroll={w['rerolls']}(wasted {w['wasted'] / mb:.2f}MB)")
    rate_all = tot_b / tot_s / 1024.0 if tot_s > 0 else 0.0
    log(
        f"job {jid}: wire "
        + " ".join(parts)
        + f" 合计={tot_b / mb:.2f}MB/{tot_s:.1f}s({rate_all:.0f}KB/s)"
    )


def _read_body(
    resp: Any,
    *,
    idle_timeout: float,
    total_timeout: float | None,
    progress: Any = None,
    allow_reroll: bool = False,
) -> bytes:
    """分块读 body：报进度 + **停滞/超预算即抛**（异常正文带已收字节数与原因）。

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


def _progress_logger(label: str, log: Any):
    """进度行工厂：`job X: payload 下载中 3.20 MB / 4.85 MB (66%) 用时 12s（270 KB/s）`。"""

    def _report(got: int, total: int, elapsed: float) -> None:
        mb = 1024.0 * 1024.0
        rate = (got / elapsed / 1024.0) if elapsed > 0 else 0.0
        pct = f" ({got * 100 // total}%)" if total > 0 else ""
        of = f" / {total / mb:.2f} MB" if total > 0 else ""
        log(f"{label} 下载中 {got / mb:.2f} MB{of}{pct} 用时 {elapsed:.0f}s（{rate:.0f} KB/s）")

    return _report


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
) -> tuple[int, bytes]:
    """单发 GET/POST。缺省（不给 `idle_timeout`/`progress`）行为与改造前逐字节相同。

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
            )
    except urllib.error.HTTPError as e:
        return e.code, e.read()


#: "base_url:status" -> 上次告警墙钟（节流：非 200 时每分钟最多一条，别刷屏）
_POLL_WARN_AT: dict[str, float] = {}


def poll_job(
    base_url: str,
    token: str,
    timeout: float = 30.0,
    log: Any = None,
    hub_scope: int | None = None,
    worker_id: str = "",
    offline_ok: bool = False,
) -> dict | None:
    """GET /jobs/next → {job_id, manifest, halt} 或 None（无任务且无停机达令）。

    `offline_ok`（2026-09-19 离线训练模式）：本会话**能自己跑完整段**（`kind="run"`）时
    带上能力头 —— hub 只把离线课的 job 放给带标的 worker（普通逐轮 worker 领不到）。
    这是能力声明，不是课程绑定：带标 worker 照样领在线课。

    `hub_scope` / `worker_id`（2026-09-17 竞速广播）：本 worker 轮询几个 hub、以及自己
    是谁。hub 靠这两个事实判定“机群是不是只服务单一课程”——**不报就按多 hub 保守**
    （退回 P3b 独占）。身份必须自报：隧道回源把全流量归成 127.0.0.1，源 IP 分不出 worker。

    停机达令（§386）随任务同发：有任务 → 原样上浮（含 halt 标志，worker 先试停机、
    停不掉照常执行任务）；无任务但 halt → {"halt": True}；两者皆无 → None。

    `log` 用于**区分「队列空」与「被 hub 拒绝」**（2026-09-16 x3-step 事故）：
    此前非 200 一律静默返回 None，worker 被 403 ip blocked 时日志与空队列完全
    一样（只有 "no job yet"），现场无法判断到底是没活还是被封。现在非 200 会
    按 (url, status) 节流打印一条。"""
    _poll_headers: dict[str, str] = {}
    if worker_id:
        _poll_headers[WORKER_ID_HEADER] = worker_id
    if hub_scope is not None:
        _poll_headers[HUB_SCOPE_HEADER] = str(int(hub_scope))
    if offline_ok:
        _poll_headers[OFFLINE_CAP_HEADER] = OFFLINE_CAP_VALUE
    status, body = _request(
        base_url, token, "/jobs/next", timeout=timeout, headers=_poll_headers or None
    )
    if status != 200:
        if log is not None:
            now = time.time()
            key = f"{base_url}:{status}"
            if now - _POLL_WARN_AT.get(key, 0.0) > 60:
                _POLL_WARN_AT[key] = now
                hint = (
                    "鉴权失败或该 IP 已被 hub 封禁——检查 --token 与 hub 日志 AUTH FAIL/BLOCKED"
                    if status in (401, 403)
                    else "hub 异常，请检查 hub 进程与隧道"
                )
                log(f"poll {base_url}: HTTP {status} — {hint}（这不是「队列空」）")
        return None
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        return None
    if data.get("job_id"):
        return data  # 有任务：halt 标志随任务同行（达令+任务同批）
    if data.get("halt") is True:
        return {"halt": True}
    return None


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
    """
    last: str = ""
    rerolls = 0
    for attempt in range(1, attempts + 1):
        # 重抽只在前几次尝试上开放：**最后一次必然老老实实传完**（否则慢链路就变成
        # 「永远下不完」——6 KB/s 的坏签确实存在，重抽是赌，不能把赌注全压在赌上）。
        allow_reroll = reroll and attempt < attempts and rerolls < WIRE_REROLL_MAX
        t_req = time.time()
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
            )
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


def download_payload(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    """取 payload 归档：**分块 + 进度行 + 停滞即断**（2026-09-20 事故的修复面）。

    停滞判据 = 45s 无新字节（`BODY_IDLE_TIMEOUT_SEC`），总预算 300s。原来只有
    一个 `timeout=300` 的整读：隧道/代理中途停滞时，操作员看到的是**几分钟零输出**
    且日志里连一句「失败」都没有（socket 超时的裸异常没有正文）。
    """
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/payload",
        timeout=BODY_TOTAL_TIMEOUT_SEC,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=BODY_TOTAL_TIMEOUT_SEC,
        progress=_progress_logger(f"job {jid}: payload", log),
        wire_jid=jid,
        wire_seg="payload",
        reroll=True,
    )


def download_code(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    # code.zip 同样走隧道（1-3MB）：与 payload 同规的停滞判据与进度行。
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/code",
        timeout=120.0,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=120.0,
        progress=_progress_logger(f"job {jid}: code", log),
        wire_jid=jid,
        wire_seg="code",
        reroll=True,
    )


def download_ts_code(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    """M3：取 TS 运行时 zip（kind=iter 的节点要用 bun 跑 rollout）。

    代价只付一次：内容寻址缓存（`ts_code_cache/<sha>`）命中后同 sha 永不重下。
    """
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/ts_code",
        timeout=BODY_TOTAL_TIMEOUT_SEC,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=BODY_TOTAL_TIMEOUT_SEC,
        progress=_progress_logger(f"job {jid}: ts_code", log),
        wire_jid=jid,
        wire_seg="ts_code",
        reroll=True,
    )


def download_blob(
    base_url: str,
    token: str,
    jid: str,
    name: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    """M2 B3：取内容寻址 blob（raw opt/ref）。404 = 确定性缺失（响亮失败，非静默降级）。"""
    return _get_with_retry(
        base_url,
        token,
        f"/jobs/{jid}/blob?name={name}",
        timeout=BODY_TOTAL_TIMEOUT_SEC,
        attempts=attempts,
        log=log,
        idle_timeout=BODY_IDLE_TIMEOUT_SEC,
        total_timeout=BODY_TOTAL_TIMEOUT_SEC,
        progress=_progress_logger(f"job {jid}: blob {name}", log),
        wire_jid=jid,
        wire_seg=f"blob:{name}",
        reroll=True,
    )


def _cache_blob(blob_root: Path, sha: str, raw: bytes, log) -> None:
    """把 raw blob 写入 `blob_cache/<sha>`（原子改名；写失败只记日志）。"""
    if not sha or not raw:
        return
    try:
        blob_root.mkdir(parents=True, exist_ok=True)
        p = blob_root / sha
        if p.exists():
            return
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(p)
    except OSError as e:
        log(f"blob cache 写失败（{e}）——下一轮将重传该 blob")


def _resolve_blob(
    *,
    blob_root: Path,
    name: str,
    sha: str,
    inline_b64: str,
    jid: str,
    base_url: str,
    token: str,
    preloaded: dict | None,
    log,
) -> tuple[bytes, bool, str]:
    """解析一个 M2 blob → (raw, hit, src)；src ∈ cache|preloaded|download|inline|none。

    安全阀（plan §4.3）：`sha` 非空时**只认内容寻址路径** —— 缓存命中 / preloaded /
    HTTP 取，任一校验不过就响亮失败（RetryableError/ProtocolError）；**绝不**退回
    内联或 warm-start（那会静默把 D5 的 Adam 动量丢掉）。`sha` 为空 = 未开瘦身/
    旧 hub，走内联（内联也空则返回 none，由调用方决定策略）。
    """
    import hashlib as _hl

    if sha:
        cp = blob_root / sha
        if cp.exists():
            raw = cp.read_bytes()
            if _hl.sha256(raw).hexdigest() == sha:
                _wire_hit(jid, f"blob:{name}")
                return raw, True, "cache"
            log(f"blob {name}: cache 命中但 sha 不符（损坏）——重新取")
        pl = (preloaded or {}).get("blobs") or {}
        if name in pl:
            raw = pl[name]
            src = "preloaded"
            _wire_hit(jid, f"blob:{name}", "preloaded")
        else:
            raw = download_blob(base_url, token, jid, name, log=log)
            src = "download"
        if _hl.sha256(raw).hexdigest() != sha:
            # 传输损坏 = 瞬时（同 payload_sha256 口径）：重下可修复
            raise RetryableError(f"blob {name} sha 不匹配——传输损坏（重下可修复）")
        _cache_blob(blob_root, sha, raw, log)
        return raw, False, src
    if name == BLOB_OPT and inline_b64:
        return decode_opt_tar(inline_b64), True, "inline"
    if name == BLOB_REF and inline_b64:
        import base64 as _b64

        return _b64.b64decode(inline_b64.encode("ascii")), True, "inline"
    return b"", False, "none"


def post_result(
    base_url: str,
    token: str,
    jid: str,
    result: dict,
    lease_token: str = "",
    timeout: float = 120.0,
    attempts: int = 5,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> int:
    """POST 结果：瞬时失败（网络/5xx）指数退避重试（最贵产物不允许最后一米丢失）；
    4xx = 确定性拒绝立即抛 ProtocolError；409 = hub 已有同 job 结果（幂等，按成功）。"""
    last: str = ""
    # 方案B（2026-09-10）：v2 体 = gzip 裸二进制段（省掉 base64 的 33% 膨胀）。
    # 实测线上 1,150,292 -> 862,717 B（相对未压缩的 1,634,596 省 47.2%），上行 ~5.2 -> ~3.9 s。
    # JSON 体保留作**旧 hub 的退路**（见下方 4xx 分支）。
    req_body = pack_result_v2(result)
    ctype = WIRE_V2_CONTENT_TYPE
    req_body_json = json.dumps(result, ensure_ascii=False).encode("utf-8")
    t0 = time.time()
    for attempt in range(1, attempts + 1):
        t_a = time.time()  # 本次尝试的墙钟（传输账用；`t0` 含退避，不适合算速率）
        try:
            status, body = _request(
                base_url,
                token,
                f"/jobs/{jid}/result",
                timeout=timeout,
                data=req_body,
                method="POST",
                headers={
                    "Content-Type": ctype,
                    # H2：结果回传须携带领取时下发的 lease_token（hub 校验后收）
                    **({"X-Lease-Token": lease_token} if lease_token else {}),
                },
            )
        except Exception as e:
            status, body = None, repr(e).encode()
        if status in (200, 201):
            _wire_add(jid, "result", len(req_body), time.time() - t_a)
            log(
                f"result POST ok: {len(req_body)} bytes ({ctype.rsplit('/', 1)[-1]})"
                f" in {time.time() - t0:.1f}s (attempt {attempt})"
                + (
                    f"  [同内容 JSON 体为 {len(req_body_json)} bytes]"
                    if ctype != "application/json"
                    else ""
                )
            )
            return status
        if status == 409:
            # 竞速广播下这是**输家的正常结局**：同 job 已被别人先回传，本份结果丢弃。
            # 绝不重试、绝不当失败上报（否则一个赢家会让 N-1 个 worker 白报错）。
            _wire_add(jid, "result", len(req_body), time.time() - t_a)  # 字节确实出去了
            log("result POST 409（hub 已有同 job 结果：竞速输家/重复回传）——按成功丢弃")
            return 409
        if status is not None and 400 <= status < 500:
            if ctype == WIRE_V2_CONTENT_TYPE and attempt < attempts:
                # 旧 hub 进程（只认 application/json）会 4xx —— 退回 JSON 重发一次。
                # 绝不让最贵的产物因为一次协议不匹配丢在最后一米。
                log(f"result POST 被拒（HTTP {status}）——退回 JSON 体重试（旧 hub？）")
                req_body, ctype = req_body_json, "application/json"
                continue
            raise ProtocolError(
                f"result POST rejected: HTTP {status}: {body[:300].decode('utf-8', 'replace')}"
            )
        last = (
            f"HTTP {status}" if status is not None else repr(body.decode("utf-8", "replace")[:120])
        )
        if attempt < attempts:
            backoff = min(2**attempt, 16)
            log(f"result POST 瞬时失败({last}) — {backoff}s 后第 {attempt + 1}/{attempts} 次重试")
            time.sleep(backoff)
    raise RetryableError(f"result POST 重试 {attempts} 次仍失败: {last}")


def release_job(
    base_url: str,
    token: str,
    jid: str,
    lease_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> None:
    """瞬时失败后主动还租约（POST /jobs/{id}/release）：job 立即回池可重领，
    不再干等 30min 租约过期。尽力而为：release 本身不可达时由租约过期兜底。"""
    try:
        _request(
            base_url,
            token,
            f"/jobs/{jid}/release",
            timeout=15.0,
            method="POST",
            headers={**({} if not lease_token else {"X-Lease-Token": lease_token})},
        )
    except Exception:
        pass  # release 不可达：租约过期兜底（30min），与旧行为一致


def worker_tag() -> str:
    """本 worker 的可读身份（失败回报的 `worker` 字段）。

    多机共用一个 hub 时，「是哪台机器说 bun 缺失」是定位现场的**唯一**线索
    （全部节点共用同一份 token，日志里分不出来）。
    """
    try:
        import socket

        return f"{socket.gethostname()}:{os.getpid()}"
    except Exception:  # 主机名不可得（容器/受限沙箱）——pid 也够用
        return f"pid{os.getpid()}"


def _failure_detail(e: BaseException, limit: int = 4000) -> str:
    """异常现场（traceback 尾段）——回报给人看，不参与任何判定。

    截**尾**段而非头段：栈顶几帧是 transport 样板，真正的原因是最后一帧的
    `ProtocolError: bun 未安装 …`。
    """
    import traceback

    try:
        return traceback.format_exc()[-limit:]
    except Exception:  # 极端情况下 format_exc 本身不可用——退回落单行
        return f"{type(e).__name__}: {e}"[:limit]


def report_job_failure(
    base_url: str,
    token: str,
    jid: str,
    reason: str,
    *,
    kind: str = "",
    detail: str = "",
    lease_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bool:
    """回报**确定性**失败原因（`POST /jobs/{id}/fail`）。

    2026-09-17（plan/remote-wire-remediation §5.3 缺口）：worker_loop 的
    `except ProtocolError` 此前只写一行云机日志就 skip——**不回传、不还租约**，训练侧
    只能等 `wait_job` 25 分钟超时，看到的是一行「超时」而不是「bun 装不上」。
    回报后 hub 把它落成 job 的终局（fail.json），训练侧立即带原因收兵。

    尽力而为：回报不可达时返回 False，由超时兜底（与 release_job 同策略）。
    """
    if not base_url or not jid or not reason:
        return False
    body = json.dumps(
        {
            "reason": str(reason)[:2000],
            "kind": str(kind)[:200],
            "detail": str(detail)[:4000],
            "worker": worker_tag(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        status, resp = _request(
            base_url,
            token,
            f"/jobs/{jid}/fail",
            timeout=15.0,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"X-Lease-Token": lease_token} if lease_token else {}),
            },
        )
    except Exception as e:
        log(f"job {jid} 失败回报未送达（{type(e).__name__}）——训练侧将走超时兜底")
        return False
    if status == 200:
        log(f"job {jid} 失败原因已回报 hub: {str(reason)[:160]}")
        return True
    log(f"job {jid} 失败回报被拒：HTTP {status}: {resp[:200].decode('utf-8', 'replace')}")
    return False


def heartbeat(base_url: str, token: str, jid: str, lease_token: str = "") -> None:
    try:
        _request(
            base_url,
            token,
            f"/jobs/{jid}/heartbeat",
            timeout=15.0,
            method="POST",
            headers={**({} if not lease_token else {"X-Lease-Token": lease_token})},
        )
    except Exception:
        pass  # 心跳失败不致命：下一次轮询/心跳再续


def _wire_block(**over: object) -> dict:
    """M0 统一计量：worker 侧 `wire` 子字典（全 additive——旧 hub 的 validate_result
    不校验未知字段，旧读方忽略）。over 里 None 的键保留默认值（不把缺失写成 null）。"""
    w: dict = {
        "payload_bytes": 0,
        "payload_dl_sec": 0.0,
        "unpack_sec": 0.0,
        "opt_restore_sec": 0.0,
        "grad_sec": 0.0,
        "blob_hits": 0,
        "blob_miss_bytes": 0,
        "result_bytes": 0,
        # M3 kind=iter（其余 job 恒 0/False = 本轮没走这条线）；rollout_sec / bun_version
        # 不在这里给默认值——缺席就代表「本轮没有节点侧 rollout」，不能写成 0 冒充。
        "ts_code_bytes": 0,
        "ts_code_hit": False,
    }
    w.update({k: v for k, v in over.items() if v is not None})
    return w


def _persist_result(work_dir: Path, jid: str, result: dict) -> None:
    """结果落盘 _result.json：回传失败后重领同 job 时直接复用，不重算 PPO。"""
    rpath = work_dir / jid / "_result.json"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    rpath.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


#: worker 侧保留的 job 目录数（含在跑的本 job）。2026-09-11：c6b 单 job ≈280 MB
#: （600 shard × 439 KB 解包后 + payload 归档 + code.zip），20 轮 ≈5.6 GB。
#: ⚠ hub 侧 keep_iters=3 只轮转本地 it* 与 remote-jobs——**管不到**这里的
#:   /tmp/remote-worker（云）与 tmp/remote-worker-serve（本机 self 节点）。
#: 保留 2 个：上一个 job 的 _result.json 要留给"回传失败后重领同 job"的幂等路径。
JOB_DIR_KEEP = 2

#: 热替换退出码：worker 子进程代码变更时以该码退出，**监督器**（supervise_worker /
#: 新版 main()）收到后用同一套参数重新拉起子进程（fresh 进程 → sys.modules 必然为空
#: → 新代码生效）。不用 0（=正常完成）：处理失败/退出原因必须可区分。
HOT_RELOAD_EXIT = 86


def prune_job_dirs(work_dir: Path, keep: int = JOB_DIR_KEEP, log=lambda msg: None) -> int:
    """按 mtime 保留最近 `keep` 个 job 目录，其余删除。返回删除个数。

    只动 work_dir 下的 job 目录（按 jid 命名），**跳过 code_cache / blob_cache /
    ts_code_cache**（按 sha 内容寻址，命中即省一次下载/上传）。删除失败（占用/沙箱
    保护）跳过，不抛。

    ⚠ 新增内容寻址缓存目录时必须加进这份豁免名单（2026-09-17 M2 事故：`blob_cache`
    漏了名单 → 每轮被当旧 job 目录删掉 → 缓存永远未命中，而现象看起来是「协议没生效」）。
    """
    try:
        dirs = [
            d
            for d in work_dir.iterdir()
            if d.is_dir() and d.name not in ("code_cache", "blob_cache", "ts_code_cache")
        ]
    except OSError:
        return 0
    if len(dirs) <= keep:
        return 0
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    from platform_utils import rmtree_best_effort

    removed = 0
    for d in dirs[keep:]:
        try:
            # 计数必须挂在返回值上（与 rl/workdir_sweep 同策略）：沙箱删除保护拦截
            # 时 rmtree_best_effort 返回 False，无条件 +1 会把没删掉的也算进 n。
            if rmtree_best_effort(d, ignore_errors=True):
                removed += 1
        except BaseException as e:  # 含 SystemExit：沙箱删除守卫会打死调用线程
            log(f"prune: 跳过 {d.name}（{type(e).__name__}）")
    if removed:
        log(f"prune: 删除 {removed} 个旧 job 目录（保留最近 {keep} 个）")
    return removed


# ------------------------------------------------------------------ PPO 执行


def unpack_opt_tar(tar_bytes: bytes, dest: Path) -> None:
    """opt_init base64 tar → dest。兼容 3.10（无 filter 参数）。"""
    import io

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(dest)


def pack_opt_tar(src_dir: Path) -> bytes:
    """_ppo_save 目录 → tar bytes（回传用）。

    H5（review-hy）：**只打 model.pt + opt.pt，不打 state.json**——state.json 里的
    numpy RNG 状态从未被读取（worker 每次按 per-job 种子重播，D5 自洽），tar 里躺着
    死数据只会误导。Adam 动量（opt.pt）才是跨轮续跑真正需要的状态。
    """
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name in ("model.pt", "opt.pt"):
            p = src_dir / name
            if p.exists():
                tf.add(p, arcname=name)
    return buf.getvalue()


def _git_head(repo_root: Path = REPO_ROOT) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def _ensure_commit(target: str, repo_root: Path = REPO_ROOT, log=lambda msg: None) -> bool:
    """确保本地 HEAD 等于 target commit。不等则 git fetch + checkout 自动修复。

    返回 True（一致）或 False（重试 5 次后仍不一致）。
    """
    import subprocess as _sp

    for attempt in range(5):
        head = _git_head(repo_root)
        if head and head == target:
            return True
        log(
            f"commit mismatch: HEAD={head[:12] if head else '?'} "
            f"target={target[:12]} — fetching (attempt {attempt + 1}/5)"
        )
        try:
            _sp.run(
                ["git", "fetch", "origin"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            _sp.run(
                ["git", "checkout", target],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as e:
            log(f"git fetch/checkout failed: {e}")
    head = _git_head(repo_root)
    return head == target


# D14 比对规则的**唯一实现**住 `remote.protocol`（发布端 `hub_client.iter_shard_dirs`
# 打包时用同一条规则挑选 shard）——这里只做名字转发，保持既有 import/调用面不变。
d14_corpus_match = protocol_d14_corpus_match


def _bc_fetch_resume(
    base_url: str,
    token: str,
    jid: str,
    *,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> tuple[int, bytes] | None:
    """GET /jobs/{id}/resume → (epoch, weights_bytes) | None（404 = 全新训练）。

    网络错误/5xx → RetryableError（release 后重领再查——resume 未知就开训可能
    白扔已有进度，一致性优先）；4xx 其它 = 确定性拒收。"""
    last = ""
    for attempt in range(1, 4):
        try:
            status, body = _request(base_url, token, f"/jobs/{jid}/resume", timeout=30.0)
        except Exception as e:
            status, body = None, repr(e).encode()
        if status == 200:
            try:
                d = json.loads(body.decode("utf-8"))
                return int(d["epoch"]), decode_weights_json(str(d["weights"]))
            except (ValueError, KeyError, TypeError) as e:
                raise ProtocolError(f"bc resume 体非法: {e}") from e
        if status == 404:
            return None
        last = f"HTTP {status}" if status is not None else repr(body.decode("utf-8", "replace")[:120])
        if attempt < 3:
            log(f"bc resume 查询瞬时失败({last})——退避重试 {attempt}/3")
            time.sleep(min(2**attempt, 8))
    raise RetryableError(f"bc resume 查询 3 次仍失败: {last}")


def _bc_local_resume_dir(work_dir: Path) -> Path:
    """worker 本地 resume 存储（push 模式无 hub 时的持久层；pull 模式作缓存）。
    ⚠ 独立于 job_dir——job_dir 被重领清场，resume 必须在它外面才能活过重领。"""
    d = Path(work_dir) / "bc-resume"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bc_store_local_resume(work_dir: Path, jid: str, body: dict) -> None:
    """本地 resume 原子覆盖写 + 目录收敛（保留最近 2 个 job 的 resume）。"""
    import os as _os

    d = _bc_local_resume_dir(work_dir)
    tmp = d / f"{jid}.json.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    _os.replace(tmp, d / f"{jid}.json")
    files = sorted(
        (f for f in d.glob("*.json") if not f.name.endswith(".tmp")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for stale in files[2:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _bc_load_local_resume(work_dir: Path, jid: str) -> tuple[int, bytes] | None:
    p = _bc_local_resume_dir(work_dir) / f"{jid}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return int(d["epoch"]), decode_weights_json(str(d["weights"]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] bc local resume 损坏（忽略，全新训练）: {e}", flush=True)
        return None


def _bc_post_epoch(
    base_url: str,
    token: str,
    jid: str,
    body: dict,
    lease_token: str,
    *,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> None:
    """POST /jobs/{id}/epoch（每 epoch 权重+指标回传；租约校验在 hub 侧）。

    best-effort：2 次退避重试后仍失败只 WARN——resume 停在最后成功 epoch，
    下个 epoch 自带重试；绝不因回传失败打断训练。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = ""
    for attempt in range(1, 3):
        try:
            status, resp = _request(
                base_url,
                token,
                f"/jobs/{jid}/epoch",
                timeout=60.0,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    **({"X-Lease-Token": lease_token} if lease_token else {}),
                },
            )
        except Exception as e:
            status, resp = None, repr(e).encode()
        if status in (200, 201):
            return
        last = (
            f"HTTP {status}"
            if status is not None
            else repr(resp.decode("utf-8", "replace")[:120])
        )
        if attempt < 2:
            time.sleep(2)
    log(f"WARN: epoch {body.get('epoch')} 回传失败({last})——resume 停留在上一成功 epoch")


def _bc_device(dev_str: str) -> str:
    """BC 任务的设备透传（2026-09-13 多卡：train/bc.py 自带 cuda-dp 语义与
    单卡/无卡响亮退化——worker 不再代为砍成单卡）；tpu/xla 确定性拒绝
    （train/bc.py 无 xla 路径），绝不静默降级——设备语义错了的 benchmark
    数据比没有更糟。"""
    s = str(dev_str).lower()
    if s in ("tpu", "xla"):
        raise ProtocolError(f"bc 任务不支持设备 {dev_str!r}（v1 仅 cpu/cuda/cuda-dp）")
    return s or "cpu"


def normalize_ppo_device(device: object, *, cuda_available: bool | None = None) -> str:
    """把 PPO 的 ``--device`` 归一化成 torch 认得的字符串；``auto`` 在此兜底解析。

    为什么需要（2026-09-15 Colab push-first 事故）：push-first 引导在
    `notebook_runtime.resolve_device()` 解析设备**之前**就把 worker spawn 起来了
    （cell 与 `push_bootstrap.run_push_first` 都只做 `device_resolved or device` 兜底），
    于是字面量 ``"auto"`` 被一路送到 `torch.device("auto")`，整轮 job 报
    ``Expected one of cpu, cuda, ... device type at start of device string: auto``。

    兜底规则：``auto`` → 有 CUDA 用 ``cuda``（**单卡**），否则 ``cpu``。
    **刻意不自动升 ``cuda-dp``**：DataParallel 会改变梯度归约顺序，是"新开一条实验臂"
    的开关而非透明加速（见 `run_job` 多卡段注释，以及 `notebook_runtime.resolve_device`
    的「显式 cuda 绝不悄悄升级」纪律）。要多卡必须显式 ``--device cuda-dp``。
    """
    s = str(device or "cpu").strip().lower()
    if s != "auto":
        return s
    if cuda_available is None:
        try:
            import torch

            cuda_available = bool(torch.cuda.is_available())
        except Exception:  # torch 缺失 / 驱动异常 → 按无 CUDA 处理
            cuda_available = False
    return "cuda" if cuda_available else "cpu"


def resolve_bc_seed(manifest: dict) -> tuple[int, str]:
    """BC job 训练种子 → (seed, 来源标签)。

    2026-09-14：`manifest["train_seed"]`（来自课程 `train.seed`，run_bc 经 extra 注入）
    **优先**——R1（v2 vs v3 obs 对照）必须两臂同 seed 才能得到同一 val 划分
    （`train/bc.py` 用 seed 做 `make_loaders` 的切分），否则 best_val_loss 不可比；
    同时让同课程云端重跑可复现。
    缺省（旧 job 无该键）回退 per-job 确定性种子（D5：同一 job 重发 chunk 逐字节一致
    —— 但它含 runId，而 runId 每轮启动都变 ⇒ 复现性只限同一 job 的重发）。

    键名刻意不用 `seed`：manifest 里 `seed` 已有历史口径（hex per-job 种子占位，
    见 tests/test_bc_epoch_e2e.py 的 fixture，以及 dist_common 的 stage/seed 语义）。
    """
    explicit = manifest.get("train_seed")
    if explicit is None:
        seed_hex = job_seed(manifest["runId"], int(manifest["it"]), manifest["init_weights_fp"])
        return int(seed_hex[:8], 16), "per-job"
    return int(explicit), "course"


def _run_bc_job(
    *,
    jid: str,
    manifest: dict,
    job_dir: Path,
    shard_dirs: list[str],
    device: str,
    torch_threads: int,
    echo: bool,
    base_url: str = "",
    token: str = "",
    lease_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> dict:
    """云端 BC job（plan/bc-cloud-integration.plan.md §3；每 epoch 回传 2026-09-13）：
    D14 语料血缘校验 →（--echo 冒烟回显）→ **resume 接续**（hub bc-resume 优先，
    本地 bc-resume/ 兜底——中断重领同 job 从最后完成的 epoch 接着训，绝不从头重训）
    → 复用 `train/bc.py::train` 训练（on_epoch 回调每 epoch 回传权重+指标：hub
    POST /jobs/{id}/epoch 租约校验防旧 worker 覆盖新 resume + 本地原子覆盖）→
    BC 权重 + metrics 回传。

    语料 = payload 解包出的 npy shard 目录（obs/scalars/actions/masks/conditions/
    returns + manifest），bc.py 的 scan_shards 对 job_dir 递归扫描即全部装载。
    """
    # ---- D14 语料血缘：与 PPO 同规（BC shard manifest 同样携带 course_fp/corpus_fp）----
    _cfp = str(manifest["course_fp"])
    _corpus = str(manifest.get("corpus_fp", "") or "")
    for _sd in shard_dirs:
        try:
            with open(os.path.join(_sd, "manifest.json"), encoding="utf-8") as _f:
                _sm = json.load(_f)
        except (OSError, ValueError) as _e:
            raise ProtocolError(f"D14 course_fp: 读 shard manifest 失败 {_sd}: {_e}") from _e
        if not d14_corpus_match(_cfp, _corpus, _sm):
            raise ProtocolError(
                f"D14 corpus_fp 不匹配：job={_corpus[:12] or _cfp[:12]}… "
                f"shard={str(_sm.get('corpus_fp') or _sm.get('course_fp'))[:12]}… "
                f"（{_sd}）——跨课程语料混入，拒收"
            )

    t0 = time.time()

    # ---- 冒烟回显（--echo，2026-09-05 同语义）：不拉 torch 不训练——占位 weights +
    # smoke 标记（消费方 run_bc 作废轮，不落盘不归档）。三重校验按构造必过。
    if echo:
        result = {
            "job_id": jid,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(b'{"smoke-placeholder":true}'),
            "opt_tar_b64": "",
            "metrics": {
                "epochs": 0,
                "train_samples": 0,
                "val_samples": 0,
                "best_val_loss": 0.0,
            },
            "commit_echo": manifest["commit"],
            "bc_sec": 0.0,
            "smoke": True,
        }
        validate_result(result, manifest, commit_echo_must_match=False)
        _persist_result(job_dir.parent, jid, result)
        log(f"job {jid}: ECHO (bc smoke) — 占位权重回传（未跑 BC）")
        return result

    # ---- 延迟 import torch + BC 训练器（B7 同款；本模块顶层零 torch）----
    import torch

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)
    from types import SimpleNamespace

    from data.weights_io import save_weights_json
    from train.bc import train as bc_train

    # 训练种子（2026-09-14 修正）：课程/调用方指定优先 —— 见 resolve_bc_seed。
    seed_int, seed_src = resolve_bc_seed(manifest)
    dev = _bc_device(device)
    out_path = job_dir / "bc-weights.json"
    total_epochs = int(manifest["epochs"])

    # ---- resume 接续：hub 优先（跨会话/跨 worker 持久），本地 bc-resume/ 兜底 ----
    resume: tuple[int, bytes] | None = None
    if base_url:
        resume = _bc_fetch_resume(base_url, token, jid, log=log)
    if resume is None:
        resume = _bc_load_local_resume(job_dir.parent, jid)
    resume_epoch, resume_weights = resume if resume else (0, b"")
    if resume_epoch:
        log(f"job {jid}: resume 接续——从 epoch {resume_epoch} 继续（共 {total_epochs}）")

    # 每 epoch 回传回调：本地原子覆盖 + hub POST（均 best-effort——失败只 WARN，
    # resume 停在最后一次成功回传的 epoch，下个 epoch 重试）。
    def _on_epoch(gepoch: int, raw_model: Any, m: dict) -> None:
        tmp = job_dir / "bc-epoch-tmp.json"
        save_weights_json(raw_model, str(tmp), extra_meta={"epoch": gepoch, "ckpt": True})
        body = {
            "epoch": int(gepoch),
            "weights": encode_weights_json(tmp.read_bytes()),
            "metrics": dict(m),
        }
        try:
            tmp.unlink()
        except OSError:
            pass
        _bc_store_local_resume(job_dir.parent, jid, body)
        if base_url:
            _bc_post_epoch(base_url, token, jid, body, lease_token, log=log)

    if resume_epoch >= total_epochs > 0:
        # 上轮已完成全部 epoch、只差最终回传（epoch POST 成功但 result 丢失）：
        # resume 权重即终态——零重训直接产出。
        log(f"job {jid}: resume 已达 {resume_epoch}/{total_epochs} —— 零重训直接回传终态")
        wj_bytes = resume_weights
        result_metrics: dict[str, Any] = {
            "epochs": total_epochs,
            "train_samples": 0,
            "val_samples": 0,
            "best_val_loss": 0.0,
            "resumed_complete": True,
        }
    else:
        ns = SimpleNamespace(
            data_dir=str(job_dir),
            arch=str(manifest["arch"]),
            out=str(out_path),
            notes=f"bc-job {jid} course={manifest.get('course_name', '')}",
            resume=None,
            epoch_offset=0,
            ckpt_every=0,  # 云端持久化走 on_epoch 回传（job_dir 重领即清场，盘上 ckpt 无意义）
            checkpoint=None,
            epochs=total_epochs - resume_epoch,
            batch=int(manifest["mb"]),
            lr=float(manifest["lr"]),
            val_split=float(manifest.get("val_split", 0.1)),
            mirror_p=float(manifest.get("mirror_p", 0.5)),
            seed=seed_int,
            num_workers=0,
            device=dev,
            value_coef=float(manifest.get("value_coef", 0.0) or 0.0),
            # fire 头正例权重（2026-09-14）：'auto' 或数字；旧 job 无此键 → 0.0
            # （关闭 = 历史语义，重放旧 job 行为不变）。
            fire_pos_weight=manifest.get("fire_pos_weight", 0.0),
            on_epoch=_on_epoch,
        )
        if resume_epoch:
            resume_in = job_dir / "bc-resume-in.json"
            resume_in.write_bytes(resume_weights)
            ns.resume = str(resume_in)
            ns.epoch_offset = resume_epoch
        log(
            f"job {jid}: BC start arch={ns.arch} epochs={ns.epochs} batch={ns.batch} "
            f"lr={ns.lr} device={dev} seed={seed_int}({seed_src}) shards={len(shard_dirs)}"
            + (f" resume@{resume_epoch}" if resume_epoch else "")
        )
        metrics = bc_train(ns)
        hist = metrics.get("history", {})
        sizes = metrics.get("sizes", {})
        result_metrics = {
            "epochs": total_epochs,
            "train_samples": int(sizes.get("train", 0) or 0),
            "val_samples": int(sizes.get("val", 0) or 0),
            "best_val_loss": float(metrics.get("best_val_loss", 0.0) or 0.0),
            "move_acc": float(hist.get("move_acc", [0.0])[-1]) if hist.get("move_acc") else 0.0,
            "fire_acc": float(hist.get("fire_acc", [0.0])[-1]) if hist.get("fire_acc") else 0.0,
            "params": int(metrics.get("params", 0) or 0),
        }
        if resume_epoch:
            result_metrics["resumed_from"] = resume_epoch
        wj_bytes = Path(str(metrics["out"])).read_bytes()

    bc_sec = round(time.time() - t0, 1)
    if "metrics" in dir():
        pass
    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(wj_bytes),
        "opt_tar_b64": "",
        "metrics": result_metrics,
        "commit_echo": manifest["commit"],
        "bc_sec": bc_sec,
    }
    validate_result(result, manifest, commit_echo_must_match=False)  # 自查
    # _persist_result 落 work_dir/<jid>/_result.json —— job_dir.parent 即 run_job 语义
    # 里的 work_dir（pull 与 push 两条路径同构），重领同 job 的缓存复用才能读到。
    _persist_result(job_dir.parent, jid, result)
    return result


def _ensure_ts_code(
    base_url: str,
    token: str,
    jid: str,
    manifest: dict,
    *,
    ts_root: Path,
    preloaded: dict | None,
    log=lambda msg: None,
) -> tuple[Path, int, bool]:
    """M3 kind=iter：把 TS 运行时 zip 解包到内容寻址目录，返回 `(目录, 字节数, 缓存命中)`。

    与 code.zip 同口径（按 sha 隔离 + tmp 原子改名），但**另一棵缓存树**：Python 代码走
    `sys.path`，TS 代码走 bun 的 cwd —— 两者生命周期/内容无关，混在一起只会让删除豁免
    名单变难维护。sha 不匹配 = 传输损坏（瞬时）⇒ RetryableError，与 payload/code 同规。

    返回的字节数用于 M0 计量（`wire.ts_code_bytes`；缓存命中时为 0 = 本轮没走这条线）。
    """
    import zipfile

    sha = str(manifest.get("ts_code_sha256", "") or "")
    if not sha:
        raise ProtocolError("kind=iter 的 manifest 缺 ts_code_sha256——无法定位 TS 运行时")
    cache = ts_root / sha
    if cache.exists():
        log(f"job {jid}: ts_code cache 命中（{sha[:12]}…）——跳过下载解压")
        return cache, 0, True
    raw = (preloaded or {}).get("ts_code_zip") or download_ts_code(base_url, token, jid, log=log)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise RetryableError("ts_code_sha256 不匹配——传输损坏（重下可修复）")
    tmp = ts_root / (sha + ".tmp")
    if tmp.exists():
        from platform_utils import rmtree_best_effort

        rmtree_best_effort(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    zip_path = tmp.parent / (sha + ".zip")
    zip_path.write_bytes(raw)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    ts_root.mkdir(parents=True, exist_ok=True)
    if cache.exists():  # 并发窗口：别人已解好 → 用别人的
        from platform_utils import rmtree_best_effort as _rm

        _rm(tmp, ignore_errors=True)
        _rm(zip_path, ignore_errors=True)
        return cache, 0, True
    tmp.rename(cache)
    try:
        zip_path.unlink()
    except OSError:
        pass
    n_ts = len(list(cache.rglob("*.ts")))
    log(
        f"job {jid}: ts_code.zip unpacked ({len(raw)} bytes, {n_ts} .ts files) -> {cache.name}"
    )
    return cache, len(raw), False


def run_job(
    base_url: str,
    token: str,
    job: dict,
    *,
    work_dir: Path,
    device: str = "cpu",
    torch_threads: int = 0,
    echo: bool = False,
    preloaded: dict | None = None,
    code_cache_dir: Path | None = None,
    ts_code_cache_dir: Path | None = None,
    lease_token: str = "",
    # ---- 半离线（kind=run；2026-09-17）产物目录与本次预算 ----
    # artifacts_dir：产物根（缺省按 Kaggle /kaggle/working / Colab Drive / 工作目录自动解析）。
    # run_max_iters / run_budget_sec：本次自主段的额外上限（0 = 只认计划）——后者是
    # Kaggle 会话到期前「干净停机」的把手。
    artifacts_dir: str | Path | None = None,
    run_max_iters: int = 0,
    run_budget_sec: float = 0.0,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> dict:
    """执行单个 job：下载 → 校验 → PPO → 产出 weights_json + opt tar → POST。

    preloaded（push 模式，2026-09-05）：{"payload_zip": bytes, "code_zip"?: bytes,
    "ts_code_zip"?: bytes}——HUB 把 payload/code 随 POST /job 直接上传（worker_server），
    跳过下载步骤；code 仍走 sha 缓存目录（code_zip 缺省时要求本地缓存已命中或
    服务器自行处理）。ts_code_zip 同规（M3 kind=iter 专用，其余 job 恒缺席）。

    echo=True（冒烟）：下载/校验全走，但不拉 torch 不跑 PPO——init 权重原样回传
    并带 smoke 标记（消费方作废本轮）；hub-start --smoke-only 的 Kaggle 交互预演。

    返回 hub 侧需要的 result dict（未 POST——调用方决定上传时机；本函数
    负责完成 PPO 与产物）。幂等：已完成（result 已落盘）的 job 由 hub 返回
    404/409，调用方跳过。
    """
    jid = job["job_id"]
    manifest = normalize_manifest(job["manifest"])

    # ---- 结果复用（2026-09-05）：上次已算完但回传失败 → 本地缓存直接重传，不重算 PPO ----
    cached_path = work_dir / jid / "_result.json"
    if cached_path.exists():
        try:
            cached: dict = json.loads(cached_path.read_text(encoding="utf-8"))
            validate_result(cached, manifest, commit_echo_must_match=False)
            log(f"job {jid}: 复用上次算完的结果（上次回传失败）——直接重传")
            return cached
        except Exception:
            pass  # 缓存缺失/跨 manifest/损坏 → 清场走全流程

    # ---- payload 来源（D1：sha256 校验防截断/损坏，两条路径同规） ----
    # M0 统一计量：payload 大小与拿到它的墙钟（push = 随 POST body 抵达，下载耗时归 hub；
    # pull = 真下载时间）。其余拆分（unpack/opt_restore/grad）各自包在下面。
    t_dl = time.time()
    if preloaded is not None and "payload_zip" in preloaded:
        raw = preloaded["payload_zip"]
        log(f"job {jid}: payload from push ({len(raw)} bytes)")
        payload_dl_sec = 0.0
    else:
        raw = download_payload(base_url, token, jid)
        payload_dl_sec = round(time.time() - t_dl, 3)
        log(f"job {jid}: payload downloaded ({len(raw)} bytes in {payload_dl_sec:.1f}s)")
    if hashlib.sha256(raw).hexdigest() != manifest["payload_sha256"]:
        # 传输损坏属瞬时故障：重下即可修复（RetryableError → 释放租约立即重领重下）
        raise RetryableError("payload_sha256 不匹配——传输损坏（重下可修复）")

    job_dir = work_dir / jid
    if job_dir.exists():
        from platform_utils import rmtree_best_effort

        rmtree_best_effort(job_dir)
    job_dir.mkdir(parents=True)
    # 磁盘：本 job 之后最多留 JOB_DIR_KEEP 个目录（放开头 = 失败轮也照样清理）
    prune_job_dirs(work_dir, JOB_DIR_KEEP, log=log)
    zip_path = job_dir / PAYLOAD_NAME
    zip_path.write_bytes(raw)
    # zip 内 manifest 是占位副本，解包仅取 shard 目录；权威校验全走 job 记录 manifest。
    # init_weights.json / opt_init.tar.b64 与 shard 目录同落 job_dir 根（解包天然如此）。
    t_unpack = time.time()
    _unused_manifest, shard_dirs = unpack_payload(zip_path, job_dir)
    unpack_sec = round(time.time() - t_unpack, 3)

    # ---- commit 校验：下载 code.zip 解压到 sys.path（替代 git 同步，D6） ----
    # 云端 worker 不再依赖 git checkout，而是使用 hub 启动时打包的代码快照。
    # ---- code.zip 内容寻址缓存（2026-09-05）：同 sha 只下载/解压一次 ----
    # code 在多次迭代间通常不变（sha 只由源码内容决定，pack 侧时间戳已固定化），
    # 缓存命中即省一次隧道下载 + 解压。缓存目录按 sha 隔离，tmp 原子改名防半截。
    # 多课程共享 worker（P3b C3）：code_cache 留共享根（按 sha 内容寻址，跨课复用），
    # 只有 job 目录按源分区——调用方经 code_cache_dir 传入共享根。
    code_root = code_cache_dir if code_cache_dir is not None else work_dir / "code_cache"
    #: M2 B3 blob 缓存根（跨课共享，同 code_cache 约定；键 = raw sha256）。
    blob_root = code_root.parent / "blob_cache"
    code_cache_dir = code_root / manifest["code_sha256"]
    if code_cache_dir.exists():
        sys.path.insert(0, str(code_cache_dir))
        _wire_hit(jid, "code")  # 零字节命中也要进本 job 的传输账（否则摘要读数失真）
        log(f"job {jid}: code cache 命中（{manifest['code_sha256'][:12]}…）——跳过下载解压")
    else:
        if preloaded is not None and "code_zip" in preloaded:
            code_raw = preloaded["code_zip"]
        else:
            code_raw = download_code(base_url, token, jid)
        if hashlib.sha256(code_raw).hexdigest() != manifest["code_sha256"]:
            # per-job 快照 sha 与 manifest 对账：不匹配 = 传输损坏（瞬时，重下可修复）
            raise RetryableError("code_sha256 不匹配——传输损坏（重下可修复）")
        import zipfile

        code_extract_tmp = code_root / (manifest["code_sha256"] + ".tmp")
        code_extract_tmp.mkdir(parents=True, exist_ok=True)
        code_zip_path = job_dir / "code.zip"
        code_zip_path.write_bytes(code_raw)
        with zipfile.ZipFile(code_zip_path) as zf:
            zf.extractall(code_extract_tmp)
        code_cache_dir.parent.mkdir(parents=True, exist_ok=True)
        code_extract_tmp.rename(code_cache_dir)
        sys.path.insert(0, str(code_cache_dir))
        log(
            f"job {jid}: code.zip unpacked ({len(code_raw)} bytes, "
            f"{len(list(code_cache_dir.rglob('*.py')))} .py files) -> sys.path[0]"
        )

    # ---- 热替换护栏（2026-09-11）：本进程已 import 的代码版本必须 == 本 job 要求 ----
    # sys.path.insert 只影响**尚未导入**的模块；已进 sys.modules 的 ppo/rl 不会重读。
    # 不查就会用旧代码跑出新结果：零报错、commit_echo 也照样回显，只有 sha 能揭穿。
    global _ACTIVE_CODE_SHA
    _job_sha = str(manifest["code_sha256"])
    if _ACTIVE_CODE_SHA is None:
        _ACTIVE_CODE_SHA = _job_sha
        log(f"job {jid}: 本进程加载代码 sha={_job_sha[:12]}…（后续 job 若变化将自重启）")
    elif _job_sha != _ACTIVE_CODE_SHA:
        raise CodeChangedError(_ACTIVE_CODE_SHA, _job_sha)

    # ---- 设备归一化（**必须在 kind 分叉之前**）----
    # BC 与 PPO 两条链都要用 device，且都要能接住未经解析的 "auto"：push-first 引导在
    # `notebook_runtime.resolve_device()` 之前就把 worker spawn 了（只做
    # `device_resolved or device` 兜底），字面量 "auto" 会一路传进来。
    # 只归一化一次、放在分叉前，两条链共用 —— 2026-09-15 两次事故的教训：
    # 第一次只改了 PPO 的分派条件（else 仍 `torch.device(device)`），第二次才发现
    # BC 分叉也是直接透传原始 device。
    _raw_dev = str(device or "cpu").strip().lower()
    device = normalize_ppo_device(device)
    if device != _raw_dev:
        log(
            f"job {jid}: --device {_raw_dev!r} 未经解析，worker 兜底为 {device}"
            "（调用方应先 resolve_device；要多卡须显式 --device cuda-dp）"
        )

    # ---- kind 分叉（BC 整合，plan/bc-cloud-integration.plan.md §3）：bc 任务走独立
    # 执行链（语料 npy shard 训练，无 reward/γ/λ/init-weights 语义）；ppo 任务继续
    # 走下方 per-tick 红线 + PPO 链路（默认行为零变化）。
    if str(manifest["kind"]) == "bc":
        return _run_bc_job(
            jid=jid,
            manifest=manifest,
            job_dir=job_dir,
            shard_dirs=shard_dirs,
            device=device,
            torch_threads=torch_threads,
            echo=echo,
            base_url=base_url,
            token=token,
            lease_token=lease_token,
            log=log,
        )

    # ---- M3 kind 分叉：iter = 一整轮上云（节点自己跑 rollout 产 shard）----
    #
    # 位置很关键：必须在 mode 红线 / D14 / echo **之前**——因为本轮真正要校验的 shard
    # 是刚跑出来的（payload 里没有 shard），D14 必须看到它们；echo 轮则整个跳过 rollout
    # （回显冒烟不跑任何重计算，只验 payload/code/ts_code 三样传输）。
    iter_info: dict | None = None
    ts_code_bytes = 0
    ts_code_hit = False
    # ---- 半离线（kind=run）：先把计划接过来校验（**跑第一局之前**）----
    # 三道门（sha / 形状 / 全段对集指纹）都在 run_loop.verify_plan_file 里；失败 = 计划
    # 与 hub 侧不一致，此时不跑任何一局，也不写任何产物。
    run_plan: dict | None = None
    run_plan_sha = ""
    if str(manifest["kind"]) == "run":
        from remote.run_loop import verify_plan_file

        run_plan, run_plan_sha = verify_plan_file(job_dir, manifest, log=log)
    if str(manifest["kind"]) in ("iter", "run"):
        ts_root = (
            ts_code_cache_dir
            if ts_code_cache_dir is not None
            else code_root.parent / "ts_code_cache"
        )
        _ts_dir, ts_code_bytes, ts_code_hit = _ensure_ts_code(
            base_url, token, jid, manifest, ts_root=ts_root, preloaded=preloaded, log=log
        )
        if echo:
            log(f"job {jid}: kind=iter + echo——跳过 rollout（只验传输链）")
        else:
            iter_info = run_iter_rollout(
                job_dir, manifest["rollout"], ts_dir=_ts_dir, log=log
            )
            shard_dirs = list(iter_info["shard_dirs"])

    # ---- mode 红线（v1：仅 per-tick） ----
    if manifest["mode"] != "per-tick":
        raise ProtocolError(f"mode={manifest['mode']!r} != per-tick——远程 v1 红线，拒收")

    # ---- D14 语料血缘：job.course_fp == 每个 shard 的 manifest.course_fp ----
    # （跨课程语料绝不混训——发布端已保证 shard 集按 course_fp 过滤，这里再校验一次）
    _cfp = str(manifest["course_fp"])
    _corpus = str(manifest.get("corpus_fp", "") or "")
    for _sd in shard_dirs:
        try:
            with open(os.path.join(_sd, "manifest.json"), encoding="utf-8") as _f:
                _sm = json.load(_f)
        except (OSError, ValueError) as _e:
            raise ProtocolError(f"D14 course_fp: 读 shard manifest 失败 {_sd}: {_e}") from _e
        if not d14_corpus_match(_cfp, _corpus, _sm):
            raise ProtocolError(
                f"D14 course_fp 不匹配：job={_cfp[:12]}… shard={str(_sm.get('course_fp'))[:12]}… "
                f"（{_sd}）——跨课程语料混入，拒收"
            )

    # ---- 冒烟回显（--echo，2026-09-05）：不拉 torch、不跑 PPO——把 payload 携带的
    # init 权重与指纹原样回传为 job 结果（hub-start --smoke-only 的 Kaggle 交互预演，
    # 用户拍板：真课程 + 作废轮，不建虚拟课程）。三重校验按构造必过
    # （init_weights_fp/data_fp/commit_echo 均为 manifest 回显）；消费方
    # （rl/loop_steps._remote_ppo）见 result["smoke"] 作废本轮（it 不前进）。
    if echo:
        init_w = job_dir / "init_weights.json"
        if not init_w.exists():
            raise ProtocolError("payload 缺 init_weights.json——echo 冒烟无法回显")
        # opt tar：manifest 携带则原样回显（Adam 动量 = 发布时状态）；首轮无 tar 时
        # 回空 tar（解包为空目录——作废轮的落位产物会被 _prepare_iter_dir 清场，
        # 真 PPO 轮会重新落位覆盖，永不消费空 tar）
        opt_b64 = str(manifest.get("opt_init") or "")
        if not opt_b64:
            import io as _io

            _buf = _io.BytesIO()
            with tarfile.open(fileobj=_buf, mode="w:"):
                pass
            opt_b64 = encode_opt_tar(_buf.getvalue())
        result = {
            "job_id": jid,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(init_w.read_bytes()),
            "opt_tar_b64": opt_b64,
            "agg": {
                "policy": 0.0,
                "value": 0.0,
                "entropy": 0.0,
                "kl": 0.0,
                "kickstart": 0.0,
                "mean_ret": 0.0,
                "steps": 0,
                "chunks": 0,
            },
            "commit_echo": manifest["commit"],
            "ppo_sec": 0.0,
            "smoke": True,
        }
        result["wire"] = _wire_block(
            payload_bytes=len(raw),
            payload_dl_sec=payload_dl_sec,
            unpack_sec=unpack_sec,
        )
        # 两遍收敛：result_bytes 自己的十进制位数要算进体长（第一遍以占位 0 计）。
        result["wire"]["result_bytes"] = len(pack_result_v2(result))
        result["wire"]["result_bytes"] = len(pack_result_v2(result))
        validate_result(result, manifest, commit_echo_must_match=False)
        _persist_result(work_dir, jid, result)
        log(f"job {jid}: ECHO (smoke) — init 权重原样回传（未跑 PPO）")
        return result

    # ---- 课程上下文：快照全文 → CourseConfig → reward_fn（D1/D6/D13） ----
    import numpy as np

    course_text = manifest["course"]
    course_path = job_dir / "course.jsonc"
    course_path.write_text(course_text, encoding="utf-8")
    from rl.config import load_course

    course = load_course(str(course_path))
    if course.reward_spec().identity() != manifest["formula_hash"]:
        raise ProtocolError(
            f"course formula_hash 与快照不符：manifest={manifest['formula_hash']} "
            f"本地算={course.reward_spec().identity()}"
        )
    from rl.reward_context import update as ctx_update
    from rl.reward_library import build_reward_fn

    reward_fn = build_reward_fn(course.reward_spec())
    ctx_update(
        reward_fn=reward_fn,
        gamma=float(manifest["gamma"]),
        lam=float(manifest["lam"]),
        it=int(manifest["it"]),
        metrics_version=int(manifest["metrics_version"]),
        identity={"course": course.name, "formula_hash": manifest["formula_hash"]},
    )

    # ---- 延迟导入 torch / ppo 后端（B7 同款；本模块顶层零 torch） ----
    import torch

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)
    import ppo.engine as ppo_engine
    from data.weights_io import load_state_into, save_weights_json

    # ---- per-job 确定性种子（D5）：load/chunk/update 前重新播种 ----
    # numpy RandomState 种子必须 < 2^32：sha256 前 8 个 hex 字符（32 bit）
    seed_hex = job_seed(manifest["runId"], int(manifest["it"]), manifest["init_weights_fp"])
    np.random.seed(int(seed_hex[:8], 16))

    # ---- 模型构建：opt_init（_ppo_save tar）优先，否则 init 权重 + 新 opt ----
    # hub 侧免 torch（D2）：模型权重/opt 由 tar 或 weights_json 提供，worker 负责
    # 重建——tar 内 model.pt = 上一轮 PPO 终态（含 Adam 动量，D5）。
    init_w = job_dir / "init_weights.json"
    # 标注为 torch.nn.Module（而非推断出的 PPOStudent）：多卡分支要把 model 换成
    # DataParallel，且下游 save_weights_json / load_state_into 收的就是 Module。
    # M2（B4）：有 opt blob 时 payload 不再带 init_weights.json（model+Adam 都在 opt
    # tar 里）——build_ppo 只借它读 arch，缺文件走默认（per-tick 固定 64/8/128）。
    model: torch.nn.Module = ppo_engine.build_ppo(str(init_w) if init_w.exists() else None)
    # 设备解析（2026-09-10 TPU 接力）：--device tpu/xla 走 torch_xla 的 xla_device()，
    # 而不是 torch.device("xla")——后者在部分 torch_xla 版本上拿不到带序号的设备句柄。
    # torch_xla 只在真的选了 TPU 时才 import（未装 torch_xla 的机器行为不变）。
    # `device` 已在 run_job 入口（kind 分叉之前）过 normalize_ppo_device，"auto" 不再可能到达这里。
    dev_str = str(device)
    use_dp = False
    if dev_str in ("tpu", "xla"):
        # 统一走 ppo.common.xla_device()（torch_xla.device() 优先，旧版回退 xm.xla_device()）
        from ppo.common import xla_device, xla_world_size

        device_t = xla_device()
        log(f"job {jid}: TPU/XLA 设备 {device_t}，world_size={xla_world_size()}"
            "（8=8 核；None=读不到，诊断用）")
    elif dev_str in ("cuda-dp", "dp"):
        # 多卡（2026-09-10 实测 1.92×）：torch.device("cuda-dp") 不是合法设备，
        # 必须显式落到 cuda；真正的包装在 state_dict 装载之后（见下方 use_dp 段）。
        device_t = torch.device("cuda")
        use_dp = torch.cuda.is_available() and torch.cuda.device_count() > 1
    else:
        # 必须用**归一化后**的 dev_str，不是原始 device —— 2026-09-15 二次事故：
        # 上一版只把分派条件换成 dev_str，这里仍写 `torch.device(device)`，
        # 于是 auto 照样被喂进 torch.device（日志上「兜底为 cuda」打了、job 仍炸 auto）。
        device_t = torch.device(dev_str)
    # ---- M2 B3：opt 内容寻址解析（cache / preloaded / download / inline）----
    # 安全阀（plan §4.3）：opt_sha 存在而 blob 不可得 → 响亮失败，绝不静默 warm-start
    # （那会把 D5 的 Adam 动量悄悄归零，日志上却一切正常）。
    opt_sha = str(manifest.get("opt_sha", "") or "")
    blob_hits = 0
    blob_miss_bytes = 0
    opt_raw, opt_hit, opt_src = _resolve_blob(
        blob_root=blob_root,
        name=BLOB_OPT,
        sha=opt_sha,
        inline_b64=str(manifest.get("opt_init", "") or ""),
        jid=jid,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
    )
    if opt_hit and opt_src == "cache":
        blob_hits += 1
    if opt_src == "download":
        blob_miss_bytes += len(opt_raw)
    if opt_sha and not opt_raw:
        raise ProtocolError(
            f"job {jid}: opt_sha={opt_sha[:12]}… 存在但 blob 不可得——拒收"
            "（不许静默退回 warm-start，D5）"
        )
    opt = None
    opt_restore_sec = 0.0
    if opt_raw:
        t_opt = time.time()
        opt_dir = job_dir / "opt_init"
        unpack_opt_tar(opt_raw, opt_dir)
        # 必须在 model.to(device_t) 之前加载 state_dict，然后统一移到目标设备
        model.load_state_dict(torch.load(opt_dir / "model.pt", map_location="cpu"))
        model.to(device_t)
        opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
        # 统一 map_location="cpu"：Optimizer.load_state_dict 会把载入张量 cast 到
        # param 所在设备，所以 XLA/CPU/CUDA 三条路都靠这一句完成搬迁（原先写死
        # device_t 在 XLA 上会走 torch.load 的设备 hook，跨 runtime 不稳）。
        opt.load_state_dict(torch.load(opt_dir / "opt.pt", map_location="cpu"))
        opt_restore_sec = round(time.time() - t_opt, 3)
        log(f"job {jid}: model/opt 从 opt_init（{opt_src}）恢复（Adam 动量延续，D5）")
    else:
        # 首轮/无 tar：从 init 权重 warm-start（hub 打包时写入 payload 的 weights_json）
        if not init_w.exists():
            raise ProtocolError("payload 缺 init_weights.json 且无 opt_init/blob——无法构建模型")
        load_state_into(model, str(init_w))
        model.to(device_t)
        opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
        log(f"job {jid}: 无 opt_init，从 init_weights warm-start + 新 Adam")

    # ---- 多卡（--device cuda-dp）----
    # 位置很重要：**必须在 load_state_dict / load_state_into + .to(device_t) 之后**再包，
    # 否则 ckpt 的键会长出 "module." 前缀。raw_model 始终指向未包装模块，产物落盘用它。
    # ⚠ DP 会改变梯度归约顺序 ⇒ 末位 ulp 变化，与单卡 run 的逐位 A/B 不可比；
    #   它是新开一条实验臂的开关，不是透明加速（plan/ppo-optimization.plan.md §3.4）。
    raw_model = model
    if dev_str in ("cuda-dp", "dp"):
        if use_dp:
            _n = torch.cuda.device_count()
            _mb = int(manifest.get("mb", 0) or 0)
            model = torch.nn.DataParallel(model)
            log(
                f"job {jid}: DataParallel 生效（{_n} 卡"
                + (f"，mb={_mb} -> 每卡 {_mb // _n}" if _mb else "")
                + "）——梯度归约顺序变化，与单卡 run 数值不可逐位比"
            )
        else:
            log(
                f"job {jid}: 请求了 cuda-dp 但只可见 {torch.cuda.device_count()} 张卡"
                " —— 退化为单卡（行为等同 --device cuda）"
            )

    # ---- BC-anchored kickstart ref（§363）：有系数无尺子＝静默裸奔，不可接受——
    # 缺字节响亮拒绝；系数为 0 直接跳过（零开销，旧 manifest 行为不变）。
    kick_kl = float(manifest.get("kickstart_kl", 0.0) or 0.0)
    # 阈值判据（见 remote/protocol.NEGLIGIBLE_COEF）：课程按几何衰减永远到不了精确 0，
    # 实测 1.455e-11 时旧判据 `> 0` 仍会加载 ref 并每轮预计算 3 s。用 coef_active 兜底，
    # 也覆盖"旧 hub 产出的、仍带微小系数的在途 manifest"。
    if kick_kl != 0.0 and not coef_active(kick_kl):
        log(f"job {jid}: kickstart_kl={kick_kl:g} 低于阈值 —— 按关闭处理（省 ref 加载+预计算）")
        kick_kl = 0.0
    ref_model: torch.nn.Module | None = None
    if kick_kl > 0:
        import hashlib as _hl

        ref_sha = str(manifest.get("ref_sha", "") or "")
        ref_b64 = str(manifest.get("ref_weights_b64", "") or "")
        ref_fp = str(manifest.get("ref_weights_fp", "") or "")
        # M2 B3：ref 也走内容寻址（ref_sha = sha256(raw 权重) = ref_weights_fp）。
        ref_raw, ref_hit, ref_src = _resolve_blob(
            blob_root=blob_root,
            name=BLOB_REF,
            sha=ref_sha,
            inline_b64=ref_b64,
            jid=jid,
            base_url=base_url,
            token=token,
            preloaded=preloaded,
            log=log,
        )
        if not ref_raw:
            raise ProtocolError(f"job {jid}: kickstart_kl={kick_kl} 但无 ref_weights——拒收")
        if ref_fp and _hl.sha256(ref_raw).hexdigest() != ref_fp:
            raise ProtocolError(f"job {jid}: ref_weights 指纹不符——拒收")
        if ref_hit and ref_src == "cache":
            blob_hits += 1
        if ref_src == "download":
            blob_miss_bytes += len(ref_raw)
        ref_path = job_dir / "ref_weights.json"
        ref_path.write_bytes(ref_raw)
        ref_model = ppo_engine.build_ppo(str(ref_path))
        load_state_into(ref_model, str(ref_path))
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()
        ref_model.to(device_t)
        if use_dp:
            ref_model = torch.nn.DataParallel(ref_model)
        log(f"job {jid}: kickstart ref 已加载（BC 冻结 master，kl={kick_kl}）")

    # ---- PPO：load → chunk → update（同一 backend 调用链，D4） ----
    shards_root = str(job_dir)
    t_ppo = time.time()
    episodes = ppo_engine.load_episodes(
        shards_root,
        float(manifest["gamma"]),
        float(manifest["lam"]),
        normalize_adv=str(manifest["adv_norm"]) != "none",
        normalize_ret=bool(manifest.get("normalize_ret", False)),
        # 严格样本量配额（target_transitions 路线）：逐关只收前 N 步，截断在 GAE
        # 之前。0/缺失（旧 hub 产出的 manifest）= 全收，历史行为逐字节不变。
        per_stage_quota=int(manifest.get("per_stage_quota", 0) or 0),
    )
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    chunks = ppo_engine.chunk_episodes(
        episodes, int(manifest["mb"]), shuffle=bool(manifest["shuffle"])
    )
    agg = ppo_engine.ppo_update(
        model,
        opt,
        chunks,
        int(manifest["epochs"]),
        device_t,
        kl_coef=float(manifest["kl_coef"]),
        # ent_coef：None（旧 hub / 未配）→ 引擎常量 ENT_COEF；0.0 是合法值，不能 `or` 兜底。
        ent_coef=(
            None if manifest.get("ent_coef") is None else float(manifest["ent_coef"])
        ),
        ref_model=ref_model,
        kickstart_kl=kick_kl,
    )
    ppo_sec = round(time.time() - t_ppo, 1)
    log(
        f"job {jid}: PPO done in {ppo_sec}s, steps={total_steps} chunks={len(chunks)} kl={agg.get('kl')}"
    )

    # ---- 产物：weights_json（save_weights_json，D12/G1）+ _ppo_save tar（D5） ----
    # XLA：先落图执行边界再物化回主机。否则 state_dict() / save_weights_json 读到的是
    # 尚未执行的惰性图（权重是最新一轮 `mark_step` 时的快照，不是本轮终态）。
    from ppo.common import _ppo_save, xla_mark_step

    xla_mark_step(device_t)
    # ⚠ 用 raw_model 而非 model：DP 包装的 state_dict 键带 "module." 前缀（已实证），
    #   写出去会让 ckpt 与单卡路径互不兼容（课程 resume 会炸）。raw_model 与 DP 共享
    #   同一批参数对象，.to("cpu") 对两者等价。
    raw_model.to("cpu")
    wj_path = job_dir / "weights.json"
    save_weights_json(raw_model, str(wj_path))
    ckpt_dir = job_dir / "ppo_final"
    _ppo_save(str(ckpt_dir), raw_model, opt, int(manifest["epochs"]))
    opt_tar_raw = pack_opt_tar(ckpt_dir)
    opt_tar_b64 = encode_opt_tar(opt_tar_raw)
    # M2 B3：把刚产出的 raw opt tar 写进 blob_cache（键 = sha256）——下一轮 hub 的
    # opt_sha 由 verify_and_land 落盘的同一份原始字节算出，故同会话内 100% 命中。
    _cache_blob(blob_root, hashlib.sha256(opt_tar_raw).hexdigest(), opt_tar_raw, log)

    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(wj_path.read_bytes()),
        "opt_tar_b64": opt_tar_b64,
        "agg": {
            "policy": float(agg.get("policy", 0.0)),
            "value": float(agg.get("value", 0.0)),
            "entropy": float(agg.get("entropy", 0.0)),
            "kl": float(agg.get("kl", 0.0)),
            "kickstart": float(agg.get("kickstart", 0.0)),
            "mean_ret": float(agg.get("mean_ret", 0.0)),
            "steps": int(total_steps),
            "chunks": len(chunks),
        },
        "commit_echo": manifest["commit"],
        "ppo_sec": ppo_sec,
    }
    if iter_info is not None:
        # M3：节点自己跑的 rollout 的采集口径（协议层必校——hub 侧无本地 shard 可算）。
        result["report"] = iter_info["report"]
    result["wire"] = _wire_block(
        payload_bytes=len(raw),
        payload_dl_sec=payload_dl_sec,
        unpack_sec=unpack_sec,
        opt_restore_sec=opt_restore_sec,
        grad_sec=ppo_sec,
        blob_hits=blob_hits,
        blob_miss_bytes=blob_miss_bytes,
        ts_code_bytes=ts_code_bytes,
        ts_code_hit=ts_code_hit,
        rollout_sec=(iter_info or {}).get("rollout_sec"),
        bun_version=(iter_info or {}).get("bun_version"),
    )
    # 两遍收敛（同 echo 路径）：result_bytes 与自身体长自指，一遍差它的十进制位数。
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    # ---- 半离线尾巴（kind=run）：本轮跑完 → 把计划里剩下的轮次自己跑完 ----
    # 位置在前面的自查**之前**：合并结果是「末轮形状 + iters 明细」，自查要用最终形状。
    if str(manifest["kind"]) == "run" and not echo:
        from remote.run_loop import run_plan_job

        assert run_plan is not None  # kind=run 必过 verify_plan_file（上面已抛）
        result = run_plan_job(
            job_id=jid,
            manifest=manifest,
            job_dir=job_dir,
            work_dir=work_dir,
            plan=run_plan,
            plan_sha256=run_plan_sha,
            first_result=result,
            course=course,
            device=device,
            torch_threads=torch_threads,
            code_cache_dir=code_cache_dir,
            ts_code_cache_dir=ts_code_cache_dir,
            artifacts_dir=artifacts_dir,
            max_iters=run_max_iters,
            budget_sec=run_budget_sec,
            # 产物补传：本 job 就是从这条连接上领来的（地址与 token 手边就有）——半离线段
            # 因此默认开着补传：hub 中途失联也不至于「跑完一整段、控制面一无所知」。
            hub_url=base_url,
            hub_token=token,
            # 补传的**归位键**：hub 在 `/jobs/next` 里告诉我们这份活属于哪门课（多课程
            # hub 里没有它，每条补传都会被 400 「无法归属课程」拒掉——而 manifest 里的
            # `course_name` 是课程文件的 name 字段，与 hub 的课程键不是一回事）。
            hub_course=str(job.get("course") or ""),
            log=log,
        )
    validate_result(result, manifest, commit_echo_must_match=False)  # 自查
    _persist_result(work_dir, jid, result)
    return result


# ------------------------------------------------------------------ 主循环


#: 本进程**已 import 的**代码 sha（对比 manifest["code_sha256"] 揭穿热替换）
_ACTIVE_CODE_SHA: str | None = None


def _request_reload(restart_argv: list[str] | None, log=lambda msg: None) -> bool:
    """热替换：有监督器 → 以 HOT_RELOAD_EXIT 干净退出，交监督器拉起新进程；无 → False。

    为什么不再 os.execve（2026-09-11 线上事故）：notebook 里 worker_loop 跑在 kernel
    进程内，execv 会**原地替换 kernel 镜像**——ipykernel 对 sys.stdout 的重定向对象
    随之丢失（单元格输出直接断流，只剩 kernel server 的控制台能看见），且 ZMQ 执行
    服务不再应答，Jupyter 判定 kernel 死。用户看到"自重启"后单元格没下文 → 按停止 →
    SIGINT 打断正在跑的 worker → kernel 重启 → 云端会话报废（本次事故的完整链条）。

    现统一契约：worker 以退出码 HOT_RELOAD_EXIT 退出，由 **监督器**（supervise_worker）
    用同一套参数重新拉起子进程——fresh 进程里 sys.modules 必然为空，新代码一定生效；
    监督器本身（notebook 的 kernel）不 execv、输出流不断、也不被判定死亡。

    restart_argv 仍只认**显式传入**：notebook 里 sys.argv 是 kernel 自己的参数。
    None = 没有监督器（裸 worker_loop 直调）→ 返回 False，调用方降级为提示人工重启。
    """
    if not restart_argv:
        log("自重启不可用：未提供 restart_argv（无监督器可拉起新进程）")
        return False
    log(f"代码已变更 —— 以退出码 {HOT_RELOAD_EXIT} 交监督器重启（fresh 进程加载新代码）")
    raise SystemExit(HOT_RELOAD_EXIT)


def supervise_worker(
    restart_argv: list[str],
    *,
    cmd: list[str] | None = None,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker-supervisor] {msg}", flush=True),
) -> int:
    """监督器：worker 跑在**子进程**里，热替换以 exit(HOT_RELOAD_EXIT) 请求重启。

    - 输出转发：子进程 stdout/stderr → 本进程 stdout 逐行转发。notebook 里本函数在
      kernel 进程内执行，转发让日志持续进单元格；CLI 下等价于直通。
    - 热替换：子进程退 HOT_RELOAD_EXIT → 用同一套参数重新拉起（fresh 进程加载新代码）。
      换代码从"打掉 kernel"变成一次无害的拉起重演，kernel/输出流永不中断。
    - KeyboardInterrupt：先终止子进程再上抛（中断单元格不会留下孤儿 worker）。
    - 返回子进程最终退出码（热替换已内部消化，不会带 86 返回）。

    cmd：测试注入口（默认 [sys.executable, -u, -m, remote.worker, *restart_argv]）。
    """
    nn_root = str(Path(__file__).resolve().parents[1])  # remote/ -> nn-training/
    if cmd is None:
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "remote.worker",
            *(str(a) for a in restart_argv),
        ]
    while True:
        env = dict(os.environ)
        # 子进程要能 import remote.worker（notebook 的 sys.path 不进子进程，只能靠 PYTHONPATH）
        env["PYTHONPATH"] = nn_root + os.pathsep + env.get("PYTHONPATH", "")
        # 子进程 main() 看到该标记直跑 worker_loop，不再递归监督
        env["REMOTE_WORKER_CHILD"] = "1"
        log(f"spawn worker 子进程（{len(restart_argv)} 参数）")
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            child_out = proc.stdout
            if child_out is None:  # stdout=PIPE，结构化保证非空；只为让我 mypy 类型收窄
                raise RuntimeError("supervise_worker: stdout=PIPE 却拿不到管道（不该发生）")
            for line in child_out:  # `-u` 保证子进程每行即刷，转发不滞后
                print(line, end="", flush=True)
            rc = proc.wait()
        except KeyboardInterrupt:
            log("收到中断 —— 终止 worker 子进程")
            try:
                proc.kill()
            except Exception:
                pass
            raise
        if rc == HOT_RELOAD_EXIT:
            log("worker 代码已变更 —— 重新拉起子进程加载新代码（输出流不中断）")
            continue
        return rc


def _release_cloud_machine(
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> None:
    """尽力真释放云机（§386，用户确认：worker 退出≠停机省钱）。

    worker 是 supervise_worker 拉起的**子进程**，不在 IPython kernel 里——
    `google.colab.runtime.unassign()` 需要 `get_ipython().kernel`，子进程里是 None。
    所以写哨兵文件，由 notebook cell 的 keepalive 循环（跑在 kernel 里）检测并执行 unassign。

    - Colab：写 /tmp/battle-halt-request 哨兵 → keepalive 检测 → kernel 里调 unassign()。
    - 其它（Kaggle 等）：无释放 API——诚实提示必须人工在宿主页面断开/关闭会话。
    任何失败都不抛（停机链路绝不能反过来崩 worker）。
    """
    sentinel = Path("/tmp/battle-halt-request")
    try:
        sentinel.write_text(str(time.time()))
        log("已写停机哨兵 /tmp/battle-halt-request（notebook keepalive 将检测并释放实例）")
    except OSError as e:
        log(f"写停机哨兵失败：{e}——请手工断开宿主会话")
    # 兼容：如果 worker 恰好跑在 kernel 里（单测 / 非 supervise 场景），直接试一次
    try:
        runtime_mod: Any = importlib.import_module("google.colab.runtime")
        ipython_mod = importlib.import_module("IPython")
        if ipython_mod.get_ipython() is not None:
            log("检测到 Colab kernel 环境 → 直接调用 runtime.unassign()")
            runtime_mod.unassign()
            return
    except Exception:
        pass


def worker_loop(
    base_url: str,
    token: str,
    *,
    work_dir: Path,
    device: str = "cpu",
    torch_threads: int = 0,
    poll_sec: float = 5.0,
    once: bool = False,
    echo: bool = False,
    max_idle_sec: float = 0.0,
    restart_argv: list[str] | None = None,
    hub_urls: list[str] | None = None,
    # 半离线（kind=run）：产物根与本次自主段上限（透传给 run_job；缺省 = 自动解析/只认计划）
    artifacts_dir: str | Path | None = None,
    run_max_iters: int = 0,
    run_budget_sec: float = 0.0,
    # 离线训练模式（2026-09-19）：本会话能自己跑完整段 ⇒ 带能力头领离线课
    offline_ok: bool = False,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> int:
    """无状态轮询主循环。返回处理的 job 数。

    once=True：处理一个 job 后退出（M1 假云回环冒烟用）。
    max_idle_sec>0：连续空闲超过该时长退出（M2 会话活性观测用）。
    restart_argv：热替换时**监督器重启**用的参数列表（不含解释器与 `-m remote.worker`）；
      传值 = 有监督器（supervise_worker / 新版 main()）→ 热替换以 HOT_RELOAD_EXIT
      退出，由监督器用同一套参数重新拉起子进程加载新代码；
      None = 无监督器 → 热替换降级为提示人工重启并返回。
    hub_urls：多 hub 轮询（P3b §3.8：`--poll` 可重复/逗号分隔，同 token）。
      None/空 → [base_url]（默认行为零变化）。多 hub 时 round-robin 串行 run_job
      （单 GPU 互挤否决项），work_dir 按 hub 索引分区（`hub0/<jid>`…），code_cache
      留共享根。异 commit job 由既有 _ACTIVE_CODE_SHA 守卫转 86 + 监督器重拉。
    """
    done = 0
    idle_since = time.time()
    _last_alive_log = time.time()
    _polls_since_log = 0
    _polls_since_accept = 0
    # 多 hub 轮询（P3b §3.8）：None/空 → 单 hub（默认行为零变化）；多 hub round-robin。
    hubs = [u for u in (hub_urls or [base_url]) if u]
    if not hubs:
        hubs = [base_url]
    multi = len(hubs) > 1
    shared_code_cache = work_dir / "code_cache"
    hi = 0
    # §386：停机状态感知（过渡尝试一次；halt 清除后复位，下次停机可再试）——按 hub 独立。
    halt_seen: dict[int, bool] = {}
    while True:
        base_url = hubs[hi]
        # work 分区（P3b C3）：多 hub 按源分区 hub0/<jid>…（分区才语义正确）；
        # 单 hub 沿用旧根（默认行为零变化）。code_cache 永远共享根。
        part_dir = work_dir / f"hub{hi}" if multi else work_dir
        if multi:
            part_dir.mkdir(parents=True, exist_ok=True)
        try:
            _polls_since_log += 1
            _polls_since_accept += 1
            job = poll_job(
                base_url,
                token,
                log=log,
                hub_scope=len(hubs),  # 竞速判定输入：本 worker 服务几个 hub
                worker_id=worker_tag(),
                offline_ok=offline_ok,  # 能力自报：能自己跑完整段
            )
        except Exception as e:
            log(f"poll failed: {e} — retry in {poll_sec}s")
            time.sleep(poll_sec)
            hi = (hi + 1) % len(hubs)
            continue
        got_halt = isinstance(job, dict) and job.get("halt") is True
        if got_halt and not halt_seen.get(hi):
            # §386：停机命令随任务同发——先尝试真停机（Colab unassign）；
            # 停不掉（Kaggle 无 API）→ **照常执行下面的任务**（云机活着就不闲置，能继续训练）。
            halt_seen[hi] = True
            log("云端停机达令已送达：先尝试停机宿主实例（停不掉则照常执行任务）")
            _release_cloud_machine(log)
        elif not got_halt and halt_seen.get(hi):
            halt_seen[hi] = False  # 停机条件消失（hub resume）→ 复位
        if job is None:
            if once:
                break  # --once：无 job 或已处理完都退出（冒烟/单发）
            if max_idle_sec > 0 and time.time() - idle_since > max_idle_sec:
                log(f"idle > {max_idle_sec}s — exit")
                break
            # 每 60s 打一次 alive 日志，让用户知道 worker 在正常运行
            # （附带周期内请求数——验证轮询周期真在生效 + 附带连续空闲秒数便于判断孤儿 job）。
            if time.time() - _last_alive_log > 60:
                log(
                    f"polling hub (no job yet, {done} done, {_polls_since_log} polls, idle {int(time.time() - idle_since)}s)"
                )
                _last_alive_log = time.time()
                _polls_since_log = 0
            time.sleep(poll_sec)
            hi = (hi + 1) % len(hubs)  # 空闲也轮转：多 hub 下一个也得被问到
            continue
        if job.get("halt") is True and not job.get("job_id"):
            # 纯停机达令、无任务：不退出、继续等待（云机活着=随时可续训）。
            # 这里 job 非 None → 上面的 None 分支存活日志会被吞——补一条同频日志，
            # 否则停机期日志静默会被误读为"worker 罢工"（2026-09-11 现场）。
            if time.time() - _last_alive_log > 60:
                log(
                    f"cloud halted, polling hub (no job yet, {done} done, "
                    f"{_polls_since_log} polls, idle {int(time.time() - idle_since)}s)"
                )
                _last_alive_log = time.time()
                _polls_since_log = 0
            time.sleep(poll_sec)
            hi = (hi + 1) % len(hubs)  # 纯停机达令也轮转
            continue
        idle_since = time.time()
        _polls_since_log = 0  # claim 即上报：alive 行下次只数 claim 之后的轮询，不与本行重复
        jid = job["job_id"]
        lease_token = str(job.get("lease_token", "") or "")
        raced = bool(job.get("race"))  # 竞速副本（无租约）：先回传者胜，后到者 409 丢弃
        log(
            f"job {jid} claimed"
            + (" [RACE 副本：先回传者胜]" if raced else "")
            + f" — downloading payload ({_polls_since_accept} polls since last accepted result)"
        )
        # 心跳线程仅在有租约时启动（P3b 独占 hub 下发 lease_token；无租约
        # （旧 hub/§343 时代）则不续租，结果胜负由首写锁定决定）。
        # job 执行期间 60s 周期续租（长 job 靠它活过 CLAIM_TTL_SEC），job 结束 join。
        _hb_stop = threading.Event()
        hb_thread = None
        if lease_token:

            def _hb_loop() -> None:
                while not _hb_stop.wait(HEARTBEAT_SEC):
                    heartbeat(base_url, token, jid, lease_token)

            hb_thread = threading.Thread(target=_hb_loop, daemon=True, name=f"hb-{jid[:8]}")
            hb_thread.start()
        job_ok = False
        try:
            result = run_job(
                base_url,
                token,
                job,
                work_dir=part_dir,
                device=device,
                torch_threads=torch_threads,
                echo=echo,
                code_cache_dir=shared_code_cache if multi else None,
                lease_token=lease_token,
                artifacts_dir=artifacts_dir,
                run_max_iters=run_max_iters,
                run_budget_sec=run_budget_sec,
                log=log,
            )
            _post_st = post_result(base_url, token, jid, result, lease_token=lease_token)
            log(
                f"job {jid} done — "
                + ("lost the race (409, 赢家已落账) — 本份丢弃" if _post_st == 409 else "result accepted")
            )
            done += 1
            _polls_since_accept = 0
            job_ok = True
        except RetryableError as e:
            # 瞬时失败（网络/5xx/传输损坏）：主动还租约立即回池——不再付 30min 过期等待
            log(f"job {jid} 瞬时失败: {e} — release 租约回池，立即可重领")
            release_job(base_url, token, jid, lease_token, log=log)
        except CodeChangedError as e:
            # 热替换：本进程 sys.modules 是旧代码，继续跑 = 用旧逻辑产出"看着正常"的
            # 结果。有监督器 → 以 HOT_RELOAD_EXIT 干净退出，由 supervise_worker 用同一
            # 套参数重新拉起子进程（fresh sys.modules → 新代码生效，输出流不断）。
            # 无监督器（裸 worker_loop 直调）→ 降级为提示人工重启。
            log(f"job {jid}: {e}")
            release_job(base_url, token, jid, lease_token, log=log)  # 别占着租约等重启
            if not _request_reload(restart_argv, log=log):
                log(
                    "无监督器 —— 请手动重启本进程"
                    "（notebook: Runtime → Restart runtime 后重跑步骤 4）"
                )
                return done
        except ProtocolError as e:
            log(f"job {jid} REJECTED: {e} — skip (not retried)")
            # 确定性拒绝（commit 不符/模式不符/节点能力缺失如 bun 装不上）不重试——
            # 轮询下一个。
            # 2026-09-17：**必须把原因报给 hub**，否则这条确定性失败在训练侧只表现为
            # 25 分钟超时（能力缺失被读成网络/排队问题，且每次重试白烧一个超时窗口）。
            # 只在这一分支报（CodeChangedError 会重启进程靠租约回池、RetryableError
            # 靠 release 回池，报了就等于把可恢复的 job 钉死）；hub 侧把它落成终局后
            # 该 job 不再回池，重发同 job（同幂等键）会清标记。
            report_job_failure(
                base_url,
                token,
                jid,
                str(e) or type(e).__name__,
                kind=type(e).__name__,
                detail=_failure_detail(e),
                lease_token=lease_token,
                log=log,
            )
        except Exception as e:
            log(f"job {jid} FAILED: {type(e).__name__}: {e} — will re-poll (idempotent)")
            # 瞬态失败（网络/远端关闭）：租约未续会自动回池，重拉同 job 幂等
        finally:
            # 每 job 一行传输账：payload/code/blob/result 的 (bytes, sec) + 零字节命中 + 重抽
            _wire_flush(jid, log)
            _hb_stop.set()
            if hb_thread is not None:
                hb_thread.join(timeout=HEARTBEAT_SEC + 5)
        if once:
            # H8（review-hy）：--once 模式 job 失败必须非零退出——冒烟/单发场景
            # 退出码 0 会静默掩盖失败（smoke 只判 returncode）
            return -1 if not job_ok else done
        hi = (hi + 1) % len(hubs)  # 跑完一个换下一 hub（round-robin 公平）
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="remote PPO worker (cloud, stateless)")
    ap.add_argument(
        "--poll",
        required=True,
        action="append",
        help="hub-server base URL（可重复/逗号分隔多 hub，同 token；"
        "P3b 多课程共享 worker：本地采集与云端 PPO 可执行不同课程任务）",
    )
    ap.add_argument("--token", default="", help="bearer token（与 hub-server 一致）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    ap.add_argument("--out", default="tmp/remote-worker", help="work dir (payloads/ckpts)")
    ap.add_argument("--device", default="cpu", help="torch device: cpu / cuda / cuda:0")
    ap.add_argument("--threads", type=int, default=0, help="torch intra-op threads (0=default)")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--once", action="store_true", help="处理一个 job 后退出")
    ap.add_argument(
        "--echo",
        action="store_true",
        help="冒烟：跳过 PPO，回显 init 权重为结果（hub-start 冒烟预演；消费方作废本轮）",
    )
    ap.add_argument("--max-idle-sec", type=float, default=0.0, help="空闲超时退出（0=永不）")
    # ---- 半离线（kind=run；2026-09-17）----
    # 产物目录：缺省按 Kaggle /kaggle/working → Colab Drive → <work>/artifacts 自动解析。
    ap.add_argument(
        "--artifacts",
        default="",
        help="半离线产物目录（kind=run 的交付面；缺省自动：Kaggle /kaggle/working / Colab Drive）",
    )
    ap.add_argument(
        "--run-max-iters",
        type=int,
        default=0,
        help="本次自主段最多再跑几轮（0=只认计划；计划本身也有上限）",
    )
    ap.add_argument(
        "--run-budget-sec",
        type=float,
        default=0.0,
        help="本次自主段最多跑多少秒（0=不限；Kaggle 会话到点前干净停机的把手）",
    )
    # ---- 离线训练模式（2026-09-19）----
    # 能力自报：本会话能自己跑完整段（kind=run）。hub 只把**离线课**的 job 放给带标的
    # worker；不带标就领不到（离线课不实时派发，但不是谁都领得到的公共池）。
    # 这是能力声明，不是课程绑定：带标 worker 照样领在线课（课程与 worker 正交）。
    ap.add_argument(
        "--offline",
        action="store_true",
        help="自报「能自主跑完整段」：领离线课的整段 job（kind=run）；带标仍可领在线课",
    )
    args = ap.parse_args()
    token = args.token
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[{time.strftime('%H:%M:%S')}] [worker] ERROR: 读 --token-file 失败: {e}", flush=True)
            sys.exit(1)
    if not token:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] ERROR: 需要 --token 或 --token-file", flush=True)
        sys.exit(1)
    # 2026-09-08 双 tmp 统一：相对 work 路径锚定仓库根（remote/ 上溯 3 层），
    # 不再落到 nn-training/tmp（此前 spawn cwd=nn-training 时相对路径走偏）。
    out = Path(args.out)
    if not out.is_absolute():
        out = Path(__file__).resolve().parents[2] / out
    out.mkdir(parents=True, exist_ok=True)
    # P3b：--poll 可重复/逗号分隔（同 token）；单值 = 旧行为。顺序即轮询顺序。
    raw_polls = args.poll if isinstance(args.poll, list) else [args.poll]
    hub_urls = [u.strip().rstrip("/") for p in raw_polls for u in str(p).split(",") if u.strip()]
    if not hub_urls:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] ERROR: --poll 为空", flush=True)
        sys.exit(1)
    if len(hub_urls) > 1:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] 多 hub 轮询（round-robin）：{hub_urls}", flush=True)
    if os.environ.get("REMOTE_WORKER_CHILD") == "1":
        # ── 子进程模式（由监督器 supervise_worker / 新版 main() 拉起）──
        # 热替换必须退 HOT_RELOAD_EXIT(86) 让监督器重拉：worker_loop 以
        # restart_argv「非空 = 有监督器」判定走退出码（空 = 无监督器返回）。
        # 这里传本进程的参数列表（与监督器那侧同源）即可，子进程不自己重拉。
        n = worker_loop(
            hub_urls[0],
            token,
            work_dir=out,
            device=args.device,
            torch_threads=args.threads,
            poll_sec=args.poll_sec,
            once=args.once,
            echo=args.echo,
            max_idle_sec=args.max_idle_sec,
            restart_argv=sys.argv[1:],
            hub_urls=hub_urls,
            artifacts_dir=args.artifacts or None,
            run_max_iters=args.run_max_iters,
            run_budget_sec=args.run_budget_sec,
            offline_ok=args.offline,
        )
        print(f"[{time.strftime('%H:%M:%S')}] [worker] done: {n} job(s) processed", flush=True)
        # H8：--once 失败（返回 -1）→ 非零退出码
        sys.exit(0 if n >= 0 else 1)
    # ── 监督器模式（默认入口）──
    # worker 由子进程承担（REMOTE_WORKER_CHILD=1 直跑上面的分支），输出逐行转发到
    # 本进程 stdout —— notebook 里本进程是 kernel，转发保住单元格输出流，换代码不会
    # 再打掉 kernel（2026-09-11 线上事故修复，见 _request_reload 的 docstring）。
    # sys.argv[1:] 就是可重放的热替换参数（argv[0] 可能是 -m 或脚本路径，统一由
    # supervise_worker 用 `-m remote.worker` 重建，故这里只取参数部分）。
    rc = supervise_worker(sys.argv[1:])
    print(f"[{time.strftime('%H:%M:%S')}] [worker-supervisor] worker exited: rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
