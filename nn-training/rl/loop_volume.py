"""loop_volume —— **动态采集（按样本量）编排** mixin（2026-09-25 从 rl/loop_core.py 拆出，S4 第十八刀）。

这一簇是 `TrainingLoop` 里**唯一一条真正的方法间调用链**：9 个成员 / 445 行（占原模块 32%），
其余方法都是被主循环各自调用的叶子。链的形状 = **入口（轮内预排表）→ 关集/估计解算 →
逐关补波或连续配额派发 → 报告合并**（v2 起生产路径是 `_volume_collect_continuous`，
`_volume_topup` 的 wave 规则只留给旧 e2e 与 `--collect-only` 之外的历史路径）：

```
_iteration_pairs ─────┬─► _volume_active
（轮内预排表入口）      ├─► _volume_est_samples ─┐
                       └─► _volume_stages ──────┼─► _volume_stage_ests_map ─┐
                                               │                           │
_volume_topup ─┬─► _volume_journal_replay      │                           ├─► _dispatch_volume_wave
（wave 规则）  └─► _volume_active ──────────────┘                           │
_volume_collect_continuous（VOLUME_RULE_V2 生产路径）──► _dispatch_volume_wave
```

## 依赖方向：调用者依赖被调用者

`rl/loop_round_steps.py` 里是 `class RoundSteps(TrainingVolume)` —— 本簇的**生产入口全部**在
`RoundSteps`：`step_course_iter` 调 `_iteration_pairs`，`step_rollout` 调 `_volume_active` /
`_volume_collect_continuous`。与 S4 第二步（`class TrainingSteps(TrainingRemote)`）同源的规则：
**调用者依赖被调用者** ⇒ 本簇当基类。

（`_volume_topup` 的**离散补波**规则自 2026-09-19 VOLUME_RULE_V2 起已退役：生产路径全部走
`_volume_collect_continuous`，`step_volume_topup` 只是 `STEP_ORDER` 要求的**保留空步**，已经不调
本簇任何方法。它仍留在此处，因为既有 e2e（`e2e/test_volume_e2e.py`）与单测
（`tests/test_rollout_volume.py`）以 unbound 形式直接驱动它，而 `volume_waves` 的纯逻辑是那条
规则的可复算实现。）

为什么不「给 `TrainingLoop` 加一个基类」：那要改组合类 + 四个「继承真混入」的测试宿主，并让
`tests/test_loop_eval_split.py` 里「组合类三件套不变」那句失守。走 `RoundSteps` 这一侧，
`TrainingLoop.__bases__ == (RoundSteps, TrainingSteps, TrainingGuards)` 与全部既有守卫**一行不改**
（本刀零守卫改动，只有一处 patch 目标迁移，见下）。

## 状态归属：七个 volume 槽位随簇搬来（声明在 `TrainingVolume`）

`_volume_target` / `_volume_collected` / `_volume_waves` / `_volume_g0` / `_volume_est` /
`_volume_capped` / `_volume_stage_ests`：只被这一簇读写。`TrainingLoop.__init__` 仍负责**赋值**
（它持有全部跨轮字段），本模块提供**声明**——混入的状态契约必须在每个文件里对 mypy 可见。

## ⚠ DI seam：`log` 是本模块**自己的**注入点（本刀唯一要迁的 patch 目标）

`_volume_topup` 的「未达标 / 触单关局数硬顶」等日志按**模块全局**解析 ⇒ 原先
`monkeypatch.setattr(rl.loop_core, "log", …)`（`e2e/test_volume_e2e.py`）在搬家后**打不中**
（假绿：断言「未达标清单进日志」会当场红，但只补 `rl.loop_core` 的写法是静默空操作）——
同名 seam 在两个命名空间里是两个各自真实的注入点，这是 S4 第二步的教训，已同步改到本模块。
`build_pairs` / `combine_reports` / `dispatch_rollout_phase` / `settled_stage_totals` /
`trailing_samples_per_game` 同理随簇搬来；`dist_common` / `rl.volume_waves` / `rl.volume_quota` /
`rl.resume` 本来就只在**方法体内**延迟 import（逐字保留：它们是原有的 DI 面，测试 patch 的一直是
实现模块——`dist_common` 是模块对象，补在哪个命名空间都算命中，所以顶层无需再导一次）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rl.course import build_pairs
from rl.log import log
from rl.reports import combine_reports
from rl.resume import settled_stage_totals, trailing_samples_per_game
from rl.rollout_phase import dispatch_rollout_phase


class TrainingVolume:
    """动态采集（按样本量）编排 mixin：初波/补波/连续配额 → 派发 → 报告合并 → WAL。

    被 `RoundSteps` 继承（调用者依赖被调用者）；只有组合类 `TrainingLoop` 会被实例化。
    """

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop /
    # TrainingSteps）。与其它混入里的同类声明**有意并存**：混入的状态契约必须在**每个**
    # 文件里对 mypy 可见，否则本文件里的 `self._report` 会被判成未声明属性。用 `Any`
    # 而不是精确类型：同一名字在 `TrainingSteps` / `RoundSteps` 里已各有声明，精确类型
    # 反而会撞成「多继承定义不兼容」（`Any` 与任何类型相容）。
    args: Any
    bun: Any
    ppo_backend: Any
    update_kwargs: Any
    _model: Any
    _opt: Any
    _device: Any
    _ref_model: Any
    _start_it: Any
    _traj_root: Any
    _traj_dir: Any
    _jsonl_path: Any
    _rotate_seed: Any
    _course_fp: Any
    _corpus_fp: Any
    _extra_wver: Any
    _report: Any
    _stream_meta: Any
    #: 由 `TrainingSteps` 提供、在本模块里被**调用**的 WAL 句柄（组合实例上动态解析）。
    #: 声明为 `Any` 的理由同 `rl/loop_remote.py`：混入间互调的方法必须在每个文件里类型可见。
    _commit_journal: Any

    # ---- 本簇自己的状态（S4 第十八刀随簇搬来；只被这一簇读写）----------------------
    #: 动态采集（plan/dynamic-rollout-volume）：None = 本轮课程未开该模式。
    _volume_target: int | None
    _volume_collected: int | None
    #: 本轮已跑的波次/批次数（初波 = 1）与初波每关局数（硬顶默认值依赖后者）。
    _volume_waves: int
    _volume_g0: int
    #: 局均 samples 估计（`_volume_est_samples` 的结果，循环里复用）。
    _volume_est: int
    _volume_capped: bool
    #: 分关 est_s（`_volume_stage_ests_map` 的结果，循环里复用）。
    _volume_stage_ests: dict[int, int] | None

    # ------------------------------------------------- 动态采集（按样本量）
    #
    # plan/dynamic-rollout-volume.plan.md：课程用 target_transitions 代替固定局数做
    # 配额，轮中按**已结算 transitions** 逐关补波。纯逻辑在 rl/volume_waves.py，
    # 账本在 rl/resume.settled_stage_totals；本节只做接线 + 日志 + WAL。
    #
    # v1 边界（计划 §3-P0/P1 明确）：只接串行路径；stream/double-buffer 保持老语义
    # （不再动它是为了让双缓冲那套墙钟优化不被这一版搅动）。

    def _volume_active(self) -> bool:
        """本课程是否开了动态采集（`target_transitions > 0`；缺席 = 一个函数都不调）。"""
        return int(getattr(self.args, "target_transitions", 0) or 0) > 0

    def _volume_stages(self) -> list[int]:
        """动态采集的关集 = 课程声明的 stages（分关配额的分母）。

        门控窗口（curriculum/rotate）与配额分关 v1 不兼容：两者的「本轮实际采样关」
        是 it 的函数，而配额按固定关集反解——硬凑会让分关达标线与现实不符。响亮
        SystemExit 而不是静默取一个关集（静默错分 = 采集量对不上目标，最难发现那种）。
        """
        args = self.args
        if (
            str(getattr(args, "curriculum_stages", "") or "")
            or int(getattr(args, "rotate_stages", 0) or 0) > 0
        ):
            raise SystemExit(
                "[volume] target_transitions 与 --curriculum-stages / --rotate-stages 的"
                "门控窗口 v1 不兼容（配额按课程声明的关集分关）——改用显式 --stages"
            )
        from rl.volume_waves import parse_stages_arg

        raw = str(getattr(args, "stages", "") or "").strip()
        # P2-c（2026-09-15）：原先是 `parse_range(str(... or "0-3"))` —— 缺 --stages
        # 时静默退成硬编码 4 关。实测本腿 args.stages 恒有值（launch 期从课程
        # stages 派生）所以没踩到，但静默猜关数 = 分关配额分母错、采集量对不上
        # 目标而不报错。改成响亮退出：拿不到显式关集就别开动态采集。
        # `parse_stages_arg`（`rl.volume_waves` 的共享解析器，训练侧
        # `_per_stage_quota` 同源）对空串/不可解析串抛 ValueError（不是返回空集）
        # ⇒ 这里先挡空串、再兜住 ValueError，两条路都收敛到同一条 SystemExit 文案。
        if not raw:
            raise SystemExit(
                "[volume] --stages 缺席，无法分关配额（动态采集不接受硬编码 fallback "
                "关集——请显式传 --stages，或关掉 target_transitions）"
            )
        try:
            stages = parse_stages_arg(raw)
        except ValueError as exc:
            raise SystemExit(
                f"[volume] --stages={raw!r} 无法解析为关号列表（{exc}）——动态采集的"
                "分关配额需要一个明确的关集"
            ) from exc
        if not stages:
            raise SystemExit(f"[volume] --stages={raw!r} 解析为空集，无法分关配额（请显式传关号）")
        return stages

    def _volume_est_samples(self) -> int:
        """局均 **samples** 估计：jsonl 的 trailing 均值（可 replay），无历史落课程声明值。

        量纲（2026-09-15 T9）：samples（jsonl 的 `samples` 字段 = nSamples 之和），
        **不是 ticks**——后者差 K 倍（x3 实测 samples/ticks≈0.1007），会让第二轮起
        采量偏离 10×（旧 `trailing_ticks_per_game` 就是这条 bug 的一半）。
        """
        declared = int(getattr(self.args, "est_samples_per_game", 0) or 0)
        return trailing_samples_per_game(self._jsonl_path, window=5, fallback=declared)

    def _iteration_pairs(self, it: int) -> list[tuple[int, int]]:
        """本轮对局表：动态采集串行路径**不预排初波**（连续配额实时派发）；其余 build_pairs。

        volume 仍返回按全局 est 均分的保守表——仅供 `rollout_src=node` / export_bundle
        等需要固定 pairs 的路径；串行 ` _volume_collect_continuous` **不用**这张表。
        """
        if not self._volume_active():
            self._volume_target = None
            self._volume_collected = None
            return build_pairs(self.args, it, self._rotate_seed)
        from rl.volume_quota import target_per_stage
        from rl.volume_waves import initial_games, initial_wave_pairs

        args = self.args
        stages = self._volume_stages()
        est = self._volume_est_samples()
        target = int(args.target_transitions)
        g0 = initial_games(target, len(stages), est)
        self._volume_target = target
        self._volume_g0 = g0
        self._volume_est = est
        self._volume_waves = 0
        self._volume_capped = False
        self._volume_stage_ests = None  # 连续采集启动时现算
        # 每关 G0 局的初波前缀（= 连续配额流的前 G0 个 seed）。**与 `rl/plan.pairs_for`
        # 同一个函数**：云端重放的语料因此与本地集群同一轮的前缀批逐位相同（2026-09-22）。
        pairs = initial_wave_pairs(self._rotate_seed, it, stages=stages, games_per_stage=g0)
        log(
            f"[volume] it{it}: continuous quota mode target={target} "
            f"per_stage={target_per_stage(target, len(stages))} est_global={est} "
            f"（node/export 预排表 {len(pairs)} 局；串行路径按差额实时派发）"
        )
        return pairs

    def _volume_stage_ests_map(self) -> dict[int, int]:
        """分关 est_s：近轮盘上 shard 局均 nSamples，缺省回退全局 est。"""
        from rl.resume import trailing_stage_samples_per_game
        from rl.volume_waves import parse_stages_arg

        stages = self._volume_stages()
        fallback = int(self._volume_est or self._volume_est_samples() or 1)
        raw = str(getattr(self.args, "stages", "") or "")
        try:
            stages = parse_stages_arg(raw) if raw else stages
        except ValueError:
            pass
        return trailing_stage_samples_per_game(
            self._traj_root, stages, window_iters=3, fallback=fallback
        )

    def _dispatch_volume_wave(
        self, it: int, pairs: list[tuple[int, int]], dist_cfg: dict | None
    ) -> dict:
        """补波派发（复用首波同一派发路径，不另起调度器——计划 §3-P1）。

        `eval_on_round=False`：补波不重复派 eval（本轮评估已在首波派发/延迟派发处理，
        多派一次 = 重复评估 + 重复占集群）。stream 句柄一律丢弃（补波只走串行路径）。
        """
        (report, stream_meta, _thread, _gate, _child, _early) = dispatch_rollout_phase(
            self.args,
            self.bun,
            dist_cfg,
            it,
            self._traj_dir,
            pairs,
            self._jsonl_path,
            self._model,
            self._opt,
            self._device,
            self.ppo_backend,
            self.update_kwargs,
            self._start_it,
            self._ref_model,
            self._extra_wver,
            False,
            course_fp=self._course_fp,
            corpus_fp=self._corpus_fp,
        )
        if stream_meta is not None:
            raise SystemExit("[volume] 补波落到 stream 路径——v1 只支持串行路径")
        return report

    def _volume_journal_replay(self, it: int) -> Any:
        """读 WAL：把本迭代的波次预算推到现在，并返回**未闭环**的那一波（待重放）。

        为何不能只靠账本重算（2026-09-15 e2e 证伪）：`wave_idx` 是**决策**而不是账本的
        函数——它同时是种子流的键（`[rotate_seed, tag, it, stage, wave]`）。重启后计数器
        若从 1 重头，就会用 wave-1 的种子去补 wave-3 的缺口 = 同观测史、不同波次序列
        （正是 plan §2.3.1 要禁止的「重新抛硬币」）。所以：
          · 波次预算按 WAL 续算（跨重启**不重领额度**，防「崩了就拿新一轮 3 波」）；
          · 停在波次中间（有 start 无 finish 的最后那一波）→ 原样重放它的对局表
            （同 wave_idx ⇒ 同种子流；已结算的由调度器剔除，缺口原样补齐）。
        """
        from rl.volume_waves import parse_wave_records

        path = Path(self._traj_dir) / "commit_journal.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        recs = parse_wave_records(lines, it)
        if not recs:
            return None
        last = recs[max(recs)]
        self._volume_waves = max(self._volume_waves, last.wave_idx + 1)
        return None if last.finished else last

    def _volume_topup(self, it: int, dist_cfg: dict | None) -> None:
        """按已结算 transitions 逐关补波直到达标/触顶（计划 §2.2.3/2.2.4）。

        账本口径：`settled_stage_totals`（只数已落盘 manifest 的 nSamples）——掉局零
        样本天然触发补采；超时局的 transitions 是真实 on-policy 数据，计入。
        每波决策进 WAL（`volume_wave` 相位）：预算跨重启续算（不重领额度），停在波次
        中间的那一波按 WAL 的对局表原样重放。**不能只按账本重算**——`wave_idx` 是决策
        而非账本的函数，它同时是种子流的键（首版「纯函数就够、无需重放」的假设已被
        e2e 证伪，见 `_volume_journal_replay`）。
        """
        if not self._volume_active():
            return
        args = self.args
        if self._stream_meta is not None:
            log("[volume] 流式路径 v1 不补波（保持老语义）——本轮只按初波结算，配额缺口不在本轮补齐")
            return
        if int(getattr(args, "collect_only", 0) or 0):
            return
        import dist_common
        from rl.volume_waves import (
            DEFAULT_MAX_WAVES,
            WAVE_PHASE,
            plan_topup,
            wave_pairs,
            wave_round_key,
        )

        stages = self._volume_stages()
        est = self._volume_est
        cap = int(getattr(args, "max_games_per_stage", 0) or 0)
        target = int(args.target_transitions)
        journal = self._commit_journal()
        collected_total = 0
        # 重启续跑：先重放 WAL 里那一波未完成的决策（同 wave_idx + 同对局表 ⇒ 同种子流；
        # 已结算的由调度器剔除），再按账本继续后面的波。
        replay = self._volume_journal_replay(it)
        if replay is not None and replay.games:
            replay_pairs = wave_pairs(self._rotate_seed, it, replay.games, replay.wave_idx)
            log(
                f"[volume] it{it}: WAL 重放未完成的补波 w{replay.wave_idx} "
                f"games={replay.games} → {len(replay_pairs)} 局（同种子流，不重抛硬币）"
            )
            replay_report = self._dispatch_volume_wave(it, replay_pairs, dist_cfg)
            self._report = combine_reports([self._report, replay_report])
        while True:
            wver = dist_common.weights_fingerprint(args.out)
            totals = settled_stage_totals(
                self._traj_dir,
                wver,
                extra_wver=self._extra_wver,
                course_fp=self._course_fp,
                corpus_fp=self._corpus_fp,
            )
            collected = {s: totals.get(s, (0, 0))[1] for s in stages}
            games_done = {s: totals.get(s, (0, 0))[0] for s in stages}
            collected_total = sum(collected.values())
            plan = plan_topup(
                stages=stages,
                collected=collected,
                target_transitions=target,
                est_samples_per_game=est,
                waves_done=self._volume_waves,
                games_done=games_done,
                max_waves=DEFAULT_MAX_WAVES,
                max_games_per_stage=cap,
                initial_g0=self._volume_g0,
            )
            if not plan.games_by_stage:
                met = [s for s, why in plan.stopped.items() if why == "quota_met"]
                unmet = {s: why for s, why in plan.stopped.items() if why != "quota_met"}
                if any(why == "game_cap" for why in unmet.values()):
                    # 硬顶 = 配额未满但停采。必须响亮：否则「采够了」与「踩顶了」在
                    # 日志上长得一模一样，而这正是长短局失衡 + est 偏差的指纹。
                    self._volume_capped = True
                    log(
                        f"[volume] WARN it{it}: 触单关局数硬顶"
                        f"（cap={cap or self._volume_g0 * 4}）但配额未满——"
                        f"shortfall={plan.shortfall}；已停采，iteration 事件打 capped 标"
                    )
                log(
                    f"[volume] it{it}: 补波收官 waves={self._volume_waves} "
                    f"collected={collected_total}/{target} samples "
                    f"达标关={len(met)}/{len(stages)}" + (f" 未达标={unmet}" if unmet else "")
                )
                break
            pairs = wave_pairs(self._rotate_seed, it, plan.games_by_stage, plan.wave_idx)
            round_key = wave_round_key(it, plan.wave_idx)
            journal.start(
                WAVE_PHASE,
                round_key,
                wave_idx=plan.wave_idx,
                games={str(s): n for s, n in plan.games_by_stage.items()},
                collected={str(s): collected[s] for s in stages},
                shortfall={str(s): plan.shortfall.get(s, 0) for s in stages},
                target=target,
                est=est,
            )
            log(
                f"[volume] it{it}: 补波 w{plan.wave_idx} "
                f"games={plan.games_by_stage}（缺口 {plan.shortfall}）→ {len(pairs)} 局"
            )
            wave_report = self._dispatch_volume_wave(it, pairs, dist_cfg)
            # 报告合并（既有多轮聚合口径：combine_reports 吃单轮报告，与远端单局摘要同构）
            self._report = combine_reports([self._report, wave_report])
            self._volume_waves = plan.wave_idx + 1
            journal.finish(
                WAVE_PHASE,
                round_key,
                games={str(s): n for s, n in plan.games_by_stage.items()},
            )
            if plan.capped:
                # 硬顶 = 配额未满但停采：必须响亮（否则「采够了」与「踩顶了」在日志上
                # 长得一模一样，而这正是长短局失衡 + est 偏差的指纹）。
                self._volume_capped = True
                log(
                    f"[volume] WARN it{it}: 触单关局数硬顶（cap={cap or self._volume_g0 * 4}）"
                    f"但配额未满——shortfall={plan.shortfall}；已停采该关，"
                    "iteration 事件打 transitions_capped"
                )
        self._volume_collected = collected_total
        # it 级 rollout 聚合（2026-09-19）：combine_reports 已把各波
        # weights_dist_start_ts / collect_end_ts 压成 pure_collect_sec
        # （首波分发 → 全部样本齐）。多波时打一条便于对照 dashboard。
        if self._volume_waves > 1 and self._report.get("pure_collect_sec") is not None:
            agg_ok = self._report.get("rollout_collect_aggregated")
            log(
                f"[volume] it{it}: rollout 聚合 waves={self._report.get('rollout_collect_waves', self._volume_waves)} "
                f"pure_collect_sec={self._report['pure_collect_sec']}"
                f"（首波分发→样本齐；aggregated={agg_ok}）"
            )

    def _volume_collect_continuous(self, it: int, dist_cfg: dict | None) -> None:
        """配额感知连续采集（2026-09-19 用户指令；VOLUME_RULE_V2）。

        **退役离散补波**：loop 读账本 → 按各关 `quota - collected - inflight*est_s`
        决定下一批局数 → 派发 → 再读账本，直到各关达标 / game_cap / batch 安全阀。
        即将足额（软停）不再派；差额大的关多派。种子 = `(it, stage, 本轮第k局)`。

        §15.5：相对 wave 规则的新语料语义 —— 课程若从 wave 迁到 continuous，
        应 fresh `--out/--traj`（用户已知）。
        """
        if not self._volume_active():
            return
        args = self.args
        if self._stream_meta is not None:
            log("[volume] 流式路径不支持连续配额 v2（保持 stream 老语义）")
            from rl.reports import adopt_volume_report

            self._report = adopt_volume_report(None)
            return
        if int(getattr(args, "collect_only", 0) or 0):
            from rl.reports import adopt_volume_report

            self._report = adopt_volume_report(None)
            return
        import dist_common
        from rl.resume import settled_stage_totals
        from rl.volume_quota import (
            DEFAULT_MAX_BATCHES,
            continuous_pairs,
            default_game_cap,
            plan_continuous_batch,
            target_per_stage,
        )

        stages = self._volume_stages()
        target = int(args.target_transitions)
        quota = target_per_stage(target, len(stages))
        est_global = int(self._volume_est or self._volume_est_samples() or 1)
        est_s = self._volume_stage_ests_map()
        self._volume_stage_ests = est_s
        game_cap = int(getattr(args, "max_games_per_stage", 0) or 0)
        if game_cap <= 0:
            game_cap = default_game_cap(quota, est_global)
        max_batches = int(getattr(args, "volume_max_batches", 0) or DEFAULT_MAX_BATCHES)
        course_fp = self._course_fp
        corpus_fp = self._corpus_fp
        extra_wver = self._extra_wver
        start_idx = {s: 0 for s in stages}
        combined: dict | None = None
        self._volume_capped = False
        self._volume_waves = 0

        while self._volume_waves < max_batches:
            wver = dist_common.weights_fingerprint(args.out)
            totals = settled_stage_totals(
                self._traj_dir,
                wver,
                extra_wver=extra_wver,
                course_fp=course_fp,
                corpus_fp=corpus_fp,
            )
            collected = {s: int(totals.get(s, (0, 0))[1]) for s in stages}
            games_done = {s: int(totals.get(s, (0, 0))[0]) for s in stages}
            # 同步 dispatch：批间无在飞；批内调度器自己竞速。软停用批前账本。
            inflight = {s: 0 for s in stages}
            plan = plan_continuous_batch(
                stages=stages,
                collected=collected,
                inflight=inflight,
                target_transitions=target,
                ests=est_s,
                games_done=games_done,
                game_cap=game_cap,
                fallback_est=est_global,
            )
            if not plan.games_by_stage:
                break
            pairs = continuous_pairs(
                self._rotate_seed, it, plan.games_by_stage, start_idx
            )
            for s, n in plan.games_by_stage.items():
                start_idx[s] = start_idx.get(s, 0) + int(n)
            self._volume_waves += 1
            short_note = {s: plan.shortfall.get(s, 0) for s in stages if plan.shortfall.get(s, 0)}
            log(
                f"[volume] it{it}: batch{self._volume_waves} "
                f"games={plan.games_by_stage}（差额 {short_note} "
                f"est_s={ {s: est_s.get(s) for s in plan.games_by_stage} }）"
            )
            wave_report = self._dispatch_volume_wave(it, pairs, dist_cfg)
            combined = (
                wave_report if combined is None else combine_reports([combined, wave_report])
            )
            if plan.capped:
                self._volume_capped = True

        wver = dist_common.weights_fingerprint(args.out)
        totals = settled_stage_totals(
            self._traj_dir,
            wver,
            extra_wver=extra_wver,
            course_fp=course_fp,
            corpus_fp=corpus_fp,
        )
        collected_total = sum(int(totals.get(s, (0, 0))[1]) for s in stages)
        unmet = {
            s: quota - int(totals.get(s, (0, 0))[1])
            for s in stages
            if int(totals.get(s, (0, 0))[1]) < quota
        }
        if unmet:
            self._volume_capped = True
            log(
                f"[volume] WARN it{it}: 连续配额未满 batches={self._volume_waves}/{max_batches} "
                f"unmet={unmet} per_stage_quota={quota} game_cap={game_cap}——"
                f"est 持续低估或触硬顶（非静默；pooled={collected_total}/{target}）"
            )
        stage_stats = {
            s: {
                "collected": int(totals.get(s, (0, 0))[1]),
                "games": int(totals.get(s, (0, 0))[0]),
                "est_s": int(est_s.get(s, est_global)),
            }
            for s in stages
        }
        log(
            f"[volume] it{it}: continuous 收官 batches={self._volume_waves} "
            f"collected={collected_total}/{target} 达标关="
            f"{len(stages) - len(unmet)}/{len(stages)} stats={stage_stats}"
        )
        self._volume_collected = collected_total
        # 报告真源 = 本轮盘上 shard（与 settled_stage_totals 同源）；wave 只补时间锚点。
        # 禁止 combine 进上一轮 _report（pure_collect 起点会钉在历史波，§adopt_volume_report）。
        # 配额已满重启 ⇒ batches=0，若只 adopt(combined=None) 会把除零保护的 winRate=0
        # 写进账本（x20-steady it76 / §107）——必须从 shard 回填 outcomes。
        from rl.reports import merge_volume_report
        from rl.resume import resumed_manifests

        disk_reports = resumed_manifests(
            self._traj_dir,
            wver,
            extra_wver=extra_wver,
            course_fp=course_fp,
        )
        if stages:
            stage_set = {int(s) for s in stages}
            filtered: list[dict] = []
            for r in disk_reports:
                st = r.get("stage")
                if st is None or int(st) in stage_set:
                    filtered.append(r)
            disk_reports = filtered
        self._report = merge_volume_report(combined, disk_reports)
        if self._volume_waves > 0 and self._report.get("pure_collect_sec") is not None:
            log(
                f"[volume] it{it}: rollout 聚合 batches={self._report.get('rollout_collect_waves', self._volume_waves)} "
                f"pure_collect_sec={self._report['pure_collect_sec']}"
                f"（首批分发→样本齐；aggregated={self._report.get('rollout_collect_aggregated')}）"
            )
