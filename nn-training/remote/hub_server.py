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
import ipaddress
import json
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from common.protocol import (
    AUTH_HEADER,
    CLAIM_TTL_SEC,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_RESULT_PATH,
    OFFLINE_RESUME_BLOB_PATH,
    OFFLINE_RESUME_PATH,
    OFFLINE_TASK_PACK_PATH,
    PUSH_POLL_SEC,
    PUSH_TIMEOUT_SEC,
    WORKER_ID_HEADER,
    ProtocolError,
    parse_course_arg,
)
from remote._instance_lock import (
    acquire_instance_lock,
    default_instance_lock_path,
    release_instance_lock,
)
from remote._port_guard import ensure_port_free

# ── 状态类 / 调度面 / 鉴权原语：S4 第十四·十五刀把它们**全部下沉**到 `remote/hub/`。
#
# 本模块从此只做两件事：**组装**（`as_hub` / `make_server` / `main`）与 **HTTP 面**
# （`HubHandler`）。下面几个名字是本模块**对外**的取名字入口（约 20 个测试、`e2e/`、
# `remote/smoke_loopback.py`、`remote/tunnel_ab_probe.py` 从这里取），所以走自别名 re-export
# —— **名字是契约，位置不是**。
#
# 反向禁止：`remote/hub/*` **不得** import 本模块（否则与「本模块 import 它们」成环，而本仓
# 的环只有「下沉共同依赖」与「参数注入」两种处理方式，`DEFERRED_CYCLES` 至今为空）：
#   * `_AuthGuard`（进程级鉴权闭锁：一个 IP 的失败计数不按课程各算一套）与 `_is_loopback`
#     （闭锁规则的一部分：回环永不计数/永不封禁）→ `hub/auth.py`；
#   * `_JobStore` 组合类 → `hub/store.py`（六个域混入在 `hub/store_*.py`）；
#   * `_HubQueue` 组合类 → `hub/queue.py`（七个域混入在 `hub/queue_*.py`）。
# 两次搬迁都是**逐字节等价**的纯搬（同一把 `_lock`、同一个 `self`）⇒ `hub._stores[...]` /
# `store._leases[...]` 这类直读、`patch remote.hub_server.*` 全部照旧。
from remote.hub.admin import AdminRoutes

# 鉴权原语（S4 第十五刀）：`_AuthGuard` 是**进程级**的一份（一个 IP 的失败计数不按课程
# 各算一套），`_HubQueue` 与 `_JobStore` 都继承它。`_is_loopback` 是闭锁规则的一部分
# （回环永不计数/永不封禁），`attributed_source` 与 handler 都用它。
# 两个名字都走自别名 re-export：`remote.hub_server` 是既有取名字的入口（名字是契约）。
from remote.hub.auth import _AuthGuard as _AuthGuard
from remote.hub.auth import _is_loopback
from remote.hub.blob import BlobRoutes
from remote.hub.offline import OfflineRoutes

# 多课程调度面（S4 第十五刀）：`_HubQueue` 1033 行 / 76 方法拆成七个域混入
# （`hub/queue_*.py`），组合类住 `hub/queue.py`。**同一把 `_lock`、同一个 `self`** ——
# 所以 `hub._stores[...]` / `hub._halts` 这类直读、以及测试里的 `patch remote.hub_server.*`
# 全部照旧（自别名 re-export 保住取名字的入口）。
from remote.hub.queue import _HubQueue as _HubQueue
from remote.hub.result import ResultRoutes
from remote.hub.schedule import ScheduleRoutes

# 组合类（S4 第十五刀）：第十四刀把六个域混入拆到 `hub/store_*.py`，本刀把组合类本身也
# 搬出来 —— 课程表域要**构造** store、`_store_of` 要**注解**它，而 `remote/hub/*` 不得
# import 本模块（成环）。同样自别名 re-export（`hs._JobStore` 是既有入口）。
from remote.hub.store import _JobStore as _JobStore
from remote.hub.store_leases import (
    FREEZE_AFTER_RECLAIMS as FREEZE_AFTER_RECLAIMS,  # 测试从这里取（名字是契约，位置不是）
)
from remote.hub.store_leases import (
    ClaimOutcome as ClaimOutcome,  # 同上：测试拿 `hs.ClaimOutcome` 与 store_leases 对账
)

# HTTP 路由组（S4 第十一刀）：四组域混入，派发表（do_GET / do_POST，仍在 HubHandler）调它们。
# 它们只向外调通用助手（`_auth_ok` / `_json` / `_bytes` / `_job_id` / `_read_json_body` …），
# 那些助手仍住本模块 —— 混入把它们声明成 `Any`（同 `hub.admin` 的先例）。
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



# ------------------------------------------------- 多课程调度面（常量）
#
# 调度面本体（`_HubQueue` 组合类 + 七个域混入）在 S4 第十五刀搬到了 `remote/hub/queue.py`
# 与 `remote/hub/queue_*.py`；`_MISSING_ROOT` 随 `_job_dir` 进了 `queue_observe`。
# 本模块只留**启动参数**用的常量。`_HubQueue` 走自别名 re-export
# （`hs._HubQueue` 是约 10 个测试与 e2e 的取名字入口——名字是契约，位置不是）。

#: 自动发现（`--discover`）的扫描节拍（秒）。派发热路径也会顺带扫（有最小间隔闸），
#: 这里只是「没人轮询时」的兜底：后台线程按这个节拍把新课程登记进来。
DISCOVER_SCAN_SEC = 5.0




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
