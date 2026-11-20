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

★ 2026-11-19（B8 第十一刀）：**任务协议**（提交 / 轮询 / 取消 / 结果容器 / 能力握手）→
`common/task_client.py`；**权重传输**（探针 / POST / 并行下发 / 409 自愈）→
`common/weights_client.py`。本模块此后只剩**传输核心**：机器配置 / 错误分类 / 收工断连的
在飞登记 / 唯一 HTTP 漏斗 `node_request` / 探活（`node_ping` / `ping_nodes_parallel` /
`push_node_ping`）/ 权重指纹。上面几簇都经属性查找调 `distribution.node_request`，
所以「打桩打在漏斗上」这条缝没变（见 `docs/nn/engineering.md` §220）。
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from common import http_pool, net_http, runtime
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


def set_request_tag(tag: str, kind: str | None = None) -> None:
    """给**当前线程**的 HTTP 请求打标（`abort_active_requests` 的作用域键）。

    线程本地，不继承：调用方须在每个自建线程（worker/supervisor）开头显式设置。
    空 tag = 不参与收工断连（rollout/A/C 层的既有行为逐字不变）。

    `kind` 顺带设计数桶的用途段（`None` = 不动已有 kind）：tag 与 kind 同在请求上下文里，
    两个都设时不必写两行（`trainer/dispatch.py` 等文件有 LOC 预算）。
    """
    _TAG.value = tag
    if kind is not None:
        set_request_kind(kind)


def request_tag() -> str:
    return str(getattr(_TAG, "value", "") or "")


#: 请求用途（`http_pool` 计数分桶的第二段）。thread-local，与 `_TAG` 同纪律。
_KIND = threading.local()


def set_request_kind(kind: str) -> None:
    """给**当前线程**的 HTTP 请求打用途标签（`rollout|eval|batch|weights|probe|other`）。

    2026-11-14 评审：池计数是进程级且读后清零，而一个 serve 进程并发跑 10+ 课程 ⇒
    没有这一层就分不清「谁的 connect」。线程本地、不继承：调用方须在每个自建线程
    （rollout worker / eval 节点腿 / batch supervisor）开头显式设置；空串 = 回到 path 推断。
    """
    _KIND.value = kind


def request_kind() -> str:
    return str(getattr(_KIND, "value", "") or "")


def _kind_from_url(url: str) -> str:
    """未显式设 kind 时的推断：探活 → probe · 权重面 → weights · 任务面按 `mode` 分
    （`mode=eval` = 评估腿，其余 = rollout）。推断就够用的地方不设 thread-local（LOC 预算）。"""
    parts = urllib.parse.urlsplit(url)
    path = parts.path or ""
    if path in ("/v1/weights", "/v1/blob-sha", "/v1/code-sha"):
        return "weights"
    if path.startswith("/v1/ping") or path.startswith("/v1/status") or path == "/v1/restart":
        return "probe"
    if path in ("/v1/task", "/v1/result"):
        mode = urllib.parse.parse_qs(parts.query).get("mode", [""])[0]
        return "eval" if mode == "eval" else "rollout"
    return "other"


def pool_bucket(kind: str | None = None) -> str:
    """`http_pool` 的计数桶 = `<course>/<kind>`（唯一拼法；空课程/空 kind 自动省略）。

    为什么带课程名：同一个 trainer 进程同时跑多门课（现场 10+），而池计数是**进程级**的
    ——只分 kind 不分课程，两个课程的行依然互相抢桶（plan §2.2）。
    """
    k = (request_kind() if kind is None else kind).strip()
    return "/".join(p for p in (course_name_of(), k) if p)


def pool_round_suffix(kinds: tuple[str, ...] = ("rollout", "eval")) -> str:
    """`[dist] round done` 行尾的**全部**桶读数（只打一个数永远说不清是谁的）。"""
    return "".join(http_pool.stats_suffix(pool_bucket(k)) for k in kinds)


def abort_active_requests(tag: str = "") -> int:
    """停发同作用域的新请求 + **交 daemon 线程**关闭其全部在飞连接（返回待关条数）。

    同时通知长连接池（`common.http_pool`）：池内响应也要关、该作用域不得再重发
    （「收工即断连」不允许把一局重新开起来）。

    **为什么关连接必须异步**（2026-09-19 实测）：`HTTPResponse.close()` 可能阻塞——
    若响应体正被读线程消费，close 要等它让出内部锁；800 局探针实测主线程在
    `abort_active_requests` 里卡了 **81 秒**（该批 181s 就跑完 800 局，收工却占掉 32%
    墙钟）。收工路径「立即！马上！」是硬要求 ⇒ 置位 + 尽力关，绝不等。

    已知局限（如实记录）：阻塞在 `urlopen`（响应头都还没回来的慢节点）的连接不在
    注册表里 ⇒ 关不到它们。那部分只能等它们自己超时；`all_done` 已置位 + 新请求拒发，
    调用方不再依赖它们的结果。
    """
    scope = tag or request_tag()
    # 长连接池也要关、且该作用域不得再重发（「收工即断连」不允许把一局重新开起来）。
    http_pool.abort_scope(scope)
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
    http_pool.clear_abort(tag)


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
    # 长连接池（2026-11-14，P4）：本机→节点每请求新建连接要 0.77s，复用 0.32s。池内保留
    # **竞速 connect**（§149 的 IPv6 税修复住那里）与 urllib 的异常语义（HTTPError/URLError）；
    # `NODE_HTTP_POOL=0` 逐字节回退到下面的 racing_urlopen 分支。
    if http_pool.enabled():
        return http_pool.request(
            url,
            timeout=timeout,
            data=data,
            headers=auth,
            method=method,
            scope=scope,
            bucket=pool_bucket(request_kind() or _kind_from_url(url)),
        )
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


def node_probe(
    url: str,
    auth_key: str,
    timeout: float = STATUS_TIMEOUT_SEC,
    *,
    path: str = "/v1/ping",
) -> tuple[dict | None, str]:
    """探活 + **失败归因**：返回 `(info, why)`，成功时 `why == ""`。

    `node_ping`（采样 `/v1/ping`）与 `push_node_ping`（push `/ping`）都只是它的薄壳。

    为什么把原因带出来（2026-11-17 真机）：hub 的注册门对每台云机恒 422，而日志只有
    「无应答，或 authKey 不符」——`400`、连接超时、authKey 不符三种完全不同的故障被压成
    一句话，现场只能另起 curl 猜。归因串**只进日志**，不进任何 HTTP 应答；判定仍是
    `info is None`（逐字不变）。
    """
    try:
        status, body = node_request(url.rstrip("/") + path, auth_key, timeout)
        if status != 200:
            return None, f"HTTP {status}"
        # json.loads 返回 Any；节点探活契约固定为 dict。
        info: Any = json.loads(body.decode("utf-8"))
        if not isinstance(info, dict):
            return None, f"应答不是对象（{type(info).__name__}）"
        return info, ""
    except urllib.error.HTTPError as e:
        return None, f"HTTPError {e.code}"
    except PING_ERRORS as e:
        # 只吞**网络面**（`PING_ERRORS`）：程序员错（AttributeError/KeyError）必须穿出去，
        # 否则「节点全部不可用」会把自己的 bug 掩盖成一条正常结论（DECISIONS 2026-11-15）。
        return None, f"{type(e).__name__}: {e}"


def node_ping(url: str, auth_key: str, timeout: float = STATUS_TIMEOUT_SEC) -> dict | None:
    """GET /v1/ping → dict；任何失败返回 None（调用侧决定排除该节点）。"""
    return node_probe(url, auth_key, timeout)[0]


def push_node_ping(url: str, auth_key: str, timeout: float = 3.0) -> dict | None:
    """`GET /ping`（Bearer）→ dict；任何失败返回 None。

    为什么不能复用 `node_ping`：push 类节点（`gpu_push` / `offline_push`）跑的是
    `remote/worker_server.py`，它的探活面是 **`/ping`**（Bearer），不是 rollout agent 的
    `/v1/ping`（query token）。同一个 hub 同时伺候两种节点，判据必须按 `kind` 分流
    （`hub.admin._admin_nodes_register`）——两者共用 `node_probe` 的实现与归因。
    """
    return node_probe(url, auth_key, timeout, path="/ping")[0]


#: 进程级课程身份（环境变量名）。训练进程 = 一门课，`apply_course` 挂上后所有
#: 出站权重/任务都带它——agent 侧据此按 (course, kind) 分桶（多课程互不驱逐）。
#: 为什么走环境变量而不是逐调用点加参数：这 9 个出站点散在 6 个文件的不同闭包里，
#: 逐点穿参要改十几个签名且每加一个调用点就要记得再穿一次；而「这个进程是哪门课」
#: 是**进程级身份**，不是逐请求的参数（与 PYTHONPATH/PYTHONHASHSEED 同类）。
COURSE_ENV = "RL_COURSE_NAME"


def course_name_of() -> str:
    """本进程的课程短名（未设/空 → 空串 = 旧单课程桶，agent 侧两向兼容）。"""
    return os.environ.get(COURSE_ENV, "").strip()
