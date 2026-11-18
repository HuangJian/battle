"""`common/scheduling.py` — 调度**策略**（L0，stdlib-only）：B8 第二刀，2026-11-18。

它从 `common/protocol.py` 整段搬来（逐字节），理由写在 `python.refactor.md` §5.1：
那个模块同时装着**线格式**（`PROTO` / 编解码 / manifest 与 result 的校验）与**调度策略**
（优先级表 / push 选人 / 节点能力形状）。两件事的变化速度不同 —— 改一个掉队阈值或选人闸，
评审不该被迫读一遍线协议；反过来改 wire 也该能一眼看出有没有动到策略。

搬过来的三族（都是纯函数或常量表，没有一个碰 I/O）：

* **优先级**：`PRIORITY_*` 五档 + `job_priority`（§1.4 表的唯一实现，时基只认 `computing_at`）
  + `STRAGGLER_SEC` / `JOB_CANCEL_POLL_SEC` 两个阈值、`CLAIM_MODE_*`（策略侧的词，wire 只承载）；
* **hub 中介推送**：`DISPATCH_HUB_PUSH` 标记 + `PUSH_*` 节奏/冷却/告警阈值 + `push_worker_id_of`；
* **节点能力**：`CAP_*` / `NODE_CAPABILITIES` / `NODE_KINDS` + `node_capabilities` /
  `sampler_nodes` / `push_worker_from_node`（判据与训练侧 `trainer/loop_steps._gpu_push_nodes`
  **同一把尺子**，两边不一致的症状是 job 永远躺在队首）+ `pick_push_worker` / `push_job_wants_hub_push`。

依赖面：**只有 stdlib**（`collections.abc` 的三个泛型）。不 import `common.protocol` —— 依赖方向是
「协议不解释策略」，不是反过来。`common/protocol.py` 对每个名字留了 `X as X` 门面，历史 import
一行都不用改（守卫 `tests/common/test_scheduling_split.py`）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

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
#: GPU push 的**预推深度**（缺省 1，2026-10-24）：worker 在跑时它还有一条有界接单队列
#: （`remote.worker_server.WORKER_QUEUE_MAX`）——允许 hub 先把下一份推过去，把 payload
#: 传输叠到当前 job 的 PPO 上（否则每次换 job 都是「传完才开算」的纯空转）。
#: `0` = 真旧行为（只推空闲机；回退档）。预推会把租约起点提前，故不写死、由 `--push-ahead` 定。
PUSH_AHEAD_DEFAULT = 1
#: 跑死过某份 job 的 worker 冷却时长（秒）：冷却期内不再给它派同一份 job
#: （「改为推送其它 worker」的落地；过期自动解禁，不永久拉黑）。
PUSH_COOLDOWN_SEC = 600.0
#: **单段运输**的告警阈值（秒，2026-10-30）。链路退化时训练只会「变慢」，不会报错——
#: 真机那一晚：2.2MB 上传 3.5s→45~215s、220KB 结果取回 ~9s→234s，而 hub 日志对这两段
#: 一个字都没有（只有「已推送（上传 151.884s）」这种正常记账行的括号）。判据分开给：
#: 正常档 upload ≈ 3–6s、结果取回 ≈ 5–15s，取 5×/4× 余量，避免 LAN 抖动误报。
PUSH_SLOW_UPLOAD_SEC = 30.0
PUSH_SLOW_RESULT_SEC = 60.0
#: 租约持有人身份前缀：`push:<worker id>`。带前缀是为了让 `/admin/queue` 的
#: inflight 持有人、避让记录一眼能分清「云机自己报的名字」与「hub 代持的推送」。
PUSH_WORKER_PREFIX = "push:"


def push_worker_id_of(worker_id: str) -> str:
    """push 派发的租约持有人身份（`push:<id>`；已带前缀则原样返回——幂等）。"""
    w = str(worker_id or "")
    return w if w.startswith(PUSH_WORKER_PREFIX) else f"{PUSH_WORKER_PREFIX}{w}"


# ---- 节点能力键（2026-10-23，plan/push-mode-notebooks §3.2）---------------------
#: push 类节点在 rl-config `nodes[]` 里的**能力键**（布尔字段）。能力键是唯一持久事实；
#: 注册 wire 上的 `kind` 只是那一次请求的说法，不落盘（见 `hub.admin` 的合并语义）。
CAP_GPU_PUSH = "gpu_push"
CAP_OFFLINE_PUSH = "offline_push"
#: 全部能力键（切换 kind 时按这份名单清理 ⇒ 新增能力不会漏清）。
NODE_CAPABILITIES = (CAP_GPU_PUSH, CAP_OFFLINE_PUSH)
#: 注册 `kind` 的白名单。`sampler` = 采样节点（老客户端**根本不发** `kind`，见 §4.2-P0）。
NODE_KINDS = ("sampler", CAP_GPU_PUSH, CAP_OFFLINE_PUSH)


def node_capabilities(node: object) -> list[str]:
    """一个 `nodes[]` 条目声明了哪些能力键（读面/排障用；顺序稳定）。"""
    if not isinstance(node, dict):
        return []
    return [cap for cap in NODE_CAPABILITIES if node.get(cap)]


def sampler_nodes(nodes: object) -> list[dict]:
    """`nodes[]` 里**采样/评估侧能派活**的条目：`enabled` 且没声明任何 push 能力键。

    为什么必须过滤（2026-10-24 真机）：push 节点跑的是 `remote/worker_server`（GPU）或离线
    push 面，探活面是 `GET /ping`（Bearer），**没有** `/v1/ping`。自动注册把 `offline-colab`
    写进 `nodes[]` 之后，rollout 与 eval 的每轮节点门都去 ping 它一次，换回一条
    `ping failed — excluded this round` / `ping 失败/超时`——纯噪声，还把「节点表里有 6 台」
    这种读数弄脏。判据与 `push_worker_from_node` 相反相成：那边按能力键挑，这边按能力键剔。
    """
    if not isinstance(nodes, (list, tuple)):
        return []
    return [
        n
        for n in nodes
        if isinstance(n, dict) and n.get("enabled", True) and not node_capabilities(n)
    ]


def push_worker_from_node(node: object, capability: str = CAP_GPU_PUSH) -> dict | None:
    """rl-config `nodes[]` 条目 → push worker；不是该能力的 push 节点 → None。

    判据与训练侧 `trainer/loop_steps._gpu_push_nodes` **同一把尺子**（能力键且
    `enabled` 缺省视为 true、url 非空）：两边若判据不同，就会出现「训练侧认为该推这台、
    hub 却认为一台都没有」——症状是 job 永远躺在队首（最难查的一种）。

    `capability` 缺省 `gpu_push`（GPU 派发器的既有语义，返回形状不变）；离线派发器传
    `CAP_OFFLINE_PUSH`。**返回值形状与能力无关**（`capability` 由登记表自己记）。

    返回 `{id, url, key, concurrency}`：`id` 缺省回落到 url（与训练侧日志口径一致），
    `concurrency` 缺省 1（gpu_push 节点线上就没有这个键——单 GPU 一次一份）。
    """
    if not isinstance(node, dict):
        return None
    if not node.get(capability) or not node.get("enabled", True):
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
    *,
    ahead: int = 0,
) -> dict | None:
    """挑一个**现在就能接活**的 worker；没有空闲者 → None。

    四道闸（全部必要，任缺一个都会把「推送」退化成「往死机器上撞」）：在线（最近一次
    探活通过且未达离场阈值）、自报不忙（`/ping` 的 busy）、在飞数 < concurrency、不在
    本次避让名单里（刚跑死这份 job 的那台）。

    顺序 = 注册序（稳定 ⇒ 行为可测）；**不按快慢排序**——「谁空谁接」是既定口径
    （EWMA 快慢 hold 已被用户裁定删除，见 `trainer/dispatch.py` 注释）。

    `ahead > 0`（2026-10-24）追加**第二轮**：所有空闲机都接不了时，忙机若还有接单位
    （自报 `queued < ahead` 且 `在飞 < concurrency + ahead`）也可以接——它的 payload 传输
    叠在当前 job 的 PPO 上，worker 侧有界 FIFO 负责「当前 job 结束即顶上」。只在第一轮
    无人可接时才走第二轮 ⇒ 有空闲机时位置口径逐字节不变。

    为什么 `ahead` 缺省 0：预推把租约的起点提前到「job 还在队里」的时刻，这不是所有
    调用方都愿意接受的时钟；GPU 派发器显式传 `--push-ahead`（缺省 1）。
    """
    blocked = set(avoid)

    def _fits(w: dict, wid: str, boost: int) -> bool:
        limit = int(w.get("concurrency") or 1) + int(boost)
        return int(inflight.get(wid, 0)) < limit

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
        if not _fits(w, wid, 0):
            continue
        return w
    if int(ahead) <= 0:
        return None
    for w in workers:
        if not isinstance(w, dict):
            continue
        wid = str(w.get("id") or "")
        if not wid or wid in blocked:
            continue
        if not w.get("online"):
            continue
        if not w.get("busy"):
            continue  # 第一轮已经处理过空闲机
        if int(w.get("queued") or 0) >= int(ahead):
            continue  # 接单队列已经够深（多推一份只是把它排得更长）
        if not _fits(w, wid, int(ahead)):
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
