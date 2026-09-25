"""loop_remote_drive —— **两个驱动入口** mixin（2026-09-25 S4 第二十二刀拆出）。

判据同源：「**谁驱动这条腿**」——轮内细粒度三相（`_remote_ppo_step`，允许让位）·
整轮上云（`_remote_iter`，`kind=iter`）。两者都只在**本机侧**做派发与结算，真正的
打包/校验/落位全在 `TrainingRemoteJob`。

★ 半离线整段（`_remote_run_segment`，`kind=run`）**已于 2026-09-25 退役**
（`plan/online-offline-role-routing.plan.md` §7）：离线课改由云机**取任务包**接手，
本机侧由 `COLLECT_OFFLINE` → `ROUND_OFFLINE_EXIT` 干净收官（见 `rl/loop_round_steps.py`）。
「发一份 kind=run 队列项、随后等 8h」那条腿与取包链干的是同一件事，两个执行者正是
云机接错盘事故的结构。

依赖方向：`class TrainingRemoteDrive(TrainingRemoteFail, TrainingRemoteJob)`——驱动要
用失败策略（`_abort_node_failure` / `_handle_remote_failure`）与 job 四步/组合入口。
组合根 `TrainingRemote` 只有一个基类，就是本模块。
"""

from __future__ import annotations

import time

import dist_common
from common.protocol import JobFailedError
from rl.events import write_gate_verdict
from rl.log import log
from rl.loop_remote_fail import TrainingRemoteFail
from rl.loop_remote_job import TrainingRemoteJob
from rl.loop_round import RoundContext, StepResult, wait_for
from rl.loop_transport import fatal_remote_http, remote_retryable_exceptions


class TrainingRemoteDrive(TrainingRemoteFail, TrainingRemoteJob):
    """两个驱动入口：轮内三相 / 整轮上云（半离线整段已退役，见模块头部）。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 「有意并存」的理由同 `rl/loop_remote_push.py`；`args` / `_jsonl_path` /
    # `_leg_abort` / `_remote_fail` 由基类声明（同一个对象的契约分片）。
    _report: dict
    _rollout_sec: float
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
