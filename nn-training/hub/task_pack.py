"""hub/task_pack.py — 任务包新鲜度门 + 离线任务读数的**纯判据**（叶子模块）。

## 为什么单独一个模块

这套判据有两个读者，而它们在同一侧的**两个方向**上：

* `hub/offline.py`（路由混入，L0 附近）：`GET /offline/task-pack` 的**过期门**（§8.3）与
  **缺包自愈门**（§3.4）；
* `hub/queue_offline.py`（离线任务清单 + 租约混入）：`GET /offline/tasks` 的清单读数
  （`task_state` / `TASK_STATE_RANK` / `_file_sha256`）。

在 origin 里它们住 `hub_server.py` 的**模块级**（那时 hub 是一个文件、一个层）。S4 把 hub 拆成
`hub/*` 之后，把这批助手挂在 `hub/http_face.py`（L5）上会让上面两个低层读者**向上** import ——
`tests/helpers/remote_dag.py` 的账本当场报反向边。于是按本仓的老规矩（第十刀起）：**共同依赖下沉**，
住这一层叶子；谁用谁往下拿。

## 这块管的是什么（原 module 级注释，随代码搬来）

`GET /offline/task-pack` 原来只递文件，而**课程状态会前进、包不会**（回传轮把
`tmp/<课>/weights.json` 推着走，`_land_offline_round_extras`）。云机整机重启、产物全丢时，
只要 resume 锚点那条腿断了，兜底起点就是**开课时那份旧包** —— 从旧起点重跑几十上百轮，
白烧算力。过期的包比没有包更危险（404 只让云机多等一拍），所以宁可偏严。

## 依赖方向

`hub.task_pack → {common.protocol, common.net_http}`（严格向下；记账字典住本模块，
用锁 —— `reset_*` 与触发路径会被并发请求打到）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from threading import Lock

from common.net_http import urlopen as _net_urlopen
from common.protocol import PLAN_NAME

# ── 任务包新鲜度门（plan/offline-rerun-local-first §8，2026-09-24）────────────────
#
# 为什么 hub 要管这个：`GET /offline/task-pack` 原来只递文件，而**课程状态会前进、包不会**
# （回传轮把 `tmp/<课>/weights.json` 推着走，`_land_offline_round_extras`）。云机整机重启、
# 产物全丢时，只要 resume 锚点那条腿断了，兜底起点就是**开课时那份旧包** —— 从旧起点重跑
# 几十上百轮，白烧算力。过期的包比没有包更危险（404 只让云机多等一拍），所以宁可偏严。

#: 包索引名（与 `remote/bundle.py::BUNDLE_INDEX` 逐字相同；本模块不 import bundle 以免边，测试守）。
TASK_PACK_INDEX_NAME = "tools.task.json"
#: 控制台地址（触发「重导任务包」用）：**调用时读** env（测试要能 monkeypatch）。
CONSOLE_URL_ENV = "BCITY_CONSOLE_URL"
DEFAULT_CONSOLE_URL = "http://127.0.0.1:8900"
#: 触发控制台的超时（秒）：控制台不在本机/不可达时必须**快速降级**，不能把取包请求拖住。
TASK_PACK_TRIGGER_TIMEOUT_SEC = 8.0
#: 同一门课两次触发的最小间隔（秒）：防多台云机连打，也防"刚导完又判过期"的抖动。
TASK_PACK_STALE_THROTTLE_SEC = 600.0
#: 连续触发上界：到顶仍判过期 ⇒ 照发旧包 + 告警（**不 brick 云机**）。
#: 为什么必须有上界：只要还有别的 worker 在回传，`weights.json` 就一直在动 ⇒ 判据是**移动靶**，
#: 没有上界时云机会被 409 卡到 deadline（30 分钟）然后 `SystemExit`（评审 F5）。
TASK_PACK_STALE_TRIGGER_LIMIT = 2
#: 「**缺包**」的上界（比过期那条宽一档）：缺包是**确定要造一份**，多试两次值；
#: 到顶仍没包 ⇒ 不再触发，只在 404 正文里指路手动（plan/offline-switch-auto-bundle §3.4）。
TASK_PACK_MISS_TRIGGER_LIMIT = 3
#: 控制台「自动交接」动作路径（hub → 控制台：写 rl-config + 导任务包；不写意图、不 pin）。
AUTO_HANDOFF_CONSOLE_PATH = "/api/autoOfflineHandoff"
#: 离线任务的六种状态（清单面，`plan/offline-task-discovery.plan.md` §3.1）：
#: `ready`（有新鲜包可领）/ `stale`（包在但起点已旧，领了要从旧起点跑）/ `no_pack`（还没导）/ `claimed`（有人持租）。
#: 第五种 `not_offline` 只在 `?include=all`（控制台排障）时出现——云机永远用默认清单。
#: 第六种 `completed`（2026-10-03，plan/auto-offline-handoff §3.6）：段末摘要报到跑满
#: ⇒ **不可再领**（二轮 P1-1），等人停课 / 重导包（包 sha 变 = 解封）。
TASK_STATE_READY = "ready"
TASK_STATE_STALE = "stale"
TASK_STATE_NO_PACK = "no_pack"
TASK_STATE_CLAIMED = "claimed"
TASK_STATE_NOT_OFFLINE = "not_offline"
TASK_STATE_COMPLETED = "completed"
#: 权威三态 + 两个正交维（plan/offline-online-status-switch §3.1 `authority_of` 的值域）：
#: `pinned_online` / `pinned_offline` / `auto` 是三态；`stopped`（开课标记不在）与
#: `not_offline`（冷课带 online 记录）是两个正交维。常量住叶子：离线路由面（`hub/offline.py`）
#: 与队列面（`queue_offline.py`）都要读它们。派生实现在 `queue_offline.authority_of`。
AUTHORITY_PINNED_ONLINE = "pinned_online"
AUTHORITY_PINNED_OFFLINE = "pinned_offline"
AUTHORITY_AUTO = "auto"
AUTHORITY_STOPPED = "stopped"
AUTHORITY_NOT_OFFLINE = "not_offline"
#: 离线租约「连续静默」阈值（秒，2026-10-05，plan/offline-online-status-switch §3.3）：
#: 租约持有人超过它没有一点心跳 ⇒ 判 `stale`，允许新盘**自动接管**（不必 `takeover=1`）。
#: 为什么住本模块（叶子）而不是 `queue_offline`：判据 `lease_verdict` 就在本模块，常量跟着判据走；
#: 这是**纯 hub 侧**语义（不进 `common/protocol.py`，两端共享协议面一字不改）。env 覆盖
#: `BCITY_OFFLINE_LEASE_STALE_SEC`（e2e/单测调秒级）。
#: ★ 与 job 侧 `ORPHAN_GRACE_SEC` 同值**不同义**：job 侧 = claim 后**零心跳**；离线段 = **连续静默**
#: （心跳线程整段在跑，慢网/挂起的人为短静默不该误杀）——别照抄 store_leases._lease_state。
OFFLINE_LEASE_STALE_SEC = 180.0
# ── 接管（hold）的进度活性（plan/worker-type-dispatch-model §1.2/§1.5.2，2026-10-07）──
#
# 新派发模型把三件事拆正交：**谁在跑**（worker 类型）/ **归谁独占**（hold）/ **还活着吗**（进度）。
# 本块只做第三件：**活性只认进度信号**，心跳（60s）只续租约 TTL —— 2026-10-05/06 的
# 「假活」（心跳活、进度死）正是把心跳当活性算出来的（docs/nn/remote-transport.md §68）。
#
# 为什么与 `AUTO_HANDOFF_PENDING_SEC` 分开写：两者都是 900s 是**巧合**（一个是导包窗口、
# 一个是接管活性），调一个会误伤另一个 —— plan §1.5.2-P0-1 明令**禁止合并常量**。
#: 接管后连续静默（无任何进度信号）超过它 ⇒ 判「掉线」（惰性判据：谁读谁算，不养清理线程）。
#: env 覆盖 `BCITY_HOLD_PROGRESS_STALE_SEC`（e2e/单测调秒级）——与 `offline_lease_stale_sec()`
#: 同款「调用时读 env」。
HOLD_PROGRESS_STALE_SEC = 900.0
#: hub 重启给**盘上恢复的 hold** 的宽限（秒）：把 `last_progress_at`/`touch_at` 抬到 `now - 它`。
#: 为什么需要：重启时盘上的时间是旧的，而 worker 大概率还活着（它不知道自己「被重启」了）——
#: 不抬就会把活着的盘判掉线，协作派发与本机 held 会一起解开（plan §1.5.4-P2-2）。
HOLD_RESTORE_GRACE_SEC = 300.0


def hold_progress_stale_sec() -> float:
    """生效的接管静默阈值（env 覆盖；非法 / 非正数 ⇒ 缺省）。**调用时读 env**（可 monkeypatch）。"""
    raw = os.environ.get("BCITY_HOLD_PROGRESS_STALE_SEC", "").strip()
    try:
        v = float(raw)
    except ValueError:
        return HOLD_PROGRESS_STALE_SEC
    return v if v > 0 else HOLD_PROGRESS_STALE_SEC


def hold_progress_at(hold: object) -> float:
    """hold 记录里的**进度时刻**（0.0 = 没有记录 / 值不合法）。纯函数。"""
    if not isinstance(hold, dict):
        return 0.0
    try:
        return max(0.0, float(hold.get("last_progress_at") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def hold_touch_at(hold: object) -> float:
    """hold 记录里的**最近一次合法接触**（心跳 ∨ 进度）：`touch_at`，缺则退回 `at`（接管时刻）。

    为什么单独一个字段：TTL 余量要从**接触**起算（心跳刷它），而活性只看进度 —— 两者分开，
    「心跳活、进度死」的假活才既不掉线也不被当成新鲜（plan §1.5.4-P2-1 的四象限）。
    """
    if not isinstance(hold, dict):
        return 0.0
    for key in ("touch_at", "at"):
        try:
            v = max(0.0, float(hold.get(key) or 0.0))
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    return 0.0


def hold_state(now: float, hold: object) -> str:
    """hold 的活性（纯函数）：`""` = 没有 hold · `live` · `stale`。

    判据只有一条：**进度**（`last_progress_at`）。没有任何进度信号的 hold 一律 `stale` ——
    「刚接管、还没打点」的那几秒由**恢复宽限**与「claim 本身记第一个进度锚」兜住，
    而不是靠把「零进度」当活。
    """
    if not isinstance(hold, dict) or not hold:
        return ""
    p = hold_progress_at(hold)
    if p <= 0.0:
        return "stale"
    return "stale" if float(now) - p > hold_progress_stale_sec() else "live"


def hold_restore_grace(now: float, stored: float) -> float:
    """恢复宽限（纯函数）：盘上的接触时刻 `stored` ⇒ 抬到 `max(stored, now - 宽限)`。**只抬不压**。"""
    return max(float(stored or 0.0), float(now) - HOLD_RESTORE_GRACE_SEC)


def hold_expires_in(now: float, hold: object, *, ttl_sec: float, stale_sec: float) -> float:
    """hold 的「距判掉线的剩余秒」= **min(进度余量, TTL 余量)**（纯函数）。

    读面（`holder_info` / `/offline/hold`）给的就是这一个数（P2-3：形状不变），语义从
    「TTL 余量」改成「还要多久会被判掉线」——旧读方显示「N 秒后过期」照样说得通。
    """
    if not isinstance(hold, dict) or not hold:
        return 0.0
    n = float(now)
    progress = hold_progress_at(hold)
    touch = hold_touch_at(hold)
    left_progress = (progress + float(stale_sec)) - n if progress > 0.0 else 0.0
    left_ttl = (touch + float(ttl_sec)) - n if touch > 0.0 else 0.0
    return max(0.0, min(left_progress, left_ttl))


#: 「离线盘在线」的窗口（秒）：与离线租约 TTL 同档 —— 取包腿的报到节奏就是这个量级。
OFFLINE_DISK_WINDOW_SEC = 900.0


def offline_lease_stale_sec() -> float:
    """生效的离线租约静默阈值（env 覆盖；非法/非正数 ⇒ 缺省）。**调用时读 env**（可 monkeypatch）。"""
    raw = os.environ.get("BCITY_OFFLINE_LEASE_STALE_SEC", "").strip()
    try:
        v = float(raw)
    except ValueError:
        return OFFLINE_LEASE_STALE_SEC
    return v if v > 0 else OFFLINE_LEASE_STALE_SEC
#: 离线腿的指路（2026-09-25 退役「发一份 kind=run 队列项」之后，离线课的唯一载体是任务包）。
OFFLINE_LEG_HINT = (
    "离线课不再经 hub 队列执行：云机用 battle.offline.ipynb 取任务包接手"
    "（/offline/tasks → /offline/task-pack → 跑完回传）"
)

#: 清单排序名次：`ready` 最前、`completed`/`not_offline` 最后；同级按**开课时间**升序
#: （`training-enabled.txt` 的 mtime，读不到 ⇒ `+inf` 排最后；租约见 `queue_offline`）。
#: 为什么 `claimed` 排在 `no_pack` 之前：前者是「有人在跑」、后者是「没人能跑」——
#: 一眼看出「活儿在动」比看出「缺东西」更接近清单的用途（下一批还有人问）。
TASK_STATE_RANK = {
    TASK_STATE_READY: 0,
    TASK_STATE_STALE: 1,
    TASK_STATE_CLAIMED: 2,
    TASK_STATE_NO_PACK: 3,
    TASK_STATE_NOT_OFFLINE: 4,
    TASK_STATE_COMPLETED: 5,
}
#: 同课程的触发账本（进程内；hub 重启即清——与租约同风格，重启后重触发一次无害）。
_TASK_PACK_TRIGGERS: dict[str, dict[str, float]] = {}
#: **缺包**用另一本账（与过期那条腿分开）：两本账在同一个重导窗口里各自记账，
#: 所以同一门课在窗口内最多被推 2 次（过期 1 + 缺包 1）——这是刻意的，不是 bug。
_TASK_PACK_MISS_TRIGGERS: dict[str, dict[str, float]] = {}
#: **自动交接**（claim 无包 ⇒ 翻 mode + 请控制台写 rl-config + 导包）用第三本账
#: （2026-10-03，plan/auto-offline-handoff §3.1a/§3.1c）：云机的每次 claim 重试都会再问一次
#: ⇒ 用节流 + 上界给云机一个明确的 `give_up`，避免「导出失败 → 无限 15s 轮询」。
_TASK_AUTO_HANDOFF_TRIGGERS: dict[str, dict[str, float]] = {}
#: 两次自动交接触发的最小间隔（秒）。
TASK_AUTO_HANDOFF_THROTTLE_SEC = 600.0
#: 自动课 claim 遇缺包时、云机下一次重试的建议间隔（秒）——导出是分钟级，60s 量级查一次即可。
TASK_AUTO_HANDOFF_RETRY_SEC = 60.0
#: 连续触发上界：到顶仍无包 ⇒ 409 带 `give_up=true`（云机放弃该课，继续下一门）。
TASK_AUTO_HANDOFF_TRIGGER_LIMIT = 3
_TASK_PACK_LOCK = Lock()


def _hub_log(msg: str) -> None:
    """hub 侧一行日志（带时刻；与文件里其它 `print` 同形）。"""
    print(f"[{time.strftime('%H:%M:%S')}] [hub-server] {msg}", flush=True)


def _file_sha256(path: Path) -> str:
    """整个文件（可读时）的 sha256；读不到（不存在/权限）→ `""` = 不参与判定。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def task_pack_stale_reason(*, pack_init_sha: str, active_sha: str) -> str:
    """包是否过期（纯函数，§8.2 主判据）。

    判据：`sha256(tmp/<课>/weights.json)` ≠ 包内 `init_weights.json` 的 sha ⇒ 包的起点已不是
    课程当前起点。**两侧任何一侧读不到 ⇒ 返回 `""`（不判过期，照发）**：本端点首先是文件
    递送，判不了就不该把云机拦在门外（评审 F7）。
    """
    if not pack_init_sha or not active_sha:
        return ""
    if pack_init_sha == active_sha:
        return ""
    return (
        f"包起点 sha12={pack_init_sha[:12]}… ≠ 课程当前权重 sha12={active_sha[:12]}…"
    )


def decide_task_pack(
    *,
    stale: str,
    secs_since_trigger: float,
    triggers: int,
    throttle_sec: float = TASK_PACK_STALE_THROTTLE_SEC,
    limit: int = TASK_PACK_STALE_TRIGGER_LIMIT,
) -> str:
    """过期时该干什么（纯函数）：`serve` / `trigger` / `throttled` / `give_up`。"""
    if not stale:
        return "serve"
    if triggers >= limit:
        return "give_up"
    if secs_since_trigger < throttle_sec:
        return "throttled"
    return "trigger"


def pack_index_part_sha(pack_path: Path, part: str) -> str:
    """从包的 `task.json` 里取某件的 sha256（**不解压整包**；读不到 → `""`）。"""
    try:
        with zipfile.ZipFile(pack_path) as zf:
            idx = json.loads(zf.read(TASK_PACK_INDEX_NAME).decode("utf-8"))
    except Exception:
        return ""
    parts = idx.get("parts") if isinstance(idx, dict) else None
    rec = parts.get(part) if isinstance(parts, dict) else None
    return str(rec.get("sha256", "") or "") if isinstance(rec, dict) else ""


def pack_index_meta(pack_path: Path) -> dict:
    """包的段元信息：`{run_id, it, end_it, commit, created_at}`（读不到 → 各字段空值）。

    为什么从包里读而不查账本：包是**导出那一刻的只读快照**（控制台写的那个 zip），
    而清单要回答的是「我领了这份包会跑哪一段」——只有包里那几行是权威的。容错口径与
    `pack_index_part_sha` 同款：解不开 zip / 缺索引 / 缺 plan ⇒ 空值（**不报错**：
    清单是观测面，不该因为一个坏包变成 500）。
    """
    empty = {"run_id": "", "it": 0, "end_it": 0, "commit": "", "created_at": 0.0}
    try:
        with zipfile.ZipFile(pack_path) as zf:
            idx = json.loads(zf.read(TASK_PACK_INDEX_NAME).decode("utf-8"))
            try:
                plan = json.loads(zf.read(PLAN_NAME).decode("utf-8"))
            except (KeyError, ValueError):
                plan = {}
    except Exception:
        return empty
    if not isinstance(idx, dict):
        return empty
    plan = plan if isinstance(plan, dict) else {}

    def _int(v: object) -> int:
        if not isinstance(v, (int, float, str)):
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def _float(v: object) -> float:
        try:
            return float(str(v))
        except (TypeError, ValueError):
            return 0.0

    # 索引键名两套都认（★ 2026-10-05）：生产导出器（`remote/bundle.export_bundle`）写的是
    # **snake_case**（`run_id` / `created_at`），而历史测试夹具写的是 camelCase（`runId`）。
    # 只认 camelCase 会让**真包**的 `run_id` 永远是空串（清单读面/段末盖章判据都读到假缺失）。
    return {
        "run_id": str(idx.get("runId") or idx.get("run_id") or ""),
        "it": _int(idx.get("it") or plan.get("start_it")),
        "end_it": _int(plan.get("end_it")),
        "commit": str(idx.get("commit") or ""),
        "created_at": _float(idx.get("createdAt") or idx.get("created_at") or 0.0),
    }


def task_state(*, pack_exists: bool, stale: bool, held: bool) -> str:
    """一门课在清单里的状态（纯函数，总表可单测）：前者优先。

    顺序：`claimed` > `no_pack` > `stale` > `ready`。为什么「有主」压过「没包」：状态要
    回答的是「我能不能现在领」（`claimable` 单独给），而「谁在跑」比「缺东西」更应该先被看见。
    """
    if held:
        return TASK_STATE_CLAIMED
    if not pack_exists:
        return TASK_STATE_NO_PACK
    return TASK_STATE_STALE if stale else TASK_STATE_READY


def lease_verdict(now: float, rec: dict | None, worker_id: str) -> str:
    """租约判据（纯函数）：`free` / `expired` / `revoked` / `mine` / `stale` / `foreign`。

    **惰性过期**（读时判，不养清理线程）。`mine` 是特意分出来的一档：同一个 `worker_id`
    再来领（cell 中断后重跑、心跳超时后补领）应当直接续上，而不是被自己挡在门外
    （评审 G1：Kaggle 上十几分钟的会话预算，白等 900s 等于整个会话废掉）。

    顺序**必须**是 expired → revoked → mine → stale → foreign（2026-10-05，plan
    §3.3 定案，顺序理由写在这里，别对调）：

    * `revoked` 排在 `mine` **之前** ⇒ 老主心跳拿 `revoked`（而不是续上）——墓碑是
      **全局否决、不看任何身份**（`rec["revoked"]` 为真即命中，新主也照样命中）；
    * `mine` 排在 `stale` **之前** ⇒ 同一个 worker 回来续领自己（可能静默过）的租约时
      判 `mine` 续上，不被判成 stale 而被自己 409（cell 中断重跑要用）。

    `stale` = 身份不同 ∧ 连续静默超 `offline_lease_stale_sec()`；`beat_at` 缺失（旧记录）
    ⇒ 用 `at`（语义不变）。token 不在这里判——那是 claim/heartbeat 内部的分流（它们本来
    就有 token）。
    """
    if not rec:
        return "free"
    if float(rec.get("expires_at", 0.0)) <= float(now):
        return "expired"
    if rec.get("revoked") is True:
        return "revoked"
    if str(rec.get("worker_id", "")) == worker_id:
        return "mine"
    beat = rec.get("beat_at")
    try:
        beat_at = float(beat) if beat is not None else float(rec.get("at", 0.0) or 0.0)
    except (TypeError, ValueError):
        beat_at = 0.0
    if beat_at > 0 and float(now) - beat_at > offline_lease_stale_sec():
        return "stale"
    return "foreign"


def reset_task_pack_triggers(course: str = "") -> None:
    """清触发账本（判据回到"新鲜"时/测试用）：`course=""` 清全部。"""
    with _TASK_PACK_LOCK:
        if course:
            _TASK_PACK_TRIGGERS.pop(course, None)
        else:
            _TASK_PACK_TRIGGERS.clear()


def reset_task_pack_miss_triggers(course: str = "") -> None:
    """清**缺包**账本（包重新出现时调；`course=""` 清全部）。

    为什么必须在「包又在了」时清：不清就等于**一次上界用一辈子**——运维修完再删包
    （或干脆重导失败）时，hub 再也不会替云机推一次（plan §3.6-10）。
    """
    with _TASK_PACK_LOCK:
        if course:
            _TASK_PACK_MISS_TRIGGERS.pop(course, None)
        else:
            _TASK_PACK_MISS_TRIGGERS.clear()


def trigger_task_bundle_export(course: str, log=_hub_log) -> tuple[bool, str]:
    """POST 控制台 action `exportTaskBundle`（控制台是**唯一打包者**，hub 只触发）。

    返回 `(是否算触发成功, 原因)`。`busy`（上一次导出还在跑 ⇒ HTTP 409）**算触发成功**
    ——它本来就会产出新包。控制台不可达/只读门控（403）⇒ 不算，调用方降级成"请手动导出"。
    回环地址经 `common.net_http.urlopen`（用户级 HTTP_PROXY 会认不出 `127.*`，实测踩过）。
    """
    base = os.environ.get(CONSOLE_URL_ENV, "").strip() or DEFAULT_CONSOLE_URL
    body = json.dumps({"course": course}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/exportTaskBundle",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _net_urlopen(req, timeout=TASK_PACK_TRIGGER_TIMEOUT_SEC) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 409:
            log(f"task-pack {course}: 控制台说上一次导出还在跑（HTTP 409）——视为已触发")
            return True, "busy"
        if e.code in (401, 403):
            log(f"task-pack {course}: 控制台拒绝触发（HTTP {e.code}，只读门控？）——降级为手动导出")
            return False, f"http {e.code}"
        log(f"task-pack {course}: 控制台触发失败 HTTP {e.code}——降级为手动导出")
        return False, f"http {e.code}"
    except Exception as e:
        log(f"task-pack {course}: 控制台不可达（{type(e).__name__}: {e}）——降级为手动导出")
        return False, f"{type(e).__name__}"
    log(f"task-pack {course}: 已触发控制台重导（旧包已作废，窗口期本端点会 404）")
    return True, "ok"


def auto_handoff_decision(course: str, *, now: float | None = None) -> str:
    """自动交接该不该再触发控制台（**纯函数**）：`trigger` / `throttled` / `give_up`。"""
    now = time.time() if now is None else float(now)
    with _TASK_PACK_LOCK:
        st = _TASK_AUTO_HANDOFF_TRIGGERS.get(course)
        count = int(float(st.get("count", 0.0))) if st else 0
        last = float(st.get("last", 0.0)) if st else 0.0
    if count >= TASK_AUTO_HANDOFF_TRIGGER_LIMIT:
        return "give_up"
    if last and now - last < TASK_AUTO_HANDOFF_THROTTLE_SEC:
        return "throttled"
    return "trigger"


def note_auto_handoff_trigger(course: str, *, now: float | None = None) -> None:
    """记一次自动交接触发（进程内；hub 重启即清——与缺包账本同风格）。"""
    now = time.time() if now is None else float(now)
    with _TASK_PACK_LOCK:
        st = _TASK_AUTO_HANDOFF_TRIGGERS.setdefault(course, {"count": 0.0, "last": 0.0})
        st["count"] = float(st.get("count", 0.0)) + 1.0
        st["last"] = now


def reset_auto_handoff_triggers(course: str = "") -> None:
    """清自动交接账本（包出现 / 领取成功时调）。"""
    with _TASK_PACK_LOCK:
        if course:
            _TASK_AUTO_HANDOFF_TRIGGERS.pop(course, None)
        else:
            _TASK_AUTO_HANDOFF_TRIGGERS.clear()


def trigger_auto_handoff(course: str, log=_hub_log) -> tuple[bool, str]:
    """触发控制台的**自动交接**动作（写 rl-config + 导包；**不写意图、不 pin**）。

    与 `trigger_task_bundle_export` 的分工：那个是「缺包自愈」（取包路径的附带动作）；
    这个是「hub 已把课翻成 offline，请控制台把本机停采并出包」——多出来的唯一一步是
    rl-config（控制台的唯一写面，§3.4）。控制台不可达 ⇒ 降级为手动，半状态由 stalled 兜住。
    """
    base = os.environ.get(CONSOLE_URL_ENV, "").strip() or DEFAULT_CONSOLE_URL
    body = json.dumps({"course": course}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + AUTO_HANDOFF_CONSOLE_PATH,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _net_urlopen(req, timeout=TASK_PACK_TRIGGER_TIMEOUT_SEC) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 409:
            log(f"auto-handoff {course}: 控制台说上一次导出还在跑（HTTP 409）——视为已触发")
            return True, "busy"
        if e.code == 404:
            log(f"auto-handoff {course}: 控制台没有自动交接端点（旧版本）——降级为手动导出")
            return False, "http 404"
        if e.code in (401, 403):
            log(f"auto-handoff {course}: 控制台拒绝触发（HTTP {e.code}，只读门控？）——降级为手动")
            return False, f"http {e.code}"
        log(f"auto-handoff {course}: 控制台触发失败 HTTP {e.code}——降级为手动")
        return False, f"http {e.code}"
    except Exception as e:
        log(f"auto-handoff {course}: 控制台不可达（{type(e).__name__}: {e}）——降级为手动")
        return False, f"{type(e).__name__}"
    log(f"auto-handoff {course}: 已触发控制台（写 rl-config + 导包；完成前云机会等新包）")
    return True, "ok"
