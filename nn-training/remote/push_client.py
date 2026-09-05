"""remote/push_client.py — HUB 推模式的客户端（训练主循环侧）。

向 GPU 侧 worker_server（remote/worker_server.py，经 cloudflared 隧道暴露）推送
PPO job 并轮询结果。失败分类与 pull 链路同规：4xx = 确定性拒绝（ProtocolError），
网络异常/5xx/428 = 可重试（RetryableError）。code.zip 按 sha 内容寻址按需上传——
节点缓存已命中就不重复传（2026-09-05，DECISIONS §340 补充 4）。
"""

from __future__ import annotations

import base64
import json
import time

from remote.hub_client import _request
from remote.protocol import ProtocolError, RetryableError


def _default_log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [push] {msg}", flush=True)


def code_cached_on_node(base_url: str, token: str, code_sha256: str, timeout: float = 15.0) -> bool:
    """节点 code 缓存是否已含 sha（缓存未知/查询失败 → False 保守上传）。"""
    try:
        status, body = _request(base_url, token, f"/code-sha?sha={code_sha256}", timeout=timeout)
        if status == 200:
            return bool(json.loads(body.decode("utf-8")).get("cached"))
    except Exception:
        pass
    return False


def submit_job(
    base_url: str,
    token: str,
    manifest: dict,
    payload_zip: bytes,
    code_zip: bytes | None,
    *,
    echo: bool = False,
    timeout: float = 600.0,
    attempts: int = 3,
    log=_default_log,
) -> None:
    """POST /job 上传 manifest + payload（+ 按需 code）。瞬时失败退避重试；
    409 busy / 428 code-missing 亦按可重试处理（hub 侧换节点或补传后重试）。"""
    need_code = not code_cached_on_node(base_url, token, manifest["code_sha256"])
    if need_code and code_zip is None:
        raise RetryableError("节点无 code 缓存且本次未携带 code.zip")
    last: str = ""
    for attempt in range(1, attempts + 1):
        body_obj: dict = {"manifest": manifest, "payload_b64": base64.b64encode(payload_zip).decode("ascii")}
        if need_code and code_zip is not None:
            body_obj["code_b64"] = base64.b64encode(code_zip).decode("ascii")
        try:
            status, resp = _request(
                base_url,
                token,
                "/job",
                timeout=timeout,
                data=json.dumps(body_obj, ensure_ascii=False).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json", **({"X-Smoke-Echo": "1"} if echo else {})},
            )
        except Exception as e:
            status, resp = None, repr(e).encode()
        if status in (200, 202):
            log(f"job {manifest['job_id']} 已推送到 {base_url}（code 上传={'是' if need_code else '否，缓存命中'}）")
            return
        if status == 428:
            need_code = True  # 节点缓存未命中：下次重试带 code
            last = "428 code-missing"
        elif status == 409:
            last = "409 busy"
        elif status is not None and 400 <= status < 500:
            raise ProtocolError(f"job POST rejected: HTTP {status}: {resp[:300].decode('utf-8', 'replace')}")
        else:
            last = f"HTTP {status}" if status is not None else repr(resp.decode("utf-8", "replace")[:120])
        if attempt < attempts:
            backoff = min(2 ** attempt, 8)
            log(f"job POST 瞬时失败({last}) — {backoff}s 后第 {attempt + 1}/{attempts} 次重试")
            time.sleep(backoff)
    raise RetryableError(f"job POST 重试 {attempts} 次仍失败: {last}")


def wait_result(
    base_url: str,
    token: str,
    jid: str,
    *,
    timeout_sec: float = 1800.0,
    poll_sec: float = 5.0,
    log=_default_log,
) -> dict:
    """轮询 /job/{id}/result 直到 200（幂等读）——瞬时网络/5xx 容忍至预算。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            status, body = _request(base_url, token, f"/job/{jid}/result", timeout=30.0)
        except Exception as e:
            log(f"wait_result: job {jid} 轮询网络错误 ({type(e).__name__}) —— 重试")
            time.sleep(poll_sec)
            continue
        if status == 200:
            loaded = json.loads(body.decode("utf-8"))
            if isinstance(loaded, dict):
                return loaded
            raise ProtocolError(f"wait_result: job {jid} 结果非对象")
        if status in (202, 404):
            time.sleep(poll_sec)
            continue
        if status >= 500:
            log(f"wait_result: job {jid} HTTP {status}（瞬时错误）—— 重试")
            time.sleep(poll_sec)
            continue
        raise ProtocolError(f"wait_result: HTTP {status}: {body[:200].decode('utf-8', 'replace')}")
    raise RetryableError(f"wait_result: job {jid} 超时（>{timeout_sec}s）未完成")
