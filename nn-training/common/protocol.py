"""common/protocol.py — remote PPO job protocol (pure, torch-free, stdlib-only).

Single source of truth for the wire contract between the hub (TrainingLoop remote
branch + hub-server) and the cloud worker (`python -m remote_worker`). Design doc:
`plan/remote-ppo-architecture.md` §6 (D1/D5/D6/D9/D12), manifest fields §13 附录 A.

Contents (all pure functions / constants — no torch, no ppo import):
  * manifest validation (required-field fail fast, unknown fields ignored)
  * `data_fp` — sha256 over sorted shard relative paths + each manifest's
    {wver, stage, seed} (D1; local recompute == manifest value on both sides)
  * payload zip pack/unpack (shard dirs + manifest.json), with `payload_sha256`
  * idempotency key = (runId, course_fp, it, init_weights_fp, data_fp) (D1 +
    2026-09-24 job 身份事故：**课程身份必须进键**，否则单进程多课程下两门课共享一个
    job_id ⇒ hub 的首匹配路由让两个 trainer 读到同一份结果）
  * per-job deterministic numpy seed = hash(runId, it, init_weights_fp) (D5)
  * result envelope validation (weights_json + opt_tar + agg + commit_echo)
  * auth: bearer token header name (D9)

Rationale for torch-free: the hub (TrainingLoop remote branch + hub-server) must
never import torch (D2) — this module is imported by both hub-side and worker-side
code, so it must be importable with zero torch/numpy cost (numpy is acceptable;
test_no_torch_on_import guards the `import trainer.run_rl` chain).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

# ------------------------------------------------------------------ 下沉（S5 第六刀，2026-09-27）
# 「失败类型族」→ `common/errors.py`；「传输编码 / v1+v2 线格式」→ `common/wire_codec.py`。
# 实现搬家、名字留门面 ⇒ 全仓 `from common.protocol import …` 一行不改。
# ⚠ `errors` 是**叶子**（零 import）——`wire_codec` 与 `protocol` 都向下依赖它，勿把边反过来。
from common.errors import (
    CodeChangedError as CodeChangedError,
)
from common.errors import (
    JobCancelledError as JobCancelledError,
)
from common.errors import (
    JobFailedError as JobFailedError,
)
from common.errors import (
    ProtocolError as ProtocolError,
)
from common.errors import (
    RetryableError as RetryableError,
)
from common.errors import (
    UnreapableChildError as UnreapableChildError,
)
from common.manifest import (
    _ITER_PATH_FLAGS as _ITER_PATH_FLAGS,
)
from common.manifest import (
    _WIN_DRIVE_RE as _WIN_DRIVE_RE,
)
from common.manifest import (
    EVAL_SCRIPT as EVAL_SCRIPT,
)
from common.manifest import (
    GOAL_SCRIPT as GOAL_SCRIPT,
)
from common.manifest import (
    INTENT_SCRIPT as INTENT_SCRIPT,
)
from common.manifest import (
    ITER_NODE_LABEL as ITER_NODE_LABEL,
)
from common.manifest import (
    ITER_OUT_REL as ITER_OUT_REL,
)
from common.manifest import (
    KIND_ROLES as KIND_ROLES,
)
from common.manifest import (
    MANIFEST_BC_EXEMPT as MANIFEST_BC_EXEMPT,
)
from common.manifest import (
    MANIFEST_BC_EXTRA as MANIFEST_BC_EXTRA,
)
from common.manifest import (
    MANIFEST_ITER_EXTRA as MANIFEST_ITER_EXTRA,
)
from common.manifest import (
    MANIFEST_KINDS as MANIFEST_KINDS,
)
from common.manifest import (
    MANIFEST_OPTIONAL_DEFAULTS as MANIFEST_OPTIONAL_DEFAULTS,
)
from common.manifest import (
    MANIFEST_REQUIRED as MANIFEST_REQUIRED,
)
from common.manifest import (
    MANIFEST_RUN_EXTRA as MANIFEST_RUN_EXTRA,
)
from common.manifest import (
    PLAN_NAME as PLAN_NAME,
)
from common.manifest import (
    PLAN_PROTO as PLAN_PROTO,
)
from common.manifest import (
    PROTO as PROTO,
)
from common.manifest import (
    ROLE_FIELD as ROLE_FIELD,
)
from common.manifest import (
    ROLE_OFFLINE as ROLE_OFFLINE,
)
from common.manifest import (
    ROLE_ONLINE as ROLE_ONLINE,
)
from common.manifest import (
    ROLES as ROLES,
)
from common.manifest import (
    ROLLOUT_SCRIPT as ROLLOUT_SCRIPT,
)
from common.manifest import (
    ROLLOUT_SCRIPTS as ROLLOUT_SCRIPTS,
)
from common.manifest import (
    ROLLOUT_SPEC_DEFAULTS as ROLLOUT_SPEC_DEFAULTS,
)
from common.manifest import (
    RUN_MAX_ITERS_HARD_CAP as RUN_MAX_ITERS_HARD_CAP,
)
from common.manifest import (
    RUN_NODE_LABEL as RUN_NODE_LABEL,
)
from common.manifest import (
    SERVE_ANY_SCRIPT as SERVE_ANY_SCRIPT,
)
from common.manifest import (
    SERVE_MODE_BY_SCRIPT as SERVE_MODE_BY_SCRIPT,
)

# ------------------------------------------------------------------ 下沉（S5 第九刀，2026-09-27）
# 「job manifest 契约」→ `common/manifest.py`（proto/role 词汇 · `MANIFEST_*` schema ·
# kind→role · TS/plan 产物契约 · rollout 规格校验 · shard 命名与 `data_fp`）。
# 实现搬家、名字留门面 ⇒ 全仓 `from common.protocol import …` 一行不改。
# ⚠ `ROLE_HEADER` 与 `role_from_header` 仍住本模块（**请求头**面，不是 manifest）。
from common.manifest import (
    DataFpEntries as DataFpEntries,
)
from common.manifest import (
    _iter_flag_value as _iter_flag_value,
)
from common.manifest import (
    _iter_rel_path as _iter_rel_path,
)
from common.manifest import (
    d14_corpus_match as d14_corpus_match,
)
from common.manifest import (
    data_fp as data_fp,
)
from common.manifest import (
    data_fp_entries as data_fp_entries,
)
from common.manifest import (
    iter_declared_entries as iter_declared_entries,
)
from common.manifest import (
    iter_expected_data_fp as iter_expected_data_fp,
)
from common.manifest import (
    normalize_manifest as normalize_manifest,
)
from common.manifest import (
    parse_shard_name as parse_shard_name,
)
from common.manifest import (
    role_of as role_of,
)
from common.manifest import (
    serve_mode_for as serve_mode_for,
)
from common.manifest import (
    shard_name as shard_name,
)
from common.manifest import (
    validate_rollout_spec as validate_rollout_spec,
)
from common.payload import (
    INIT_WEIGHTS_NAME as INIT_WEIGHTS_NAME,
)
from common.payload import (
    PAYLOAD_LEGACY_NAMES as PAYLOAD_LEGACY_NAMES,
)
from common.payload import (
    PAYLOAD_NAME as PAYLOAD_NAME,
)
from common.payload import (
    PAYLOAD_PERTURB_NAME as PAYLOAD_PERTURB_NAME,
)
from common.payload import (
    PAYLOAD_XZ_PRESET as PAYLOAD_XZ_PRESET,
)
from common.payload import (
    _add_bytes as _add_bytes,
)
from common.payload import (
    _extract_archive as _extract_archive,
)
from common.payload import (
    find_payload as find_payload,
)
from common.payload import (
    pack_payload as pack_payload,
)
from common.payload import (
    unpack_payload as unpack_payload,
)
from common.wire_codec import (
    _GZIP_MAGIC as _GZIP_MAGIC,
)
from common.wire_codec import (
    _HDR_LEN_BYTES as _HDR_LEN_BYTES,
)
from common.wire_codec import (
    _WIRE_GZIP_LEVEL as _WIRE_GZIP_LEVEL,
)
from common.wire_codec import (
    BLOB_FIELDS as BLOB_FIELDS,
)
from common.wire_codec import (
    WIRE_JOB_CONTENT_TYPE as WIRE_JOB_CONTENT_TYPE,
)
from common.wire_codec import (
    WIRE_JOB_MAGIC as WIRE_JOB_MAGIC,
)
from common.wire_codec import (
    WIRE_V2_CONTENT_TYPE as WIRE_V2_CONTENT_TYPE,
)
from common.wire_codec import (
    WIRE_V2_MAGIC as WIRE_V2_MAGIC,
)
from common.wire_codec import (
    _pack_wire as _pack_wire,
)
from common.wire_codec import (
    _unpack_wire as _unpack_wire,
)
from common.wire_codec import (
    decode_opt_tar as decode_opt_tar,
)
from common.wire_codec import (
    decode_weights_json as decode_weights_json,
)
from common.wire_codec import (
    encode_opt_tar as encode_opt_tar,
)
from common.wire_codec import (
    encode_weights_json as encode_weights_json,
)
from common.wire_codec import (
    pack_job_v2 as pack_job_v2,
)
from common.wire_codec import (
    pack_result_v2 as pack_result_v2,
)
from common.wire_codec import (
    unpack_job_v2 as unpack_job_v2,
)
from common.wire_codec import (
    unpack_result_v2 as unpack_result_v2,
)

# ------------------------------------------------------------------ constants

# §343（2026-09-06）竞速广播 → P3b（2026-09-12，DECISIONS supersede §343）改回
# 独占加超时：领取即设租约（CLAIM_TTL_SEC），心跳续租，过期回池。LEASE_SEC 只留
# 旧租约兼容读；HEARTBEAT_SEC 仍是 worker 心跳周期。hub 重启即丢租约（首写锁定兜底）、
# D8 账本重建语义不变。
LEASE_SEC = 30 * 60  # （兼容）旧租约时长；现行调度只认 CLAIM_TTL_SEC
#: 独占租约 TTL（plan multi-course-parallel-training P3b §3.9：supersede §343——
#: 多 worker 时竞速广播改独占加超时）。领取即设租约（owner + expiry 同时置），
#: 心跳 60s 续租，TTL 内无心跳 → 回池。LEASE_SEC 只留旧租约兼容读。
CLAIM_TTL_SEC = 300
HEARTBEAT_SEC = 60  # （兼容）旧心跳周期；仅旧租约模式 hub 的 worker 心跳线程使用
AUTH_HEADER = "Authorization"  # Bearer <token>（D9；token 永不落盘/落日志）

#: worker → hub：worker 身份（hostname:pid）——hub 靠它数「有几个**不同**的 worker」
#: （隧道回源把全流量归成 127.0.0.1，源 IP 在此不可用）。
WORKER_ID_HEADER = "X-Worker-Id"
#: 「还在轮询」的判定窗口（秒）：超过它没再出现过就当该 worker 已离场，不参与判定。
#: （2026-09-22 P3 竞速退役：本窗口现在**只**服务 `active_worker_count()` 的避让链。）
WORKER_SEEN_WINDOW_SEC = 180.0

# ---- 归属角色（role）：本会话属于哪块盘（2026-09-25，语义从「能力」升级）----
# 原来是「能力声明」（`--offline` = 我能自主跑完整段），现在是**归属声明**。
# 为什么必须换（2026-09-25 云机接错盘的事故，plan/online-offline-role-routing.plan.md §1）：
# `kind=run`（整段）**确实**由 tailscale 盘跑得动——它有能力；事故正是「有能力的盘接了不
# 属于它的整段 job」。能力闸拦不住接错盘 ⇒ 判据必须是**角色**（该由哪块盘执行）。
# 头名与取值**保持逐字节不变**（`X-Battle-Offline: 1`）：混合部署里旧 hub/旧 worker 用同一
# 份字面量，改名只会让带标 worker 在旧 hub 上静默掉线，而“归属判断是否正确”与名字无关。
#: 角色头（peek / claim / 轮询面通用）：带它 = 本会话属于**离线盘**；缺席 = 在线盘。
ROLE_HEADER = "X-Battle-Offline"
#: 头的规范值（历史值 `1`；解析同时接受角色字面量 `offline`）。
ROLE_HEADER_VALUE = "1"


def role_from_header(raw: object) -> str:
    """角色头 → 角色（缺头 / 空 / `0` / `false` 一律 = **online**）。

    只认白名单真值（不做「非空即有」这类宽松推断）：判错的方向是明确的——低估只是少一个
    能领离线活的人（看得见：队列不降），高估会让在线盘的 worker 领走离线盘的活（看不见）。
    旧 worker 从不带这条头 ⇒ 它们一律按 online 处理（行为与今天一致）。
    """
    val = str(raw or "").strip().lower()
    if val == ROLE_OFFLINE or val in ("1", "true", "yes", "on"):
        return ROLE_OFFLINE
    return ROLE_ONLINE


def rotation_order(order: Sequence[str], start: str | None) -> list[str]:
    """跨课程轮转顺序：从 `start`（上次派发过的课程）的**下一门**开始绕一圈。

    为什么需要它：每课程一条 FIFO，若每次都从 order[0] 开始扫，第一门课的积压会把
    其它课程饿死（20 轮 backlog 的课能把 5 课程机群变成单课程机群）。`start` 不在表里
    （课程被摘除/首次派发）⇒ 原序。纯函数，可单测。
    """
    o = [c for c in order]
    if start is None or start not in o:
        return o
    i = (o.index(start) + 1) % len(o)
    return o[i:] + o[:i]


def may_avoid_stale_holder(requester: str, active_workers: int) -> bool:
    """**是否允许**把「上一份死租约」从它的前持有人手里推开（避让的闸，纯函数）。

    用户口径（2026-09-18）：job 超时回落队首后「改为推送其它 worker」。两个条件：

      · 请求者**有身份**——身份未知（旧 worker / 手写 curl）时无从避让，保守照派；
      · **还有别的活跃 worker** 可以接手——独苗时恒 False。这条是防停摆的硬条件：
        机群里只剩一个工人时，它自己超时过的 job 若也避让，就谁都领不到了（那台
        worker 永远空转，而唯一能干活的就是它）。

    注意分工：**身份是否真是前持有人由 `_JobStore.claim` 判断**（只有它知道租约回收
    后的 stale 记录），本函数只管「允不允许避让」。这么拆是因为在队列层先读 stale 记录
    再判身份会踩时序：那一刻过期租约还没被回收，stale 记录还是空的 ⇒ 避让永远不生效
    （2026-09-18 实测：写本函数的第一版就是这个顺序，回归测试当场抓出来）。
    """
    return bool(requester) and int(active_workers) >= 2


# ---- 调度优先级（2026-09-22，plan/transfer-scheduling §1.4 / §2.3）-----------------
# 背景：多课程并行下网络传输 ≈ rollout 耗时，串行「claim → 下载 → PPO → 回传」把 GPU 饿死
# 在传输上。解法 = worker 软持有预取 + job 边界问询优先级 + landed 是唯一硬取消信号。
#: 领取模式：`exclusive` = 独占（设租约，正常的「这活归我」）；
#: `backup` = **显式授权的**重复计算（软持有/掉队救援的落地面，无租约）。
#: 为什么必须把 backup 留成一个**模式**而不是删掉：它是「多卡空转防护」的唯一实现面
#: ——删了就没有任何合法途径让空闲的卡去算别人正在算的活（用户 2026-09-22 拍板）。
CLAIM_MODE_EXCLUSIVE = "exclusive"
CLAIM_MODE_BACKUP = "backup"
CLAIM_MODES = (CLAIM_MODE_EXCLUSIVE, CLAIM_MODE_BACKUP)

#: 优先级档位（§1.4 表）。`none` **同时是取消信号**：worker 拿它就地丢弃本地副本。
PRIORITY_NONE = "none"
PRIORITY_LOW = "low"
PRIORITY_MEDIUM = "medium"
PRIORITY_HIGH = "high"
PRIORITY_HIGHEST = "highest"
PRIORITY_ORDER = (PRIORITY_NONE, PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH, PRIORITY_HIGHEST)

#: 掉队阈值（秒）：超它就把该 job 提到 high（空闲卡去开备份救援），缺省 = 正常 PPO 一轮的 3×。
STRAGGLER_SEC = 180.0

#: cancel-watcher 轮询 `GET /jobs/{id}/status` 的间隔（秒）：1–2s 是用户口径。
#: 它走 P0 小包，与 bulk 不同队列；太密会把 hub 的线程池当健康检查用（每 worker 每秒一请求）。
JOB_CANCEL_POLL_SEC = 1.5


def job_priority(
    *,
    landed: bool,
    ready_elsewhere: bool,
    claimed_elsewhere: bool,
    computing_elsewhere_since: float | None,
    now: float,
    straggler_sec: float = STRAGGLER_SEC,
) -> str:
    """单份 job 的优先级（纯函数，可单测；§1.4 表的唯一实现）。

    输入**必须只描述「别人」**（问询者视角）：自己手里那份 computing 不叫「别处在算」，
    否则每个 worker 都会把自己判成中，highest 永远发不出去。

    两把时钟（R2-C1，实施期澄清）：
      · `claimed_elsewhere` = 有人 exclusive claim 了（=「承诺在跑」，但可能还在下载）；
      · `computing_elsewhere_since` = `computing_at`（`/jobs/{id}/start` 打点，PPO 真启动了）。
    掉队阈值**只认后者**：拿 claim 起算会把「下载慢」误判成「算得慢」，于是多开备份
    把本来就慢的链路压得更死（§5 test_priority_rpc 钉这一条）。

    映射（对应 §1.4 表的五行）：
      landed                      → none（无优先级 = 放弃）
      ready（算完待回传 / 在传）  → low（最末的备份保险）
      computing 且超阈值          → high（掉队救援）
      computing 且未超阈值        → medium（备份）
      claimed 但未开算            → medium（有人在做；**永不**升 high）
      无人在做                    → highest（独占；唯一性由 `epoch` 闸保证）

    `ready` **判在 computing 之前**：同一份 job 上 `ready` 就是 computing 的**后一阶段**
    （PPO 已跑完，只剩下传），拿还算着 `computing_at` 把它读成中档，就丢掉了「算完待回传 = 只
    该排最末、别人尽管开备份」这个信号——而它正是 §1.3.2 与「ready 只降优先级」的落地处。
    """
    if landed:
        return PRIORITY_NONE
    if ready_elsewhere:
        return PRIORITY_LOW
    if computing_elsewhere_since is not None:
        overdue = (float(now) - float(computing_elsewhere_since)) > float(straggler_sec)
        return PRIORITY_HIGH if overdue else PRIORITY_MEDIUM
    if claimed_elsewhere:
        return PRIORITY_MEDIUM
    return PRIORITY_HIGHEST


# ---- hub 中介 push 派发（2026-09-18，P1 余下）----------------------------------
#: 推给 GPU worker 的 job 体里带它，hub 据此认领「这份活该由 hub 推、不该等 pull」
#: （旧 hub/旧 worker 忽略未知键 —— 缺席即 pull，行为逐字节不变）。
DISPATCH_HUB_PUSH = "push"

#: 派发拍的节奏（探活/收割结果/挑活都是这一拍里做完的）。
PUSH_POLL_SEC = 10.0
#: 周期探活节奏（GET /ping）——与「离线」判定同源，不必更密（cloudflared 隧道抖动
#: 一次不该把节点判死）。
PUSH_PING_SEC = 30.0
#: 连续 N 次探活失败 ⇒ 该 worker 视为离场（在途 job 立即回落队首换人）。
PUSH_DEAD_MISSES = 3
#: 单份 job 推送后的兜底上限（秒）：超过它无论如何回落队首（PPO 一轮 10–30min，
#: 45min 足以覆盖慢链路 + 排队）。
PUSH_TIMEOUT_SEC = 45 * 60.0
#: 跑死过某份 job 的 worker 冷却时长（秒）：冷却期内不再给它派同一份 job
#: （「改为推送其它 worker」的落地；过期自动解禁，不永久拉黑）。
PUSH_COOLDOWN_SEC = 600.0
#: 租约持有人身份前缀：`push:<worker id>`。带前缀是为了让 `/admin/queue` 的
#: inflight 持有人、避让记录一眼能分清「云机自己报的名字」与「hub 代持的推送」。
PUSH_WORKER_PREFIX = "push:"


def push_worker_id_of(worker_id: str) -> str:
    """push 派发的租约持有人身份（`push:<id>`；已带前缀则原样返回——幂等）。"""
    w = str(worker_id or "")
    return w if w.startswith(PUSH_WORKER_PREFIX) else f"{PUSH_WORKER_PREFIX}{w}"


def push_worker_from_node(node: object) -> dict | None:
    """rl-config `nodes[]` 条目 → push worker；不是 push 节点 → None。

    判据与训练侧 `trainer/loop_steps._gpu_push_nodes` **同一把尺子**（`gpu_push` 且
    `enabled` 缺省视为 true、url 非空）：两边若判据不同，就会出现「训练侧认为该推这台、
    hub 却认为一台都没有」——症状是 job 永远躺在队首（最难查的一种）。

    返回 `{id, url, key, concurrency}`：`id` 缺省回落到 url（与训练侧日志口径一致），
    `concurrency` 缺省 1（gpu_push 节点线上就没有这个键——单 GPU 一次一份）。
    """
    if not isinstance(node, dict):
        return None
    if not node.get("gpu_push") or not node.get("enabled", True):
        return None
    url = str(node.get("url") or "").rstrip("/")
    if not url:
        return None
    try:
        conc = int(node.get("concurrency") or 1)
    except (TypeError, ValueError):
        conc = 1
    return {
        "id": str(node.get("id") or url),
        "url": url,
        "key": str(node.get("authKey") or ""),
        "concurrency": max(1, conc),
    }


def pick_push_worker(
    workers: Sequence[dict],
    inflight: Mapping[str, int],
    avoid: Iterable[str] = (),
) -> dict | None:
    """挑一个**现在就能接活**的 worker；没有空闲者 → None。

    四个条件（全部必要，任缺一个都会把「推送」退化成「往死机器上撞」）：在线（最近一次
    探活通过且未达离场阈值）、自报不忙（`/ping` 的 busy）、在飞数 < concurrency、不在
    本次避让名单里（刚跑死这份 job 的那台）。

    顺序 = 注册序（稳定 ⇒ 行为可测）；**不按快慢排序**——「谁空谁接」是既定口径
    （EWMA 快慢 hold 已被用户裁定删除，见 `trainer/dispatch.py` 注释）。
    """
    blocked = set(avoid)
    for w in workers:
        if not isinstance(w, dict):
            continue
        wid = str(w.get("id") or "")
        if not wid or wid in blocked:
            continue
        if not w.get("online"):
            continue  # 从没答过 / 连续失败达阈值：不往它身上推
        if w.get("busy"):
            continue  # 节点自报在跑
        if int(inflight.get(wid, 0)) >= int(w.get("concurrency") or 1):
            continue
        return w
    return None


def push_job_wants_hub_push(manifest: Mapping[str, object]) -> bool:
    """这份 job 是否要求 **hub 中介推送**（`manifest.dispatch == "push"`）。

    判定住在 manifest 而不是 hub 的课程表：它由训练侧 `--remote-transport hubpush`
    写定，是「这次发布是谁决定要推」的天然载体（hub 不在场时训练侧照样能直推云机，
    两条路的差别必须能从一个字段读出来）。
    """
    return str(manifest.get("dispatch") or "") == DISPATCH_HUB_PUSH


#: 课程模式：`online` = 实时派发 PPO；`offline` = 整段自主（kind=run），不实时派发、
#: 只收回传。用户口径（2026-09-18）：离线课程「不实时分派 ppo，但要接收 it 权重/指标
#: 回传 worker」。
COURSE_MODE_ONLINE = "online"
COURSE_MODE_OFFLINE = "offline"
COURSE_MODES = (COURSE_MODE_ONLINE, COURSE_MODE_OFFLINE)


def parse_course_arg(raw: object) -> tuple[str, str]:
    """解析一个课程规格：`NAME` / `NAME=mode`（mode ∈ {online, offline}）。

    空名/含路径分隔符/含空白 → ProtocolError（**响亮拒启**：课程名进的是磁盘路径，
    `../x` 之类必须在这里断掉，不能等落盘才发现写到别处去了）。
    """
    s = str(raw or "").strip()
    name, _, mode = s.partition("=")
    name = name.strip()
    mode = (mode.strip() or COURSE_MODE_ONLINE).lower()
    if not name:
        raise ProtocolError(f"课程名不能为空（收到 {raw!r}）")
    if any(ch in name for ch in "/\\") or any(ch.isspace() for ch in name) or name in (".", ".."):
        raise ProtocolError(f"课程名非法（不许路径分隔符/空白）: {name!r}")
    if mode not in COURSE_MODES:
        raise ProtocolError(f"课程模式必须是 {list(COURSE_MODES)}，收到 {mode!r}")
    return name, mode

#: M2 blob 载荷名（pull 端点 `GET /jobs/{id}/blob?name=opt|ref|demo|init`；push body `blobs`）。
BLOB_OPT = "opt"
BLOB_REF = "ref"
BLOB_DEMO = "demo"
#: opt-blob-diet（2026-09-24，plan/opt-blob-diet.plan.md §3.1）：初始权重的内容寻址段。
#: `sha` = `manifest.init_weights_fp`（**复用既有字段，不新增**——它就是 `sha256_file(args.out)`）；
#: `raw` = 该轮 `init_weights.json` 原样字节（= hub `args.out` = 上游 `result.weights_json`
#: 解码后的字节）。产出方 = hub（`publish_job`），消费方 = worker（restore 的模型权重 +
#: kind=iter 的 rollout）。**取代** tar 里那份重复的 `model.pt`。
BLOB_INIT = "init"
BLOB_NAMES: tuple[str, ...] = (BLOB_OPT, BLOB_REF, BLOB_DEMO, BLOB_INIT)


#: M3：TS 运行时 zip 在 job 目录内的文件名（`GET /jobs/{id}/ts_code` 服务它）。
TS_CODE_NAME = "ts_code.zip"
#: 节点确定性失败标记在 job 目录内的文件名（`POST /jobs/{id}/fail` 写、`GET
#: /jobs/{id}/result` 读；存在 = 这个 job 不会有结果，等下去只会等满超时）。
#: 训练侧**重发同一个 job** 时（同幂等键 → 同 job_id）由 `publish_job` 清除。
FAIL_NAME = "fail.json"
#: 失败原因回传体上限（人读的诊断字符串，1 个文本块足够；防大体打爆 hub 磁盘）。
FAIL_BODY_MAX = 64 * 1024

#: `POST /jobs/{id}/start|ready|abandon` 与 `/jobs/priority` / `/jobs/{id}/claim` 的请求体上限：
#: 都是小 JSON（job_id / worker_id / held 列表），比 fail 体小得多。有界是硬要求（远端体绝不
#: 信 Content-Length 之外的暗示）。住协议层：hub 的路由组与调度面都要它。
PRIORITY_BODY_MAX = 64 * 1024
#: `GET /jobs/peek?n=K` 一次最多返回的候选数（软持有深度缺省 3 的上界；防一个 worker 把队首
#: 扫空）。两个读者分住 `hub.schedule`（端点）与 `hub_server`（`_HubQueue.peek_jobs` 形参默认
#: 值），谁也 import 不了谁 ⇒ 必须住两边都能 import 的协议层。
PEEK_MAX = 16

# ---- 产物补传（「中途能连上 hub 就自动回传」；2026-09-17）----
# 全离线/半离线段把逐轮产物落在**节点本地**（Kaggle working / Colab Drive），产物本身就
# 是交付面；补传是**第二份拷贝**：节点一旦探到 hub 可达，就 best-effort 把已落盘的轮次
# 与段末摘要推上去，让控制面不用等人搬 zip。**训练永不因网络停摆**（连不上 = 静默跳过）。
#: 补传端点（节点 → hub；两条都必须 Bearer 鉴权，与其余端点同一条边界）。
#: 为什么不做无鉴权的 `/health`：探活要回答的是「**我能不能用**这条链」，不只是「对面
#: 活着」——只证可达的探针会让「token 配错」在第一次上传 2MB 体之后才暴露，而且多一个
#: 对公网泄露「hub 在线」的端点。带 token 探 `/ping` 一次同时证两件事。
OFFLINE_ARTIFACT_PATH = "/offline/artifact"

#: **「已开课」标记**文件名（落在课程 traj 目录下：`<traj-root>/<课>/training-enabled.txt`）。
#:
#: 控制台「开课」写它、「停课」删它；训练侧（`trainer/loop_plan.enabled_courses`）与 hub
#: （`_course_dir_live`）的**发现判据**都要求它存在。
#:
#: 为什么需要一个显式标记，而不是「有账本 = 在训」（2026-09-20 用户报障）：共享 trainer 是
#: **发现式**的（扫 `<traj-root>/*/training_log.jsonl`），而 tmp/ 下堆着几十门历史课的账本
#: ⇒ 进程一启动就把**所有历史课**一起拉进训练（实测：起 trainer 后控制台列出 21 门「正在
#: 训练」）。用户口径（原文）：「课程开训需要用户手动开启」⇒ 课程表 = 账本 ∧ **开课标记**。
#: 标记与账本同住课程目录：一个判据、一处位置，且开/停课各自是一次文件操作（不涉及共享
#: JSON 的读-改-写竞态）。
COURSE_ENABLE_MARKER = "training-enabled.txt"
OFFLINE_RESULT_PATH = "/offline/result"
#: 单轮补传体上限（weights ~0.3MB + opt ~1MB，base64 后 ~1.8MB；8MB 已极宽裕）。
OFFLINE_ARTIFACT_BODY_MAX = 8 * 1024 * 1024
#: 段末摘要体上限（人读的状态 + 计数，1 个文本块足够）。
OFFLINE_RESULT_BODY_MAX = 256 * 1024
#: 产物目录里补传记账文件名（= 已投递项；重启续投靠它，不靠内存）。
OFFLINE_DELIVERED_NAME = "delivered.json"
#: 任务包端点（hub → 云机）：把本机的整段任务包 `task-<课>.zip` 递出去。
#: 云机 notebook 的第一条路径就是「先连 hub，能通就从 hub 取包」（用户口径 2026-09-19）。
OFFLINE_TASK_PACK_PATH = "/offline/task-pack"
#: 续跑锚点端点（hub → 云机，2026-09-22）：任务包是导出那一刻的快照，而云机的中断/重领
#: 发生在它之后——重领时 hub 手里可能有更新的（自回传的或人工导入的）**同轮齐全**轮次。
#: `GET /offline/resume?course=<课>` 递元信息（it / 指纹 / 来源 / 指标行）；
#: `GET /offline/resume/blob?course=<课>&it=N&name=weights.json|opt.tar|row.json` 递字节。
OFFLINE_RESUME_PATH = "/offline/resume"
OFFLINE_RESUME_BLOB_PATH = "/offline/resume/blob"
#: 锚点字节端点允许的文件名（白名单：拒路径穿越与「借名读别的文件」）。
OFFLINE_RESUME_BLOB_NAMES = ("weights.json", "opt.tar", "row.json")

#: 离线**任务清单 + 领取租约**（hub → 云机，2026-09-25，`plan/offline-task-discovery.plan.md`）：
#: 云机不再要在 notebook 里写死课程名——`GET /offline/tasks` 列出可领的离线任务
#: （课程 + 包 + 新鲜度 + 谁在跑），云机 `claim` → 取包 → 跑完 → `release`，跑完一批再问一次。
OFFLINE_TASKS_PATH = "/offline/tasks"
OFFLINE_CLAIM_PATH = "/offline/claim"
OFFLINE_HEARTBEAT_PATH = "/offline/heartbeat"
OFFLINE_RELEASE_PATH = "/offline/release"
#: 清单协议版本：云机据此判断能力（老 hub 没有这个端点 ⇒ 404 ⇒ 降级到 `CFG.course`）。
OFFLINE_QUEUE_VERSION = 1
#: 离线租约时长（秒）。为什么与逐轮 job 的 `CLAIM_TTL_SEC = 300` 不同档：离线段是**小时级**
#: （取包 + 跑完整段 + 打包交付），300s 只会让心跳压力白增。心跳周期沿用 `HEARTBEAT_SEC = 60`。
#: 租约只管**领取资格**，不参与回传（`/offline/artifact` 一行不改）：回传靠 `(run_id, it)`
#: 首写幂等兜底 ⇒ 租约过期/被接管**不会**让已跑完的产物作废。
OFFLINE_LEASE_TTL_SEC = 900

_RUN_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: run_id 长度上限（它同时是 hub 侧目录名，必须短且有界）。
RUN_ID_MAX = 64


def sanitize_run_id(raw: object) -> str:
    """把远端的 `run_id` 变成**可以安全当目录名**的字符串，否则抛 ProtocolError。

    为什么必须做（这是本端点唯一的路径注入面）：hub 侧要把它拼进
    `job_root/offline/<run_id>/` —— 一个 `../../` 就能在 hub 上写任意文件（补传体还是
    远端控制不了的内容）。规则刻意只放行「字母数字开头 + 字母数字/点/下划线/连字符」：
    真实 run_id 是 `x3-rebirth-a2-<ts>-<rand>` 这种形状，用不着更宽的字符集。
    另外**显式拒绝 `..`**（`.`/`-` 本身合法，但 `a..b` 这种串在 Windows 上的解析
    行为不值得赌）与长度上限（目录名要短、要有界）。

    非字符串（None / 数字 / 列表）一律当成非法，**不做 str() 兜底**：这是不可信输入的
    边界，宽容只会把「上游传错了类型」变成一个看似正常的目录名。
    """
    s = raw.strip() if isinstance(raw, str) else ""
    if not s or len(s) > RUN_ID_MAX or not _RUN_ID_OK.match(s) or ".." in s:
        raise ProtocolError(
            f"run_id 非法（只接受 [A-Za-z0-9][A-Za-z0-9._-]* 且 ≤{RUN_ID_MAX} 字符、不含 '..'）: "
            f"{s[:80]!r}"
        )
    return s
def is_content_sha(s: str) -> bool:
    """内容寻址 sha 的形状判据（64 位小写 hex）。

    用途是**把哨兵值挡在 blob 路径之外**：`manifest.init_weights_fp` 在 BC job 与
    「全新 run 的首轮」上是 `"bc"`（`hub_client.publish_job`：`init_weights_path` 为空
    ⇒ `"bc"`）。拿它去查节点缓存 / 发 `GET ?name=init` 必然落空，会被归成「确定性
    缺失」⇒ `ProtocolError` ⇒ 停腿 / 永久 428（plan/opt-blob-diet.plan.md §3.2，评审 F2）。

    单一实现：worker（`_resolve_weights`）、push 腿（`push_client`）、节点服务端
    （`worker_server._submit`）三处共用，避免各写一份形状判据。
    """
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def blob_path(job_dir: str | Path, name: str) -> Path:
    """内容寻址 blob 在 job 目录内的落盘名（M2；hub 写、pull worker 取）。

    `name` ∈ BLOB_NAMES（opt/ref/demo/init）。raw 字节原样存（无 base64），sha 即键。
    """
    if name not in BLOB_NAMES:
        raise ProtocolError(f"未知 blob 名 {name!r}（只接受 {BLOB_NAMES}）")
    return Path(job_dir) / f"blob.{name}"


# ------------------------------------------------------------------ idempotency（S5 第七刀）
# 「job 身份簇」已下沉 `common/job_identity.py`（幂等键 / job_id / 发布端撞名守卫 + 扫描面常量）。
# 实现搬家、名字留门面 ⇒ 全仓 `from common.protocol import …` 一行不改。
from common.job_identity import (
    SIBLING_MANIFEST_GLOB as SIBLING_MANIFEST_GLOB,
)
from common.job_identity import (
    collision_rows as collision_rows,
)
from common.job_identity import (
    idempotency_key as idempotency_key,
)
from common.job_identity import (
    job_id as job_id,
)


def job_seed(run_id: str, it: int, init_weights_fp: str) -> str:
    """D5 per-job 确定性种子 = hash(runId, it, init_weights_fp)（十六进制串）。

    云 worker 以它为 numpy 种子重新播种后再 load/chunk/update——同 job 重发
    chunk 逐字节一致；跨进程（本地 vs 云）chunk 顺序差异为预期内（D7）。
    """
    h = hashlib.sha256()
    h.update(run_id.encode("utf-8"))
    h.update(str(it).encode("utf-8"))
    h.update(init_weights_fp.encode("utf-8"))
    return h.hexdigest()


# ------------------------------------------------------------------ result


def validate_result(
    r: dict,
    manifest: dict,
    *,
    commit_echo_must_match: bool = True,
) -> dict:
    """云回传结果校验（附录 A）：job_id/data_fp/init_weights_fp 与 manifest 对账 +
    weights_json 非空 + agg 关键字段。返回归一化结果。校验失败抛 ProtocolError。

    commit_echo_must_match=False：hub 侧对账时用（hub 不依赖云 echo 决定 commit
    是否一致——manifest.commit 是 hub 自己写的，echo 只是审计）。
    """
    if not isinstance(r, dict):
        raise ProtocolError(f"result 必须是对象，收到 {type(r).__name__}")
    for k in ("job_id", "data_fp", "init_weights_fp", "weights_json", "commit_echo"):
        if k not in r:
            raise ProtocolError(f"result 缺失字段 {k}")
    if r["job_id"] != manifest["job_id"]:
        raise ProtocolError(
            f"result.job_id={r['job_id']!r} != manifest.job_id={manifest['job_id']!r}——job 混用"
        )
    if r["data_fp"] != manifest["data_fp"]:
        raise ProtocolError("result.data_fp != manifest.data_fp——训练语料漂移（拒收）")
    if r["init_weights_fp"] != manifest["init_weights_fp"]:
        raise ProtocolError("result.init_weights_fp != manifest.init_weights_fp（拒收）")
    if commit_echo_must_match and r["commit_echo"] != manifest["commit"]:
        raise ProtocolError("result.commit_echo != manifest.commit——代码版本不一致（拒收）")
    wj = r["weights_json"]
    if not isinstance(wj, (str, bytes)) or len(wj) == 0:
        raise ProtocolError("result.weights_json 必须非空（base64 或原始字节）")
    if str(manifest.get("kind", "ppo") or "ppo") == "bc":
        # BC 结果：无 agg（PPO 训练指标），改查 metrics（train/bc.py 产出的训练汇总）。
        metrics = r.get("metrics")
        if not isinstance(metrics, dict) or not all(
            k in metrics for k in ("epochs", "train_samples", "val_samples", "best_val_loss")
        ):
            raise ProtocolError(f"bc result.metrics 缺关键字段: {metrics!r}")
        return r
    agg = r.get("agg")
    if not isinstance(agg, dict) or not all(
        k in agg for k in ("policy", "value", "entropy", "kl", "mean_ret")
    ):
        raise ProtocolError(f"result.agg 缺关键字段: {agg!r}")
    kind = str(manifest.get("kind", "ppo") or "ppo")
    if kind in ("iter", "run") and not r.get("smoke"):
        # M3：节点自己跑的 rollout，其采集口径必须随结果回来（hub 侧没有本地 shard
        # 可算 winRate/outcomes/samples——不校验就等于信云侧自报，回归时无法归因）。
        # 冒烟回显（smoke=true）豁免：它刻意不跑 rollout，没有报告可带。
        # kind=run（2026-09-17）：hub 侧同样**没有**这些 shard（它们在节点上跑完即
        # 走），口径同规——「本 job 自己那一轮」的报告。
        r["report"] = validate_iter_report(r.get("report"))
    if kind == "run":
        # 半离线段（kind=run）：结果 = **末轮**的形状（weights/opt/agg/report，可直接
        # 走既有落位链）+ 逐轮明细 `iters` + `it_end`。逐条校验明细：hub 拿不到这些
        # 轮的 shard，但它们**会**进账本/控制台（人据此判断这条腿学没学会），所以
        # 形状与单调性必须成立——garbage-in 就会变成一条看起来正常的假学习曲线。
        rows = r.get("iters")
        if not isinstance(rows, list) or not rows:
            raise ProtocolError(f"kind=run 的 result.iters 必须是非空数组，收到 {type(rows).__name__}")
        prev_it = 0
        for row in rows:
            if not isinstance(row, dict):
                raise ProtocolError(f"result.iters 的元素必须是对象，收到 {type(row).__name__}")
            it_row = row.get("it")
            if isinstance(it_row, bool) or not isinstance(it_row, int):
                raise ProtocolError(f"result.iters[].it 必须是整数，收到 {it_row!r}")
            if it_row <= prev_it:
                raise ProtocolError(f"result.iters 的 it 必须严格递增（{prev_it} -> {it_row}）")
            prev_it = it_row
            row_agg = row.get("agg")
            if not isinstance(row_agg, dict) or not all(
                k in row_agg for k in ("policy", "value", "entropy", "kl", "mean_ret")
            ):
                raise ProtocolError(f"result.iters[it={it_row}].agg 缺关键字段: {row_agg!r}")
            if not r.get("smoke"):
                row["report"] = validate_iter_report(row.get("report"))
        if r.get("it_end") != rows[-1]["it"]:
            raise ProtocolError(
                f"result.it_end={r.get('it_end')!r} != 末轮 it={rows[-1]['it']!r}——结果自相矛盾"
            )
    return r


#: kind=iter 结果必须回传的采集报告字段（照 `biz/reports.combine_reports` 的输出名）。
ITER_REPORT_REQUIRED: tuple[str, ...] = (
    "games",
    "winRate",
    "outcomes",
    "totalSamples",
    "totalTicks",
    "elapsedSec",
    "shards",
)


def validate_iter_report(rep: object) -> dict:
    """校验 kind=iter 回传的采集报告（缺字段/类型错 → ProtocolError，fail fast）。"""
    if not isinstance(rep, dict):
        raise ProtocolError(f"kind=iter 的 result.report 必须是对象，收到 {type(rep).__name__}")
    missing = [k for k in ITER_REPORT_REQUIRED if k not in rep]
    if missing:
        raise ProtocolError(f"kind=iter 的 result.report 缺字段: {missing}")
    for k in ("games", "totalSamples", "totalTicks", "shards"):
        v = rep[k]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ProtocolError(f"report.{k} 必须是非负整数，收到 {v!r}")
    if rep["games"] < 1:
        raise ProtocolError("report.games 必须 >= 1（0 局 = 本轮没采集，不算完成）")
    for k in ("winRate", "elapsedSec"):
        v = rep[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) < 0:
            raise ProtocolError(f"report.{k} 必须是非负数字，收到 {v!r}")
    oc = rep["outcomes"]
    if not isinstance(oc, dict) or not oc:
        raise ProtocolError(f"report.outcomes 必须是非空对象，收到 {oc!r}")
    for k, v in oc.items():
        if isinstance(v, bool) or not isinstance(v, int):
            raise ProtocolError(f"report.outcomes[{k!r}] 必须是整数，收到 {v!r}")
    out = dict(rep)
    out["winRate"] = float(rep["winRate"])
    out["elapsedSec"] = float(rep["elapsedSec"])
    out["outcomes"] = {str(k): int(v) for k, v in oc.items()}
    if not isinstance(out.get("perGame", []), list):
        raise ProtocolError("report.perGame 必须是数组（缺省允许）")
    out.setdefault("perGame", [])
    return out


# ---- 会退火到 ~0 的系数：低于此阈值即视为"关" ----
# 依据（2026-09-10 实测）：kickstart / kl 系数按 `kickstart_kl * decay ** N` 几何衰减，
# **永远到不了精确 0** —— 实测课程跑到 kl = 1.4551915228366852e-11（= 2^-36）时，
# 判据 `> 0` 仍放行，于是白付：ref 权重进 payload（~0.36 MB）+ worker 每轮预计算 3 s。
# 而它的数学贡献 1.46e-11 x 0.126 ≈ 1.8e-12，相对 policy≈0.0046 完全可忽略。
# 用户已确认课程不会回抬 kickstart（2026-09-10）。
NEGLIGIBLE_COEF = 1e-9


def coef_active(x: float) -> bool:
    """系数是否值得付它的开销（> NEGLIGIBLE_COEF）。

    用于 kickstart_kl / kl_coef 这类会退火到 ~0 的旋钮；阈值以下一律按"关"处理，
    从而省掉 ref 权重传输、worker 侧 ref 加载与预计算、engine 侧 ref 前向。
    """
    return float(x) > NEGLIGIBLE_COEF


