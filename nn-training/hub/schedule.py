"""hub/schedule.py —— 取活 / 租约 / 进度打点面（2026-09-24 S4 第十一刀）。

worker 侧「我该干哪一份、还拿着没有、算到哪了」的全部端点：

    GET  /jobs/peek           候选（不认领、无副作用、兼作 halt 达令）
    POST /jobs/priority       job 边界优先级问询（响应里的 `none` 同时是批量取消信号）
    POST /jobs/{id}/claim     认领（exclusive 设租约 / backup 显式备份副本）
    POST /jobs/{id}/start     打 computing_at（掉队阈值的唯一时基）
    POST /jobs/{id}/ready     算完待回传（P0 小包，**永不**触发取消）
    POST /jobs/{id}/result-ready  worker 说「结果已经在了」——**只叫醒** hub 的等待线程
                                （性能提示；丢了就回落 `PUSH_POLL_SEC` 定时轮询）
    POST /jobs/{id}/abandon   合法放弃（release 租约 + 零 reclaim，幂等）
    POST /jobs/{id}/heartbeat 心跳续租（60s；H2：仅租约持有人）
    POST /jobs/{id}/release   瞬时失败主动还租约（job 立即回池）

## 为什么这一组能整块搬

① 本组方法**零类属性状态**（AST 实测：`self.` 只出现通用助手，不碰 `hub` / `push` / `_blocked_logged`
之外任何实例状态）；② 依赖全向下：只 import `common.protocol`（常量 + `has_offline_capability`）
⇒ 账本秩 **0**，与 `hub.admin` 同层；③ 只往外调通用助手，反向只有派发表调用（组合方向）。

## 为什么 `PEEK_MAX` / `PRIORITY_BODY_MAX` 搬到 `common.protocol`

`PEEK_MAX` 有两个读者（本模块的 `_get_peek` 与 `hub_server._HubQueue.peek_jobs` 的形参默认值），
两边都不能 import 对方（`hub_server` import 本模块拿混入，反向 import 就成环）⇒ 它必须住**两边都
能 import 的协议层**。`PRIORITY_BODY_MAX` 是同一族请求体上限（`FAIL_BODY_MAX` /
`OFFLINE_*_BODY_MAX` 都住那里），一起搬过去免得协议上限分两处定义。
"""

from __future__ import annotations

import json
import time
from email.message import Message
from io import BufferedIOBase
from typing import Any

from common.protocol import (
    AUTH_HEADER,
    CLAIM_MODE_BACKUP,
    CLAIM_MODE_EXCLUSIVE,
    PEEK_MAX,
    PRIORITY_BODY_MAX,
    PRIORITY_LOW,
    ROLE_HEADER,
    ROLE_OFFLINE,
    role_from_header,
)


class ScheduleRoutes:
    """schedule 路由 mixin（`HubHandler(…, ScheduleRoutes, BaseHTTPRequestHandler)`）。"""

    # 由组合类（HubHandler）提供的运行时状态与通用助手——混入不继承 BaseHTTPRequestHandler，
    # 故需自行声明类型（否则 mypy 报 attr-defined）。⚠ 类型必须与 typeshed 逐字一致
    # （`headers: email.message.Message` / `rfile: BufferedIOBase` / `path: str`）：本混入在 MRO 里
    # 早于 BaseHTTPRequestHandler，声明成 Any 会把组合类里 `self.headers.get(...)` 的推断拓成 Any。
    hub: Any
    headers: Message
    path: str
    rfile: BufferedIOBase
    #: hub 中介推的派发器（`--push` 时由 boot 注入；None = 未启用）。本混入只调
    #: `worker_token_ok` / `note_ready` 两个只读/叫醒方法，不碰派发状态。
    push: Any

    # 由组合类提供（实现都在 `hub.server.HubHandler`）——混入只声明类型。
    _auth_ok: Any
    _job_body: Any
    _job_id: Any
    _job_or_404: Any
    _known_job_or_404: Any
    _json: Any
    _lease_token: Any
    _log_claim: Any
    _log_reject: Any
    _query_int: Any
    _read_json_body: Any
    _worker_id: Any
    _watch_worker_source: Any


    def _get_peek(self) -> None:
        """`GET /jobs/peek?n=K` —— **不认领**的候选查询（R1-4）；软持有的候选来源。

        一次行程兼做三件事（都是旧轮询面的附带职责，退役后不能丢）：
        ① 候选列表（无租约、无副作用、**不动 `_cursor`**）；
        ② halt 达令（空轮询也要能感知停机）；
        ③ 登记 worker（R2-2：`active_worker_count()` 是避让链的唯一输入）。
        """
        if not self._auth_ok():
            return
        n = max(1, min(int(self._query_int("n", 3)), PEEK_MAX))
        # 请求方归属（缺头/旧 worker ⇒ online；见 `role_from_header`）。
        role = role_from_header(self.headers.get(ROLE_HEADER, ""))
        # G7 观测（plan/worker-name-readable）：同名换来源 ⇒ 一行告警；只观测、不入账。
        self._watch_worker_source(self._worker_id())
        jobs = self.hub.peek_jobs(
            worker_id=self._worker_id(),
            role=role,
            n=n,
        )
        self._json({"jobs": jobs, "halt": self.hub.all_halted()})


    def _post_priority(self) -> None:
        """`POST /jobs/priority` —— job 边界问询：`{epoch, priorities, reasons}`（§2.3）。

        响应里的 `none` **同时是批量取消信号**：worker 拿它就地丢弃已落盘的本地副本
        （这也是它不能被合并进“claim-with-priority 一次往返”的原因）。
        """
        if not self._auth_ok():
            return
        body = self._read_json_body(PRIORITY_BODY_MAX)
        if body is None:
            return
        held = body.get("held")
        ids = [str(j) for j in held] if isinstance(held, list) else []
        for extra in (body.get("computing"), body.get("ready_upload")):
            if isinstance(extra, str) and extra and extra not in ids:
                ids.append(extra)
        epoch, prios, reasons = self.hub.priority_view(
            worker_id=str(body.get("worker_id") or self._worker_id()),
            job_ids=ids,
        )
        self._json({"epoch": epoch, "priorities": prios, "reasons": reasons})


    def _post_claim(self) -> None:
        """`POST /jobs/{id}/claim` —— 新面（R1-9）；体 `{mode, expected_epoch}`。

        返回：拿得 ⇒ `{lease_token, status, manifest?}`；命中 highest 闸 ⇒
        `{status:"demoted", priority:"low"}`（**不是错误**，worker 按 low 处理）；
        真的轮不到（冻结/避让/未知 job）⇒ 409。
        """
        # 与其余 job 作用域端点同一条形状（鉴权 + 解析 + 404 都在 `_job_body` 里）
        got = self._job_body(PRIORITY_BODY_MAX)
        if got is None:
            return
        jid, body = got
        mode = str(body.get("mode") or CLAIM_MODE_EXCLUSIVE)
        try:
            want_epoch = body.get("expected_epoch")
            want_epoch = None if want_epoch is None else int(want_epoch)
        except (TypeError, ValueError):
            self._json({"error": "expected_epoch 非法"}, 400)
            return
        worker_id = str(body.get("worker_id") or self._worker_id())
        self._watch_worker_source(worker_id)
        role = role_from_header(self.headers.get(ROLE_HEADER, ""))
        out = self.hub.claim_job(
            jid, mode=mode, worker_id=worker_id, expected_epoch=want_epoch, role=role
        )
        course = self.hub.course_of(jid) or ""
        if out.ok:
            self._log_claim(jid, course, worker_id, mode, out.token)
            # 发光的一行：整段（离线盘的活）落到**离线盘**请求者手上——这是「离线活真的在跑」
            # 在 hub 侧的**唯一**痕迹（它不实时派发，也不会被 push 推）。
            # ★ 判据用 **job 自己的 role**，不是课程当前 mode（后者会在热切后撒谎，
            #   而这一行存在的意义就是事后能对上账）。
            if self.hub.job_role(jid) == ROLE_OFFLINE and mode != CLAIM_MODE_BACKUP:
                print(
                    f"[{time.strftime('%H:%M:%S')}] [hub-server] 整段交领："
                    f"course={course or '-'} job={jid} worker={worker_id or '?'}"
                    f"（请求方自称 `{ROLE_HEADER}={role}`）",
                    flush=True,
                )
            # 领取标记（原轮询面也做这件事）：console 据此区分「排队等取」与「已在跑」。
            claim = self.hub._job_dir(jid) / "claimed"
            if not claim.exists():
                try:
                    claim.write_text(str(self.hub._now()), encoding="utf-8")
                except OSError:
                    pass
            resp: dict = {
                "job_id": jid,
                "course": course,
                "status": out.status,
                "lease_token": out.token,
                "halt": self.hub.halt_of(course),
                "epoch": self.hub.epoch_of(jid),
            }
            try:
                mp = self.hub._job_dir(jid) / "manifest.json"
                resp["manifest"] = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            self._json(resp)
            return
        if out.status == "demoted":
            # 「有人比我快」的正常信号：降为低档备份（§2.3 ④），**不得**报错。
            self._json({"job_id": jid, "status": "demoted", "priority": PRIORITY_LOW})
            return
        # 拒绝留痕（2026-09-24 事故）：worker 侧只会看到「HTTP 409」，原因得 hub 自己说。
        # demoted 不算拒绝（上面已 return），故这里只覆盖真拒：held/frozen/unknown/…
        self._log_reject(
            "claim",
            jid,
            str(out.status),
            course=course,
            worker=worker_id,
            reason=str(out.reason),
        )
        self._json({"error": f"claim 被拒: {out.status} ({out.reason})"}, 409)


    def _post_start(self) -> None:
        """`POST /jobs/{id}/start` —— 打 **computing_at**（掉队阈值的唯一时基；R2-C1）。

        ⚠ 不校 `expected_epoch`（R2-C4）：闸只在 claim 一处。
        """
        # `known=True`：start 打点在**已知 job** 上才有意义（未知 job 直接 404，不写一个无归属的时基）
        got = self._job_body(PRIORITY_BODY_MAX, known=True)
        if got is None:
            return
        jid, body = got
        self.hub.start_job(jid, str(body.get("worker_id") or self._worker_id()))
        self._json({"job_id": jid, "status": "computing"})


    def _post_ready(self) -> None:
        """`POST /jobs/{id}/ready` —— 算完待回传/在传（P0 小包，**永不**触发取消）。"""
        got = self._job_body(PRIORITY_BODY_MAX)
        if got is None:
            return
        jid, body = got
        self.hub.set_ready(jid, str(body.get("worker_id") or self._worker_id()))
        self._json({"job_id": jid, "status": "ready"})


    def _post_result_ready(self) -> None:
        """`POST /jobs/{id}/result-ready` —— worker 侧 PPO 出结果后敲的一下（2026-10-26）。

        只做「叫醒 `_await_result` 的等待线程」：它被叫醒后走**既有**的
        `GET /job/{id}/result` 取回，校验/入账仍只有 `accept_result` 一条路。所以：
          · 通知丢了、端点 404/409、worker 根本没配 hub URL —— 只回落 `PUSH_POLL_SEC`
            定时轮询（≤10s），**不改变任何结果**；
          · 不读 body（worker 发的是空体），也不改 job 状态——一个早到/伪造的通知最多
            让 hub 多问一次，看到的仍是 202。
        鉴权刻意不用 hub 管理 token：worker 只持有自己的 push token（`nodes[].authKey`），
        而这个端点能做的只有「叫醒」。
        """
        push = getattr(self, "push", None)
        if push is None:
            # 先答「这台 hub 没开派发器」再验 token：否则 worker 只看到 401，分不清
            # 「token 配错」与「hub 没起 --push」（而两者都只是退回轮询，但排障方向相反）。
            self._json({"error": "push dispatcher not enabled"}, 409)
            return
        if not self._push_auth_ok():
            return
        # 鉴权已过（push token）：这里只要「jid + 已知 job」那半段，形状与 `_job_or_404(known=True)` 同一份。
        jid = self._known_job_or_404()
        if jid is None:
            return
        notified = bool(push.note_ready(jid))
        self._json({"job_id": jid, "notified": notified})

    def _push_auth_ok(self) -> bool:
        """推腿通知的鉴权（Bearer = 任一登记 worker 的 authKey，或派发器缺省 token）。

        刻意**不**走 `_auth_ok`：那条路只认 hub 管理 token，且失败会计入 IP 封禁。
        通知是 best-effort（每个 job 至多一次），一条陈旧的 authKey 不该把整台云机的
        其它请求（注册 / 取包）连坐封掉一小时；token 本身是长随机串，不存在爆破面。
        """
        auth = self.headers.get(AUTH_HEADER, "")
        token = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
        push = getattr(self, "push", None)
        if push is not None and token and push.worker_token_ok(token):
            return True
        self._json({"error": "unauthorized"}, 401)
        return False

    def _post_abandon(self) -> None:
        """`POST /jobs/{id}/abandon` —— 合法放弃（R1-3）：release 租约 + 零 reclaim，幂等。"""
        got = self._job_body(PRIORITY_BODY_MAX)
        if got is None:
            return
        jid, body = got
        self.hub.abandon(jid, str(body.get("worker_id") or self._worker_id()))
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] abandon job={jid} "
            f"course={self.hub.course_of(jid) or '-'} "
            f"worker={body.get('worker_id') or self._worker_id() or '?'} "
            f"reason={str(body.get('reason') or '-')[:80]}",
            flush=True,
        )
        self._json({"job_id": jid, "status": "abandoned"})


    # ---- POST /jobs/{id}/heartbeat ----
    def _post_heartbeat(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        # H2：lease_token 必填且须与原租者一致（否则拒续）
        lease_token = self._lease_token()
        ok = self.hub.heartbeat(jid, lease_token)
        self._json({"job_id": jid, "ok": ok}, 200 if ok else 404)


    # ---- POST /jobs/{id}/release ----
    def _post_release(self) -> None:
        """worker 瞬时失败主动还租约（2026-09-05）：仅租约持有人可释放（H2）。"""
        jid = self._job_or_404(known=True)
        if jid is None:
            return
        lease_token = self._lease_token()
        if self.hub.release(jid, lease_token):
            self._json({"job_id": jid, "status": "released"})
        else:
            self._json({"error": "lease mismatch or absent — 非本 job 租约持有人"}, 403)
