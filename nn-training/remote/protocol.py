"""remote/protocol.py — remote PPO job protocol (pure, torch-free, stdlib-only).

Single source of truth for the wire contract between the hub (TrainingLoop remote
branch + hub-server) and the cloud worker (`python -m remote_worker`). Design doc:
`plan/remote-ppo-architecture.md` §6 (D1/D5/D6/D9/D12), manifest fields §13 附录 A.

Contents (all pure functions / constants — no torch, no ppo import):
  * manifest validation (required-field fail fast, unknown fields ignored)
  * `data_fp` — sha256 over sorted shard relative paths + each manifest's
    {wver, stage, seed} (D1; local recompute == manifest value on both sides)
  * payload zip pack/unpack (shard dirs + manifest.json), with `payload_sha256`
  * idempotency key = (runId, it, init_weights_fp, data_fp) (D1)
  * per-job deterministic numpy seed = hash(runId, it, init_weights_fp) (D5)
  * result envelope validation (weights_json + opt_tar + agg + commit_echo)
  * auth: bearer token header name (D9)

Rationale for torch-free: the hub (TrainingLoop remote branch + hub-server) must
never import torch (D2) — this module is imported by both hub-side and worker-side
code, so it must be importable with zero torch/numpy cost (numpy is acceptable;
test_no_torch_on_import guards the `import run_rl` chain).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import struct
import tarfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal

# ------------------------------------------------------------------ constants

PROTO = 1  # 协议版本：未知字段忽略，缺失必填 fail fast（D1）
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

    判据与训练侧 `rl/loop_steps._gpu_push_nodes` **同一把尺子**（`gpu_push` 且
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
    （EWMA 快慢 hold 已被用户裁定删除，见 `rl/dispatch.py` 注释）。
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

#: manifest 必填字段（附录 A；缺失任一 → 校验失败）
MANIFEST_REQUIRED = (
    "proto",
    "runId",
    "it",
    "job_id",
    "commit",
    "code_sha256",  # Python 源码 zip sha256（hub 启动时打包，替代 git 同步）
    "course",  # 课程 jsonc 全文快照（reward_spec 重建输入，D6）
    "course_fp",  # 课程文件 sha256（语料血缘，D14）
    "reward_formula",
    "formula_hash",
    "metrics_version",
    "gamma",
    "lam",
    "mode",
    "seed",  # per-job numpy 种子 = hash(runId,it,init_weights_fp)（D5）
    "epochs",
    "mb",
    "lr",
    "init_weights_fp",
    "data_fp",
    "payload_sha256",
)
#: kind=bc（行为克隆 job）免除的 PPO 专有必填（BC 无 reward/γ/λ 语义）；
#: 追加必填 `arch`（bc|student）。plan/bc-cloud-integration.plan.md §1。
MANIFEST_BC_EXEMPT: tuple[str, ...] = (
    "reward_formula",
    "formula_hash",
    "metrics_version",
    "gamma",
    "lam",
)
MANIFEST_BC_EXTRA: tuple[str, ...] = ("arch",)
#: 任务类型（2026-09-13 BC 整合）：缺省 "ppo" = 既有 per-tick PPO job（wire 兼容——
#: 旧 hub 产出的 manifest 无此键，一律按 ppo 校验）。"bc" = 行为克隆 job（语料 npy
#: shard 进 payload，云端跑 train/bc.py，回传 BC 权重；无 reward/γ/λ 语义）。
#: plan/bc-cloud-integration.plan.md §1。
MANIFEST_OPTIONAL_DEFAULTS: dict[str, object] = {
    "kind": "ppo",
    "kl_coef": 0.0,
    "kl_cap": None,  # None = 不覆盖，由 policy.streamKlCap 决定
    "ent_coef": None,  # 2026-09-11：None = 用引擎常量 ENT_COEF（0.01）；0.0 是合法值
    "adv_norm": "auto",
    "normalize_ret": False,  # R5：ret 归一；缺失（旧 hub）= 关
    "kickstart_kl": 0.0,  # §363：BC 缰绳系数（已衰减）；0 = 关
    "ref_weights_b64": "",  # §363：BC ref 权重 base64；空 = 无
    "ref_weights_fp": "",  # §363：上者 sha256（有字节时必对上）
    "shuffle": True,
    "schedule_raw": [],  # ppo_schedule 解析前原始表（审计）
    "opt_init": "",  # base64 tar（model/opt/numpy RNG）；空 = 无（首轮）
    # 严格样本量配额（target_transitions 路线）：训练侧逐关只收前 ceil(target/关数) 步。
    # 0 = 历史行为（全收）；缺失（旧 hub 产出的 manifest）= 0 ⇒ wire 兼容。
    "per_stage_quota": 0,
    # ---- M2（plan/remote-wire-remediation §4.2）内容寻址 blob ----
    #: raw（编码前）opt tar 的 sha256。有它时 `opt_init` 可为空——节点按 sha 去
    #: blob_cache 取，未命中才下载。**必须进 OPTIONAL**（wire 兼容红线：缺失必填会
    #: 把旧 hub/旧 worker 的 job 全部拒收）。
    "opt_sha": "",
    #: raw ref 权重的 sha256（= ref_weights_fp；同义别名，便于按名寻址）。
    "ref_sha": "",
    #: raw 字节数（预检/日志；0 = 未知）。
    "opt_bytes": 0,
    "ref_bytes": 0,
    # ---- demo 混 batch（x20 后续）：demo bank 内容寻址 + BC 系数 ----
    #: demo bank npz 的 sha256；空 = 关（worker 走 getattr 缺省，老行为）。
    "demo_sha": "",
    #: demo BC 辅 loss 系数；0.0 = 关。
    "demo_bc_coef": 0.0,
    #: 每 PPO minibatch 步抽的 demo 样本数；0 = 关。
    "demo_per_mb": 0,
    #: 本轮是否走瘦身路径（审计/A-B；False = 内联老字段，逐字节回到旧行为）。
    "slim": False,
}

# ------------------------------------------------------------------ M3: kind=iter
# plan/remote-wire-remediation.plan.md §5.2：新 job kind =「一整轮」——节点自己跑
# rollout（bun 调 exporter 产 shard）→ 接着跑既有 PPO 链路 → 只回传权重/report。
# 与 BC 同一条 kind 通道（照 MANIFEST_BC_* 的先例），mode 红线互斥。
#: kind=iter 追加必填：TS 运行时 zip 的 sha256 + rollout 规格。
MANIFEST_ITER_EXTRA: tuple[str, ...] = ("ts_code_sha256", "rollout")
#: TS 源码 zip 里允许出现的 exporter（argv[0] 白名单）。**只**放行 rollout 采集器：
#: argv 来自 hub（可信方），但白名单让「协议字段被误当命令执行」不可能发生。
ROLLOUT_SCRIPTS: tuple[str, ...] = ("tools/sim/export-rl-rollout.ts",)
#: rollout 规格里 argv 内嵌路径的允许前缀（job 目录内的相对路径，防越界）。
#: 端口无关：worker 一律以 job 目录为 cwd 执行 argv。
ITER_OUT_REL = "w"
#: 上云 rollout 写进 shard manifest 的 `node` 标签（hub 侧 argv 里的字面量）。
#: 与本地 `local` 区分（可溯源），同时是 §5.5① 逐位对拍的基准——验收时本地也用
#: 同一 argv（同标签）重跑，逐字节比对才成立。
ITER_NODE_LABEL = "node"

# ------------------------------------------------------------------ 半离线: kind="run"
# 「云端整段自主」job：hub 只在交接时给一次（课程 + 初始权重 + 代码 + **计划**），
# 节点从此不依赖 hub —— 自己按计划把剩余轮次跑完（rollout + PPO 全在节点），逐轮
# 把权重/指标写进本地产物目录（Kaggle /kaggle/working、Colab Drive），可打包下载、
# 可跨会话续跑。
#
# 与 kind=iter 的关系（**不是 fork，是延长**）：本 job 自己的那一轮（`it`）与 kind=iter
# **逐字段同构**（`rollout` + `ts_code_sha256` 必填、data_fp = 该轮声明集），节点走的
# 也是同一条执行链；区别只在尾巴——跑完本轮到 `plan.json` 继续把后续轮次自己跑完。
# 于是「离线轮」与「hub 监管轮」的行/产物/校验口径完全一致。
#: 计划文件名（payload 内，与 init_weights.json 同层；sha 进 manifest 而**不是**全文——
#: argv 模板 + 逐轮对集可达百 KB 量级，不该让 hub 每轮轮询都解析一遍）。
PLAN_NAME = "plan.json"
PLAN_PROTO = 1
#: kind=run 追加必填：kind=iter 的两项 + 计划的 sha256。
MANIFEST_RUN_EXTRA: tuple[str, ...] = (*MANIFEST_ITER_EXTRA, "plan_sha256")
#: 半离线轮写进 shard manifest 的 `node` 标签（与 `node`/`local` 区分：可溯源到
#: 「这一批局是云端自主段跑的」）。
RUN_NODE_LABEL = "run"
#: 计划的**硬上界**（防一份手写/损坏的计划把节点按在机上一整天）。命令行可用
#: `--run-max-iters` 再降；计划的 end_it 一律按其与 iters_total 的交集钳制。
RUN_MAX_ITERS_HARD_CAP = 500

#: M2 blob 载荷名（pull 端点 `GET /jobs/{id}/blob?name=opt|ref|demo`；push body `blobs`）。
BLOB_OPT = "opt"
BLOB_REF = "ref"
BLOB_DEMO = "demo"
BLOB_NAMES: tuple[str, ...] = (BLOB_OPT, BLOB_REF, BLOB_DEMO)


class ProtocolError(ValueError):
    """协议违规（缺失必填 / 类型错 / 哈希不匹配）。调用方（hub/worker）决定拒收方式。"""


class RetryableError(Exception):
    """瞬时失败（网络抖动 / 5xx / 传输损坏）——租约窗口内重试即可修复，非确定性拒绝。

    与 ProtocolError 的分界（2026-09-05，DECISIONS §340 补充 3）：4xx/字段级校验
    失败 = 确定性拒绝（重试无意义）；网络层异常与 5xx = 可重试。worker_loop 捕获
    RetryableError 后主动 release 租约回池，立即可重领（不再干等 30min 过期）。"""


class JobCancelledError(RuntimeError):
    """本 job 已被**别人赢下**（结果已落盘）⇒ 停算丢弃，**不**回传、**不**报失败。

    为什么它必须是**独立**异常（2026-09-22，plan/transfer-scheduling §2.4）：取消是一个
    **正常**结局（备份副本被首写锁定判负），而 `worker_loop` 的两个既有分支都会把它读错——
    `except ProtocolError` ⇒ `report_job_failure`（把合法放弃报成确定性失败 ⇒ 训练停腿）、
    `except RetryableError` ⇒ `release` 租约（把别人已经赢下的活重新放回池子）。

    唯一正确的处置：丢本地副本 + `POST /jobs/{id}/abandon`（幂等，含 release 租约）+ 走
    priority 选下家。抛点 = `ppo_update` 的 **epoch 边界**（`on_epoch_done`），
    实测延迟记 `cancel_latency_s`。
    """


class JobFailedError(RuntimeError):
    """**确定性**节点失败，且失败原因已随 `POST /jobs/{id}/fail` 回传到控制面。

    与 RetryableError/ProtocolError 的分界（2026-09-17，DECISIONS
    §2026-09-17-job-fail-report）：节点**已经判定这个 job 在这台机器上跑不成**（bun
    装不上 / TS 运行时取不到 / argv 非法），并把原因报给了 hub/节点服务。

    在此之前这条信息只落在**云机日志**里：pull 侧 worker 走 `except ProtocolError`
    静默 skip（不回传、不还租约），训练侧只能等 `wait_job` 25 分钟超时（看到的是
    "超时"，不是"bun 缺失"）；push 侧节点服务用 500 报失败，而 500 在
    `push_client.wait_result` 里被当**瞬时错误**重试到预算耗尽。两者都把
    「确定性能力缺失」伪装成了「网络/排队问题」。

    reason/kind/detail 由回报方填写（`kind` = 异常类名，`detail` = 截断后的原文）。
    """

    def __init__(self, message: str, *, kind: str = "", detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(message)


class CodeChangedError(RuntimeError):
    """本进程已 import 的代码与 job 携带的 code_sha256 不一致（热替换事件）。

    成因（2026-09-11 review）：worker 常驻进程在首 job 才 import 代码进 sys.modules；
    本地改代码后 hub 重打 code.zip（sha 变），后续 job 解压新代码、sys.path.insert(0,
    新目录)，但 import 只查 sys.modules → 跑的还是旧代码**且零报错**。

    ⚠ 刻意不继承 ProtocolError：worker_loop 对 ProtocolError 是 "skip (not retried)"
    ——会把该 job 永久跳过，hub 侧干等到 1800s 超时、触发 R9 连败降级/停腿。本异常
    必须走"重启进程"这条独立分支。
    """

    def __init__(self, loaded_sha: str, job_sha: str) -> None:
        self.loaded_sha = loaded_sha
        self.job_sha = job_sha
        super().__init__(
            f"代码已变更：本进程加载 {loaded_sha[:12]}… != job 要求 {job_sha[:12]}…"
            "（继续跑会用旧代码产出看似正常的结果）"
        )


def normalize_manifest(m: dict) -> dict:
    """校验 + 归一化 job manifest（proto=1：缺失必填 fail fast，未知字段忽略）。

    返回浅拷贝的 manifest（必填齐全、可选字段带默认值）。校验失败抛
    `ProtocolError`，错误信息指明缺失字段。
    """
    if not isinstance(m, dict):
        raise ProtocolError(f"manifest 必须是对象，收到 {type(m).__name__}")
    kind = str(m.get("kind", "ppo") or "ppo")
    if kind not in ("ppo", "bc", "iter", "run"):
        raise ProtocolError(f"kind={kind!r} 未知（只认 'ppo'|'bc'|'iter'|'run'）——拒收")
    required = [
        k for k in MANIFEST_REQUIRED if not (kind == "bc" and k in MANIFEST_BC_EXEMPT)
    ]
    if kind == "bc":
        required += list(MANIFEST_BC_EXTRA)
    if kind == "iter":
        required += list(MANIFEST_ITER_EXTRA)
    if kind == "run":
        required += list(MANIFEST_RUN_EXTRA)
    missing = [k for k in required if k not in m]
    if missing:
        raise ProtocolError(f"manifest 缺失必填字段: {missing}")
    if int(m.get("proto", -1)) != PROTO:
        raise ProtocolError(f"proto={m.get('proto')!r} != {PROTO}（协议版本不匹配）")
    out = dict(m)
    for k, v in MANIFEST_OPTIONAL_DEFAULTS.items():
        out.setdefault(k, v)
    # 标量类型校验（fail fast，防拼错/串位）——按 kind 实际持有的键校验
    if not isinstance(out["runId"], str) or not out["runId"]:
        raise ProtocolError("runId 必须是非空 str")
    for k in ("it", "epochs", "mb", "metrics_version"):
        if k not in out:
            continue
        if not isinstance(out[k], int) or isinstance(out[k], bool):
            raise ProtocolError(f"{k} 必须是 int，收到 {out[k]!r}")
    for k in ("gamma", "lam", "lr"):
        v = out.get(k)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ProtocolError(f"{k} 必须是 float，收到 {v!r}")
        if float(v) <= 0:
            raise ProtocolError(f"{k} 必须 > 0，收到 {v!r}")
    for k in (
        "commit",
        "code_sha256",
        "course",
        "course_fp",
        "mode",
        "seed",
        "init_weights_fp",
        "data_fp",
        "payload_sha256",
        "job_id",
    ):
        if not isinstance(out[k], str) or not out[k]:
            raise ProtocolError(f"{k} 必须是非空 str")
    # kind 红线（plan/bc-cloud-integration.plan.md §1）：ppo 仅 per-tick（v1 原红线），
    # bc 仅 mode="bc"——两种任务类型在 mode 通道上互斥，杜绝串型。
    if kind == "bc":
        if out["mode"] != "bc":
            raise ProtocolError(f"kind=bc 要求 mode='bc'，收到 {out['mode']!r}（拒收）")
        if out["arch"] not in ("bc", "student"):
            raise ProtocolError(f"bc manifest arch 必须是 'bc'|'student'，收到 {out['arch']!r}")
    else:
        if out["mode"] != "per-tick":
            raise ProtocolError(
                f"mode={out['mode']!r} != 'per-tick'（v1 红线：仅 per-tick 课程支持远程）"
            )
    if kind == "iter":
        # M3 mode 互斥红线：iter 只承载 per-tick rollout（goal/intent 导出器不在
        # 上云范围内——它们的 rollout 语义未在协议里建模）。ts_code_sha256 必须是
        # 非空 str（下面的通用非空串循环已覆盖），rollout 规格逐字段校验。
        if out["mode"] != "per-tick":
            raise ProtocolError(
                f"kind=iter 要求 mode='per-tick'，收到 {out['mode']!r}（M3 只上云 per-tick rollout）"
            )
        out["rollout"] = validate_rollout_spec(out["rollout"])
    if not isinstance(out.get("normalize_ret", False), bool):
        raise ProtocolError(f"normalize_ret 必须是 bool，收到 {out.get('normalize_ret')!r}")
    if not isinstance(out.get("kickstart_kl", 0.0), (int, float)) or isinstance(
        out.get("kickstart_kl", 0.0), bool
    ):
        raise ProtocolError(f"kickstart_kl 必须是 number，收到 {out.get('kickstart_kl')!r}")
    if float(out.get("kickstart_kl", 0.0)) < 0:
        raise ProtocolError("kickstart_kl 必须 >= 0")
    psq = out.get("per_stage_quota", 0)
    # bool 是 int 的子类 ⇒ 必须先挡 bool（True/False 混进来会让配额变成 1/0 而不报错）。
    if isinstance(psq, bool) or not isinstance(psq, int) or psq < 0:
        raise ProtocolError(f"per_stage_quota 必须是非负整数，收到 {psq!r}")
    return out


# ------------------------------------------------------------------ data_fp


#: data_fp 条目 = (shard 目录名, wver, stage, seed)。
DataFpEntries = Sequence[tuple[str, str, int, int]]


def data_fp_entries(entries: DataFpEntries) -> str:
    """data_fp 的核心：对**声明**的 shard 条目集做 sha256（排序后逐字段拼接）。

    单独抽出来是为了 M3：上云 rollout 的 shard 由节点现产，hub 侧没有目录可读，
    只能对「声明集」（argv 里逐局的 stage/seed + 约定的 wver）算期望值。节点跑完后
    对**实产**目录调 `data_fp()`（同一函数、同一拼接顺序）再比对——两侧算法同源，
    所以「相等」严格等价于「实产集 == 声明集」，任一侧漏局/多局都会露出来。
    """
    ents = sorted(entries, key=lambda e: e[0])
    h = hashlib.sha256()
    for name, wver, stage, seed in ents:
        h.update(name.encode("utf-8"))
        h.update(wver.encode("utf-8"))
        h.update(str(stage).encode("utf-8"))
        h.update(str(seed).encode("utf-8"))
    return h.hexdigest()


def d14_corpus_match(job_course_fp: str, job_corpus_fp: str, shard_manifest: dict) -> bool:
    """D14 装载校验的比对规则（DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25）。

    双侧都有 corpus_fp（语料身份 = env+reward 解析值语义哈希）⇒ 比 corpus_fp——
    预算/路径/注释类课程 mid-run 编辑只动 course_fp（文件血缘），不得触发拒收。
    任一侧缺 corpus_fp（legacy shard / 旧 job）⇒ 回退文件血缘 course_fp 逐字比对。

    ★ 为什么它住 protocol 而不是 worker：这条规则有**两个**执行端——云 worker 装载时
    逐 shard 判（拒收），hub 打包时也逐 shard 判（`hub_client.iter_shard_dirs` 只挑
    匹配的进 payload）。两份实现漂开就是一个死循环：发布端放进一个异血缘 shard、云端
    整份 job 拒收、训练轮等一个永不回传的结果（2026-09-20 事故：c6-chip it16 的 payload
    里混进了 21 个旧血缘 shard，云 worker 报 `D14 course_fp 不匹配` 整份退回）。
    同一函数 = 同一判据，打包集恒等于云端会接受的集合。
    """
    s_corpus = str(shard_manifest.get("corpus_fp", "") or "")
    if job_corpus_fp and s_corpus:
        return job_corpus_fp == s_corpus
    return str(shard_manifest.get("course_fp", "")) == job_course_fp


def data_fp(shard_dirs: Sequence[str | Path]) -> str:
    """D1 data_fp：sha256(按字典序排列的 shard 相对路径 + 各 manifest {wver,stage,seed})。

    shard_dirs：本轮应训 shard 目录（绝对/相对路径均可，按 basename 字典序排序——
    排序以 shard 目录名（rl_s{stage}_seed{seed}）为键，与打包/装载口径一致）。
    任一侧重算必须得到同一值（hub 打包时写入、验收时本地重算比对——防 ABA，D12）。
    """
    entries: list[tuple[str, str, int, int]] = []
    for d in shard_dirs:
        p = Path(d)
        mp = p / "manifest.json"
        try:
            with open(mp, encoding="utf-8") as f:
                mm = json.load(f)
        except (OSError, ValueError) as e:
            raise ProtocolError(f"data_fp: 读 {mp} 失败: {e}") from e
        entries.append(
            (
                p.name,  # rl_s{stage}_seed{seed}
                str(mm.get("wver", "")),
                int(mm.get("stage", -1)),
                int(mm.get("seed", -1)),
            )
        )
    return data_fp_entries(entries)


# ------------------------------------------------------------------ M3 rollout 规格
#
# 为什么 argv 是规格的 SSOT（而不是 stages/seeds/difficulty 一堆字段）：
# hub 侧本来就有 `rl/cmd.build_rollout_cmd` 拼装本机 rollout 命令（三导出器 + 课程
# 覆盖 + D14 血缘，单源）。上云时**用同一个函数**、只把路径换成 job 目录内的相对
# 路径，再把它交给节点执行 ⇒ 「节点跑的采集」与「本机跑的采集」逐字节同命令，
# 逐位对拍（计划 §5.5①）是构造性质而不是靠人去对对参数。新增一个字段就等于在
# 协议里复制一份 cmd.py 的知识，迟早漂。

#: argv 里必须携带的相对路径 flag（job 目录为 cwd）。
_ITER_PATH_FLAGS: tuple[str, ...] = ("--out", "--weights")
#: rollout 规格默认值（缺省即旧行为，additive）。
ROLLOUT_SPEC_DEFAULTS: dict[str, object] = {
    "wver": "",
    "workers": 1,
    # 0 = plan 没给 ⇒ **节点兜底硬顶**（`iter_rollout.DEFAULT_GAME_TIMEOUT_SEC`）。
    # 2026-09-22 改口径：旧注释写的是「0 = 不设单局超时（本机历史行为）」，也就是一个卡住的
    # bun 子进程可以永远等下去——it34 实测 rollout 中途停了 651s（10 局卡死、日志只有计数）。
    # 本机 rollout 不受影响（它不看这个字段），云端必须有个上限。
    "game_timeout_sec": 0.0,
    "bun": "bun",  # 节点侧 bun 可执行名（PATH 查找）；空 = 用节点默认
}


def shard_name(stage: int, seed: int) -> str:
    """shard 目录名（与 `export-rl-rollout.ts` 的 `rl_s${si}_seed${seed}` 同源）。"""
    return f"rl_s{stage}_seed{seed}"


def parse_shard_name(name: str) -> tuple[int, int] | None:
    """`rl_s{stage}_seed{seed}` -> (stage, seed)；不匹配返回 None（不抛）。"""
    m = re.fullmatch(r"rl_s(\d+)_seed(\d+)", str(name or ""))
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


#: Windows 盘符前缀（`C:` / `c:`）——绝对值与 drive-relative 都算，跨平台一律拒。
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _iter_flag_value(argv: list[str], flag: str) -> int:
    """取 argv 里 `flag` 的单个整数值；缺失/重复/非整数一律 ProtocolError。

    重复出列（`--stages 0 --stages 1`）也是个坑：导出器只会吃到一个，声明集却可能
    按另一个算——声明集必须就是**实际会执行**的那一组，所以重复直接拒收。
    """
    vals = [argv[i + 1] for i, a in enumerate(argv) if a == flag]
    if len(vals) != 1:
        raise ProtocolError(f"rollout.argv 的 {flag} 必须恰好出现 1 次，实得 {len(vals)} 次")
    try:
        return int(vals[0])
    except (TypeError, ValueError):
        raise ProtocolError(f"rollout.argv 的 {flag} 必须是单个整数，收到 {vals[0]!r}") from None


def _iter_rel_path(value: object, flag: str) -> str:
    """校验 argv 里的路径参数是 job 目录内的相对路径（拒绝对路径 / `..` / 空）。

    跨平台（2026-09-17 修）：`os.path.isabs` / `Path.is_absolute` 只看**当前内核**的
    规则——Linux 上 `C:/weights.json` 两者都判 False，于是 Windows 盘符路径能静默过门，
    到节点上却变成宿主盘上的文件（或直接跑挂）。节点的 cwd 契约不随着 hub 的内核变，
    所以盘符（含 drive-relative `c:x`）与 UNC 在任何平台都在这里拒收。
    """
    s = str(value or "")
    if not s:
        raise ProtocolError(f"rollout.argv 的 {flag} 不能为空")
    if (
        os.path.isabs(s)
        or Path(s).is_absolute()
        or s.startswith(("~", r"\\"))  # ~ 家目录 / UNC（`\\\\host\\share`）
        or _WIN_DRIVE_RE.match(s)  # `C:/x` / `C:\\x` / `C:x`（drive-relative）
    ):
        raise ProtocolError(
            f"rollout.argv 的 {flag}={s!r} 必须是相对路径（节点以 job 目录为 cwd）"
        )
    parts = Path(s).parts
    if ".." in parts:
        raise ProtocolError(f"rollout.argv 的 {flag}={s!r} 不得包含 ..（越界）")
    return s


def validate_rollout_spec(spec: object) -> dict:
    """校验 + 归一化 manifest 的 `rollout` 子字典（kind=iter）。失败抛 ProtocolError。

    归一化后 shape：
      {argv: [[str, ...], ...], wver: str, workers: int, game_timeout_sec: float, bun: str}
    argv 每项 = 一局的完整命令，**不含** bun 路径（worker 用自己的 bun；argv[0] 是
    exporter 脚本，受 `ROLLOUT_SCRIPTS` 白名单约束）。
    """
    if not isinstance(spec, dict):
        raise ProtocolError(f"manifest.rollout 必须是对象，收到 {type(spec).__name__}")
    allowed = set(ROLLOUT_SPEC_DEFAULTS) | {"argv"}
    out = dict(ROLLOUT_SPEC_DEFAULTS)
    for k, v in spec.items():
        if k not in allowed:
            raise ProtocolError(
                f"manifest.rollout 未知字段 {k!r}（允许：{sorted(allowed)}——拒绝，非忽略）"
            )
        out[k] = v
    raw_argv = spec.get("argv")
    if not isinstance(raw_argv, list) or not raw_argv:
        raise ProtocolError("manifest.rollout.argv 必须是非空数组（每局一项）")
    argv_out: list[list[str]] = []
    seen: set[tuple[int, int]] = set()
    for i, item in enumerate(raw_argv):
        if not isinstance(item, list) or len(item) < 2:
            raise ProtocolError(f"manifest.rollout.argv[{i}] 必须是长度 >=2 的字符串数组")
        argv = [str(x) for x in item]
        if not all(isinstance(x, str) for x in item):
            raise ProtocolError(f"manifest.rollout.argv[{i}] 含非字符串元素（拒收）")
        script = argv[0].replace("\\", "/").lstrip("./")
        if script not in ROLLOUT_SCRIPTS:
            raise ProtocolError(
                f"manifest.rollout.argv[{i}][0]={argv[0]!r} 不在白名单 {list(ROLLOUT_SCRIPTS)}（拒收）"
            )
        argv[0] = script
        st = _iter_flag_value(argv, "--stages")
        sd = _iter_flag_value(argv, "--seeds")
        for flag in _ITER_PATH_FLAGS:
            if flag not in argv:
                raise ProtocolError(f"manifest.rollout.argv[{i}] 缺 {flag}（节点无法定位产物）")
            j = argv.index(flag)
            if j + 1 >= len(argv):
                raise ProtocolError(f"manifest.rollout.argv[{i}] 的 {flag} 没有取值")
            argv[j + 1] = _iter_rel_path(argv[j + 1], flag)
        if (st, sd) in seen:
            raise ProtocolError(f"manifest.rollout.argv 重复声明同一局 (stage={st}, seed={sd})")
        seen.add((st, sd))
        argv_out.append(argv)
    out["argv"] = argv_out
    out["wver"] = str(out["wver"] or "")
    # workers：缺席 = 1（默认值）；**显式 0 拒收**（不静默改成 1——那会让「配错了」
    # 与「没配」长得一样，而并发配错正是那种「跑起来了但完全不是你要的」错误）。
    _w: object = 1 if out["workers"] is None else out["workers"]
    if isinstance(_w, bool):
        raise ProtocolError(f"manifest.rollout.workers 必须是整数，收到 {_w!r}")
    if isinstance(_w, int):
        _wn = _w
    elif isinstance(_w, str) and _w.isdigit():
        _wn = int(_w)
    else:
        raise ProtocolError(f"manifest.rollout.workers 必须是整数，收到 {_w!r}")
    if _wn < 1:
        raise ProtocolError("manifest.rollout.workers 必须 >= 1（显式 0 拒收，不静默改成 1）")
    out["workers"] = _wn
    _t: object = 0.0 if out["game_timeout_sec"] is None else out["game_timeout_sec"]
    if isinstance(_t, bool) or not isinstance(_t, (int, float)):
        raise ProtocolError(f"manifest.rollout.game_timeout_sec 必须是数字，收到 {_t!r}")
    if float(_t) < 0:
        raise ProtocolError(
            "manifest.rollout.game_timeout_sec 必须 >= 0（0 = 节点兜底硬顶，不是不限）"
        )
    out["game_timeout_sec"] = float(_t)
    out["bun"] = str(out["bun"] or "bun")
    return out


def iter_declared_entries(spec: dict) -> list[tuple[str, str, int, int]]:
    """rollout 规格 → 声明的 data_fp 条目集（与 argv 一一对应，不可能漂）。"""
    wver = str(spec.get("wver") or "")
    ents: list[tuple[str, str, int, int]] = []
    for argv in spec["argv"]:
        st = _iter_flag_value(list(argv), "--stages")
        sd = _iter_flag_value(list(argv), "--seeds")
        ents.append((shard_name(st, sd), wver, st, sd))
    return ents


def iter_expected_data_fp(spec: dict) -> str:
    """kind=iter 的 manifest.data_fp = 对**声明集**的 data_fp（hub 算、节点复算比对）。"""
    return data_fp_entries(iter_declared_entries(spec))


# ------------------------------------------------------------------ payload
# ---- payload 容器（2026-09-10：zip/deflate -> tar.xz）----
# 实测（20 个真实 c5-margin shard，裸 22.3 MB，×7.5 折算 150 份）：
#   ZIP_DEFLATED(6)  241,382 B / 1.8 s
#   ZIP_LZMA         187,443 B / 9.0 s   （只 −22.3% 且慢 5×，已否决）
#   tar.xz(preset=3) 123,788 B / 1.9 s   <== 采用：体积 −48.7%，打包耗时持平
# 折算真实 payload 3.83 MB -> ~1.96 MB，下载 2.3 s -> ~1.2 s。stdlib，无新依赖。
# 解析端**双读**（zipfile.is_zipfile 判别）⇒ 旧 hub 产的 payload.zip 与新 hub 产的
# payload.tar.xz 对新旧 worker 都能工作。
PAYLOAD_NAME = "payload.tar.xz"
PAYLOAD_LEGACY_NAMES: tuple[str, ...] = ("payload.zip",)
#: M3：TS 运行时 zip 在 job 目录内的文件名（`GET /jobs/{id}/ts_code` 服务它）。
TS_CODE_NAME = "ts_code.zip"
#: 节点确定性失败标记在 job 目录内的文件名（`POST /jobs/{id}/fail` 写、`GET
#: /jobs/{id}/result` 读；存在 = 这个 job 不会有结果，等下去只会等满超时）。
#: 训练侧**重发同一个 job** 时（同幂等键 → 同 job_id）由 `publish_job` 清除。
FAIL_NAME = "fail.json"
#: 失败原因回传体上限（人读的诊断字符串，1 个文本块足够；防大体打爆 hub 磁盘）。
FAIL_BODY_MAX = 64 * 1024

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
#: 控制台「开课」写它、「停课」删它；训练侧（`rl/loop_plan.enabled_courses`）与 hub
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

# ---- worker 能力自报（离线训练模式，2026-09-19）----
# 离线课（`kind="run"` 整段）与在线课（逐轮）对 worker 的要求不同：前者要求节点
# **自己跑完整段**（rollout + PPO 全在节点、计划随 job 走）。所以「谁能领离线课」不能靠
# 猜，要由 worker 自己声明能力。用户口径：离线模式「也支持带特别标识的云端 worker 在线
# 领取」——标识语义 = 能力，不是课程绑定（课程与 worker 正交：带标 worker 仍可领在线课）。
#: 能力头（轮询/peek 面）：`X-Battle-Offline: 1` = 本会话能自主跑完整段。
OFFLINE_CAP_HEADER = "X-Battle-Offline"
#: 头的规范值（写 1；解析放宽到常见真值）。
OFFLINE_CAP_VALUE = "1"


def has_offline_capability(raw: object) -> bool:
    """能力头 → 布尔。缺头 / 空 / `0` / `false` 一律 = **无能力**。

    只认白名单真值（不做「非空即有」这类宽松推断）：能力判错的方向是明确的——
    低估只是少一个 worker 领离线课（看得见：队列不降），高估会让一个只会逐轮的
    worker 领走整段 job 并卡在那里（看不见）。
    """
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")

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
#: kind=iter 的 payload 内要点名的 init 权重文件名（节点跑 rollout 的 --weights）。
INIT_WEIGHTS_NAME = "init_weights.json"
#: 发布端自检要求重打时写进归档根的**扰动**文件名（plan/accident.plan.md §4.6，2026-09-21）。
#: 它**不是数据**：存在的唯一意义是让重打的字节与上一次不同——打包是确定性的
#: （shard 排序 + tar 记源文件 mtime、lzma 确定性），不扰动就会逐字节相同，旧判别
#: （`zipfile.is_zipfile` 的 EOCD 启发式）在同一份字节上**永远**为真 ⇒ 重打闭环不收敛。
#: worker 侧忽略根级未知文件（只按名取 init_weights.json / plan.json，shard 靠扫描
#: 带 manifest.json 的子目录），故向前兼容。
PAYLOAD_PERTURB_NAME = "payload.perturb"
# 标注成 Literal：typeshed 的 tarfile.open("w:xz") 重载要求 preset 为 Literal[0..9]，
# 普通 int 过不了 mypy。**改档位时这里要同步改**（比如变 5 就写 Literal[5]）。
#
# ⛔ **B6（preset 3→6）已量、不采用，别再重测**（2026-09-17，3 份**真** payload ×
#    240 shard、走本函数、每份独立跑）：体积只 **−2.6…−3.0%**（~34 KB），打包却
#    **+188…+199%**（3.2s → 9.4s，即关键路径 **+6.2…+6.5s/轮**）；解包未见收益
#    （5.4→5.8 / 5.7→6.3 / 6.4→5.9 s，噪声内）。按隧道实测 ~3.5 Mbps，那 34 KB 只值
#    ~0.08s 传输 ⇒ **用 +6.3s CPU 换 0.08s 是净亏**，且计划 §4.2 的门槛是「收益 <10%
#    就别做」。同批真数据另证 B1/B2/B4：同一批 job 原盘 payload 2,045,276/2,034,536/
#    2,074,996 B → 仅打 shard 后 1,198,096/1,187,392/1,227,432 B（**−41.4%/−41.6%/−40.8%**）。
PAYLOAD_XZ_PRESET: Literal[3] = 3


def blob_path(job_dir: str | Path, name: str) -> Path:
    """内容寻址 blob 在 job 目录内的落盘名（M2；hub 写、pull worker 取）。

    `name` ∈ BLOB_NAMES（opt/ref/demo）。raw 字节原样存（无 base64），sha 即键。
    """
    if name not in BLOB_NAMES:
        raise ProtocolError(f"未知 blob 名 {name!r}（只接受 {BLOB_NAMES}）")
    return Path(job_dir) / f"blob.{name}"


def find_payload(job_dir: str | Path) -> Path | None:
    """定位 job 目录下的 payload（优先新名 tar.xz，回退旧名 zip）——新旧互通。"""
    jd = Path(job_dir)
    for name in (PAYLOAD_NAME, *PAYLOAD_LEGACY_NAMES):
        p = jd / name
        if p.exists():
            return p
    return None


def _add_bytes(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    """把一个内存字节串写进 tar（避免为 manifest 落临时文件）。"""
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


def _extract_archive(src: Path, dest: Path) -> None:
    """解包 payload 归档：**双读** tar 系 / zip（按内容**行为**判别，不看扩展名）。

    ★ 判别顺序 = **先 tar 后 zip**（2026-09-21 事故，plan/accident.plan.md §4）：
    原实现先问 `zipfile.is_zipfile()` —— 那是 stdlib 的 EOCD 形似字节启发式，xz 压缩
    数据里恰好出现该形状时**会误报为 True**。实测事故：3501788 字节的**完好** tar.xz
    被判成 zip ⇒ `BadZipFile: Bad offset for central directory` ⇒ worker 当瞬态重认领
    ⇒ 同一份毒包每 5 分钟复现、零告警空转 3.5 小时。

    反过来先试 tar 是**严格更可靠**的判别：tar 系靠 magic/透明度（`r:*`）认领，zip 的
    local header（`PK\x03\x04`）永远过不了 tar 头验证 ⇒ 行为判别天然 try/except，不再
    依赖任何启发式；legacy zip 包走后面的回退分支（tar.xz 化之前的包仍可解）。

    打不开 = **内容决定性**失败 ⇒ `ProtocolError`（不是 `BadZipFile`/`TarError`）：
    同一份字节重领永远不会自愈，必须让 worker 走确定性上报而不是重认领。
    `OSError`（磁盘/权限）例外——那是基础设施瞬时故障，原样抛出交由重认领处理。
    """
    try:
        with tarfile.open(src, "r:*") as tf:
            try:
                tf.extractall(dest, filter="data")
            except TypeError:  # Python < 3.12 无 filter 参数
                tf.extractall(dest)
        return
    except OSError:
        raise  # 磁盘/权限类 = 瞬时基础设施问题，不归容器判别
    except Exception as e:  # 非 tar 系（含 legacy zip）/ 损坏 —— 落 zip 分支再判一次
        tar_err: BaseException = e
    if not zipfile.is_zipfile(src):
        raise ProtocolError(
            f"payload 容器双读失败（tar 侧 {type(tar_err).__name__}: {tar_err}）——"
            "内容确定性失败，重领同一份字节不会自愈"
        ) from tar_err
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(dest)
    except Exception as e:
        raise ProtocolError(
            f"payload zip 解包失败（内容确定性）：{type(e).__name__}: {e}"
        ) from e


def pack_payload(
    shard_dirs: Sequence[str | Path],
    manifest: dict,
    out_path: str | Path,
    *,
    extra_files: Sequence[str | Path] | None = None,
    perturb: bytes | None = None,
) -> str:
    """把 shard 目录（npy + manifest.json）打成 **tar.xz**，写 `out_path`。

    容器演进（2026-09-10）：原为 zip/deflate —— 实测 tar.xz(preset=3) 体积 −48.7%
    而打包耗时持平（stdlib、无新依赖），解析端 `unpack_payload` 双读兼容。
    布局不变：每个 shard 目录整体进入（目录名 rl_s{stage}_seed{seed}/…），根下再写
    一份 manifest.json（payload_sha256 占位空串——最终哈希由调用方对**本函数产出的
    字节**计算后回填 job 记录，worker 以 job 记录的 payload_sha256 对原始下载字节
    校验，D1——防隧道截断）。

    返回文件字节 sha256。调用方拿到后应把 sha 写入 job 记录/账本。

    `perturb`（§4.6，2026-09-21）：非 None 时把这段字节以 `PAYLOAD_PERTURB_NAME` 写进
    归档根——发布端自检要求重打时用它**显式扰动**字节（确定性打包下唯一的换字节手段）。
    """
    zpath = Path(out_path)
    zpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = zpath.with_suffix(zpath.suffix + ".tmp")
    with tarfile.open(tmp, "w:xz", preset=PAYLOAD_XZ_PRESET) as tf:
        for d in shard_dirs:
            p = Path(d)
            if not p.is_dir():
                raise ProtocolError(f"pack_payload: shard 目录不存在 {p}")
            for f in sorted(p.iterdir()):
                if f.is_file():
                    tf.add(f, arcname=f"{p.name}/{f.name}")
        # 额外文件（落归档**根**：init_weights.json / plan.json 等按名取用）。
        # 2026-09-17：从 hub_client.pack_payload_zip 合并进来——原来两个打包器
        # （一个带 extra 一个不带）各自维护 tar.xz 口径，半离线段又需要一个带
        # extra 的，第三个副本毫无道理：统一到这里，hub 侧那个改为转调。
        for xf in extra_files or ():
            fx = Path(xf)
            if fx.is_file():
                tf.add(fx, arcname=fx.name)
        # 显式扰动（仅发布端自检要求重打时；见 `PAYLOAD_PERTURB_NAME`）。写在 extra 之后、
        # 归档根，非 shard 目录 ⇒ 不进 shard 名单、不影响任何按名取用的文件。
        if perturb is not None:
            _add_bytes(tf, PAYLOAD_PERTURB_NAME, bytes(perturb))
        # M2（B1）：不再写根级占位 manifest.json —— worker.py:896 一直把它当
        # `_unused_manifest` 丢弃（~0.89MB/轮纯冗余）。权威 manifest 走 job 记录
        # （peek/claim 返回），本函数只负责搬运 shard 数据。`manifest` 形参保留
        # 只为调用方签名兼容（不再进字节）。
    tmp.replace(zpath)
    return hashlib.sha256(zpath.read_bytes()).hexdigest()


def unpack_payload(payload_path: str | Path, dest: str | Path) -> tuple[dict, list[str]]:
    """解包 payload → (manifest, shard_dir_paths)。**双读**：zip 与 tar.xz 都支持。

    shard_dir_paths 为解包后落在 dest 下的各 shard 目录（含 manifest.json），
    供 worker 的 load_episodes 消费。返回的 manifest 为归档内副本（payload_sha256
    为占位空串）——**不作权威校验**；worker 必须用 job 记录（peek/claim 返回）
    的 manifest 做 payload_sha256 / commit / mode 等全部校验（本函数只解包）。
    """
    dest_p = Path(dest)
    dest_p.mkdir(parents=True, exist_ok=True)
    _extract_archive(Path(payload_path), dest_p)
    # 解包产物**非空**断言（2026-09-21，§4）：判别反转后"tar 打开成功却解出空包"是新
    # 路径特有的失败形态（旧实现误判时是直接抛错）。空包必须响亮——否则下游按"零 shard"
    # 静默继续（PPO 拿到空语料 = 比报错更坏的静默失败）。
    if not any(dest_p.iterdir()):
        raise ProtocolError(
            f"payload 解包产物为空（{payload_path}）——容器判别/解包路径异常，拒绝静默继续"
        )
    # M2（B1）：新 hub 产的 payload 不含根级 manifest.json（占位副本已删）——
    # 有则读、无则返回 {}（旧 payload 仍兼容；权威校验全走 job 记录 manifest）。
    mp = dest_p / "manifest.json"
    if mp.exists():
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {}
    shard_dirs: list[str] = []
    for p in sorted(dest_p.iterdir()):
        if p.is_dir() and (p / "manifest.json").exists():
            shard_dirs.append(str(p))
    return manifest, shard_dirs


# ------------------------------------------------------------------ idempotency


def idempotency_key(manifest: dict) -> tuple:
    """D1 幂等键 = (runId, it, init_weights_fp, data_fp)。云 worker 崩溃重拉同一
    job 时按此去重；hub 账本记 job 状态，不重复发包已完成 job。"""
    return (
        manifest["runId"],
        manifest["it"],
        manifest["init_weights_fp"],
        manifest["data_fp"],
    )


def job_id(manifest: dict) -> str:
    """job_id 派生：sha256(幂等键)[:16]——同一 job（幂等键相同）永远同一 job_id，
    天然幂等（hub kill -9 重启后重发布不产生重复 job）。"""
    h = hashlib.sha256()
    for part in idempotency_key(manifest):
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


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


#: kind=iter 结果必须回传的采集报告字段（照 `rl/reports.combine_reports` 的输出名）。
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


# ---- result 回传字段的传输编码 ----
# 上行只有 220 KB/s（实测），而两字段都是 JSON 文本 / torch 张量，gzip level 6 实测省
# 29.6%（weights.json 1.35×、opt tar 1.45×，CPU 仅 ~0.05 s）。level 9 换不到额外收益。
_GZIP_MAGIC = b"\x1f\x8b"
_WIRE_GZIP_LEVEL = 6


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


# ---- result 回传体的 v2 线格式（方案B：gzip 裸二进制，省掉 base64 的 33%）----
# 布局:  MAGIC(5) | uint32 BE header_len | header_json | blob0 | blob1 | ...
# header = {"result": <去掉二进制字段的 dict>, "blob_lens": [len0, len1]}
# blob 顺序固定 = BLOB_FIELDS。
# 动机（2026-09-10 实测）：方案A（gzip+base64）线上 1,150,292 B —— base64 白占 33%。
# 改裸二进制后 862,717 B（再省 25%，相对未压缩的 1,634,596 省 47.2%），上行 ~5.2 -> ~3.9 s。
# hub 只做 base64（stdlib），并把结果**还原成方案A 的字符串形态**再落盘
# ⇒ result.json 格式与下游 hub_client.decode_* 零改动。
WIRE_V2_MAGIC = b"BRV2\n"
WIRE_V2_CONTENT_TYPE = "application/x-battle-result-v2"
BLOB_FIELDS: tuple[str, ...] = ("weights_json", "opt_tar_b64")
_HDR_LEN_BYTES = 4


# ---- /job 提交体的 v2 线格式（M2 B5：payload/code/blob 去 base64，省 25%）----
# 布局:  MAGIC(5) | uint32 BE header_len | header_json | payload | [code] | blob0 | ...
# header = {"manifest": m, "has_code": bool, "blob_names": [...], "lens": [payload, code?, *blobs]}
# 动机：push body 原为 JSON + `payload_b64`（base64 白占 33%）。拆成裸二进制后
# 1.6MB → 1.2MB。解析端保留 JSON 退路（旧节点/旧 hub 混跑时降级）。
WIRE_JOB_MAGIC = b"BRJ2\n"
WIRE_JOB_CONTENT_TYPE = "application/x-battle-job-v2"


def pack_job_v2(
    manifest: dict,
    payload: bytes,
    code: bytes | None,
    blobs: dict[str, bytes] | None = None,
    ts_code: bytes | None = None,
) -> bytes:
    """job 提交体 → v2（payload/code/ts_code/blob 走裸二进制段）。blobs 按名字典序。

    段序固定：payload, code?, ts_code?, *blobs。ts_code（M3：节点跑 rollout 需要的
    TS 运行时 zip）**只有 kind=iter 才有**——其余 job 逐字节与以前一致。
    """
    bl = dict(blobs or {})
    names = sorted(bl)
    lens = [len(payload)]
    if code is not None:
        lens.append(len(code))
    if ts_code is not None:
        lens.append(len(ts_code))
    lens.extend(len(bl[n]) for n in names)
    # `has_ts` **只在该段真的存在时才写**：非 iter 的 job 体因此逐字节与以前一致
    # （本仓的「旧轮字节不变」纪律；解包侧 get("has_ts", False) 兼容缺席）。
    head: dict = {
        "manifest": manifest,
        "has_code": code is not None,
        "blob_names": names,
        "lens": lens,
    }
    if ts_code is not None:
        head["has_ts"] = True
    hdr = json.dumps(head, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    parts = [WIRE_JOB_MAGIC, struct.pack(">I", len(hdr)), hdr, payload]
    if code is not None:
        parts.append(code)
    if ts_code is not None:
        parts.append(ts_code)
    parts.extend(bl[n] for n in names)
    return b"".join(parts)


def unpack_job_v2(body: bytes) -> dict:
    """v2 job 体 → 旧 JSON 形状的 dict（payload_b64/code_b64/blobs）——服务端零下游改动。

    任何长度不符/尾部余料都响亮拒绝，不静默截断（同 unpack_result_v2 规矩）。
    """
    if not body.startswith(WIRE_JOB_MAGIC):
        raise ProtocolError("v2 job 体缺 BRJ2 魔数")
    off = len(WIRE_JOB_MAGIC)
    (hdr_len,) = struct.unpack(">I", body[off : off + _HDR_LEN_BYTES])
    off += _HDR_LEN_BYTES
    try:
        hdr = json.loads(body[off : off + hdr_len].decode("utf-8"))
        manifest: dict = hdr["manifest"]
        has_code: bool = bool(hdr["has_code"])
        # has_ts 缺失（旧 hub 产的 v2 体）= 无 ts_code 段（旧行为，additive）。
        has_ts: bool = bool(hdr.get("has_ts", False))
        names: list = hdr["blob_names"]
        lens: list = hdr["lens"]
    except (KeyError, ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"v2 job 头解析失败: {e}") from None
    off += hdr_len
    expected = 1 + (1 if has_code else 0) + (1 if has_ts else 0) + len(names)
    if len(lens) != expected:
        raise ProtocolError(f"v2 job lens 长度 {len(lens)} != {expected}")
    out: dict = {"manifest": manifest}

    def _chunk(n: int, what: str) -> bytes:
        nonlocal off
        blob = body[off : off + n]
        if len(blob) != n:
            raise ProtocolError(f"v2 job 体截断：{what} 期望 {n} 字节，实得 {len(blob)}")
        off += n
        return blob

    out["payload_b64"] = base64.b64encode(_chunk(int(lens[0]), "payload")).decode("ascii")
    i = 1
    if has_code:
        out["code_b64"] = base64.b64encode(_chunk(int(lens[i]), "code")).decode("ascii")
        i += 1
    if has_ts:
        out["ts_code_b64"] = base64.b64encode(_chunk(int(lens[i]), "ts_code")).decode("ascii")
        i += 1
    if names:
        bm: dict = {}
        for name in names:
            bm[str(name)] = base64.b64encode(_chunk(int(lens[i]), f"blob {name}")).decode("ascii")
            i += 1
        out["blobs"] = bm
    if off != len(body):
        raise ProtocolError(f"v2 job 体尾部有 {len(body) - off} 字节多余数据")
    return out


def pack_result_v2(result: dict) -> bytes:
    """result dict（二进制字段为方案A 的 base64 串）-> v2 体。

    只把 BLOB_FIELDS 从 JSON 里搬出来当二进制段，**不重新压缩**（入参已是 gzip 后的
    base64，解开即是 gzip 字节）；JSON 头保留其余全部字段 ⇒ 还原后语义逐字段一致。
    """
    blobs = [base64.b64decode(str(result.get(f, "") or "").encode("ascii")) for f in BLOB_FIELDS]
    head = {k: v for k, v in result.items() if k not in BLOB_FIELDS}
    hdr = json.dumps(
        {"result": head, "blob_lens": [len(b) for b in blobs]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return WIRE_V2_MAGIC + struct.pack(">I", len(hdr)) + hdr + b"".join(blobs)


def unpack_result_v2(body: bytes) -> dict:
    """v2 体 -> result dict（二进制字段还原成方案A 的 base64 串）。hub 侧用。

    只依赖 base64/json/struct，**不需要 gzip**（blob 原样重新 base64 即可）。
    任何长度不符/尾部余料都响亮拒绝，不静默截断。
    """
    if not body.startswith(WIRE_V2_MAGIC):
        raise ProtocolError("v2 体缺 BRV2 魔数")
    off = len(WIRE_V2_MAGIC)
    (hdr_len,) = struct.unpack(">I", body[off : off + _HDR_LEN_BYTES])
    off += _HDR_LEN_BYTES
    try:
        hdr = json.loads(body[off : off + hdr_len].decode("utf-8"))
        head: dict = hdr["result"]
        lens = hdr["blob_lens"]
    except (KeyError, ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"v2 头解析失败: {e}") from None
    off += hdr_len
    if len(lens) != len(BLOB_FIELDS):
        raise ProtocolError(f"v2 blob_lens 长度 {len(lens)} != {len(BLOB_FIELDS)}")
    for field, n in zip(BLOB_FIELDS, lens, strict=True):
        blob = body[off : off + n]
        if len(blob) != n:
            raise ProtocolError(f"v2 体截断：{field} 期望 {n} 字节，实得 {len(blob)}")
        off += n
        head[field] = base64.b64encode(blob).decode("ascii")
    if off != len(body):
        raise ProtocolError(f"v2 体尾部有 {len(body) - off} 字节多余数据")
    return head


def _pack_wire(raw: bytes) -> str:
    """原始字节 → gzip → base64（JSON 安全的回传字段）。"""
    return base64.b64encode(gzip.compress(raw, compresslevel=_WIRE_GZIP_LEVEL)).decode("ascii")


def _unpack_wire(b64: str) -> bytes:
    """base64 → (必要时 gunzip) → 原始字节。

    靠 gzip 魔数自动判别，**兼容旧格式**（未压缩的 base64）—— 历史 result.json 与
    已在途的 payload 都能照常解出。
    """
    raw = base64.b64decode(b64.encode("ascii"))
    if raw[:2] == _GZIP_MAGIC:
        return gzip.decompress(raw)
    return raw


def encode_weights_json(wj: bytes) -> str:
    """weights_json 传输编码（gzip + base64）。"""
    return _pack_wire(wj)


def decode_weights_json(b64: str) -> bytes:
    """weights_json 传输解码（hub 落盘 args.out 前用）；兼容未压缩的旧格式。"""
    return _unpack_wire(b64)


def encode_opt_tar(tar_bytes: bytes) -> str:
    """opt tar 传输编码（gzip + base64）。"""
    return _pack_wire(tar_bytes)


def decode_opt_tar(b64: str) -> bytes:
    """opt tar 传输解码；兼容未压缩的旧格式。"""
    return _unpack_wire(b64)
