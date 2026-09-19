"""
dist_common.py — 分布式采样 trainer 侧公共工具（stdlib-only，可脱离 torch 独立测试）。

与 tools/agent/sampler-agent.ts 构成双语协议契约（plan/distributed-rollout.md v3.6）：

- codeHash 配方（两侧实现必须逐字节一致）：对 glob 集（`src/nn/**` 全部文件 +
  `tools/sim/export-rl-rollout.ts`）按 posix 相对路径字典序遍历，依次喂入
  sha256(path 字节) 与 sha256(文件内容)，最终 hex。
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
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "nn-training", "rl-config.json")

SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "a_move.npy",
    "a_fire.npy",
    "lp_move.npy",
    "lp_fire.npy",
    "value.npy",
    # plan/rl-training-config.md §4.2：per-tick shard 奖励改由 Python 公式引擎按
    # metrics.npy（[N+1,21] f8）计算 —— TS 侧不再落 reward.npy。
    "metrics.npy",
    "done.npy",
    "mask.npy",
)

# M8 意图 RL shard 清单（export-intent-rollout.ts 产物）——意图步 semi-MDP：
# inject（prev one-hot 8 + duration）与 dt（窗口时长 tick，变步长 GAE 用）替换
# a_move/a_fire/lp_move/lp_fire；mask 为 8 类死类掩码。
INTENT_SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "inject.npy",
    "a_intent.npy",
    "lp_intent.npy",
    "value.npy",
    "reward.npy",
    "done.npy",
    "mask.npy",
    "dt.npy",
)

# BC 语料 shard 清单（export-godai-bc.ts 产物，BC 整合 2026-09-13；与
# nn-training/data/npyio.py SHARD_FILES + OPTIONAL_FILES 同表——bc.py 装载口径）。
# manifest.json 不在此表（write_shard 单独落）。
BC_SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "actions.npy",
    "masks.npy",
    "conditions.npy",
    "returns.npy",
)

#: BC 语料任务的模式/能力标识：agent /v1/task ?mode=bc；shard manifest collector。
BC_MODE = "bc"
BC_COLLECTOR = "BC-GOD"
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
    判据在这里落地而不是调用方各自做字符串匹配 —— 与 rl/bc_dispatch 的 busy
    背压同源（那边 2026-09-14 事故：busy 被当故障熔断，整轮训练被打死）。
    """

    def __init__(self, status: int, reason: str, transient: bool = False) -> None:
        super().__init__(f"HTTP {status}: {reason}" if status else reason)
        self.status = status
        self.reason = reason
        self.transient = bool(transient)


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
    """sha256(weights.json 文件字节) —— 版本过滤键（语义：样本确由该权重产生）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_code_hash() -> str:
    entries = _collect_code_hash_files()
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    for rel, content in entries:
        h.update(rel.encode())
        h.update(hashlib.sha256(content).digest())
    return h.hexdigest()


CODE_HASH_MANIFEST = os.path.join(REPO_ROOT, "tools", "agent", "codehash-files.txt")


def _skip_codehash_dir(name: str) -> bool:
    """目录递归时的跳过规则（F3，plan/dist-codehash-stale-fix.md）：任一路径段以
    '.' 开头（隐藏项 .git/.DS_Store/.venv）或名为 __pycache__ / node_modules。
    与 tools/agent/codehash-files.ts isSkippedCodeHashDir 逐条对齐——契约写死在
    codehash-files.txt 头部注释，任一侧改动必须双侧同步。"""
    return name.startswith(".") or name in ("__pycache__", "node_modules")


def _skip_codehash_file(name: str) -> bool:
    """文件级跳过规则（F3）：隐藏项（. 开头）+ 编辑器/构建临时后缀。
    与 tools/agent/codehash-files.ts isSkippedCodeHashFile 逐条对齐。"""
    return name.startswith(".") or name.endswith(
        (".pyc", ".orig", ".rej", ".bak", ".tmp", ".log", ".swp", "~")
    )


def _collect_code_hash_files() -> list[tuple[str, bytes]]:
    """按 SSOT 清单 codehash-files.txt 展开 codeHash 文件集（与 sampler-agent.ts 同源）。

    清单每行一个条目：'#' 注释 / 空行忽略；以 '/' 结尾 = 目录（递归纳入其下所有
    文件，目录条目受 F3 噪声过滤）；其余 = 具体文件（相对 repo 根、posix 路径；
    不存在则跳过，单文件条目不过滤）。两侧读同一清单、按同一规则展开，杜绝单侧
    硬编码漂移（2026-09-01 事故教训）。
    """
    out: list[tuple[str, bytes]] = []
    try:
        with open(CODE_HASH_MANIFEST, encoding="utf-8") as f:
            specs = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return out
    for spec in specs:
        spec = spec.replace("\\", "/")
        if spec.endswith("/"):
            root = os.path.join(REPO_ROOT, *spec.rstrip("/").split("/"))
            for dirpath, dirnames, files in os.walk(root):
                # F3：跳过隐藏/缓存目录（剪枝不递归），再按文件规则过滤。
                dirnames[:] = [d for d in dirnames if not _skip_codehash_dir(d)]
                for name in files:
                    if _skip_codehash_file(name):
                        continue
                    p = os.path.join(dirpath, name)
                    rel = os.path.relpath(p, REPO_ROOT).replace("\\", "/")
                    with open(p, "rb") as f:
                        out.append((rel, f.read()))
        else:
            p = os.path.join(REPO_ROOT, *spec.split("/"))
            if os.path.isfile(p):
                rel = os.path.relpath(p, REPO_ROOT).replace("\\", "/")
                with open(p, "rb") as f:
                    out.append((rel, f.read()))
    return out


def code_hash_report() -> str:
    """codeHash 诊断报告（F4，plan/dist-codehash-stale-fix.md）：与
    tools/agent/codehash-report.ts 同格式的 TSV，供双侧 diff 定位 stale 差异。

    每行 `sha8\tsize\trelPath`（按 relPath 排序），末行 `codeHash=<full>`。
    用法：python -c "import dist_common;print(dist_common.code_hash_report())"
    """
    entries = _collect_code_hash_files()
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    lines: list[str] = []
    for rel, content in entries:
        h.update(rel.encode())
        h.update(hashlib.sha256(content).digest())
        lines.append(f"{hashlib.sha256(content).digest().hex()[:8]}\t{len(content)}\t{rel}")
    lines.append(f"codeHash={h.hexdigest()}")
    return "\n".join(lines)


# ---------------- 节点门指纹：唯一事实来源 = SSOT 清单（回 2026-09-17 用户指令） ----------------
# rollout 门（dispatch / rescan）与 eval 门（eval_dispatch / batch_eval）现在比的是
# **同一个值 = codeHash**（展开自 tools/agent/codehash-files.txt；引擎 src/game、config、
# RNG、God AI 已并入该清单）。先例（engine_epoch 掺 git commit）会让任何与 rollout/eval
# 无关的提交（dashboard / nn-training / docs）把全节点判 stale、逼运维重启 sampler-agent。
def compute_engine_epoch() -> str:
    """engine_epoch = sha256(codeHash)[:16]（codeHash 见 SSOT 清单；纯函数）。

    **这是账本记录值，不是节点门判据**（节点门比 codeHash，见 check_code_hash）：
    它进 EvalGameRow.engine / 心跳，供 S10 记录级漂移哨兵与跨轮可比性断言用。
    """
    return hashlib.sha256(compute_code_hash().encode()).hexdigest()[:16]


def check_code_hash(ping: dict, expected: str) -> str | None:
    """节点可用性判据（rollout 与 eval 同一门）：None=通过，否则拒收原因。

    2026-09-17：eval 侧改比 codeHash（原先比 ping.engineEpoch）——该字段已从
    /v1/ping 移除，engine_epoch 退为账本记录值。唯一事实来源 = SSOT 清单
    （tools/agent/codehash-files.txt），与 rollout 无关的提交不会让本门变红。
    """
    got = ping.get("codeHash")
    if not got:
        return "missing codeHash (old agent — sync code + restart)"
    if got != expected:
        return f"codeHash mismatch: node={str(got)[:8]} expected={expected[:8]}"
    return None


# ---------------- HTTP ----------------
#: 视为瞬断/背压的 HTTP 状态码（不是节点故障）。
TRANSIENT_HTTP_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
#: 无 status 的异常（节点 SDK 直抛）里的 busy 文案判据，与 rl/bc_dispatch.is_busy_hint 同源。
_BUSY_HINT = "busy"


def is_transient_error(e: BaseException) -> bool:
    """背压/瞬断判定（**单一实现**，A/B/C 三层共用）：True = 限流或网络抖动，
    调用方**不得**把它计入节点失败 streak。

    优先级：① `DistError.transient`（fetch_task 对 10054/超时/408-429-5xx 的标记）；
    ② `DistError.status ∈ TRANSIENT_HTTP_STATUS`；③ 文案含 busy。
    非 DistError 的 OSError/TimeoutError（ConnectionResetError、读超时）一律 transient。
    409「wver not cached」**不在**此列：它是可刷新条件（见 refresh_weights），
    既不是背压也不是节点故障。

    历史：本判据原只长在 B 层（rl/batch_eval.py），A 层（rollout）只豁免 503、
    训练干净评估层完全不豁免 —— 2026-09-19 实测 3 条 HTTP 502（cloudflared 隧道）
    就把 a97 熔断整轮（training-loop.log 09:48）。
    """
    if isinstance(e, DistError):
        if e.transient:
            return True
        if e.status in TRANSIENT_HTTP_STATUS:
            return True
        return _BUSY_HINT in str(e.reason)[:64].lower()
    if isinstance(e, OSError):  # ConnectionResetError / TimeoutError / URLError …
        return True
    return _BUSY_HINT in str(e)[:64].lower()


# ---------------- 收工即断连（2026-09-19 用户裁定第 4 条） ----------------
# 「竞速后 settled 一满，直接关闭所有节点的连接！！！立即！马上！right now！」
# urllib 无连接池可关，但每个在飞请求的 HTTPResponse 都能 close()：关掉它会让阻塞中的
# read 立刻抛（连接重置），而不是等慢节点把那一局算完（最长 taskTimeoutSec=900s）。
# **作用域 = 线程 tag**：训练主循环里 rollout 与本层同进程并发，全局关连接会把别人的在飞
# 请求一起打死（rollout 任务被当瞬断重排 = 白烧节点）⇒ 只关自己 tag 的请求。
_ACTIVE_LOCK = threading.Lock()
_ACTIVE: set[tuple[str, object]] = set()
_ABORTED_TAGS: set[str] = set()
_TAG = threading.local()


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
    with _ACTIVE_LOCK:
        _ABORTED_TAGS.add(scope)
        targets = [r for t, r in _ACTIVE if t == scope]
    if targets:

        def _closer() -> None:
            for r in targets:
                try:
                    r.close()  # type: ignore[attr-defined]
                except Exception:
                    pass

        threading.Thread(target=_closer, daemon=True, name="abort-close").start()
    return len(targets)


def clear_abort() -> None:
    """解除全部收工态（**每个单元开头必须调**；否则上一单元收工后请求立刻被拒）。"""
    with _ACTIVE_LOCK:
        _ABORTED_TAGS.clear()


def abort_scope(tag: str = "") -> bool:
    """该作用域是否处于收工态（回包无用 ⇒ 调用方丢弃而不是重排）。"""
    with _ACTIVE_LOCK:
        return (tag or request_tag()) in _ABORTED_TAGS


def _request(
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
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {auth_key}", **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        key = (scope, resp)
        with _ACTIVE_LOCK:
            _ACTIVE.add(key)
        try:
            return resp.status, resp.read()
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(key)


def ping_nodes_parallel(nodes: list, timeout: float = 3.0) -> list[dict | None]:
    """**并行** ping 一批节点（保序返回，失败 = None）。

    为什么必须并行：串行 ping 的墙钟 = Σ 每台延迟，而超时预算是**按节点**的
    （`statusTimeoutSec` 默认 3s）⇒ 两台负载高的节点就让整个阶段花 ~7s（2026-09-19
    实测：`u0 阶段 gate 6.83s alive=4/6`，其中两台正是超时被丢）。节点门在每单元
    重跑一次，这笔开销按单元累加。并行后 == 最慢一台。
    """
    if not nodes:
        return []
    from concurrent.futures import ThreadPoolExecutor

    def _one(nd: dict) -> dict | None:
        # 键名兼容 `key`（归一化配置，eval_dispatch/batch_eval 用的形态）与 `authKey`
        # （rl-config.json 原始行）——旧实现只认 authKey，把归一化过的配置喂进来会静默
        # 401 ⇒ 整批节点「ping 失败」（2026-09-19 B6 现场探针实测）。
        return node_ping(
            str(nd.get("url") or ""),
            str(nd.get("key") if nd.get("key") is not None else nd.get("authKey", "")),
            timeout=timeout,
        )

    with ThreadPoolExecutor(max_workers=min(8, len(nodes))) as ex:
        return list(ex.map(_one, nodes))


def node_ping(url: str, auth_key: str, timeout: float = 3.0) -> dict | None:
    """GET /v1/ping → dict；任何失败返回 None（调用侧决定排除该节点）。"""
    try:
        status, body = _request(url.rstrip("/") + "/v1/ping", auth_key, timeout)
        if status == 200:
            # json.loads 返回 Any；节点 /v1/ping 契约固定为 dict。
            info: dict = json.loads(body.decode("utf-8"))
            return info
    except Exception:
        return None
    return None


# 训练机当前分支（run_rl.py 启动时锁存）。节点远控升级**永远**用这个分支——
# rl-config 的 upgradeBranch 已废弃（残留旧战役分支名曾把全部节点 reset 回
# 旧代码，2026-08-30 事故）。queue/eval_dispatch 传来的 branch 参数为空时用它。
UPGRADE_BRANCH: str | None = None


def set_upgrade_branch(branch: str) -> None:
    global UPGRADE_BRANCH
    UPGRADE_BRANCH = branch


def is_self_node(url: str, node_id: str = "") -> bool:
    """self/回环节点：agent 由训练机同一工作区启动——代码与训练机**天然同源**。

    远控语义（2026-08-30 用户修订）：stale 时允许远控，但只做**纯重启**
    （request_upgrade 不带 pullBranch ⇒ agent 不做任何 git 操作，重启进程即
    拾取工作区新代码）；**禁止 pull**——共享工作区上的 git pull 是破坏性的
    （曾把本机 reset 回旧分支，2026-08-30 事故）。codeHash 正确时零动作。"""
    if node_id.strip().lower() == "self":
        return True
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def upgrade_branch_or(explicit: str | None) -> str:
    if explicit:
        return explicit
    return UPGRADE_BRANCH or ""


def request_upgrade(url: str, auth_key: str, branch: str, timeout: float = 20.0) -> bool:
    """主动升级机制（M8）：POST /v1/restart {pullBranch} → agent 端 git pull + 重启。

    编排层 ping 发现节点 codeHash 不符（stale）时调用——把它从「静默排除」升级为
    「主动指示更新重启」。返回 True = 接受（agent 异步执行，重启窗口内不可达，
    rescan 会在它恢复后按新 codeHash 纳入）。失败返回 False（不抛、不阻塞训练）。
    """
    try:
        status, _body = _request(
            url.rstrip("/") + "/v1/restart",
            auth_key,
            timeout,
            data=json.dumps({"pullBranch": branch}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return status in (200, 202)
    except Exception:
        return False


# ---------------- 远控重启护栏（2026-09-01 重启循环修复） ----------------
# 症状：① 远控重启过的进程被再次远控重启（无限循环，节点无法贡献）；
#       ② 用户手动更新代码重启的进程被远控杀掉再重启。
# 根因：expected codeHash 由训练机**工作区**文件内容算出——含未提交改动；远端
#   `git pull` 只能拿到已推送提交，永远无法收敛到该 hash ⇒ 每轮 ping 门 / rescan
#   都判 stale ⇒ 再杀再拉，无限循环（2026-09-01 实测四连杀）。叠加根因：旧实现的
#   去重集合 upgrade_requested 是每轮局部变量，新一轮迭代重新建集合，stale 节点
#   每轮、每次 rescan 都再收一次 restart。
# 处置（双层）：
#   - 脏工作区护栏：hash 集内有未提交改动时，拒绝对**远端**节点下发 pull+restart
#     （无效且具破坏性）；self/回环节点豁免（代码同源，纯重启即拾取工作区新代码）。
#   - 跨代去重：同一节点 + 同一 (agent codeHash, 期望 hash) 只下发一次 restart；
#     节点 hash 变化（pull 生效 / 手动更新）或训练机期望值变化（本机 commit/切分支/
#     改集内文件）后自动恢复资格（F1，plan/dist-codehash-stale-fix.md：dedup 键纳入
#     期望 hash——否则 mac 类字节差异稳定后训练机永远不再下发升级，运维 pull+重启
#     因 hash 不变反而永远无法重新纳管）。
_RESTART_SEEN: dict[str, tuple[str, str]] = {}  # nid -> (agent_ping_hash, expected_hash)


def reset_restart_state() -> None:
    """测试专用：清空跨代去重状态（模块级状态，测试间必须隔离）。"""
    _RESTART_SEEN.clear()


def seed_restart_state(entries: list) -> int:
    """把调用方持久化的跨代去重 memo 灌回 `_RESTART_SEEN`。返回灌入条数。

    一次性进程（CLI）专用：本模块的去重状态只活在进程内，调用方（TS 工具）把上次
    成功下发的 (nid, agent codeHash, 期望 hash) 存盘，下次调用前预置回来 ⇒ 守卫
    的 `dedup` 分支语义与常驻训练循环**逐字一致**，不必在调用方重写判据。
    每项须含 id / pingHash / expectedHash（全量 hex，不接受截断值）。
    """
    n = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        nid = str(e.get("id") or "").strip()
        ping_hash = str(e.get("pingHash") or "").strip()
        exp_hash = str(e.get("expectedHash") or "").strip()
        if not nid or not ping_hash or not exp_hash:
            continue
        _RESTART_SEEN[nid] = (ping_hash, exp_hash)
        n += 1
    return n


def _parse_porcelain(text: str) -> list[str]:
    """git status --porcelain v1 输出 → 路径列表（含改名目标、去引号）。"""
    out: list[str] = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        # 格式：XY<space>PATH；改名行形如 "R  old -> new"
        p = line[3:].split(" -> ", 1)[-1].strip().strip('"')
        if p:
            out.append(p)
    return out


def _git_index_blobs(rels: list[str]) -> dict[str, str]:
    """索引中 rel → blob sha 映射；未跟踪路径不出现在结果里。"""
    if not rels:
        return {}
    proc = subprocess.run(
        ["git", "ls-files", "-s", "--", *rels],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        **_POPEN_NO_WINDOW,
    )
    if proc.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        meta, raw_path = line.split("\t", 1)
        fields = meta.split()
        if len(fields) < 2:
            continue
        out[raw_path.strip().strip('"').replace("\\", "/")] = fields[1]
    return out


def _git_cat_blobs(shas: list[str]) -> dict[str, bytes]:
    """批量取 blob 原始字节（git cat-file --batch）；缺失的 sha 不进结果。"""
    if not shas:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=REPO_ROOT,
        input=("\n".join(shas) + "\n").encode(),
        capture_output=True,
        timeout=30,
        **_POPEN_NO_WINDOW,
    )
    out: dict[str, bytes] = {}
    buf = proc.stdout
    pos = 0
    while pos < len(buf):
        nl = buf.find(b"\n", pos)
        if nl < 0:
            break
        parts = buf[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(parts) < 3:
            continue  # "<sha> missing"
        oid, size = parts[0], int(parts[2])
        out[oid] = buf[pos : pos + size]
        pos += size + 1
    return out


def dirty_hash_files() -> list[str]:
    """codeHash 覆盖集中「工作区字节 ≠ git 索引内容」的文件（含未跟踪文件）。

    非空 ⇒ 期望 codeHash 含 pull 拿不到的本地字节，远端 git pull 永远无法收敛——
    此时对远端节点下发 restart+pull 只会产生无效扰动（拉到同样的提交、hash 依旧
    不等 → 下轮再杀，无限重启循环）。返回空列表 = 集内代码与索引逐字节一致。

    判据用**原始字节 vs 索引 blob 字节**，不用 git status：core.autocrlf=input 会把
    工作区 CRLF 规范化成 LF 再比对，「工作区 CRLF / 索引 LF」这类纯 EOL 污染被完全
    隐藏（2026-09-09 mac 事故：git status 干净、dirty=[]，但 codeHash 与远端干净
    checkout 只差一个 src/nn/wasm/conv_feats.c 的 CRLF，节点卡了 40 分钟）。

    未跟踪文件同样计为 dirty——远端 pull 不会带出该文件。git 不可用/出错时返回 []
    （保守放行）——跨代去重护栏仍兜底防循环。
    """
    entries = _collect_code_hash_files()
    if not entries:
        return []
    try:
        idx = _git_index_blobs([rel for rel, _ in entries])
        blobs = _git_cat_blobs(sorted(set(idx.values())))
    except Exception:
        return []
    dirty: list[str] = []
    for rel, content in entries:
        sha = idx.get(rel)
        if sha is None or blobs.get(sha) != content:
            dirty.append(rel)
    return dirty


def request_upgrade_guarded(
    nid: str,
    url: str,
    auth_key: str,
    branch: str,
    ping_hash: str,
    timeout: float = 20.0,
    dirty: list[str] | None = None,
    expected_hash: str = "",
) -> tuple[bool, str]:
    """带护栏的重启请求（ping 门 / rescan / upgrade_stale_nodes 共用入口）。

    返回 (ok, reason)：
      restart-requested  — 已下发，节点将 pull + 重启（本轮生效）
      dedup              — 同节点同 (agent codeHash, 期望 hash) 已重启过，跳过
                           （防连环杀；期望 hash 变化 ⇒ 允许再发一次，F1）
      dirty-tree:<n>     — 期望 hash 含 n 个未提交文件，远端 pull 永不收敛，拒发
      restart-failed     — agent 拒绝（409 grace / 5xx）或不可达；不写去重状态
                           （协调器下轮 rescan 自动重试）
    self/回环节点：永远**纯重启**（branch 强制空——共享工作区禁 pull），不受
    脏工作区护栏限制，去重同样生效。
    dirty 显式传参供测试与调用方复用每轮已检测结果；None = 自动检测。
    expected_hash 默认 "" 使未传参调用方行为与旧版逐字等价（向后兼容）；调用方
    必须显式传训练机当前 codeHash（F1 核心：dedup 键含期望值）。
    """
    if dirty is None:
        dirty = [] if is_self_node(url, nid) else dirty_hash_files()
    if not is_self_node(url, nid) and dirty:
        return False, f"dirty-tree:{len(dirty)}"
    key = (ping_hash, expected_hash)
    if _RESTART_SEEN.get(nid) == key:
        return False, "dedup"
    actual_branch = "" if is_self_node(url, nid) else branch
    ok = request_upgrade(url, auth_key, actual_branch, timeout=timeout)
    if ok:
        _RESTART_SEEN[nid] = key
    return ok, ("restart-requested" if ok else "restart-failed")


def upgrade_stale_nodes(
    cfg: dict,
    expected_hash: str,
    branch: str,
    status_timeout: float = 3.0,
    restart_timeout: float = 20.0,
    max_nodes: int = 16,
    dirty: list[str] | None = None,
) -> list[dict]:
    """主动升级扫描：ping 每个 enabled 节点，codeHash ≠ expected（stale）→
    request_upgrade_guarded（跨代去重 + 脏工作区拒发，2026-09-01 重启循环修复）。

    返回 [{id, upgraded, reason}]（诊断用）。不可达/失败跳过——升级机制是 best-effort，
    绝不阻塞本轮 dispatch（stale 节点本轮仍不参与，待重启后由 rescan 纳入）。
    """
    out: list[dict] = []
    for n in cfg.get("nodes", [])[:max_nodes]:
        if not n.get("enabled", True):
            continue
        nid = str(n.get("id") or n.get("url") or "?")
        ping = node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
        if ping is None:
            out.append({"id": nid, "upgraded": False, "reason": "unreachable"})
            continue
        ping_hash = str(ping.get("codeHash"))
        if ping_hash == expected_hash:
            out.append({"id": nid, "upgraded": False, "reason": "current"})
            continue
        ok, reason = request_upgrade_guarded(
            nid,
            n["url"],
            n.get("authKey", ""),
            branch,
            ping_hash,
            timeout=restart_timeout,
            dirty=dirty,
            expected_hash=expected_hash,
        )
        # pingHash 回传给调用方：一次性 CLI 要靠它把本次下发写进跨调用 memo
        #（键 = (nid, agent ping hash, 期望 hash)，与 _RESTART_SEEN 同构）。
        out.append(
            {
                "id": nid,
                "upgraded": ok,
                "reason": reason,
                "pingHash": ping_hash,
                "agentVersion": ping.get("agentVersion"),
            }
        )
    return out


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
        status, body = _request(
            url.rstrip("/") + "/v1/weights/cached",
            auth_key,
            timeout,
            headers={"X-Weights-Sha256": sha, "X-Kind": kind},
            method="GET",
        )
    except Exception:
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


# 进程内「已成功下发过该 wver」缓存（volume 同 it 补波复用；跨 it 换 wver 天然失效）。
# 值 = 成功 POST 过的 node id（键 = (kind, wver)，见下）。失效：ping/codeHash/bun 门 exclude
# 时调用 forget_weights_node。局限：同 codeHash 手动重启可能残留脏缓存（同 it 窗口内罕见）。
# 键 = (kind, wver)。**必须带 kind**：节点侧按 kind 分桶缓存权重，而同一个权重文件
# （同一 sha）会被多条腿使用——训练 rollout 用 'rollout'，其干净评估用 'eval'
# （2026-09-19 B6）。不带 kind 时先跑的那条腿的 note 会让另一条腿误判「已下发」
# 而跳过 POST ⇒ 该节点对另一条腿整轮 409（脏缓存，与 A1 同类陷阱、方向相反）。
_WEIGHTS_PUSHED: dict[tuple[str, str], set[str]] = {}


def weights_push_cache_reset() -> None:
    """测试/运维：清空进程内权重下发缓存。"""
    _WEIGHTS_PUSHED.clear()


def note_weights_pushed(wver: str, node_id: str, kind: str = "rollout") -> None:
    if wver and node_id:
        _WEIGHTS_PUSHED.setdefault((kind, wver), set()).add(node_id)


def forget_weights_node(node_id: str) -> None:
    if not node_id:
        return
    for s in _WEIGHTS_PUSHED.values():
        s.discard(node_id)


def weights_already_pushed(wver: str, node_id: str, kind: str = "rollout") -> bool:
    return bool(node_id) and node_id in _WEIGHTS_PUSHED.get((kind, wver), ())


def post_weights(
    url: str,
    auth_key: str,
    iter_id: str,
    sha: str,
    weights_bytes: bytes,
    timeout: float = 120.0,
    kind: str = "rollout",
) -> str:
    """POST /v1/weights → 'kept' | 'purged'；失败抛 DistError。

    kind（v3.7/M8）：'rollout'（per-tick RL 采样）/ 'intent'（意图权重桶——
    intent-exec 评估 + 意图 RL rollout 共用）。agent 按 x-kind 分桶缓存。

    kept 短路径（2026-09-19，x20-rebirth it19 rollout 208s 复盘）：先探针
    GET /v1/weights/cached；sha 已在桶内 → 不传 body 直接 'kept'（补波/同 it
    多波的权重握手主成本）。探针 404/失败（旧 agent）→ 完整 POST，语义不变。
    """
    cached = probe_weights_cached(url, auth_key, sha, kind=kind, timeout=min(5.0, timeout))
    if cached is True:
        return "kept"
    status, body = _request(
        url.rstrip("/") + "/v1/weights",
        auth_key,
        timeout,
        data=gzip.compress(weights_bytes),
        headers={
            "Content-Encoding": "gzip",
            "X-Iter-Id": iter_id,
            "X-Weights-Sha256": sha,
            "X-Kind": kind,
        },
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


def post_weights_parallel(
    nodes: list,
    iter_id: str,
    wver: str,
    weights_bytes: bytes,
    timeout: float,
    kind: str = "rollout",
    log=None,
    on_alive=None,
) -> list:
    """并行 POST 权重到全部节点 → alive 节点列表（保持入参顺序）。

    失败节点记日志后排除（与串行版语义一致）。**边分发边开采**（2026-09-19）：
    `on_alive(nd)` 在**该节点 POST 成功的瞬间**于 worker 线程回调（须线程安全），
    调用方可立刻孵化该节点采样线程，无需等其它节点。返回值仍按入参顺序。
    日志在每节点完成时输出。pure_collect 锚点由调用方定义（用户 2026-09-19：
    权重开始分发 → 样本齐可交 PPO）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not nodes:
        return []

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
        except DistError as e:
            return nid, nd, None, e

    n = len(nodes)
    ok_ids: set[str] = set()
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(8, n)) as ex:
        futs = {ex.submit(_push, nd): nd for nd in nodes}
        t_sub: dict[int, float] = {id(f): time.monotonic() for f in futs}
        for fut in as_completed(futs):
            nid, nd, mode, err = fut.result()
            # 事件：**该节点权重就绪时刻**（用户 2026-09-19：排查「等十几秒 CPU 才满」
            # 与评测中途的掉档——没有这条就看不出权重阶段占了多久）。
            dt = time.monotonic() - t_sub[id(fut)]
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
            f"in {time.monotonic() - t0:.2f}s (sha {wver[:12]}…)"
        )
    return [nd for nd in nodes if str(nd.get("id") or nd.get("url") or "?") in ok_ids]


def rollout_collect_sec(t_dist_start: float | None, last_settle: float | None) -> float | None:
    """rollout 采集耗时（用户口径 2026-09-19）。

    起点 = **权重就绪开始分发**；终点 = **所有样本采集完毕可交 PPO**（末局结算）。
    边分发边开采下该值**包含**与采集重叠的分发墙钟——这正是用户要的端到端口径。
    """
    if t_dist_start is None or last_settle is None:
        return None
    return round(last_settle - t_dist_start, 1)


def partition_weights_nodes(
    nodes: list, wver: str, kind: str = "rollout"
) -> tuple[list, list]:
    """按进程内缓存把节点拆成 (reuse, need)：reuse 跳过 POST，need 要下发。

    volume 同 it 补波：权重不变，首波已成功的节点进 reuse。kind 决定取哪条腿的账（缺省 'rollout'）；ping/codeHash 门
    exclude 的节点须先 forget_weights_node，否则可能带着脏缓存进 reuse。
    """
    reuse = [
        nd
        for nd in nodes
        if weights_already_pushed(wver, str(nd.get("id") or nd.get("url") or "?"), kind=kind)
    ]
    need = [
        nd
        for nd in nodes
        if not weights_already_pushed(wver, str(nd.get("id") or nd.get("url") or "?"), kind=kind)
    ]
    return reuse, need


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
    replan: int = 0,
    reward: str = "",
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
    if course_fp:
        params["courseFp"] = course_fp
    if wins is not None:
        params["wins"] = wins
    if near_miss_times is not None:
        params["nearMissTimes"] = near_miss_times
    qs = urllib.parse.urlencode(params)
    base = url.rstrip("/")
    started = time.monotonic()
    try:
        use_async = abandon_event is not None or os.environ.get("DIST_TASK_ASYNC") == "1"
        hdrs = {"x-async": "1"} if use_async else None
        status, body = _request(f"{base}/v1/task?{qs}", auth_key, timeout, headers=hdrs)
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
    qs = urllib.parse.urlencode(qparams)
    deadline = time.monotonic() + max(1.0, budget)
    while True:
        if abandon_event is not None and abandon_event.is_set():
            raise DistError(0, "abandoned: all tasks settled — race-loser copy dropped")
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise DistError(0, "async result deadline exceeded (game still running on node?)")
        try:
            status, body = _request(
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
                raise DistError(404, f"task lost on node (restart/purge): {detail}") from e
            raise DistError(e.code, detail) from e
        except Exception:
            # 瞬断（休眠/SSH 重连/隧道抖动）：结果仍在节点缓存里，睡一下继续拉。
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


# ---------------- 结果校验（先验后落盘的红线所在） ----------------
def _shard_files_for(manifest: dict) -> tuple:
    """BC 语料 shard（collector=BC-GOD）用 BC_SHARD_FILES；意图 RL shard（collector=
    INTENT-RL）用 INTENT_SHARD_FILES；其余 per-tick SHARD_FILES。"""
    if manifest.get("collector") == BC_COLLECTOR:
        return BC_SHARD_FILES
    if manifest.get("collector") == "INTENT-RL" or "a_intent.npy" in manifest:
        return INTENT_SHARD_FILES
    return SHARD_FILES


def validate_result(
    manifest: dict,
    files: dict,
    expected_wver: str,
    expected_pairs: set[tuple[int, int]],
    seen_keys: set[tuple[int, int]],
) -> str | None:
    """返回 None=通过；否则给出拒收原因。"""
    if not isinstance(manifest, dict):
        return "manifest is not an object"
    if manifest.get("wver") != expected_wver:
        return f"wver mismatch: got {manifest.get('wver')!r}"
    key = (manifest.get("stage"), manifest.get("seed"))
    if key not in expected_pairs:
        return f"unexpected (stage,seed)={key}"
    if key in seen_keys:
        return f"duplicate (stage,seed)={key}"
    # BC wins-only 败局：合法"跳过"结果（kept:false 空容器），不是任务失败。
    if manifest.get("collector") == BC_COLLECTOR and manifest.get("kept") is False:
        if files:
            return f"bc loss-skip shard must carry no files (got {sorted(files)})"
        return None
    want = _shard_files_for(manifest)
    if set(files.keys()) != set(want):
        extra = sorted(set(files) - set(want))
        lack = sorted(set(want) - set(files))
        return f"file set mismatch (extra={extra}, missing={lack})"
    for name, val in files.items():
        try:
            # v2 容器值为原始 bytes；v1 旧 agent 值为 base64 str——双模兼容。
            raw = (
                val if isinstance(val, (bytes, bytearray)) else base64.b64decode(val, validate=True)
            )
        except Exception:
            return f"{name}: invalid base64"
        if len(raw) == 0:
            return f"{name}: empty payload"
    return None


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


def write_shard(files: dict, manifest: dict, out_dir: str) -> None:
    """校验通过后的唯一落盘出口：目录名沿用 rl_s{si}_seed{seed} 布局。

    2026-09-03 修正：补写 manifest.json——M1 metrics 方案下 engine 加载器
    （ppo.engine.load_shard → _reward_from_metrics）需要 outcome/score/metrics_version
    在**落盘目录内**（分布式/self-node 局的单局 manifest 此前只存在于 fetch 返回的
    内存对象，落盘即丢 → 被当成 timeout 错标，奖励错算）。queue_local 路径由 exporter
    直接写盘不受影响；此处补齐 dist 路径两侧同规。
    """
    os.makedirs(out_dir, exist_ok=True)
    for name in _shard_files_for(manifest):
        val = files[name]
        raw = val if isinstance(val, (bytes, bytearray)) else base64.b64decode(val, validate=True)
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(raw)
    # 2026-09-05 修复（F8.3 / plan/remote-ppo-architecture.md §11）：此前**双写**
    # manifest.json（先紧凑再 indent=2，第二次覆盖第一次）——冗余 IO + 双写窗口
    # 无谓暴露（中途崩溃留半写文件）。只保留 indent=2 写（与 TS 侧 exporter 同规），
    # 磁盘产物字节不变（旧代码最终落盘的就是 indent=2 版本）。
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
