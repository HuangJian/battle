"""remote/job_lifecycle.py — worker 的**作业取活 / 生命周期 / 回传面**（S4 第八刀，2026-09-23）。

从 `remote/worker.py` 搬出来的一整块连续代码（L277‑868，252+ 行）。它是 worker 与 hub 之间的
**作业协议面**：

* **取活三件套** —— `peek_jobs`（无副作用候选查询 + halt 达令 + 离线能力门）→
  `request_priority`（优先级/epoch 快照）→ `claim_job`（独占或备份；落 `lease_token`）；
  `acquire_job` 是这三件套的**编排**（`none` 必弃、`demoted` 换下家、问不到就按「无人在做」选，
  唯一性由 hub 的 claim 闸兜底）；`_priority_rank` 是它的排序键。
* **阶段通知** —— `job_started` / `job_ready` / `abandon_job` / `job_status` / `release_job` /
  `heartbeat`（续租）。取消环 `start_cancel_watcher` 也在这里：只有**正面证据**
  （`landed=True`）才置位，问不到不算赢。
* **上行** —— `post_result`（v2 体 + 403 丢弃语义 + 409 幂等 + 一次 JSON 退路）与
  `report_job_failure`（失败回报）。
* **小助手** —— `worker_tag` / `_failure_detail` / `job_body_error`（作业体崩溃归类：
  瞬态放回重领，确定性转 `ProtocolError`）。

## 依赖方向

`job_lifecycle → {common.protocol, common.text, http, wire, bulk_sched}`（全向下，DAG）。
**不** import `remote.worker`（`worker` 用自别名转发回来）——否则 `remote/` 内部成环。

## ★ 注入点：本仓最密的一组 seam

测试里对这簇有 60+ 处 `monkeypatch.setattr(W, ...)`。判据只有一个——**看调用点解析在哪个
命名空间**：

| 调用点 | 解析在 | patch 目标 |
|---|---|---|
| 宿主（**仍在 `worker.py`**）：`worker_loop` → `acquire_job` / `job_ready` / `abandon_job` / `release_job` / `heartbeat` / `job_started` / `start_cancel_watcher` / `report_job_failure` / `_failure_detail` / `worker_tag`；`run_job` → `job_body_error`；`_prefetch_fill` → `peek_jobs` | `remote.worker` | **`worker`（不动）** |
| **簇内互调**：`acquire_job` → `peek_jobs` / `request_priority` / `claim_job` / `_priority_rank`；`start_cancel_watcher` → `job_status`；`report_job_failure` → `worker_tag` | **本模块** | **`job_lifecycle`** |
| 本簇**直调** `_request` / `_wire_add` / `_BULK` / `_bulk_pace`（`post_result` / `peek_jobs` / `heartbeat` …） | **本模块** | **`job_lifecycle`** |

第三行是本刀最险的一处：`worker.py` 在本刀之后**已无 `_request` 调用点**——`patch
remote.worker._request` 从此不再影响任何东西（`tests/test_job_lifecycle_split.py` 把这条
钉成警报）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from common.protocol import (
    CLAIM_MODE_BACKUP,
    CLAIM_MODE_EXCLUSIVE,
    JOB_CANCEL_POLL_SEC,
    PRIORITY_NONE,
    PRIORITY_ORDER,
    ROLE_ONLINE,
    WIRE_V2_CONTENT_TYPE,
    ProtocolError,
    RetryableError,
    pack_result_v2,
)
from common.text import exc_tail
from remote.bulk_sched import BULK_P1_CRITICAL
from remote.http import (
    _request,
    _sched_headers,
    _warn_non_200,
)
from remote.wire import (
    _BULK,
    _bulk_pace,
    _wire_add,
)

__all__ = [
    "_failure_detail",
    "_priority_rank",
    "abandon_job",
    "acquire_job",
    "claim_job",
    "heartbeat",
    "job_body_error",
    "job_ready",
    "job_started",
    "job_status",
    "peek_jobs",
    "post_result",
    "release_job",
    "report_job_failure",
    "request_priority",
    "start_cancel_watcher",
    "worker_tag",
]

def peek_jobs(
    base_url: str,
    token: str,
    *,
    worker_id: str = "",
    role: str = ROLE_ONLINE,
    n: int = 3,
    timeout: float = 30.0,
    log: Any = None,
) -> tuple[list[dict], bool] | None:
    """`GET /jobs/peek?n=K` → `([候选…], halt)`；hub 不可达/被拒 → None（已记日志）。

    与旧轮询面的三点差别（都是设计意图，不是实现细节）：
      ① **不认领**——返回的是候选，本地缓存它们（软持有/预取）不产生任何租约；
      ② 无副作用：hub 侧不动轮转游标（R2-C2），所以「先看一眼」不会移走别人的轮次；
      ③ 停机达令同行（承接退役的旧轮询面）。
    """
    status, body = _request(
        base_url,
        token,
        f"/jobs/peek?n={max(1, int(n))}",
        timeout=timeout,
        headers=_sched_headers(worker_id, role=role) or None,
    )
    if status != 200:
        _warn_non_200(base_url, status, log, body=body)
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs")
    cands = (
        [j for j in jobs if isinstance(j, dict) and j.get("job_id")]
        if isinstance(jobs, list)
        else []
    )
    return cands, data.get("halt") is True


def request_priority(
    base_url: str,
    token: str,
    *,
    worker_id: str = "",
    held: list[str] | tuple[str, ...] = (),
    computing: str = "",
    ready_upload: str = "",
    timeout: float = 30.0,
    log: Any = None,
) -> dict:
    """`POST /jobs/priority` → `{epoch, priorities, reasons}`；不可达 → `{epoch:None,...}`。

    不可达时的回落是**有意的**：拿不到优先级就按「无人在做」（highest）选——
    网络抖动不应该把 worker 变成只等不干的空转卡；唯一性由 hub 侧的 claim 闸兜底，
    而「选一份别人正在算的活」的代价只是白算一份（首写定胜负），比空转便宜。
    """
    payload = json.dumps(
        {
            "worker_id": worker_id,
            "held": [str(j) for j in held],
            "computing": computing or None,
            "ready_upload": ready_upload or None,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        status, body = _request(
            base_url,
            token,
            "/jobs/priority",
            timeout=timeout,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **_sched_headers(worker_id),
            },
        )
    except Exception as e:  # 不可达：按 highest 下垂（见 docstring）
        if log is not None:
            log(f"priority 问询失败（{type(e).__name__}）——按无人在做处理")
        return {"epoch": None, "priorities": {}, "reasons": {}}
    if status != 200:
        _warn_non_200(base_url, status, log, body=body)
        return {"epoch": None, "priorities": {}, "reasons": {}}
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"epoch": None, "priorities": {}, "reasons": {}}
    if not isinstance(data, dict):
        return {"epoch": None, "priorities": {}, "reasons": {}}
    if not isinstance(data.get("priorities"), dict):
        data["priorities"] = {}
    return data


def claim_job(
    base_url: str,
    token: str,
    jid: str,
    *,
    mode: str = CLAIM_MODE_EXCLUSIVE,
    worker_id: str = "",
    role: str = ROLE_ONLINE,
    expected_epoch: int | None = None,
    timeout: float = 30.0,
    log: Any = None,
) -> dict | None:
    """`POST /jobs/{id}/claim` → 响应 dict（`status ∈ ok|backup|demoted`）；拒收 → None。

    语义要点：
      · `expected_epoch` = 上一次 priority 响应的版本。不匹配**不是错误**（有人比我快）——
        hub 会回 `demoted`，调用方按 low 处理（还有别的活就换，只剩它就算备份）；
      · `status="backup"` 时 `lease_token` 为空串（备份无租约）⇒ 不心跳、不续租；
      · 409（冻结/避让/未知 job）→ None，调用方丢副本。
    """
    payload = json.dumps(
        {"mode": mode, "worker_id": worker_id, "expected_epoch": expected_epoch},
        ensure_ascii=False,
    ).encode("utf-8")
    status, body = _request(
        base_url,
        token,
        f"/jobs/{jid}/claim",
        timeout=timeout,
        data=payload,
        method="POST",
        # ★ 归属头必须随 claim 一起发（F6 的老毛病是「头只到 peek，claim 不带」；
        # 而在归属闸下沉到 `_claim_locked` 之后，claim 不带头 = 带标 worker 自锁）。
        headers={"Content-Type": "application/json", **_sched_headers(worker_id, role=role)},
    )
    if status != 200:
        _warn_non_200(base_url, status, log, body=body, jid=jid)
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def job_started(base_url: str, token: str, jid: str, *, worker_id: str = "", timeout: float = 15.0) -> None:
    """`POST /jobs/{id}/start`：打 `computing_at`（掉队阈值的唯一时基，R2-C1）。

    抛点 = PPO 真正启动前一刻（下载/解包/权重装载都已完成），**不是** claim 之后。
    尽力而为：不可达不致命（最坏后果是这份 job 的掉队阈值晚一点起算）。
    """
    try:
        _request(
            base_url,
            token,
            f"/jobs/{jid}/start",
            timeout=timeout,
            data=json.dumps({"worker_id": worker_id}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    except Exception:
        pass


def job_ready(base_url: str, token: str, jid: str, *, worker_id: str = "", timeout: float = 15.0) -> None:
    """`POST /jobs/{id}/ready`：算完待回传（P0 小包，只降别人的优先级、**永不**取消）。"""
    try:
        _request(
            base_url,
            token,
            f"/jobs/{jid}/ready",
            timeout=timeout,
            data=json.dumps({"worker_id": worker_id}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    except Exception:
        pass


def abandon_job(
    base_url: str,
    token: str,
    jid: str,
    *,
    worker_id: str = "",
    reason: str = "",
    timeout: float = 15.0,
) -> None:
    """`POST /jobs/{id}/abandon`：合法放弃（R1-3）= **release 租约** + 零 reclaim。

    为什么不只 `release_job`：release 只还租约、不清 hub 侧的 `claimed/computing/ready`
    可见性，那份 job 的优先级会永远上不到 highest（死 worker 的承诺挂着）。幂等。
    """
    try:
        _request(
            base_url,
            token,
            f"/jobs/{jid}/abandon",
            timeout=timeout,
            data=json.dumps({"worker_id": worker_id, "reason": reason}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    except Exception:
        pass  # 不可达：租约过期兜底（与 release_job 同策略）


def job_status(base_url: str, token: str, jid: str, *, timeout: float = 15.0) -> dict | None:
    """`GET /jobs/{id}/status` → 摘要 dict；不可达/未知名 → None（调用方不据此取消）。"""
    try:
        status, body = _request(base_url, token, f"/jobs/{jid}/status", timeout=timeout)
    except Exception:
        return None
    if status != 200:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def start_cancel_watcher(
    base_url: str,
    token: str,
    jid: str,
    stop: threading.Event,
    cancelled: threading.Event,
    *,
    interval: float = JOB_CANCEL_POLL_SEC,
    timeout: float = 15.0,
    log: Any = None,
) -> threading.Thread:
    """计算期间盯 `landed`（**唯一**硬取消信号）——独立线程走 P0 小包。

    为什么必须有它：backup 副本与掉了队的重领者都可能正在算一份**已经有人赢下**的 job，
    而「谁赢了」只有 hub 知道（结果落盘）。轮询的代价是每 1.5s 一个小 GET，
    收益是停止烧一张卡的 GPU（现网口径：T_ppo < 60s，一轮就是一次白算）。

    `landed=True` 时置 `cancelled`，由 `run_job` 在 epoch 边界抛出 —— 延迟判据 <20s
    （R1-6）；实测值由调用方记进 `cancel_latency_s`。
    """

    def _loop() -> None:
        while not stop.wait(interval):
            st = job_status(base_url, token, jid, timeout=timeout)
            if st is None:
                continue  # 问不到 ≠ 赢了：只看正面证据（宁多算一轮，不错杀）
            if st.get("landed") is True:
                if log is not None:
                    log(f"job {jid}: hub 已有结果（landed）——请求停算（epoch 边界生效）")
                cancelled.set()
                return

    th = threading.Thread(target=_loop, daemon=True, name=f"cancel-{jid[:8]}")
    th.start()
    return th


def _priority_rank(prio: str) -> int:
    """优先级排序键（越大越优先）。未知档位按最高处理（不确定时宁可去干，见 `request_priority`）。"""
    try:
        return PRIORITY_ORDER.index(prio)
    except ValueError:
        return len(PRIORITY_ORDER)


def acquire_job(
    base_url: str,
    token: str,
    *,
    worker_id: str = "",
    role: str = ROLE_ONLINE,
    depth: int = 3,
    on_drop: Any = None,
    log: Any = None,
) -> dict | None:
    """job 边界的取活三件套：`peek → priority → claim`（§2.3 / §2.6）。

    `on_drop(jid)`：`none` 级的通知面（P2 用它丢本地预取副本——「别人已落盘」的 job
    再预取就是白花带宽；回调抛错不影响选活）。

    返回形状与旧轮询面 **逐字段兼容**（`{job_id, manifest, halt, lease_token, course}`
    或 `{"halt": True}` 或 None）——`worker_loop` 的下游（心跳/停机达令/run_job）零改动。

    选活规则：优先级降序，`none`（= 结果已落盘）**直接丢**（它同时是批量取消信号）；
    同一档内按 peek 给的顺序（= hub 的跨课程轮转序）取第一份。claim 回 `demoted` 时
    继续试下一份（§2.3 ④）——「还有别的活就换，只剩它就算备份」的对称面是：
    demoted 的那一份**不**回头当备份（备份要有明确收益，交给预取/掉队救援去触发）。
    """
    peeked = peek_jobs(
        base_url,
        token,
        worker_id=worker_id,
        role=role,
        n=depth,
        log=log,
    )
    if peeked is None:
        return None
    cands, halt = peeked
    if not cands:
        return {"halt": True} if halt else None
    pr = request_priority(
        base_url,
        token,
        worker_id=worker_id,
        held=[c["job_id"] for c in cands],
        log=log,
    )
    prios = pr.get("priorities") or {}
    epoch = pr.get("epoch") if isinstance(pr.get("epoch"), int) else None
    # `none` **先整批处理**（不是排到队尾再跳过）：它是批量取消信号——本地软持有/
    # 预取的副本此刻就该作废。放在排序里「顺手 skip」的话，一旦前一个候选 claim 成功
    # 就 return 了，后面那些 none 副本永远不会被清（P2 预取落地后这条就是存储泄漏）。
    alive = []
    for cand in cands:
        jid = str(cand["job_id"])
        if str(prios.get(jid, "highest")) == PRIORITY_NONE:
            if log is not None:
                log(f"job {jid}: priority=none（别人已落盘）——丢弃本地副本，不再试领")
            if on_drop is not None:
                try:
                    on_drop(jid)
                except Exception:  # 丢副本失败绝不影响选活
                    pass
            continue
        alive.append(cand)
    if not alive:
        return {"halt": True} if halt else None
    ranked = sorted(alive, key=lambda c: -_priority_rank(str(prios.get(c["job_id"], "highest"))))
    for cand in ranked:
        jid = str(cand["job_id"])
        got = claim_job(
            base_url,
            token,
            jid,
            mode=CLAIM_MODE_EXCLUSIVE,
            worker_id=worker_id,
            role=role,  # ★ 与 peek 同一份归属（漏了它 = 带标 worker 自锁）
            expected_epoch=epoch,
            log=log,
        )
        if got is None:
            continue
        if str(got.get("status")) == "demoted":
            if log is not None:
                log(f"job {jid}: claim 被降级（epoch 变过/已有承诺者）——试下一份")
            continue
        if got.get("job_id") and got.get("manifest"):
            got.setdefault("course", cand.get("course"))
            return got
    return {"halt": True} if halt else None


def post_result(
    base_url: str,
    token: str,
    jid: str,
    result: dict,
    lease_token: str = "",
    timeout: float = 120.0,
    attempts: int = 5,
    mode: str = "ok",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> int:
    """POST 结果：瞬时失败（网络/5xx）指数退避重试（最贵产物不允许最后一米丢失）；
    4xx = 确定性拒绝立即抛 ProtocolError；409 = hub 已有同 job 结果（幂等，按成功）。

    `mode`（2026-09-22，R1-2）：本份是 **backup 副本**（`mode="backup"`）时，403
    lease-mismatch 按**丢弃**处理（返回 403，不抛）——它是「这份活已被别人赢下」的
    同义面，而不是确定性失败。不这么分的话 backup 副本会被读成
    `ProtocolError` ⇒ `report_job_failure` ⇒ **训练停腿**（一个赢家把输家炸成事故）。
    非 backup 的 403 仍走原路径（那才是真的协议违规）。
    """
    last: str = ""
    # 方案B（2026-09-10）：v2 体 = gzip 裸二进制段（省掉 base64 的 33% 膨胀）。
    # 实测线上 1,150,292 -> 862,717 B（相对未压缩的 1,634,596 省 47.2%），上行 ~5.2 -> ~3.9 s。
    # JSON 体保留作**旧 hub 的退路**（见下方 4xx 分支）。
    req_body = pack_result_v2(result)
    ctype = WIRE_V2_CONTENT_TYPE
    req_body_json = json.dumps(result, ensure_ascii=False).encode("utf-8")
    t0 = time.time()
    for attempt in range(1, attempts + 1):
        # 段账口径（2026-09-25）：只算**进槽之后**的真实上传 —— 进槽前的排队归调度账
        # （`wait=` / `排队 … 才拿到单通道`）。现场 `result=0.63MB/18-19s` 与同一段日志里的
        # `排队 17.6s` 高度喷合：那一大段大部分是排队，不是上传。
        t_xfer = time.time()
        try:
            # P1 关键回传：占唯一 bulk 通道且**不被抢断**（POST 大 body 没有安全 Range）。
            # 占槽范围 = 单次尝试；退避睡眠在槽外（绝不抱着通道睡 16s）。
            with _BULK.slot(BULK_P1_CRITICAL, label="result") as _tok:
                t_xfer = time.time()  # ★ 排队结束、开传那一刻
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
                    pace=_bulk_pace(_tok, BULK_P1_CRITICAL),
                )
        except Exception as e:
            status, body = None, repr(e).encode()
        if status in (200, 201):
            _wire_add(jid, "result", len(req_body), time.time() - t_xfer)
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
            _wire_add(jid, "result", len(req_body), time.time() - t_xfer)  # 字节确实出去了
            log("result POST 409（hub 已有同 job 结果：竞速输家/重复回传）——按成功丢弃")
            return 409
        if status == 403 and mode == CLAIM_MODE_BACKUP:
            # backup 副本被租约闸拦下（R1-2）：单列为「丢弃」，**绝不**进 ProtocolError
            # （那会触发 report_job_failure ⇒ 训练停腿）。不重试：结果已在别人手里。
            log("result POST 403（backup 副本非当前租约持有人）——按成功丢弃，不报失败")
            return 403
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

    唯一实现见 `common.text.exc_tail`（截尾不截头、绝不抛的理由都在那里）；本名保留
    为调用点别名。`rl/stream.py::_exc_tail` 是本函数的同源孪生，两边现已同源。
    """
    return exc_tail(e, limit)


#: 作业体（restore/grad）崩溃里**仍按瞬态**处理的异常特征：换台机器 / 换个时机可能就好了。
#: OOM 最重要（不同 worker 显存不同）；`OSError` 含磁盘/权限类瞬态（与 `unpack_payload_or_fail`
#: 同口径：那里也刻意不吞 OSError）。
_TRANSIENT_BODY_EXC = (RetryableError, MemoryError, OSError)
_TRANSIENT_BODY_NAMES = ("OutOfMemoryError", "CudaOutOfMemoryError")
_TRANSIENT_BODY_HINTS = ("out of memory", "no space left on device", "cuda error: initialization")


def job_body_error(phase: str, e: BaseException) -> BaseException:
    """把**作业体崩溃**归类（plan/accident.plan.md §4.2，2026-09-21）。

    为什么需要这一步：`restore`（模型/优化器/ref 装载）与 `grad`（PPO 更新）里的
    异常目前落到 `worker_loop` 的 `except Exception` ⇒ 只写一行云机日志就重领——
    同一份字节上，这个崩溃会**逐一重演**，而训练侧只看到超时（§4 事故的同一形状：
    真实原因在最里面，外面只剩一行“超时”）。

    返回 `ProtocolError` = 内容/模型决定性 ⇒ 走既有确定性回传（`report_job_failure` →
    hub 终局 failed → 训练侧 `JobFailedError` 带原因停腿）；返回原异常 = 真瞬态 ⇒ 调用方
    `raise` 它，照旧靠租约过期/`release` 重领（钉成终局会把「换台机器就能跑」变成停腿）。

    判据是**异常类型 + 消息特征**（不用白名单的另原因是：torch 的错误类型随版本漂，
    把类别名硬编进去反而脆）。拿不准时归瞬态——熔断（§4.1）是那类“未知死法”兜底，
    判错方向有兜底，而错钉终局没有。
    """
    if isinstance(e, _TRANSIENT_BODY_EXC):
        return e
    if type(e).__name__ in _TRANSIENT_BODY_NAMES:
        return e
    msg = str(e).lower()
    if any(h in msg for h in _TRANSIENT_BODY_HINTS):
        return e
    return ProtocolError(
        f"{phase} 崩溃（内容决定性：同一份字节重领也会同一处炸）：{type(e).__name__}: {e}"
    )


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
