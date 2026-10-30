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

#: 节点侧结果端点（hub 侧是 `/jobs/{jid}/result`，只差一个 s）——探针靠注入它复用同一份
#: 状态码分类实现（见 `hub_http.probe_job_result`）。
NODE_RESULT_PATH = "/job/{jid}/result"

# `_request` 仍是本模块其它函数（缓存查询、submit）的实现细节；结果探测自身走
# `probe_job_result`（状态码分类的唯一实现，见 hub_http——**HTTP 面的所有者**）。
from common.protocol import (
    BLOB_INIT,
    WIRE_JOB_CONTENT_TYPE,
    ProtocolError,
    RetryableError,
    is_content_sha,
    pack_job_v2,
)
from remote.hub_http import PROBE_READY, PROBE_TRANSIENT, _request, probe_job_result


def _default_log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [push] {msg}", flush=True)


#: (base_url, kind, sha) -> True 的**只记命中**记忆（2026-10-30）：探针失败时沿用它。
#: 用 dict 而非 set：`cap` 到了整片清掉（这份记账永不无界增长，且命中最坏只是重传一次）。
_CACHE_HITS: dict[tuple[str, str, str], bool] = {}
_CACHE_HITS_CAP = 512


def forget_cache_hit(base_url: str, kind: str, sha: str) -> None:
    """忘掉「这台节点有这个 sha」的记忆。

    **节点 428 说缺之后必须调**（`submit_job` 的三条 428 分支各调一次）：记忆不翻面的话，
    下一次推送又会认为缓存命中而跳过补传，428 来回打转。
    """
    _CACHE_HITS.pop((str(base_url or ""), str(kind or ""), str(sha or "")), None)


def _probe_sha(base_url: str, token: str, path: str, timeout: float) -> bool | None:
    """一次内容寻址探针：`True/False` = 节点明说了；`None` = **问不到**（不是「没有」）。"""
    try:
        status, body = _request(base_url, token, path, timeout=timeout)
        if status == 200:
            return bool(json.loads(body.decode("utf-8")).get("cached"))
    except Exception:
        pass
    return None


def _cached_or_last_hit(
    kind: str, base_url: str, token: str, sha: str, path: str, timeout: float
) -> bool:
    """探针 → 「要不要再传」；**失败沿用上次命中**，从没命中过才按 miss（保守上传）。

    为什么不能「失败 = 没缓存」（旧语义，★2026-10-30 真机）：慢链路上 `/code-sha` 15s 超时
    是常态，失败 ⇒ 判 miss ⇒ 多传 `code.zip` 1.69MB + `opt` 1MB ⇒ 链路更慢 ⇒ 下次更必然
    超时。z2-r5g2 it45–49 的 `up_bytes` 3.2MB→5.0MB、每轮 `code 上传=是` 全是这条正反馈
    （code sha `2f538c06…` 全程没变，纯粹是探针超时）。一次上传 45–215s，代价是分钟级。

    为什么这样安全：探针只回答「要不要再传一份」。真丢了缓存时 worker 会 428 拒收，
    `submit_job` 当场把字节补上并把记忆清掉（`forget_cache_hit`）—— 漏传退化成一次
    可重试的往返，而不是静默失败。`False`（明确的 miss）永远覆盖记忆，不会粘住。
    """
    key = (str(base_url or ""), str(kind or ""), str(sha or ""))
    hit = _probe_sha(base_url, token, path, timeout)
    if hit is None:
        return bool(_CACHE_HITS.get(key))
    if hit:
        if len(_CACHE_HITS) > _CACHE_HITS_CAP:
            _CACHE_HITS.clear()
        _CACHE_HITS[key] = True
    else:
        _CACHE_HITS.pop(key, None)
    return hit


def code_cached_on_node(base_url: str, token: str, code_sha256: str, timeout: float = 15.0) -> bool:
    """节点 code 缓存是否已含 sha（**探针失败**时沿用上次命中；从没命中过 ⇒ False 保守上传）。"""
    return _cached_or_last_hit(
        "code", base_url, token, code_sha256, f"/code-sha?sha={code_sha256}", timeout
    )


def blob_cached_on_node(base_url: str, token: str, sha: str, timeout: float = 15.0) -> bool:
    """M2 B3：节点 blob 缓存（opt/ref/init raw）是否已含 sha（失败语义同上）。"""
    if not sha:
        return True
    return _cached_or_last_hit("blob", base_url, token, sha, f"/blob-sha?sha={sha}", timeout)


def ts_code_cached_on_node(base_url: str, token: str, sha: str, timeout: float = 15.0) -> bool:
    """M3：节点 TS 运行时缓存（ts_code_cache）是否已含 sha（失败语义同上）。"""
    if not sha:
        return True
    return _cached_or_last_hit("ts", base_url, token, sha, f"/ts-code-sha?sha={sha}", timeout)


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
    # opt-blob-diet（2026-09-24，§4 / §3.2）：`init`（初始权重）同规——节点缓存未命中
    # 才随 body 上传。**必须先过 `is_content_sha`**：BC job 的 `init_weights_fp == "bc"`
    # 不是内容寻址的 sha（拿它问节点缓存 = 每轮都当 miss，评审 F2）。
    init_sha = str(manifest.get("init_weights_fp") or "")
    init_raw = blob_src.get(BLOB_INIT)
    if (
        is_content_sha(init_sha)
        and init_raw
        and not blob_cached_on_node(base_url, token, init_sha)
    ):
        blob_needs[BLOB_INIT] = init_raw
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
            # worker 的受理态：`accepted` = 立刻开算；`queued` = 进了它的接单队列
            # （hub 预推，2026-10-24）。旧 worker/旧形体读不到就当空串 = 旧语义。
            try:
                ack = json.loads(resp.decode("utf-8")) if resp else {}
            except (ValueError, UnicodeDecodeError):
                ack = {}
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
                #: 节点侧受理态（`accepted` / `queued`）；纯加法，旧调用方忽略。
                "status": str((ack or {}).get("status") or "") if isinstance(ack, dict) else "",
            }
        if status == 428:
            need_code = True  # 节点缓存未命中：下次重试带 code
            # 记忆必须翻面：否则下一轮探针"沿用命中"又会跳过补传（见 `_cached_or_last_hit`）。
            forget_cache_hit(base_url, "code", manifest.get("code_sha256", ""))
            # M2 B3：节点说缺 blob → 把本次携带的 blob 全补上（命中缓存的不必传）。
            if b"blob-missing" in resp:
                for name, raw in blob_src.items():
                    # opt-blob-diet（§4）：`init` 同规——节点说缺就得补传，否则下一次
                    # 重试仍然 428（而 `need_blobs` 已经点名了它）。
                    if name in ("opt", "ref", BLOB_INIT) and raw:
                        blob_needs[name] = raw
                for name in ("opt", "ref"):
                    forget_cache_hit(base_url, "blob", manifest.get(f"{name}_sha", ""))
                forget_cache_hit(base_url, "blob", manifest.get("init_weights_fp", ""))
                last = "428 blob-missing"
            elif b"ts-code-missing" in resp:
                # M3：节点没有 TS 运行时缓存 → 下次重试带上（调用方必须提供字节；
                # 拿不到就在下一轮循环里被 need_ts+None 的守卫响亮拒掉）。
                # **必须**把 need_ts 置真：走到这里的典型场景是「/ts-code-sha 探到缓存、
                # 但真正 POST 时缓存已不在」（并发清理/两课共享节点），此时 need_ts 本是
                # False ⇒ 不置真就会一直重发不带 ts 的体，白烧满重试预算后整轮失败。
                # 与上面 code-missing 的 `need_code = True` 同规。
                need_ts = True
                forget_cache_hit(base_url, "ts", _ts_sha)
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


def cancel_job(base_url: str, token: str, jid: str, *, timeout: float = 15.0) -> bool:
    """向节点推一帧 landed 取消（`POST /job/{id}/cancel`，幂等）。

    语义（R2-4b / §2.4）：可达 ⇒ True（Event 已置，节点侧在 epoch 边界停算）；不可达/
    非 200 ⇒ False —— **不算失败**：让它跑完，结果回传时按 409 丢弃（与「上传中/已传」
    那一行的口径一致）。所以本函数**不抛**，调用方也不必包 try。
    """
    try:
        status, _body = _request(
            base_url, token, f"/job/{jid}/cancel", timeout=timeout, method="POST"
        )
    except Exception:
        return False
    return status == 200


def poll_result(
    base_url: str,
    token: str,
    jid: str,
    *,
    timeout: float = 30.0,
) -> dict | None:
    """非阻塞探一次节点结果：就绪 → 结果；未就绪 / 瞬时错 → None（终局失败照抛）。

    单进程调度器的让位判据（R2c-3）：**问一句就走**。与 hub 侧的 `poll_job` 共用状态码
    分类实现（`path` 注入节点侧端点），两条链路不可能各判一套。
    """
    return probe_job_result(
        base_url, token, jid, path=NODE_RESULT_PATH, timeout=timeout
    ).result


def wait_result(
    base_url: str,
    token: str,
    jid: str,
    *,
    timeout_sec: float = 1800.0,
    poll_sec: float = 5.0,
    log=_default_log,
) -> dict:
    """轮询 /job/{id}/result 直到 200（幂等读）——瞬时网络/5xx 容忍至预算。

    单次探测与状态码分类在 `hub_http.probe_job_result`（`path` 注入节点侧端点）；
    本函数只负责「**固定** poll_sec 重试 + 超时收尾」——与 hub 侧的差别仅在退避策略
    （直推节点是弱链路热点，历史选择不放大频率），而这个差别不该再复制一份分类逻辑。
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        probe = probe_job_result(base_url, token, jid, path=NODE_RESULT_PATH, timeout=30.0)
        if probe.state == PROBE_READY:
            assert probe.result is not None  # ready 必带结果
            return probe.result
        if probe.state == PROBE_TRANSIENT:
            log(f"wait_result: job {jid} 轮询瞬时错误 ({probe.detail}) —— 重试")
        time.sleep(poll_sec)
    raise RetryableError(f"wait_result: job {jid} 超时（>{timeout_sec}s）未完成")
