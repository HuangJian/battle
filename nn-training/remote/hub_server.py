"""remote/hub_server.py — 旁路 hub-server 进程（stdlib http.server，零新依赖）。

进程拓扑（D11/§3.1，plan/remote-ppo-architecture.md）：训练主循环只负责
「打包 → 发布 → 轮询/等待 → 校验落位」；job 队列/租约/鉴权/jsonl 账单全部
归本进程。两端通过**磁盘 IPC** 解耦：
  * job 目录（`<job_root>/<job_id>/`）：`payload.zip`（hub 发布时写入）、
    `manifest.json`（同一份）、`result/`（worker POST 结果落盘）；
  * jsonl 账本（`training_log.jsonl`）：`job_pending`（发布时写）→
    `job_completed`（验收落位后写）双态事件——重启后可领取池由 jsonl 纯重读
    重建（D8），不依赖进程内状态。

端点（附录 C）：
  GET  /jobs/peek               云 worker 轮询候选（**不认领**：无租约/无副作用/不动
                                游标；2026-09-22 取代旧的轮询面），兼作 halt 达令
  POST /jobs/priority           job 边界优先级问询（软持有 / 掉队救援的判据）
  POST /jobs/{id}/claim         认领（mode=exclusive 设租约 / mode=backup 显式备份副本）
  POST /jobs/{id}/start|ready|abandon   开算打点（computing_at）/ 算完待传 / 合法放弃
  GET  /jobs/{id}/payload       下载 payload zip
  POST /jobs/{id}/heartbeat     心跳续租（60s）
  POST /jobs/{id}/result        worker 回传结果（weights_json + opt_tar + agg）
  GET  /jobs/{id}/status        训练主循环轮询 job 状态（pending/leased/done/failed/frozen）
                                frozen = 毒包熔断（§4.1，认领后零回传达 3 次）；`/result`
                                一并回 410 + 原因，训练侧不停在 25min 超时上
  POST /admin/unfreeze?job_id=  人工解冻熔断的 job（回池可重领；重发**不**解冻）
  POST /jobs/{id}/release       worker 瞬时失败主动还租约（job 立即回池，2026-09-05）
  POST /jobs/{id}/fail          节点**确定性**失败回报原因（bun 装不上 / TS 运行时取不到
                                / argv 非法）——落 `fail.json` 为终局，训练侧从
                                `/result` 拿 410 + 原因**立即**停腿，不再等 25min 超时
                                （2026-09-17）
  GET  /jobs/{id}/result        训练主循环取回已落盘结果做三重校验（已失败 → 410 + 原因）
  POST /offline/artifact        **产物补传**：节点把全离线/半离线段已落盘的一轮产物
                                （权重 + opt + 账本行）best-effort 推上来（按 (run_id, it)
                                幂等、首写锁定）；落在 `<job_root>/offline/<run_id>/`
                                （2026-09-17）
  POST /offline/result          同上，段末摘要（跑到哪 / 什么状态）——会覆盖写（最新一份）

鉴权（D9）：`Authorization: Bearer <token>`。**先验 token，封禁只拒无效鉴权尝试**
（2026-09-17 改序）：同一来源 IP 连续 5 次**无效**鉴权 → 该 IP 的无效尝试 1 小时内
一律 403，但**合法 token 永远放行**（封禁不连坐）。旧序先查封禁 ⇒ 一次误封（本机组件
用陈旧 token 连打 5 次）会把该来源 IP 的**全部**流量（含 console 健康检查、训练循环、
worker 拉活）拒之门外一小时，而封禁只住进程内存、只能靠重启清除——重启又正好被
自己占着的端口挡住（2026-09-17 hub-server 重启死锁事故）。
另：**回环来源（`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1`）永不计数、永不封禁**
（用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）——回环就是本机自己的组件，而
cloudflared 回源会把隧道流量也全归成 127.0.0.1，对它封禁 = 把本机服务面整体连坐。

**隧道来源还原（B，2026-09-17）**：回环对端 + `CF-Connecting-IP`（合法 IP 字面量、且非回环值）
⇒ 按**归因 IP** 计数/封禁（`attributed_source`），把「隧道入口无封禁」这个改序代价补回来；
无头 / 头不合法 ⇒ 仍按回环豁免（本机组件不受影响）。直连（tailnet）对端一律**只认 TCP 对端
IP**——那台机器能自己写任何头。假设与失效代价（头若可伪造）见 `attributed_source` docstring。
token 永不落日志。

启动：
  python -m remote.hub_server --port 8787 --token <token> \
      --job-root <traj_root>/remote-jobs --jsonl <traj_root>/training_log.jsonl

监听地址（2026-09-16）：默认 `0.0.0.0`。原因是 Tailscale 内网直连场景下，云 worker
是从 tailnet 侧**入站**访问本 hub（以前 cloudflared 是本机主动外连，绑 127.0.0.1 就够）；
绑回环时 tailnet 根本连不上，表现为对端一直超时。只想听 tailnet 就显式
`--host <本机 Tailscale IP>`；单测/冒烟仍然各自显式传 `host="127.0.0.1"`。
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import ipaddress
import json
import sys
import tempfile
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread

from common.protocol import (
    AUTH_HEADER,
    CLAIM_MODE_EXCLUSIVE,
    CLAIM_MODES,
    CLAIM_TTL_SEC,
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    COURSE_MODE_ONLINE,
    COURSE_MODES,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_RESULT_PATH,
    OFFLINE_RESUME_BLOB_PATH,
    OFFLINE_RESUME_PATH,
    OFFLINE_TASK_PACK_PATH,
    PEEK_MAX,
    PRIORITY_NONE,
    PUSH_POLL_SEC,
    PUSH_TIMEOUT_SEC,
    WORKER_ID_HEADER,
    WORKER_SEEN_WINDOW_SEC,
    ProtocolError,
    find_payload,
    may_avoid_stale_holder,
    parse_course_arg,
    rotation_order,
    sanitize_run_id,
)
from remote._instance_lock import (
    acquire_instance_lock,
    default_instance_lock_path,
    release_instance_lock,
)
from remote._port_guard import ensure_port_free

# 状态类（S4 第十四刀）：`_JobStore` 按域拆成六个混入 —— **同一把 `_lock`、同一个 `self`**，
# 所以 `store._leases[...]` 这类直读、`patch remote.hub_server._JobStore`、`_JobStore.X` 常量
# （`OFFLINE_DIR` / `RESUME_PARTS` / `BC_EPOCH_BODY_MAX`）全部照旧走 MRO 解析。
# `remote/hub/*` **不得** import 本模块（否则与「本模块 import 混入」成环）。
from remote.hub.admin import AdminRoutes
from remote.hub.blob import BlobRoutes
from remote.hub.offline import OfflineRoutes
from remote.hub.result import ResultRoutes
from remote.hub.schedule import ScheduleRoutes
from remote.hub.store_leases import (
    FREEZE_AFTER_RECLAIMS as FREEZE_AFTER_RECLAIMS,  # 测试从这里取（名字是契约，位置不是）
)
from remote.hub.store_leases import (
    ClaimOutcome,
    LeaseMixin,
)

# 产物账本行 → 课程账本行的搬运**只在 remote.artifacts 实现一份**（人工导入与实时补传共用）：
# 两份翻译必然漂开，而「两腿同字段」正是控制台那张表存在的意义。（第十四刀后本模块不再直
# 接用它——离线段带走了唯一的调用点，`store_offline.py` 自己 import。）
# HTTP 路由组（S4 第十一刀）：四组域混入，派发表（do_GET / do_POST，仍在 HubHandler）调它们。
# 它们只向外调通用助手（`_auth_ok` / `_json` / `_bytes` / `_job_id` / `_read_json_body` …），
# 那些助手仍住本模块 —— 混入把它们声明成 `Any`（同 `hub.admin` 的先例）。
from remote.hub.store_ledger import LedgerMixin
from remote.hub.store_offline import OfflineRoundsMixin
from remote.hub.store_results import ResultsMixin
from remote.hub.store_scheduling import SchedulingMixin
from remote.hub.store_wire import WireMeterMixin
from remote.push_dispatch import (
    DEFAULT_PUSH_CONFIG,
    PushDispatcher,
    PushWorkers,
)

# ------------------------------------------------------------------ 来源判定

#: Cloudflare 边缘注入的「真实客户端 IP」头（客户端自带的值由边缘覆写——本方案的**未实测假设**，
#: 实测方法：向隧道发带伪造值的无效鉴权，看本文件的 AUTH FAIL 审计行 src= 显示哪个）。
#: 只认它，不认 `X-Forwarded-For`：后者是**可追加的逗号列表**，取哪一段都是语义游戏。
CF_SOURCE_HEADER = "CF-Connecting-IP"

#: 单次**响应发送**的超时（秒）：对端半开（隧道/代理侧掉了，本机 TCP 还挂着）时
#: `wfile.write()` 会**永久**阻塞，那个 handler 线程就永久卡在写里。
#:
#: 2026-09-20 事故：4.8MB payload 卡在对端 ⇒ 云机侧「claim 后几分钟零日志」，而 hub 侧
#: 日志**一个字都没有**（`/payload` 访问行属于高频静默规则）。有界即响亮：超时后打印
#: 已发字节数并断开连接（HTTP/1.0 ⇒ 连接随即关闭，线程回归）。
SEND_TIMEOUT_SEC = 60.0
#: 发送切片（字节）：分片写让上面的超时**每片**都生效（一次大 write 只有整体超时）。
SEND_CHUNK = 256 * 1024
#: 打「发送完成」日志的最小 body（字节）：小 JSON 不打（高频），payload/code 这类必打。
SEND_LOG_MIN_BYTES = 256 * 1024


def _is_ip_literal(s: str) -> bool:
    """是否是合法的 IP 字面量（`ipaddress` 严格解析；带端口的 `1.2.3.4:56` 不算）。"""
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def attributed_source(peer: str, cf_header: str) -> tuple[str, str]:
    """归因来源 = 「这次鉴权失败算在谁头上」⇒ `(ip, 依据)`，依据 ∈ {`"cf"`, `"peer"`}。

    规则（**只在「对端是回环」时采信边缘头**）：
      * 回环对端（cloudflared 回源 / 本机组件）+ `CF-Connecting-IP` 是合法 IP 字面量、且不是
        回环值 ⇒ 归因给该 IP（依据 `"cf"`）；
      * 其余的（头缺失 / 头不是合法 IP / 头写的还是回环值 / 对端不是回环）⇒ 归因给 TCP 对端
        （依据 `"peer"`）。

    **为什么只在回环时采信**：tailnet 直连的对端**能自己写任何头**（那台机器就是攻击者时，头就是
    它自己编的），故直连路径只认 TCP 对端 IP；而回环对端意味着「由本机上的中继（cloudflared）
    转发进来」，此时头由 Cloudflare 边缘注入。

    ⚠️ **假设与失效代价**（待实测，见 `docs/nn/remote-transport.md` §6 的归因来源条目）：本规则成立的前提是
    边缘**会覆写** `CF-Connecting-IP`。即使假设不成立（客户端能自带该头），最坏后果**两条都良性**：
      ① 轮换头值 ⇒ 攻击者拿不到封禁，效果退化为「回环豁免」（= 本方案之前的状态，不会更差）；
      ② 伪造别人（如某个 tailnet worker）的 IP ⇒ 那个 IP **只**会被拒「无效鉴权尝试」，带
         正确 token 的请求照常放行（先验 token 的改序使然）⇒ 不构成对合法对端的 DoS。
    这正是本方案**不需要**额外加「全局退避闸」的理由（失效时它只会把速率压下去，不增加安全）。
    """
    p = (peer or "").strip()
    if not _is_loopback(p):
        return p, "peer"
    cf = (cf_header or "").strip()
    if cf and _is_ip_literal(cf) and not _is_loopback(cf):
        return cf, "cf"
    return p, "peer"


def _is_loopback(ip: str) -> bool:
    """回环来源（`127.0.0.0/8` / `::1` / IPv4-mapped `::ffff:127.0.0.1`）。

    **闭锁永不作用于回环**（2026-09-17 用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）：
    回环来源就是本机自己的组件（console 健康检查、训练循环、worker 拉活），而 cloudflared
    回源还会把**隧道流量一并归成 127.0.0.1** —— 对它封禁等于把整台机器的服务面连坐，且
    封禁只住进程内存、只能靠重启清除（2026-09-17 hub-server 重启死锁事故的根因）。
    回环上的**无效**鉴权照常 401（鉴权边界与审计行不变），只是**不计数、不封禁**。
    """
    s = (ip or "").strip().lower()
    if s.startswith("::ffff:"):  # IPv4-mapped IPv6
        s = s[len("::ffff:") :]
    return s == "::1" or s == "localhost" or s.startswith("127.")


# ------------------------------------------------------------------ state


class _AuthGuard:
    """D9 鉴权闭锁（提取自 `_JobStore`，2026-09-18）。

    为什么要独立成类：多课程单 hub 之后**鉴权面是进程级的一份**（一个 IP 的失败计数
    不该按课程各算一套，否则“同一来源 5 次无效鉴权”会把封禁阈值变成 5×N）。`_JobStore`
    仍继承它（旧调用/旧测试的 `store.is_blocked(...)` 逐字不变）。"""

    def __init__(self, now_fn=None) -> None:
        self._lock = Lock()
        self._now = now_fn or time.time
        #: 来源 IP -> 无效鉴权计数（满 5 封禁，D9）
        self._auth_fail: dict[str, int] = {}
        self._auth_blocked_until: dict[str, float] = {}

    # ---- 闭锁（D9） ----
    def auth_failure(self, ip: str) -> int:
        """记一次鉴权失败，返回**累计次数**（含本次）；满 5 次封禁 3600s。

        返回值供 handler 打印审计行——2026-09-16 x3-step 事故：401 落在
        `/ping`・`/jobs/peek` 等静默路径上，五次失败把 127.0.0.1 封掉后
        **hub 日志一行痕迹都没有**，训练循环被 cloudflared 回源 IP 连坐后
        连续 403 自杀退出，只能靠猜。故次数必须上浮到调用方记录。

        **回环来源永不计数、永不封禁**（返回 0）——口径见 `_is_loopback`。
        """
        if _is_loopback(ip):
            return 0
        with self._lock:
            n = self._auth_fail.get(ip, 0) + 1
            self._auth_fail[ip] = n
            if n >= 5:
                self._auth_blocked_until[ip] = self._now() + 3600
                self._auth_fail.pop(ip, None)
            return n

    def auth_success(self, ip: str) -> None:
        """一次合法鉴权：清零该 ip 的失败计数（**不改封禁状态**）。

        2026-09-17 改序配套：封禁只拒无效尝试后，合法流量必须能把计数打回零——否则
        与合法组件共用同一个来源 IP 的坏客户端（典型：cloudflared 回源把隧道流量与
        所有本机组件都归成 127.0.0.1）仍会**慢性累积**到 5 次，把整个 IP 拖进封禁。
        封禁本身不在此解除：它已只影响无效尝试，到点自愈，无需合法流量代劳。
        """
        with self._lock:
            self._auth_fail.pop(ip, None)

    def is_blocked(self, ip: str) -> bool:
        if _is_loopback(ip):  # 回环永不被封（即便旧内存态里混进过记录）
            return False
        with self._lock:
            until = self._auth_blocked_until.get(ip, 0.0)
            return until > self._now()

    def blocked_remaining(self, ip: str) -> float:
        """该 ip 剩余封禁秒数（未封禁 = 0）——供审计行提示「还要封多久」。"""
        with self._lock:
            return max(0.0, self._auth_blocked_until.get(ip, 0.0) - self._now())



# ───────────────── 状态类：六个域混入 + 组合（S4 第十四刀）─────────────────
#
# `_JobStore` **按域拆成六个混入**，而**不是**拆成各自持锁的协作对象——三条实测判据：
#
# 1. **一把锁是类的不变式**：30/49 个方法在同一把 `_lock` 下（`_*_locked` 后缀标的就是
#    临界区内的那半）。协作对象各持一把锁 = **换语义**（并发行为不同），不满足「零行为
#    变化」；
# 2. **跨域互调是常态**（37/49）：`_claim_locked` → `_job_priority_locked` /
#    `_collect_expired_locked` → `_drop_commitment_locked` → `_bump_epoch_locked` …
#    混入把它们留在 `self.X` 上 ⇒ **零 seam**（既不用迁移也不用注入）；
# 3. **tests 直接读私有属性**（`store._leases` / `._lease_owners` / `._stale_holders` /
#    `._claimed` / `._backup_authorized` / `._last_heartbeat` / `halt_workers`，20+ 处
#    断言）——协作对象会让这些**全部改路**；混入是同一个对象 ⇒ 一行测试都不用改。
#
# 代价（明确记在案）：**状态声明分散到六个 `_init_*` 里**。所以组合类的 `__init__` 把六次
# 调用**逐个显式写出来**，而不是走 `super().__init__()` 链：谁初始化了什么、什么顺序，
# 只有读这一个地方才对得齐；MRO 链会让顺序隐式化（本仓先例：`_AuthGuard.__init__` 也是
# 被显式调用的）。
#
# 混入顺序只影响同名的解析，而六个混入**零重名**（守卫
# `tests/test_hub_job_store_split.py` 钉住）；这里按「越底层越靠后」排在便于读。


class _JobStore(
    LedgerMixin,
    WireMeterMixin,
    SchedulingMixin,
    LeaseMixin,
    ResultsMixin,
    OfflineRoundsMixin,
    _AuthGuard,
):
    """磁盘 job 存储 + 内存租约状态。

    事实来源 = 磁盘（jsonl 账本 + job 目录）；内存只存租约（重启即丢，符合
    D8「重启后 job_pending 未完成的重发、job_completed 跳过」）。

    H2（review-hy）：领取时下发 `lease_token`（随机串），心跳/结果回传必须携带——
    hub 校验后才续租/收结果，杜绝「任何持 token 者都能续租/抢租约」的多 worker 竞态。
    H6（review-hy）：jsonl 增量读——记住上次文件 size，只解析新增行（长跑轮询
    不随账本线性变慢）。"""

    def __init__(self, job_root: str | Path, jsonl_path: str | Path, now_fn=None) -> None:
        self.job_root = Path(job_root)
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = Path(jsonl_path)
        # 域私有状态：**随所属域进了各自混入**（第十四刀）——四个有状态的域各有一个钩子，
        # 逐个显式调用，理由见本类上方那段「为什么不用 super() 链」。
        # （`store_results` / `store_offline` **没有钩子**：它们真的没有常驻状态——
        # 守卫对它们改成正面断言「方法体里零 `self.X = …` 赋值」，不造 `pass` 空钩。）
        LedgerMixin._init_ledger(self)
        WireMeterMixin._init_wire(self)
        SchedulingMixin._init_scheduling(self)
        LeaseMixin._init_leases(self)
        # 鉴权面（`_AuthGuard`）：进程级一份——多课程单 hub 下不按课程各算一套计数。
        # 它同时建了**本 store 唯一的那把 `_lock`** ⇒ 组合类不再自建一把（旧的
        # `self._lock = Lock()` 本来就会被这一行覆盖掉：一个被丢弃的锁对象是下次读的人
        # 的陷阱，第十四刀顺手删了）。
        _AuthGuard.__init__(self, now_fn)
        # ---- 进程级状态（多课程单 hub 下 `_HubQueue` **借**的就是这一份）----
        # 它们不属于任何域：job 账本每课程一份，而「停机达令 / worker 登记」是**进程**概念
        # （借用关系写在 `_HubQueue` docstring ③）。
        #: 云端停机标志（§386：停机命令随任务同发；云机先试停机、停不掉照常干活）。
        #: 置位后 /jobs/peek 响应带 halt:true；由 console 经 /admin/workers/{halt,resume}
        #: 控制；hub 重启即复位（volatile）。停机**不拦任务分发**。
        self.halt_workers = False
        #: worker 登记表：worker_id -> last_seen（秒）。2026-09-22 P3 竞速退役后，本表
        #: 的**唯一**生产消费者是避让链 `active_worker_count()`（`may_avoid_stale_holder`）。
        #: 隧道回源把全流量归成 127.0.0.1 ⇒ 源 IP 分不出 worker，必须由 worker 自报身份。
        self._workers: dict[str, float] = {}

    def note_worker(self, worker_id: str) -> None:
        """登记一次 worker 轮询（peek / priority 入口）。空 id 不记（无身份无法去重计数）。"""
        wid = (worker_id or "").strip()
        if not wid:
            return
        with self._lock:
            self._workers[wid] = self._now()


# ------------------------------------------------------------------ 多课程调度面

#: 未知 job_id 的哨兵根：归属解析不到时 `_job_dir` 返回它下面的路径。
#: 选 tempdir 而不是仓库内目录：任何漏网的 mkdir 都落在系统临时目录（不污染真 store），
#: 而 handler 侧的 `(... / "manifest.json").exists()` 仍是 False ⇒ 行为与「没这个 job」同。
_MISSING_ROOT = Path(tempfile.gettempdir()) / "hub-queue-missing"

#: 自动发现（`--discover`）的扫描节拍（秒）。派发热路径也会顺带扫（有最小间隔闸），
#: 这里只是「没人轮询时」的兜底：后台线程按这个节拍把新课程登记进来。
DISCOVER_SCAN_SEC = 5.0


class _HubQueue(_AuthGuard):
    """多课程单 hub 的调度面（2026-09-18 用户指令：一个进程服务所有并行课程）。

    形状：**一个进程托管 N 份 `_JobStore`**，而磁盘布局逐字节不变（每课程仍是
    `tmp/<course>/remote-jobs` + `tmp/<course>/training_log.jsonl`）。这是本设计的
    核心取舍：多课程只是「同一个进程里多挂几份账本」，不是换一套磁盘契约 —— 于是
    既有工具、既有 66 个单课程用例、`tmp/<course>` 约定全部照旧。

    本类负责三件跨课程的事：

      ① **路由**：任意 `/jobs/{id}/...` 先按 job_id 找归属课程。job_id 的幂等键含
         `runId`，而 runId 是每进程随机的 8 字节 hex（`rl/queue.py::RUN_ID`）⇒ 跨课程
         天然不撞；判据 = 「哪个课程的 job 目录里真有它」，命中即入缓存（一次 fs 探测）。
      ② **队形**：每课程一条 FIFO（`claimable_job_ids` 本来就是发布序）；派发时
         **跨课程轮转**（`protocol.rotation_order`）—— 否则一门积压 20 轮的课会把
         其它课程饿死（5 课程机群退化成单课程机群）。
      ③ **进程级状态**（停机达令 / 鉴权闭锁 / worker 登记）：单课程时**借**那一份
         `_JobStore` 的，多课程时用自己的。这不是洁癖 —— 既有用例会在 store 上预热
         `_auth_blocked_until` / 摆 `_workers` 再发 HTTP 请求，若本类另有
         副本，那些预热就不生效了。

    对外的 job 作用域方法**与 `_JobStore` 同名同签名**（内部先解析归属），所以 handler
    侧只需把 `self.hub.` 换成 `self.hub.`，单课程行为逐字节等价。
    """

    #: epoch POST 体上限（与 `_JobStore` 同源，不留第二份魔数）
    BC_EPOCH_BODY_MAX = _JobStore.BC_EPOCH_BODY_MAX

    #: 自动发现时判定「课程目录是不是活的」的新鲜窗口（秒）。
    #: 一个 PPO 轮次是分钟级（rollout 采集 + 云端结算 10–30min），窗口取 1h：正在跑的课
    #: 每轮都会在 `remote-jobs/` 里增删条目、往 jsonl 追加行，秒级就落在窗口内；而几天前
    #: 的陈旧实验目录（同样的磁盘形状，同样残留 pending job）永远不会被误当成「在跑的课」
    #: ——误登记会把已死课程的 job 继续派给真 GPU worker（白烧租约）。
    DISCOVER_FRESH_SEC = 3600.0

    #: 两次扫描之间的最小间隔（秒）：`claim_next` 是派发热路径（worker 每几秒一轮询），
    #: 每次 readdir 都扫一遍没必要，也没意义。
    DISCOVER_SCAN_MIN_SEC = 2.0

    def __init__(
        self,
        stores,
        order=None,
        modes=None,
        now_fn=None,
        discover_root=None,
        discover_fresh_sec: float = DISCOVER_FRESH_SEC,
    ) -> None:
        self._stores: dict[str, _JobStore] = dict(stores)
        self._order: list[str] = [c for c in (order or list(self._stores)) if c in self._stores]
        #: 自动发现根（`--traj-root`）；None = 关（`--discover` 未给，零开销零行为变化）
        self._discover_root: Path | None = Path(discover_root) if discover_root else None
        self._discover_fresh = float(discover_fresh_sec)
        self._discover_last = 0.0
        #: 「跳过未开课课程」的告警去重集（每门课只喊一次，不刷屏）。
        self._no_marker_warned: set[str] = set()
        md = modes or {}
        self._modes: dict[str, str] = {
            c: str(md.get(c) or COURSE_MODE_ONLINE) for c in self._order
        }
        #: 上次派发过的课程（轮转起点）；None = 从序首开始
        self._cursor: str | None = None
        #: job_id -> course（归属解析缓存；job_id 不可复用，故不会失效）
        self._locate_cache: dict[str, str] = {}
        #: 单课程 = 旧形状：进程级状态一律借那一份 store（见类 docstring ③）
        self._solo: _JobStore | None = (
            next(iter(self._stores.values())) if len(self._stores) == 1 else None
        )
        #: 自动发现的扫描闸（`_discover_last` 初值 0 ⇒ 首次调用必扫）
        if self._discover_root is not None:
            self._discover_last = float("-inf")
        _AuthGuard.__init__(self, now_fn)
        # 时钟与单课程 store 同源（测试注入的假时钟必须一致，否则 claimed 标记的时间戳
        # 会混入真实墙钟）。
        self._now = self._solo._now if self._solo is not None else (now_fn or time.time)
        #: 多课程时自己的 worker 登记表（worker_id -> last_seen）——避让链的唯一事实源。
        self._workers: dict[str, float] = {}
        # 停机达令**按课程**（2026-09-18 单 hub 化）：一个 hub 服务所有课程之后，若达令还是
        # 进程级一个布尔，「A 课门禁 ABORT」会连坐 B 课的云机（B 的 worker 下一轮轮询就
        # 拿到 halt 并自停）。故：无课程参数 = 全课程（旧调用方语义，落 `_halt_default`，
        # 新发现的课也继承）；`?course=` = 只动那一门课的例外（`_halts`）。
        self._halt_default = False
        self._halts: dict[str, bool] = {}

    def halt_of(self, course: str) -> bool:
        """本课程是否在停机态（单课程借 store 时恒看那一份 store 的旗标）。"""
        if self._solo is not None:
            return bool(self._solo.halt_workers)
        return bool(self._halts.get(course, self._halt_default))

    def all_halted(self) -> bool:
        """**所有已登记课程**都在停机态。

        空课程表 → False（`--discover` 刚起、还没有课程时“没课可停”，不是停机）——
        否则空闲 worker 会收到一个凭空的停机达令。
        """
        if self._solo is not None:
            return bool(self._solo.halt_workers)
        return bool(self._order) and all(self._halts.get(c, self._halt_default) for c in self._order)

    def set_halt(self, halt: bool, course: str = "") -> bool:
        """置/解停机达令；未知 course → False（不猜、不静默改写全局）。"""
        if self._solo is not None:
            self._solo.halt_workers = bool(halt)
            return True
        if course:
            if course not in self._stores:
                return False
            self._halts[course] = bool(halt)
            return True
        self._halt_default = bool(halt)
        self._halts.clear()
        return True

    # ---- 课程表自动发现（`--discover`） ----
    def add_course(self, name: str, mode: str = COURSE_MODE_ONLINE) -> bool:
        """登记一门课程（现建 `_JobStore`）；已登记/空名/未开发现 → False（幂等）。

        派生目录与 `--course` 启动参数**逐字节相同**（`<root>/<name>/remote-jobs` +
        `<root>/<name>/training_log.jsonl`）⇒ 自动发现的课与显式声明的课在观测面、诊断
        工具、`tmp/<course>` 约定里无法区分，也不该区分。
        """
        c = str(name or "")
        if not c or c in self._stores or self._discover_root is None:
            return False
        self._adopt_solo()
        self._stores[c] = _JobStore(
            self._discover_root / c / "remote-jobs",
            self._discover_root / c / "training_log.jsonl",
            now_fn=self._now,  # 时钟同源：租约时间戳与判定不能一边真墙钟一边假钟
        )
        self._order.append(c)
        m = (mode or "").strip().lower()
        self._modes[c] = m if m in COURSE_MODES else COURSE_MODE_ONLINE
        return True

    def _adopt_solo(self) -> None:
        """从「单课程借 store」切到「多课程自有状态」：把进程级状态搬到自己身上。

        为什么必须搬：单课程时 halt / worker 登记 / 鉴权计数都住在那一份 store 里
        （既有用例直接预热 store 字段），一旦课程数变成 2，这些状态必须继续生效——不搬
        就是「多发现一门课，把停机达令、worker 登记、鉴权闭锁全悄悄清了」。
        """
        st = self._solo
        if st is None:
            return
        self._halt_default = bool(st.halt_workers)
        self._workers = dict(st._workers)
        self._auth_fail = dict(st._auth_fail)
        self._auth_blocked_until = dict(st._auth_blocked_until)
        self._solo = None

    def discover(self) -> list[str]:
        """扫 `<traj_root>/<course>/{remote-jobs,offline}`，把新鲜且未登记的课程登记进来。

        返回本次新增的课程（目录序，稳定）。`--discover` 未开 → 恒空（零开销）。

        为什么以**磁盘**为发现源、而不是让控制台/训练器走一次 HTTP 注册：训练侧把 job
        发布到 `<traj>/remote-jobs` 是**文件系统事实**（hub 与 trainer 共享同一份盘），
        所以「有新课程在跑」这件事本身就写在盘上。再加一条注册旁路就是「会失败、会乱序、
        会忘了调」的第二事实源——而漏注册的后果是那门课**永久饿死**（跨课程轮转表里
        没有它），且表面上「训练正常」。
        """
        root = self._discover_root
        if root is None:
            return []
        now = self._now()
        if now - self._discover_last < self.DISCOVER_SCAN_MIN_SEC:
            return []
        self._discover_last = now
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            return []
        added: list[str] = []
        for ent in entries:
            try:
                if not ent.is_dir():
                    continue
            except OSError:
                continue
            if ent.name in self._stores:
                continue
            try:
                parse_course_arg(ent.name)
            except ProtocolError:
                continue  # 非课程目录（tmp/training-start 之类）——安静跳过
            if not self._course_dir_live(ent, now):
                continue
            if self.add_course(ent.name):
                added.append(ent.name)
        if added:
            print(f"[hub-server] discovered courses: {', '.join(added)}", flush=True)
        return added

    def _serves_course(self, course: str) -> bool:
        """派发闸：**发现模式**下课程目录必须仍带开课标记（`training-enabled.txt`）。

        为什么发现时判过还要在这里再判一次（2026-09-20 事故）：课程表是**发现那一刻**
        建的，而 `remote-jobs/` 里躺着的 pending job 不会自己消失。没有这道闸，任何
        在旧表/旧代码里登记过的课程会把它的**陈旧 job 继续派给真 GPU worker**——
        白烧租约，云端逐份失败（D14 血缘不匹配 / 旧 code.zip 触发自重启），而训练侧
        什么都看不到（那门课早就不跑了）。用户口径：「课程开训需要用户手动开启」——
        删掉标记就该立刻停止派发，不能等到下一次发现扫描或靠控制台记得置离线。

        单课程模式（`--job-root` 直给、无 `--discover`）不受影响：那条路径的「开课」
        就是有人显式起了这个 hub。
        """
        if self._discover_root is None:
            return True
        st = self._stores.get(course)
        if st is None:
            return False
        if (st.job_root.parent / COURSE_ENABLE_MARKER).exists():
            return True
        with self._lock:
            if course not in self._no_marker_warned:
                self._no_marker_warned.add(course)
                print(
                    f"[hub-server] 跳过未开课的 {course}：无 {COURSE_ENABLE_MARKER}"
                    "（控制台「训练」写入 / 「停课」删除）——队列原样保留，开课即恢复派发",
                    flush=True,
                )
        return False

    def _course_dir_live(self, ent: Path, now: float) -> bool:
        """课程目录「在训」判据：**已开课标记**存在，且 `{remote-jobs,offline}` 之一存在且新鲜。

        ★ 开课标记（`training-enabled.txt`）是 2026-09-20 加的**显式闸**：没有它，hub 会把
        tmp/ 下每一门历史课（都有 remote-jobs/ 残影）都当成「在跑的课」登记进课程表，并继续
        把残留的 pending job 派给真 GPU worker（白烧租约）。用户口径：「课程开训需要用户手动
        开启」；标记由控制台开课写、停课删（`common.protocol.COURSE_ENABLE_MARKER`）。
        """
        if not (ent / COURSE_ENABLE_MARKER).exists():
            return False
        for sub in ("remote-jobs", "offline"):
            d = ent / sub
            if not d.is_dir():
                continue
            newest = 0.0
            for p in (d, ent / "training_log.jsonl"):
                try:
                    newest = max(newest, p.stat().st_mtime)
                except OSError:
                    continue
            if newest > 0 and now - newest <= self._discover_fresh:
                return True
        return False

    # ---- 进程级状态（单课程借 store，多课程用自己那份） ----
    #: 进程级读写（`all_halted()` 的旧名）：既有测试/调用方直接读写它。
    @property
    def halt_workers(self) -> bool:
        return self.all_halted()

    @halt_workers.setter
    def halt_workers(self, v: bool) -> None:
        self.set_halt(bool(v))

    # ---- 鉴权面：同样「单课程借 store」。
    # 为什么这四个必须委派而不能用自己那份：鉴权面是**进程级一份**，而单课程 hub 的
    # 计数/封禁历史就住在那一份 `_JobStore` 里——既有用例会在 store 上预热 5 次失败
    # 再断言下一个请求拿到 403，也会在 HTTP 请求后断言 `store._auth_fail` 被更新。
    # 各存一份副本会让这两类断言全部反，且是对生产行为的真实偏离（两个计数器）。
    def auth_failure(self, ip: str) -> int:
        if self._solo is not None:
            return self._solo.auth_failure(ip)
        return _AuthGuard.auth_failure(self, ip)

    def auth_success(self, ip: str) -> None:
        if self._solo is not None:
            self._solo.auth_success(ip)
            return
        _AuthGuard.auth_success(self, ip)

    def is_blocked(self, ip: str) -> bool:
        if self._solo is not None:
            return self._solo.is_blocked(ip)
        return _AuthGuard.is_blocked(self, ip)

    def blocked_remaining(self, ip: str) -> float:
        if self._solo is not None:
            return self._solo.blocked_remaining(ip)
        return _AuthGuard.blocked_remaining(self, ip)

    def _registry(self) -> dict[str, float]:
        """生效的 worker 登记表（单课程 = store 的，多课程 = 自己的）。"""
        return self._solo._workers if self._solo is not None else self._workers

    def note_worker(self, worker_id: str) -> None:
        """登记一次 worker 轮询（peek / priority 入口）。空 id 不记（无身份无法去重计数）。"""
        wid = (worker_id or "").strip()
        if not wid:
            return
        reg = self._registry()
        with self._lock:
            reg[wid] = self._now()

    def active_worker_count(self) -> int:
        """窗口内**不同** worker 数（避让判据「还有别的卡能接手」的唯一口径）。"""
        now = self._now()
        with self._lock:
            return len(
                {wid for wid, seen in self._registry().items() if now - seen <= WORKER_SEEN_WINDOW_SEC}
            )

    # ---- 课程表与归属 ----
    def courses(self) -> list[str]:
        return list(self._order)

    def course_of(self, job_id: str) -> str | None:
        """job_id → 归属课程；**找不到返回 None**（不是空串！）。

        为什么必须用 None 区分：单课程队列（以及旧单课程 hub）的课程名**就是空串**
        （`tmp/nocourse` 那套约定）。用空串兼作「找不到」会把它当成找不到 —— 直接后果
        是 `/jobs/peek` 刚列出的 job 立刻解析不到归属，handler 打到哨兵路径上 500
        （2026-09-18 白测一次的真故障）。

        为什么搜目录而不是搜账本：账本行里没有课程字段（磁盘契约不变），而
        `<job_root>/<job_id>/` 的存在本身就是归属证据，且是一次 fs 调用 —— 比读账本便宜。
        """
        jid = str(job_id or "")
        if not jid:
            return None
        hit = self._locate_cache.get(jid)
        if hit is not None:
            return hit
        for course in self._order:
            try:
                if (self._stores[course].job_root / jid).exists():
                    self._locate_cache[jid] = course
                    return course
            except OSError:
                continue
        return None

    def _store_of(self, job_id: str) -> _JobStore | None:
        course = self.course_of(job_id)
        return None if course is None else self._stores.get(course)

    def mode_of(self, course: str) -> str:
        return self._modes.get(course, COURSE_MODE_ONLINE)

    def offline_courses(self) -> list[str]:
        return [c for c in self._order if self.mode_of(c) == COURSE_MODE_OFFLINE]

    def set_mode(self, course: str, mode: str) -> bool:
        """热切一门课的模式（在线/离线）。非法课程/模式 → False。

        volatile（与 halt 同性质）：重启回启动参数给定的模式。
        """
        if course not in self._stores:
            return False
        m = (mode or "").strip().lower()
        if m not in COURSE_MODES:
            return False
        self._modes[course] = m
        return True

    def active_courses(self) -> int:
        """**在实时派发**的课程数（竞速判据的分母）：非离线，且有待领或未过期在飞 job。

        离线课程不算（用户口径：它不实时派发 PPO）；已跑完无待办的课程不算（没活可抢，
        把它算进去只会白降压竞速阈值）。
        """
        n = 0
        for course in self._order:
            if self.mode_of(course) == COURSE_MODE_OFFLINE:
                continue
            st = self._stores[course]
            if st.claimable_job_ids() or st.inflight():
                n += 1
        return n

    # ---- 派发（跨课程轮转 + 超时换 worker） ----
    def claim_next(
        self, worker_id: str = "", offline_ok: bool = False
    ) -> tuple[str, str, str] | None:
        """取下一份该派发的 job → (course, job_id, lease_token)；无 → None。

        轮转从**上次派发的下一门**开始（`rotation_order`）：每课程一条 FIFO，若每次都
        从序首扫，一门课的积压会把其它课程饿死。

        避让（用户口径「超时回落队首并改为推送其它 worker」）：候选 job 的上一份租约是
        **过期死掉的**且持有人就是本次请求者时，本次跳过它（`avoid_expired_holder`）——
        但机群只剩一个活跃 worker 时不避让（否则它自己超时过的 job 谁都领不到 = 停摆）。

        离线课（2026-09-19 用户口径「也支持带特别标识的云端 worker 在线领取」）：
        只有 `offline_ok=True`（worker 自报能跑完整段）的请求才能领——它不实时派发，
        但也**不是**谁都领不到的坟墓。带标 worker 仍可领在线课（课程与 worker 正交）。
        """
        # 派发前扫一次（有最小间隔闸）：新课程/新 job 目录出现后，**下一次轮询**就能被领到，
        # 不必等后台节拍——否则新开的课在最坏情况下要等一个扫描周期才有人来领活。
        self.discover()
        active_workers = self.active_worker_count()
        for course in rotation_order(self._order, self._cursor):
            if not self._serves_course(course):
                continue
            offline = self.mode_of(course) == COURSE_MODE_OFFLINE
            if offline and not offline_ok:
                continue
            st = self._stores[course]
            for jid in st.claimable_job_ids():
                # 只给「允不允许避让」的闸；身份比对在 store 里（它才知道租约回收后的
                # stale 记录，在这里判会踩时序——见 `may_avoid_stale_holder` docstring）。
                avoid = may_avoid_stale_holder(worker_id, active_workers)
                tok = st.claim(jid, worker_id=worker_id, avoid_stale_holder=avoid)
                if tok is None:
                    self._announce_freeze(course, jid)
                    continue  # 活租约在持 / 本次该避让 / 并发领取竞负 / 已熔断冻结
                with self._lock:
                    self._cursor = course
                return course, jid, tok
        return None

    def traj_root(self) -> Path | None:
        """课程根目录（`<traj>/<课>/{remote-jobs,training_log.jsonl}` 的 `<traj>`）。

        发现模式 = `--traj-root`；单课程模式由 `--job-root`（= `<traj>/<课>/remote-jobs`）
        回推两级。推不出来（测试里的裸 job_root）⇒ None，调用方据此拒服务而不是猜路径。
        """
        if self._discover_root is not None:
            return self._discover_root
        if self._solo is not None:
            return self._solo.job_root.parent.parent
        return None

    def course_dir(self, course: str) -> Path:
        """课程目录 `<traj>/<课>`（`job_root` = `<traj>/<课>/remote-jobs` 回推一级）。"""
        return self._stores[course].job_root.parent

    def resume_sources(self, course: str) -> list[dict]:
        """一门课的续跑锚点来源（**两个来源、同一台机器**）：自回传 + 人工导入。

        * `backfeed`：云机补传落下的 `<job_root>/offline/<run_id>/it-NNN/`；
        * `import`  ：控制台「导入产物」解出的 `<traj>/<课>/deliver/<run_id>/it-NNN/`
          （`remote.deliver_zip` 的落地布局，与回传同一形状——所以两边的判据能共用）。

        两个来源都要：用户口径是「云机回传**或者**人工导入 权重/opt/指标 后」再领任务都要
        能接上——只认回传就漏了手动那条路（而手动那条恰恰是 hub 不在场时的唯一路）。
        """
        st = self._stores[course]
        out: list[dict] = []
        for it, info in st.complete_rounds().items():
            out.append(
                {"it": int(it), "run_id": info["run_id"], "source": "backfeed", "dir": Path(info["dir"])}
            )
        deliver_root = st.job_root.parent / "deliver"
        try:
            runs = sorted(p for p in deliver_root.iterdir() if p.is_dir())
        except OSError:
            runs = []
        for run_dir in runs:
            try:
                it_dirs = sorted(p for p in run_dir.iterdir() if p.is_dir())
            except OSError:
                continue
            for it_dir in it_dirs:
                if not it_dir.name.startswith("it-"):
                    continue
                try:
                    it = int(it_dir.name[3:])
                except ValueError:
                    continue
                if not all((it_dir / n).is_file() for n in _JobStore.RESUME_PARTS):
                    continue
                out.append(
                    {"it": int(it), "run_id": run_dir.name, "source": "import", "dir": it_dir}
                )
        return out

    def merge_eval_rows(self, course: str, rows: object) -> int:
        """把离线补传来的云机 A 层评估行并进**课程账本** `eval_log.jsonl`，返回新增行数。

        为什么要 hub 做这一步：那是控制台/门判唯一读的账本（`<traj>/<课>/eval_log.jsonl`），
        而云机那侧只看得见自己的产物目录——不并进去，整段的评估读数要等「跑完人工导入」
        才存在，而「一条跑偏的腿」正是这条腿要尽早看见的东西。

        去重按 `(iter, wver, stage, seed)`（`rl.eval_local.eval_row_key`）：补传天然会重传
        （重连/重启续投），重复行会让曲线出现两个同一点。只接 `event:"eval"` 逐局行——
        summary 由课程侧按合并后的台账重算，云端那份不并（避免同 iter 两个 summary 打架）。
        """
        if not isinstance(rows, list) or not rows:
            return 0
        from rl.eval_local import append_eval_rows

        ledger = self._stores[course].job_root.parent / "eval_log.jsonl"
        return append_eval_rows(ledger, [r for r in rows if isinstance(r, dict)])

    def resume_anchor(self, course: str) -> dict | None:
        """最新一轮**同轮齐全**的续跑锚点（`None` = 没有可交回的进度）。

        选法（用户 2026-09-22 口径「必须同轮齐全，否则退到更早轮」）：从最大的 it 往下找，
        第一个三件齐全的轮次就是锚点；同一 it 有两个来源时取目录 mtime 更新的那个
        （回传与导入可能各有一份，人刚导完的那份更可信）。**绝不**用「部分齐全」的轮次
        凑数——那会静默丢掉 Adam 动量或那一轮的指标。
        """
        by_it: dict[int, list[dict]] = {}
        for cand in self.resume_sources(course):
            by_it.setdefault(int(cand["it"]), []).append(cand)
        for it in sorted(by_it, reverse=True):
            cands = by_it[it]
            if len(cands) > 1:
                try:
                    cands = sorted(cands, key=lambda c: c["dir"].stat().st_mtime, reverse=True)
                except OSError:
                    pass
            best = cands[0]
            d = Path(best["dir"])
            try:
                row = json.loads((d / "row.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                row = {}
            wfp = str((row or {}).get("weights_fp", "") or "")
            if not wfp:
                try:
                    wfp = hashlib.sha256((d / "weights.json").read_bytes()).hexdigest()
                except OSError:
                    wfp = ""
            return {
                "course": course,
                "it": int(it),
                "run_id": str(best["run_id"]),
                "source": str(best["source"]),
                "weights_fp": wfp,
                "opt_bytes": int(row.get("opt_bytes", 0) or 0),
                "metrics": row if isinstance(row, dict) else {},
                "_dir": str(d),
            }
        return None

    def task_pack_path(self, course: str) -> Path:
        """整段任务包落点：`<traj>/<课>/task-<课>.zip`（控制台导出的就是它）。

        课程名进的是磁盘路径 ⇒ 在这里断掉分隔符/`..`（与 `parse_course_arg` 同一条边界）。
        hub 不知道 traj 根 ⇒ ProtocolError（响亮，不猜）。
        """
        root = self.traj_root()
        if root is None:
            raise ProtocolError("hub 不知道课程根目录（--traj-root / --discover 未给）")
        name = (course or "").strip()
        if not name or name in (".", "..") or any(ch in name for ch in ("/", "\\", "\x00")):
            raise ProtocolError(f"课程名非法: {course!r}（不得含路径分隔符/空名）")
        if ".." in name:
            raise ProtocolError(f"课程名非法: {course!r}（不得含 ..）")
        return root / name / f"task-{name}.zip"

    def offline_progress(self) -> dict[str, dict]:
        """每课程已收到的离线进度（补传产物）：`{课: {run_id: {its: [...], count, last_mtime}}}`。

        为什么单开一个读面：段内进度**只能**从产物目录看出来（hub 不跑那几轮，账本里没有
        它们的行），而控制台要在长段期间看到进度曲线——「它在跑」与「它挂了」的唯一区别
        就是最近一轮的时间戳。只读列目录，不解析产物（解析权重不在观测面做）。
        """
        out: dict[str, dict] = {}
        for course in self._order:
            runs: dict[str, dict] = {}
            base = self._stores[course].job_root / _JobStore.OFFLINE_DIR
            try:
                run_dirs = sorted(p for p in base.iterdir() if p.is_dir())
            except OSError:
                run_dirs = []
            for run_dir in run_dirs:
                its: list[int] = []
                last = 0.0
                try:
                    for it_dir in run_dir.iterdir():
                        if not it_dir.is_dir() or not it_dir.name.startswith("it-"):
                            continue
                        try:
                            its.append(int(it_dir.name[3:]))
                            last = max(last, it_dir.stat().st_mtime)
                        except (ValueError, OSError):
                            continue
                except OSError:
                    continue
                runs[run_dir.name] = {
                    "its": sorted(its),
                    "count": len(its),
                    "last_mtime": last,
                }
            if runs:
                out[course] = runs
        return out

    # ---- 观测面 ----
    def queue_state(self) -> dict:
        """`/admin/queue`：每课程的深度/在飞/最近心跳/退避记录 + 竞速的两个判据数。

        只读观测——不参与任何调度决策，但它是「为什么某门课在饿着」的唯一答案面。
        """
        now = self._now()
        courses: dict[str, dict] = {}
        for course in self._order:
            st = self._stores[course]
            pending = st.claimable_job_ids()
            inflight: list[dict] = []
            for jid in st.inflight():
                holder = st.lease_worker(jid) or "?"
                inflight.append(
                    {
                        "job_id": jid,
                        "worker": holder,
                        "heartbeat_ago": round(now - st._last_heartbeat.get(jid, now), 1),
                    }
                )
            courses[course] = {
                "mode": self.mode_of(course),
                # 停机达令是**每课程**的（一门课的门禁 ABORT 只停那门课的云机）
                "halt": self.halt_of(course),
                "pending": pending,
                "pending_n": len(pending),
                "inflight": inflight,
                "next_job": pending[0] if pending else None,
                # §4.1 可观测（毒包熔断）：冻了谁、冻在几次；已冻的 job 已不在 pending 里，
                # 不给这一行就只剩「队列莫名其妙短了」
                "frozen": {
                    jid: st.frozen_info(jid) for jid in st.frozen_job_ids()
                },
            }
        return {
            "courses": courses,
            "order": self._order,
            "cursor": self._cursor,
            "offline": self.offline_courses(),
            "active_courses": self.active_courses(),
            "active_workers": self.active_worker_count(),
            "halt": self.all_halted(),
        }

    # ---- job 作用域委派（与 `_JobStore` 同名同签名） ----
    def _job_dir(self, job_id: str) -> Path:
        """归属课程 job 目录；找不到 → 哨兵路径（`manifest.json` 必不存在 ⇒ 调用方 404）。"""
        st = self._store_of(job_id)
        if st is None:
            return _MISSING_ROOT / str(job_id)
        return st._job_dir(job_id)

    def job_root_of(self, course: str) -> Path | None:
        st = self._stores.get(course)
        return st.job_root if st else None

    def shared_code_zip(self, course: str = "") -> Path | None:
        """共享 code.zip 路径：给课程就用它，否则用第一份**真存在**的（bootstrap 用）。"""
        order = [course] if course in self._stores else self._order
        for c in order:
            p = self._stores[c].job_root / "code.zip"
            if p.exists():
                return p
        return None

    def _announce_freeze(self, course: str, jid: str) -> None:
        """★ 毒包熔断告警（§4.1）——一次性事件，喊过就不重喊。

        为什么在这里喊：检测点在 store（它才看得到租约过期），而告警要课程名与认领者，
        两者都在队列层手上。**新 claim 面也必须喊**（R2-6）：否则旧的轮询面退役后
        熔断就只剩下「静默不再回池」，而那正是 §4.1 事故要治的东西。
        """
        st = self._store_of(jid)
        froze = st.consume_freeze_announcement(jid) if st else None
        if froze is None:
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] ★ 熔断冻结："
            f"job={jid} course={course or '-'} "
            f"—— 连续 {froze.get('reclaims')} 次认领后零回传"
            f"（最后一次认领者={froze.get('worker') or '?'}）；"
            "已从可领取池移除，**重发不清冻结**；"
            f"确认后解冻：POST /admin/unfreeze job_id={jid}",
            flush=True,
        )

    def claimable_job_ids(self, course: str) -> list[str]:
        st = self._stores.get(course)
        return st.claimable_job_ids() if st else []

    def claim(
        self,
        job_id: str,
        ttl: float = CLAIM_TTL_SEC,
        worker_id: str = "",
        avoid_stale_holder: bool = False,
        mode: str | None = None,
        expected_epoch: int | None = None,
    ) -> str | None:
        """（单份领取；多课程的挑选入口是 `claim_next`——离线课的能力闸在那边。）"""
        st = self._store_of(job_id)
        if st is None:
            return None
        return st.claim(
            job_id,
            ttl=ttl,
            worker_id=worker_id,
            avoid_stale_holder=avoid_stale_holder,
            mode=mode,
            expected_epoch=expected_epoch,
        )

    # ---- 新调度面（2026-09-22，plan/transfer-scheduling）：peek / claim / priority ----
    def peek_jobs(
        self,
        *,
        worker_id: str = "",
        offline_ok: bool = False,
        n: int = PEEK_MAX,
    ) -> list[dict]:
        """候选 job（**不认领**：无租约、无副作用、不动游标）——§2.6 的软持有候选来源。

        与旧 `claim_next` 同三道闸：`_serves_course`（开课标记）、离线课的能力闸
        （`offline_ok` = worker 自报能跑完整段）、冻结/已落盘的排除（在 `claimable_job_ids` 里）。

        跨课程公平性：顺序取 `rotation_order(self._order, self._cursor)`，**只读不写**
        （R2-C2）——游标由真正 claim 成功的那一方推进（`claim_job`）。若在这里推进，
        「看一眼」就会移走别人的轮次；而若完全不推，轮转又会钉死在序首（一门课饿死）。

        每课程**至多给一个**候选：候选是「这轮可以干哪几门课」，不是「把队首扫空」
        （深度 3 的预取靠多轮 peek 填满，而不是靠一次拿 16 个）。
        """
        self.discover()
        # R2-2：登记表（避让链的唯一输入）改由 peek/priority 喂——缺它
        # `active_worker_count()` 恒 0 ⇒ 避让链静默失效（纯函数用例测不出「调用点为 0」）。
        self.note_worker(worker_id)
        out: list[dict] = []
        want = max(1, int(n))
        for course in rotation_order(self._order, self._cursor):
            if len(out) >= want:
                break
            if not self._serves_course(course):
                continue
            if self.mode_of(course) == COURSE_MODE_OFFLINE and not offline_ok:
                continue
            st = self._stores[course]
            ids = st.claimable_job_ids()
            if not ids:
                continue
            jid = ids[0]
            man = self._manifest_summary(jid)
            out.append(
                {
                    "job_id": jid,
                    "course": course,
                    "payload_bytes": man.get("payload_bytes", 0),
                    "payload_sha256": man.get("payload_sha256"),
                    "runId": man.get("runId"),
                    "it": man.get("it"),
                }
            )
        return out

    def _manifest_summary(self, job_id: str) -> dict:
        """候选的 manifest 摘要（**不**把整份 manifest 塞进 peek：那是认领后才需要的东西）。"""
        jd = self._job_dir(job_id)
        try:
            man = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(man, dict):
            return {}
        try:
            pl = find_payload(jd)
            size = pl.stat().st_size if pl is not None else 0
        except OSError:
            size = 0
        return {
            "runId": man.get("runId"),
            "it": man.get("it"),
            "payload_bytes": int(size),
            # `payload_sha256` 也进摘要（2026-09-22，P2）：预取拿到的字节必须能**就地**校验
            # 是不是这份 job 的 payload——不带它的话，预取会把「sha 不符」的发现推到开算前
            # （那时已占了 claim 租约，错一份就多一次租约往返）。
            "payload_sha256": man.get("payload_sha256"),
        }

    def claim_job(
        self,
        job_id: str,
        *,
        mode: str = CLAIM_MODE_EXCLUSIVE,
        worker_id: str = "",
        expected_epoch: int | None = None,
    ) -> ClaimOutcome:
        """新 claim 面（`POST /jobs/{id}/claim`）的唯一实现入口。

        与旧 `claim_next` 的差别：挑活已在客户端（peek + priority）；这里只负责「这一份
        归不归你」+ 游标推进 + 熔断告警——**不再**在这里扫整张表。
        避让的「允不允许」仍在调用方算（`may_avoid_stale_holder`，R2-2 的避让链）。
        """
        st = self._store_of(job_id)
        if st is None:
            return ClaimOutcome(False, "", "unknown", "unknown")
        if mode not in CLAIM_MODES:
            return ClaimOutcome(False, "", "bad_mode", f"mode 必须是 {list(CLAIM_MODES)}")
        avoid = may_avoid_stale_holder(worker_id, self.active_worker_count())
        out = st.claim_outcome(
            job_id,
            mode=mode,
            worker_id=worker_id,
            avoid_stale_holder=avoid,
            expected_epoch=expected_epoch,
        )
        if out.ok:
            course = self.course_of(job_id) or ""
            with self._lock:
                self._cursor = course  # R2-C2：只有真正拿到才推进轮转起点
            return out
        self._announce_freeze(self.course_of(job_id) or "", job_id)
        return out

    def priority_view(
        self,
        *,
        worker_id: str = "",
        job_ids: list[str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, str]]:
        """`POST /jobs/priority` 的事实面：`(epoch, {jid: 优先级}, {jid: 一行理由})`。

        问询的 job 集合 = 调用方给的 `held`（软持有 ∪ 已 claim 未开算）；`worker_id` 用来
        把**自己的**痕迹排除掉——自己手里那份 computing 不叫「别处在算」（§1.4）。
        """
        self.note_worker(worker_id)  # R2-2
        prios: dict[str, str] = {}
        reasons: dict[str, str] = {}
        epoch = 0
        for jid in job_ids or []:
            st = self._store_of(jid)
            if st is None:
                prios[jid] = PRIORITY_NONE
                reasons[jid] = "unknown job（本 hub 无此 job）"
                continue
            p, why = st.priority_for(jid, exclude_worker=worker_id or "")
            prios[jid] = p
            reasons[jid] = why
            epoch = max(epoch, st.scheduling_epoch())
        if not job_ids:
            epoch = max((st.scheduling_epoch() for st in self._stores.values()), default=0)
        return epoch, prios, reasons

    def start_job(self, job_id: str, worker_id: str = "") -> bool:
        st = self._store_of(job_id)
        return st.start_job(job_id, worker_id) if st else False

    def set_ready(self, job_id: str, worker_id: str = "") -> bool:
        st = self._store_of(job_id)
        return st.set_ready(job_id, worker_id) if st else False

    def abandon(self, job_id: str, worker_id: str = "") -> bool:
        """合法放弃（R1-3）：release 租约 + 清可见性 + **零** reclaim。幂等。"""
        st = self._store_of(job_id)
        return st.abandon_job(job_id, worker_id) if st else False

    def epoch_of(self, job_id: str) -> int:
        """该 job 归属 store 的调度面版本（单课程/多课程统一口径；未知 → 0）。"""
        st = self._store_of(job_id)
        return st.scheduling_epoch() if st else 0

    def job_status(self, job_id: str) -> dict:
        """`GET /jobs/{id}/status` 的调度面摘要（cancel-watcher 的判据就在里面）。"""
        st = self._store_of(job_id)
        if st is None:
            return {}
        facts = st.scheduling_facts(job_id)
        prio, why = st.priority_for(job_id)
        return {
            "landed": bool(facts["landed"]),
            "ready": bool(facts["ready"]),
            "computing_at": facts["computing_at"],
            "lease_holder": facts["lease_holder"],
            "epoch": st.scheduling_epoch(),
            "priority": prio,
            "reason": why,
        }

    def job_priority_of(self, job_id: str, *, exclude_worker: str = "") -> str:
        """该 job 当前的优先级档（push 腿用的**同一张表**，§2.9；未知 → none）。

        为什么快照一份而不是让 push 腿自己去拼：判据落在 store 的 `_claimed`/`_computing`/
        `_ready` 上（同一把锁下的一致读），在队列层重新拼就是第二个事实源。
        """
        st = self._store_of(job_id)
        if st is None:
            return PRIORITY_NONE
        prio, _why = st.priority_for(job_id, exclude_worker=exclude_worker)
        return prio

    def reclaims(self, job_id: str) -> int:
        """该 job 的「认领后零回传」次数（§4.1 熔断判据；未知 job → 0）。"""
        st = self._store_of(job_id)
        return st.reclaims(job_id) if st else 0

    def frozen_info(self, job_id: str) -> dict | None:
        """该 job 的冻结记录（未冻结/未知 → None）。"""
        st = self._store_of(job_id)
        return st.frozen_info(job_id) if st else None

    def consume_freeze_announcement(self, job_id: str) -> dict | None:
        """取一次「刚刚落冻」的告警载荷（一次性；未冻结/已喊过 → None）。"""
        st = self._store_of(job_id)
        return st.consume_freeze_announcement(job_id) if st else None

    def unfreeze(self, job_id: str) -> dict | None:
        """人工解冻（§4.1 可逆口）：返回被解冻的记录（本来未冻结 → None）。"""
        st = self._store_of(job_id)
        return st.unfreeze(job_id) if st else None

    def frozen_jobs(self) -> list[str]:
        """全部已冻结 job（跨课程，观测面用）。"""
        out: list[str] = []
        for st in self._stores.values():
            out.extend(st.frozen_job_ids())
        return out

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        st = self._store_of(job_id)
        return st.heartbeat(job_id, lease_token) if st else False

    def release(self, job_id: str, lease_token: str) -> bool:
        st = self._store_of(job_id)
        return st.release(job_id, lease_token) if st else False

    def result_token_ok(self, job_id: str, lease_token: str) -> bool:
        st = self._store_of(job_id)
        # 找不到归属 = 这个 job 不归本 hub 管 ⇒ **拒收**（放行会写出一个无归属的 result）
        return st.result_token_ok(job_id, lease_token) if st else False

    def store_result(self, job_id: str, result: dict) -> bool:
        st = self._store_of(job_id)
        return st.store_result(job_id, result) if st else False

    def store_job_failure(self, job_id: str, rec: dict) -> bool:
        st = self._store_of(job_id)
        return st.store_job_failure(job_id, rec) if st else False

    def job_failure(self, job_id: str) -> dict | None:
        st = self._store_of(job_id)
        return st.job_failure(job_id) if st else None

    def get_result(self, job_id: str) -> dict | None:
        st = self._store_of(job_id)
        return st.get_result(job_id) if st else None

    def mark_completed(self, job_id: str) -> None:
        st = self._store_of(job_id)
        if st:
            st.mark_completed(job_id)

    def record_payload_sent(self, job_id: str, n: int) -> None:
        st = self._store_of(job_id)
        if st:
            st.record_payload_sent(job_id, n)

    def record_result_recv(self, job_id: str, n: int) -> None:
        st = self._store_of(job_id)
        if st:
            st.record_result_recv(job_id, n)

    def record_push_wire(self, job_id: str, n: int, payload_bytes: int, upload_sec: float) -> None:
        """hub 中介推送的传输实测（按 job 归属委派；未知 job 静默跳过）。"""
        st = self._store_of(job_id)
        if st:
            st.record_push_wire(job_id, n, payload_bytes, upload_sec)

    def wire_stats(self, job_id: str) -> dict:
        st = self._store_of(job_id)
        return st.wire_stats(job_id) if st else {}

    def store_bc_epoch(self, job_id: str, body: dict) -> bool:
        st = self._store_of(job_id)
        return st.store_bc_epoch(job_id, body) if st else False

    def get_bc_resume(self, job_id: str) -> dict | None:
        st = self._store_of(job_id)
        return st.get_bc_resume(job_id) if st else None

    def get_bc_metrics(self, job_id: str) -> list[dict]:
        st = self._store_of(job_id)
        return st.get_bc_metrics(job_id) if st else []

    def lease_expires_in(self, job_id: str) -> float | None:
        """距租约到期秒数；无租约/无归属 → None（`/jobs/{id}/status` 观测用）。"""
        st = self._store_of(job_id)
        if st is None:
            return None
        exp = st._leases.get(job_id)
        return None if exp is None else round(exp - st._now(), 1)

    def last_heartbeat_ago(self, job_id: str) -> float | None:
        """距上次心跳秒数；从未心跳/无归属 → None。"""
        st = self._store_of(job_id)
        if st is None:
            return None
        hb = st._last_heartbeat.get(job_id)
        return None if hb is None else round(st._now() - hb, 1)

    def append_ledger(self, job_id: str, event: dict) -> None:
        """往**归属课程**的账本追加一行（`/jobs/{id}/fail` 的 job_cancelled 用）。"""
        st = self._store_of(job_id)
        if st is not None:
            st._append_ledger(event)

    # ---- 离线产物补传（路由：显式 course > 已有 offline 目录 > 400） ----
    def locate_offline_course(self, body: dict, query_course: str = "") -> str | None:
        """定一段补传产物归哪门课程；**归不到返回 None**（不是空串）。

        ① 体里的 `course`/`course_name`（节点从 job manifest 抄来，最可靠）；
        ② `?course=` 查询参数（运维手工补传）；
        ③ 已有 `offline/<run_id>/` 目录的课程 —— 补传天然会重传续投，第一条推送
           建目录、后续自动归位（幂等）。

        为什么返回 None 而不用空串表「归不到」：单课程队列（与旧单课程 hub）的课程名
        **就是空串**，空串兼作缺失值会让单课程下的每一次补传都 400（2026-09-18 实测）。
        """
        named = str(body.get("course") or body.get("course_name") or query_course or "").strip()
        if named in self._stores:
            return named
        run_id = str(body.get("run_id") or "")
        if run_id:
            try:
                safe = sanitize_run_id(run_id)
            except ProtocolError:
                return None
            for course in self._order:
                try:
                    if (self._stores[course].job_root / _JobStore.OFFLINE_DIR / safe).exists():
                        return course
                except OSError:
                    continue
        return None

    def store_offline_artifact(self, course: str, body: dict) -> dict:
        return self._stores[course].store_offline_artifact(body)

    def store_offline_result(self, course: str, body: dict) -> dict:
        return self._stores[course].store_offline_result(body)


# ------------------------------------------------------------------ HTTP


class HubHandler(
    AdminRoutes,
    ScheduleRoutes,
    ResultRoutes,
    BlobRoutes,
    OfflineRoutes,
    BaseHTTPRequestHandler,
):
    """单例 handler：类属性持共享调度面（ThreadingHTTPServer 每请求新建实例）。

    注：类属性名从 `store` 改为 `hub`（2026-09-18 多课程）——它现在是**多课程调度面**
    `_HubQueue` 而不再是单一 job store；单课程时它是包着一份 `_JobStore` 的队列，行为
    与改造前逐字节等价（见 `make_server`）。
    """

    hub: _HubQueue = None  # type: ignore[assignment]  # 由 factory 注入
    #: hub 中介 push 派发器（`--push` 时注入；None = 未启用，端点 409）。
    push: PushDispatcher | None = None

    #: ip -> 上次打印「封禁拒绝」的墙钟（节流：被封客户端高频轮询时每 ip 每分钟一条）
    _blocked_logged: dict[str, float] = {}

    # ---- 基础 ----
    def log_message(self, fmt: str, *args: object) -> None:  # 只打非常规事件
        # 静默高频只读访问（/ping 健康检查、/jobs/peek 拉活、result/payload 轮询含 404）
        # ——这些在多 worker 下每秒可打多行，把 hub-server.out 刷爆。POST result、
        # ERROR、/admin、/code 仍保留。
        # 例外：**401/403 永不静默**（2026-09-16 x3-step 事故）——鉴权失败与封禁
        # 恰恰最爱发生在这些高频路径上，静默等于抹掉唯一的破案线索。
        line = fmt % args
        if (
            '"GET /ping ' in line
            or '"GET /jobs/peek' in line
            or ('"GET /jobs/' in line and ('/result ' in line or '/payload ' in line))
        ) and ' 401 ' not in line and ' 403 ' not in line:
            return
        # 隧道来源还原（B，2026-09-17）：回源流量在这里本来全写成 127.0.0.1（x1-rebirth 事故
        # 排查时最大的阻雾），有归因来源就补上——`log_message` 只打非常规事件，不刷屏。
        peer = self.client_address[0]
        # `self.headers` 在请求行都解析失败的早期错误路径上可能是 None（BaseHTTPRequestHandler
        # 先把它置 None 再 parse）——日志绝不能在错误路径上再抛一次异常。
        hdrs = self.headers
        src_ip, via = attributed_source(peer, hdrs.get(CF_SOURCE_HEADER, "") if hdrs else "")
        tag = f"{peer} src={src_ip} via=cf" if via == "cf" else peer
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server {tag}] {line}",
            flush=True,
        )

    def _auth_ok(self) -> bool:
        peer = self.client_address[0]
        # 归因来源（B）：隧道（回环对端）按边缘头还原真实来源；直连只认 TCP 对端。
        ip, via = attributed_source(peer, self.headers.get(CF_SOURCE_HEADER, ""))
        auth = self.headers.get(AUTH_HEADER, "")
        token = self.server.token if hasattr(self.server, "token") else ""
        # ① 先验 token（2026-09-17 改序）：**合法 token 永远放行**，封禁只拒无效鉴权尝试。
        # 旧序先查 is_blocked ⇒ 一次误封会把该来源 IP 的全部流量（console 健康检查、训练
        # 循环、worker 拉活）403 一小时，而封禁只住进程内存、只能靠重启清除——重启又被
        # 端口守卫挡死 = 死锁（2026-09-17 hub-server 重启事故）。
        if token and auth == f"Bearer {token}":
            self.hub.auth_success(ip)
            return True
        # ② 无效鉴权尝试：已封禁 → 只拒（不重复计数，封禁到点自愈）；未封禁 → 计数，满 5 封禁。
        if self.hub.is_blocked(ip):
            self._log_blocked(ip, peer=peer, via=via)
            self._json({"error": "ip blocked"}, 403)
            return False
        n = self.hub.auth_failure(ip)
        if _is_loopback(ip):
            counter, note = "AUTH FAIL", "（回环来源：不计数、不封禁）"
        else:
            counter = f"AUTH FAIL {n}/5"
            note = " — 已封禁该 IP 3600s（仅拒无效鉴权；合法 token 不受影响）" if n >= 5 else ""
        src = f"peer={peer}" if via == "peer" else f"peer={peer} src={ip} via=cf"
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server {ip}] {counter} "
            f"path={self.path.split('?', 1)[0]} {src}{note}",
            flush=True,
        )
        self._json({"error": "unauthorized"}, 401)
        return False

    def _log_blocked(self, ip: str, peer: str = "", via: str = "peer") -> None:
        """封禁命中审计（每 ip 每 60s 一条）：被封客户端往往仍在高频轮询，
        不节流会把日志刷爆，但完全不打则「谁在被封」永远查不到。"""
        now = time.time()
        if now - HubHandler._blocked_logged.get(ip, 0.0) < 60:
            return
        HubHandler._blocked_logged[ip] = now
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server {ip}] BLOCKED — 请求被拒 "
            f"path={self.path.split('?', 1)[0]} 剩余封禁 "
            f"{int(self.hub.blocked_remaining(ip))}s（5/5 次**无效**鉴权触发；"
            f"合法 token 照常放行——本次请求的 token 不匹配；"
            f"归因: peer={peer} via={via}）",
            flush=True,
        )

    def _json(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(
        self,
        data: bytes,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
    ) -> None:
        """发送整块 body：大 body **分片 + 有界**，且完成/停滞各有一行日志。

        2026-09-20 事故（云机领到 job 后几分钟零输出）的 hub 侧半边：`wfile.write()`
        没有超时，对端半开时阻塞**永不返回** ⇒ 客户端永远拿不到 payload，而 `/payload`
        的访问行被高频静默规则吃掉 ⇒ 两端日志同时沉默（唯一的现象是「卡住」）。
        现在：分片写（每片独立超时）⇒ 停滞 ≤SEND_TIMEOUT_SEC 内被断掉并**响亮打印**
        已发字节数；≥SEND_LOG_MIN_BYTES 的 body 完成时也打一行（可对账传输时长/速率）。
        """
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            # 习惯文件名（下载时手一按就是这个名字，不必再改名）
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        path = self.path.split("?", 1)[0]
        total = len(data)
        sent = 0
        t0 = time.time()
        stalled = False
        try:
            self.connection.settimeout(SEND_TIMEOUT_SEC)
            view = memoryview(data)
            while sent < total:
                n = self.wfile.write(view[sent : sent + SEND_CHUNK])
                if n is None:  # 缓冲写（wbufsize > 0）：视作整片已收
                    n = min(SEND_CHUNK, total - sent)
                if n <= 0:  # 0 = 对端不再接收（半开）——不能空转
                    raise TimeoutError("write 返回 0——对端不再接收")
                sent += n
        except OSError as e:
            stalled = True
            print(
                f"[{time.strftime('%H:%M:%S')}] [hub-server] 响应发送**停滞** {path}："
                f"已发 {sent}/{total} bytes 后 {time.time() - t0:.0f}s 无进展（{e!r}）"
                "——对端半开，断开连接（不再永久占住 handler 线程）",
                flush=True,
            )
        finally:
            try:
                self.connection.settimeout(None)
            except OSError:
                pass
        if not stalled and total >= SEND_LOG_MIN_BYTES:
            print(
                f"[{time.strftime('%H:%M:%S')}] [hub-server] 响应发送完成 {path} "
                f"{sent} bytes in {time.time() - t0:.1f}s",
                flush=True,
            )

    def _job_id(self) -> str | None:
        """从路径 /jobs/{id}/... 取 job_id；非法 404。"""
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "jobs" and len(parts[1]) > 0:
            return urllib.parse.unquote(parts[1])
        return None

    def _query_course(self) -> str:
        """`?course=` 查询参数（空 = 未给定）。与路径解析同源（urllib.parse）。"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (qs.get("course") or [""])[0].strip()

    # ---- 路由 ----
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/ping" or path == "/":
                if not self._auth_ok():
                    return
                self._json({"status": "ok"}, 200)
            elif path == "/code":
                self._get_shared_code()
            elif path == "/jobs/peek":
                self._get_peek()
            elif path == "/admin/workers/halt":
                self._admin_halt(True, self._query_course())
            elif path == "/admin/workers/resume":
                self._admin_halt(False, self._query_course())
            elif path == "/admin/workers/status":
                self._admin_status()
            elif path == "/admin/queue":
                self._admin_queue()
            elif path == "/admin/push-workers":
                self._admin_push_workers(set_action=False)
            elif path == "/admin/courses":
                self._admin_courses(set_mode=False)
            elif path == "/admin/net-probe":
                self._admin_net_probe()
            elif path == "/admin/offline":
                self._admin_offline()
            elif path == OFFLINE_TASK_PACK_PATH:
                self._get_task_pack()
            elif path == OFFLINE_RESUME_PATH:
                self._get_offline_resume()
            elif path == OFFLINE_RESUME_BLOB_PATH:
                self._get_offline_resume_blob()
            elif path.startswith("/jobs/") and path.endswith("/payload"):
                self._get_payload()
            elif path.startswith("/jobs/") and path.endswith("/ts_code"):
                self._get_ts_code()
            elif path.startswith("/jobs/") and path.endswith("/code"):
                self._get_code()
            elif path.startswith("/jobs/") and path.endswith("/blob"):
                self._get_blob()
            elif path.startswith("/jobs/") and path.endswith("/status"):
                self._get_status()
            elif path.startswith("/jobs/") and path.endswith("/result"):
                self._get_result()
            elif path.startswith("/jobs/") and path.endswith("/resume"):
                self._get_bc_resume()
            elif path.startswith("/jobs/") and path.endswith("/bc-metrics"):
                self._get_bc_metrics()
            else:
                self._json({"error": "not found"}, 404)
        except (ProtocolError, ValueError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # 服务器不因单请求崩溃
            self.log_message("ERROR %s: %s", path, e)
            self._json({"error": "internal"}, 500)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path.startswith("/jobs/") and path.endswith("/heartbeat"):
                self._post_heartbeat()
            elif path == "/jobs/priority":
                self._post_priority()
            elif path.startswith("/jobs/") and path.endswith("/claim"):
                self._post_claim()
            elif path.startswith("/jobs/") and path.endswith("/start"):
                self._post_start()
            elif path.startswith("/jobs/") and path.endswith("/ready"):
                self._post_ready()
            elif path.startswith("/jobs/") and path.endswith("/abandon"):
                self._post_abandon()
            elif path.startswith("/jobs/") and path.endswith("/release"):
                self._post_release()
            elif path.startswith("/jobs/") and path.endswith("/fail"):
                self._post_fail()
            elif path.startswith("/jobs/") and path.endswith("/result"):
                self._post_result()
            elif path.startswith("/jobs/") and path.endswith("/epoch"):
                self._post_bc_epoch()
            elif path == "/admin/unfreeze":
                self._admin_unfreeze()
            elif path == "/admin/courses":
                self._admin_courses(set_mode=True)
            elif path == "/admin/push-workers":
                self._admin_push_workers(set_action=True)
            elif path == "/admin/net-probe":
                self._admin_net_probe_upload()
            elif path == OFFLINE_ARTIFACT_PATH:
                self._post_offline_artifact()
            elif path == OFFLINE_RESULT_PATH:
                self._post_offline_result()
            else:
                self._json({"error": "not found"}, 404)
        except (ProtocolError, ValueError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self.log_message("ERROR %s: %s", path, e)
            self._json({"error": "internal"}, 500)

    # ---- 新调度面（2026-09-22，plan/transfer-scheduling）：peek / priority / claim / start / ready / abandon ----
    def _query_int(self, key: str, default: int) -> int:
        """整数查询参数（非法/缺失 → default）——`?n=` 这类别让写错就 500。"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            return int((qs.get(key) or [str(default)])[0])
        except (TypeError, ValueError):
            return int(default)

    # ---- 路由组的**通用助手**（2026-09-24 S4 第十一刀）：四组混入都只经这几个助手落地，
    # 形状只此一份。混入把它们声明成 `Any`，实现只住这里（组合类提供、混入消费）。
    def _lease_token(self) -> str:
        """租约令牌头（H2）。两种写法都认——历史客户端的大小写不一致。"""
        return self.headers.get("X-Lease-Token", "") or self.headers.get("lease-token", "")

    def _job_or_404(self, *, known: bool = False) -> str | None:
        """鉴权 + 取 `job_id` + 404。返回 None = **已经回过响应**（调用方直接 `return`）。

        `known=True` 还要求 `manifest.json` 存在：未知 job 与写错的 URL 同样是 404——
        分开了它们在排障时看起来像两件事，而它们要的下一步是同一个。
        """
        if not self._auth_ok():
            return None
        jid = self._job_id()
        if jid is None or (known and not (self.hub._job_dir(jid) / "manifest.json").exists()):
            self._json({"error": "not found"}, 404)
            return None
        return jid

    def _job_body(self, cap: int, *, known: bool = False) -> tuple[str, dict] | None:
        """`_job_or_404` + 读小 JSON 体。返回 None = 已回过响应。

        取活/打点那一族（priority / claim / start / ready / abandon）的**共同前缀**：
        「哪一份 job」与「它声称拿着什么」是同一趟请求里的两件事，分写五遍就会漂五遍。
        """
        jid = self._job_or_404(known=known)
        if jid is None:
            return None
        body = self._read_json_body(cap)
        if body is None:
            return None
        return jid, body

    def _serve_path(self, p: Path, *, missing: str) -> None:
        """递一个**已定位**的本地文件；不存在 → 404。

        `missing` 是给排障的**具体**原因（"no code zip" / "no blob" …），不是通用 not found：
        「payload 还没发布」与「这轮没打 ts_code」是两条完全不同的下一步。
        """
        if not p.exists():
            self._json({"error": missing}, 404)
            return
        self._bytes(p.read_bytes())

    def _read_raw_body(self) -> bytes | None:
        """按 `Content-Length` 读满请求体；读不到 → 400 并返回 None。

        与 `_read_capped_body` 的差别：这里**先不判上限**——`_post_fail` / `_post_result` /
        `_post_bc_epoch` 各自有自己的界（`FAIL_BODY_MAX` / 无界 / `BC_EPOCH_BODY_MAX`）或
        自己不做界，所以把「读」与「限」拆开，上限由调用方在那之后判。
        """
        try:
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))
        except Exception as e:
            self._json({"error": f"read body failed: {e}"}, 400)
            return None

    def _worker_id(self) -> str:
        return self.headers.get(WORKER_ID_HEADER, "")

    def _log_claim(self, jid: str, course: str, worker_id: str, mode: str, token: str) -> None:
        """★ 认领可观测（§4.3）：**每次** claim 一行（job/课程/worker/模式/租约/次数）。

        为什么必须每行都有（2026-09-21 事故复盘的原话）：现场重建只能靠「payload served
        ×40」的 cadence 反推认领循环——hub 日志里**没有任何一行**说「谁在什么时候领走了它」。
        新面孔（`/jobs/{id}/claim`）与 peek/priority 共用本函数（R2-6）：换个端点就把这行
        弄丢，等于把事故的取证面弄丢。`reclaims>0` 是「认领后零回传」计数（熔断前兆）。
        """
        n = self.hub.reclaims(jid)
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] claim job={jid} course={course or '-'} "
            f"worker={worker_id or '?'} mode={mode} "
            f"lease={token[:8] if token else 'none'}"
            + (f" reclaims={n}" if n else ""),
            flush=True,
        )

    def _read_json_body(self, cap: int) -> dict | None:
        """读并解析小 JSON 体；不合规 → 400/413 已回，返回 None。"""
        raw = self._read_capped_body(cap)
        if raw is None:
            return None
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError) as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return None
        if not isinstance(body, dict):
            self._json({"error": "体必须是 JSON 对象"}, 400)
            return None
        return body

    @staticmethod
    def _clip(v: object, n: int) -> str:
        """截断成有界字符串（失败体来自远端机器，长度不可信）。非字符串 → 空。"""
        return v[:n] if isinstance(v, str) else ""

    # ---- POST /offline/artifact·/offline/result（产物补传；2026-09-17）----
    def _read_capped_body(self, cap: int) -> bytes | None:
        """读请求体，超 `cap` → 413 并返回 None（**远端体绝不信 Content-Length 之外的任何
        暗示**；超限直接拒，不读进内存）。

        与 `/admin/net-probe` 的差别：那里是「读掉就算了」的探针（可以分块丢弃），这里是
        要解析的 JSON，所以先按声明长度把关再一次性读。
        """
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "Content-Length 非法"}, 400)
            return None
        if n <= 0:
            self._json({"error": "空请求体"}, 400)
            return None
        if n > cap:
            self._json({"error": f"请求体越界（0..{cap}），收到 {n}"}, 413)
            return None
        try:
            raw = self.rfile.read(n)
        except Exception as e:
            self._json({"error": f"read body failed: {e}"}, 400)
            return None
        if len(raw) != n:
            self._json({"error": f"请求体截断（声明 {n}，实收 {len(raw)}）"}, 400)
            return None
        return raw


def as_hub(store_or_hub: _JobStore | _HubQueue) -> _HubQueue:
    """把单个 `_JobStore` 包成单课程队列（幂等）。

    为什么保留这层：`make_server(store, ...)` 是既有测试与 e2e 夹具的**唯一**入口
    （十多个文件直接构造 `_JobStore` 再起 server）。不包装就要改十几个测试，而「多课程」
    本身不需要他们改一行 —— 单课程队列就是 `_HubQueue` 的退化情形，行为逐字节等价。
    """
    if isinstance(store_or_hub, _HubQueue):
        return store_or_hub
    return _HubQueue({"": store_or_hub}, order=[""])


def make_server(
    store_or_hub: _JobStore | _HubQueue,
    port: int,
    token: str,
    host: str = "0.0.0.0",
    *,
    hub: _HubQueue | None = None,
    push: PushDispatcher | None = None,
) -> ThreadingHTTPServer:
    """构造 server（handler 注入调度面 + token）。

    `store_or_hub` 兼容两种：单一 `_JobStore`（自动包成单课程队列，旧调用零改动）或
    已装配好的 `_HubQueue`（多课程；也可用 `hub=` 显式传）。

    host 默认 0.0.0.0（2026-09-16）：Tailscale 直连时云 worker 从 tailnet 入站访问，
    绑 127.0.0.1 会导致对端超时。单测/冒烟需回环时显式传 host="127.0.0.1"。
    """

    class Server(ThreadingHTTPServer):
        def __init__(self) -> None:
            super().__init__((host, port), HubHandler)
            self.token = token

    HubHandler.hub = hub if hub is not None else as_hub(store_or_hub)
    # push 派发器（可选）：只挂上，**不在此处启动** —— 起线程是启动器的动作，
    # 免得每个「只想拿个 server 发请求」的测试都被意外拉起一个后台拍。
    HubHandler.push = push
    return Server()


def main() -> None:
    ap = argparse.ArgumentParser(description="hub-server: remote PPO job queue (stdlib)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--token", default="", help="Bearer token（云 worker 与训练主循环共享）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    # 单课程（旧形状）：job-root/jsonl 直接给。既有测试、既有 dashboard 调用零改动。
    ap.add_argument(
        "--job-root", default="", help="job 目录根（单课程；多课程用 --course + --traj-root）"
    )
    ap.add_argument(
        "--jsonl", default="", help="training_log.jsonl 路径（单课程；多课程自动派生）"
    )
    # 多课程（2026-09-18 用户指令：一个 hub 进程服务所有并行课程）：
    #   --course tiny-a --course x1-rebirth-a2=offline ...
    #   派生 job-root=<traj-root>/<course>/remote-jobs、jsonl=<traj-root>/<course>/training_log.jsonl
    #   —— **磁盘布局与每课程一个 hub 时逐字节相同**，所以控制台/诊断工具/tmp/<course> 约定全照旧。
    ap.add_argument(
        "--course",
        action="append",
        default=[],
        metavar="NAME[=online|offline]",
        help="课程（可重复）。offline = 不实时派发 PPO、只收回传（整段自主的 kind=run 课）",
    )
    ap.add_argument(
        "--traj-root",
        default="tmp",
        help="--course/--discover 时的每课程目录根（相对 cwd 或绝对路径）；缺省 tmp",
    )
    # 共享 hub（2026-09-18 用户指令：hubserver 只开一个进程就同时支持所有并行课程）：
    # 课程表从盘上自动发现——训练侧发布 job 就是"这门课在跑"的事实，不需要第二事实源。
    ap.add_argument(
        "--discover",
        action="store_true",
        help="课程表从 --traj-root 自动发现（扫 <root>/*/{remote-jobs,offline}，新鲜窗口内"
        "自动登记）——新增/结束课程无需重启 hub、无需注册",
    )
    ap.add_argument(
        "--discover-sec",
        type=float,
        default=DISCOVER_SCAN_SEC,
        help=f"自动发现的扫描节拍（秒；缺省 {DISCOVER_SCAN_SEC:g}）",
    )
    ap.add_argument(
        "--lock-file",
        default="",
        help="单实例锁路径（缺省 nn-training/.hub_server.<port>.lock；按端口键控）",
    )
    ap.add_argument(
        "--push",
        action="store_true",
        help="启用 hub 中介 push 派发：按队列顺序把 job 推给登记在册的空闲 GPU worker"
        "（缺省关：不启用时连探活线程都不起，行为与改造前逐字节一致）",
    )
    ap.add_argument(
        "--push-config",
        default="",
        help=f"push worker 登记来源（rl-config 形状；缺省 {DEFAULT_PUSH_CONFIG}）",
    )
    ap.add_argument(
        "--push-poll-sec", type=float, default=PUSH_POLL_SEC, help="派发/结果轮询节拍（秒）"
    )
    ap.add_argument(
        "--push-timeout-sec",
        type=float,
        default=PUSH_TIMEOUT_SEC,
        help="单份 job 推送后的兜底上限（秒）；超时回落队首换 worker",
    )
    args = ap.parse_args()
    token = args.token
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[hub-server] ERROR: 读 --token-file 失败: {e}", flush=True)
            sys.exit(1)
    if not token:
        print("[hub-server] ERROR: 需要 --token 或 --token-file", flush=True)
        sys.exit(1)
    # §单实例锁（2026-09-17，第二道闸）：端口守卫是「探测 → bind」的 TOCTOU —— 两个
    # starter 同时探测会双双通过（Windows 的 SO_REUSEADDR 还允许双绑，后启动者静默
    # 变僵尸）。锁用 O_CREAT|O_EXCL 把启动串行化，且能在**持有者身份可核验**的前提下
    # 自动接管陈旧锁（PID 复用 / 崩溃残留），不再出现「锁在、进程没了、永远启不来」。
    lock_path = args.lock_file or default_instance_lock_path("hub_server", args.port)
    if not acquire_instance_lock(lock_path, marker="hub_server", tag="hub-server"):
        sys.exit(1)
    atexit.register(release_instance_lock, lock_path)
    # §双监听守卫：Windows SO_REUSEADDR 允许双绑同端口（后启动者静默变僵尸）——
    # bind 前探测，端口已有活监听者即拒绝启动（2026-09-09 8787 双实例事故）。
    # 通配地址（0.0.0.0 / :: / ""）没有可连的语义 ⇒ 统一探回环，避免 0.0.0.0 在
    # Windows 上直接 WSAEADDRNOTAVAIL 而让守卫形同虚设。
    _probe_host = args.host if args.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    try:
        ensure_port_free(_probe_host, args.port)
    except RuntimeError as e:
        print(f"[hub-server] ERROR: {e}", flush=True)
        sys.exit(1)
    # ---- 课程表：--course 优先；两者都给 = 响亮拒启（不知道听谁的比听错好）----
    if (args.course or args.discover) and (args.job_root or args.jsonl):
        print(
            "[hub-server] ERROR: --course/--discover 与 --job-root/--jsonl 不能同时给"
            "（前者=多课程，后者=单课程）",
            flush=True,
        )
        sys.exit(1)
    hub: _HubQueue
    traj_root = Path(args.traj_root).resolve()
    if args.course or args.discover:
        specs: dict[str, str] = {}
        for raw in args.course:
            try:
                name, mode = parse_course_arg(raw)
            except ProtocolError as e:
                print(f"[hub-server] ERROR: --course {raw!r}: {e}", flush=True)
                sys.exit(1)
            if name in specs and specs[name] != mode:
                print(
                    f"[hub-server] ERROR: 课程 {name!r} 被重复声明且模式不同（{specs[name]} vs {mode}）",
                    flush=True,
                )
                sys.exit(1)
            specs[name] = mode
        root = traj_root
        stores = {
            name: _JobStore(
                root / name / "remote-jobs",
                root / name / "training_log.jsonl",
            )
            for name in specs
        }
        hub = _HubQueue(
            stores,
            order=list(specs),
            modes=specs,
            discover_root=root if args.discover else None,
        )
        desc = ", ".join(f"{c}:{specs[c]}" for c in specs)
        print(
            f"[hub-server] courses={len(specs)} [{desc}] traj_root={root} "
            f"discover={bool(args.discover)} offline={hub.offline_courses() or '-'}",
            flush=True,
        )
        if args.discover:
            # 后台节拍只是「没人轮询（push 模式 / 无 worker）」时的兜底：pull 路径的
            # `claim_next` 自己会先扫一次（带最小间隔闸），不让新课程等一个节拍。
            def _scan_loop() -> None:
                while True:
                    time.sleep(max(1.0, float(args.discover_sec)))
                    try:
                        hub.discover()
                    except Exception as e:  # 扫描失败不该让调度面死掉
                        print(f"[hub-server] discover 扫描失败: {e}", flush=True)

            Thread(target=_scan_loop, daemon=True, name="hub-discover").start()
    else:
        if not args.job_root or not args.jsonl:
            print(
                "[hub-server] ERROR: 需要 --course/--discover（多课程）或 "
                "--job-root + --jsonl（单课程）",
                flush=True,
            )
            sys.exit(1)
        single = _JobStore(args.job_root, args.jsonl)
        hub = as_hub(single)
    # push 派发（可选，2026-09-18）：登记表来自 rl-config 的 gpu_push 节点（控制台的
    # worker 登记入口回写它），派发器每拍探活 + 按队列顺序推给空闲 worker。
    push_disp: PushDispatcher | None = None
    if args.push:
        push_workers = PushWorkers(
            args.push_config or DEFAULT_PUSH_CONFIG,
            log=lambda m: print(f"[{time.strftime('%H:%M:%S')}] [hub-server push] {m}", flush=True),
        )
        push_workers.reload(force=True)  # 先读一次（缺文件/空表不致命，只是没人可推）
        push_disp = PushDispatcher(
            hub,
            push_workers,
            token,
            poll_sec=args.push_poll_sec,
            timeout_sec=args.push_timeout_sec,
            log=lambda m: print(f"[{time.strftime('%H:%M:%S')}] [hub-server push] {m}", flush=True),
        )
        print(
            f"[hub-server] push 派发已启用：登记表={push_workers.path} "
            f"候选 worker={len(push_workers.snapshot())} "
            f"poll={args.push_poll_sec:g}s timeout={args.push_timeout_sec:g}s",
            flush=True,
        )
    srv = make_server(hub, args.port, token, host=args.host, push=push_disp)
    if push_disp is not None:
        push_disp.start()
    print(
        f"[hub-server] listening on {args.host}:{args.port} "
        f"courses={hub.courses()} claim_ttl={CLAIM_TTL_SEC}s",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if push_disp is not None:
            push_disp.stop()


if __name__ == "__main__":
    main()
