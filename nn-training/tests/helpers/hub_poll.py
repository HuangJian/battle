"""`/jobs/next` 退役后的测试侧同形取活助手（R2-8）。

判据 = 「旧面能做的三件事，新面各做各的」：挑活 → `GET /jobs/peek`；认领 →
`POST /jobs/{id}/claim`；停机达令 → peek 响应同行。本模块把它们拼回**与旧
`/jobs/next` 逐字段同形**的返回值（`{job_id, manifest, halt, lease_token, course}` /
`{halt: True}` / `None`），好让既有用例机械替换而不各写十份会各自漂的实现。

放在 `tests/helpers/` 而不是各文件里：R2-8 点名的 10 个文件都打过旧面，一处改一份
就是十份分叉。**不**给生产兼容层——旧 worker 拿到 404 就是 404（R1-9 要的响亮）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

WORKER_ID_HEADER = "X-Worker-Id"
OFFLINE_CAP_HEADER = "X-Battle-Offline"
OFFLINE_CAP_VALUE = "1"


def req(
    base: str,
    token: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """一次 JSON 往返 → `(status, parsed_body)`（非 2xx 也返回，不抛）。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw.decode("utf-8")) if raw else {})
        except ValueError:
            return e.code, {}


def hub_poll(
    base: str,
    token: str,
    *,
    worker_id: str = "",
    offline_ok: bool = False,
    n: int = 1,
    timeout: float = 10.0,
) -> dict | None:
    """旧 `/jobs/next` 的同形替代（`peek` + `claim`）。

    返回 `{job_id, manifest, halt, lease_token, course}`；无活但停机 ⇒ `{"halt": True}`；
    两者皆无 ⇒ `None`。`peek` 不可达/被拒同样返回 `None`（与旧面「非 200 一律 None」同规）。
    """
    h: dict[str, str] = {}
    if worker_id:
        h[WORKER_ID_HEADER] = worker_id
    if offline_ok:
        h[OFFLINE_CAP_HEADER] = OFFLINE_CAP_VALUE
    st, body = req(base, token, f"/jobs/peek?n={max(1, int(n))}", headers=h, timeout=timeout)
    if st != 200:
        return None
    halt = body.get("halt") is True
    jobs = body.get("jobs")
    jobs = jobs if isinstance(jobs, list) else []
    if not jobs:
        return {"halt": True} if halt else None
    cand = jobs[0] if isinstance(jobs[0], dict) else {}
    jid = str(cand.get("job_id") or "")
    if not jid:
        return {"halt": True} if halt else None
    cst, cbody = req(
        base,
        token,
        f"/jobs/{jid}/claim",
        method="POST",
        body={"mode": "exclusive", "worker_id": worker_id},
        headers=h,
        timeout=timeout,
    )
    if cst != 200:
        # claim 被拒（避让/熔断/竞负）——与旧面「没领到」同形。
        return {"halt": True} if halt else None
    return {
        "job_id": jid,
        "manifest": cbody.get("manifest"),
        "halt": bool(cbody.get("halt", halt)),
        "lease_token": cbody.get("lease_token") or "",
        "course": cbody.get("course") or cand.get("course") or "",
    }


def hub_next_http(base: str, token: str, **kw) -> tuple[int, dict]:
    """`_http(base, token, "/jobs/next")` 的 drop-in：`(200, 同形 dict)` 或 `(204, {})`。

    204 表示「无活」（旧面是 `{"job_id": null}`；调用方只需按 `job_id` 是否为空判定）。
    """
    got = hub_poll(base, token, **kw)
    if got is None:
        return 204, {}
    return 200, got
