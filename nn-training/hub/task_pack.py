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

`hub.task_pack → {common.protocol, common.net_http}`（严格向下；三本触发账收在
`PackTriggers` 里 —— 对象的锁罩住自己的写入口，`reset_*` 与触发路径会被并发请求打到）。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from threading import Lock

from common.hold_window import hold_progress_stale_sec
from common.logutil import tag_logger
from common.protocol import PLAN_NAME
from hub import seams

#: 「这个包读不出来」的异常集（P0-7 第十七批，2026-11-18）。
#:
#: 这三个函数（`pack_index_part_sha` / `pack_index_meta` / `pack_seg_end_kind`）是**观测面**
#: （清单 + 段末判据），坏包不许变成 500 —— 所以它们必须捕。但捕什么要写清楚：以前的
#: `except Exception` 把本模块自己的 bug（`AttributeError`：索引 JSON 从对象变成列表、
#: `TypeError`：字段改名）也读成「包不可读」，现场表现是「这个包没有计划 / 没有 sha」，
#: 而包其实是好的 —— 云机拿到了空的段末判据，回退成旧行为。
_PACK_UNREADABLE: tuple[type[BaseException], ...] = (
    OSError,  # 不在 / 没权限
    zipfile.BadZipFile,  # 截断 / 根本不是 zip
    EOFError,  # 截断的 ZIP 流（zipfile 有时抛这个而不是 BadZipFile）
    KeyError,  # 包里没有那个成员
    NotImplementedError,  # 压缩方式不认识（外部工具打的包）
    ValueError,  # 索引/计划 JSON 坏了，或非 UTF-8
)

#: 打控制台的传输失败集（与 `remote.http.NET_ERRORS` 同口径，但**不经由 remote** 取证
#: —— hub→remote 是向上 import，层账不允许）；多一个 `ValueError`：坏 URL / 坏头。
_CONSOLE_NET_ERRORS: tuple[type[BaseException], ...] = (
    OSError,  # URLError / HTTPError / 连接被拒 / 超时
    http.client.HTTPException,
    ValueError,
)

#: 行日志：格式/时间戳只走 `common/logutil`（`[HH:MM:SS] [tag] msg`）。
_log = tag_logger("hub-server", lambda: time)


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
# ── 接管（hold）的进度活性（plan/worker-type-dispatch-model §1.2/§1.5.2，2026-10-07）──
#
# 新派发模型把三件事拆正交：**谁在跑**（worker 类型）/ **归谁独占**（hold）/ **还活着吗**（进度）。
# 本块只做第三件：**活性只认进度信号**，心跳（60s）只续租约 TTL —— 2026-10-05/06 的
# 「假活」（心跳活、进度死）正是把心跳当活性算出来的（docs/nn/remote-transport.md §68）。
#
# 窗口本体与 env 旋钮 2026-11-16 下沉 `common/hold_window.py`：训练侧（`worker/loop_control`
# 读控制文件的 `held` 缓存时自判活）用的是**同一把尺子**，它原先靠 `from hub.task_pack import`
# 拿这个数 ⇒ 一个常量把纯逻辑模块钉成「碰网络的」。这里只留名字给既有调用点。
#: hub 重启给**盘上恢复的 hold** 的宽限（秒）：把 `last_progress_at`/`touch_at` 抬到 `now - 它`。
#: 为什么需要：重启时盘上的时间是旧的，而 worker 大概率还活着（它不知道自己「被重启」了）——
#: 不抬就会把活着的盘判掉线，协作派发与本机 held 会一起解开（plan §1.5.4-P2-2）。
HOLD_RESTORE_GRACE_SEC = 300.0


def hold_progress_at(hold: object) -> float:
    """hold 记录里的**进度时刻**；`-1.0` = 没有记录 / 值不合法。纯函数。

    ★M1b：哨兵从 `0.0` 改成 **`-1.0`**。为什么：`0.0` 既是「没写」又是「写了一个真的 0」
    ——生产时钟是 epoch（永远不会是 0），所以旧写法在线上不会错，而**假时钟从 0 起步的测试**
    （`now_fn=lambda: 0.0`）会把「刚 claim 的进度锚」读成「没有进度」⇒ 活跃的 hold 当场被判
    stale。真值域（≥0）与哨兵（-1）自此不重叠。
    """
    if not isinstance(hold, dict) or "last_progress_at" not in hold:
        return -1.0
    try:
        return max(0.0, float(hold["last_progress_at"]))
    except (TypeError, ValueError):
        return -1.0


def hold_touch_at(hold: object) -> float:
    """hold 记录里的**最近一次合法接触**（心跳 ∨ 进度）：`touch_at`，缺则退回 `at`（接管时刻）。

    为什么单独一个字段：TTL 余量要从**接触**起算（心跳刷它），而活性只看进度 —— 两者分开，
    「心跳活、进度死」的假活才既不掉线也不被当成新鲜（plan §1.5.4-P2-1 的四象限）。
    ★M1b：缺失哨兵同样改 **`-1.0`**（§`hold_progress_at` 的理由），且「有没有这个字段」
    按**键存不存在**判，不再按 `> 0` 判。
    """
    if not isinstance(hold, dict):
        return -1.0
    for key in ("touch_at", "at"):
        if key not in hold:
            continue
        try:
            return max(0.0, float(hold.get(key) or 0.0))
        except (TypeError, ValueError):
            continue
    return -1.0


def hold_state(now: float, hold: object) -> str:
    """hold 的活性（纯函数）：`""` = 没有 hold · `live` · `stale`。

    判据只有一条：**进度**（`last_progress_at`）。没有任何进度信号的 hold 一律 `stale` ——
    「刚接管、还没打点」的那几秒由**恢复宽限**与「claim 本身记第一个进度锚」兜住，
    而不是靠把「零进度」当活。
    """
    if not isinstance(hold, dict) or not hold:
        return ""
    p = hold_progress_at(hold)
    if p < 0.0:
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
    left_progress = (progress + float(stale_sec)) - n if progress >= 0.0 else 0.0
    left_ttl = (touch + float(ttl_sec)) - n if touch >= 0.0 else 0.0
    return max(0.0, min(left_progress, left_ttl))


#: 「离线盘在线」的窗口（秒）：与离线租约 TTL 同档 —— 取包腿的报到节奏就是这个量级。
OFFLINE_DISK_WINDOW_SEC = 900.0

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
#: 两次自动交接触发的最小间隔（秒）。
TASK_AUTO_HANDOFF_THROTTLE_SEC = 600.0
#: 自动课 claim 遇缺包时、云机下一次重试的建议间隔（秒）——导出是分钟级，60s 量级查一次即可。
TASK_AUTO_HANDOFF_RETRY_SEC = 60.0
#: 控制台「导出成败」的**只读**动作路径（hub → 控制台；2026-10-15，J3 §3.3）。
TASK_BUNDLE_STATUS_CONSOLE_PATH = "/api/taskBundleStatus"
#: 已知一次**导出失败**之后，hub 多久内不再重复触发（秒；与导包窗同量级）。
#:
#: 为什么必须挂在这条**持久**证据上（评审 P1-6）：触发账本是**进程内**记忆，而
#: `reset_auto_handoff_triggers` 在「换主 ∨ 超窗」时清零（`hub/queue_offline.py:1007`）
#: ⇒「3 次用完就 give_up」在时间轴上不成立（每 900s 新窗口又能重试一次）——现场就演成
#: 「一直在失败」（而失败确实在重复，只是没人知道）。窗口锚 = 那份状态的**写入时刻**
#: （跨 hub 重启、与账本是否清零无关）。
TASK_BUNDLE_FAILURE_WINDOW_SEC = 900.0
#: 同一个导包窗口内，向控制台问状态的**最小间隔**（秒）：云机每 ~60s 重试一次 claim，
#: 不限频就会每次去问一遍（而导出本身是分钟级的）。
TASK_BUNDLE_STATUS_POLL_SEC = 60.0
#: 「已触发过导包」但控制台确认**没有**本课导出进程时，要等多久才敢下这个结论（秒）。
#:
#: 为什么需要宽限：触发 → 起的是 detached 子进程，python 侧一进 `--export-bundle` 就写
#: `running:true`（`remote/bundle.py::export_status_guard`）——正常一两秒内就能看见。
#: 90s 是给「冷启动 + 磁盘排队」留的宽裕值，不是常规期望。
EXPORT_START_GRACE_SEC = 90.0
#: 连续触发上界：到顶仍无包 ⇒ 409 带 `give_up=true`（云机放弃该课，继续下一门）。
TASK_AUTO_HANDOFF_TRIGGER_LIMIT = 3
#: 一本触发账的形状：`课程 → {"count" / "last" / "warned" / "polled_at" / "failed_at" / …}`。
#: 值域 `float | str` 只服务自动交接腿的「已知导出失败原因」（见 `PackTriggers`）。
_Book = dict[str, dict[str, float | str]]


class PackTriggers:
    """三本触发账（过期 / 缺包 / 自动交接）—— 一把锁、同一份进程内生命周期。

    P0-5（2026-11-21）：原来是三个模块级 dict + 模块级 `_TASK_PACK_LOCK`，而**两个模块**都在写
    （本模块的判据侧与 `hub/offline.py` 的两道门），`hub/server.py` 还把 `_TASK_PACK_TRIGGERS`
    当门面再导出 ⇒ 「同一份状态散在模块作用域、谁都能写」。现在三本账各有一组写入口，锁不再是
    调用方的义务（`hub/offline.py` 也不必再 import 一把别人家的锁）。

    * **过期腿** `stale`：包起点 sha ≠ 课程当前权重时的重导节流 + 上界；
    * **缺包腿** `miss`：包根本不存在时的重导节流 + 上界。与过期腿**分开记账**是有意的：
      同一门课在同一个重导窗口里最多被推 2 次（过期 1 + 缺包 1）——「过期的包」与「没有包」
      是两种现场，合并记账会让其中一条腿在另一条腿的窗口里静默；
    * **自动交接腿** `handoff`（2026-10-03，plan/auto-offline-handoff §3.1a/§3.1c）：claim 无包
      ⇒ 翻 mode + 请控制台写 rl-config + 导包。除了节流与上界，还缓存「已知的导出失败原因」
      （★ 2026-10-15 J3：值域从纯 float 扩到 `float | str`）。它**不是**判据 —— 判据是控制台
      那份 `export-status.json` 与它的 `at`，这里只是免得每次 claim 都去问一遍。

    三本账都是**进程内记忆**（hub 重启即清）—— 与租约同风格，重启后重触发一次无害。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._stale: _Book = {}
        self._miss: _Book = {}
        self._handoff: _Book = {}

    # ---- 过期腿 ----

    def stale_gate(self, course: str, *, now: float, stale: str) -> tuple[str, bool]:
        """判一次 + 记账：返回 `(verdict, 是否该打 give_up 告警)`。

        `warned` 只翻一次面：同一窗口里的第二次 `give_up` 不重复喊。
        """
        return self._leg_gate(
            self._stale,
            course,
            now=now,
            reason=stale,
            limit=TASK_PACK_STALE_TRIGGER_LIMIT,
            warn_on_give_up=True,
        )

    def clear_stale(self, course: str = "") -> None:
        """清过期账（判据回到「新鲜」时 / 测试用）：`course=""` 清全部。"""
        self._clear(self._stale, course)

    # ---- 缺包腿 ----

    def miss_gate(self, course: str, *, now: float, reason: str) -> str:
        """判一次 + 记账（**缺包**用另一本账：与过期腿各自记账，同一窗里最多推 2 次）。"""
        verdict, _warn = self._leg_gate(
            self._miss,
            course,
            now=now,
            reason=reason,
            limit=TASK_PACK_MISS_TRIGGER_LIMIT,
        )
        return verdict

    def clear_miss(self, course: str = "") -> None:
        """清缺包账（包重新出现时调；`course=""` 清全部）。理由见 `reset_task_pack_miss_triggers`。"""
        self._clear(self._miss, course)

    # ---- 自动交接腿 ----

    def note_failure(self, course: str, reason: str, at: float) -> None:
        """记一次**已知的**导出失败（原因 + 那份状态的写入时刻）。"""
        with self._lock:
            st = self._handoff.setdefault(course, {"count": 0.0, "last": 0.0})
            st["failed_at"] = float(at)
            st["failed_reason"] = str(reason)[:400]

    def failure(self, course: str) -> tuple[str, float]:
        """读「已知的导出失败」`(原因, 写入时刻)`；没有 ⇒ `("", 0.0)`。"""
        with self._lock:
            st = self._handoff.get(course) or {}
        return str(st.get("failed_reason", "")), float(st.get("failed_at", 0.0))

    def poll_window(self, course: str, *, now: float) -> tuple[bool, float]:
        """问控制台的**限频槽**：`(是否该问, 上次触发时刻)`。

        没触发过（读接口不该凭空向外发请求）或窗内刚问过 ⇒ `(False, 0.0)`。
        """
        with self._lock:
            st = self._handoff.get(course)
            if st is None:
                return False, 0.0
            last = float(st.get("last", 0.0))
            polled = float(st.get("polled_at", 0.0))
            if polled and now - polled < TASK_BUNDLE_STATUS_POLL_SEC:
                return False, 0.0
            st["polled_at"] = now
        return True, last

    def decision(self, course: str, *, now: float) -> str:
        """自动交接该不该再触发（纯判据）：`trigger` / `throttled` / `give_up` / `failed`。"""
        with self._lock:
            st = self._handoff.get(course)
            count = int(float(st.get("count", 0.0))) if st else 0
            last = float(st.get("last", 0.0)) if st else 0.0
            failed_at = float(st.get("failed_at", 0.0)) if st else 0.0
        # ★ 2026-10-15（J3）：**已知失败**优先于计数 —— 再触发一万次也还是同一个静态拒因
        #   （iters / 上界 / 无起点权重），而每次触发都会烧一次 code-snapshot 与一次 git push。
        #   窗口锚是那份状态的写入时刻（持久），不是进程内计数（重启即清零）。
        if failed_at and now - failed_at < TASK_BUNDLE_FAILURE_WINDOW_SEC:
            return "failed"
        if count >= TASK_AUTO_HANDOFF_TRIGGER_LIMIT:
            return "give_up"
        if last and now - last < TASK_AUTO_HANDOFF_THROTTLE_SEC:
            return "throttled"
        return "trigger"

    def note_trigger(self, course: str, *, now: float) -> None:
        """记一次自动交接触发（进程内；hub 重启即清——与缺包账本同风格）。"""
        with self._lock:
            st = self._handoff.setdefault(course, {"count": 0.0, "last": 0.0})
            st["count"] = float(st.get("count", 0.0)) + 1.0
            st["last"] = now

    def clear_handoff(self, course: str = "") -> None:
        """清自动交接账（包出现 / 领取成功时调）。"""
        self._clear(self._handoff, course)

    def snapshot(self) -> dict[str, _Book]:
        """三本账的浅拷贝快照（观测 / 用例用：不改状态、不把内部字典交出去）。"""
        with self._lock:
            return {
                "stale": {k: dict(v) for k, v in self._stale.items()},
                "miss": {k: dict(v) for k, v in self._miss.items()},
                "handoff": {k: dict(v) for k, v in self._handoff.items()},
            }

    def _leg_gate(
        self,
        book: _Book,
        course: str,
        *,
        now: float,
        reason: str,
        limit: int,
        warn_on_give_up: bool = False,
    ) -> tuple[str, bool]:
        """两条重导腿的公共体：判据 + 记账 + （只在过期腿上）到顶告警。

        **先记账再让调用方去触发**：并发请求里只有第一个真去触发（窗内其余看到 `throttled`）。
        两条腿只在「用哪本账 / 上界多少 / 要不要喊 give_up」上不同 —— 判据与记账必须同源，
        否则「过期推两次、缺包推三次」这类差异会变成两份需要对齐的代码。
        """
        with self._lock:
            st = book.setdefault(course, {})
            verdict = decide_task_pack(
                stale=reason,
                secs_since_trigger=(now - float(st.get("last", 0.0))) if st.get("last") else 1e9,
                triggers=int(st.get("count", 0)),
                throttle_sec=TASK_PACK_STALE_THROTTLE_SEC,
                limit=limit,
            )
            if verdict == "trigger":
                st["last"] = now
                st["count"] = int(st.get("count", 0)) + 1
            warn = warn_on_give_up and verdict == "give_up" and not st.get("warned")
            if warn:
                st["warned"] = 1.0
        return verdict, warn

    def _clear(self, book: _Book, course: str) -> None:
        with self._lock:
            if course:
                book.pop(course, None)
            else:
                book.clear()


#: 本进程的三本触发账（过期/缺包门与自动交接腿的唯一写入口）。
_TRIGGERS = PackTriggers()


def pack_triggers() -> PackTriggers:
    """本进程的触发账；顺序 / 判据全在对象里，调用方只拿这一个人口。

    hub 重启即清 —— 与租约同风格（重启后重触发一次无害）；跨进程不共享。
    """
    return _TRIGGERS


def _hub_log(msg: str) -> None:
    """hub 侧一行日志（带时刻；格式与时间戳走 `common/logutil`，与 hub 其余入口同源）。"""
    _log(msg)


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
    except _PACK_UNREADABLE:
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
    except _PACK_UNREADABLE:
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


#: 段末三态（J2，`plan/offline-open-ended-iters-and-loud-export` §3.2.3）：与云机侧
#: `remote.plan_run.SEG_END_*` 逐字同值。hub 只消费不判决，所以这里的字面量就是契约。
SEG_END_OPEN = "open"
SEG_END_COURSE = "course"
SEG_END_SEGMENT = "segment"


def pack_seg_end_kind(pack_path: Path) -> str:
    """从包的 `plan.json` **只读**推出「这一段跑满时算哪种段末」（三态；反推兜底）。

    为什么 hub 侧还要会算（评审 P1-8）：三态是 v2 新增字段，而它在父/子进程之间走控制文件
    协议 —— **在飞的 `code.zip` 是导出那一刻的快照**，老子进程不认新键 ⇒ 云机自报里那个键
    静默缺席。那正是「同一件事两份事实源」的经典形态，而包里就有一份**只读**的权威
    （`plan.json` 的 `open_ended` / `iters_total` / `end_it`，与云机读的是同一份）——
    拿它当缺席时的兜底，比把段末当课末（或反之）便宜得多。

    判据与 `remote.plan_run._seg_end_kind` 逐字同源：`open_ended` ⇒ `open`；
    `end_it >= iters_total`（有限）⇒ `course`；其余 ⇒ `segment`。
    包不在 / 解不开 / 没有 plan ⇒ `""`（**不可知**，调用方按旧行为处理）。
    """
    try:
        with zipfile.ZipFile(pack_path) as zf:
            plan = json.loads(zf.read(PLAN_NAME).decode("utf-8"))
    except _PACK_UNREADABLE:
        return ""
    if not isinstance(plan, dict):
        return ""
    if plan.get("open_ended") is True:
        return SEG_END_OPEN
    raw_total = plan.get("iters_total")
    total = int(raw_total) if isinstance(raw_total, (int, float)) and not isinstance(raw_total, bool) else 0
    raw_end = plan.get("end_it")
    end_it = int(raw_end) if isinstance(raw_end, (int, float)) and not isinstance(raw_end, bool) else 0
    if total > 0 and end_it >= total:
        return SEG_END_COURSE
    if end_it > 0:
        return SEG_END_SEGMENT
    return ""


def course_finished(jsonl_path: Path, max_bytes: int = 256 * 1024) -> bool:
    """课账本尾部：最后一条 `run_complete` 是不是在最后一次 `run_start` **之后**？

    这是「这门课还要不要继续干」的唯一信号（`trainer/bc_loop` 与 RL 都写它）；
    重启会写新的 `run_start` ⇒ 收官记录作废（又开工了）。读不到账本 ⇒ 未收官
    （不可知不当作完成——过早停云机比多跑一轮贵）。

    ★ 2026-11-12：从 `hub/queue_scope.py` 下沉到这里——`/offline/tasks` 的 BC 课程行
    也要用同一把尺子（`queue_offline` 不能 import 兄弟混入），两处必须逐字同源。
    """
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return False
    last_start = last_done = -1
    for i, raw in enumerate(text.splitlines()):
        try:
            ev = json.loads(raw).get("event")
        except (ValueError, AttributeError):
            continue
        if ev == "run_start":
            last_start = i
        elif ev == "run_complete":
            last_done = i
    return last_done > last_start


def task_state(
    *, pack_exists: bool, stale: bool, held: bool
) -> str:
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

    ★ 2026-10-07（M1b，plan §1.6-F2）：`stale` 的判据从**心跳静默**改成**进度静默**
    （`last_progress_at`，阈值 `hold_progress_stale_sec()`）——「心跳活、进度死」的假活
    （docs/nn/remote-transport.md §68）不该被当成活（心跳只续 TTL，不作活性）。
    ★M4b：beat_at 退回腿与 180s 常量一起删——一条租约没有进度字段就**不可证明活着**，
    按 stale 处理（保守方向：允许接管；`_leases` 是进程内的，跨重启不存在这种记录）。
    token 不在这里判——那是 claim/heartbeat 内部的分流。
    """
    if not rec:
        return "free"
    if float(rec.get("expires_at", 0.0)) <= float(now):
        return "expired"
    if rec.get("revoked") is True:
        return "revoked"
    if str(rec.get("worker_id", "")) == worker_id:
        return "mine"
    progress = hold_progress_at(rec)
    if progress < 0.0:
        return "stale"
    if float(now) - progress > hold_progress_stale_sec():
        return "stale"
    return "foreign"


def reset_task_pack_triggers(course: str = "") -> None:
    """清触发账本（判据回到"新鲜"时/测试用）：`course=""` 清全部。"""
    pack_triggers().clear_stale(course)


def reset_task_pack_miss_triggers(course: str = "") -> None:
    """清**缺包**账本（包重新出现时调；`course=""` 清全部）。

    为什么必须在「包又在了」时清：不清就等于**一次上界用一辈子**——运维修完再删包
    （或干脆重导失败）时，hub 再也不会替云机推一次（plan §3.6-10）。
    """
    pack_triggers().clear_miss(course)


def trigger_task_bundle_export(
    course: str, log=_hub_log, *, net: seams.NetPort | None = None
) -> tuple[bool, str]:
    """POST 控制台 action `exportTaskBundle`（控制台是**唯一打包者**，hub 只触发）。

    返回 `(是否算触发成功, 原因)`。`busy`（上一次导出还在跑 ⇒ HTTP 409）**算触发成功**
    ——它本来就会产出新包。控制台不可达/只读门控（403）⇒ 不算，调用方降级成"请手动导出"。
    回环地址经 `hub.seams.NET`（用户级 HTTP_PROXY 会认不出 `127.*`，实测踩过）。
    """
    net = net or seams.NET
    base = os.environ.get(CONSOLE_URL_ENV, "").strip() or DEFAULT_CONSOLE_URL
    body = json.dumps({"course": course}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/exportTaskBundle",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with net.open(req, timeout=TASK_PACK_TRIGGER_TIMEOUT_SEC) as resp:
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
    except _CONSOLE_NET_ERRORS as e:
        log(f"task-pack {course}: 控制台不可达（{type(e).__name__}: {e}）——降级为手动导出")
        return False, f"{type(e).__name__}"
    log(f"task-pack {course}: 已触发控制台重导（旧包已作废，窗口期本端点会 404）")
    return True, "ok"


def auto_handoff_failure(course: str) -> tuple[str, float]:
    """读「已知的导出失败」`(原因, 写入时刻)`；没有 ⇒ `("", 0.0)`。"""
    return pack_triggers().failure(course)


def console_export_status(
    course: str,
    *,
    log=_hub_log,
    timeout: float = TASK_PACK_TRIGGER_TIMEOUT_SEC,
    net: seams.NetPort | None = None,
) -> dict:
    """问控制台「上次导出成没成」+「此刻有没有本课的导出进程」（**只读**动作，零副作用）。

    返回三种形状：
      · `{"unknown": True, "present": bool, "exporting": bool | None}`
      · `{"ok": bool, "reason": str, "at": float, "present": True, "exporting": bool | None}`
      · `{"unknown": True}`（控制台不可达 —— 此时 `present`/`exporting` 都**没有意义**）

    `present` = 盘上有没有这份课的状态文件（曾经跑过导出）；`exporting` = **控制台自己**的
    「本课导出进程在跑吗」判据（进程内 busy 键 + 持久 `running`，与「点导出」同源）。
    ★ 三态：`True` 在跑 / `False` 确认没有 / **`None` 控制台没给（旧版本）**。
    `None` 绝不能当 `False` —— 那会把「旧控制台」判成「没起」。

    **不可知不是失败**：没写过状态（正在跑 / 被 kill / 旧版本）/ 控制台不可达 ⇒ 只回 unknown；
    这条腿只负责「**知道**失败时少烧资源」，不负责把未知当失败。
    """
    net = net or seams.NET
    base = os.environ.get(CONSOLE_URL_ENV, "").strip() or DEFAULT_CONSOLE_URL
    body = json.dumps({"course": course}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + TASK_BUNDLE_STATUS_CONSOLE_PATH,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with net.open(req, timeout=timeout) as resp:
            parsed = json.loads(resp.read().decode("utf-8") or "{}")
    except _CONSOLE_NET_ERRORS as e:
        log(f"export-status {course}: 控制台不可达/不接受（{type(e).__name__}）——视为不可知")
        return {"unknown": True}
    exporting: bool | None = None
    if isinstance(parsed, dict) and isinstance(parsed.get("exporting"), bool):
        exporting = bool(parsed["exporting"])
    st = parsed.get("status") if isinstance(parsed, dict) else None
    if not isinstance(st, dict):
        # `status: null` = 还没写过状态文件（正在跑 / 被 kill / 旧版本）——不可知，**不是成功**。
        # 但「没写文件」与「没起进程」是两件事：后者由 `exporting` 回答，见 `poll_console_export_status`。
        return {"unknown": True, "present": False, "exporting": exporting}
    # ★ 2026-10-21（plan/export-bundle-stall §3.3）：`running`/`stale`/`expired` 都是**不可知**。
    #   把 `running` 当 `ok:false` 会让 hub 在导出进行中就记一笔失败、
    #   停试 900s（v1 的新 bug）；把 stale 当 ok:true 则会演一个陈旧的成功。
    if st.get("running") or st.get("stale") or st.get("expired"):
        return {"unknown": True, "present": True, "exporting": exporting}
    return {
        "ok": bool(st.get("ok")),
        "reason": str(st.get("reason") or ""),
        "at": float(st.get("at") or 0.0),
        "present": True,
        "exporting": exporting,
    }


def poll_console_export_status(
    course: str,
    *,
    now: float | None = None,
    log=_hub_log,
    net: seams.NetPort | None = None,
) -> dict:
    """在「谁在导包」的窗口里问一次控制台：**限频** + **只认新事实**。

    返回（全部可叠加判断，空 dict = 没新事实 / 不可知 / 被限频）：
      · `{"failure": <原因>}` —— 有**这一轮**的失败（`at` 不早于上次触发，否则是上一轮的，不归罪）
      · `{"ok": True}`       —— 这一轮已经导出成功（包在盘上，云机下一拍即可取）
      · `{"live": True}`     —— 控制台确认本课导出进程在跑
      · `{"live": False, "present": bool}` —— 控制台确认**没有**导出进程（已过启动宽限）

    ★ 2026-10-22：后两种是**新事实**（现场：四门课全说「已经在导包了」，而三门课一个导出进程
    都没起）。此前这条腿只认 `ok:false`，`status:null` 与 `running` 一律压成 `unknown`
    ⇒ 「本窗口触发过」被当成了「正在导包」的同义词。
    """
    now = time.time() if now is None else float(now)
    # 没触发过就没这条腿（读接口不该凭空向外发请求）；窗内已问过也跳过。
    allow, last = pack_triggers().poll_window(course, now=now)
    if not allow:
        return {}
    got = console_export_status(course, log=log, net=net)
    exporting = got.get("exporting")
    if got.get("unknown"):
        if exporting is True:
            return {"live": True}
        # 「确认没有进程」需要两个条件同时成立：控制台**明说** False（None = 旧版本，不算），
        # 且触发已经过了启动宽限（刚触发的几秒里状态文件本来就还没落）。
        if exporting is False and last and now - last >= EXPORT_START_GRACE_SEC:
            present = bool(got.get("present"))
            log(
                f"export-status {course}: 控制台确认**没有**本课导出进程"
                f"（触发后 {now - last:.0f}s；状态文件{'在' if present else '不存在'}）"
            )
            return {"live": False, "present": present}
        return {}
    if exporting is True:
        return {"live": True}
    at = float(got.get("at") or 0.0)
    if at < last:
        return {}  # 上一轮的失败/成功：不归罪也不报喜
    if got.get("ok"):
        return {"ok": True}
    reason = str(got.get("reason") or "导出进程失败（控制台日志末行有原因）")
    # 进程内只是缓存（判据的权威在控制台那份 `export-status.json`，它跨 hub 重启）；
    # 存下来只是为了不必每次 claim 都去问一遍。
    pack_triggers().note_failure(course, reason, at)
    log(f"export-status {course}: 控制台报**导出失败**：{reason}")
    return {"failure": reason}


def auto_handoff_decision(course: str, *, now: float | None = None) -> str:
    """自动交接该不该再触发控制台（**纯函数**）：
    `trigger` / `throttled` / `give_up` / `failed`（已知导出失败，窗口内不再重试）。"""
    now = time.time() if now is None else float(now)
    return pack_triggers().decision(course, now=now)


def note_auto_handoff_trigger(course: str, *, now: float | None = None) -> None:
    """记一次自动交接触发（进程内；hub 重启即清——与缺包账本同风格）。"""
    now = time.time() if now is None else float(now)
    pack_triggers().note_trigger(course, now=now)


def reset_auto_handoff_triggers(course: str = "") -> None:
    """清自动交接账本（包出现 / 领取成功时调）。"""
    pack_triggers().clear_handoff(course)


def trigger_auto_handoff(
    course: str, log=_hub_log, *, net: seams.NetPort | None = None
) -> tuple[bool, str]:
    """触发控制台的**自动交接**动作（写 rl-config + 导包；**不写意图、不 pin**）。

    与 `trigger_task_bundle_export` 的分工：那个是「缺包自愈」（取包路径的附带动作）；
    这个是「hub 已把课翻成 offline，请控制台把本机停采并出包」——多出来的唯一一步是
    rl-config（**课程/训练态一侧**仍只由控制台写：§3.4。2026-10-10 起 rl-config 多了第二个写者——
    本进程的 `/admin/nodes/register` 只 upsert `nodes[]` 的 `managed` 条目，
    §2026-10-10-goalnn-rollout-node-auto-register）。控制台不可达 ⇒ 降级为手动，半状态由 stalled 兜住。
    """
    net = net or seams.NET
    base = os.environ.get(CONSOLE_URL_ENV, "").strip() or DEFAULT_CONSOLE_URL
    body = json.dumps({"course": course}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + AUTO_HANDOFF_CONSOLE_PATH,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with net.open(req, timeout=TASK_PACK_TRIGGER_TIMEOUT_SEC) as resp:
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
    except _CONSOLE_NET_ERRORS as e:
        log(f"auto-handoff {course}: 控制台不可达（{type(e).__name__}: {e}）——降级为手动")
        return False, f"{type(e).__name__}"
    log(f"auto-handoff {course}: 已触发控制台（写 rl-config + 导包；完成前云机会等新包）")
    return True, "ok"
