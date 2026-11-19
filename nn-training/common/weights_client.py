"""common/weights_client.py — 节点**权重传输**（探针 / POST / 并行下发 / 409 自愈）。

2026-11-19 从 `common/distribution.py` 整簇搬出（B8 第十一刀）。判据与升级/重启簇、任务协议簇
同一条：消费者（`trainer/*`、`worker/*`）全在 L0 之上，且这一簇没有自己的出口。

为什么要独立：权重下发是**内容寻址的幂等推送**（sha 相同即 kept、页面级探针省掉整份重传、
409 是「可刷新条件」而非节点故障），与取任务的语义正交；同文件里还塞着配置与收工断连，
读者要在一千行里找「这次到底传没传」。

⚠ **打桩缝**：出站一律经 `distribution.distribution.node_request(...)`（属性查找）——用例打桩
`common.distribution.node_request` 仍能拦住这里的每一次 POST / 探针。

`PushHooks` 三个出口（log / on_alive / monotonic）的来历与「边分发边开采」语义见
`docs/nn/engineering.md` §220。
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.parse
from collections.abc import Callable
from concurrent.futures import as_completed
from dataclasses import dataclass

from common import distribution, runtime
from common.distribution import (
    NET_ERRORS,
    PING_ERRORS,
    DistError,
    course_name_of,
)
from common.weights_ledger import (
    forget_weights_node,
    note_weights_pushed,
)


def weights_cached_on_node(
    url: str, auth_key: str, sha: str, kind: str = "rollout", timeout: float = 15.0
) -> bool:
    """`GET /v1/weights?sha=` → 节点是否已缓存该 sha（未知/失败/空 sha → False 保守上传）。

    语义与 `push_client.code_cached_on_node` / `/blob-sha` 同族：**少传一次是省流量，
    错判不传是 409 停活**，所以任何不确定都往「传」那边掉。旧 agent 没有这个端点
    ⇒ 非 200 ⇒ False ⇒ 退化成改造前行为（多传一次，不影响正确性）。
    """
    if not sha:
        return False
    qs = urllib.parse.urlencode({"sha": sha, "kind": kind, "course": course_name_of()})
    try:
        status, body = distribution.node_request(
            url.rstrip("/") + f"/v1/weights?{qs}", auth_key, timeout, method="GET"
        )
        if status == 200:
            info = json.loads(body.decode("utf-8"))
            # 形状显式判（旧 agent / 中间代理返回数组或裸值时不许把 `AttributeError` 藏进宽捕）。
            return bool(info.get("cached")) if isinstance(info, dict) else False
    except PING_ERRORS:
        return False
    return False


def post_weights_cached(
    url: str,
    auth_key: str,
    iter_id: str,
    sha: str,
    weights_bytes: bytes,
    timeout: float = 120.0,
    kind: str = "rollout",
) -> str:
    """带预检的 `post_weights`：节点已持有同一 sha → 只查不发，返回 `'cached'`。

    为什么值得：POST 是「先收完整个体才知道已缓存」（返回 kept 时 ~0.5MB 已经传完）。
    多课程并行时每个评估轮×每个节点都在重传同一份权重，白传量按「课程数 × 节点数」
    放大，而权重按 sha 内容寻址、天然幂等——先问一句就能把这份白传全免掉。
    """
    if weights_cached_on_node(url, auth_key, sha, kind=kind):
        return "cached"
    return post_weights(url, auth_key, iter_id, sha, weights_bytes, timeout=timeout, kind=kind)


def probe_weights_cached(
    url: str,
    auth_key: str,
    sha: str,
    kind: str = "rollout",
    timeout: float = 3.0,
) -> bool | None:
    """GET /v1/weights/cached → True/False；探针不可用（旧 agent / 网络失败）→ None。

    语义：节点该 kind 桶内是否已持有此 sha。None 时调用方必须走完整 POST。
    """
    if not sha:
        return None
    try:
        status, body = distribution.node_request(
            url.rstrip("/") + "/v1/weights/cached",
            auth_key,
            timeout,
            headers={"X-Weights-Sha256": sha, "X-Kind": kind},
            method="GET",
        )
    except NET_ERRORS:
        return None
    if status != 200:
        return None
    try:
        info = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        return None
    if not isinstance(info, dict) or "cached" not in info:
        return None
    return bool(info.get("cached"))


def post_weights(
    url: str,
    auth_key: str,
    iter_id: str,
    sha: str,
    weights_bytes: bytes,
    timeout: float = 120.0,
    kind: str = "rollout",
    course: str | None = None,
) -> str:
    """POST /v1/weights → 'kept' | 'purged'；失败抛 DistError。

    kind（v3.7/M8）：'rollout'（per-tick RL 采样）/ 'intent'（意图权重桶——
    intent-exec 评估 + 意图 RL rollout 共用）。agent 按 x-kind 分桶缓存。

    course（v5 2026-09-18 多课程）：None = 用进程级身份（`COURSE_ENV`），空串 = 显式
    落「旧单课程桶」。agent 侧按 **(course, kind)** 分桶——课程之间不再互相驱逐
    （5 门课共用一个 `rollout` 桶时，64 桶被分摊，历史深度掉到 ~13 轮，慢节点回来
    取旧权重就是 409）。

    kept 短路径（2026-09-19，x20-rebirth it19 rollout 208s 复盘）：先探针
    GET /v1/weights/cached；sha 已在桶内 → 不传 body 直接 'kept'（补波/同 it
    多波的权重握手主成本）。探针 404/失败（旧 agent）→ 完整 POST，语义不变。
    """
    # 探针预算 5s（2026-11-11 收回 §148 的 15s）：当时抬到 15s 是想兜「云机忙时探针超时
    # ⇒ 退化成整份重传」，但真因是串行 connect 的**每地址** timeout（15s 实测变成 30.83s，
    # 节点明明已有这份权重）。竞速后单请求 ≈1s，5s 够用；探针超时的代价仍是后面那次全额
    # POST，所以别顺手改成 0/跳过。
    cached = probe_weights_cached(url, auth_key, sha, kind=kind, timeout=min(5.0, timeout))
    if cached is True:
        return "kept"
    course_name = course_name_of() if course is None else course
    headers = {
        "Content-Encoding": "gzip",
        "X-Iter-Id": iter_id,
        "X-Weights-Sha256": sha,
        "X-Kind": kind,
    }
    if course_name:
        headers["X-Course"] = course_name
    status, body = distribution.node_request(
        url.rstrip("/") + "/v1/weights",
        auth_key,
        timeout,
        data=gzip.compress(weights_bytes),
        headers=headers,
        method="POST",
    )
    if status not in (200, 204):
        raise DistError(status, body[:300].decode("utf-8", "replace"))
    try:
        info = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        info = {}
    return str(info.get("cache", "kept"))


def refresh_weights(
    node: dict,
    *,
    iter_id: str,
    wver: str,
    weights_bytes: bytes,
    timeout: float = 120.0,
    kind: str = "rollout",
    err: str = "",
    log=None,
) -> bool:
    """409「wver not cached here」的自愈路径：清 reuse 缓存 + 就地重发该权重。

    节点侧那份权重会**在客户端不知情的情况下消失**：per-kind 桶只保留 KEEP 份、
    所有客户端共享同一桶（别的训练作业 / 本机 eval 上传 / agent 重启都能挤掉），
    而进程内 `_WEIGHTS_PUSHED` 仍以为在（它只在 ping 失败/codeHash 不符时失效）
    ⇒ 任务持续 409 直到把节点**当故障熔断**（2026-09-19 x20-rebirth 09:48 a97：
    5 条 409 → circuit-broken，7 槽位整轮闲置，同一 wver 44s 前刚收过）。

    409 是**可刷新条件**而非节点故障：重发一次即可继续用同一节点。
    返回 True = 重发成功 ⇒ 调用方**不得**记节点失败 streak。
    """
    nid = str(node.get("id") or node.get("url") or "?")
    forget_weights_node(nid)  # 清脏缓存：下一次波次必须重新握手
    try:
        mode = post_weights(
            node["url"],
            str(node.get("key") if node.get("key") is not None else node.get("authKey", "")),
            iter_id,
            wver,
            weights_bytes,
            timeout=timeout,
            kind=kind,
        )
    except DistError as e:
        if log is not None:
            log(
                f"[dist] node {nid}: wver not cached（409: {err[:60]}）就地重发失败"
                f"（{e}）—— 仍未持有权重，本任务回队"
            )
        return False
    note_weights_pushed(wver, nid, kind=kind)
    if log is not None:
        log(
            f"[dist] node {nid}: wver not cached（409）—— 已就地重发权重（{mode}）"
            f"并清 reuse 缓存（可刷新条件，不记节点故障）"
        )
    return True


@dataclass(frozen=True)
class PushHooks:
    """`post_weights_parallel` 的三个可选出口（一个对象顶三根可选参数：§P0-3「>7 参数 = 缺对象」）。

    * `log`：每节点完成 / 阶段收尾各一行；
    * `on_alive`：该节点 POST 成功的瞬间回调（worker 线程里，须线程安全）；
    * `monotonic`：**本段计时的读数**（§P1-6 的 clock 半边）—— 注入它，那两行里的秒数就是
      确定的差值，而不是「日志里大概有个 0.xs」。
    """

    log: Callable[[str], None] | None = None
    on_alive: Callable[[dict], None] | None = None
    monotonic: Callable[[], float] = time.monotonic


def post_weights_parallel(
    nodes: list,
    iter_id: str,
    wver: str,
    weights_bytes: bytes,
    timeout: float,
    kind: str = "rollout",
    hooks: PushHooks | None = None,
) -> list:
    """并行 POST 权重到全部节点 → alive 节点列表（保持入参顺序）。

    失败节点记日志后排除（与串行版语义一致）。**边分发边开采**（2026-09-19）：
    `on_alive(nd)` 在**该节点 POST 成功的瞬间**于 worker 线程回调（须线程安全），
    调用方可立刻孵化该节点采样线程，无需等其它节点。返回值仍按入参顺序。
    日志在每节点完成时输出。pure_collect 锚点由调用方定义（用户 2026-09-19：
    权重开始分发 → 样本齐可交 PPO）。三个可选出口见 `PushHooks`。
    """
    if not nodes:
        return []
    hk = hooks or PushHooks()
    log = hk.log
    on_alive = hk.on_alive

    def _key_of(nd) -> str:
        return str(nd.get("key") if nd.get("key") is not None else nd.get("authKey", ""))

    def _push(nd):
        nid = str(nd.get("id") or nd.get("url") or "?")
        try:
            mode = post_weights(
                nd["url"],
                _key_of(nd),
                iter_id,
                wver,
                weights_bytes,
                timeout=timeout,
                kind=kind,
            )
            return nid, nd, mode, None
        except (DistError, OSError) as e:
            # 裸 socket 异常（URLError/10054、读超时）也只剔除该节点：`_request` 不包装
            # OSError，放它穿过 `fut.result()` 会炸掉整批（2026-11-02 z4-polish it5：
            # kaggle 一次 10054 → 整轮 800 局 eval 0 局派出、无 summary）。
            return nid, nd, None, e

    n = len(nodes)
    ok_ids: set[str] = set()
    with runtime.Runtime(scope="weights-push", workers=min(8, n), monotonic=hk.monotonic) as ex:
        # 本段的两处计时都走本域的单调钟（§P1-6 的 clock 半边）：注入的时钟一换，
        # 「每个节点多久就绪 / 整批多久铺开」就完全可复现，不必再 mock 模块里的 `time`。
        t0 = ex.monotonic()
        futs = {ex.submit(_push, nd): nd for nd in nodes}
        t_sub: dict[int, float] = {id(f): ex.monotonic() for f in futs}
        for fut in as_completed(futs):
            nid, nd, mode, err = fut.result()
            # 事件：**该节点权重就绪时刻**（用户 2026-09-19：排查「等十几秒 CPU 才满」
            # 与评测中途的掉档——没有这条就看不出权重阶段占了多久）。
            dt = ex.monotonic() - t_sub[id(fut)]
            if err is not None:
                if log is not None:
                    log(f"[dist] weights POST to {nid} failed ({err}, {dt:.2f}s) — excluded")
                continue
            note_weights_pushed(wver, nid, kind=kind)
            if log is not None:
                log(f"[dist] weights[{kind}] -> {nid} ({mode}, {dt:.2f}s)")
            if on_alive is not None:
                on_alive(nd)
            ok_ids.add(nid)
        if log is not None:
            # 事件：**权重传输完毕**（阶段墙钟）——分发起点就是它。
            log(
                f"[dist] weights[{kind}] ready on {len(ok_ids)}/{n} nodes "
                f"in {ex.monotonic() - t0:.2f}s (sha {wver[:12]}…)"
            )
    return [nd for nd in nodes if str(nd.get("id") or nd.get("url") or "?") in ok_ids]
