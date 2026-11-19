"""
common/distribution.py — 分布式采样 trainer 侧公共工具（stdlib-only，可脱离 torch 独立测试）。

与 tools/agent/sampler-agent.ts 构成双语协议契约（plan/distributed-rollout.md v3.6）：

- codeHash 配方（两侧实现必须逐字节一致）→ `common/codehash.py`（2026-11-16 拆出：
  它只读文件、不发请求）。
- 远控升级 / 重启护栏 → `remote/node_upgrade.py`（2026-11-19 整簇搬出：它是跨机器的
  控制动作，且 L0 不该再长一处出站副作用；本模块只留它要用的 HTTP 漏斗 `node_request`）。
- 结果容器：
  - v2（BCV2，v3.6 起 agent 缺省）：gzip( magic u32 | headerLen u32 | headerJSON |
    entry* )，entry = nameLen u16 | name | dataLen u64 | 原始 npy 字节——由 exporter
    子进程打包，无 base64。files 值为 bytes。
  - v1（旧 agent 兼容）：gzip(JSON {manifest, files:{name: base64}})。files 值为 str。
  解码端 unpack_container() 按 magic 自动识别，validate/write_shard 双模兼容。
- 任务获取：fetch_task() **缺省 sync**（不带 x-async）——agent 跑完一局后在同一
  连接上直接回 BCV2 包（v3.5 路径；2026-09-14 实测 arena2 局均 ~0.4s，3s 异步
  轮询把吞吐打成 ~1/3，sync 与 poll=0.2 同速且实现更简单）。竞速输家副本仍传
  abandon_event → 自动回退 x-async+轮询，保留「all_settled 立即放弃」语义
  （2026-09-06 竞速收尾洞修复）。DIST_TASK_ASYNC=1 可全局强制旧异步路径。
- 权重下发：POST body = gzip(weights.json 字节)，头部 X-Iter-Id / X-Weights-Sha256；
  agent 校验 sha 一致后，同 sha 幂等不动、异 sha 原子切换并清空结果缓存。
  kept 短路径（2026-09-19）：先 GET /v1/weights/cached（头 X-Weights-Sha256 / X-Kind）
  探测；命中则不传 body 直接 kept。旧 agent 无此路径 → 404 → 回退完整 POST。
  批量下发用 post_weights_parallel（ThreadPool，与 ping 并行化同款）；pure_collect
  锚点语义不变（仍 = 全部节点权重就绪时刻）。
  同 it 补波复用（2026-09-19）：进程内 `_WEIGHTS_PUSHED[wver]→node ids`——
  partition_weights_nodes 拆 reuse/need；成功 POST 自动 note；ping/codeHash/bun
  exclude 须 forget_weights_node（防脏缓存）。

红线：远端结果必须先过 validate_result() 再落进 traj_dir —— discover_rl_shards()
对已落盘目录是无条件递归扫描的，落盘之后没有任何兜底。

★ 2026-09-27（S5 第十一刀）：shard 清单 / 结果容器校验 / 落盘 → `common/shard.py`；
进程内权重下发账本（`_WEIGHTS_PUSHED` + partition/note/forget）→ `common/weights_ledger.py`。
两者都在本模块留 `X as X` 门面（历史调用点一行不改）。
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import as_completed
from dataclasses import dataclass
from typing import Any

from common import net_http, runtime
from common.distribution_limits import STATUS_TIMEOUT_SEC as STATUS_TIMEOUT_SEC
from common.distribution_limits import node_slots as node_slots
from common.distribution_limits import status_timeout_of as status_timeout_of
from common.errors import NET_ERRORS as _BASE_NET_ERRORS
from common.hashing import sha256_file
from common.paths import REPO_ROOT

# 竞速账 / 采集口径 → `common/race_metrics.py`（2026-11-11 LOC 预算）。纯函数、无状态，
# 日志行与 `dist` 块共用同一处算术；本模块只留门面，`common.distribution.race_stats` 等
# 打桩面仍是同一个对象。
from common.race_metrics import by_node_of as by_node_of
from common.race_metrics import dist_block as dist_block
from common.race_metrics import dup_rate as dup_rate
from common.race_metrics import effective_slots as effective_slots
from common.race_metrics import race_log_line as race_log_line
from common.race_metrics import race_stats as race_stats
from common.race_metrics import rollout_collect_sec as rollout_collect_sec

# ------------------------------------------------------------------ 下沉（S5 第十一刀，2026-09-27）
# 「shard 清单 + 结果容器校验 + 落盘」→ `common/shard.py`；「进程内权重下发账本」→
# `common/weights_ledger.py`。实现搬家、名字留门面 ⇒ 全仓 `common.distribution.X(...)` 与
# `from common.distribution import X` 一律不改；两个新家都是 stdlib-only（零仓内依赖）。
#
# ⚠ patch 目标：驻本模块的调用方（`post_weights_parallel` / `refresh_weights`）与
# `rl.*` 的既有调用点读的都是**本模块全局** ⇒ 打桩打在门面才生效；账本 dict 是同一个
# 对象（`common.distribution._WEIGHTS_PUSHED is common.weights_ledger._WEIGHTS_PUSHED`）。
from common.shard import (
    BC_COLLECTOR as BC_COLLECTOR,
)
from common.shard import (
    BC_SHARD_FILES as BC_SHARD_FILES,
)
from common.shard import (
    INTENT_SHARD_FILES as INTENT_SHARD_FILES,
)
from common.shard import (
    SHARD_FILES as SHARD_FILES,
)
from common.shard import (
    _shard_files_for as _shard_files_for,
)
from common.shard import (
    validate_result as validate_result,
)
from common.shard import (
    write_shard as write_shard,
)
from common.task_key import task_key as task_key
from common.weights_ledger import (
    _WEIGHTS_PUSHED as _WEIGHTS_PUSHED,
)
from common.weights_ledger import (
    forget_weights_node as forget_weights_node,
)
from common.weights_ledger import (
    note_weights_pushed as note_weights_pushed,
)
from common.weights_ledger import (
    partition_weights_nodes as partition_weights_nodes,
)
from common.weights_ledger import (
    weights_already_pushed as weights_already_pushed,
)
from common.weights_ledger import (
    weights_push_cache_reset as weights_push_cache_reset,
)

#: 机器本地启动配置的环境变量名（`rl-config.json` **永不入库**：`nn-training/.gitignore`
#: 里 ignore 了它——它承载机器侧事实，控制台会直接改写它）。
RL_CONFIG_ENV = "BCITY_RL_CONFIG"


def rl_config_path() -> str:
    """rl-config.json 的**唯一**路径来源（env `BCITY_RL_CONFIG` > `nn-training/rl-config.json`）。

    为什么要有这个缝（2026-09-22，用户指令「测试应该使用自己的 fixtures」）：路径原本硬编码在
    三处（`trainer/run_rl.py` / `trainer/loop_serve.py::_read_rl_config` / 本文件），于是**任何读它的用例都
    隐式依赖本机那份未入库的配置**——本机 `rl.stream=1` 就让「`course_args` ≡ `trainer/run_rl.py`」的
    解析对拍变红（`tests/trainer/test_serve_wiring.py` 实测）：绿不绿取决于**别人机器上文件的内容**。
    有缝之后用例自带 tmp 夹具，两侧的读取点都走同一个 env。

    相对路径按「nn-training/ 下」解析（绝对路径原样），与默认值同一约定。
    """
    raw = (os.environ.get(RL_CONFIG_ENV) or "").strip()
    if not raw:
        return os.path.join(REPO_ROOT, "nn-training", "rl-config.json")
    if os.path.isabs(raw):
        return raw
    return os.path.join(REPO_ROOT, "nn-training", raw)


CONFIG_PATH = rl_config_path()

#: BC 语料任务的模式标识：agent /v1/task ?mode=bc（God-AI 教师，无权重）。
BC_MODE = "bc"
#: v6 BC 采集：agent 跑 `export-rl-rollout.ts`（策略权重 + 可选 --teacher-argmax）。
BC_V6_MODE = "bc-v6"
#: BC 任务 wver 常量（无权重语义——God-AI 教师自对弈不需要策略权重）。
BC_WVER = "bc"


#: `EVAL_TRACE_EVENTS` 的真值词（事件级追踪开关）。
_TRACE_TRUE = frozenset({"1", "true", "yes", "on"})


def trace_enabled(default: bool = False) -> bool:
    """事件级追踪开关（逐局派发/返回 + 在飞采样），供「CPU 满一阵又掉档」类排查。

    默认由调用方给（训练循环的 A/B/C 层保持安静；一次性评估入口缺省开）。
    `EVAL_TRACE_EVENTS=0` 强制关、=1 强制开——判据只此一处，调用方不各自解析字符串。
    """
    raw = os.environ.get("EVAL_TRACE_EVENTS")
    if raw is None:
        return default
    return raw.strip().lower() in _TRACE_TRUE


class DistError(RuntimeError):
    """节点交互失败：status=HTTP 状态码（0=本地校验拒绝），reason=可读原因。

    transient=True 表示**限流/抖动信号**（并发槽满 503 busy、连接被重置 10054、
    408/429/5xx）而非节点故障：调用方应背压重排 + 退避，**不计**节点失败 streak。
    判据在这里落地而不是调用方各自做字符串匹配 —— 与 biz/bc_dispatch 的 busy
    背压同源（那边 2026-09-14 事故：busy 被当故障熔断，整轮训练被打死）。
    """

    def __init__(self, status: int, reason: str, transient: bool = False) -> None:
        super().__init__(f"HTTP {status}: {reason}" if status else reason)
        self.status = status
        self.reason = reason
        self.transient = bool(transient)


#: 节点交互能抛出的**网络层**异常面（2026-11-15，G4 窄捕；基底见 `common/errors.py`）。
#:
#: 本模块十来个「探活 / 探测 / 取消」出站点原本一律 `except Exception`，把
#: 「节点没答」（连不上 / 超时 / 隧道抖动 —— 探活返回 None、探测返回 False 都是设计好的语义）
#: 与本模块自己的 bug（回执形状变了 ⇒ `AttributeError`，字段拼错 ⇒ `KeyError`）抹成同一分支。
#: 后者的现场表现是「全部节点不可用」：整批节点被静默排除、份额落到本地，而真因一行日志都没有。
#: 因此这里点名：`OSError`（URLError / TimeoutError / SSLError / 10054）、
#: `http.client.HTTPException`（`IncompleteRead` / `BadStatusLine`）、`RetryableError`、
#: 防御性 `RuntimeError`（含本模块的 `DistError`：收工态自投，不是故障而是「本单元不需要了」）。
#: 程序员的错不在这条边界里 —— 让它抛出去。
#: 见 DECISIONS §2026-11-15-network-error-surface-is-a-named-tuple。
NET_ERRORS: tuple[type[BaseException], ...] = (*_BASE_NET_ERRORS, http.client.HTTPException)

#: 探活类出站点：网络面 + 回执读不出来（`200` 但 body 不是 JSON ⇒ `JSONDecodeError` /
#: `UnicodeDecodeError`，都是 `ValueError`）。多一个成员就让探活返回「不可用」而不是崩，
#: 这不削弱判据：形状不看字段（`AttributeError` / `KeyError`）的错仍照抛。
PING_ERRORS: tuple[type[BaseException], ...] = (*NET_ERRORS, ValueError)


def load_dist_config(path: str = CONFIG_PATH) -> dict | None:
    """每轮分派点调用一次（动态读取）；文件缺失/损坏返回 None（纯本地模式）。

    用 utf-8-sig：运维在 Windows 上用 PowerShell 改 JSON 常带 BOM，必须容忍。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"[dist] WARN: cannot parse {path}: {e} — falling back to local-only")
        return None
    if not isinstance(cfg, dict) or not isinstance(cfg.get("nodes"), list):
        return None
    return cfg


def weights_fingerprint(path: str) -> str:
    """sha256(weights.json 文件字节) —— 版本过滤键（语义：样本确由该权重产生）。

    唯一实现见 `common.hashing.sha256_file`（本名保留：它是调用面最广的公共 API，
    20+ 处调用与账本口径都认这个名字）。本模块曾是「同字节同哈希」契约的三个定义点
    之一（另两个在 `remote.artifacts` / `remote.hub_client`），现在只剩 `common.hashing`。
    """
    return sha256_file(path)


# ---------------- HTTP ----------------
#: 视为瞬断/背压的 HTTP 状态码（不是节点故障）。
TRANSIENT_HTTP_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
#: 无 status 的异常（节点 SDK 直抛）里的 busy 文案判据，与 biz/bc_dispatch.is_busy_hint 同源。
_BUSY_HINT = "busy"


def is_transient_error(e: BaseException) -> bool:
    """背压/瞬断判定（**单一实现**，A/B/C 三层共用）：True = 限流或网络抖动，
    调用方**不得**把它计入节点失败 streak。

    优先级：① `DistError.transient`（fetch_task 对 10054/超时/408-429-5xx 的标记）；
    ② `DistError.status ∈ TRANSIENT_HTTP_STATUS`；③ 文案含 busy。
    非 DistError 的 OSError/TimeoutError（ConnectionResetError、读超时）一律 transient。
    409「wver not cached」**不在**此列：它是可刷新条件（见 refresh_weights），
    既不是背压也不是节点故障。

    历史：本判据原只长在 B 层（trainer/batch_eval.py），A 层（rollout）只豁免 503、
    训练干净评估层完全不豁免 —— 2026-09-19 实测 3 条 HTTP 502（cloudflared 隧道）
    就把 a97 熔断整轮（training-loop.log 09:48）。
    """
    if isinstance(e, DistError):
        if e.transient:
            return True
        if e.status in TRANSIENT_HTTP_STATUS:
            return True
        return _BUSY_HINT in str(e.reason)[:64].lower()
    # 鸭子协议（plan/eval-gap-honesty D1）：本机直跑的子进程失败自报 `transient`——
    # 等价于下面「OSError ⇒ True」那一档，只是 errno 已被归过类（`worker/local_eval_error.py`）。
    # 为什么用协议而不是 import：`common/` 是下层，不得反向依赖 `worker/`。
    if getattr(e, "transient", None) is True:
        return True
    if isinstance(e, OSError):  # ConnectionResetError / TimeoutError / URLError …
        return True
    return _BUSY_HINT in str(e)[:64].lower()


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


# ---------------- 收工即断连（2026-09-19 用户裁定第 4 条） ----------------
# 「竞速后 settled 一满，直接关闭所有节点的连接！！！立即！马上！right now！」
# urllib 无连接池可关，但每个在飞请求的 HTTPResponse 都能 close()：关掉它会让阻塞中的
# read 立刻抛（连接重置），而不是等慢节点把那一局算完（最长 taskTimeoutSec=900s）。
# **作用域 = 线程 tag**：训练主循环里 rollout 与本层同进程并发，全局关连接会把别人的在飞
# 请求一起打死（rollout 任务被当瞬断重排 = 白烧节点）⇒ 只关自己 tag 的请求。
_TAG = threading.local()


class RequestRegistry:
    """在飞 HTTP 响应登记 + 收工断连标记（显式对象；模块只留一个进程级默认实例）。

    P0-5（`common/__init__.py` 契约 #4）：「`common/` 是 L0，模块级可变容器必须清零」
    ⇒ 三份状态（锁 / 在飞集合 / 收工标记集）搬进本类，模块顶层只剩 `_REQUESTS` 这一个
    对象。要隔离的测试自己 `RequestRegistry()`，不再去清/读模块私有集合。
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: set[tuple[str, object]] = set()
        self.aborted: set[str] = set()

    def begin_abort(self, scope: str) -> list[object]:
        """置收工态并摘出该作用域的在飞响应（调用方负责异步 close）。"""
        with self.lock:
            self.aborted.add(scope)
            return [r for t, r in self.active if t == scope]

    def clear_abort(self, tag: str = "") -> None:
        with self.lock:
            if tag:
                self.aborted.discard(tag)
            else:
                self.aborted.clear()

    def is_aborted(self, tag: str) -> bool:
        with self.lock:
            return tag in self.aborted

    def tags(self) -> set[str]:
        """当前处于收工态的**全部**作用域（副本；诊断 / 用例用）。"""
        with self.lock:
            return set(self.aborted)

    def track(self, key: tuple[str, object]) -> None:
        with self.lock:
            self.active.add(key)

    def untrack(self, key: tuple[str, object]) -> None:
        with self.lock:
            self.active.discard(key)


_REQUESTS = RequestRegistry()


def set_request_tag(tag: str) -> None:
    """给**当前线程**的 HTTP 请求打标（`abort_active_requests` 的作用域键）。

    线程本地，不继承：调用方须在每个自建线程（worker/supervisor）开头显式设置。
    空 tag = 不参与收工断连（rollout/A/C 层的既有行为逐字不变）。
    """
    _TAG.value = tag


def request_tag() -> str:
    return str(getattr(_TAG, "value", "") or "")


def abort_active_requests(tag: str = "") -> int:
    """停发同作用域的新请求 + **交 daemon 线程**关闭其全部在飞连接（返回待关条数）。

    **为什么关连接必须异步**（2026-09-19 实测）：`HTTPResponse.close()` 可能阻塞——
    若响应体正被读线程消费，close 要等它让出内部锁；800 局探针实测主线程在
    `abort_active_requests` 里卡了 **81 秒**（该批 181s 就跑完 800 局，收工却占掉 32%
    墙钟）。收工路径「立即！马上！」是硬要求 ⇒ 置位 + 尽力关，绝不等。

    已知局限（如实记录）：阻塞在 `urlopen`（响应头都还没回来的慢节点）的连接不在
    注册表里 ⇒ 关不到它们。那部分只能等它们自己超时；`all_done` 已置位 + 新请求拒发，
    调用方不再依赖它们的结果。
    """
    scope = tag or request_tag()
    targets = _REQUESTS.begin_abort(scope)
    if targets:

        def _closer() -> None:
            for r in targets:
                try:
                    r.close()  # type: ignore[attr-defined]
                except Exception:
                    # 契约静默（G9 冻结，非旧债）：守护线程里尽力关响应对象，没人在等它关完；
                    # 目标类型不保证有 close（上面已显式 isinstance 筛过）。
                    pass

        #: 收尾线程归这里（`abort-close-drop`）：只关连接、没人等它（daemon）。
        runtime.Runtime(scope="abort").thread(_closer, name="close-drop", daemon=True)
    return len(targets)


def clear_abort(tag: str = "") -> None:
    """解除收工态（**每个单元开头必须调**；否则上一单元收工后请求立刻被拒）。

    `tag` 非空只清该作用域：多课程同进程各有自己的 `evalscope:*`/`batcheval:*`，
    全局清空会把兄弟单元刚落下的收工态重新解封（尾巴线程又获准发请求）。
    空 = 全清（旧调用方逐字不变）。
    """
    _REQUESTS.clear_abort(tag)


def abort_scope(tag: str = "") -> bool:
    """该作用域是否处于收工态（回包无用 ⇒ 调用方丢弃而不是重排）。"""
    return _REQUESTS.is_aborted(tag or request_tag())


def aborted_tags() -> set[str]:
    """当前处于收工态的全部作用域（副本；诊断 / 用例用——判「成功结算有没有留脏」）。"""
    return _REQUESTS.tags()


def node_request(
    url: str,
    auth_key: str,
    timeout: float,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
):
    scope = request_tag()
    if scope and abort_scope(scope):
        # 收工后不再发新请求（settled 已满 = 本单元不需要任何新结果）。
        raise DistError(0, "aborted: settled complete", transient=True)
    auth = {"Authorization": f"Bearer {auth_key}", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=auth, method=method)
    # 竞速 connect（2026-11-11）：云机隧道主机名解析出多个地址，stdlib 串行试、每个地址
    # 各给一份 timeout ⇒ 本机无 IPv6 路由时一次 ping 的 tcp 段就要 16s（见 common/net_http.py）。
    # 节点通信全走这条出口，所以 ping / 权重探针 / POST / 取任务 / 取结果一并受益。
    with net_http.racing_urlopen(req, timeout=timeout) as resp:
        key = (scope, resp)
        _REQUESTS.track(key)
        try:
            return resp.status, resp.read()
        finally:
            _REQUESTS.untrack(key)


# 探活/槽位预算的门面（2026-11-11 下沉，见 `common/distribution_limits.py`）：上面三行
# 与 `distribution_limits` 是同一个对象，缺省 3s 的来历与 §149 的收回理由都记在那里。
def ping_nodes_parallel(nodes: list, timeout: float = STATUS_TIMEOUT_SEC) -> list[dict | None]:
    """**并行** ping 一批节点（保序返回，失败 = None）。

    为什么必须并行：串行 ping 的墙钟 = Σ 每台延迟，而超时预算是**按节点**的
    （`STATUS_TIMEOUT_SEC`）⇒ 两台负载高的节点就让整个阶段花两倍预算（2026-09-19
    实测：`u0 阶段 gate 6.83s alive=4/6`，其中两台正是超时被丢）。节点门在每单元
    重跑一次，这笔开销按单元累加。并行后 == 最慢一台。
    """
    if not nodes:
        return []

    def _one(nd: dict) -> dict | None:
        # 键名兼容 `key`（归一化配置，eval_dispatch/batch_eval 用的形态）与 `authKey`
        # （rl-config.json 原始行）——旧实现只认 authKey，把归一化过的配置喂进来会静默
        # 401 ⇒ 整批节点「ping 失败」（2026-09-19 B6 现场探针实测）。
        return node_ping(
            str(nd.get("url") or ""),
            str(nd.get("key") if nd.get("key") is not None else nd.get("authKey", "")),
            timeout=timeout,
        )

    with runtime.Runtime(scope="ping-nodes") as rt:
        return rt.map(_one, nodes, workers=min(8, len(nodes)))


def node_ping(url: str, auth_key: str, timeout: float = STATUS_TIMEOUT_SEC) -> dict | None:
    """GET /v1/ping → dict；任何失败返回 None（调用侧决定排除该节点）。"""
    try:
        status, body = node_request(url.rstrip("/") + "/v1/ping", auth_key, timeout)
        if status == 200:
            # json.loads 返回 Any；节点 /v1/ping 契约固定为 dict。
            info: dict = json.loads(body.decode("utf-8"))
            return info
    except PING_ERRORS:
        return None
    return None


def push_node_ping(url: str, auth_key: str, timeout: float = 3.0) -> dict | None:
    """`GET /ping`（Bearer）→ dict；任何失败返回 None。

    为什么不能复用 `node_ping`：push 类节点（`gpu_push` / `offline_push`）跑的是
    `remote/worker_server.py`，它的探活面是 **`/ping`**（Bearer），不是 rollout agent 的
    `/v1/ping`（query token）。同一个 hub 同时伺候两种节点，判据必须按 `kind` 分流
    （`hub.admin._admin_nodes_register`）。
    """
    try:
        status, body = node_request(url.rstrip("/") + "/ping", auth_key, timeout)
        if status == 200:
            info: Any = json.loads(body.decode("utf-8"))
            return info if isinstance(info, dict) else None
    except PING_ERRORS:
        return None
    return None


#: 进程级课程身份（环境变量名）。训练进程 = 一门课，`apply_course` 挂上后所有
#: 出站权重/任务都带它——agent 侧据此按 (course, kind) 分桶（多课程互不驱逐）。
#: 为什么走环境变量而不是逐调用点加参数：这 9 个出站点散在 6 个文件的不同闭包里，
#: 逐点穿参要改十几个签名且每加一个调用点就要记得再穿一次；而「这个进程是哪门课」
#: 是**进程级身份**，不是逐请求的参数（与 PYTHONPATH/PYTHONHASHSEED 同类）。
COURSE_ENV = "RL_COURSE_NAME"


def course_name_of() -> str:
    """本进程的课程短名（未设/空 → 空串 = 旧单课程桶，agent 侧两向兼容）。"""
    return os.environ.get(COURSE_ENV, "").strip()


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
        status, body = node_request(
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
        status, body = node_request(
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
    status, body = node_request(
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
        status, _ = node_request(
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
        status, body = node_request(f"{base}/v1/task?{qs}", auth_key, timeout, headers=hdrs)
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
            status, body = node_request(
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
