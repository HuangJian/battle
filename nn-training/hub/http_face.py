"""hub/http_face.py — hub 的 **HTTP 面**：来源判定 + `HubHandler`（S4 第十六刀从 `hub/server.py` 搬出）。

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
  POST /jobs/{id}/result-ready  push worker 的结果就绪通知（只叫醒 hub 的等待线程；
                                丢了回落 `PUSH_POLL_SEC` 定时轮询）
  GET  /jobs/{id}/payload       下载 payload zip
  POST /jobs/{id}/heartbeat     心跳续租（60s）
  POST /jobs/{id}/result        worker 回传结果（weights_json + opt_tar + agg）
  GET  /jobs/{id}/status        训练主循环轮询 job 状态（pending/leased/done/failed/frozen/cancelled）
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
  python -m hub.server --port 8787 --token <token> \
      --job-root <traj_root>/remote-jobs --jsonl <traj_root>/training_log.jsonl

监听地址（2026-09-16）：默认 `0.0.0.0`。原因是 Tailscale 内网直连场景下，云 worker
是从 tailnet 侧**入站**访问本 hub（以前 cloudflared 是本机主动外连，绑 127.0.0.1 就够）；
绑回环时 tailnet 根本连不上，表现为对端一直超时。只想听 tailnet 就显式
`--host <本机 Tailscale IP>`；单测/冒烟仍然各自显式传 `host="127.0.0.1"`。
"""

from __future__ import annotations

import base64
import ipaddress
import json
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Literal

from common.http_respond import respond_json
from common.logutil import log_line, tag_logger
from common.protocol import (
    ADMIN_CANCEL_JOB_PATH,
    ADMIN_WORKER_ERROR_PATH,
    ADMIN_WORKER_ERRORS_PATH,
    ADMIN_WORKER_PREFETCH_PATH,
    AUTH_HEADER,
    CAP_REPLAY_CORPUS,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_CLAIM_PATH,
    OFFLINE_HEARTBEAT_PATH,
    OFFLINE_HOLD_PATH,
    OFFLINE_PROGRESS_PATH,
    OFFLINE_RELEASE_PATH,
    OFFLINE_RESULT_PATH,
    OFFLINE_RESUME_BLOB_PATH,
    OFFLINE_RESUME_PATH,
    OFFLINE_TASK_PACK_PATH,
    OFFLINE_TASKS_PATH,
    ROLE_HEADER,
    ROLE_OFFLINE,
    WORKER_ID_HEADER,
    WORKER_SEEN_WINDOW_SEC,
    ProtocolError,
    role_from_header,
)

# 五组路由混入（S4 第十一刀把它们从 `HubHandler` 里拆出来）：本模块的 `HubHandler` 只是
# **组装**它们 + 提供通用助手（`_auth_ok` / `_json` / `_bytes` / `_job_or_404` …）。混入把那批
# 助手声明成 `Any` 并消费，实现只住本模块 —— 这条「实现一份、声明两份」是第十一刀定下的。
from hub.admin import AdminRoutes

# 第 151 行起的「来源判定」只用 `_is_loopback`（闭锁规则的一部分：回环永不计数/永不封禁）。
# `_AuthGuard`（进程级闭锁）不在这里 —— 它被 `hub.store` / `hub.queue_auth` 继承，
# 本模块只经 `self.hub.auth_failure/her` 用它（调度面的方法），不直接碰状态。
from hub.auth import _is_loopback
from hub.blob import BlobRoutes
from hub.offline import OfflineRoutes

# 类属性 `hub: _HubQueue` 要能模块级解析（`from __future__ import annotations` 只推迟求值，
# mypy 仍要那个名字在）⇒ 组合类随本模块一起 import。`hub.queue` 是 L4，本模块据它算 L5。
from hub.queue import _HubQueue
from hub.relay import CHANNEL_STALE_SEC, PING_SERVE_MAX_SEC, PING_TTL_SEC, RelayTimeoutError
from hub.relay import POLL_WAIT_DEFAULT as RELAY_POLL_WAIT
from hub.relay import POLL_WAIT_MAX as RELAY_POLL_WAIT_MAX
from hub.relay import RELAY as NODE_RELAY
from hub.relay import content_type_of as _relay_ctype
from hub.result import ResultRoutes
from hub.schedule import ScheduleRoutes

# worker 静默哨兵（2026-10-31）：纯观测的进程级打点表，零依赖叶子 ⇒ 本模块（L5）在它上面。
from hub.worker_pulse import WorkerPulse

# 类属性 `push: PushDispatcher | None` 同理：注解要在模块级可解析。
# 只 import 这一个名字（`PushWorkers` / `DEFAULT_PUSH_CONFIG` 是引导链的，住 `hub/boot.py`）。
from remote.push_dispatch import PushDispatcher

# ------------------------------------------------------------------ 日志出口

#: 请求面/调度面的行日志：格式与时间戳只走 `common/logutil`（`[HH:MM:SS] [tag] msg`）。
#: 迁移前本文件 8 处手拼 `[{time.strftime(...)}] [hub-server] …` ——输出逐字节不变
#: （`stamp()` 就是 `[HH:MM:SS]`），只是不再有第二份时间戳实现（python.refactor.md §P0-6）。
#: 固定 tag 用 `_log`；**tag 里带请求源**（`hub-server <ip> src=… via=cf`）的行直接调
#: `log_line(<tag>, msg)` —— 动态 tag 不需要再包一层工厂。
_log = tag_logger("hub-server", lambda: time)

# ------------------------------------------------------------------ 来源判定

#: Cloudflare 边缘注入的「真实客户端 IP」头（客户端自带的值由边缘覆写——本方案的**未实测假设**，
#: 实测方法：向隧道发带伪造值的无效鉴权，看本文件的 AUTH FAIL 审计行 src= 显示哪个）。
#: 只认它，不认 `X-Forwarded-For`：后者是**可追加的逗号列表**，取哪一段都是语义游戏。
CF_SOURCE_HEADER = "CF-Connecting-IP"

#: 「读请求体失败」的真失败形态（`_read_raw_body` / `_read_capped_body` 的路面）：
#: 对端断/半开/半截 → `OSError`（`ConnectionResetError` / `TimeoutError` / `socket.timeout`
#: 都是它）；`Content-Length` 不是整数 → `int()` 的 `ValueError`（两个助手都把解析包在
#: 同一个 try 里，所以它也是 400）。**编程错不落这个集**：`AttributeError`（`rfile` 形状变了 /
#: 头部对象被换掉）与 `TypeError` 是我们的 bug，该在日志里炸成 500 而不是声称「客户端发错了」。
_BODY_READ_ERRORS = (OSError, ValueError)

#: 同名 worker「异来源」告警的节流（秒）——两台机器交替轮询时避免每请求一行
#: （plan/worker-name-readable G7；先例 = 本模块 `_blocked_logged` 的每分钟一条）。
WORKER_SOURCE_WARN_THROTTLE_SEC = 60.0

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

#: 「对端提前断开」的日志节流（首条 + 每 N 条一条）。
#:
#: 2026-11-18 DSW 现场：`ERROR /relay/dsw/v1/result: [WinError 10053] An established connection
#: was aborted by the software in your host machine` 一分钟 16 条，全被组件错误注册表当成
#: 「未处理错误」上报。**它不是 hub 的错误** —— 10053/10054 的语义是「这条连接被对端/本机
#: 栈终止」，而 relay 的 `/relay/<node>/v1/result` 会让 hub 等最多 45s 再写回执，调用方自己的
#: timeout 更短时必然出现这一幕。记成 ERROR 有两个害处：真错误被淹、复盘时分不清「链路抖动」
#: 与「hub 崩了」。
CLIENT_GONE_LOG_EVERY = 10

#: relay 模式下**后台刷新** `/v1/ping` 载荷的等待上限（秒）。
#:
#: 它不受调用方预算（3s）约束：这条线程的存在意义只是「让下一次请求命中缓存」，给足一个
#: 长轮询周期才可能成功；而它失败也不影响任何调用方。
RELAY_PING_REFRESH_SEC = 25.0


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

# ------------------------------------------------------------------ 路由表
#
# **表是数据，分派是查表**（python.refactor.md 附录 B 的 `HubHandler` 行）：原先两个端点面各有
# 一条 30 支的顺序 `elif path == … / startswith + endswith` 链，于是「加一个端点」= 往链条
# 中间插一行 —— 插错位置会静默遮蔽后面的同后缀路由，而 stub 掉处理方法的单元用例全绿
# （`test_node_register` 的端到端那条就是为这个形状写的）。现在一条路由 = 一行数据：
# `do_GET` / `do_POST` 只剩「查表 → 调方法」与两条异常边界，表本身可被用例穷举、去重、
# 与 `HubHandler` 对账（`tests/hub/test_hub_route_table.py`）。
#
# **顺序即优先级**：同表内先命中者胜。当前没有任何路径会同时命中两条（用例逐条穷举证明），
# 保留顺序语义是为了让更宽的规则（`job_contains`）永远排在更具体的全等/后缀规则之后。

#: 匹配形态：`exact` = 路径全等 · `job_suffix` = `/jobs/{id}<后缀>` ·
#: `job_contains` = `/jobs/` 前缀且路径含该片段（`epoch-weights` 那类带中缀的）。
RouteKind = Literal["exact", "job_suffix", "job_contains"]


@dataclass(frozen=True, slots=True)
class Route:
    """一行路由：匹配形态 + 形态参数 + `HubHandler` 上的处理方法名。

    `handler` 存**名字**而不是绑定方法/闭包：表因此在任何实例之前就存在（可 import 即断言），
    且「哪个方法服务哪条路径」是数据，不是一张函数对象的间接表。参数绑定的那几条端点
    （`/admin/workers/halt` 之类）各有一个零参包装方法，见 `HubHandler._get_*` / `_post_*`。
    """

    kind: RouteKind
    value: str
    handler: str


def match_route(table: tuple[Route, ...], path: str) -> str | None:
    """按表取处理方法名；一条都不命中 ⇒ `None`（调用方答 404）。纯函数 ⇒ 可穷举测试。"""
    for route in table:
        if route.kind == "exact":
            hit = path == route.value
        elif route.kind == "job_suffix":
            hit = path.startswith("/jobs/") and path.endswith(route.value)
        else:  # job_contains
            hit = path.startswith("/jobs/") and route.value in path
        if hit:
            return route.handler
    return None


#: `GET` 端点表（判定顺序 = 声明顺序）。
GET_ROUTES: tuple[Route, ...] = (
    Route("exact", "/ping", "_get_ping"),
    Route("exact", "/", "_get_ping"),
    Route("exact", "/code", "_get_shared_code"),
    # 采样节点 bootstrap 用的共享 TS 运行时包（与 `/jobs/{id}/ts_code` 同源、同快照）。
    Route("exact", "/ts_code", "_get_shared_ts_code"),
    Route("exact", "/jobs/peek", "_get_peek"),
    Route("exact", "/admin/workers/halt", "_get_workers_halt"),
    Route("exact", "/admin/workers/resume", "_get_workers_resume"),
    Route("exact", "/admin/workers/status", "_admin_status"),
    Route("exact", "/admin/mem", "_admin_mem"),
    Route("exact", "/admin/queue", "_admin_queue"),
    Route("exact", "/admin/push-workers", "_get_admin_push_workers"),
    Route("exact", "/admin/offline-push-workers", "_admin_offline_push_workers"),
    Route("exact", "/admin/nodes", "_admin_nodes_list"),
    Route("exact", "/admin/courses", "_admin_courses"),
    Route("exact", "/admin/net-probe", "_admin_net_probe"),
    # 云机错误上报的读面（plan/component-error-registry §4.3-S3b）：**纯观测**的增量拉取，
    # 与 `/admin/queue`/`/admin/offline` 同规（只读，不碰调度状态）。
    Route("exact", ADMIN_WORKER_ERRORS_PATH, "_admin_errors"),
    Route("exact", "/admin/offline", "_admin_offline"),
    Route("exact", OFFLINE_TASK_PACK_PATH, "_get_task_pack"),
    Route("exact", OFFLINE_TASKS_PATH, "_get_offline_tasks"),
    Route("exact", OFFLINE_HOLD_PATH, "_get_offline_hold"),
    Route("exact", OFFLINE_RESUME_PATH, "_get_offline_resume"),
    Route("exact", OFFLINE_RESUME_BLOB_PATH, "_get_offline_resume_blob"),
    Route("job_suffix", "/payload", "_get_payload"),
    Route("job_suffix", "/ts_code", "_get_ts_code"),
    Route("job_suffix", "/code", "_get_code"),
    Route("job_suffix", "/blob", "_get_blob"),
    Route("job_suffix", "/status", "_get_status"),
    Route("job_suffix", "/result", "_get_result"),
    Route("job_contains", "/epoch-weights/", "_get_bc_epoch_weights"),
    Route("job_suffix", "/resume", "_get_bc_resume"),
    Route("job_suffix", "/bc-metrics", "_get_bc_metrics"),
)

#: `POST` 端点表（判定顺序 = 声明顺序）。
POST_ROUTES: tuple[Route, ...] = (
    Route("job_suffix", "/heartbeat", "_post_heartbeat"),
    Route("exact", "/jobs/priority", "_post_priority"),
    Route("job_suffix", "/claim", "_post_claim"),
    Route("job_suffix", "/start", "_post_start"),
    Route("job_suffix", "/ready", "_post_ready"),
    Route("job_suffix", "/result-ready", "_post_result_ready"),
    Route("job_suffix", "/abandon", "_post_abandon"),
    Route("job_suffix", "/release", "_post_release"),
    Route("job_suffix", "/fail", "_post_fail"),
    Route("job_suffix", "/result", "_post_result"),
    Route("job_suffix", "/epoch", "_post_bc_epoch"),
    Route("exact", "/admin/unfreeze", "_admin_unfreeze"),
    # #8：清空课程级 BC 续训索引（只删别名；权重/历史不动）。
    Route("exact", "/admin/bc-resume/clear", "_admin_clear_bc_resume"),
    # 2026-11-12：人工解除 BC 课程独占（dashboard 按钮）。
    Route("exact", "/admin/bc-exclusive/clear", "_admin_release_bc_exclusive"),
    # 单 job 定向作废（2026-10-11 事故 W7）：与 /admin/unfreeze 同为人工口。
    Route("exact", ADMIN_CANCEL_JOB_PATH, "_admin_cancel_job"),
    Route("exact", "/admin/courses", "_post_admin_courses"),
    Route("exact", "/admin/push-workers", "_post_admin_push_workers"),
    Route("exact", "/admin/nodes/register", "_admin_nodes_register"),
    Route("exact", "/admin/nodes/unregister", "_admin_nodes_unregister"),
    Route("exact", "/admin/net-probe", "_admin_net_probe_upload"),
    Route("exact", OFFLINE_ARTIFACT_PATH, "_post_offline_artifact"),
    Route("exact", OFFLINE_RESULT_PATH, "_post_offline_result"),
    Route("exact", OFFLINE_CLAIM_PATH, "_post_offline_claim"),
    Route("exact", OFFLINE_HEARTBEAT_PATH, "_post_offline_heartbeat"),
    Route("exact", OFFLINE_RELEASE_PATH, "_post_offline_release"),
    Route("exact", OFFLINE_PROGRESS_PATH, "_post_offline_progress"),
    # worker 的预取状态上报（plan/dashboard-ppo-live-rows）：**纯观测**——与 `/jobs/*` 那一族
    # 分开（那一族有租约/游标语义），也不走 `/offline/*`（它报的是 job 队列的软持有，不是离线盘）。
    Route("exact", ADMIN_WORKER_PREFETCH_PATH, "_post_worker_prefetch"),
    # 云机的错误上报（plan/component-error-registry §4.3-S3b，同上形状来源）：报的是「只在云机
    # 发生的失败」（机器级停滞/瞬时失败/离线段失败），与取活协议完全无关 ⇒ 同样刻意开在
    # `/admin/*` 下，失败即丢、绝不参与派发判据。
    Route("exact", ADMIN_WORKER_ERROR_PATH, "_post_worker_error"),
)

# ------------------------------------------------------------------ 进程级状态


@dataclass
class HubState:
    """`HubHandler` 的**进程级可变状态**（`ThreadingHTTPServer` 每请求新建一个 handler 实例
    ⇒ 共享面只能挂在类上；本对象就是那个「只此一份」的家）。

    为什么收成一个对象，而不是四个类属性（python.refactor.md 附录 B 的 `HubState` 行）：

      ① **可读**：写点在代码里长成 `self.state.blocked_logged[ip] = now`，「这是进程级共享状态」
         在调用点就看得见；散成 `self._blocked_logged` 时与实例字段长得一模一样。
      ② **可重置**：用例可以整体换一份干净的（`handler.state = HubState()`），而不是逐表
         `.clear()`——后者漏清一张表不会报错，只会让用例之间偷偷串味。
      ③ **零行为差**：默认实例在 import 期建一次（与类属性的生命周期逐字节等价），
         `hub/boot.py` 的注入面仍然只有 `configure_pulse` / `pulse_events` 两个 classmethod。

    字段都**只由本进程的请求线程写**，不加锁（与类属性时代同规：dict 的单次读/写在 CPython
    下原子；跨字段一致性在这个观测面上不是契约）。
    """

    #: ip -> 上次打印「封禁拒绝」的墙钟（节流：被封客户端高频轮询时每 ip 每分钟一条）
    blocked_logged: dict[str, float] = field(default_factory=dict)
    #: 同名 worker 来源观测（plan/worker-name-readable G7）：worker 名 -> (来源, 末次见到秒)。
    #: 只观测——同名在 `WORKER_SEEN_WINDOW_SEC` 内换了来源 ⇒ 一行告警；**不改名、不入账、
    #: 不分配后缀**。
    seen_sources: dict[str, tuple[str, float]] = field(default_factory=dict)
    #: 上述告警的节流表：worker 名 -> 上次告警墙钟。
    source_warned: dict[str, float] = field(default_factory=dict)
    #: 拒绝日志的 60s 节流表：`"kind:jid:status"` -> 上次告警墙钟。
    reject_warn_at: dict[str, float] = field(default_factory=dict)
    #: ★ 对端提前断开的累计次数（2026-11-18）：只进「不该打 ERROR」那条分支的读数。
    client_gone: dict[str, int] = field(default_factory=lambda: {"n": 0})
    #: ★ worker 静默哨兵（2026-10-31，`hub/worker_pulse.py`）：每个带 `X-Worker-Id` 的请求
    #: `touch()` 一下，后台线程（`hub/boot.py` 的 `hub-worker-pulse`）每 5s 扫一拍，静默
    #: ≥ 阈值且本 episod 有轮询节奏 ⇒ 一条 err 上报进 `note_worker_error` 的 ring。
    pulse: WorkerPulse = field(default_factory=WorkerPulse)


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

    #: ★ **不要**在这里设 `protocol_version = "HTTP/1.1"`（2026-11-19 撤销，事故全文见
    #: `docs/nn/remote-transport.md` §165）。隧道 origin 面（cloudflared → hub）一旦复用连接，
    #: 任何一条被截断/半写（`_send_body` 停滞、头部已发后出错）的响应都会让那条连接**永久**
    #: 失同步：现场 cloudflared 打 `malformed HTTP status code "HTML>"`，此后 20 分钟里
    #: relay 的请求一条都不再到达 origin（节点侧表现为两条路由全 timeout）。1.0 的「每条响应
    #: 后必关」正是这条失同步的天然兜底。relay 的复用收益在**客户端 ↔ Cloudflare edge** 那一段，
    #: origin 说不说 1.1 与它无关 ⇒ 拿稳定性换这点本地回环开销不值。
    hub: _HubQueue = None  # type: ignore[assignment]  # 由 factory 注入
    #: hub 中介 push 派发器（`--push` 时注入；None = 未启用，端点 409）。
    push: PushDispatcher | None = None
    #: 离线 push 派发器（`--offline-push` 时注入；None = 未启用，端点 409）。这里是 `Any`：
    #: 为一个注解去 import L3 的 `hub.offline_push` 会把整条派发器拖进 HTTP 面的 import 图
    #: （探活/网络/线程都在里面），而这里只调它的 `state()` —— 鸭子类型足够。
    offline_push: Any = None

    #: ★ **进程级可变状态**（闭锁节流 / 同名 worker 来源观测 / 静默哨兵）：四份散着的类属性
    #: 现收进 `HubState`（2026-11-19，python.refactor.md 附录 B）。与 `hub` / `push` 同规挂类上
    #: （`ThreadingHTTPServer` 每请求新建实例 ⇒ 类属性才是共享面），但**写点有名字**：
    #: `self.state.blocked_logged[…]`。用例整体换一份干净的即可（`handler.state = HubState()`）。
    state: HubState = HubState()

    @classmethod
    def note_client_gone(cls, path: str, err: BaseException) -> str:
        """对端先走 ⇒ 该打的那一行（`""` = 这一条不打了，只计数）。

        判据不是「异常长得像不像 10053」（那是 Windows 文案），而是**异常类型**：
        `ConnectionError` 一族（`ConnectionAbortedError` 10053 / `ConnectionResetError` 10054 /
        `BrokenPipeError`）的语义统一是「这条连接没了」。对端走了，hub 再回 500 也没人收 ——
        上一版正是在这里写 500，写失败又把 traceback 打进了错误注册表。
        """
        gone = cls.state.client_gone
        gone["n"] = int(gone.get("n", 0)) + 1
        n = int(gone["n"])
        if n == 1 or n % CLIENT_GONE_LOG_EVERY == 0:
            return f"WARN {path}: 对端提前断开（累计 {n} 次，非 hub 错误）：{err}"
        return ""

    # ---- 基础 ----
    @classmethod
    def pulse_events(cls) -> list[dict]:
        """扫一拍 worker 静默哨兵，返回本拍要上发的事件（**只给 `hub/boot.py` 的后台线程调**）。

        为什么留这个 classmethod 而不是让引导链直接摸 `state.pulse`：状态是进程级共享面
        （`ThreadingHTTPServer` 每请求新建实例），但「谁有资格读它」应当是显式的——
        boot 只负责把事件翻译成 `note_worker_error` 的上报体 + 一行日志。
        """
        return cls.state.pulse.sweep()

    @classmethod
    def configure_pulse(cls, *, silence_sec: float) -> None:
        """按启动参数重建哨兵（`--worker-silence-sec`；0 或负数 = 关闭）。

        与 `hub`/`push` 同规：**引导链注入类属性**，`HubHandler` 自己不读 CLI。
        """
        cls.state.pulse = WorkerPulse(silence_sec=silence_sec)

    @classmethod
    def pulse_readout(cls) -> dict:
        """当前哨兵快照（只读；诊断 / 用例用——`pulse_events` 会推进状态，不能拿它看）。"""
        return cls.state.pulse.readout()

    #: 访问日志的写入锁：`print()` 是**两次** `write`（正文 + 换行），`ThreadingHTTPServer`
    #: 的高并发下会交错成 `… 200 -[11:24:05] [hub-server …] "GET …" 200 -` —— 现场
    #: `hub-server.out` 里成片的 relay 行就是这么粘在一起的（那行日志正是排查 relay 问题的
    #: 入口，粘了就解析不出请求/状态）。一次 `write` 一行，句柄只在这里持有。
    _log_lock = threading.Lock()

    def log_message(self, fmt: str, *args: object) -> None:  # 只打非常规事件
        # 静默高频只读访问（/ping 健康检查、/jobs/peek 拉活、result/payload 轮询含 404）
        # ——这些在多 worker 下每秒可打多行，把 hub-server.out 刷爆。POST result、
        # ERROR、/admin、/code 仍保留。
        # 例外：**401/403 永不静默**（2026-09-16 x3-step 事故）——鉴权失败与封禁
        # 恰恰最爱发生在这些高频路径上，静默等于抹掉唯一的破案线索。
        line = fmt % args
        if (
            (
                '"GET /ping ' in line
                or '"GET /jobs/peek' in line
                or ('"GET /jobs/' in line and ("/result " in line or "/payload " in line))
            )
            and " 401 " not in line
            and " 403 " not in line
        ):
            return
        # 隧道来源还原（B，2026-09-17）：回源流量在这里本来全写成 127.0.0.1（x1-rebirth 事故
        # 排查时最大的阻雾），有归因来源就补上——`log_message` 只打非常规事件，不刷屏。
        peer = self.client_address[0]
        # `self.headers` 在请求行都解析失败的早期错误路径上可能是 None（BaseHTTPRequestHandler
        # 先把它置 None 再 parse）——日志绝不能在错误路径上再抛一次异常。
        hdrs = self.headers
        src_ip, via = attributed_source(peer, hdrs.get(CF_SOURCE_HEADER, "") if hdrs else "")
        tag = f"{peer} src={src_ip} via=cf" if via == "cf" else peer
        # **单次 write**（正文+换行一起）：分两次 `print` 会被其它线程插进去，日志行互相粘接。
        with HubHandler._log_lock:
            sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] [hub-server {tag}] {line}\n")
            sys.stdout.flush()

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
        # tag 里带变量（请求源）⇒ 用 `log_line` 直调；`log_line` 的 `[tag]` 位就是本仓的
        # 「多段 tag」面（`hub-server <ip src=… via=cf>`），不必为它再包一层工厂。
        log_line(
            f"hub-server {ip}",
            f"{counter} path={self.path.split('?', 1)[0]} {src}{note}",
        )
        self._json({"error": "unauthorized"}, 401)
        return False

    def _log_blocked(self, ip: str, peer: str = "", via: str = "peer") -> None:
        """封禁命中审计（每 ip 每 60s 一条）：被封客户端往往仍在高频轮询，
        不节流会把日志刷爆，但完全不打则「谁在被封」永远查不到。"""
        now = time.time()
        if now - self.state.blocked_logged.get(ip, 0.0) < 60:
            return
        self.state.blocked_logged[ip] = now
        log_line(
            f"hub-server {ip}",
            "BLOCKED — 请求被拒 "
            f"path={self.path.split('?', 1)[0]} 剩余封禁 "
            f"{int(self.hub.blocked_remaining(ip))}s（5/5 次**无效**鉴权触发；"
            f"合法 token 照常放行——本次请求的 token 不匹配；"
            f"归因: peer={peer} via={via}）",
        )

    # 响应形状（头部顺序 / UTF-8 / ensure_ascii=False / 长度算法）= 线协议的一部分：
    # 与节点侧 worker_server 共用同一个实现（common/http_respond.py）。类属性别名 =
    # 方法绑定语义与普通 def 相同，`self._json(obj, status)` 的所有调用点零改动。
    _json = respond_json

    # ───────────── 响应体：三种喂法，一条传输语义（2026-10-17 L1） ─────────────
    #
    # 旧形状只有 `_bytes(bytes)` 一条路 ⇒ 「递一个盘上的文件」必须先把整个文件
    # `read_bytes()` 成一份等长的 bytes（Range 续传还要再切一份）。一次递送的峰值随文件
    # 大小线性增长，而 CPython/Windows 堆在释放后并不归还 ⇒ hub 连跑 35h 后 RSS 停在
    # 6.2GB（peak 7.7GB），且打 320MB 探针 RSS 零增长（堆里全是空洞，不再向 OS 要也不还）
    # ——见 `plan/hub-server-memory-slim.plan.md` §2。
    #
    # 现在 `_bytes`（内存里的 bytes）/ `_bytes_file`（盘上文件的一段）/ `_bytes_files`
    # （多件分帧）共用 `_send_headers` + `_send_body`：**峰值恒 = 一块**（≤SEND_CHUNK），
    # 与文件/body 总长无关。传输语义（超时 / 停滞日志 / 完成日志 / Accept-Ranges）只有一份。

    def _send_headers(
        self,
        total: int,
        *,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """三种喂法共用的响应头（`Content-Length` 必须先算出来 —— 流式也要先声明总长）。"""
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(total))
        # 「本端点支持 Range 续传」的**声明**（2026-10-12）：`/payload` 的 206 分支靠它让客户端
        # 敢在重抽时带 `Range`。无条件带上——头就几十字节，但没了它客户端只能靠"试一次看回 200
        # 还是 206"来发现，那是把探测塞进数据面。
        self.send_header("Accept-Ranges", "bytes")
        for _k, _v in (extra_headers or {}).items():
            self.send_header(_k, _v)
        if filename:
            # 习惯文件名（下载时手一按就是这个名字，不必再改名）
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()

    def _send_body(
        self,
        chunks: Iterator[bytes | memoryview],
        total: int,
        *,
        path_hint: str = "",
    ) -> None:
        """把一串 ≤SEND_CHUNK 的块写进响应体；**停滞 ≤SEND_TIMEOUT_SEC 就被断掉**。

        2026-09-20 事故（云机领到 job 后几分钟零输出）的 hub 侧半边：`wfile.write()`
        没有超时，对端半开时阻塞**永不返回** ⇒ 客户端永远拿不到 payload，而 `/payload`
        的访问行被高频静默规则吃掉 ⇒ 两端日志同时沉默（唯一的现象是「卡住」）。
        现在：分片写（每片独立超时）⇒ 停滞时**响亮打印**已发字节数；
        ≥SEND_LOG_MIN_BYTES 的 body 完成时也打一行（可对账传输时长/速率）。
        """
        path = path_hint or self.path.split("?", 1)[0]
        sent = 0
        t0 = time.time()
        stalled = False
        try:
            self.connection.settimeout(SEND_TIMEOUT_SEC)
            for chunk in chunks:
                if not chunk:
                    continue
                # `memoryview` 切片是**零复制**（`bytes` 切片会再拷一份 ⇒ 峰值 ×2，
                # 那 512KB 正是本函数在 1MB 递送上第一次被测到的峰值）。
                view = memoryview(chunk)
                off = 0
                left = len(view)
                while left > 0:
                    n = self.wfile.write(view[off : off + left])
                    if n is None:  # 缓冲写（wbufsize > 0）：视作整片已收
                        n = left
                    if n <= 0:  # 0 = 对端不再接收（半开）——不能空转
                        raise TimeoutError("write 返回 0——对端不再接收")
                    off += n
                    left -= n
                    sent += n
        except OSError as e:
            stalled = True
            _log(
                f"响应发送**停滞** {path}："
                f"已发 {sent}/{total} bytes 后 {time.time() - t0:.0f}s 无进展（{e!r}）"
                "——对端半开，断开连接（不再永久占住 handler 线程）"
            )
        finally:
            try:
                self.connection.settimeout(None)
            except OSError:
                pass
        if not stalled and total >= SEND_LOG_MIN_BYTES:
            _log(f"响应发送完成 {path} {sent} bytes in {time.time() - t0:.1f}s")

    def _bytes(
        self,
        data: bytes,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """发送**已在内存里**的整块 body（小 JSON / 探针填充这类；大文件走 `_bytes_file`）。

        分片只是为了让 `_send_body` 的超时**每片**生效（一次大 write 只有整体超时），
        不解决"这份 bytes 已经占着内存"的问题——那个问题由 `_bytes_file` 从源头避免。
        """
        total = len(data)
        self._send_headers(
            total, status=status, ctype=ctype, filename=filename, extra_headers=extra_headers
        )
        view = memoryview(data)
        self._send_body(
            (view[i : i + SEND_CHUNK] for i in range(0, total, SEND_CHUNK)),
            total,
        )

    def _bytes_file(
        self,
        path: Path,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
        *,
        extra_headers: dict[str, str] | None = None,
        start: int = 0,
        end: int | None = None,
    ) -> None:
        """递盘上文件的 `[start, end]`（**含两端**；`end=None` = 到文件尾），**边读边写**。

        为什么必须有它：任务包 / payload / blob 都是几十 MB 量级，旧形状每递一次就要一份
        等长的 bytes（Range 续传还要再切一份 ⇒ 峰值 ×2），那正是 hub 35h 6.2GB 的机制。
        现在峰值 = SEND_CHUNK，与文件大小**无关**。
        """
        size = path.stat().st_size
        lo = max(0, int(start))
        hi = size - 1 if end is None else min(int(end), size - 1)
        total = max(0, hi - lo + 1)
        self._send_headers(
            total, status=status, ctype=ctype, filename=filename, extra_headers=extra_headers
        )

        def chunks() -> Iterator[memoryview]:
            if total <= 0:
                return
            # ★ 一块**复用到底**（`bytearray` + `readinto`）：若每块 `f.read()` 一个新 bytes，
            # 生成器 yield 之后旧块还被循环变量持有 ⇒ 新旧两块同时活着，峰值恒 = 2×SEND_CHUNK。
            buf = bytearray(SEND_CHUNK)
            view = memoryview(buf)
            with open(path, "rb", buffering=0) as f:
                f.seek(lo)
                left = total
                while left > 0:
                    n = f.readinto(view[: min(SEND_CHUNK, left)])
                    if not n:
                        break  # 文件在统计与读取之间被截短：按实发计（Content-Length 已声明）
                    left -= n
                    yield view[:n]

        self._send_body(chunks(), total)

    def _bytes_files(
        self,
        entries: list[tuple[str, Path]],
        *,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """递**多件**文件的分帧体（`name\\nlen\\n` + raw），逐件流式；峰值 = 一块。

        blob 批量端点（`?name=init,ref,opt`）用：旧实现先把每件 `read_bytes()` 再
        `b"".join(parts)` ⇒ 峰值 ≈ 2× 三件之和，现在只有 SEND_CHUNK。
        """
        sizes: list[int] = []
        total = 0
        for name, p in entries:
            sz = p.stat().st_size
            sizes.append(sz)
            total += len(name.encode("utf-8")) + 1 + len(str(sz).encode("ascii")) + 1 + sz
        self._send_headers(
            total, status=status, ctype=ctype, filename=filename, extra_headers=extra_headers
        )

        buf = bytearray(SEND_CHUNK)
        view = memoryview(buf)

        def chunks() -> Iterator[bytes | memoryview]:
            for (name, p), _sz in zip(entries, sizes, strict=True):
                yield name.encode("utf-8") + b"\n" + str(_sz).encode("ascii") + b"\n"
                with open(p, "rb", buffering=0) as f:
                    while True:
                        n = f.readinto(view)
                        if not n:
                            break
                        yield view[:n]

        self._send_body(chunks(), total)

    def _job_id(self) -> str | None:
        """从路径 /jobs/{id}/... 取 job_id；非法 404。"""
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "jobs" and len(parts[1]) > 0:
            return urllib.parse.unquote(parts[1])
        return None

    def _note_offline_disk(self) -> None:
        """离线**盘**报名（`/offline/*` 面）：带 `X-Battle-Offline` 的请求 = 这块盘自报身份。

        ★ 为什么必须在这里（2026-09-25，plan §7.0.1 #2 / §7.2.3）：跑 `battle.offline.ipynb`
        的云机**不碰 `/jobs/*`**（它走清单 + 取包 + 租约 + 补传），而角色头此前只在 worker 的
        peek/claim 面上被读 ⇒ 离线盘在 hub 眼里是匿名的，「有没有离线盘」这个读数恒为空。
        身份取 `?worker=`（租约面自带）；清单/取包不带 worker ⇒ 记成 `<offline>` 一个人次。
        """
        if role_from_header(self.headers.get(ROLE_HEADER, "")) != ROLE_OFFLINE:
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.hub.note_offline_disk((qs.get("worker") or [""])[0])

    def _query_course(self) -> str:
        """`?course=` 查询参数（空 = 未给定）。与路径解析同源（urllib.parse）。"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (qs.get("course") or [""])[0].strip()

    def _query_str(self, key: str) -> str:
        """任意 query 参数（空 = 未给定）。★ 2026-10-23：`?parts=`（补传按件分流）用它。"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (qs.get(key) or [""])[0].strip()

    # ---- 路由参数的绑定点 ----
    # 表只存「方法名」（纯数据）；带参数的那几条端点在这里把参数钉住，于是表里没有闭包、
    # 也没有 `getattr(self, name)(*args)` 这种「参数住在数据里的」间接面。
    def _get_ping(self) -> None:
        """/ping 与 / 的健康应答（鉴权闸先过——它是最早的存活探针，token 不对就不该报 ok）。"""
        if not self._auth_ok():
            return
        # 能力位（2026-11-16，plan/replay-corpus.plan.md D2/B7）：hub 自己声明支持
        # replay 语料（claim 闸 + 转换器合同已在本进程）。additive；旧客户端忽略。
        self._json({"status": "ok", "capabilities": [CAP_REPLAY_CORPUS]}, 200)

    def _get_workers_halt(self) -> None:
        self._admin_halt(True, self._query_course())

    def _get_workers_resume(self) -> None:
        self._admin_halt(False, self._query_course())

    def _get_admin_push_workers(self) -> None:
        self._admin_push_workers(set_action=False)

    def _post_admin_courses(self) -> None:
        self._admin_courses(post=True)

    def _post_admin_push_workers(self) -> None:
        self._admin_push_workers(set_action=True)

    def _post_offline_claim(self) -> None:
        self._post_offline_lease("claim")

    def _post_offline_heartbeat(self) -> None:
        self._post_offline_lease("heartbeat")

    def _post_offline_release(self) -> None:
        self._post_offline_lease("release")

    # ---- 路由 ----
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        self.state.pulse.touch(self._worker_id())
        if path.startswith("/offline/"):
            # 离线盘报名（取包链的全部端点都在这条前缀下；见 `_note_offline_disk`）。
            self._note_offline_disk()
        try:
            if path.startswith("/relay/"):
                self._relay_route(method="GET")
                return
            name = match_route(GET_ROUTES, path)
            if name is None:
                self._json({"error": "not found"}, 404)
            else:
                dispatch: Callable[[], None] = getattr(self, name)
                dispatch()
        except (ProtocolError, ValueError) as e:
            self._json({"error": str(e)}, 400)
        except ConnectionError as e:
            # 对端提前断开（10053/10054/EPIPE）：**不是** hub 出错，也不回 500 ——
            # 那条 socket 已经死了，写只会再抛一次同样的事（而那一次的 traceback 才是
            # dashboard 里那条「未处理错误」）。计数 + 节流一行 WARN，见 `note_client_gone`。
            note = self.note_client_gone(path, e)
            if note:
                self.log_message("%s", note)
        except Exception as e:  # 服务器不因单请求崩溃
            self.log_message("ERROR %s: %s", path, e)
            self._json({"error": "internal"}, 500)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        self.state.pulse.touch(self._worker_id())
        if path.startswith("/offline/"):
            # 同上：租约/补传也在这条前缀下（POST 面）。
            self._note_offline_disk()
        try:
            if path.startswith("/relay/"):
                self._relay_route(method="POST")
                return
            name = match_route(POST_ROUTES, path)
            if name is None:
                self._json({"error": "not found"}, 404)
            else:
                dispatch: Callable[[], None] = getattr(self, name)
                dispatch()
        except (ProtocolError, ValueError) as e:
            self._json({"error": str(e)}, 400)
        except ConnectionError as e:
            # 同 `do_GET`：对端先走不是 hub 的错误。relay 的回执就写在这条分支上 ——
            # `/relay/<node>/v1/result` 等 45s 才写，调用方 timeout 更短时必然撞上。
            note = self.note_client_gone(path, e)
            if note:
                self.log_message("%s", note)
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

    def _known_job_or_404(self) -> str | None:
        """取 `job_id` + 要求 `manifest.json` 已知，否则 404。None = 已回过响应。

        鉴权**不**在这里做：`POST /jobs/{id}/result-ready` 认的是 worker 的 push token
        而不是 hub 管理 token（`hub/schedule.py::_push_auth_ok`），与 `_job_or_404` 的
        `_auth_ok` 是两条路；但「jid 写错」与「job 不存在」同答 404 的形状只留这一份。
        """
        jid = self._job_id()
        if jid is None or not (self.hub._job_dir(jid) / "manifest.json").exists():
            self._json({"error": "not found"}, 404)
            return None
        return jid

    def _job_or_404(self, *, known: bool = False) -> str | None:
        """鉴权 + 取 `job_id` + 404。返回 None = **已经回过响应**（调用方直接 `return`）。

        `known=True` 还要求 `manifest.json` 存在：未知 job 与写错的 URL 同样是 404——
        分开了它们在排障时看起来像两件事，而它们要的下一步是同一个。
        """
        if not self._auth_ok():
            return None
        if known:
            return self._known_job_or_404()
        jid = self._job_id()
        if jid is None:
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
        # 流式（2026-10-17 L1）：旧实现 `read_bytes()` 整份入内存（code/ts_code/blob 都走这里）。
        self._bytes_file(p)

    def _read_raw_body(self) -> bytes | None:
        """按 `Content-Length` 读满请求体；读不到 → 400 并返回 None。

        与 `_read_capped_body` 的差别：这里**先不判上限**——`_post_fail` / `_post_result` /
        `_post_bc_epoch` 各自有自己的界（`FAIL_BODY_MAX` / 无界 / `BC_EPOCH_BODY_MAX`）或
        自己不做界，所以把「读」与「限」拆开，上限由调用方在那之后判。
        """
        try:
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))
        except _BODY_READ_ERRORS as e:
            self._json({"error": f"read body failed: {e}"}, 400)
            return None

    # ───────────── relay：只能出网（无入站隧道）的节点的反向会合面 ─────────────
    # 协议与「为什么需要」见 `hub/relay.py` 头部；这里只做 HTTP 形状：
    #   * `_poll` / `_reply` 是**节点侧**（Bearer = HUB_TOKEN，与其余端点同一把锁）；
    #   * 其余路径是**调用方侧**（trainer / eval / dashboard，全部与 hub 同机）⇒ 只接受
    #     本机回环，且**绝不放行经隧道的流量**：cloudflared 回源的对端也是 127.0.0.1，靠
    #     `attributed_source` 的 `via == "peer"` 区分；否则公网上任何人知道 node id 就能
    #     借 hub 打本机。
    #: 调用方等待上限**按路径**取（与调用方自己的 urllib 预算同量级；比它短会让健康的慢请求
    #: 变成 504，比它长则节点掉线时白占一个 handler 线程）。
    _RELAY_TIMEOUTS: tuple[tuple[str, float], ...] = (
        ("/v1/result", 45.0),
        ("/v1/task", 45.0),
        ("/v1/weights", 150.0),
    )

    def _relay_route(self, *, method: str) -> None:
        raw_path = self.path
        path = raw_path.split("?", 1)[0]
        node, _, tail = path[len("/relay/") :].partition("/")
        if not node or not tail:
            self._json({"error": "not found"}, 404)
            return
        if tail == "_poll":
            if not self._auth_ok():
                return
            wait = self._query_float("wait", RELAY_POLL_WAIT)
            req = NODE_RELAY.poll(node, min(max(wait, 0.0), RELAY_POLL_WAIT_MAX))
            # `local_port` 是「注册 URL 该填哪个端口」的唯一来源：节点只知道 hub 的**公网**
            # 地址，而 trainer 要打的是**本机回环**（两者端口可以不同）。
            port = int(getattr(self.server, "server_address", ("", 0))[1])
            self._json({"req": req, "local_port": port}, 200)
            return
        if tail.startswith("_reply/"):
            if not self._auth_ok():
                return
            rid = tail[len("_reply/") :]
            raw = self._read_raw_body()
            if raw is None:
                return
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError as e:
                self._json({"error": f"bad json: {e}"}, 400)
                return
            if not isinstance(body, dict):
                self._json({"error": "体必须是对象"}, 400)
                return
            try:
                payload = base64.b64decode(str(body.get("body_b64") or ""), validate=True)
            except (ValueError, TypeError):
                self._json({"error": "body_b64 非法"}, 400)
                return
            ok = NODE_RELAY.reply(
                node, rid, int(body.get("status") or 0), dict(body.get("headers") or {}), payload
            )
            self._json({"ok": ok}, 200)
            return
        # —— 调用方侧（本机回环）——
        peer = self.client_address[0]
        _ip, via = attributed_source(
            peer, self.headers.get(CF_SOURCE_HEADER, "") if self.headers else ""
        )
        if via != "peer" or not _is_loopback(peer):
            self._json({"error": "relay 调用面只接受本机回环（trainer 与 hub 同机）"}, 403)
            return
        # 探活短路：`/v1/ping` 拿「通道心跳 + 上次载荷」答，不等往返（见 `_serve_relay_ping`）。
        # 位置有讲究：必须在回环闸之后（否则公网能拿它当探测器）、在 `call` 之前（否则又等一轮）。
        if method == "GET" and tail == "v1/ping" and self._serve_relay_ping(node):
            return
        raw = b""
        if method != "GET":
            raw = self._read_raw_body()
            if raw is None:
                return
        target = "/" + tail + (("?" + raw_path.split("?", 1)[1]) if "?" in raw_path else "")
        timeout = next((t for pfx, t in self._RELAY_TIMEOUTS if target.startswith(pfx)), 15.0)
        try:
            status, rhdrs, body = NODE_RELAY.call(
                node, method, target, dict(self.headers.items()), raw, timeout=timeout
            )
        except RelayTimeoutError as e:
            self._json({"error": f"relay timeout: {e}"}, 504)
            return
        if tail == "v1/ping" and status == 200:
            # 回落路径也得**把载荷存下来**：否则缓存永远空 ⇒ 每次都走真调用，短路白写。
            self._cache_relay_ping(node, body)
        self._send_headers(len(body), status=status or 502, ctype=_relay_ctype(rhdrs))
        if body:
            self.wfile.write(body)

    @staticmethod
    def _cache_relay_ping(node: str, raw: bytes) -> bool:
        """把一次（真调用或后台刷新拿到的）`/v1/ping` 载荷记进缓存；`False` = 这体不能用。"""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        NODE_RELAY.note_ping(node, payload)
        return True

    def _serve_relay_ping(self, node: str) -> bool:
        """`/v1/ping` 用**通道心跳 + 上次载荷**回答；`False` = 这次答不了，照旧走真调用。

        治什么（2026-11-18 DSW 稳定性）：hub 每轮派发都要 ping（调用方预算 3s），而 relay
        一次调用得等节点下一次长轮询（≤20s）⇒ 约一半探活**假失败**（节点被当成「这轮够不着」），
        同时那个 handler 线程空占到 15s 才放手。今夜 hub 日志：`/relay/dsw/v1/ping`
        186 ok / 192 ×「对端先走」/ 67 ×504 —— 全是同一个结构性问题，不是隧道坏。

        判据换成**本地事实**：节点最近一次 `_poll` 有多久（= 通道活着）+ 上一次真 ping 的载荷。
        过期载荷照用、**后台刷一次**（单飞行），于是稳态下 ping 零往返、零超时；
        载荷比 `PING_SERVE_MAX_SEC` 还旧就老实走真调用（不拿化石台账骗派发器）。
        """
        age = NODE_RELAY.channel_age(node)
        if age is not None and age > CHANNEL_STALE_SEC:
            # 通道确实断了（节点不再轮询）⇒ **快速失败**，别让调用方等 15s 才拿到同样的结论
            self._json({"error": f"relay 通道心跳过期（{age:.0f}s 未轮询）"}, 503)
            return True
        body, cache_age = NODE_RELAY.ping_snapshot(node)
        if body is None or cache_age > PING_SERVE_MAX_SEC:
            return False
        body["relayAgeSec"] = round(cache_age, 1)
        self._json(body, 200)
        if cache_age > PING_TTL_SEC and NODE_RELAY.claim_ping_refresh(node):
            threading.Thread(
                target=self._refresh_relay_ping,
                args=(node, dict(self.headers.items())),
                daemon=True,
                name=f"relay-ping-refresh:{node}",
            ).start()
        return True

    def _refresh_relay_ping(self, node: str, headers: dict[str, str]) -> None:
        """后台把过期载荷刷一遍：**不阻塞任何调用方**，成败都由自己放回单飞行。"""
        try:
            status, _hdrs, raw = NODE_RELAY.call(
                node, "GET", "/v1/ping", headers, b"", timeout=RELAY_PING_REFRESH_SEC
            )
            if status == 200:
                self._cache_relay_ping(node, raw)
        except RelayTimeoutError:
            # 契约静默（窄捕，非旧债）：ping 刷新是**旁路**，失败只会让下一次 ping
            # 多问一跳；这里没有任何调用方在等它。程序员错穿出去（线程 traceback 可见）。
            pass
        finally:
            NODE_RELAY.release_ping_refresh(node)

    def _query_float(self, key: str, default: float) -> float:
        """查询参数取浮点；缺失/非法 ⇒ 缺省（`_poll` 的 wait 是唯一读者）。"""
        try:
            return float(self._query_str(key) or default)
        except ValueError:
            return default

    def _worker_id(self) -> str:
        return self.headers.get(WORKER_ID_HEADER, "")

    def _watch_worker_source(self, worker_id: str) -> None:
        """★ 观测守卫（plan/worker-name-readable G7）：同名 + 异来源 ⇒ 一行告警（节流）。

        前提（用户裁决 ⑤）= 同一环境不会同时有两台云机 ⇒ `{env}-{link}` 天然唯一。前提一旦
        被打破，hub 的 `_workers` 会把两台互相覆盖（避让链/贡献度面板合并成一行 = 假数）而
        **没有任何痕迹**——本函数就是那一条痕迹。**只观测**：不改名、不入账、不分配后缀。

        来源 = 既有归因 `attributed_source`（回环对端 + `CF-Connecting-IP`；直连只认 TCP
        对端）。已知盲区：两台**本机** worker（`local`）来源都是 127.0.0.1 ⇒ 静默
        （记档在 plan O4；`_workers` 合并还会让 `active_worker_count()` 读 1）。
        """
        wid = (worker_id or "").strip()
        if not wid:
            return
        hdrs = self.headers
        src, _via = attributed_source(
            self.client_address[0], hdrs.get(CF_SOURCE_HEADER, "") if hdrs else ""
        )
        now = time.time()
        prev = self.state.seen_sources.get(wid)
        self.state.seen_sources[wid] = (src, now)
        if not prev or prev[0] == src or now - prev[1] > WORKER_SEEN_WINDOW_SEC:
            return
        last = self.state.source_warned.get(wid, 0.0)
        if now - last < WORKER_SOURCE_WARN_THROTTLE_SEC:
            return
        self.state.source_warned[wid] = now
        _log(
            f"⚠ worker 名 {wid} 在 "
            f"{WORKER_SEEN_WINDOW_SEC:.0f}s 窗口内换了来源（{prev[0]} → {src}）——"
            "「同环境单台」前提可能被打破（只观测，不改名/不入账）"
        )

    def _log_claim(self, jid: str, course: str, worker_id: str, mode: str, token: str) -> None:
        """★ 认领可观测（§4.3）：**每次** claim 一行（job/课程/worker/模式/租约/次数）。

        为什么必须每行都有（2026-09-21 事故复盘的原话）：现场重建只能靠「payload served
        ×40」的 cadence 反推认领循环——hub 日志里**没有任何一行**说「谁在什么时候领走了它」。
        新面孔（`/jobs/{id}/claim`）与 peek/priority 共用本函数（R2-6）：换个端点就把这行
        弄丢，等于把事故的取证面弄丢。`reclaims>0` 是「认领后零回传」计数（熔断前兆）。
        """
        n = self.hub.reclaims(jid)
        _log(
            f"claim job={jid} course={course or '-'} "
            f"worker={worker_id or '?'} mode={mode} "
            f"lease={token[:8] if token else 'none'}"
            + (f" reclaims={n}" if n else "")
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
        except _BODY_READ_ERRORS as e:
            self._json({"error": f"read body failed: {e}"}, 400)
            return None
        if len(raw) != n:
            self._json({"error": f"请求体截断（声明 {n}，实收 {len(raw)}）"}, 400)
            return None
        return raw

    def _log_reject(
        self,
        kind: str,
        job_id: str,
        status: str,
        *,
        course: str = "",
        worker: str = "",
        reason: str = "",
    ) -> None:
        """hub 侧「为什么拒了这份活」的唯一打点（2026-09-24 事故）。

        现场那次 409 在 worker 日志里被兜底文案写成「hub 异常，请检查 hub 进程与隧道」，
        而真因是身份歧义导致的跨课程路由 ⇒ hub 侧必须留下**自己那一半**的证词：
        谁、哪门课、什么状态、什么原因。按 (kind, jid, status) 60s 节流——同一个 worker
        在租约期内会反复撞同一道闸，逐次打点会把日志刷爆。
        """
        now = time.time()
        key = f"{kind}:{job_id}:{status}"
        if now - self.state.reject_warn_at.get(key, 0.0) <= 60:
            return
        self.state.reject_warn_at[key] = now
        _log(
            f"{kind} 被拒: job={job_id} "
            f"course={course or '-'} worker={worker or '?'} status={status} "
            f"reason={reason or '-'}"
        )
