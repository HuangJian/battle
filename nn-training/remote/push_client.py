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
from collections.abc import Callable
from dataclasses import dataclass

#: 节点侧结果端点（hub 侧是 `/jobs/{jid}/result`，只差一个 s）——探针靠注入它复用同一份
#: 状态码分类实现（见 `hub_http.probe_job_result`）。
NODE_RESULT_PATH = "/job/{jid}/result"

# `_request` 仍是本模块其它函数（缓存查询、submit）的实现细节；结果探测自身走
# `probe_job_result`（状态码分类的唯一实现，见 hub_http——**HTTP 面的所有者**）。
from common.logutil import log_line
from common.ports import PortPair
from common.protocol import (
    BLOB_INIT,
    NET_ERRORS,
    WIRE_JOB_CONTENT_TYPE,
    ProtocolError,
    RetryableError,
    is_content_sha,
    pack_job_v2,
)
from remote.hub_http import PROBE_READY, PROBE_TRANSIENT, _request, probe_job_result

#: 出站线路端口（P0-8，2026-11-28）：本模块的 `_request` 来自 `remote.hub_http`，
#: 但解析在本模块命名空间 ⇒ 测试打的是**这里的** `WIRE`（与 `hub_http.WIRE` 互不影响）。
WIRE: PortPair = PortPair(request=_request)

#: 探针的「问不到」面：网络/传输类（`NET_ERRORS`）**加** `ValueError`（200 但 body 不是
#: JSON）。必须模块级具名 tuple —— `except (*NET_ERRORS, ValueError)` 的星号形式 mypy 不收。
PROBE_ERRORS: tuple[type[BaseException], ...] = (*NET_ERRORS, ValueError)


def _default_log(msg: str) -> None:
    log_line("push", msg)


#: 「这台节点上次探到有这个 (kind, sha)」的记忆上限。
_CACHE_HITS_CAP = 512


class ShaCacheHits:
    """**只记命中**的探针记忆（2026-10-30）：探针失败时沿用上一次的结论（P0-5，2026-11-21）。

    为什么要有这份记忆：慢链路上 `/code-sha` 15s 超时是常态，把失败当「没缓存」会变成正反馈
    （超时 ⇒ 判 miss ⇒ 多传 2.7MB ⇒ 更慢 ⇒ 更必然超时，真机 it45–49 实测）。所以探针**问不到**
    时沿用「上次探到命中」，从没命中过才按 miss（保守上传）。

    `record` / `forget` 是仅有的两个写入口，**都必须能翻面**：
    * 探针明确回 miss ⇒ `forget`（明确结论永远覆盖记忆，不粘住）；
    * 节点 428 说缺 ⇒ `forget`（否则下一次推送又会跳过补传，428 来回打转）。

    上限是**整片清空**而不是 LRU：这份账只在探针失败时被读，清空的代价是下一轮保守上传一次；
    混入淘汰策略只会让「哪条记得住」变成需要解释的东西。
    """

    def __init__(self, cap: int = _CACHE_HITS_CAP) -> None:
        self._hits: dict[tuple[str, str, str], bool] = {}
        self._cap = max(1, int(cap))

    @staticmethod
    def _key(base_url: str, kind: str, sha: str) -> tuple[str, str, str]:
        return (str(base_url or ""), str(kind or ""), str(sha or ""))

    def record(self, base_url: str, kind: str, sha: str) -> None:
        """记下「这台节点有这份 sha」（探针刚答了 200 + cached）。"""
        if len(self._hits) > self._cap:
            self._hits.clear()
        self._hits[self._key(base_url, kind, sha)] = True

    def known(self, base_url: str, kind: str, sha: str) -> bool:
        """上次是否探到命中（探针问不到时的兜底答案）。"""
        return bool(self._hits.get(self._key(base_url, kind, sha)))

    def forget(self, base_url: str, kind: str, sha: str) -> None:
        """翻面（明确 miss / 节点 428 说缺）——幂等。"""
        self._hits.pop(self._key(base_url, kind, sha), None)

    def __len__(self) -> int:
        return len(self._hits)


#: 本进程那份记忆（生产写者是 `_cached_or_last_hit` 与 `forget_cache_hit`）。
_HITS = ShaCacheHits()


def cache_hits() -> ShaCacheHits:
    """默认记忆（观测/用例读面；替换公开名字即可接管，不必碰私有实例）。"""
    return _HITS


def forget_cache_hit(base_url: str, kind: str, sha: str) -> None:
    """忘掉「这台节点有这个 sha」的记忆。

    **节点 428 说缺之后必须调**（`submit_job` 的三条 428 分支各调一次）：记忆不翻面的话，
    下一次推送又会认为缓存命中而跳过补传，428 来回打转。
    """
    cache_hits().forget(base_url, kind, sha)


def _probe_sha(base_url: str, token: str, path: str, timeout: float) -> bool | None:
    """一次内容寻址探针：`True/False` = 节点明说了；`None` = **问不到**（不是「没有」）。

    `None` 的语义是「链路/服务端暂时答不了」，不是「什么都算」：**200 但 body 不是 JSON**
    是节点契约变了（`/code-sha` 的回执形状），那必须当场炸出来 —— 否则调用方把它读成
    「问不到 ⇒ 保守上传」，于是每轮都白传 `code.zip`，而真因永不出现（与
    `common/distribution` 的探针、`remote/hub_http` 的薄壳同一条判据）。
    """
    try:
        status, body = WIRE.request(base_url, token, path, timeout=timeout)
    except PROBE_ERRORS:
        return None
    if status != 200:
        return None
    return bool(json.loads(body.decode("utf-8")).get("cached"))


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
    hits = cache_hits()
    hit = _probe_sha(base_url, token, path, timeout)
    if hit is None:
        return hits.known(base_url, kind, sha)
    if hit:
        hits.record(base_url, kind, sha)
    else:
        hits.forget(base_url, kind, sha)
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


@dataclass(frozen=True, kw_only=True)
class JobUpload:
    """一次 `POST /job` 要发的**东西**与它的传输旋钮（P0-3：8 个形参 → 一个对象）。

    为什么 `base_url`/`token` 不进来：它们是「发给谁」，与 `cancel_job` / `wait_result` /
    `poll_result` 同一对参数（同一条腿的四处端点用同一个 `(url, token)`），并进对象反而
    要拆两次。

    为什么其余要收：旧签名里 `manifest` / `payload_zip` / `code_zip` 是三个相邻的
    「看起来都像 body」的位置参数，而 `blobs` / `ts_code_zip` 是两类按 sha 寻址的可选段 ——
    传错一格不会被任何类型拦住，后果是节点解不开包（且只在云端才看得到）。

    `code_zip=None` **不等于**「没带 code」：它表示「本次不带」，节点缓存未命中时本函数
    会当场 `RetryableError`（见 `need_code` 分支），不会发出一个注定失败的包。
    """

    manifest: dict
    payload_zip: bytes
    #: 整库 code.zip（内容寻址；命中节点缓存时不上传）。`None` = 本次不带。
    code_zip: bytes | None
    #: 内容寻址 blob（opt / ref / init raw）；只传节点未命中的那几项。
    blobs: dict | None = None
    #: M3：TS 运行时 zip（`kind=iter` 才非 None；同样判节点缓存）。
    ts_code_zip: bytes | None = None
    #: 本节点冒烟档：让节点把回执 `echo` 回来（手动验证用）。
    echo: bool = False
    timeout: float = 600.0
    attempts: int = 3
    log: Callable[[str], None] = _default_log


@dataclass
class _UploadPlan:
    """本轮「带什么上路」：三次缓存探针的结论 + 要补传的 blob 字节。

    `need_code` / `need_ts` 与 `blob_needs` 会在重试循环里被节点回执（428）翻面 —— 所以是**可变**
    对象，且只活在这一轮（不越过 `submit_job` 的边界）。
    """

    need_code: bool
    need_ts: bool
    blob_src: dict[str, bytes]
    blob_needs: dict[str, bytes]
    ts_sha: str


def _plan_uploads(
    base_url: str,
    token: str,
    manifest: dict,
    code_zip: bytes | None,
    blobs: dict[str, bytes] | None,
    ts_code_zip: bytes | None,
) -> _UploadPlan:
    """开跑前的三次缓存探针 + blob 需求（M2 B3 / M3）：本轮带什么，只在这里决定。"""
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
    return _UploadPlan(
        need_code=need_code,
        need_ts=need_ts,
        blob_src=blob_src,
        blob_needs=blob_needs,
        ts_sha=_ts_sha,
    )


def _body_for(
    manifest: dict,
    payload_zip: bytes,
    code_zip: bytes | None,
    ts_code_zip: bytes | None,
    plan: _UploadPlan,
    use_v2: bool,
) -> tuple[bytes, str, bool, bool]:
    """这一拍要 POST 的字节与 Content-Type（v2 裸二进制段；JSON 体是永远在场的退路）。"""
    need_code, need_ts = plan.need_code, plan.need_ts
    blob_needs = plan.blob_needs
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
    return body_bytes, ctype, code_sent, ts_sent


def _handle_428(
    base_url: str, resp: bytes, plan: _UploadPlan, manifest: dict
) -> str:
    """节点说缺什么，下一轮就带什么（并把缓存记忆翻面）；返回可读的失败面。"""
    need_code, need_ts = plan.need_code, plan.need_ts
    blob_needs = plan.blob_needs
    blob_src = plan.blob_src
    _ts_sha = plan.ts_sha
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
    plan.need_code, plan.need_ts = need_code, need_ts
    return last


def submit_job(base_url: str, token: str, upload: JobUpload) -> dict:
    """POST /job 上传 manifest + payload（+ 按需 code / TS 运行时）。瞬时失败退避重试；
    409 busy / 428 code-missing / 428 ts-code-missing 亦按可重试处理（hub 侧换节点或
    补传后重试）。

    M0 统一计量：成功时返回本轮实测传输账（body_bytes / payload_bytes /
    code_bytes / ts_code_bytes / upload_sec / attempts）——训练主循环把它写进
    iteration 事件的 `wire` 子字典；旧调用方忽略返回值，行为不变。

    M3：kind=iter 的 TS 运行时同 code.zip 同规按 sha 内容寻址，命中就不传
    （manifest 无 ts_code_sha256 的 job 恒不涉及）。
    """
    # 旧签名 8 参 → 一个 `upload` 对象（P0-3）；下面一次展开，函数体逐字不动。
    manifest, payload_zip = upload.manifest, upload.payload_zip
    code_zip, blobs, ts_code_zip = upload.code_zip, upload.blobs, upload.ts_code_zip
    echo, timeout, attempts = upload.echo, upload.timeout, upload.attempts
    log = upload.log
    plan = _plan_uploads(base_url, token, manifest, code_zip, blobs, ts_code_zip)
    blob_needs = plan.blob_needs
    last: str = ""
    t0 = time.time()
    use_v2 = True
    for attempt in range(1, attempts + 1):
        body_bytes, ctype, code_sent, ts_sent = _body_for(
            manifest, payload_zip, code_zip, ts_code_zip, plan, use_v2
        )
        try:
            status, resp = WIRE.request(
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
                f"job {manifest['job_id']} 已推送到 {base_url}（code 上传={'是' if plan.need_code else '否，缓存命中'}）"
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
            last = _handle_428(base_url, resp, plan, manifest)
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
        status, _body = WIRE.request(
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
