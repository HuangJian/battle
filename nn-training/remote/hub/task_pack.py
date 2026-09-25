"""remote/hub/task_pack.py — 任务包新鲜度门 + 离线任务读数的**纯判据**（叶子模块）。

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

`hub.task_pack → {common.protocol, remote.net_http}`（严格向下；记账字典住本模块，
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

from common.protocol import PLAN_NAME
from remote.net_http import urlopen as _net_urlopen

# ── 任务包新鲜度门（plan/offline-rerun-local-first §8，2026-09-24）────────────────
#
# 为什么 hub 要管这个：`GET /offline/task-pack` 原来只递文件，而**课程状态会前进、包不会**
# （回传轮把 `tmp/<课>/weights.json` 推着走，`_land_offline_round_extras`）。云机整机重启、
# 产物全丢时，只要 resume 锚点那条腿断了，兜底起点就是**开课时那份旧包** —— 从旧起点重跑
# 几十上百轮，白烧算力。过期的包比没有包更危险（404 只让云机多等一拍），所以宁可偏严。

#: 包索引名（与 `remote/bundle.py::BUNDLE_INDEX` 逐字相同；本模块不 import bundle 以免边，测试守）。
TASK_PACK_INDEX_NAME = "task.json"
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
#: 离线任务的四种状态（清单面，`plan/offline-task-discovery.plan.md` §3.1）：
#: `ready`（有新鲜包可领）/ `stale`（包在但起点已旧，领了要从旧起点跑）/ `no_pack`（还没导）/ `claimed`（有人持租）。
#: 第五种 `not_offline` 只在 `?include=all`（控制台排障）时出现——云机永远用默认清单。
TASK_STATE_READY = "ready"
TASK_STATE_STALE = "stale"
TASK_STATE_NO_PACK = "no_pack"
TASK_STATE_CLAIMED = "claimed"
TASK_STATE_NOT_OFFLINE = "not_offline"
#: 「离线盘在线」的窗口（秒）：与离线租约 TTL 同档 —— 取包腿的报到节奏就是这个量级。
OFFLINE_DISK_WINDOW_SEC = 900.0
#: 离线腿的指路（2026-09-25 退役「发一份 kind=run 队列项」之后，离线课的唯一载体是任务包）。
OFFLINE_LEG_HINT = (
    "离线课不再经 hub 队列执行：云机用 battle.offline.ipynb 取任务包接手"
    "（/offline/tasks → /offline/task-pack → 跑完回传）"
)

#: 清单排序名次：`ready` 最前、`not_offline` 最后；同级按包的 mtime **升序**（最老的先跑）。
#: 为什么 `claimed` 排在 `no_pack` 之前：前者是「有人在跑」、后者是「没人能跑」——
#: 一眼看出「活儿在动」比看出「缺东西」更接近清单的用途（下一批还有人问）。
TASK_STATE_RANK = {
    TASK_STATE_READY: 0,
    TASK_STATE_STALE: 1,
    TASK_STATE_CLAIMED: 2,
    TASK_STATE_NO_PACK: 3,
    TASK_STATE_NOT_OFFLINE: 4,
}
#: 同课程的触发账本（进程内；hub 重启即清——与租约同风格，重启后重触发一次无害）。
_TASK_PACK_TRIGGERS: dict[str, dict[str, float]] = {}
#: **缺包**用另一本账（与过期那条腿分开）：两本账在同一个重导窗口里各自记账，
#: 所以同一门课在窗口内最多被推 2 次（过期 1 + 缺包 1）——这是刻意的，不是 bug。
_TASK_PACK_MISS_TRIGGERS: dict[str, dict[str, float]] = {}
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

    return {
        "run_id": str(idx.get("runId") or ""),
        "it": _int(idx.get("it") or plan.get("start_it")),
        "end_it": _int(plan.get("end_it")),
        "commit": str(idx.get("commit") or ""),
        "created_at": float(idx.get("createdAt") or 0.0),
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
    """租约判据（纯函数）：`free` / `mine` / `foreign` / `expired`。

    **惰性过期**（读时判，不养清理线程）。`mine` 是特意分出来的一档：同一个 `worker_id`
    再来领（cell 中断后重跑、心跳超时后补领）应当直接续上，而不是被自己挡在门外
    （评审 G1：Kaggle 上十几分钟的会话预算，白等 900s 等于整个会话废掉）。
    """
    if not rec:
        return "free"
    if float(rec.get("expires_at", 0.0)) <= float(now):
        return "expired"
    return "mine" if str(rec.get("worker_id", "")) == worker_id else "foreign"


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
    回环地址经 `remote.net_http.urlopen`（用户级 HTTP_PROXY 会认不出 `127.*`，实测踩过）。
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
