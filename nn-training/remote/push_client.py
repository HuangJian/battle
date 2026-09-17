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

from remote.hub_client import _job_failed_from_body, _request
from remote.protocol import (
    WIRE_JOB_CONTENT_TYPE,
    ProtocolError,
    RetryableError,
    pack_job_v2,
)


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


def blob_cached_on_node(base_url: str, token: str, sha: str, timeout: float = 15.0) -> bool:
    """M2 B3：节点 blob 缓存（opt/ref raw）是否已含 sha（未知/失败 → False 保守上传）。"""
    if not sha:
        return True
    try:
        status, body = _request(base_url, token, f"/blob-sha?sha={sha}", timeout=timeout)
        if status == 200:
            return bool(json.loads(body.decode("utf-8")).get("cached"))
    except Exception:
        pass
    return False


def ts_code_cached_on_node(base_url: str, token: str, sha: str, timeout: float = 15.0) -> bool:
    """M3：节点 TS 运行时缓存（ts_code_cache）是否已含 sha（未知/失败 → False 保守上传）。"""
    if not sha:
        return True
    try:
        status, body = _request(base_url, token, f"/ts-code-sha?sha={sha}", timeout=timeout)
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
    blobs: dict | None = None,
    ts_code_zip: bytes | None = None,
    echo: bool = False,
    timeout: float = 600.0,
    attempts: int = 3,
    log=_default_log,
) -> dict:
    """POST /job 上传 manifest + payload（+ 按需 code / TS 运行时）。瞬时失败退避重试；
    409 busy / 428 code-missing / 428 ts-code-missing 亦按可重试处理（hub 侧换节点或
    补传后重试）。

    M0 统一计量：成功时返回本轮实测传输账（body_bytes / payload_bytes /
    code_bytes / ts_code_bytes / upload_sec / attempts）——训练主循环把它写进
    iteration 事件的 `wire` 子字典；旧调用方忽略返回值，行为不变。

    M3：kind=iter 的 TS 运行时同 code.zip 同规按 sha 内容寻址，命中就不传
    （manifest 无 ts_code_sha256 的 job 恒不涉及）。
    """
    need_code = not code_cached_on_node(base_url, token, manifest["code_sha256"])
    if need_code and code_zip is None:
        raise RetryableError("节点无 code 缓存且本次未携带 code.zip")
    _ts_sha = str(manifest.get("ts_code_sha256", "") or "")
    need_ts = bool(_ts_sha) and not ts_code_cached_on_node(base_url, token, _ts_sha)
    if need_ts and ts_code_zip is None:
        raise RetryableError("节点无 ts_code 缓存且本次未携带 ts_code.zip（kind=iter 必备）")
    # M2 B3：opt/ref 内容寻址 blob —— 仅当节点缓存未命中时随 body 上传
    # （manifest 带 opt_sha/ref_sha 且调用方提供了 raw 字节）。
    blob_src = blobs or {}
    blob_needs: dict[str, bytes] = {}
    for name, sha in (("opt", manifest.get("opt_sha")), ("ref", manifest.get("ref_sha"))):
        s = str(sha or "")
        raw = blob_src.get(name)
        if s and raw and not blob_cached_on_node(base_url, token, s):
            blob_needs[name] = raw
    last: str = ""
    t0 = time.time()
    # M2 B5：/job 体默认走 v2（payload/code/blob 裸二进制段，省掉 base64 的 33%%）；
    # 旧 hub 进程只认 application/json，4xx 时退回 JSON 重发一次（同 result 路径）。
    use_v2 = True
    for attempt in range(1, attempts + 1):
        code_sent = need_code and code_zip is not None
        # JSON 形状始终构造 —— 既是 v2 的退路，也是 v2 打包的源。
        body_obj: dict = {
            "manifest": manifest,
            "payload_b64": base64.b64encode(payload_zip).decode("ascii"),
        }
        if code_sent:
            assert code_zip is not None  # 收窄：code_sent 已保证非 None
            body_obj["code_b64"] = base64.b64encode(code_zip).decode("ascii")
        ts_sent = need_ts and ts_code_zip is not None
        if ts_sent:
            assert ts_code_zip is not None  # 收窄：ts_sent 已保证非 None
            body_obj["ts_code_b64"] = base64.b64encode(ts_code_zip).decode("ascii")
        if blob_needs:
            body_obj["blobs"] = {
                k: base64.b64encode(v).decode("ascii") for k, v in blob_needs.items()
            }
        if use_v2:
            body_bytes = pack_job_v2(
                manifest,
                payload_zip,
                code_zip if code_sent else None,
                blob_needs,
                ts_code_zip if ts_sent else None,
            )
            ctype = WIRE_JOB_CONTENT_TYPE
        else:
            body_bytes = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        try:
            status, resp = _request(
                base_url,
                token,
                "/job",
                timeout=timeout,
                data=body_bytes,
                method="POST",
                headers={
                    "Content-Type": ctype,
                    **({"X-Smoke-Echo": "1"} if echo else {}),
                },
            )
        except Exception as e:
            status, resp = None, repr(e).encode()
        if status in (200, 202):
            log(
                f"job {manifest['job_id']} 已推送到 {base_url}（code 上传={'是' if need_code else '否，缓存命中'}）"
            )
            return {
                "body_bytes": len(body_bytes),
                "payload_bytes": len(payload_zip),
                "code_bytes": len(code_zip) if code_sent and code_zip is not None else 0,
                # M3：TS 运行时实际上传字节（0 = 缓存命中，本轮没走这条线）。
                "ts_code_bytes": len(ts_code_zip) if ts_sent and ts_code_zip is not None else 0,
                "blob_bytes": sum(len(v) for v in blob_needs.values()),
                "blobs_sent": sorted(blob_needs),
                "upload_sec": round(time.time() - t0, 3),
                "attempts": attempt,
            }
        if status == 428:
            need_code = True  # 节点缓存未命中：下次重试带 code
            # M2 B3：节点说缺 blob → 把本次携带的 blob 全补上（命中缓存的不必传）。
            if b"blob-missing" in resp:
                for name, raw in blob_src.items():
                    if name in ("opt", "ref") and raw:
                        blob_needs[name] = raw
                last = "428 blob-missing"
            elif b"ts-code-missing" in resp:
                # M3：节点没有 TS 运行时缓存 → 下次重试带上（调用方必须提供字节；
                # 拿不到就在下一轮循环里被 need_ts+None 的守卫响亮拒掉）。
                # **必须**把 need_ts 置真：走到这里的典型场景是「/ts-code-sha 探到缓存、
                # 但真正 POST 时缓存已不在」（并发清理/两课共享节点），此时 need_ts 本是
                # False ⇒ 不置真就会一直重发不带 ts 的体，白烧满重试预算后整轮失败。
                # 与上面 code-missing 的 `need_code = True` 同规。
                need_ts = True
                last = "428 ts-code-missing"
            else:
                last = "428 code-missing"
        elif status == 409:
            last = "409 busy"
        elif status is not None and 400 <= status < 500:
            if use_v2 and attempt < attempts:
                # 旧节点/hub 不认 v2 Content-Type 会 4xx —— 退回 JSON 重发一次。
                log(f"job POST 被拒（HTTP {status}）——退回 JSON 体重试（旧节点？）")
                use_v2 = False
                continue
            raise ProtocolError(
                f"job POST rejected: HTTP {status}: {resp[:300].decode('utf-8', 'replace')}"
            )
        else:
            last = (
                f"HTTP {status}"
                if status is not None
                else repr(resp.decode("utf-8", "replace")[:120])
            )
        if attempt < attempts:
            backoff = min(2**attempt, 8)
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
        if status == 410:
            # 410 = 节点已判定这个 job 在这台机器上**跑不成**（终局，原因在体内）。
            # 2026-09-17 前节点用 500 报失败 ⇒ 落进下面的 5xx 分支被当瞬时错误重试到
            # 预算耗尽（bun 装不上 = 等满 30 分钟）。现在立即带着原因失败。
            raise _job_failed_from_body(jid, body)
        if status >= 500:
            log(f"wait_result: job {jid} HTTP {status}（瞬时错误）—— 重试")
            time.sleep(poll_sec)
            continue
        raise ProtocolError(f"wait_result: HTTP {status}: {body[:200].decode('utf-8', 'replace')}")
    raise RetryableError(f"wait_result: job {jid} 超时（>{timeout_sec}s）未完成")
