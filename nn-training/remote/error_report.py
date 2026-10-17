"""remote/error_report.py — 云机错误上报腿（纯观测、失败即丢、不阻塞）。

动机（plan/component-error-registry §4.3-S3b，2026-10-16）：云机的两类失败**只在云机**——

  * pull 的机器级停滞 / 瞬时失败（`remote/job_round.py` 的三个 except 分支：子进程收不了尸、
    网络/5xx/传输损坏、兜底瞬态）——除了云机日志，本机看不到任何东西；
  * 离线段的失败（`remote/run_loop.py` 的 `[run] 失败`）——段末摘要只报 `state`，失败原因
    彻底丢失（离线段告警只看「有没有新产物」，不看为什么）。

形状**照抄** `POST /admin/worker-prefetch`（`hub/admin.py::_post_worker_prefetch`）：

    POST <hub>/admin/worker-error
      Authorization: Bearer <与 job 面同一枚 token>
      body: {worker, component, course?, job_id?, it?, severity?, reason, detail?, ts?}

三条硬约束（G9，任何一条破了都是「观测腿干扰训练」）：

  1. **不阻塞**：发送在 **daemon 线程**里（超时 2s），调用点**立刻返回** —— pull 轮询循环的
     间隔与 `state.kick()` 的时序零影响；
  2. **失败即丢**：连不上 / 超时 / 非 2xx / 坏 JSON 一律吞掉，**不重试**、不抛（观测面坏掉
     不该拖垮训练）；
  3. **不进任何判据**：本模块不参与派发、租约、游标；`report_error` 的返回值是 `None`。

为什么**不**复用 `/jobs/{id}/fail`（评审 F7/F8 的正面回答）：那条腿有「首写胜出 + 只收确定性
失败」的语义（`hub/store_results.py`），把瞬时错误塞进去会污染「训练侧读到的那一条失败原因」；
而且 push worker 手里没有出站 token（E3 那条路因此改走既有 410 通道，见 `worker_server.py`）。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from typing import Any

from common import net_http
from common.protocol import ADMIN_WORKER_ERROR_PATH, AUTH_HEADER

#: 单次上报的超时（秒）。取 2s：这比任何一次 job 传输都短几个数量级，又足够覆盖一次跨隧道
#: 的 POST（失败的代价是丢掉一条观测，不是重试）。
WORKER_ERROR_TIMEOUT_SEC = 2.0

#: `reason` / `detail` 的长度上限（与 hub 侧 `note_worker_error` 的截断、控制台
#: `ERROR_DETAIL_MAX` 同量：一处口径，三处对齐）。
WORKER_ERROR_REASON_MAX = 2000
WORKER_ERROR_DETAIL_MAX = 4000


def default_worker_id() -> str:
    """上报者身份：`hostname:pid`。

    离线腿（`run_loop.py`）没有 worker 注册表里的 id（那个 id 在 pull 循环的宿主手里），而
    「哪台机器报的」是现场的必要信息 —— hostname 就是那个答案；带 pid 是为了同一台机器上
    多进程/LAN 补评面可分辨。pull 腿一律传自己真实的 `worker_id`（那是 hub 认得的身份）。
    """
    try:
        host = socket.gethostname() or "cloud"
    except Exception:  # 极端环境下 gethostname 都能抛——不因为取名失败而丢掉上报
        host = "cloud"
    return f"{host}:{os.getpid()}"


def error_payload(
    *,
    worker: str,
    reason: str,
    component: str = "ppo-worker",
    detail: str = "",
    course: str = "",
    job_id: str = "",
    it: int | None = None,
    severity: str = "warn",
) -> dict[str, Any]:
    """上报体（**纯函数**，用例直接喂它）：字段名与 hub 读面、控制台投影同源。

    `severity` 只发两档（`err`/`warn`）：hub 侧也只认这两档（其余归 `warn`），而**兜底那条路**
    （`job_round.py` 的 `except Exception`）只准装真瞬态 ⇒ 缺省 `warn`。`it` 缺省不下发
    （`None` ⇒ 键缺席：控制台按「不可知」渲染，不编 0）。
    """
    rec: dict[str, Any] = {
        "worker": str(worker),
        "component": str(component),
        "course": str(course),
        "job_id": str(job_id),
        "severity": "err" if severity == "err" else "warn",
        "reason": str(reason)[:WORKER_ERROR_REASON_MAX],
        "detail": str(detail)[:WORKER_ERROR_DETAIL_MAX],
        "ts": time.time(),
    }
    if it is not None:
        rec["it"] = int(it)
    return rec


def post_error(
    base_url: str,
    token: str,
    payload: dict[str, Any],
    *,
    timeout: float = WORKER_ERROR_TIMEOUT_SEC,
) -> bool:
    """同步发一次上报（**用例直接调它**；生产路径只经 `report_error` 的线程）。

    返回是否 2xx；**任何异常都吞掉并返回 False**（绝不让观测面把调用方带崩）。
    回环地址经 `common.net_http`（绕开代理，与 hub↔worker 其余出口同规）。
    """
    if not base_url or not token:
        return False
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{ADMIN_WORKER_ERROR_PATH}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={AUTH_HEADER: f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with net_http.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def report_error(
    base_url: str,
    token: str,
    *,
    reason: str,
    detail: str = "",
    component: str = "ppo-worker",
    course: str = "",
    job_id: str = "",
    it: int | None = None,
    severity: str = "warn",
    worker_id: str = "",
    log: Any = None,
) -> None:
    """上报一条云机错误；**立即返回**（发送在 daemon 线程里）。

    `base_url`/`token` 缺一 ⇒ 静默丢弃：没有 hub 地址就没有这条腿（旧调用方、离线无 hub 的
    本地预演、单测），而**这不是错误**（观测面没有不存在的义务）。
    """
    if not base_url or not token:
        return
    payload = error_payload(
        worker=worker_id or default_worker_id(),
        reason=reason,
        component=component,
        detail=detail,
        course=course,
        job_id=job_id,
        it=it,
        severity=severity,
    )

    def _send() -> None:
        ok = post_error(base_url, token, payload)
        if log is not None and not ok:
            # 失败只留一行给**云机日志**（那是它的最后一站），不重试、不抛。
            try:
                log(f"错误上报未送达（观测腿，失败即丢）：{payload['reason'][:120]}")
            except Exception:
                pass

    threading.Thread(target=_send, daemon=True, name="error-report").start()
