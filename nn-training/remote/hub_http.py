"""remote/hub_http —— hub 客户端的 **HTTP 面**（S5 第五刀，2026-09-27）。

从 `remote/hub_client.py` 整块搬出（**逐字节不动**）。这里是「怎么把一次请求发到 hub、怎么分类
它的回答、怎么等一个 job 回传」的**唯一**实现：

* **传输薄壳**：`_request` —— 全模块**唯一**碰 `urlopen` 的地方（回环走 `remote.net_http` 绕代理）；
* **回传消费**：`probe_job_result`（状态码分类的唯一实现，hub 与节点两条链路共用）· `poll_job`
  （非阻塞探针）· `wait_job`（阻塞等待 + 退避 + 饥饿响亮）· `_wait_state_note`；
* **行政面**：`report_job_failure`（确定性失败回报）· `set_cloud_halt` / `hub_halted` /
  `clear_halt_on_startup`（云端停机达令）；
* **失败类型**：`HubClientError`（发布/等待/校验三环共用；旧家的 raise 通过 `hub_client` 的
  转发名指向**同一个对象**）。

**为什么单独成家**：它是传输面 —— 「把请求发出去、把回答读回来」，与「打包 / 磁盘 IPC 发布 /
校验落位」是两件事。分开之后：

* 注入点单一且显式：**`remote.hub_http._request`**（宿主函数的命名空间解析就在本模块）。
  旧家的 `hub_client._request` 是转发名；照它打补丁**不再**影响宿主函数，这是**刻意**的分档
  （见 `tests/test_hub_http_split.py` 的档位断言）——patch 打偏而测试全绿是本仓最贵的坑之一。
* 节点侧客户端（`remote/push_client`）与 `rl/bc_ingest` 都直接依赖本模块，而不再依赖 1688 行的
  `hub_client` 门面。

依赖面 = stdlib（`json` / `time` / `urllib.parse`）+ `common.protocol` + `remote.net_http`（延迟）。
**不** import `remote.hub_client`（无环）。

`remote/hub_client.py` 保留 `X as X` 门面：历史 `from remote.hub_client import …` 一行不改。
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import NamedTuple

from common.protocol import AUTH_HEADER, FAIL_BODY_MAX, JobFailedError


class HubClientError(RuntimeError):
    """hub 侧远程 PPO 失败（发布/等待/校验任一环）。"""

# ------------------------------------------------------------------ 等待（HTTP）


def _request(
    base_url: str,
    token: str,
    path: str,
    timeout: float = 30.0,
    data: bytes | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    from remote.net_http import urlopen as _urlopen

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={AUTH_HEADER: f"Bearer {token}", **(headers or {})},
        method=method,
    )
    try:
        # 回环（本机 hub）绕开环境代理——`no_proxy` 里的 `127.*` 通配 Python 不认，
        # 见 remote/net_http.py 模块头。
        with _urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def report_job_failure(
    base_url: str,
    token: str,
    jid: str,
    reason: str,
    *,
    kind: str = "",
    detail: str = "",
    worker: str = "",
    lease_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> bool:
    """回报**确定性**失败原因（`POST /jobs/{id}/fail`）——训练侧随即从
    `wait_job` 拿到 410 + 原因立刻停腿，不再等满超时。

    返回 True = hub 采纳（或已记录过）。尽力而为：回报本身不可达时返回 False，
    由超时兜底（与 release_job 同策略）——**永不让回报本身炸掉 worker 主循环**。

    只用于「这台机器干不了」的确定性失败（bun 缺失 / TS 运行时取不到 / argv 非法）。
    瞬时失败（网络/5xx）走 release 回池，**不**报这里——那会把可恢复的 job 钉死。
    重发同 job（同幂等键 → 同 job_id）时 publish_job 会清除失败标记。
    """
    if not base_url or not jid or not reason:
        return False
    body = json.dumps(
        {
            "reason": str(reason)[:2000],
            "kind": str(kind)[:200],
            "detail": str(detail)[:4000],
            "worker": str(worker)[:200],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    if len(body) > FAIL_BODY_MAX:  # 已按字段截断，兜底防御（hub 也会 400）
        return False
    try:
        st, resp = _request(
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
    if st == 200:
        log(f"job {jid} 失败原因已回报（hub 采纳）: {str(reason)[:160]}")
        return True
    log(f"job {jid} 失败回报被拒：HTTP {st}: {resp[:200].decode('utf-8', 'replace')}")
    return False


def set_cloud_halt(
    base_url: str,
    token: str,
    halt: bool,
    timeout: float = 15.0,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
    course: str = "",
) -> bool:
    """§386：向 hub 下发/解除云端停机达令（console 与 TrainingLoop 共用一端点）。

    返回 True = hub 已采纳。网络故障/非 200 → False（记录日志，**停机链路永不
    阻断训练**）——local/push 模式无 hub 时会带空 url 进来，直接短路 False。

    `course`（2026-09-18 单 hub 化）：一个 hub 服务所有并行课程，故达令必须**按课程**
    下发（`?course=`）——否则 A 课的门禁 ABORT 会把 B 课的云机一起停掉。空串 = 全课程
    （旧语义：单课程 hub / 没有课程上下文的调用方）。
    """
    if not base_url or not token:
        return False
    path = "/admin/workers/halt" if halt else "/admin/workers/resume"
    if course:
        path += "?course=" + urllib.parse.quote(course)
    try:
        st, _ = _request(base_url, token, path, timeout=timeout)
    except Exception as e:  # 网络层（tunnel 抖动等）——基础设施不可用，不阻断训练
        log(f"cloud {'halt' if halt else 'resume'} 下发失败（{type(e).__name__}: {e}）")
        return False
    if st != 200:
        log(f"cloud {path} → HTTP {st}（不阻断训练）")
        return False
    log(f"cloud {'halt' if halt else 'resume'} 已下发（hub 采纳）")
    return True


def hub_halted(base_url: str, token: str, timeout: float = 10.0, course: str = "") -> bool | None:
    """读 /admin/workers/status → True=停机中 / False=已清除 / None=未知。

    未配置 hub（local/push）或不可达/非 200/体裁不对 → None（呼叫方按未知处理，
    绝不把"问不到"当成"没停机"）。`course` = 只看那一门课（空串 = 全课程都停才 True）。
    """
    if not base_url or not token:
        return None
    path = "/admin/workers/status"
    if course:
        path += "?course=" + urllib.parse.quote(course)
    try:
        st, body = _request(base_url, token, path, timeout=timeout)
    except Exception:
        return None
    if st != 200:
        return None
    try:
        v = json.loads(body.decode("utf-8")).get("halt")
    except ValueError:
        return None
    return bool(v) if isinstance(v, bool) else None


def clear_halt_on_startup(
    base_url: str,
    token: str,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
    course: str = "",
    timeout: float = 10.0,
) -> bool:
    """TrainingLoop 启动即清空 hub 停机态（2026-09-12 it17 事故复盘）。

    上轮门判 REMEDIATE / 人工停机后未恢复 / worker 自杀残留的 halt 若带进新 run，
    首轮 PPO job 直接进无人区（训练机空等 30min 超时）。启动=需要算力=停机条件
    作废：先读后清，读回确认才算数。

    `timeout` 透传给 `hub_halted` / `set_cloud_halt`（生产缺省 10s；测试在不可达
    地址上拧到 0.05s——Windows 对关闭端口的 connect 也会空等数秒，门禁实测 6.2s）。

    返回 True = 已确认清除（或本无 halt、无 hub）；False = 仍停机/未知（只告警，
    **永不阻断启动**——PPO 等待期会再次表面化，控制台 PPO 排队告警是第二道网）。
    """
    if not base_url or not token:
        return True  # local/push 无 hub——无事可做即成功
    cur = hub_halted(base_url, token, timeout=timeout, course=course)
    if cur is False:
        log("[run_rl] hub 停机态：启动时检查，本已清除，无事可做")
        return True
    if cur is True:
        log("[run_rl] hub 停机态：检测到遗留 halt（上轮门判/人工停机残留）——启动即清空")
    else:
        log("[run_rl] hub 停机态未知（不可达？）——仍尝试 resume（幂等），失败不阻断启动")
    if not set_cloud_halt(base_url, token, False, timeout=timeout, log=log, course=course):
        log("[run_rl] WARN: hub resume 下发失败——首轮 PPO 可能排队超时，盯控制台 PPO 告警")
        return False
    if hub_halted(base_url, token, timeout=timeout, course=course) is False:
        log("[run_rl] hub 停机态：已清除并回读确认")
        return True
    log("[run_rl] WARN: hub resume 已下发但回读仍为 halt——首轮 PPO 可能排队超时")
    return False


def _job_failed_from_body(jid: str, body: bytes) -> JobFailedError:
    """410/status=failed 的响应体 → JobFailedError（原因取 error/reason，详情取 fail_detail）。

    消息形如 `job <id> 失败: bun 未安装 … [kind=ProtocolError]`——外部（训练主循环的
    确定性失败分支）靠 `JobFailedError` 类型收兵，人靠这行字定位现场。损坏体不丢
    失败事实（退回通用文案）。
    """
    reason, kind, detail = "节点报告确定性失败（无原因文本）", "", ""
    try:
        loaded = json.loads(body.decode("utf-8"))
    except (ValueError, AttributeError, UnicodeDecodeError):
        loaded = None
    if isinstance(loaded, dict):
        raw = loaded.get("error") or loaded.get("reason")
        if isinstance(raw, str) and raw:
            reason = raw
        if isinstance(loaded.get("fail_kind"), str):
            kind = loaded["fail_kind"]
        if isinstance(loaded.get("fail_detail"), str):
            detail = loaded["fail_detail"]
    suffix = f" [kind={kind}]" if kind else ""
    return JobFailedError(f"job {jid} 失败: {reason}{suffix}", kind=kind, detail=detail)


#: 等待期「饥饿响亮」的默认节流（秒）：每 N 秒把「在等什么」写一行（见 `wait_job`）。
#: 理由（plan/accident.plan.md §3）：零 worker 时训练侧原先只有超时一条路——job 被反复
#: 派发/过期 3.5 小时而日志安静如常。等着可以，**安静地等**不行。
WAIT_REPORT_SEC = 300.0


#: 非阻塞探针的三态（`probe_job_result` 的 `state`）。
PROBE_READY = "ready"  # 结果已落 hub，可取
PROBE_PENDING = "pending"  # 还没回（正常排队）——**不等于失败**，过一会儿再问
PROBE_TRANSIENT = "transient"  # 网络抖动 / 5xx——可重试的「没答」

#: 非阻塞探针的单次请求超时（秒）。**单进程调度器会同步调它**（问一句就走），所以
#: 必须短：探针只可能给出「就绪 / 还没好 / 瞬时错」，把「瞬时错」误当「还没好」的代价
#: 只是再等一轮，永远不会误判成成功——所以宁可短，也不让一次网络卡顿堵住整条调度链。
PROBE_TIMEOUT_SEC = 10.0


class ProbeResult(NamedTuple):
    """一次非阻塞探测的结论。

    `state` ∈ {ready, pending, transient}；`result` 仅在 ready 时非空；`detail` 是
    transient 的诊断（异常类型或 HTTP 状态），供调用方的日志/退避用。
    终局失败（410 / status=failed）**不走三态**——它抛 `JobFailedError`，与阻塞等待的
    收兵口径一致（「还没好」与「永远好不了」必须分开）。
    """

    state: str
    result: dict | None = None
    detail: str = ""


def probe_job_result(
    base_url: str,
    token: str,
    jid: str,
    *,
    path: str = "/jobs/{jid}/result",
    timeout: float = PROBE_TIMEOUT_SEC,
) -> ProbeResult:
    """**一次**请求探测 job 结果——状态码分类的**唯一**实现（hub / 节点两条链路共用）。

    节点侧（worker_server）的结果端点路径不同（`/job/{jid}/result`），故 `path` 可注入；
    除路径外两条链路的语义**必须一致**：否则「同一份 job 在 hub 上判 pending、在节点上
    判 transient」这类分叉会各自演化（历史上 hub 与 push 两段轮询就是这么漂开的）。

    网络异常/5xx = transient；202/404 = pending；410 = 终局失败（抛）；其余 = 协议错误（抛）。
    """
    try:
        # `timeout` 走**关键字**：测试里的假 `_request` 常把它声明成 keyword-only
        # （位置传参会 TypeError，症状是「探针一调就炸」而不是「问了没答」）。
        status, body = _request(base_url, token, path.format(jid=jid), timeout=timeout)
    except Exception as e:  # 瞬时网络错误：与 404 一样是「没答」，不是失败
        return ProbeResult(PROBE_TRANSIENT, None, f"{type(e).__name__}")
    if status == 200:
        loaded = json.loads(body.decode("utf-8"))
        if isinstance(loaded, dict):
            return ProbeResult(PROBE_READY, loaded)
        raise HubClientError(f"probe_job_result: job {jid} 结果非对象: {type(loaded).__name__}")
    if status in (202, 404):
        return ProbeResult(PROBE_PENDING)
    if status == 410:
        # 终局：节点已报**确定性失败**（`POST /jobs/{id}/fail`），原因在体内。
        raise _job_failed_from_body(jid, body)
    if status >= 500:
        return ProbeResult(PROBE_TRANSIENT, None, f"HTTP {status}")
    raise HubClientError(
        f"probe_job_result: HTTP {status}: {body[:200].decode('utf-8', 'replace')}"
    )


def poll_job(
    base_url: str, token: str, jid: str, *, timeout: float = PROBE_TIMEOUT_SEC
) -> dict | None:
    """非阻塞探一次 hub 结果：就绪 → 结果；未就绪 / 瞬时错 → None（终局失败照抛）。

    单进程调度器的让位判据就靠它（R2c-3）：**问一句就走**——不等、不睡、不轮询。
    """
    return probe_job_result(base_url, token, jid, timeout=timeout).result


def _wait_state_note(base_url: str, token: str, jid: str, waited: float) -> str:
    """等待期的一行状态（best-effort，**绝不**把观测失败变成训练失败）。

    区分两种「还没回」——它们对操作员是完全不同的两件事：
      * `pending` = **没有任何 worker 认领**（饥饿）⇒ 要算就去控制台起 worker；
      * `leased` = 已被认领、云机在跑（带租约剩余与上次心跳）⇒ 只能等。

    在线 worker 数来自 `/admin/queue`（hub 已有面，不新造端点）；取不到就只说状态。
    """
    state = "?"
    extra = ""
    try:
        st, body = _request(base_url, token, f"/jobs/{jid}/status", timeout=15.0)
        if st == 200:
            info = json.loads(body.decode("utf-8"))
            if isinstance(info, dict):
                state = str(info.get("state") or "?")
                if state == "leased":
                    left = info.get("lease_expires_in")
                    hb = info.get("last_heartbeat_ago")
                    extra = f"; 租约剩 {left}s, 上次心跳 {hb}s 前" if left is not None else ""
    except Exception:  # 观测失败不影响等待语义（本行只为日志服务）
        pass
    if state == "leased":
        return f"job {jid} 执行中（state=leased{extra}）（已等 {waited:.0f}s）"
    if state == "frozen":
        # §4.1 熔断：不该在这里长等——`/result` 会给 410 让探针当场抛 JobFailedError。
        # 这一行只是「探测到 410 的那一拍」之前的过渡（例如冻结刚发生、本拍问的是 /status）。
        return (
            f"job {jid} **已被 hub 熔断冻结**（state=frozen，已等 {waited:.0f}s）"
            "——需人工确认后解冻重发（POST /admin/unfreeze）"
        )
    workers = "?"
    try:
        st2, body2 = _request(base_url, token, "/admin/queue", timeout=15.0)
        if st2 == 200:
            q = json.loads(body2.decode("utf-8"))
            if isinstance(q, dict) and q.get("active_workers") is not None:
                workers = str(q["active_workers"])
    except Exception:  # 同上：拿不到 worker 数就只说状态
        pass
    return (
        f"job {jid} **等待认领中**（state={state}，已等 {waited:.0f}s；hub 在线 worker = {workers}）"
        "——要算就去控制台起 worker（本机/云机同一认领协议），不要就停腿"
    )


def wait_job(
    base_url: str,
    token: str,
    jid: str,
    *,
    timeout_sec: float = 25 * 60,
    poll_sec: float = 5.0,
    poll_max_sec: float = 60.0,
    report_every_sec: float = WAIT_REPORT_SEC,
    now=time.time,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> dict:
    """阻塞等待 job 完成（worker 已 POST 结果）→ 返回结果 dict。超时抛 HubClientError。

    R9（2026-09-10 c6 it50 事故，plan/feasibility-map.md §12）：**轮询退避**。
    此前网络错误/5xx 一律固定 `poll_sec` 重试——隧道抖动期间这是固定频率猛敲一个
    已经不可达的边缘（cloudflared `region1.v2.argotunnel.com i/o timeout` 持续
    6h），既救不回 job 也放大噪声。连续错误按 2 的幂退避（`poll_max_sec` 封顶），
    一次成功即复位。404（job 还没回）是**正常等待**，不走退避。

    单次探测与状态码分类在 `probe_job_result`（与 `poll_job` 同一实现）——本函数只负责
    「退避策略 + 超时收尾」，这正是它与非阻塞版该有的唯一区别。

    饥饿响亮（2026-09-21，plan/accident.plan.md §3）：排队期每 `report_every_sec` 秒写一行
    「在等什么」（等待认领 vs 执行中，见 `_wait_state_note`）——零 worker 时应当**响亮**地
    等，而不是让操作员从超时反推（C 腿事故：无人认领空转 3.5 小时，日志只有超时）。
    """
    t0 = now()
    deadline = t0 + timeout_sec
    err_streak = 0
    next_report = t0 + report_every_sec if report_every_sec > 0 else float("inf")
    while now() < deadline:
        probe = probe_job_result(base_url, token, jid, timeout=30.0)
        if probe.state == PROBE_READY:
            assert probe.result is not None  # ready 必带结果（probe_job_result 保证）
            return probe.result
        if probe.state == PROBE_PENDING:
            err_streak = 0  # 还没回 = 正常排队，复位退避
            if now() >= next_report:
                # 节流打点：按墙钟下一次报告点推进（不按「检查次数」——poll_sec 会被
                # 调用方调成 0.01 的测试值，次数节流会当场把日志刷爆）。
                waited = now() - t0
                next_report = now() + report_every_sec
                log(_wait_state_note(base_url, token, jid, waited))
            time.sleep(poll_sec)
            continue
        # 瞬时错误（隧道抖动/DNS/5xx）——与 404 同等续等，但按 2 的幂退避；
        # 2026-09-05：此前单次错误直接抛 HubClientError 会废掉整轮迭代
        # （loop 连击 retry），对 24/7 隧道运营是可靠性缺陷。
        err_streak += 1
        backoff = min(poll_sec * (2 ** (err_streak - 1)), poll_max_sec)
        log(
            f"wait_job: job {jid} 轮询瞬时错误 ({probe.detail}) "
            f"— 连续第 {err_streak} 次，{backoff:.0f}s 后退避重试"
        )
        time.sleep(backoff)
        continue
    # H3：超时前二次确认——leased（云仍在跑）→ 延长等待；done → 直接取结果；
    # frozen（§4.1 熔断）→ 立刻带原因收兵。
    s_status, s_body = _request(base_url, token, f"/jobs/{jid}/status", timeout=15.0)
    if s_status == 200:
        s_info = json.loads(s_body.decode("utf-8"))
        state = s_info.get("state") if isinstance(s_info, dict) else None
        if state == "done":
            st2, body2 = _request(base_url, token, f"/jobs/{jid}/result", timeout=15.0)
            if st2 == 200:
                loaded = json.loads(body2.decode("utf-8"))
                if isinstance(loaded, dict):
                    return loaded
        elif state == "failed":
            # 终局（2026-09-17）：收尾确认时也要认失败——否则又多等一个超时窗口。
            st2, body2 = _request(base_url, token, f"/jobs/{jid}/result", timeout=15.0)
            raise _job_failed_from_body(jid, body2 if st2 == 410 else s_body)
        elif state == "frozen":
            # ★ 毒包熔断（§4.1）：不再「再给一个预算」——冻结就是「没人会来跑它了」，
            # 立刻带着原因收兵（与 410 报的同一件事；两条路一致才不会各自漂）。
            info = s_info if isinstance(s_info, dict) else {}
            raise JobFailedError(
                f"job {jid} 已被 hub 熔断冻结：连续 {int(info.get('reclaims', 0) or 0)} 次"
                "认领后零回传（疑似内容决定性毒包）——人工确认后解冻重发",
                kind="PoisonFrozen",
                detail=str(info.get("last_worker", "")),
            )
        elif state == "leased":
            # 云仍在跑 ⇒ 再给一个完整预算（H3 原语义）。report/now 原样下传：
            # 延长等待期仍要按同一节流继续「在等什么」的报告。
            log(f"wait_job: job {jid} 仍在 leased（云 PPO 执行中）——再等 {timeout_sec}s")
            return wait_job(
                base_url,
                token,
                jid,
                timeout_sec=timeout_sec,
                poll_sec=poll_sec,
                poll_max_sec=poll_max_sec,
                report_every_sec=report_every_sec,
                now=now,
                log=log,
            )
    raise HubClientError(f"wait_job: job {jid} 超时（>{timeout_sec}s）未完成")
