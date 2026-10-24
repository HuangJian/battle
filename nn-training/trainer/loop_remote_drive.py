"""loop_remote_drive —— **两个驱动入口** mixin（2026-09-25 S4 第二十二刀拆出）。

判据同源：「**谁驱动这条腿**」——轮内细粒度三相（`_remote_ppo_step`，允许让位）·
整轮上云（`_remote_iter`，`kind=iter`）。两者都只在**本机侧**做派发与结算，真正的
打包/校验/落位全在 `TrainingRemoteJob`。

★ 半离线整段（`_remote_run_segment`，`kind=run`）**已于 2026-09-25 退役**
（`plan/online-offline-role-routing.plan.md` §7）：被接管的课改由云机**取任务包**接手，
本机侧由 `COLLECT_HELD` → `ROUND_HELD_EXIT` 干净收官（见 `trainer/loop_round_steps.py`；
★M2 起判据是 hub 的 hold，不是 rl-config 的 `rollout_src=run`）。
「发一份 kind=run 队列项、随后等 8h」那条腿与取包链干的是同一件事，两个执行者正是
云机接错盘事故的结构。

依赖方向：`class TrainingRemoteDrive(TrainingRemoteFail, TrainingRemoteJob)`——驱动要
用失败策略（`_abort_node_failure` / `_handle_remote_failure`）与 job 四步/组合入口。
组合根 `TrainingRemote` 只有一个基类，就是本模块。
"""

from __future__ import annotations

import time
from pathlib import Path

import common.distribution
from common.log import log
from common.protocol import JobFailedError
from trainer.loop_remote_fail import TrainingRemoteFail
from trainer.loop_remote_job import TrainingRemoteJob
from trainer.loop_transport import fatal_remote_http, remote_retryable_exceptions
from worker.events import write_gate_verdict
from worker.loop_round import ROUND_SKIP, RemotePpoJob, RoundContext, StepResult, wait_for

#: ★M1（plan/ppo-job-lifecycle-ownership §2）看门狗：两次 `/jobs/{id}/status` 探询的最小
#: 间隔（秒）——节流，防把 hub 变成新的轮询热点。
LOST_PROBE_SEC = 30.0
#: ★M1 看门狗：hub 侧持续答「不认识这个 jid」多久才判 lost（秒）。
#: 宽限期**必须**给：`publish` 到 hub 扫到目录之间有秒级窗口（`hub/queue_discover.py`），
#: 不给宽限会把正常排队误判成丢失。检测上界 = 宽限 + 2×探针节拍（≈120s）。
LOST_GRACE_SEC = 60.0


def _self_healable_cancel(jsonl_path: Path, jid: str) -> bool:
    """这份活是不是被「上一代残留清理」撤的（⇒ 本进程该自愈：撤单 + 重发同 jid）？

    ★ 2026-10-17（plan/export-must-not-cancel-live-job §4，2026-10-16 事故）：三类写者往
    **同一份**账本落 `job_cancelled`——M2 腿启动清理（上一代 runId 的残留）/ 人工
    `POST /admin/cancel-job` / `cancel_unsettled_jobs`（模式切换等）。只有第一类应该自愈：
    无差别重发会**替操作员反悔**（而且 `_cancel_remote_job` 顺带 `unfreeze_job`，连人工/熔断的
    冻结一起解掉）。判据取**白名单**（reason 前缀，写者与读者共用
    `remote.hub_client.M2_CANCEL_REASON_PREFIX`），默认不自愈。

    净态判据走 `worker.bc_ledger.job_net_event`（唯一实现）：撤单后重发的活净态回 `job_pending`
    ⇒ 本函数为 False（**不许**拿 `scan_jobs` 的「曾经终局」集合当净态——那会一拍一撤）。
    """
    from remote.hub_client import M2_CANCEL_REASON_PREFIX
    from worker.bc_ledger import JOB_CANCELLED_EVENT, job_net_event

    ev = job_net_event(jsonl_path, jid)
    if ev is None or ev.get("event") != JOB_CANCELLED_EVENT:
        return False
    return str(ev.get("reason") or "").startswith(M2_CANCEL_REASON_PREFIX)


class TrainingRemoteDrive(TrainingRemoteFail, TrainingRemoteJob):
    """两个驱动入口：轮内三相 / 整轮上云（半离线整段已退役，见模块头部）。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 「有意并存」的理由同 `trainer/loop_remote_push.py`；`args` / `_jsonl_path` /
    # `_leg_abort` / `_remote_fail` 由基类声明（同一个对象的契约分片）。
    _report: dict
    _rollout_sec: float
    #: M3（plan/ppo-job-lifecycle-ownership §4）：重发意图的「坏文件只喊一次」去重（空 = 无错）。
    _redo_err_note: str
    #: M3：本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。必须在这里声明类型
    #: ——只在 _remote_iter 里赋值会被 mypy 推成 float，子类的 `float | None` 就冲突。
    _node_rollout_sec: float | None
    # 注：`_rotate_seed` 的声明**不在**这里了——它唯一在本族的读者是已退役的 `_remote_run_segment`；
    # 今天读它的是 `loop_export._volume_plan_block`（声明在那里）。留着就是一条永不被碰的假契约
    # （`test_borrowed_declarations_are_exactly_the_touched_set` 的口径）。

    def _remote_ppo_step(self, ctx: RoundContext) -> StepResult | None:
        """远端 PPO 的**三相驱动**（细粒度路径；R2c-3）。

        返回 `None` = 本轮远端 PPO 已收口（成功，或已降级给本机）；返回 `StepResult`
        = 本轮结束（让位 / 停车）。

        与组合路径（`_remote_ppo`）共用同一批相位方法与同一份失败判决
        （`_handle_remote_failure`），唯一差别在这里**允许让位**：`ctx.resumable` 为真时
        未就绪就 `wait_for`，执行权交给别的课程；为假则退化成阻塞取结果（组合语义）。
        """
        it = ctx.it
        sess = ctx.remote
        # ★M3：重发本轮意图先消费（每拍读一次；命中 ⇒ 撤单 + 丢 sess ⇒ 本拍走正常发布路径）。
        if sess is not None and self._maybe_consume_redo(sess, it):
            sess = None
            ctx.remote = None
        if sess is None:
            try:
                sess = self._remote_ppo_publish(it)
            except remote_retryable_exceptions() as e:
                if self._handle_remote_failure(it, e):
                    raise
                return None  # 已降级：调用方改走本机
            ctx.remote = sess
        try:
            result = self._remote_ppo_probe(sess)
        except remote_retryable_exceptions() as e:
            if self._handle_remote_failure(it, e):
                raise
            return None
        if result is None:
            if ctx.resumable:
                if self._remote_ppo_lost(sess, it):
                    # 已判 lost 并撤单：丢掉这条会话——下一拍 `ctx.remote` 为空 ⇒ 走正常
                    # 发布路径重发**同一 jid**（幂等键不含 code/时间，重发就是同一份活的复活）。
                    ctx.remote = None
                    return wait_for(f"等远端 PPO 重发（job {sess.jid} 已在 hub 侧丢失，已撤单）")
                # ★ 让位点：job 已经发布，只是还没回。本机没在替它干活（云机在跑）
                # ⇒ 票还掉，机器让给别的课（`hold=False` 见 `LoopRunner`）。
                return wait_for(f"等远端 PPO 回传（job {sess.jid}）")
            try:
                result = self._remote_ppo_fetch(sess)
            except remote_retryable_exceptions() as e:
                if self._handle_remote_failure(it, e):
                    raise
                return None
        try:
            self._remote_ppo_land(sess, result)
        except remote_retryable_exceptions() as e:
            from remote.hub_client import TripleCheckError

            if isinstance(e, TripleCheckError):
                # ★ 2026-10-11（事故；评审 F2）：三重校验拒收 = 这个会话已死。
                # 清掉它 ⇒ 下一次 attempt 重新发布/规划（不再只 probe 同一 jid、不再把
                # 同一份坏结果重落 5 次）。**只对拒收清**：瞬时性落位失败保留会话，
                # 重试只重落取回的结果，不白烧一次云上重算。
                ctx.remote = None
            if self._handle_remote_failure(it, e):
                raise
            return None
        ctx.remote = None
        self._remote_fail = 0
        return None

    # ------------------------------------------------------- M1 看门狗 / M3 重发（2026-10-13）

    def _remote_wait_report(self, sess: RemotePpoJob, now: float) -> None:
        """细粒度路径的「在等什么」打点（与 `wait_job` 共用同一个 `_wait_state_note`）。

        节流按墙钟：每 `WAIT_REPORT_SEC` 一行（常量与 `wait_job` 同一份，不各写一份节拍）。
        它是这条路径**唯一**的现场可见性——细粒度路径没有超时兜底，零打点的后果就是
        2026-10-13 事故里的「安静地挂 22 分钟」。观测失败永不影响等待语义
        （`_wait_state_note` 自身 best-effort）。
        """
        from remote.hub_http import WAIT_REPORT_SEC, _wait_state_note

        base = sess.last_wait_report or sess.t_ppo
        if now - base < WAIT_REPORT_SEC:
            return
        sess.last_wait_report = now
        waited = now - sess.t_ppo
        if sess.transport == "hub":
            line = _wait_state_note(sess.hub_url, sess.hub_token, sess.jid, waited)
        else:
            # push 腿的状态端点在节点上、路径不同（`/job/{jid}/status`）——本刀不猜它的形状，
            # 只报「在等谁、等了多久」。TODO(push)：补节点侧状态问询（同 M1 的判据表）。
            line = f"job {sess.jid} 等待节点回传（transport=push）（已等 {waited:.0f}s）"
        log("[run_rl] " + line)

    def _remote_ppo_lost(self, sess: RemotePpoJob, it: int) -> bool:
        """M1 看门狗：让位期间把「这份 job 还能不能回来」问出一个答案。

        返回 True = **已判 lost 并撤单**（调用方不再等旧 jid；下一拍正常发布路径重发同 jid）。
        返回 False = 还在等（含：hub 还不认识它但没超宽限 / hub 不可达 / hub 说在跑或排队 /
        本地撤单不属本进程该自愈的那一类）。

        判据（唯一实现；分类表见 plan §2(a)）：
          * **本地账本净态 = `job_cancelled` 且撤单来源是本进程该自愈的那一类** ⇒ 判 lost
            （2026-10-17 加；见 `_self_healable_cancel`——2026-10-16 事故里 hub 一路答 200）；
          * `/jobs/{id}/status` **404** = hub 侧不存在这个 jid ⇒ 起表/续表 `lost_since`；
          * `200`（pending/leased/done/failed/frozen/cancelled 任意）= hub 认识 ⇒ 清表；
          * 异常 / 其它状态码 = 网络问题 ⇒ **不动表、更不判 lost**（判错的代价是一次完整重发）。
        """
        now = time.time()
        self._remote_wait_report(sess, now)
        if sess.transport != "hub":
            # push 腿（直推节点）的状态面是 `push_client.poll_result`（另一个端点）——本刀只在
            # hub 路径（含 hub 中介推送）生效。TODO(push)：补等价判据（别假装覆盖）。
            return False
        if now - sess.last_lost_probe < LOST_PROBE_SEC:
            return False
        sess.last_lost_probe = now
        if _self_healable_cancel(self._jsonl_path, sess.jid):
            # ★ 2026-10-17（plan/export-must-not-cancel-live-job §4）：账本里这份活**已经被撤**
            # （上一代 runId 的残留被另一个进程的 M2 清理作废——2026-10-16 事故：导出腿把在线腿
            # 正在等的活撤了，而 hub 的 `/status` 仍答 pending，M1 的 404 判据照不到）。
            # 本地终局是**确定性事实** ⇒ 不设宽限；收尾与既有 lost 完全同一条路（撤单 + 连败预算
            # + 丢会话 ⇒ 下一拍重发同 jid），净态回 pending 后不会再判（重发即复活）。
            self._cancel_remote_job(sess, it=it, reason="本地账本已判终局（上一代残留被清理），不再等待")
            self._bump_remote_fail(it, f"job {sess.jid} 本地账本已撤（等待无意义）")
            log(
                f"[run_rl] it{it}: job {sess.jid} 本地账本已判终局（上一代残留被清理）— 已撤单；"
                "下一拍重发同 jid"
            )
            return True
        from remote.hub_http import JOB_ALIVE, JOB_UNKNOWN, job_liveness

        st = job_liveness(sess.probe_base_url, sess.probe_token, sess.jid)
        if st == JOB_ALIVE:
            if sess.lost_since:
                log(f"[run_rl] it{it}: job {sess.jid} 恢复可见（hub 侧又认识它）——撤销丢失观察")
                sess.lost_since = 0.0
            return False
        if st != JOB_UNKNOWN:
            # unreachable：不动表（也不清）——网络抖动不是丢失；但**绝不**据此判 lost。
            return False
        if not sess.lost_since:
            sess.lost_since = now
            log(
                f"[run_rl] it{it}: job {sess.jid} 在 hub 侧不存在（status 404）——进入 "
                f"{LOST_GRACE_SEC:.0f}s 宽限观察（多为刚发布/刚重启的秒级窗口）"
            )
            return False
        waited = now - sess.lost_since
        if waited < LOST_GRACE_SEC:
            return False
        # 判 lost：撤单 + 丢会话 ⇒ 下一拍重发同一 jid（同幂等键 = 同一份活复活，
        # hub 认领闸按账本净态放行）。连败预算复用 `_bump_remote_fail`（3 次 ABORT，不新造阈值）。
        self._cancel_remote_job(sess, it=it, reason=f"hub 侧不存在（lost 持续 {waited:.0f}s）")
        self._bump_remote_fail(it, f"job {sess.jid} 在 hub 侧丢失（持续 {waited:.0f}s）")
        log(
            f"[run_rl] it{it}: job {sess.jid} 在 hub 侧已不存在（lost 持续 {waited:.0f}s）"
            "— 已撤单；下一拍重发同 jid"
        )
        return True

    def _maybe_consume_redo(self, sess: RemotePpoJob, it: int) -> bool:
        """M3：消费「重发本轮 PPO」意图（幂等）。返回 True = 已撤单（调用方丢 `ctx.remote`）。

        意图文件 `tmp/<course>/redo-ppo.json`（控制台写；形状/回执契约见 `trainer/loop_control.py`）：
        命中且 `it` 与当前轮一致（或未限定）⇒ 撤当前 sess、写回执；**按 `at` 去重**——同一份
        意图只执行一次（否则 `_remote_ppo_step` 每拍都会再撤一次刚重发的活，变成无限重发）。
        坏文件只喊一次（按错误文案去重），宁可不动作。
        """
        # 课程级路径（`tmp/<course>/redo-ppo.json`）——**不是** `_traj_dir`（那是本轮
        # `it<N>/`），意图文件按课存在、跨轮存活。
        traj = getattr(self, "_traj_root", None)
        if traj is None:
            return False
        from trainer.loop_control import consume_redo, write_redo_applied

        intent, err = consume_redo(traj, it)
        if err:
            if err != self._redo_err_note:
                self._redo_err_note = err
                log(f"[run_rl] 忽略重发意图（保守：不动作）：{err}")
            return False
        if intent is None:
            return False
        self._cancel_remote_job(sess, it=it, reason="控制台重发本轮 PPO")
        werr = write_redo_applied(traj, it=it, at=intent.at)
        if werr:
            log(f"[run_rl] WARN 重发回执写入失败（不影响训练）：{werr}")
        log(f"[run_rl] it{it}: 控制台要求重发本轮 PPO——已撤单 job {sess.jid}；本拍重发同 jid")
        return True

    def _cancel_remote_job(self, sess: RemotePpoJob, *, it: int, reason: str) -> None:
        """撤单一条已发布的远端 job（M1 lost / M3 redo 共用）——**不硬杀在飞**。

        ① 写账本 `job_cancelled`（幂等净态门；唯一事实源，hub 的认领闸按净态对账）；
        ② best-effort `POST /admin/unfreeze`（冻结只住 hub 进程内存；不清 ⇒ 同 jid 重发后
           `/result` 立刻 410 PoisonFrozen——「自愈」变「停腿」）；
        ③ best-effort `POST /admin/cancel-job`：404（未知/已不在池）/409（**在飞，hub 不撤**，
           2026-10-03 用户裁决）都容忍——**409/不可达时不删目录**（在算的 worker 回传要落进
           它；结果回来若 commit 不符会被三重校验响亮拒收，是既有语义），其余档位过护栏
           (`reap_job_dir`：manifest 的 job_id/runId/it 与账本一致才删) 清场。
        任何一步失败都只降级为「只写账本」，绝不阻断训练主链。
        """
        from remote.hub_client import cancel_job, reap_job_dir
        from remote.hub_http import (
            ADMIN_CANCEL_INFLIGHT,
            ADMIN_CANCEL_UNREACHABLE,
            admin_cancel_job,
            unfreeze_job,
        )

        cancel_job(self._jsonl_path, sess.jid, reason=reason, it=it)
        unfreeze_job(sess.hub_url, sess.hub_token, sess.jid, log=log)
        verdict = admin_cancel_job(sess.hub_url, sess.hub_token, sess.jid, log=log)
        if verdict in (ADMIN_CANCEL_INFLIGHT, ADMIN_CANCEL_UNREACHABLE):
            if verdict == ADMIN_CANCEL_UNREACHABLE:
                log(
                    f"[run_rl] it{it}: job {sess.jid} 人工作废未送达（hub 不可达）——"
                    "只写账本降级、目录保留"
                )
            return
        man_it = sess.manifest.get("it")
        reap_job_dir(
            sess.job_root,
            sess.jid,
            run_id=str(sess.manifest.get("runId") or ""),
            it=int(man_it) if isinstance(man_it, int) else None,
            log=log,
        )

    def _remote_iter(self, it: int, pairs: list[tuple[int, int]]) -> str | None:
        """M3：**整轮上云**（kind=iter）——节点跑 rollout + PPO，hub 只发规格、收结果。

        与 `_remote_ppo` 共享整条发布/传输/三重校验/落位/埋点链（只换「发什么、收什么」）：
          * 发：rollout 规格（逐局 argv，job 目录内相对路径）+ TS 运行时 + init 权重；
          * 收：权重/opt/agg（同旧）+ **采集报告**（本机此时无 shard 可算）。

        返回：`None` = 正常推进；`ROUND_SKIP` = 本轮跳过（配置冲突，见下）。

        与动态采集（target_transitions）不相容：那套语义要求训练侧反复读本地 shard 补波，
        而本轮上云的 spec 不带 `volume` 块（`worker/iter_job.build_iter_spec`），shard 又在
        节点上（跑完即毁）。**正常路径走不到这里**（`resolve_collect` 的 `node_ok` 已把这种课
        改成本机 rollout）；这里是兜底：只跳本轮，由既有失败梯子（30s 退避 + 5 连击）收口，
        不再 `SystemExit` 把整门课下线（2026-10-23，plan/offline-hold-stale-fallback §3-S3）。
        """
        args = self.args
        if int(getattr(args, "target_transitions", 0) or 0) > 0:
            log(
                "[run_rl] rollout_src=node 与 --target-transitions（动态采集）不相容："
                "补波需要训练侧反复读本地 shard，而上云轮的 shard 在节点上（跑完即毁）。"
                "本轮跳过；连续 5 轮仍冲突 ⇒ 该课下线（与其他失败同一架梯子）"
            )
            return ROUND_SKIP
        from worker.iter_job import build_iter_spec

        wver = common.distribution.weights_fingerprint(args.out)
        # 节点并发（2026-10-09，plan/course-workers-removal §3-S2-3）：**0 = 节点自定**
        # （wire 语义），不再回退 `args.workers`——那是**训练机**的本机配额，曾被当成
        # 节点规模把 96 核云机钉死在 8。正整数 = 操作员加压阀（节点仍按核数夹取）。
        workers = int(getattr(args, "remote_iter_workers", 0) or 0)
        spec = build_iter_spec(
            args,
            pairs,
            wver=wver,
            workers=workers,
            game_timeout_sec=float(getattr(args, "remote_iter_game_timeout", 0.0) or 0.0),
            hub_bun=str(getattr(self, "bun", "bun") or "bun"),
        )
        log(
            f"[run_rl] rollout_src=node it{it}: {len(pairs)} 局上云采集"
            f"（node workers={workers if workers > 0 else '自定'}，wver={wver[:12] if wver else '-'}…）"
        )
        t_roll = time.time()
        try:
            result = self._remote_ppo(it, rollout_spec=spec)
        except remote_retryable_exceptions() as e:
            # 上云轮走的是 `_remote_ppo` 组合入口（loop_core 在 _node_rollout 时
            # 跳过 _serial_ppo），所以那条路的「鉴权/闭锁类失败立即 ABORT」得在这里
            # 补上：否则 401/403 会走通用兜底 5×30s 重发同一 job 再死（x3-step 事故
            # 的同一个浪费）。只贴判决，不在这里降级——上云轮没有本地 shard 可训练。
            if isinstance(e, JobFailedError):
                # 与上面同规：节点已回报原因（如 bun 装不上 / TS 运行时取不到）——
                # 这是确定性能力缺失，重试无益，立即带原因停腿。
                self._abort_node_failure(it, e, where="rollout_src=node 采集+PPO")
                raise
            fatal = fatal_remote_http(e)
            if fatal:
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"rollout_src=node 不可重试失败 HTTP {fatal}——检查 --remote-token "
                    f"与节点隧道：{str(e)[:200]}",
                    decider="loop",
                )
                log(
                    f"[run_rl] GATE ABORT it{it}: 上云 node 轮远端 HTTP {fatal}"
                    f"（鉴权/闭锁类，非网络抖动）——不再重试，立即停腿"
                )
                self._leg_abort = True
            raise
        rep = dict(result.get("report") or {})
        rep.pop("perGameSecs", None)  # 逐局秒数只用于诊断，不进 iteration 账本
        self._report = rep
        # 节点侧采集墙钟（本机口径的 self._rollout_sec 在这里无意义——整轮都在云上）。
        self._node_rollout_sec = float(rep.get("elapsedSec") or 0.0)
        self._rollout_sec = self._node_rollout_sec
        log(
            f"[run_rl] remote iter it{it}: 节点采集 {rep.get('games')} 局 "
            f"（{self._node_rollout_sec}s），往返 {round(time.time() - t_roll, 1)}s"
        )
        return None
