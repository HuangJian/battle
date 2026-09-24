"""remote/hub/result.py —— 回传与终局面（2026-09-24 S4 第十一刀）。

「这一份跑完了吗、成不成、为什么不成」的全部端点：

    POST /jobs/{id}/result   回传结果（weights_json + opt_tar + agg；v2 体按魔数自动识别）
    POST /jobs/{id}/fail     节点**确定性**失败回报（bun 装不上 / TS 取不到 / argv 非法）
    GET  /jobs/{id}/status   训练主循环轮询状态（pending/leased/done/failed/frozen）
    GET  /jobs/{id}/result   取已落盘结果做三重校验（已失败 → 410 + 原因）
    POST /jobs/{id}/epoch    BC 每 epoch 回传（权重 resume + 指标行）
    GET  /jobs/{id}/resume   最新 epoch 权重（worker 重领时接续训练）
    GET  /jobs/{id}/bc-metrics  每 epoch 指标行

## 为什么这一组在账本里是 **L4**（比同批的另外三组高）

`_post_result` 走 `remote.push_dispatch.accept_result` —— 「校验必须一条不落」的那条纪律要求
推模式（hub 代发后取回）与拉模式（云机 POST 上来）用**同一个**校验函数。于是本模块依赖
`remote.push_dispatch`(L3) ⇒ 拓扑秩 **4**。宿主 `hub_server` 因此升到 **L5**（与 `remote.worker`
对称：两个宿主各组装自己的 L4 执行单元）。这不是口味：账本的秩断言会把标错的层当场报出来。
"""

from __future__ import annotations

import json
from email.message import Message
from io import BufferedIOBase
from typing import Any

from common.protocol import (
    FAIL_BODY_MAX,
    WIRE_V2_MAGIC,
    ProtocolError,
    unpack_result_v2,
)
from remote.push_dispatch import accept_result


class ResultRoutes:
    """result 路由 mixin（`HubHandler(…, ResultRoutes, BaseHTTPRequestHandler)`）。"""

    # 由组合类（HubHandler）提供的运行时状态与通用助手——混入不继承 BaseHTTPRequestHandler，
    # 故需自行声明类型（否则 mypy 报 attr-defined）。⚠ 类型必须与 typeshed 逐字一致
    # （`headers: email.message.Message` / `rfile: BufferedIOBase` / `path: str`）：本混入在 MRO 里
    # 早于 BaseHTTPRequestHandler，声明成 Any 会把组合类里 `self.headers.get(...)` 的推断拓成 Any。
    hub: Any
    headers: Message
    path: str
    rfile: BufferedIOBase

    # 由组合类提供（实现都在 `remote/hub_server.HubHandler`）——混入只声明类型。
    _clip: Any
    _job_or_404: Any
    _json: Any
    _lease_token: Any
    _read_raw_body: Any
    log_message: Any


    # ---- POST /jobs/{id}/result ----
    def _post_result(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        jd = self.hub._job_dir(jid)
        if not (jd / "manifest.json").exists():
            self._json({"error": "unknown job"}, 404)
            return
        raw = self._read_raw_body()
        if raw is None:
            return
        # M0 统一计量：收到的 result 请求体字节（云上行实测）——即便后面校验失败
        # 也已实收，如实记录，供对账。
        self.hub.record_result_recv(jid, len(raw))
        # 竞速广播：**胜负已定就不再往下走**——结果已存在的回传一律 409（与租约无关；
        # 赢家可能持旧 token、输家根本没 token）。必须在租约校验**之前**：否则输了竞速
        # 的副本会因「非持有人」拿 403，而 worker 把 4xx 当确定性拒绝 → 报 job 失败。
        if (jd / "result").exists():
            self._json({"error": "result already stored (race loser / duplicate)"}, 409)
            return
        try:
            # 方案B（2026-09-10）：v2 体（gzip 裸二进制段）**按魔数自动识别** —— 不依赖
            # Content-Type，故旧 worker（纯 JSON）与新 worker（v2）都能收。还原出的 dict
            # 与方案A 逐字段一致（二进制字段被重新 base64）⇒ 下游零改动。
            if raw.startswith(WIRE_V2_MAGIC):
                result = unpack_result_v2(raw)
            else:
                result = json.loads(raw.decode("utf-8"))
        except (ValueError, ProtocolError) as e:
            self._json({"error": f"result rejected: {e}"}, 400)
            return
        # 对账（job_id/data_fp/init_weights_fp/commit_echo）+ 租约校验 + 首写锁定：
        # 走 `push_dispatch.accept_result` —— **与 hub 中介 push 派发器同一个函数**。
        # 推模式下两条腿并存（云机 POST 上来 / hub 代发后取回），校验绝不能一条有一条无：
        # 那正是「一份对不上账的结果被静默落盘成一轮看起来正常的训练」的入口。
        lease_token = self._lease_token()
        code, why = accept_result(
            self.hub, jid, result, lease_token, log=lambda m: self.log_message("%s", m)
        )
        if code != 200:
            self._json({"error": why}, code)
            return
        self._json({"job_id": jid, "status": "accepted"})


    # ---- POST /jobs/{id}/fail（节点确定性失败回报；2026-09-17）----
    def _post_fail(self) -> None:
        """节点判定「这个 job 在这台机器上跑不成」时回报原因（bun 缺失 / TS 运行时
        取不到 / argv 非法）。训练侧随后从 `GET /jobs/{id}/result` 拿到 **410 + 原因**，
        立刻停腿——不再等 25 分钟超时（超时会把「能力缺失」写成「网络/排队问题」）。

        鉴权同 release/result（H2：活租约须持有人）；首写锁定见 store_job_failure。
        """
        jid = self._job_or_404(known=True)
        if jid is None:
            return
        raw = self._read_raw_body()
        if raw is None:
            return
        if len(raw) > FAIL_BODY_MAX:
            self._json({"error": "fail body too large"}, 400)
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return
        if not isinstance(body, dict) or not isinstance(body.get("reason"), str) or not body["reason"]:
            self._json({"error": "reason 必填（非空字符串）"}, 400)
            return
        lease_token = self._lease_token()
        if not self.hub.result_token_ok(jid, lease_token):
            self._json({"error": "lease mismatch — 非本 job 租约持有人"}, 403)
            return
        rec = {
            "reason": self._clip(body["reason"], 2000),
            "kind": self._clip(body.get("kind", ""), 200),
            "detail": self._clip(body.get("detail", ""), 4000),
            "worker": self._clip(body.get("worker", ""), 200),
            "ts": self.hub._now(),
        }
        recorded = self.hub.store_job_failure(jid, rec)
        if recorded:
            # 账本事件（审计 + 控制台训练日志可见）：训练侧与会话结束后的复盘都能
            # 看到「哪一轮、哪台机器、为什么失败」，而不是一行超时。
            # 多课程：账本是**每课程一份**，所以追加必须带 job_id 让调度面先解归属
            #（旧单课程 hub 只有一份账本，不需要这个参数）。
            self.hub.append_ledger(
                jid,
                {
                    "event": "job_failed",
                    "job_id": jid,
                    "reason": rec["reason"],
                    "kind": rec["kind"],
                    "worker": rec["worker"],
                    "ts": rec["ts"],
                },
            )
            self.log_message("JOB FAILED %s: %s", jid, rec["reason"])
        self._json(
            {"job_id": jid, "status": "failed-recorded" if recorded else "already-recorded"}
        )


    # ---- GET /jobs/{id}/status ----
    def _get_status(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        jd = self.hub._job_dir(jid)
        if not (jd / "manifest.json").exists():
            self._json({"error": "unknown job"}, 404)
            return
        fail = self.hub.job_failure(jid)
        frozen = self.hub.frozen_info(jid)
        if (jd / "result" / "result.json").exists():
            state = "done"
        elif fail is not None:
            # 终局（2026-09-17）：节点已报确定性失败——控制台与 wait_job 的收尾
            # 二次确认都读这个 state，不必再去 /result 取 410。
            state = "failed"
        elif (self.hub.lease_expires_in(jid) or 0.0) > 0:
            state = "leased"
        elif frozen is not None:
            # ★ 毒包熔断（§4.1）：终局状态之一（与 pending/leased 并列）。训练侧只要
            # “还会不会有人来跑”这一个答案，而冻结的答案就是「不会，除非人工解冻」。
            state = "frozen"
        else:
            state = "pending"
        resp: dict = {"job_id": jid, "state": state}
        # 调度面摘要（2026-09-22）：cancel-watcher 靠 `landed` 判是否停算；`computing_at`
        # 是掉队阈值的时基；`priority` 是「我该不该继续算」的现成答案。旧读方忽略未知字段。
        sched = self.hub.job_status(jid)
        for k in ("landed", "ready", "computing_at", "epoch"):
            if k in sched:
                resp[k] = sched[k]
        if state in ("pending", "leased") and "priority" in sched:
            resp["priority"] = sched["priority"]
            resp["priority_reason"] = sched["reason"]
        if frozen is not None:
            resp["reclaims"] = int(frozen.get("reclaims", 0) or 0)
            resp["frozen_at"] = float(frozen.get("ts", 0.0) or 0.0)
            resp["last_worker"] = str(frozen.get("worker", "") or "")
        if fail is not None and state == "failed":
            resp["reason"] = str(fail.get("reason", ""))
            resp["fail_kind"] = str(fail.get("kind", ""))
            self._json(resp)
            return
        # P3b 可观测：租约剩余秒 + 距上次心跳秒（worker 吞错保持现状，文档化——
        # 心跳 5xx 时 worker 侧只记日志不抛，见 worker._hb_loop）。
        if state == "leased":
            resp["lease_expires_in"] = self.hub.lease_expires_in(jid)
            resp["last_heartbeat_ago"] = self.hub.last_heartbeat_ago(jid)
        self._json(resp)


    # ---- GET /jobs/{id}/result ----
    def _get_result(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        r = self.hub.get_result(jid)
        if r is None:
            # 410 = 这个 job **不会有结果**（节点已报确定性失败，原因在体内）。
            # 刻意不用 404（那是「还没回来，继续等」）也不用 5xx（调用方按瞬时错误
            # 重试）——410 让 wait_job 立刻带着原因收兵，而不是等满 25 分钟。
            fail = self.hub.job_failure(jid)
            if fail is not None:
                self._json(
                    {
                        "job_id": jid,
                        "failed": True,
                        "error": str(fail.get("reason", "job failed")),
                        "fail_kind": str(fail.get("kind", "")),
                        "fail_detail": str(fail.get("detail", "")),
                    },
                    410,
                )
                return
            froze = self.hub.frozen_info(jid)
            if froze is not None:
                # ★ 毒包熔断（§4.1）：冻结也是「不会有结果」——不把训练侧挂在 25 分钟
                # 超时上（那正是本次事故的形态：真实原因在最里面，外面只剩一行超时）。
                # fail_kind 与节点失败区分开（控制台/日志能一眼看出这是熔断，不是能力缺失）。
                self._json(
                    {
                        "job_id": jid,
                        "failed": True,
                        "error": (
                            f"job 已被 hub 熔断冻结：连续 {int(froze.get('reclaims', 0) or 0)} 次"
                            "认领后零回传（疑似内容决定性毒包）——人工确认后 "
                            f"POST /admin/unfreeze job_id={jid} 解冻重发"
                        ),
                        "fail_kind": "PoisonFrozen",
                        "fail_detail": (
                            f"last_worker={froze.get('worker') or '?'} "
                            f"reclaims={int(froze.get('reclaims', 0) or 0)}"
                        ),
                    },
                    410,
                )
                return
            self._json({"error": "not done"}, 404)
            return
        # M0 统一计量：把 hub 侧传输层实测字节（additive 的 wire_hub 键）随结果
        # 一并回给训练主循环——旧读方忽略未知键，旧 result.json 不受影响。
        stats = self.hub.wire_stats(jid)
        if stats:
            r = {**r, "wire_hub": stats}
        self._json(r)


    # ---- POST /jobs/{id}/epoch（BC 每 epoch 回传：权重 resume + 指标行，2026-09-13）----
    def _post_bc_epoch(self) -> None:
        jid = self._job_or_404(known=True)
        if jid is None:
            return
        raw = self._read_raw_body()
        if raw is None:
            return
        if len(raw) > self.hub.BC_EPOCH_BODY_MAX:
            self._json({"error": "epoch body too large"}, 400)
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return
        # 租约口径与 result 相同：活租约须持有人（防被顶掉的旧 worker 用旧 epoch
        # 覆盖新 resume）；无租约（过期/释放/重启后）照收——resume 是幂等覆盖存最新。
        lease_token = self._lease_token()
        if not self.hub.result_token_ok(jid, lease_token):
            self._json({"error": "lease mismatch — 非本 job 租约持有人"}, 403)
            return
        if not self.hub.store_bc_epoch(jid, body):
            self._json({"error": "invalid epoch body"}, 400)
            return
        self._json({"job_id": jid, "status": "accepted"})


    # ---- GET /jobs/{id}/resume（最新 epoch 权重——worker 重领时接续训练）----
    def _get_bc_resume(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        r = self.hub.get_bc_resume(jid)
        if r is None:
            self._json({"error": "no resume checkpoint"}, 404)
            return
        self._json(r)


    # ---- GET /jobs/{id}/bc-metrics（训练机/run_bc 轮询每 epoch 指标行）----
    def _get_bc_metrics(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        self._json({"job_id": jid, "rows": self.hub.get_bc_metrics(jid)})
