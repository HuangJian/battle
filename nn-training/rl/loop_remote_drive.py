"""loop_remote_drive —— **三个驱动入口** mixin（2026-09-25 S4 第二十二刀拆出）。

判据同源：「**谁驱动这条腿**」——轮内细粒度三相（`_remote_ppo_step`，允许让位）·
整轮上云（`_remote_iter`，`kind=iter`）· 半离线整段（`_remote_run_segment`，`kind=run`）。
三者都只在**本机侧**做派发与结算，真正的打包/校验/落位全在 `TrainingRemoteJob`。

依赖方向：`class TrainingRemoteDrive(TrainingRemoteFail, TrainingRemoteJob)`——驱动要
用失败策略（`_abort_node_failure` / `_handle_remote_failure`）与 job 四步/组合入口。
组合根 `TrainingRemote` 只有一个基类，就是本模块。
"""

from __future__ import annotations

import time
from typing import Any

import dist_common
from common.protocol import JobFailedError
from rl.events import write_event, write_gate_verdict
from rl.log import log
from rl.loop_remote_fail import TrainingRemoteFail
from rl.loop_remote_job import TrainingRemoteJob
from rl.loop_round import RoundContext, StepResult, wait_for
from rl.loop_transport import _run_wait_sec, fatal_remote_http, remote_retryable_exceptions


class TrainingRemoteDrive(TrainingRemoteFail, TrainingRemoteJob):
    """三个驱动入口：轮内三相 / 整轮上云 / 半离线整段。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 「有意并存」的理由同 `rl/loop_remote_push.py`；`args` / `_jsonl_path` /
    # `_leg_abort` / `_remote_fail` 由基类声明（同一个对象的契约分片）。
    _report: dict
    _rollout_sec: float
    #: M3：本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。必须在这里声明类型
    #: ——只在 _remote_iter 里赋值会被 mypy 推成 float，子类的 `float | None` 就冲突。
    _node_rollout_sec: float | None
    _rotate_seed: int

    #: 由 `TrainingExport`（产物出包簇）提供、在本模块里被**调用**的助手。
    _volume_plan_block: Any

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
            if self._handle_remote_failure(it, e):
                raise
            return None
        ctx.remote = None
        self._remote_fail = 0
        return None

    def _remote_iter(self, it: int, pairs: list[tuple[int, int]]) -> None:
        """M3：**整轮上云**（kind=iter）——节点跑 rollout + PPO，hub 只发规格、收结果。

        与 `_remote_ppo` 共享整条发布/传输/三重校验/落位/埋点链（只换「发什么、收什么」）：
          * 发：rollout 规格（逐局 argv，job 目录内相对路径）+ TS 运行时 + init 权重；
          * 收：权重/opt/agg（同旧）+ **采集报告**（本机此时无 shard 可算）。

        与动态采集（target_transitions）互斥：那套语义要求训练侧反复读本地 shard 补波，
        而这里 shard 在节点上（跑完即毁）。配错就响亮失败，不静默降级。
        """
        args = self.args
        if int(getattr(args, "target_transitions", 0) or 0) > 0:
            raise SystemExit(
                "[run_rl] rollout_src=node 与 --target-transitions（动态采集）互斥："
                "补波需要训练侧反复读本地 shard，而上云轮的 shard 在节点上（跑完即毁）。"
                "要动态采集就保持 rollout_src=local"
            )
        from rl.iter_job import build_iter_spec

        wver = dist_common.weights_fingerprint(args.out)
        workers = int(getattr(args, "remote_iter_workers", 0) or 0) or int(
            getattr(args, "workers", 1) or 1
        )
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
            f"（node workers={workers}，wver={wver[:12] if wver else '-'}…）"
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

    def _remote_run_segment(self, it: int, pairs: list[tuple[int, int]], n: int) -> int:
        """半离线：把 it..end_it **整段**交给云机自主跑（kind=run），返回段尾 it。

        用户需求（2026-09-17）：「云机领到任务（课程 + 初始权重 + 代码）后，即使本机 hub
        一直失联，也能全程自主完成训练，并以 kaggle/colab 官方方式提供产物打包下载」。

        与本机、kind=iter（逐轮上云）的差别只有一条：**hub 不再逐轮决策**。计划
        （`rl/plan.build_plan`）把「后面每轮跑哪些局 + argv 长什么样 + 到哪停」一次性写成
        文件随 payload 下发；节点用同 commit 的代码重放（`pairs_fp` 两侧对账，不符就
        一局不跑），逐轮权重/指标写进产物目录（`remote/artifacts.py`），末尾才回传合并结果。

        本函数只做三件 hub 侧的事：① 组装计划并发布；② 用**放大的**等待预算阻塞（整段
        墙钟量级）；③ 把节点回的逐轮明细落成 `run_segment` 事件（给控制台画曲线），并把
        段尾的报告/指标交给本轮结算（iteration 事件复用 `_record_iteration`）。

        中间轮没有本机 eval：它们不在本机跑，归档里也没有它们的权重（拿活指针去充数就是
        P0 修过的「标签超前一轮」）。所以调用方在本轮**跳过** `_dispatch_delayed_eval`。
        """
        args = self.args
        # ★ 2026-09-21（§3）：原先这里有「要求 --ppo remote」的闸——旗标删除后它恒真、
        # 会把整段误拒。单一 PPO 路径下「整段 rollout + PPO 都在节点上」本就是唯一形态。
        from rl.iter_job import build_iter_spec
        from rl.plan import RUN_NODE_LABEL, build_plan, dump_plan

        wver = dist_common.weights_fingerprint(args.out)
        if not wver:
            raise SystemExit(f"[run_rl] 半离线 it{it}: 本机无权重（{args.out}）——无起点不发段")
        workers = int(getattr(args, "remote_iter_workers", 0) or 0) or int(
            getattr(args, "workers", 1) or 1
        )
        game_timeout = float(getattr(args, "remote_iter_game_timeout", 0.0) or 0.0)
        spec = build_iter_spec(
            args,
            pairs,
            wver=wver,
            workers=workers,
            game_timeout_sec=game_timeout,
            hub_bun=str(getattr(self, "bun", "bun") or "bun"),
            node_label=RUN_NODE_LABEL,
        )
        iters_total = int(getattr(args, "iters", 0) or 0)
        if iters_total <= 0:
            if n < 0:
                raise SystemExit(
                    "[run_rl] --run-iters<0（跑到课程末尾）需要课程声明 iters——"
                    "没有终点就不叫整段，节点会一直跑下去"
                )
            iters_total = it + n
        plan = build_plan(
            args,
            it=it,
            iters_total=iters_total,
            rotate_seed=int(self._rotate_seed),
            # n-1：计划里的 argv 模板是给**下一轮**用的，段尾 = it + (n-1)。
            max_iters=0 if n < 0 else n - 1,
            workers=workers,
            game_timeout_sec=game_timeout,
            budget_sec=float(getattr(args, "run_budget_sec", 0.0) or 0.0),
            volume=self._volume_plan_block(),
            log=log,
        )
        end_it = int(plan["end_it"])
        log(
            f"[run_rl] rollout_src=run it{it}: 半离线段 it{it} → it{end_it}"
            f"（节点自主跑 {end_it - it} 轮；hub 失联不影响，产物在节点工作目录）"
        )
        t0 = time.time()
        try:
            result = self._remote_ppo(
                it,
                spec,
                plan_bytes=dump_plan(plan),
                wait_timeout_sec=_run_wait_sec(args),
            )
        except remote_retryable_exceptions() as e:
            # 与 `_remote_iter` 同规：节点已回报的确定性失败（bun 装不上 / TS 拿不到）
            # 与鉴权/闭锁类 HTTP 都**不重试**——重发同一段只是再白烧一个巨大等待预算。
            if isinstance(e, JobFailedError):
                self._abort_node_failure(it, e, where="半离线段 rollout+PPO")
                raise
            fatal = fatal_remote_http(e)
            if fatal:
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"半离线段不可重试失败 HTTP {fatal}——检查 --remote-token 与节点隧道："
                    f"{str(e)[:200]}",
                    decider="loop",
                )
                log(f"[run_rl] GATE ABORT it{it}: 半离线段远端 HTTP {fatal}——不再重试")
                self._leg_abort = True
            raise
        rows = [r for r in (result.get("iters") or []) if isinstance(r, dict)]
        last = rows[-1] if rows else {}
        rep = dict(last.get("report") or result.get("report") or {})
        rep.pop("perGameSecs", None)  # 逐局秒数只用于诊断，不进 iteration 账本
        self._report = rep
        # 结算口径用**段尾那一轮**（本轮的 PPO 已在节点跑完）：采集墙钟同理。
        self._node_rollout_sec = float(rep.get("elapsedSec") or 0.0)
        write_event(
            self._jsonl_path,
            {
                "event": "run_segment",
                "iter_start": int(it),
                "iter_end": end_it,
                "iters": rows,
                "run_state": result.get("run_state"),
                "plan_sha256": str(result.get("plan_sha256", "") or ""),
                "artifacts": result.get("artifacts") or {},
                "wall_sec": round(time.time() - t0, 1),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        got_end = int(result.get("it_end") or end_it)
        if got_end != end_it:
            # 节点自报的段尾与计划不符：结果仍然可信（协议已校验严格递增 + it_end == 末轮），
            # 但“我们以为跑到哪”必须按**实际**改，否则下一轮会重跑已训过的轮。
            log(
                f"[run_rl] WARN 半离线段实际跑到 it{got_end}（计划 it{end_it}）——"
                f"按实际推进（run_state={result.get('run_state')}）"
            )
        log(
            f"[run_rl] 半离线段收回：it{it} → it{got_end}（{len(rows)} 轮明细，"
            f"往返 {round(time.time() - t0, 1)}s，state={result.get('run_state')}）"
        )
        return got_end
