"""common/task_client.py — 节点**任务协议**（提交 / 轮询 / 取消 / 结果容器 / 能力握手）。

2026-11-19 从 `common/distribution.py` 整簇搬出（B8 第十一刀）：那一簇是**协议**（agent 的
`/v1/task`、`/v1/result`、`/v1/cancel` 四张契约 + BCV2/v1 容器解码），而 `distribution` 只该
剩**传输核心**（配置 / 错误分类 / 收工断连 / 唯一 HTTP 漏斗 / 探活）。搬家与升级/重启簇同一
判据：消费者全在 L0 之上，且这一簇没有自己的出口。

⚠ **打桩缝**：本模块经 `distribution.distribution.node_request(...)` 发请求（属性查找，不是
`from … import node_request`）——用例打桩 `common.distribution.node_request` 仍能拦住这里的
每一次出站；改名/内联会让那 20+ 条「零 socket」用例变成静默空操作（本仓已踩过两次）。

红线（不变）：远端结果必须先过 `validate_result()` 再落盘；`unpack_container` 只解容器，不做
校验。取任务的 sync/poll 取舍与 DIST_TASK_ASYNC 语义见 `docs/nn/engineering.md` §220。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
from collections.abc import Callable

from common import distribution
from common.distribution import (
    NET_ERRORS,
    TRANSIENT_HTTP_STATUS,
    DistError,
    course_name_of,
    request_tag,
    set_request_tag,
)
from common.task_key import task_key

#: 「取包丢失」标记：节点重启 / 权重切换清场后结果缓存为空，轮询 /v1/result 答 404。
#: **单一来源**——本常量在此定义，_poll_result 用它拼消息、is_task_lost_error 用它判据，
#: A/C 层不得各写一份字面量。
TASK_LOST_MARKER = "task lost on node"


def is_task_lost_error(e: BaseException) -> bool:
    """取包丢失（节点重启 / 清场后节点侧结果缓存为空）——**既非背压、也非节点故障**。

    节点没坏，只是它**进程内**那份结果没了（`resultCache` / `failedTasks` / `inflight`
    都是节点内存态，agent 一重启即空）⇒ 正确处置 = 立刻回队重跑（局是 (权重,关卡,种子)
    的纯函数 ⇒ 字节一致）+ 清该节点 reuse 账本（重启同时也意味着它的权重桶可能空了）。

    为什么不并入 is_transient_error：处置与措辞都不同（瞬断=背压退避重排；任务丢失=重新
    提交），且**裸 404**（路径写错之类客户端 bug）必须继续响亮失败 ⇒ 判据要求
    「状态 404 **∧** 文案带 TASK_LOST_MARKER」。

    背景（2026-09-19 审计 F1）：旧实现把这条 404 当确定性失败记节点 streak ⇒ 与 409 同族，
    3 条就把**刚重启**的节点熔断整轮，还白耗 attempt 配额。
    """
    return (
        isinstance(e, DistError) and e.status == 404 and str(e.reason).startswith(TASK_LOST_MARKER)
    )


def unpack_container(raw: bytes) -> tuple[dict, dict]:
    """解结果容器（自动识别版本）→ (manifest, files)。

    v2（BCV2）files 值为 bytes；v1（旧 agent）值为 base64 str——下游
    validate_result/write_shard 双模兼容，勿假设其中一种。
    """
    frame = gzip.decompress(raw.lstrip(b" \t\r\n"))
    if len(frame) >= 8:
        (magic,) = struct.unpack_from(">I", frame, 0)
        if magic == PACK_MAGIC:
            return _unpack_bcv2(frame)
    container = json.loads(frame.decode("utf-8"))
    if not isinstance(container, dict) or not isinstance(container.get("files"), dict):
        raise DistError(0, "container missing files map")
    return container.get("manifest") or {}, container["files"]


# BCV2 魔数 'B''C''V''2' —— 与 tools/sim/pack-container.ts 逐字节一致的双语契约。
PACK_MAGIC = 0x42435632


def _unpack_bcv2(frame: bytes) -> tuple[dict, dict]:
    off = 4
    (hlen,) = struct.unpack_from(">I", frame, off)
    off += 4
    header = json.loads(frame[off : off + hlen].decode("utf-8"))
    off += hlen
    manifest = header.get("manifest") or {}
    files: dict[str, bytes] = {}
    for spec in header.get("files") or []:
        (nlen,) = struct.unpack_from(">H", frame, off)
        off += 2
        name = frame[off : off + nlen].decode("utf-8")
        off += nlen
        (dlen,) = struct.unpack_from(">Q", frame, off)
        off += 8
        if name != spec.get("name") or dlen != spec.get("len"):
            raise DistError(0, f"bcv2 entry mismatch: {name!r} (header said {spec!r})")
        files[name] = frame[off : off + dlen]
        off += dlen
    return manifest, files


def check_node_capabilities(
    args: object,
    node_id: str,
    nd: dict,
    *,
    stage_json: str | None = "",
) -> None:
    """节点能力握手（fail-closed）：缺能力位的旧 agent 一律拒派，绝不静默降级。

    四条契约（plan §5.2 / LC §4.6 / k5-rhythm / greedy-outcome-quota rev5）：
      · stageJsonSupport      —— 自定义关（旧 agent 会静默跑默认关）
      · decisionEventsSupport —— 事件门（旧 agent 会静默跑均匀 K）
      · decisionKSupport      —— K≠10（旧 agent 会静默按 K=10 跑）
      · greedyRolloutSupport  —— `?labelMode=greedy`（旧 agent 会静默跑成采样局，
        正是 o1-gq-* 前 9 轮 `bins` 全 0 的根因）
    只在对应能力被激活时检查；缺席 = 老课程逐字节不变。
    """
    ping = nd.get("ping") or {}
    if stage_json and not ping.get("stageJsonSupport"):
        raise DistError(
            0,
            f"node {node_id} 缺 stageJsonSupport 能力位（旧 agent）——"
            "stageJson 任务被拒；不降级（plan §5.2 能力握手）",
        )
    if bool(getattr(args, "decision_events", False)) and not ping.get("decisionEventsSupport"):
        raise DistError(
            0,
            f"node {node_id} 缺 decisionEventsSupport 能力位（旧 agent）——"
            "事件任务被拒；不降级（升级 agent 后自动加入）",
        )
    dk = int(getattr(args, "decision_k", 10))
    if dk != 10 and not ping.get("decisionKSupport"):
        raise DistError(
            0,
            f"node {node_id} 缺 decisionKSupport 能力位（旧 agent）——"
            f"K={dk} 任务被拒；不降级（升级 agent 后自动加入）",
        )
    if bool(getattr(args, "rollout_argmax", False)) and not ping.get("greedyRolloutSupport"):
        raise DistError(
            0,
            f"node {node_id} 缺 greedyRolloutSupport 能力位（旧 agent）——"
            "greedy 局被拒；升级 sampler-agent 后自动加入",
        )


def cancel_task(base_url: str, auth_key: str, key: str, timeout: float = 3.0) -> bool:
    """POST /v1/cancel {key}；旧 agent / 404 / 网络错误一律 False（不抛）。

    调用方在**独立 daemon 线程**里跑：不继承 worker 的 `rollout:*` 断连 tag，
    否则 reaper 刚 `abort_active_requests` 过的作用域会把这条取消请求也拦掉。
    """
    old_tag = request_tag()
    set_request_tag("")
    try:
        status, _ = distribution.node_request(
            base_url.rstrip("/") + "/v1/cancel",
            auth_key,
            timeout,
            data=json.dumps({"key": key}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return bool(status == 200)
    except NET_ERRORS:
        return False
    finally:
        set_request_tag(old_tag)


def fetch_task(
    url: str,
    auth_key: str,
    *,
    iter_id: str,
    wver: str,
    stage: int,
    seed: int,
    max_ticks: int,
    difficulty: str,
    timeout: float,
    mode: str | None = None,
    kind: str = "rollout",
    # 标签/动作来源：`argmax`/`sampled` = BC v6 标签（`mode=bc-v6`）；
    # `greedy` = RL greedy 分层采集（2026-11-10，所有节点通用）。非空即进 agent
    # taskKey 后缀（`:lm<mode>`）⇒ 同一 iterId 下不同动作选择方式的局互不回放缓存。
    label_mode: str = "",
    replan: int = 0,
    reward: str = "",
    course: str | None = None,
    dodge: str = "",
    stage_json: str = "",
    lives_override: int | None = None,
    player_level: int | None = None,
    course_fp: str = "",
    # BC 语料任务（mode="bc"）的 God-AI 教师参数（export-godai-bc 透传）。
    wins: int | None = None,
    near_miss_times: int | None = None,
    abandon_event: threading.Event | None = None,
    # T1.2 policy 透传（EvalBench）：'nn' | 'god'（C 层 God 基线）。
    # agent 侧已就绪（sampler-agent.ts:1150 收 ?policy= → export-eval-game --policy）。
    # 缓存键注意：agent taskKey 无 policy 分量——调用方必须用独立 iterId 命名空间
    # 隔离不同 policy（A 层恒 nn；B/C 批用 batch 命名空间），否则 god/nn 同键串局。
    policy: str = "nn",
    # R2 事件 rung（plan/new-era-stop.plan.md §6）：True ⇒ agent 以事件门跑本局
    # （均匀 K ∪ threat-ONSET + Δt≥3）；缺席/False = 老行为（agent 侧缺省）。
    # agent taskKey 含 `:de1` 后缀（与 stageJsonHash 同规），旧缓存不串局；
    # 不支持的旧 agent 由**调用方**凭 ping.decisionEventsSupport 提前排除（fail-closed，
    # 同 stageJsonSupport），本函数只负责透传。
    decision_events: bool = False,
    # 决策周期 K（plan/k5-rhythm.plan.md）：课程 `decision_k`（默认 10）透传。
    # **仅 ≠10 激活**（缺席/10 = 老 agent 照旧，URL 逐字节不变）；agent 端 taskKey 含
    # `:k<N>` 后缀（缓存隔离）；旧 agent 无 decisionKSupport 能力位 —— 由调用方提前排除
    # （fail-closed；本函数只透传，不查 ping）。
    decision_k: int = 10,
    # 键回调：请求发出前把 agent 认可的 taskKey 交回调用方。分配超时取消
    # （/v1/cancel）必须与提交端逐字节同键，故由这里统一算，调用方不再复制配方。
    on_key: Callable[[str], None] | None = None,
) -> tuple[dict, dict]:
    """获取一局结果 → (manifest, files)；失败抛 DistError。

    mode='eval' 请求干净评估局（agent 端贪心 runner、无 shards）；仅对 ping 返回
    evalSupport=true 的节点使用——旧 agent 会静默忽略该参数跑成采样局。

    kind（M8）：'intent' 请求意图权重桶（意图 RL rollout 走 export-intent-rollout.ts）。
    replan（M8）：意图 rollout 的 replan cadence（0=不传）。
    reward（goal-nn 卡 A2）：玩具奖励臂覆盖（''=不传，导出器按 stage 解析默认）。
    dodge（goal-nn 卡 A3）：dodge 模式覆盖（''=不传，导出器按 stage 解析默认）。

    stage_json / lives_override / player_level（M1d，plan/rl-training-config.md §5.2）：
    课程自定义关透传参数（远端 export-rl-rollout --stage-json/--lives-override/
    --player-level）。stageJson 非空时只派给 ping.stageJsonSupport=true 的节点
    （旧 agent 不认识该参数会静默丢弃跑默认关——数据污染，绝不降级）。

    缺省 **sync**（不带 x-async）：agent 跑完直接在同一连接回整包。timeout 必须
    覆盖整局墙钟（arena2 ~1s；经典关可到数十秒）——短超时会把同步请求误杀。

    仍走 x-async+轮询的例外：
      · abandon_event 非 None（竞速输家）——轮询阶段可在 all_settled 时立刻放弃；
        sync 长连接无法中断，会把注定丢弃的局等满。
      · 环境变量 DIST_TASK_ASYNC=1（运维强制旧异步路径 / WAN 抖动排查）。
    """
    params = {
        "iterId": iter_id,
        "wver": wver,
        "stage": stage,
        "seed": seed,
        "maxTicks": max_ticks,
        "difficulty": difficulty,
    }
    if mode:
        params["mode"] = mode
    if policy and policy != "nn":
        params["policy"] = policy
    if kind != "rollout":
        params["kind"] = kind
    if label_mode:
        params["labelMode"] = label_mode
    if replan > 0:
        params["replan"] = replan
    if reward:
        params["reward"] = reward
    if dodge:
        params["dodge"] = dodge
    if stage_json:
        params["stageJson"] = stage_json
        # 布局指纹（评审 R0-3 / LC §1.3）：agent resultCache 键并入该哈希，改 grid
        # 不改 stage id 不再静默复用旧局。agent 侧对收到的 stageJson 算同一 sha256。
        params["stageJsonHash"] = hashlib.sha256(stage_json.encode("utf-8")).hexdigest()[:16]
    if lives_override is not None:
        params["livesOverride"] = lives_override
    if player_level is not None:
        params["playerLevel"] = player_level
    # R2 事件 rung：仅激活时透传（缺席 = 老 agent 照旧，老行为逐字节不变）。
    if decision_events:
        params["decisionEvents"] = "1"
    # 决策周期 K：仅非默认值透传（缺席/10 = 老 agent 照旧，URL 逐字节不变）。
    if int(decision_k) != 10:
        params["decisionK"] = str(int(decision_k))
    if course_fp:
        params["courseFp"] = course_fp
    # v5 多课程：任务自报课程——agent 按 (course, kind) 取权重桶。缺省走进程级身份
    # （旧训练侧不传 → 空串桶，行为与改造前一致）。
    if course is None:
        course = course_name_of()
    if course:
        params["course"] = course
    if wins is not None:
        params["wins"] = wins
    if near_miss_times is not None:
        params["nearMissTimes"] = near_miss_times
    if on_key is not None:
        on_key(
            task_key(
                iter_id,
                str(params.get("mode", "rollout")),
                str(params.get("kind", "rollout")),
                stage,
                seed,
                str(params.get("stageJsonHash", "")),
                str(params.get("courseFp", "")),
                str(params.get("decisionEvents", "")),
                str(params.get("decisionK", "")),
                str(params.get("labelMode", "")),
            )
        )
    qs = urllib.parse.urlencode(params)
    base = url.rstrip("/")
    started = time.monotonic()
    try:
        use_async = abandon_event is not None or os.environ.get("DIST_TASK_ASYNC") == "1"
        hdrs = {"x-async": "1"} if use_async else None
        status, body = distribution.node_request(f"{base}/v1/task?{qs}", auth_key, timeout, headers=hdrs)
        if status == 202:
            return _poll_result(
                base,
                auth_key,
                params,
                timeout - (time.monotonic() - started),
                abandon_event=abandon_event,
            )
        if status == 200:
            return unpack_container(body)
        raise DistError(status, body[:300].decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise DistError(
            e.code,
            e.read()[:300].decode("utf-8", "replace"),
            transient=e.code in TRANSIENT_HTTP_STATUS,
        ) from e
    except DistError:
        raise
    except OSError as e:
        # 传输层瞬断：连接被重置（WinError 10054）/超时/对端关闭 —— 节点可能只是满负荷。
        # 标记 transient 由调用方背压重排，不当节点故障（2026-09-19：满负荷集群下
        # 一瞬 10 次 10054 曾把 6 个节点全部熔断，评估份额静默全落本地）。
        raise DistError(0, f"task fetch failed: {e}", transient=True) from e
    except Exception as e:
        # 其余非 DistError（损坏容器解包错等确定性错误）不标 transient：重试没有意义，
        # 真实原因保留在 message 里。
        raise DistError(0, f"task fetch failed: {e}") from e


#: `_poll_result` 预算的**下限**（生产语义：防住「预算小到没意义」的调用）。
#: 独立成模块常量而非内联 `max(1.0, …)`，是为了让用例能把它调到「毫秒级」而**不改判据**：
#: 「未置位 ⇒ 持续轮询到预算耗尽」这条断言与预算绝对长度无关，但预算就是那些用例的
#: 固有墙钟（2026-09-29，§43：`tests/common/test_dist_common_poll.py` 两例共 ~1s 全花在跑满下限）。
POLL_MIN_BUDGET_SEC = 1.0


def _poll_result(
    base_url: str,
    auth_key: str,
    params: dict,
    budget: float,
    poll_s: float = 3.0,
    abandon_event: threading.Event | None = None,
) -> tuple[dict, dict]:
    """轮询 GET /v1/result 直到取包/失败/超时。budget 秒内传输瞬断一律重试。

    只有网络调用本身受瞬断重试保护；容器解包与非预期状态码是确定性错误，
    必须立即抛出真实原因——绝不能被重试逻辑吞成误导性的 deadline exceeded。
    abandon_event（2026-09-06，竞速收尾洞修复）：置位 = 本轮全部任务已结算，
    本副本必是竞速输家（结果注定被丢弃）→ 立即放弃，trainer 不再等慢节点把
    注定丢弃的局跑完（实测输家副本曾拖住发布 4.5 分钟）。
    """
    qparams = {
        "iterId": params["iterId"],
        "stage": params["stage"],
        "seed": params["seed"],
    }
    # mode/kind 必须与提交端 key 配方一致（agent 的 result key = iterId:mode:kind:stage:seed）——
    # 意图 rollout（kind=intent）不传则轮询落空 404（实测教训）。
    if params.get("mode"):
        qparams["mode"] = params["mode"]
    if params.get("kind"):
        qparams["kind"] = params["kind"]
    # stageJsonHash 必须进轮询键（agent resultCache key 含布局指纹，M1d）
    if params.get("stageJsonHash"):
        qparams["stageJsonHash"] = params["stageJsonHash"]
    # courseFp 必须进轮询键（agent resultCache key 含课程血缘，D14）
    if params.get("courseFp"):
        qparams["courseFp"] = params["courseFp"]
    # 决策门分量必须进轮询键（agent resultCache key 含 `:de1` / `:k<N>` 后缀，
    # 提交端与轮询端同配方——漏传 = 同上键永远找不到任务，2026-10-07 审计修复：
    # decisionEvents 此前漏在这一层，事件课程的 async 竞速副本必 404）。
    if params.get("decisionEvents"):
        qparams["decisionEvents"] = params["decisionEvents"]
    if params.get("decisionK"):
        qparams["decisionK"] = params["decisionK"]
    # v6 BC：labelMode 进提交端 taskKey 后缀，轮询端必须同配方（否则 async 必 404）。
    if params.get("labelMode"):
        qparams["labelMode"] = params["labelMode"]
    qs = urllib.parse.urlencode(qparams)
    deadline = time.monotonic() + max(POLL_MIN_BUDGET_SEC, budget)
    while True:
        if abandon_event is not None and abandon_event.is_set():
            raise DistError(0, "abandoned: all tasks settled — race-loser copy dropped")
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise DistError(0, "async result deadline exceeded (game still running on node?)")
        try:
            status, body = distribution.node_request(
                f"{base_url}/v1/result?{qs}", auth_key, timeout=min(20.0, remain)
            )
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            if e.code == 500:
                # 局失败已被 agent 一次性消费；trainer 照常回队重试（可能换节点）
                try:
                    msg = json.loads(detail).get("error", detail)
                except ValueError:
                    msg = detail
                raise DistError(500, str(msg)) from e
            if e.code == 404:
                # 文案前缀 = TASK_LOST_MARKER（单一来源）：客户端据此判定「重启/清场丢包」，
                # 与「裸 404 = 路径写错」区分开。
                raise DistError(404, f"{TASK_LOST_MARKER} (restart/purge): {detail}") from e
            raise DistError(e.code, detail) from e
        except NET_ERRORS:
            # 瞬断（休眠/SSH 重连/隧道抖动）：结果仍在节点缓存里，睡一下继续拉。
            # 窄捕：解包/记账里的程序员错不许伪装成「节点慢」而重试到 deadline。
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(poll_s, max(0.5, deadline - time.monotonic())))
            continue
        if status == 200:
            return unpack_container(body)
        if status == 202:
            time.sleep(min(poll_s, remain))
            continue
        raise DistError(status, body[:300].decode("utf-8", "replace"))


def validate_eval_result(manifest: dict, expected_wver: str) -> str | None:
    """干净评估局的轻量校验：无 shards，仅对账 wver、模式回显与关键字段。"""
    if not isinstance(manifest, dict):
        return "manifest is not an object"
    if manifest.get("wver") != expected_wver:
        return f"wver mismatch: got {manifest.get('wver')!r}"
    if manifest.get("mode") != "eval":
        return f"mode echo mismatch: got {manifest.get('mode')!r} (old agent ran sampled game?)"
    for k in ("outcome", "ticks", "win", "stage", "seed"):
        if k not in manifest:
            return f"missing field {k!r}"
    return None
