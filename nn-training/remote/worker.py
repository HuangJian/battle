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
from pathlib import Path
from typing import Any

from common.protocol import (
    BLOB_DEMO,
    BLOB_OPT,
    BLOB_REF,
    CLAIM_MODE_BACKUP,
    CLAIM_MODE_EXCLUSIVE,
    HEARTBEAT_SEC,
    JOB_CANCEL_POLL_SEC,
    PAYLOAD_NAME,
    PRIORITY_NONE,
    PRIORITY_ORDER,
    WIRE_V2_CONTENT_TYPE,
    CodeChangedError,
    JobCancelledError,
    ProtocolError,
    RetryableError,
    coef_active,
    encode_opt_tar,
    encode_weights_json,
    job_seed,
    normalize_manifest,
    pack_result_v2,
    validate_result,
)
from common.protocol import (
    d14_corpus_match as protocol_d14_corpus_match,
)
from common.text import exc_tail

# BC 作业（S4 第六步之二 → `remote/bc_job.py`）：**显式转发**（e2e / tests 直接 import
# 这些名字，见该模块头部；本组无 monkeypatch 接缝）。
from remote.bc_job import (
    _bc_device as _bc_device,
)
from remote.bc_job import (
    _bc_fetch_resume as _bc_fetch_resume,
)
from remote.bc_job import (
    _bc_load_local_resume as _bc_load_local_resume,
)
from remote.bc_job import (
    _bc_local_resume_dir as _bc_local_resume_dir,
)
from remote.bc_job import (
    _bc_post_epoch as _bc_post_epoch,
)
from remote.bc_job import (
    _bc_store_local_resume as _bc_store_local_resume,
)
from remote.bc_job import (
    _run_bc_job as _run_bc_job,
)
from remote.bc_job import (
    normalize_ppo_device as normalize_ppo_device,
)
from remote.bc_job import (
    resolve_bc_seed as resolve_bc_seed,
)
from remote.bulk_sched import (
    BULK_P1_CRITICAL,
    BULK_P2_PREFETCH,
    BulkPreemptError,
)

# 下载簇（S4 第七刀 → `remote/download.py`）：**显式转发**。宿主（run_job / _prefetch_fill）
# 经这些转发名调用 ⇒ patch `remote.worker.download_*` 仍有效；但**组内互调**（`_resolve_blob`
# → `download_blob`）解析在本模块 ⇒ 那类测试要 patch `remote.download`。
from remote.download import (
    _cache_blob as _cache_blob,
)
from remote.download import (
    _ensure_ts_code as _ensure_ts_code,
)
from remote.download import (
    _progress_logger as _progress_logger,
)
from remote.download import (
    _resolve_blob as _resolve_blob,
)
from remote.download import (
    download_blob as download_blob,
)
from remote.download import (
    download_code as download_code,
)
from remote.download import (
    download_payload as download_payload,
)
from remote.download import (
    download_ts_code as download_ts_code,
)
from remote.http import (
    _POLL_WARN_AT as _POLL_WARN_AT,
)

# HTTP 传输核心（S4 第五步 → `remote/http.py`）：**显式转发**，见该模块头部。
# 注入点分档：patch 后调宿主函数的仍 patch `remote.worker`；patch 后调
# `_get_with_retry` / `_read_body` 的必须 patch `remote.http`。
from remote.http import (
    BODY_CHUNK as BODY_CHUNK,
)
from remote.http import (
    BODY_IDLE_TIMEOUT_SEC as BODY_IDLE_TIMEOUT_SEC,
)
from remote.http import (
    BODY_PROGRESS_MIN_SEC as BODY_PROGRESS_MIN_SEC,
)
from remote.http import (
    BODY_TOTAL_TIMEOUT_SEC as BODY_TOTAL_TIMEOUT_SEC,
)
from remote.http import (
    _get_opener as _get_opener,
)
from remote.http import (
    _get_with_retry as _get_with_retry,
)
from remote.http import (
    _opener as _opener,
)
from remote.http import (
    _read_body as _read_body,
)
from remote.http import (
    _request as _request,
)
from remote.http import (
    _sched_headers as _sched_headers,
)
from remote.http import (
    _warn_non_200 as _warn_non_200,
)
from remote.iter_rollout import run_iter_rollout

# 作业工作区 / 产物落盘 / TAR / git 物化（S4 第六步 → `remote/job_fs.py`）：**显式转发**。
# 本组无 monkeypatch 接缝（全仓都是直接调用）⇒ 转发即够。
from remote.job_fs import (
    JOB_DIR_KEEP as JOB_DIR_KEEP,
)
from remote.job_fs import (
    _ensure_commit as _ensure_commit,
)
from remote.job_fs import (
    _git_head as _git_head,
)
from remote.job_fs import (
    _persist_result as _persist_result,
)
from remote.job_fs import (
    pack_opt_tar as pack_opt_tar,
)
from remote.job_fs import (
    prune_job_dirs as prune_job_dirs,
)
from remote.job_fs import (
    unpack_opt_tar as unpack_opt_tar,
)
from remote.job_fs import (
    unpack_payload_or_fail as unpack_payload_or_fail,
)
from remote.prefetch import (
    PREFETCH_DEPTH_DEFAULT,
    PrefetchStore,
    pick_candidates,
)
from remote.result_upload import (
    RESULT_UPLOAD_MODE_DEFAULT,
    RESULT_UPLOAD_MODES,
    Outcome,
    ResultUploader,
    UploadTask,
)

# 传输账 / 低速重抽 / bulk 节流（S4 拆分 → `remote/wire.py`）。这里是**显式转发**：
# 状态与实现同住 `wire`，避免模块全局在拆分后变成两份账（见 `remote/wire.py` 头部）。
# `_BEST_RATE` 是重绑式的会话标量 —— 它的注入点是 `remote.wire._BEST_RATE`。
from remote.wire import (
    _BEST_RATE as _BEST_RATE,
)
from remote.wire import (
    _BULK as _BULK,
)
from remote.wire import (
    _WIRE as _WIRE,
)
from remote.wire import (
    WIRE_MAX_JOBS as WIRE_MAX_JOBS,
)
from remote.wire import (
    WIRE_MIN_RATE as WIRE_MIN_RATE,
)
from remote.wire import (
    WIRE_PROBE_BYTES as WIRE_PROBE_BYTES,
)
from remote.wire import (
    WIRE_PROBE_SEC as WIRE_PROBE_SEC,
)
from remote.wire import (
    WIRE_RATE_SAMPLE_MIN_BYTES as WIRE_RATE_SAMPLE_MIN_BYTES,
)
from remote.wire import (
    WIRE_REROLL_BUDGET_SEC as WIRE_REROLL_BUDGET_SEC,
)
from remote.wire import (
    WIRE_REROLL_MAX as WIRE_REROLL_MAX,
)
from remote.wire import (
    WireSlowError as WireSlowError,
)
from remote.wire import (
    _bulk_pace as _bulk_pace,
)
from remote.wire import (
    _min_rate as _min_rate,
)
from remote.wire import (
    _note_rate as _note_rate,
)
from remote.wire import (
    _reroll_decision as _reroll_decision,
)
from remote.wire import (
    _wire_add as _wire_add,
)
from remote.wire import (
    _wire_bucket as _wire_bucket,
)
from remote.wire import (
    _wire_flush as _wire_flush,
)
from remote.wire import (
    _wire_hit as _wire_hit,
)
from remote.wire import (
    _wire_note_reroll as _wire_note_reroll,
)
from remote.wire import (
    _wire_start as _wire_start,
)
from remote.wire import (
    _wire_time as _wire_time,
)
from remote.wire import (
    set_bulk_log as set_bulk_log,
)


def peek_jobs(
    base_url: str,
    token: str,
    *,
    worker_id: str = "",
    offline_ok: bool = False,
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
        headers=_sched_headers(worker_id, offline_ok=offline_ok) or None,
    )
    if status != 200:
        _warn_non_200(base_url, status, log)
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
        _warn_non_200(base_url, status, log)
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
        headers={"Content-Type": "application/json", **_sched_headers(worker_id)},
    )
    if status != 200:
        _warn_non_200(base_url, status, log)
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
    offline_ok: bool = False,
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
        offline_ok=offline_ok,
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
        t_a = time.time()  # 本次尝试的墙钟（传输账用；`t0` 含退避，不适合算速率）
        try:
            # P1 关键回传：占唯一 bulk 通道且**不被抢断**（POST 大 body 没有安全 Range）。
            # 占槽范围 = 单次尝试；退避睡眠在槽外（绝不抱着通道睡 16s）。
            with _BULK.slot(BULK_P1_CRITICAL, label="result") as _tok:
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


#: 热替换退出码：worker 子进程代码变更时以该码退出，**监督器**（supervise_worker /
#: 新版 main()）收到后用同一套参数重新拉起子进程（fresh 进程 → sys.modules 必然为空
#: → 新代码生效）。不用 0（=正常完成）：处理失败/退出原因必须可区分。
HOT_RELOAD_EXIT = 86


# ------------------------------------------------------------------ PPO 执行


# D14 比对规则的**唯一实现**住 `common.protocol`（发布端 `hub_client.iter_shard_dirs`
# 打包时用同一条规则挑选 shard）——这里只做名字转发，保持既有 import/调用面不变。
d14_corpus_match = protocol_d14_corpus_match


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
    # ---- 调度面（2026-09-22，plan/transfer-scheduling R2-5）----
    # `should_cancel()`：每 **epoch 边界** 问一次「结果是不是已经 landed」——是则抛
    # `JobCancelledError`（停算丢弃，零回传/零 fail）。不在 chunk 内层加回调（用户拍板：
    # 不值得为 15s→1s 去动 `ppo_update` 内层结构）。
    # `on_ppo_start()`：PPO 真正启动前一刻打点（hub 把它记成 `computing_at` = 掉队阈值
    # 的**唯一**时基；下载/解包/权重装载都已完成的那个瞬间）。
    should_cancel: Any = None,
    on_ppo_start: Any = None,
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
    # 解包失败 = 内容决定性失败（走 ProtocolError → 确定性上报，不重认领）——见 §4 事故。
    _unused_manifest, shard_dirs = unpack_payload_or_fail(zip_path, job_dir)
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
        from ppo.common import (
            tpu_backend_missing_reason,
            xla_device,
            xla_device_speed_probe,
            xla_enable_compile_cache,
            xla_fingerprint,
            xla_world_size,
        )

        # 持久化编译缓存：必须在**任何计算之前**（下面 xla_device() 之后的指纹/速度自检就会
        # 产生第一张图）。缓存被挤出时读盘而非重编，不改变任何数值——真机 ragged tail 每轮
        # 多付的 ~14s 就是缓存淘汰后的重编（docs/nn/tpu-perf.md §6）。
        log(
            f"job {jid}: XLA 持久化编译缓存 {xla_enable_compile_cache(work_dir / 'xla-compile-cache')}"
        )
        device_t = xla_device()
        # ★ 2026-09-22（Kaggle TPU 实例上离线课程 PPO 单步 8~9s ⇒ 疑似静默跑 CPU）：XLA 的
        #   CPU 插件也返回 `xla:0`，所以「设备字符串」证明不了什么；这里把**后端指纹**与一次
        #   速度自检打出来，并在后端不是 TPU 时**拒跑**（在 CPU 上跑完整段看起来一切正常，
        #   只是慢两个数量级——正是最该响的那类静默降级）。
        _fp = xla_fingerprint(device_t)
        _spd = xla_device_speed_probe(device_t)
        log(
            f"job {jid}: TPU/XLA 设备 {device_t}｜device_type={_fp['device_type']}｜"
            f"XLA 设备数={_fp['global_device_count']}（本进程可见 {_fp['addressable_device_count']}）｜"
            f"replication={_fp['replication_devices']}｜attrs={_fp['attrs']}｜"
            f"world_size={xla_world_size()}（无复制时恒 1，**别拿它当 TPU 判据**）｜"
            f"2048² matmul {(_spd * 1000) if _spd is not None else float('nan'):.1f} ms"
            "（TPU 量级 ~ms；~10ms+ = 后端是 CPU）"
        )
        _why = tpu_backend_missing_reason(_fp)
        if _why:
            raise ProtocolError(
                f"job {jid}: 要的是 TPU，但 XLA 运行时不是 TPU 后端（{_why}）——**拒跑**。"
                "在 CPU 上跑完整段会看起来完全正常、只慢两个数量级，所以这里宁可停下："
                "① 确认 `PJRT_DEVICE=TPU` 在**任何** torch_xla import/初始化之前就已设置"
                "（XLA 运行时一经初始化就不能再换后端）；"
                "② Kaggle/Colab 上先单独打印 `torch_xla.runtime.device_type()` 与"
                "`global_device_count()` 对账（TPU v5e-8 ⇒ 8）；"
                "③ 若 ①② 都正常而设备属性仍无 TPU 指纹，重开 runtime（设备可能被别的进程占着）。"
            )
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
    # ★ §4.2（2026-09-21）：restore 段的崩溃归确定性失败（带 traceback 摘要）——
    # 它是本段最容易“静默重演”的一处（优化器/模型卷积不兼容会逐一重演到天亮）。
    try:
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
    except ProtocolError:
        raise  # 上游已判定的确定性失败（缺 blob / 缺 init 权重）原样上抛
    except Exception as e:
        raise job_body_error("restore（model/opt 恢复）", e) from e

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
    # 阈值判据（见 common/protocol.NEGLIGIBLE_COEF）：课程按几何衰减永远到不了精确 0，
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
        # ★ §4.2：ref 装载同属 restore——ref 与 policy 的架构/形状不合会在每一份字节上
        # 重演（而它只会被写成一行云机日志，训练侧看到的是超时）。
        try:
            ref_model = ppo_engine.build_ppo(str(ref_path))
            load_state_into(ref_model, str(ref_path))
            for p in ref_model.parameters():
                p.requires_grad = False
            ref_model.eval()
            ref_model.to(device_t)
            if use_dp:
                ref_model = torch.nn.DataParallel(ref_model)
            log(f"job {jid}: kickstart ref 已加载（BC 冻结 master，kl={kick_kl}）")
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（kickstart ref 装载）", e) from e

    # ---- demo 混 batch（x20 后续）：bank 内容寻址 + 装载校验 ----
    # 安全阀同 ref：coef>0 而 bank 不可得 ⇒ 响亮失败，绝不静默降级为纯 PPO
    # （那会让 demo 腿的整轮更新在日志一切正常下丢失 demo 项）。
    demo_coef = float(manifest.get("demo_bc_coef", 0.0) or 0.0)
    demo_per_mb = int(manifest.get("demo_per_mb", 0) or 0)
    demo_bank: dict | None = None
    if demo_coef > 0 and demo_per_mb > 0:
        import io as _io

        import numpy as _np

        demo_sha = str(manifest.get("demo_sha", "") or "")
        if not demo_sha:
            raise ProtocolError(f"job {jid}: demo_bc_coef>0 但无 demo_sha——拒收")
        demo_raw, demo_hit, demo_src = _resolve_blob(
            blob_root=blob_root,
            name=BLOB_DEMO,
            sha=demo_sha,
            inline_b64="",
            jid=jid,
            base_url=base_url,
            token=token,
            preloaded=preloaded,
            log=log,
        )
        if demo_hit and demo_src == "cache":
            blob_hits += 1
        if demo_src == "download":
            blob_miss_bytes += len(demo_raw)
        if not demo_raw:
            raise ProtocolError(f"job {jid}: demo_sha 存在但 blob 不可得——拒收")
        try:
            demo_bank = {k: _np.asarray(v) for k, v in dict(_np.load(_io.BytesIO(demo_raw))).items()}
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（demo bank 装载）", e) from e
        need = {"obs", "scalars", "actions", "masks"}
        if not need.issubset(demo_bank.keys()):
            raise ProtocolError(
                f"job {jid}: demo bank 缺字段 {sorted(need - set(demo_bank.keys()))}——拒收"
            )
        log(
            f"job {jid}: demo bank 已加载（N={demo_bank['obs'].shape[0]}"
            f" coef={demo_coef:g} per_mb={demo_per_mb} src={demo_src}）"
        )

    # ---- PPO：load → chunk → update（同一 backend 调用链，D4） ----
    # ★ §4.2（2026-09-21）：grad 段（含读 shard / 分块）的崩溃归确定性失败。
    # 为什么连读 shard 一起包：那同样是「这份字节决定的」失败（缺字段/形状不符），
    # 而 OOM 那类真瞬态由 `job_body_error` 原样放回重领路径。
    shards_root = str(job_dir)
    if on_ppo_start is not None:
        try:
            # 打点失败不致命：最坏后果是这份 job 的掉队阈值晚起算（多开一份备份）。
            on_ppo_start()
        except Exception:
            pass

    def _cancel_at_epoch_boundary(_ep_done: int, _mdl: Any) -> None:
        """epoch 边界查一次取消（R1-6；延迟判据 <20s）——由 hub 的 landed 驱动。"""
        if should_cancel is None or not should_cancel():
            return
        raise JobCancelledError(
            f"job {jid}: 结果已 landed（别人先赢）——epoch {_ep_done} 边界停算丢弃"
        )

    if should_cancel is not None and should_cancel():
        # 下载/解包/装载期间结果就已 landed：**开算前**就丢，别白烧一整轮 PPO。
        # （epoch 边界那个回调只救得了「开算之后才 landed」的情形。）
        raise JobCancelledError(f"job {jid}: 结果已 landed（下载期间）——开算前丢弃")

    t_ppo = time.time()
    try:
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
            demo_bank=demo_bank,
            demo_bc_coef=demo_coef,
            demo_per_mb=demo_per_mb,
            # ★ 取消接线（R2-5）：今天这条调用**没有**传它——不传则取消延迟永远是
            # 「跑完才响应」。训练侧那条（`rl/stream.py`）传的是双缓冲预采回调，
            # 与这里不是同一个调用点，别去动那一条。
            on_epoch_done=_cancel_at_epoch_boundary if should_cancel is not None else None,
        )
    except ProtocolError:
        raise
    except JobCancelledError:
        # 取消是**正常结局**（备份副本被首写锁定判负）：绝不能落到下面的
        # `job_body_error`（它会把未知异常转成 ProtocolError ⇒ report_job_failure ⇒
        # 训练停腿——把合法放弃报成确定性失败）。
        raise
    except Exception as e:
        raise job_body_error("grad（PPO 更新）", e) from e
    ppo_sec = round(time.time() - t_ppo, 1)
    # P0.5：T_ppo 进传输账（与 T_in/T_out 同一条 `wire` 行 ⇒ 占比可复算，不用人肉拼日志）。
    _wire_time(jid, "ppo", time.time() - t_ppo)
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
            "demo_bc": float(agg.get("demo_bc", 0.0)),
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
            # 补传的**归位键**：hub 在轮询面里告诉我们这份活属于哪门课（多课程
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


#: 预取传输账的合成 job id（预取不属于任何在跑的 job，但又必须记字节——
#: 否则「预取花了多少带宽」只能从 hub 侧对账，而 hub 看到的是同一张脸）。
PREFETCH_WIRE_ID = "prefetch"
#: 预取填充的轮询间隔（秒）：一轮填满后等这么久再问下一次 peek。
PREFETCH_ROUND_SEC = 5.0


def _prefetch_fill(
    base_url: str,
    token: str,
    store: PrefetchStore,
    stop: threading.Event,
    *,
    worker_id: str = "",
    offline_ok: bool = False,
    depth: int = PREFETCH_DEPTH_DEFAULT,
    skip: set[str] | None = None,
    log: Any = None,
) -> None:
    """后台填充软持有队列（P2）：`peek`（控制面，无副作用）→ **P2** 下载 → 暂存。

    与 `run_job` **重叠**运行——这就是预取的全部价值所在（§0：串行把 GPU 饿死在传输上）。
    三条纪律：

      · 下载一律走 `bulk_prio=BULK_P2_PREFETCH`：**可被控制面/关键传输当场打断**（丢半截，
        幂等重下）。预取不该有能力拖慢在跑的 job 或控制环。
      · 失败**不是失败**：被挤走/404/瞬时错误 → 就地丢掉、记一行、下一轮再来。
        **绝不**进 `ProtocolError`/`report_job_failure`（否则网络抖动会被报成节点故障）。
      · 预取只走 `download_payload` 一条路：minimize-payload 的 omit 协商落在那个函数里，
        预取落地后**自动继承**；在这里另写一个「整包 GET」就是把已瘦身的部分又吹回去。
    """
    skip = skip or set()
    log = log or (lambda _m: None)
    while not stop.is_set():
        try:
            peeked = peek_jobs(
                base_url,
                token,
                worker_id=worker_id,
                offline_ok=offline_ok,
                n=max(1, int(depth)),
                log=None,  # 预取的 peek 不进 poll 告警节流表（同一 url 会互相压报）
            )
        except Exception as e:
            log(f"prefetch: peek 失败（{type(e).__name__}）——{PREFETCH_ROUND_SEC:.0f}s 后再试")
            stop.wait(PREFETCH_ROUND_SEC)
            continue
        cands = list(peeked[0]) if peeked else []
        picked = pick_candidates(cands, held=store.held(), skip=skip, depth=depth)
        for cand in picked:
            if stop.is_set():
                break
            jid = str(cand["job_id"])
            try:
                payload = download_payload(
                    base_url,
                    token,
                    jid,
                    bulk_prio=BULK_P2_PREFETCH,
                    wire_jid=PREFETCH_WIRE_ID,  # 账记在合成 id 上，不污染在跑 job 的账
                    log=lambda m, _j=jid: log(f"prefetch {_j[:8]}: {m}"),
                )
            except BulkPreemptError as e:
                log(f"prefetch {jid[:8]}: {e}")
                continue
            except (ProtocolError, RetryableError, CodeChangedError) as e:
                log(f"prefetch {jid[:8]}: 放弃（{type(e).__name__}）——预取失败不算失败")
                continue
            except Exception as e:
                log(f"prefetch {jid[:8]}: 放弃（{type(e).__name__}: {e}）")
                continue
            if store.store(jid, payload, cand):
                log(f"prefetch {jid[:8]}: 已预取 {len(payload)} bytes（软持有，无租约）")
        _wire_flush(PREFETCH_WIRE_ID, log)  # 每轮一行预取传输账
        stop.wait(PREFETCH_ROUND_SEC)
    _wire_flush(PREFETCH_WIRE_ID, log)  # 收尾：最后一次没有等满一轮的也上账


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
    # 软持有预取深度（P2，2026-09-22）：0 = 关预取（只调度不预取，§7 的回退档）
    prefetch_depth: int = PREFETCH_DEPTH_DEFAULT,
    # 结果回传模式（P2.5，2026-09-22）：async = 回传**不占关键路径**（缺省）；sync = 旧行为
    result_upload: str = RESULT_UPLOAD_MODE_DEFAULT,
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
    set_bulk_log(log)  # 调度器的排队/让路/抢占日志与 worker 同一条流
    # 身份只算一次（旧路径每个轮询都调 worker_tag()：hostname 系统调用不贵但没必要
    # 每秒一次；而 hub 的登记表靠这个值去重，值必须稳定）。
    worker_id = worker_tag()
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
    # P2.5 异步回传（2026-09-22）：把 `out` 从关键路径上摘下来。`post_result` 按**调用时**
    # 解析（供测试 monkeypatch）；`sync` = 逐字回退到改造前（`--result-upload sync`）。
    uploader = ResultUploader(upload=post_result, mode=result_upload, log=log)

    def _result_settled(_j: str, out: Outcome, wall_end: float) -> None:
        """回传落定：打结算行 + **把本 job 的传输账收在这一刻**（P2.5）。

        账必须等到这里才收：`out` 的字节/秒是 `post_result` 内部记的，而 async 下它发生
        在关键路径之后 —— 提前 flush 会把回传读成 0s（那正是最该看见的一段）。
        `wall_end` 只在 async 下传（sync = 回传就在关键路径里，口径不变）。
        """
        log(
            f"job {_j} done — "
            + (
                "lost the race (409, 赢家已落账) — 本份丢弃"
                if out.status == 409
                else (
                    "backup 副本被拒（403，非本 job 租约持有人）— 本份丢弃，不算失败"
                    if out.status == 403
                    else "result accepted"
                )
            )
            + (f"  [回传 {out.seconds:.1f}s]" if out.seconds else "")
        )
        _wire_flush(_j, log, wall_end=wall_end if uploader.mode == "async" else None)
    # P2 软持有暂存区（按 hub 分区；`blob_cache` 共享根与 run_job 的口径一致）。
    pf_stores: dict[int, PrefetchStore] = {}
    try:
        while True:
            base_url = hubs[hi]
            # work 分区（P3b C3）：多 hub 按源分区 hub0/<jid>…（分区才语义正确）；
            # 单 hub 沿用旧根（默认行为零变化）。code_cache 永远共享根。
            part_dir = work_dir / f"hub{hi}" if multi else work_dir
            if multi:
                part_dir.mkdir(parents=True, exist_ok=True)
            if prefetch_depth > 0 and hi not in pf_stores:
                pf_stores[hi] = PrefetchStore(
                    part_dir, log=log, blob_root=shared_code_cache.parent / "blob_cache"
                )
            pf_store = pf_stores.get(hi)
            try:
                _polls_since_log += 1
                _polls_since_accept += 1
                # 取活三件套（2026-09-22）：peek（候选，**不认领**）→ priority（job 边界
                # 问询）→ claim（独占）。旧轮询面已退役（R1-9：不兼容旧 worker 是用户
                # 授权的**一次性**切换）——旧 worker 会拿到 404 而不是静默错跑。
                job = acquire_job(
                    base_url,
                    token,
                    worker_id=worker_id,
                    offline_ok=offline_ok,  # 能力自报：能自己跑完整段
                    # P2：已被别人落盘的 job（none 级）就地丢掉本地预取副本——再预取就白花带宽。
                    on_drop=(pf_store.drop if pf_store is not None else None),
                    log=log,
                )
            except Exception as e:
                log(f"acquire failed: {e} — retry in {poll_sec}s")
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
            _wire_start(jid)  # 阶段占比（in/out/ppo/other）的 wall 从 claim 起算
            lease_token = str(job.get("lease_token", "") or "")
            claim_mode = str(job.get("status") or "ok")  # ok（独占）| backup（无租约副本）
            log(
                f"job {jid} claimed [mode={claim_mode}]"
                + ("" if lease_token else "（无租约：先回传者胜，后到者 409 丢弃）")
                + f" — downloading payload ({_polls_since_accept} polls since last accepted result)"
            )
            # 心跳线程仅在有租约时启动（P3b 独占 hub 下发 lease_token；无租约
            # （旧 hub/§343 时代）则不续租，结果胜负由首写锁定决定）。
            # job 执行期间 60s 周期续租（长 job 靠它活过 CLAIM_TTL_SEC），job 结束 join。
            # ---- P2 预取填充（后台，与下面的 run_job 重叠）：PPO_A 跑着的时候下载 B ----
            # 命中即零下载开算（`preloaded` 接缝）；未命中就是「先串行下载关键 payload」，
            # 用命中率压掉空转。填充线程与 job 同生命周期（job 结束就停，绝不留常驻线程）。
            preloaded: dict | None = None
            _pf_stop = threading.Event()
            pf_thread: threading.Thread | None = None
            if pf_store is not None:
                got = pf_store.take(jid)
                want_sha = str((job.get("manifest") or {}).get("payload_sha256") or "")
                if got is not None and (not want_sha or str(got.get("blob_sha")) == want_sha):
                    preloaded = {"payload_zip": got["payload_zip"]}
                elif got is not None:
                    # 暂存副本与 claim 到的这份不是同一字节（hub 换过 job）：丢弃，走关键下载。
                    log(f"job {jid}: 预取副本 sha 与 claim manifest 不符——丢弃走关键下载")
                pf_thread = threading.Thread(
                    target=_prefetch_fill,
                    args=(base_url, token, pf_store, _pf_stop),
                    kwargs={
                        "worker_id": worker_id,
                        "offline_ok": offline_ok,
                        "depth": prefetch_depth,
                        "skip": {jid},
                        "log": log,
                    },
                    daemon=True,
                    name=f"pf-{jid[:8]}",
                )
                pf_thread.start()
            _hb_stop = threading.Event()
            hb_thread = None
            if lease_token:

                def _hb_loop() -> None:
                    while not _hb_stop.wait(HEARTBEAT_SEC):
                        heartbeat(base_url, token, jid, lease_token)

                hb_thread = threading.Thread(target=_hb_loop, daemon=True, name=f"hb-{jid[:8]}")
                hb_thread.start()
            job_ok = False
            uploaded = False  # P2.5：本 job 有没有走到「交回传」（没走到 = finally 里照旧 flush）
            # 取消环（2026-09-22）：唯一硬取消信号 = `landed`（结果已落盘）。backup 副本与
            # 掉队重领者都可能正在算一份**别人已经赢下**的 job——停算的收益是省一张卡的 GPU，
            # 代价是每 1.5s 一个 P0 小包。取消点在 epoch 边界（<20s），实测值进 cancel_latency_s。
            _cancel = threading.Event()
            _watch_stop = threading.Event()
            start_cancel_watcher(base_url, token, jid, _watch_stop, _cancel, log=log)
            _t_ppo0 = time.time()
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
                    preloaded=preloaded,  # P2 命中面：有它则 payload 段零网络
                    log=log,
                    should_cancel=_cancel.is_set,
                    on_ppo_start=lambda: job_started(base_url, token, jid, worker_id=worker_id),
                )
                # 算完待回传（P0 小包）：只降别人的优先级（低档备份保险），**永不**触发取消。
                job_ready(base_url, token, jid, worker_id=worker_id)
                # ★ 关键路径到此为止（P2.5 异步回传）：回传不再占着算力等。交给上传线程，
                #   主循环立刻去领下一份——而下一份的字节多半已被预取到本地（P2），两者
                #   资源不相交（链路 vs CPU/GPU），天然可叠。
                #   用户口径（2026-09-22）：双课程交错已把 rollout/PPO 填满，所以「缩掉关键
                #   路径上的传输」是唯一的胜法；而 `out` 25s > `in` 15s，正是最大的一块。
                _t_ready = time.time()

                def _on_settled(_j: str, _out: Outcome, _t: float = _t_ready) -> None:
                    """绑住本 job 的 `wall_end`（默认参数而非闭包变量——B023 的口径）。"""
                    _result_settled(_j, _out, _t)

                uploader.submit(
                    UploadTask(
                        jid=jid,
                        base_url=base_url,
                        token=token,
                        result=result,
                        lease_token=lease_token,
                        claim_mode=claim_mode,
                        on_settled=_on_settled,
                    )
                )
                uploaded = True
                done += 1
                # 口径微调（P2.5）：async 下此刻还不知道 hub 收没收，所以这一格的含义从
                # 「距上次**被接受**」变成「距上次**产出结果**」（收没收看落定行/收尾行）。
                # 它是存活日志里的诊断读数（是不是在疯狂轮询却不产活），不是判据。
                _polls_since_accept = 0
                job_ok = True
            except JobCancelledError as e:
                # ★ 唯一正确的取消处置（§2.4）：不写 _result.json、不 POST、不报 fail、
                # abandon（release 租约 + 零 reclaim），立刻去问 priority 选下家。
                # 绝不能落到下方 except ProtocolError（= 把合法放弃报成确定性失败 ⇒ 训练停腿）
                # 或 except RetryableError（= 把别人已赢下的活 release 回池）。
                log(
                    f"job {jid} CANCELLED: {e} — 停算丢弃（零回传/零 fail），"
                    f"cancel_latency_s={time.time() - _t_ppo0:.1f}"
                )
                abandon_job(base_url, token, jid, worker_id=worker_id, reason="landed")
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
                # 瞬态失败（网络/远端关闭）：租约未续会自动回池，重拉同 job 幂等。
                # ★ 2026-09-21（§4）：本分支**只准**装真瞬态。内容决定性失败（解包/校验/
                #   运行时能力缺失）必须在上游就转成 `ProtocolError`（见 unpack_payload_or_fail、
                #   manifest 校验、bun 检测），否则同一份字节会无限重领（事故：40 次 / 3.5h 空转）。
            finally:
                # 每 job 一行传输账：payload/code/blob/result 的 (bytes, sec) + 零字节命中 + 重抽。
                # P2.5：async 且本 job **已交回传**时**不**在这里 flush —— `out` 的账要等回传
                # 落定才记完，过早 flush 会把回传读成 0s（`_result_settled` 负责收）。
                # 没走到回传（取消/失败/backup 丢弃）的话回传永不会落定，必须在这里收。
                if not uploaded:
                    _wire_flush(jid, log)
                # 取消环必须每 job 都收（否则一个 job 一个常驻线程，长跑 worker 会漏线程）
                _watch_stop.set()
                _hb_stop.set()
                # 预取填充线程同理：job 结束即停（它最多再跑一轮下载，join 有超时兜底）。
                _pf_stop.set()
                if pf_thread is not None:
                    pf_thread.join(timeout=30.0)
                if hb_thread is not None:
                    hb_thread.join(timeout=HEARTBEAT_SEC + 5)
            if once:
                # H8（review-hy）：--once 模式 job 失败必须非零退出——冒烟/单发场景
                # 退出码 0 会静默掩盖失败（smoke 只判 returncode）。
                # P2.5：async 下成败只能等回传落定才知道，所以 --once 必须先 drain 再判。
                uploader.drain()
                if uploaded:
                    _out = uploader.outcome(jid)
                    if _out is not None:
                        job_ok = _out.ok
                return -1 if not job_ok else done
            hi = (hi + 1) % len(hubs)  # 跑完一个换下一 hub（round-robin 公平）
        return done
    finally:
        # P2.5（2026-09-22）：**每条**退出路径都要过这里——`break`（空闲/停机）、
        # `--once` 的 return、热替换的 SystemExit(86)、以及任何异常。队列里可能还有
        # 没送出去的结果；不等它落定就退出 = 静默丢掉最贵的产物（训练侧要等租约
        # 过期才看得出来，正是「3.5 小时静默」那一类事故）。
        _drained = uploader.close()
        _st = uploader.stats()
        log(
            f"回传收尾: mode={_st['mode']} 提交={_st['submitted']} "
            f"成功={_st['ok']} 失败={_st['failed']} 未落定={_st['pending']} "
            f"同步退化={_st['sync_fallback']}"
        )
        if not _drained or _st['failed']:
            log(
                "★ 回传收尾有未落定/失败的结果——训练侧会在租约过期后才发现，"
                "请按上面的 jid 排查（hub 日志 / 网络）"
            )


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
    # P2.5（2026-09-22）：回传模式。async（缺省）= 回传与下一份 job 并发（把 `out`
    # 从关键路径上摘下来）；sync = 旧行为（回传占关键路径）——现场逃生口。
    ap.add_argument(
        "--result-upload",
        choices=RESULT_UPLOAD_MODES,
        default=RESULT_UPLOAD_MODE_DEFAULT,
        help="结果回传模式：async=不占关键路径（缺省）/ sync=旧行为（逃生口）",
    )
    ap.add_argument(
        "--echo",
        action="store_true",
        help="冒烟：跳过 PPO，回显 init 权重为结果（hub-start 冒烟预演；消费方作废本轮）",
    )
    ap.add_argument("--max-idle-sec", type=float, default=0.0, help="空闲超时退出（0=永不）")
    # P2 软持有预取：把一个 job 的下载叠到上一个 job 的 PPO 上（§2.6）。
    # 0 = 关预取（只调度不预取，plan §7 的回退档）。深度缺省 3（跨课程轮转取）。
    ap.add_argument(
        "--prefetch-depth",
        type=int,
        default=PREFETCH_DEPTH_DEFAULT,
        help=f"软持有预取深度（0=关；缺省 {PREFETCH_DEPTH_DEFAULT}）",
    )
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
            prefetch_depth=args.prefetch_depth,
            result_upload=args.result_upload,
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
