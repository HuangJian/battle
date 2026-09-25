"""loop_steps —— TrainingSteps mixin：单轮结算与账（2026-09-02 从 rl/loop_core.py 拆出）。

run_training 迭代体的「采集之后」各阶段里，**留在本文件**的是两类：
课程读盘与配额（`_hot_reload_course` / `_course_iter` / `_per_stage_quota`）与
落账/取证（`_write_iter_stats` / `_log_report` / `_record_iteration` / `_forensics` /
`_commit_journal`）。八个成员彼此**零互调**（全部是叶子，各自被轮内步骤调用）。

三簇移居基类（方向都是「调用者依赖被调用者」，组合类仍是
`TrainingLoop(RoundSteps, TrainingSteps, TrainingGuards)`）：
  · 远端 PPO 腿（13 方法）→ `TrainingRemote`（`rl/loop_remote.py`，S4 第二步）；
  · in-loop 评估链（8 成员 + 五个 eval 槽位）→ `TrainingEval`（`rl/loop_eval.py`，S4 第十七刀）；
  · 产物出包（4 方法，含本文件原来**唯一**一条方法间调用链
    `_export_offline_bundle` → `_volume_plan_block`）→ `TrainingExport`
    （`rl/loop_export.py`，S4 第二十一刀）。
依赖的实例属性在 TrainingLoop.__init__/迭代方法中赋值，此处仅声明类型。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rl.events import write_iteration
from rl.log import log

# in-loop 评估链（S4 第十七刀）：派发 / 尾巴收拢 / join / 收官 drain 与 `_eval_on_round`
# 的占位搬到 `rl/loop_eval.py`。方向仍是「调用者依赖被调用者」——本类是调用者（轮内
# `RoundSteps` 与 `TrainingLoop` 从上面拿 `_dispatch_delayed_eval` / `_join_eval` /
# `_drain_pending_eval`），所以 `TrainingEval` 是基类。**追加**在既有基类之后：两混入
# 零重名、零互调、零 `super()` ⇒ 顺序在今天是惰性的，没有理由动已记录的 MRO。
from rl.loop_eval import TrainingEval
from rl.loop_export import TrainingExport
from rl.loop_remote import TrainingRemote

if TYPE_CHECKING:
    from rl.commit_journal import CommitJournal


# ─────────────────────────────────────────────────────────────────────────────
# 传输/发布层的**唯一实现**在 `rl/loop_transport.py`（S4，2026-09-23）。
# 这里只做门面 re-export：既有的 `from rl.loop_steps import X` 调用点（含
# tests/ 与 e2e/ 里对这些名字的 monkeypatch seam）因此都不必改。
# 「re-export = unused import」是门面的本意，故显式列进 __all__。
# ─────────────────────────────────────────────────────────────────────────────
from rl.loop_transport import (
    FATAL_REMOTE_HTTP,
    REMOTE_TRANSPORTS,
    ROLLOUT_SRCS,
    RUN_WAIT_DEFAULT_SEC,
    BundleExportedError,
    SmokeVoidRoundError,
    _course_cf_tunnel,
    _gate_round_shards,
    _gpu_push_nodes,
    _hub_push_opt_in,
    _kickstart_ref_payload,
    _push_job_round,
    _push_over_nodes,
    _remote_forward_agg,
    _rollout_source,
    _run_segment_iters,
    _run_wait_sec,
    _wire_from_result,
    fatal_remote_http,
    kickstart_coef,
    kickstart_warn_kind,
    remote_retryable_exceptions,
    require_remote_transport,
    resolve_hub_push,
    resolve_transport,
)

#: mixin 本体 + 传输/发布层门面。列全是为了让 ruff 的 F401 认得「这些 import 是
#: re-export（本意）而不是漏删」——没有 __all__ 时 F401 会把门面判成未用导入。
__all__ = [
    "FATAL_REMOTE_HTTP",
    "REMOTE_TRANSPORTS",
    "ROLLOUT_SRCS",
    "RUN_WAIT_DEFAULT_SEC",
    "BundleExportedError",
    "SmokeVoidRoundError",
    "TrainingSteps",
    "_course_cf_tunnel",
    "_gate_round_shards",
    "_gpu_push_nodes",
    "_hub_push_opt_in",
    "_kickstart_ref_payload",
    "_push_job_round",
    "_push_over_nodes",
    "_remote_forward_agg",
    "_rollout_source",
    "_run_segment_iters",
    "_run_wait_sec",
    "_wire_from_result",
    "fatal_remote_http",
    "kickstart_coef",
    "kickstart_warn_kind",
    "remote_retryable_exceptions",
    "require_remote_transport",
    "resolve_hub_push",
    "resolve_transport",
]


class TrainingSteps(TrainingRemote, TrainingEval, TrainingExport):
    """单轮结算与账 mixin。

    三个基类按「调用者依赖被调用者」挂在本类**末位之后**（追加不插队，`__mro__[1]` 仍是
    `TrainingRemote`）：远端 PPO 腿（`TrainingRemote`，13 方法；S4 第二十二刀切成
    Push / Job / Fail / Drive 四个混入，组合根仍住 `rl/loop_remote.py`）· in-loop 评估链
    （`TrainingEval`，8 成员）· 产物出包（`TrainingExport`，4 方法）。本类只被它们驱动，
    全体由 `TrainingLoop` 组合。
    """

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    ppo_backend: Any
    update_kwargs: Any
    _model: Any
    _opt: Any
    _device: Any
    _ref_model: Any
    _bc_ref: Any
    _ppo_mod: Any
    _ppo_goal: Any
    _ppo_intent: Any
    _save_weights_json: Any
    _start_it: int
    _traj_dir: Any
    _jsonl_path: Any
    #: R2a：写入账本后把事件并入 `LedgerView` 的钩子（实现在 TrainingGuards）。
    _ledger_apply: Any
    _report: dict
    _stream_meta: dict | None
    _rollout_sec: float
    #: M3：本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。必须在这里声明类型
    #: ——只在 _remote_iter 里赋值会被 mypy 推成 float，子类的 `float | None` 就冲突。
    _node_rollout_sec: float | None
    _ppo_sec: float
    #: 云端 worker **自报**的真训练秒（load+chunk+update，不含上传/排队/下载）。
    #: 与 `_ppo_sec`（往返墙钟）分开记——后者打包传输与排队，用于诊断/配额，
    #: 不应当作"训练量"（排队越久越"达标"是错的，且本地采样期间云端空转它看不到）。
    _ppo_cloud_sec: float
    _total_steps: int
    _chunks_n: int
    _agg: Any
    _kl_cum: Any
    _halted_flag: bool
    #: 远端连续失败 → 写 ABORT 后停腿（loop 在 PPO 步后检查）。
    _leg_abort: bool
    #: 远端连续失败计数（成功即复位）。
    _remote_fail: int

    _rotate_seed: int
    _dropped_games: Any
    _load_sec: Any
    _tail_drain_sec: Any
    _waves_n: Any
    #: 动态采集（plan/dynamic-rollout-volume）：None = 本轮课程未开该模式。
    _volume_target: int | None
    _volume_collected: int | None
    _volume_capped: bool
    #: bun 可执行文件路径（TrainingLoop 持有；延迟 eval 派发传给评估子进程）。
    bun: str
    #: 上轮节点配置快照（loop 每轮热读；drain 复用最近一份）。
    _last_dist_cfg: Any
    #: in-loop 评估链（派发 / 尾巴收拢 / join / 收官 drain）连同 `_eval_on_round` 的占位
    #: 一起住在 `TrainingEval`（`rl/loop_eval.py`，S4 第十七刀）：五个 eval 槽位与那 8 个
    #: 方法都在那边。槽位声明只有那一处——本类经继承可见（组合类仍是 `TrainingLoop`）。

    def _hot_reload_course(self, it: int) -> None:
        """课程热加载（§2026-09-13-hot-reload）：每 iter 重读课程文件，rollout 前执行。

        - 语料身份未变（B/C 类编辑）→ 白名单字段写回 args，下一 iter 生效；
          结构绑定字段（bc/workers/out 等）响亮日志「停止→启动后生效」。
        - 语料身份变了（A 类破坏性）→ `course_edit` 事件（控制台横幅）+ 响亮日志，
          **沿用启动配置继续训练**；D13/指纹用启动冻结字节 ⇒ 编辑不进云端 payload。
        - 文件半行写/瞬时坏档 → 沿用旧配置静默等到能读，不打横幅。
        """
        args = self.args
        course = getattr(args, "course_obj", None)
        path = str(getattr(args, "course_path", "") or "")
        if course is None or not path:
            return
        from rl.hot_reload import apply_hot_fields, changed_field_names, plan_reload

        try:
            from rl.config import load_course

            new_course = load_course(path)
        except Exception as e:  # 半行写/编码竞态——下轮重试
            if not getattr(self, "_hr_broken", False):
                log(f"[hot-reload] it{it}: 课程文件暂不可读（沿用启动配置）：{e}")
            self._hr_broken = True
            return
        self._hr_broken = False

        verdict, hot, restart = plan_reload(course, new_course)
        if verdict == "same":
            if getattr(self, "_hr_verdict", "") == "rejected":
                from rl.events import write_event

                write_event(
                    self._jsonl_path,
                    {"event": "course_edit", "verdict": "restored", "it": it},
                )
                log(f"[hot-reload] it{it}: 课程文件已恢复启动配置——拒绝横幅解除")
            self._hr_verdict = "same"
            return

        if verdict == "rejected":
            from rl.config import corpus_identity_fp
            from rl.events import write_event

            new_fp = corpus_identity_fp(new_course)
            if getattr(self, "_hr_reject_fp", "") != new_fp:
                fields = changed_field_names(course, new_course)
                write_event(
                    self._jsonl_path,
                    {
                        "event": "course_edit",
                        "verdict": "rejected",
                        "it": it,
                        "fields": fields,
                        "detail": "语料身份（关卡环境/奖励语义）被编辑",
                    },
                )
                log(
                    f"[hot-reload] it{it}: ⚠ 拒绝热加载——语料身份被编辑"
                    f"（{','.join(fields)}）。沿用启动配置继续训练，"
                    f"编辑内容不进云端 payload；要应用请派生新课程/新关卡"
                    f"（D14 语料血缘不可 mid-run 破坏）"
                )
                self._hr_reject_fp = new_fp
            self._hr_verdict = "rejected"
            return

        from rl.events import write_event

        changed = apply_hot_fields(args, new_course)
        if "max_hours" in changed:
            self._deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
        write_event(
            self._jsonl_path,
            {
                "event": "course_edit",
                "verdict": "applied",
                "it": it,
                "fields": changed,
            },
        )
        log(
            f"[hot-reload] it{it}: 课程编辑已热应用（{','.join(changed) or '无'}）"
            f"——下一 iter 生效"
            + (
                f"；restart-only 字段（{','.join(r for r in restart)}）停止→启动后生效"
                if restart
                else ""
            )
        )
        self._hr_verdict = "apply"

    def _course_iter(self, it: int) -> None:
        """M1c：每 iter 注入课程配置的加载期上下文（holder）与超参 schedule。

        - holder（reward_context）：reward_fn + gamma/lam + it + 血缘——loaders
          （ppo.engine.load_shard）读取，奖励唯一定义源=课程配置公式；
        - ppo_schedule（按绝对 iter 查表）：lr 改 opt.param_groups（保 Adam
          动量）、epochs/mb 改 args（串行/流式每轮读取）、kl_coef 存 args._kl_coef
          供 update 期注入。
        """
        args = self.args
        course = getattr(args, "course_obj", None)
        if course is None:
            from rl.reward_context import reset as _ctx_reset

            _ctx_reset()
            args._kl_coef = 0.0
            return
        if getattr(self, "_course_reward_fn", None) is None:
            from rl.reward_library import build_reward_fn

            self._course_reward_fn = build_reward_fn(course.reward_spec())
            log(f"[course] reward_fn compiled: formula_len={len(course.reward.formula)}")
        spec = course.reward_spec()
        from rl.reward_context import update as _ctx_update

        _ctx_update(
            reward_fn=self._course_reward_fn,
            gamma=float(getattr(args, "gamma", 0.995)),
            lam=float(getattr(args, "lam", 0.95)),
            it=it,
            identity={"course": course.name, "formula_hash": spec.identity()},
        )
        sch: dict = {}
        if course.ppo_schedule:
            from rl.schedule import resolve_ppo_schedule

            sch = resolve_ppo_schedule(course.ppo_schedule_dicts(), it)
        if "lr" in sch:
            # lr 折算必须落到 args.lr：remote 模式 hub 侧无 _opt，job manifest 的
            # lr 取自 args.lr（publish_job），worker 以 Adam(lr=manifest["lr"])
            # 建优化器——只写 opt.param_groups 会让三段 lr 表在远程路径全程失效。
            # 本地模式再同步 param_groups（保 Adam 动量，原语义不变）。
            args.lr = float(sch["lr"])
            if getattr(self, "_opt", None) is not None:
                self._opt.param_groups[0]["lr"] = args.lr
        if "mb" in sch:
            args.mb = int(sch["mb"])
        if "epochs" in sch:
            args.epochs = int(sch["epochs"])
        kl_coef = float(sch.get("kl_coef", 0.0) or 0.0)
        args._kl_coef = kl_coef
        args._kl_cap = sch.get("kl_cap")  # None = 不覆盖，由 policy.streamKlCap 决定
        # ent_coef（2026-09-11）：None = 用引擎常量 ENT_COEF（0.01）。0.0 是合法值（关掉熵正则），
        # 因此**不能**像 kl_coef 那样 `or 0.0` 兜底——那会把 None 与 0.0 混淆。
        args._ent_coef = sch.get("ent_coef")
        if sch:
            log(
                f"[course] ppo_schedule@it{it}: lr={sch.get('lr')} epochs={sch.get('epochs')} "
                f"mb={sch.get('mb')} kl_coef={kl_coef} kl_cap={sch.get('kl_cap', 'default')} "
                f"ent_coef={sch.get('ent_coef', 'default')}"
            )

    def _write_iter_stats(self, it: int) -> None:
        """M1c：每 iter 落 metrics_stats.jsonl（全维度统计 + 血缘；非致命）。

        M3：本轮 rollout 在云节点时**跳过**——metrics_stats 读的是本地 traj 目录的
        shard，上云轮的 shard 在节点上（跑完即毁），硬跑只会写一份 shards=0 的空统计，
        看起来像「本轮没采样」。逐维度口径改由节点回传的 report（dimMeans/scoreStats）
        承担（已记入 iteration 事件）。
        """
        if getattr(self, "_node_rollout_sec", None) is not None:
            log(
                f"[run_rl] metrics_stats it{it}: 本轮 rollout 在云节点（本地无 shard）——"
                "跳过逐维度统计，改看 iteration 的 report/wire"
            )
            return
        course = getattr(self.args, "course_obj", None)
        if course is None:
            return
        try:
            from rl.metrics_stats import metrics_stats

            identity = {
                "course": course.name,
                "formula_hash": course.reward_spec().identity(),
            }
            rec = metrics_stats(str(self._traj_dir), it=it, identity=identity)
            log(
                f"[run_rl] metrics_stats it{it}: shards={rec['shards']} "
                f"steps={rec['decision_steps']} elapsed_ms={rec['elapsed_ms']}"
            )
        except Exception as e:  # 统计失败不打断训练（warn-only，评审 P1-4）
            log(f"[run_rl] WARN metrics_stats failed (non-fatal): {e}")

    def _log_report(self, it: int, t_rollout: float) -> None:
        """报告结算：stream 报告拆解（eval 线程句柄 / 阶段耗时 / 遥测）与日志行。"""
        report = self._report
        stream_meta = self._stream_meta
        kl_cum = None
        halted_flag = False
        dropped_games = None
        load_sec = None
        tail_drain_sec = None
        waves_n = None
        if stream_meta is not None:
            # 流式评估线程句柄随报告回传（R4）：jsonl 写回前 join。
            # 槽位声明在 `TrainingEval`（S4 第十七刀）——两处写者经继承看同一个实例属性。
            self._eval_thread = report.pop("_eval_thread", None)
            _sm = report.pop("_stream")
            self._rollout_sec = _sm["rollout_sec"]
            self._ppo_sec = _sm["ppo_sec"]
            self._ppo_cloud_sec = float(_sm.get("ppo_sec") or 0.0) or self._ppo_sec
            self._total_steps = _sm["steps"]
            self._chunks_n = _sm["chunks"]
            self._agg = _sm["agg"]
            tail_drain_sec = _sm.get("tail_drain_sec")
            kl_cum = _sm.get("kl_cum")
            halted_flag = bool(_sm.get("halted", False))
            dropped_games = _sm.get("dropped_games")
            load_sec = _sm.get("load_sec")
            waves_n = _sm.get("waves")
        elif self._node_rollout_sec is not None:
            # M3 上云轮：t_rollout 含「等待节点跑完 rollout + PPO」的整段墙钟，拿它当
            # rollout_sec 会把 PPO/传输全算进采集（假指标）。用节点自报的采集墙钟。
            self._rollout_sec = float(self._node_rollout_sec)
        else:
            self._rollout_sec = round(time.time() - t_rollout, 1)
        self._kl_cum = kl_cum
        self._halted_flag = halted_flag
        self._dropped_games = dropped_games
        self._load_sec = load_sec
        self._tail_drain_sec = tail_drain_sec
        self._waves_n = waves_n
        log(
            f"[run_rl] rollout it{it}: games={report.get('games', 0)} "
            f"winRate={report.get('winRate', 0.0)} "
            f"outcomes={json.dumps(report.get('outcomes') or {})} "
            f"samples={report.get('totalSamples', 0)} ticks={report.get('totalTicks', 0)}"
        )
        if "scoreStats" in report:
            ss = report["scoreStats"]
            log(
                f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                f"min={ss['min']:.4f} max={ss['max']:.4f}"
            )
        if "dimMeans" in report:
            log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

    def _commit_journal(self) -> CommitJournal:
        """I1 WAL（hy E4/dsf）：PPO 提交序列的 started/done 台账（懒建）。

        路径 <traj>/commit_journal.jsonl。首次创建时扫描 pending——重启后见
        started 无 done 的轮次就大声报（本地轮靠 ppo_ckpt epoch 级断点续跑、
        远端轮按同 it 重发 job），把「上一轮提交到哪了」从事故考古变成一条日志。
        """
        j = getattr(self, "_commit_journal_obj", None)
        if j is None:
            from rl.commit_journal import CommitJournal

            j = CommitJournal(Path(self._traj_dir) / "commit_journal.jsonl")
            self._commit_journal_obj = j
            # R2b：报**在飞集**（不只是 phase/round）——「在等哪个 job、推给了谁」
            # 从「事故考古」变成一条日志（job_id 由 publish 后的 attach 行带上）。
            inflight = j.inflight()
            if inflight:
                detail = ", ".join(
                    f"{r['phase']}@{r['round']}"
                    + (f" jid={r['jid']}" if r.get("jid") else "")
                    + (f" via {r['dispatch']}" if r.get("dispatch") else "")
                    for r in inflight
                )
                log(
                    f"[run_rl] WAL replay-check: {len(inflight)} 个未完成提交 "
                    f"[{detail}] —— 本地轮由 ppo_ckpt 续跑、远端轮重发同 it job（幂等）"
                )
        return j

    def _forensics(self, tag: str) -> None:
        """I1 第 0 步取证（hy E4）：内存/磁盘快照进 run jsonl。

        OOM killer 与写盘失败不留 Python 堆栈、faulthandler 也不落盘——提交边界的
        最后一条 forensics 快照就是临终状态（RSS 峰值贴顶 = OOM 实锤；disk_free ≈ 0
        = 写盘失败实锤）。任何失败只记日志，绝不反杀训练。
        """
        try:
            from rl.forensics import log_snapshot

            log_snapshot(tag, self._jsonl_path, paths=[self._traj_dir])
        except Exception as e:  # 取证失败不阻断训练（诊断手段不是新故障面）
            log(f"[forensics] {tag} 快照失败（{type(e).__name__}: {e}）")

    def _per_stage_quota(self) -> int:
        """逐关严格样本量配额 = `ceil(target_transitions / 关数)`；**0 = 全收 = 老行为**。

        与 `_volume_topup` 共用 `target_per_stage`（同一个数，两侧不会漂），
        也共用 `parse_stages_arg` 解析 `--stages`（见其 docstring：**策略可以不同，
        判断必须同源**——否则会出现「采集说合法、训练说非法」的裂缝）。

        **本方法自带 mode 门**：`target_transitions` 只对 per-tick 有意义
        （intent/goal 不支持动态采集）⇒ 非 per-tick 一律返 0。放在这里而不是各调用点，
        是为了让 serial 与 remote `publish_job` 两条路径**不可能一个 gate 一个不 gate**。

        关集解析**不调 `self._volume_stages()`**：后者定义在 `TrainingVolume`（`rl/loop_volume.py`，
        S4 第十八刀）上，本 mixin（`TrainingSteps`）在类型层看不到它（mypy attr-defined）。
        `--stages` 缺席/不可解析
        ⇒ 返 0（静默降级为全收）；**响亮报错留在 `_volume_topup`** —— 采集侧先跑，
        真配错了在那里就炸，不必在这里重复炸一次。
        """
        if str(getattr(self.args, "mode", "")) != "per-tick":
            return 0
        target = int(getattr(self.args, "target_transitions", 0) or 0)
        if target <= 0:
            return 0
        from rl.volume_waves import parse_stages_arg, target_per_stage

        try:
            n_stages = len(parse_stages_arg(getattr(self.args, "stages", "")))
        except ValueError:
            return 0
        if n_stages <= 0:
            return 0
        return int(target_per_stage(target, n_stages))

    # ---- 产物出包：已搬到 `rl/loop_export.py::TrainingExport`（S4 第二十一刀）------
    #
    # 这一簇 4 个成员是「这一轮要给出去的东西」：TS 运行时 zip（`_ensure_ts_code`）· 离线计划里的
    # 动态采集块（`_volume_plan_block`）· 全离线任务包（`_export_offline_bundle`）· 权重归档
    # （`_export_weights`）。**本文件原来唯一一条方法间调用链**（`_export_offline_bundle` →
    # `_volume_plan_block`）就在这一簇里——搬走后本类**零方法间调用**（8 个成员全是叶子）。
    #
    # 方向：调用者依赖被调用者。调用者是 `RoundSteps`（`step_export_offline_bundle` /
    # `step_export_weights`）与远端腿（S4 第二十二刀后：`TrainingRemoteJob._remote_ppo_publish`
    # → `_ensure_ts_code`、`TrainingRemoteDrive._remote_run_segment` → `_volume_plan_block`），
    # 两者都在本类的基类之前 ⇒ 本簇挂
    # `TrainingSteps` 的**末位**基类（`class TrainingSteps(TrainingRemote, TrainingEval,
    # TrainingExport)`）。依据（实测）：4 个成员名在既有混入里零同名定义，末位追加不会被遮罩。

    # ------------------------------------------------- in-loop eval 墙钟（2026-09-17）
    # 整簇（`_eval_policy_cfg` / `_eval_join_soft_sec` / `_sweep_eval_tail` / `_join_eval` /
    # `_dispatch_delayed_eval` / `_eval_covered` / `_drain_pending_eval`，连同
    # `_eval_on_round` 的占位）已搬到 `TrainingEval`（`rl/loop_eval.py`，S4 第十七刀）——
    # 那是本类里第一条被搬走的方法间调用链；留下的成员全是叶子（产物出包那一簇里还有一条
    # `_export_offline_bundle` → `_volume_plan_block`，S4 第二十一刀一并搬去 `loop_export`）。

    def _record_iteration(self, it: int) -> None:
        """iteration 事件落账（字段契约在 rl/events.py::write_iteration）。

        R2a：写入后立刻并入 `LedgerView`（`_ledger_apply`）——视图因此始终 == 盘上
        账本，且没有第二次全文件扫描（用户 2026-09-18 裁决：每课只读一遍）。
        """
        self._ledger_apply(
            write_iteration(
                self._jsonl_path,
                self.args,
                it,
                self._report,
                {
                    "rollout_sec": self._rollout_sec,
                    "ppo_sec": self._ppo_sec,
                    "ppo_cloud_sec": self._ppo_cloud_sec,
                    # M0 统一计量（additive；本地/旧路径无此键 → None）。
                    "wire": getattr(self, "_wire", None),
                    "total_steps": self._total_steps,
                    "chunks_n": self._chunks_n,
                    "agg": self._agg,
                    "kl_cum": self._kl_cum,
                    "halted": self._halted_flag,
                    "dropped_games": self._dropped_games,
                    "waves": self._waves_n,
                    "load_sec": self._load_sec,
                    "tail_drain_sec": self._tail_drain_sec,
                    "eval_join_sec": self._eval_join_sec,
                    # 动态采集（None = 未开该模式；additive 字段，旧行无此键）
                    "transitions_target": self._volume_target,
                    "transitions_collected": self._volume_collected,
                    "transitions_capped": True if self._volume_capped else None,
                },
            )
        )
