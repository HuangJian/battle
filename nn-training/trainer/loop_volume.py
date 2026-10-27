"""loop_volume —— **动态采集（按样本量）编排** mixin（2026-09-25 从 trainer/loop_core.py 拆出，S4 第十八刀）。

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

`trainer/loop_round_steps.py` 里是 `class RoundSteps(TrainingVolume)` —— 本簇的**生产入口全部**在
`RoundSteps`：`step_course_iter` 调 `_iteration_pairs`，`step_rollout` 调 `_volume_active` /
`_volume_collect_continuous`。与 S4 第二步（`class TrainingSteps(TrainingRemote)`）同源的规则：
**调用者依赖被调用者** ⇒ 本簇当基类。

（`_volume_topup` 的**离散补波**规则自 2026-09-19 VOLUME_RULE_V2 起已退役：生产路径全部走
`_volume_collect_continuous`，`step_volume_topup` 只是 `STEP_ORDER` 要求的**保留空步**，已经不调
本簇任何方法。它仍留在此处，因为既有 e2e（`e2e/test_volume_e2e.py`）与单测
（`tests/worker/test_rollout_volume.py`）以 unbound 形式直接驱动它，而 `volume_waves` 的纯逻辑是那条
规则的可复算实现。）

为什么不「给 `TrainingLoop` 加一个基类」：那要改组合类 + 四个「继承真混入」的测试宿主，并让
`tests/trainer/test_loop_eval_split.py` 里「组合类三件套不变」那句失守。走 `RoundSteps` 这一侧，
`TrainingLoop.__bases__ == (RoundSteps, TrainingSteps, TrainingGuards)` 与全部既有守卫**一行不改**
（本刀零守卫改动，只有一处 patch 目标迁移，见下）。

## 状态归属：八个 volume 槽位随簇搬来（声明在 `TrainingVolume`）

`_volume_target` / `_volume_collected` / `_volume_waves` / `_volume_g0` / `_volume_est` /
`_volume_capped` / `_volume_stage_ests` / `_volume_stage_stats`：只被这一簇读写。
`TrainingLoop.__init__` 仍负责**赋值**
（它持有全部跨轮字段），本模块提供**声明**——混入的状态契约必须在每个文件里对 mypy 可见。

## ⚠ DI seam：`log` 是本模块**自己的**注入点（本刀唯一要迁的 patch 目标）

`_volume_topup` 的「未达标 / 触单关局数硬顶」等日志按**模块全局**解析 ⇒ 原先
`monkeypatch.setattr(trainer.loop_core, "log", …)`（`e2e/test_volume_e2e.py`）在搬家后**打不中**
（假绿：断言「未达标清单进日志」会当场红，但只补 `trainer.loop_core` 的写法是静默空操作）——
同名 seam 在两个命名空间里是两个各自真实的注入点，这是 S4 第二步的教训，已同步改到本模块。
`build_pairs` / `combine_reports` / `dispatch_rollout_phase` / `settled_stage_totals` /
`trailing_samples_per_game` 同理随簇搬来；`common.distribution` / `biz.volume_waves` / `biz.volume_quota` /
`biz.resume` 本来就只在**方法体内**延迟 import（逐字保留：它们是原有的 DI 面，测试 patch 的一直是
实现模块——`common.distribution` 是模块对象，补在哪个命名空间都算命中，所以顶层无需再导一次）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from biz.course import build_pairs
from common.log import log
from trainer.rollout_phase import begin_payload_parts, dispatch_rollout_phase
from worker.reports import combine_reports
from worker.resume import settled_stage_totals, trailing_samples_per_game


def _learned_samples_per_game(samples: int, games: int) -> int:
    """**本轮自己**已结算的局 → 局均 samples（自校准；`0` = 还学不到/不可用）。

    为什么需要它（plan/rollout-estfree-worksteal §4.1）：est 已从课程/CLI 面退场，首轮连
    历史都没有 ⇒ 第一小批只能「不猜」（按宽度满铺），而**这一批本身就是最好的标定源**：
    下一批起 `collected/games` 就是盘上事实，不需要任何估计。

    口径与 `worker.resume.trailing_samples_per_game` 同：只认已结算 shard 的 `nSamples`
    与**占过名额的局数**（掉局零样本 ⇒ samples=0 ⇒ 返回 0，让调用方继续「不猜」而不是
    拿一个被瞬时掉局拖低的数去反解局数）。纯函数，无 IO。
    """
    g = int(games)
    s = int(samples)
    return max(1, round(s / g)) if g > 0 and s > 0 else 0


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
    _state_init: Any
    _extra_wver: Any
    _report: Any
    _stream_meta: Any
    #: 由 `TrainingSteps` 提供、在本模块里被**调用**的 WAL 句柄（组合实例上动态解析）。
    #: 声明为 `Any` 的理由同 `trainer/loop_remote_push.py`：混入间互调的方法必须在每个文件里类型可见。
    _commit_journal: Any

    # ---- 本簇自己的状态（S4 第十八刀随簇搬来；只被这一簇读写）----------------------
    #: 动态采集（plan/dynamic-rollout-volume）：None = 本轮课程未开该模式。
    #: 零落盘（plan/payload-zero-disk）：本轮 shard 段账（`ShardSegments`，组合根建）。
    #: 补波与首波共用同一份——`begin` 对同一 it 重入无副作用。
    _payload_parts: Any
    _volume_target: int | None
    _volume_collected: int | None
    #: 本轮已跑的波次/批次数（初波 = 1）与初波每关局数（硬顶默认值依赖后者）。
    _volume_waves: int
    _volume_g0: int
    #: 局均 samples 估计（`_volume_est_samples` 的结果，循环里复用）。
    _volume_est: int
    _volume_capped: bool
    #: 2026-10-21：逐关配额全部达成 ⇒ 已熔断（`halt_event`）；池里剩下的候选**没有派出去**。
    _volume_halted: bool
    #: 分关 est_s（`_volume_stage_ests_map` 的结果，循环里复用）。
    _volume_stage_ests: dict[int, int] | None
    #: 分关采集账（规则版本 + 分关 collected/games/est_s/cap_games + 缺口）——收官后写，
    #: `TrainingSteps._record_iteration` 读进 iteration 事件。None = 本轮不在连续配额链。
    #: 这里必须**显式**声明：只在方法体里赋值会让 mypy 把类型推成 `dict[str, Any]`，
    #: 与 `TrainingSteps` 一侧的 `dict | None` 冲突（`trainer/loop_core.py` 多继承合并报错）。
    _volume_stage_stats: dict | None

    # ------------------------------------------------- 动态采集（按样本量）
    #
    # plan/dynamic-rollout-volume.plan.md：课程用 target_transitions 代替固定局数做
    # 配额，轮中按**已结算 transitions** 逐关补波。纯逻辑在 biz/volume_waves.py，
    # 账本在 biz/resume.settled_stage_totals；本节只做接线 + 日志 + WAL。
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
        from worker.volume_waves import parse_stages_arg

        raw = str(getattr(args, "stages", "") or "").strip()
        # P2-c（2026-09-15）：原先是 `parse_range(str(... or "0-3"))` —— 缺 --stages
        # 时静默退成硬编码 4 关。实测本腿 args.stages 恒有值（launch 期从课程
        # stages 派生）所以没踩到，但静默猜关数 = 分关配额分母错、采集量对不上
        # 目标而不报错。改成响亮退出：拿不到显式关集就别开动态采集。
        # `parse_stages_arg`（`biz.volume_waves` 的共享解析器，训练侧
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
        """局均 **samples** 估计：jsonl 的 trailing 均值（可 replay）；**无历史 ⇒ `0`（无估计）**。

        2026-10-12（plan/rollout-estfree-worksteal §4.1）：课程/CLI 的声明值已退场 —— est
        只剩「盘上历史派生量」一个身份，首轮无历史是**正常状态**（调用方走「不猜」档：按
        宽度满铺 / 初批每关 1 局），绝不再拿一个假估计去反解局数。

        量纲（2026-09-15 T9）：samples（jsonl 的 `samples` 字段 = nSamples 之和），
        **不是 ticks**——后者差 K 倍（x3 实测 samples/ticks≈0.1007），会让第二轮起
        采量偏离 10×（旧 `trailing_ticks_per_game` 就是这条 bug 的一半）。
        """
        return trailing_samples_per_game(self._jsonl_path, window=5)

    def _iteration_pairs(self, it: int) -> list[tuple[int, int]]:
        """本轮对局表：动态采集串行路径**不预排初波**（连续配额实时派发）；其余 build_pairs。

        volume 仍返回按全局 est 均分的保守表——仅供 `rollout_src=node` / export_bundle
        等需要固定 pairs 的路径；串行 ` _volume_collect_continuous` **不用**这张表。
        """
        if not self._volume_active():
            self._volume_target = None
            self._volume_collected = None
            return build_pairs(self.args, it, self._rotate_seed)
        from worker.volume_quota import target_per_stage
        from worker.volume_waves import initial_games, initial_wave_pairs

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
        self._volume_stage_stats = None  # 分关账：连续配额收官时写（不跑该链 ⇒ 留 None）
        # 每关 G0 局的初波前缀（= 连续配额流的前 G0 个 seed）。**与 `biz/plan.pairs_for`
        # 同一个函数**：云端重放的语料因此与本地集群同一轮的前缀批逐位相同（2026-09-22）。
        pairs = initial_wave_pairs(self._rotate_seed, it, stages=stages, games_per_stage=g0)
        log(
            f"[volume] it{it}: continuous quota mode target={target} "
            f"per_stage={target_per_stage(target, len(stages))} est_global={est} "
            f"（node/export 预排表 {len(pairs)} 局；串行路径按差额实时派发）"
        )
        return pairs

    def _volume_stage_ests_map(self, it: int) -> dict[int, int]:
        """分关 est_s：近轮盘上 shard 局均 nSamples，缺省回退全局 est。

        `it` = **正在跑的这一轮**：必须排除它。`step_prepare_iter` 在本轮开跑前就
        `mkdir(traj_root/it{it})`（`loop_iter_dir._prepare_iter_dir`），所以「mtime 最新的
        目录」在**每轮首批**恒等于这个空目录 ⇒ 不排除就会整份回退 `fallback`（全局单值），
        把分关估计静默抹平（plan/rollout-corpus-supply 评审 P0，2026-10-21）。
        """
        from worker.resume import trailing_stage_samples_per_game
        from worker.volume_waves import parse_stages_arg

        stages = self._volume_stages()
        fallback = int(self._volume_est or self._volume_est_samples() or 0)  # 0 = 无估计（首轮）
        raw = str(getattr(self.args, "stages", "") or "")
        try:
            stages = parse_stages_arg(raw) if raw else stages
        except ValueError:
            pass
        # `agg` 可选（`--est-agg`，2026-10-21 评审 #8）：latest = 最近一个真有 shard 的轮（缺省）；
        # pooled = 近 3 轮池化（旧口径）。两侧（本链 / `loop_export`）必须同值——不一致
        # 就是「同一轮两份反解」（云机拿到的 G_s 与本地派局不同源）。
        agg = str(getattr(self.args, "est_agg", "latest") or "latest")
        return trailing_stage_samples_per_game(
            self._traj_root,
            stages,
            window_iters=3,
            fallback=fallback,
            agg=agg,
            exclude_it=int(it),
        )

    def _dispatch_volume_wave(
        self,
        it: int,
        pairs: list[tuple[int, int]],
        dist_cfg: dict | None,
        *,
        # `Any` 而不是 `threading.Event`：本模块顶层不许 import threading（import 面闭集守卫），
        # 句柄由 `_volume_collect_continuous` 内部构造并透传——它只需要"能被 set / is_set"。
        halt_event: Any = None,
        on_result: Any = None,
        # `halt` 的来源：True = 逐关满额（候选池语义，没派的不算缺口）；
        # False = 外部熔断（KL 等，没派的局照旧报 missing）。
        pool_halt: bool = False,
    ) -> dict:
        """补波派发（复用首波同一派发路径，不另起调度器——计划 §3-P1）。

        `eval_on_round=False`：补波不重复派 eval（本轮评估已在首波派发/延迟派发处理，
        多派一次 = 重复评估 + 重复占集群）。stream 句柄一律丢弃（补波只走串行路径）。

        `halt_event` / `on_result`（2026-10-21 用户口径）：`on_result` 每结算一局回调一次
        ⇒ 调用方实时更新逐关账本；`halt_event` 一置位，dispatcher 的 worker 立即停止
        取新任务（池里没派出去的候选**从不运行**），在飞副本按既有 dedup 路径丢弃。
        """
        # 零落盘（plan/payload-zero-disk §3.2）：补波与首波记同一份段账（同一 it ⇒ 不清）。
        begin_payload_parts(self.args, it, self._payload_parts)
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
            halt_event=halt_event,
            on_result=on_result,
            on_shard=self._payload_parts.add,
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
        from worker.volume_waves import parse_wave_records

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
        if int(self._volume_est or 0) <= 0:
            # 无历史 ⇒ 无局均估计：**不猜**（plan/rollout-estfree-worksteal §4.1）。本波路径本来
            # 就已被连续配额取代（VOLUME_RULE_V2）；这里响亮拒绝而不是拿一个假值去解缺口方程。
            log(
                "[volume] wave 补波：无局均估计（首轮无历史）⇒ 不猜、不补波"
                "（连续配额路径会按宽度满铺）"
            )
            return
        import common.distribution
        from worker.volume_waves import (
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
            wver = common.distribution.weights_fingerprint(args.out)
            totals = settled_stage_totals(
                self._traj_dir,
                wver,
                extra_wver=self._extra_wver,
                course_fp=self._course_fp,
                corpus_fp=self._corpus_fp,
                state_init=self._state_init,
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
            from worker.reports import adopt_volume_report

            self._report = adopt_volume_report(None)
            return
        if int(getattr(args, "collect_only", 0) or 0):
            from worker.reports import adopt_volume_report

            self._report = adopt_volume_report(None)
            return
        import common.distribution
        from worker.resume import settled_stage_totals
        from worker.volume_alloc import (
            DEFAULT_POOL_MULT,
            VOLUME_ALLOC_RULE,
            default_game_caps,
            pick_stage_est,
            pool_games_by_stage,
            pull_est_low,
            quota_met,
            spread_games_by_stage,
        )
        from worker.volume_quota import (
            DEFAULT_MAX_BATCHES,
            continuous_pairs,
            target_per_stage,
        )

        stages = self._volume_stages()
        target = int(args.target_transitions)
        quota = target_per_stage(target, len(stages))
        est_global = int(self._volume_est or self._volume_est_samples() or 0)  # 0 = 无估计（首轮）
        est_s = self._volume_stage_ests_map(it)
        self._volume_stage_ests = est_s
        # 硬顶**分关**（2026-10-07，plan/rollout-stage-balance §4.2）：课程显式
        # `--max-games-per-stage` 优先；否则 `4 × ceil(quota/est_s)`。用全局 est 算 cap
        # 会让低 est 的关「配额未满就触顶」，而 DoD 把触顶当合格路径 ⇒ 静默短采。
        game_cap = int(getattr(args, "max_games_per_stage", 0) or 0)
        stage_caps = default_game_caps(stages, target, ests=est_s, explicit=game_cap)
        max_batches = int(getattr(args, "volume_max_batches", 0) or DEFAULT_MAX_BATCHES)
        # 冷启动满铺的「宽度」（plan §4.1）：= 本轮能同时跑的局数上限。顺序 = 本机直跑槽位 →
        # 机器 workers → 关数（退化：每关 1 局）。取不到也**不猜 est** —— 宁可批小（多一个屏障）。
        width = (
            int(getattr(args, "local_slots", 0) or 0)
            or int(getattr(args, "workers", 0) or 0)
            or len(stages)
        )
        course_fp = self._course_fp
        corpus_fp = self._corpus_fp
        extra_wver = self._extra_wver
        start_idx = {s: 0 for s in stages}
        combined: dict | None = None
        self._volume_capped = False
        self._volume_waves = 0
        # 首波的 est 读数（进 iteration 事件）：批数由**首波**决定，所以要记首波用了什么，
        # 而不是末波（末波总是「剩余缺口 / 新 est」，它好了不代表首波没浪费一批）。
        # 三个数必须一起记（2026-10-21 评审 P1）：
        #   est_raw  = 首波选中的估计**原值**（learned / trailing，未乘安全系数）
        #   est_used = 实际喂给 `plan_continuous_batch` 的值（= pull_est_low(est_raw)）
        #   est_s    = 盘上 trailing（最近一轮），与旧行同名字段同源
        # 只有 est_used 时，「高估是估计器的错还是 0.92 的错」分不开（P0-2 的偏差表算不出来）。
        first_est_used: dict[int, int] = {}
        first_est_raw: dict[int, int] = {}
        first_est_src: dict[int, str] = {}

        # ★★ 铺满 + 满额即停（用户口径 2026-10-21）：
        #   派出去的是一个**候选池**（缺口 × mult），不是"刚好够的局数"；dispatcher 的队列语义
        #   本来就是「空一个槽就从池里取下一个」，所以每个槽从 t=0 起满负荷、结算一局补一局；
        #   `_quota_watch`（on_result 回调）实时累加逐关样本，**逐关**全满额就置 `halt` ⇒ worker 立即停止取新任务
        #   ⇒ 池里剩下的候选**从不运行**（不是"派了再丢"）。在飞副本走既有 dedup 路径丢弃。
        import threading

        halt = threading.Event()
        live: dict[int, int] = {}
        stage_set = {int(s) for s in stages}
        pool_mult = float(getattr(args, "volume_pool_mult", 0) or DEFAULT_POOL_MULT)
        self._volume_halted = False

        def _quota_watch(summary: object) -> None:
            if not isinstance(summary, dict):
                return
            try:
                key = int(summary.get("stage"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            if key not in stage_set:
                return
            n = summary.get("nSamples")
            if isinstance(n, int) and n > 0:
                live[key] = live.get(key, 0) + int(n)
            # 判据是**逐关**（短局关不能靠长局关顶），与 `shortfall_by_stage` 同源
            if quota_met(stages=stages, collected=live, target_transitions=target):
                halt.set()

        while self._volume_waves < max_batches:
            wver = common.distribution.weights_fingerprint(args.out)
            totals = settled_stage_totals(
                self._traj_dir,
                wver,
                extra_wver=extra_wver,
                course_fp=course_fp,
                corpus_fp=corpus_fp,
                state_init=self._state_init,
            )
            collected = {s: int(totals.get(s, (0, 0))[1]) for s in stages}
            games_done = {s: int(totals.get(s, (0, 0))[0]) for s in stages}
            # 实时账本从盘上事实起步（**原地**更新：`_quota_watch` 持有的是同一个 dict）
            live.clear()
            live.update(collected)
            # 同步 dispatch：批间无在飞；批内调度器自己竞速。
            # （`inflight` 只喂给旧版 `plan_continuous_batch` 的软停；改成候选池后不再需要——
            #   满额判据由 `on_result` 的实时账本承担。）
            # 局均估计（plan/rollout-estfree-worksteal §4.1）：① **本轮自己**已结算的局
            # （自校准 — 冷启动第一小批之后就有值了）；② 前几轮的**盘上历史**（est_s，最近一轮）。
            # 顺序不能反（plan/rollout-corpus-supply §1.3/§3.1 A0-a）：旧代码是
            # `trailing or learned`，而 trailing 永远有值 ⇒ 自校准**恒被短路**，est 一直跟着
            # 历史窗口走（652 vs 实际 530，+23% ⇒ 少派局 ⇒ 多补一批）。
            # 两者都拿不到（首轮第一小批）⇒ **不猜**：按宽度满铺一把（est-free），下一批起重校准。
            est_s_now: dict[int, int] = {}
            est_src_now: dict[int, str] = {}
            for s in stages:
                est_s_now[s], est_src_now[s] = pick_stage_est(
                    _learned_samples_per_game(collected.get(s, 0), games_done.get(s, 0)),
                    int(est_s.get(s, 0) or 0),
                )
            est_now = int(est_global or 0) or _learned_samples_per_game(
                sum(collected.values()), sum(games_done.values())
            )
            # 派局用的 est 往低处拉（A0-c）：`ceil(缺口/est)` 因此多派 ~8% 局，把「局长继续变短」
            # 的趋势误差吃掉一部分。只动派局，**不动硬顶**（`stage_caps` 仍由原 est 算）。
            est_dispatch = {s: pull_est_low(v) for s, v in est_s_now.items()}
            est_srcs = set(est_src_now.values())
            est_src = next(iter(est_srcs)) if len(est_srcs) == 1 else "mixed"
            if est_now > 0:
                # ★ 2026-10-21（用户口径）：派的是**候选池**，不是"刚好够的局数"。
                #   池 = 缺口 × mult（默认 2）⇒ 队列里一直有活 ⇒ 每个槽从 t=0 起就满负荷；
                #   结算一局就补一局（`on_result` 实时更新 `live`），逐关一满额就 `halt`，
                #   池里剩下的候选**从不运行**。墙钟由「配额达成时刻」决定，与池多大无关，
                #   因此 est 的 ±40% 跳变不再换来「第二批」。
                plan_g = pool_games_by_stage(
                    stages=stages,
                    collected=collected,
                    games_done=games_done,
                    target_transitions=target,
                    ests=est_dispatch,
                    game_caps=stage_caps,
                    width=0,  # 池远大于槽位 ⇒ 不需要（也没法）知道集群有几个槽
                    mult=pool_mult,
                )
                short_note = {
                    s: quota - int(collected.get(s, 0))
                    for s in stages
                    if int(collected.get(s, 0)) < quota
                }
                plan_capped = False
                pool_note = f"池 {sum(plan_g.values())} 局（×{pool_mult}）"
            else:
                # 无估计（首轮无历史）⇒ 「不猜」档：与云机补差**同一个** est-free 原语满铺。
                plan_g = spread_games_by_stage(
                    stages=stages,
                    collected=collected,
                    games_done=games_done,
                    target_transitions=target,
                    game_caps=stage_caps,
                    width=width,
                )
                short_note = {
                    s: quota - int(collected.get(s, 0))
                    for s in stages
                    if int(collected.get(s, 0)) < quota
                }
                plan_capped = False
                est_src = "cold-start"
                est_src_now = {s: "cold-start" for s in stages}
                pool_note = f"池 {sum(plan_g.values())} 局（冷启动按宽度 {width} 铺）"
            if not plan_g:
                break
            # 首波读数：进 iteration 事件（`est_used` / `est_src`）—— 批数由首波决定，
            # 记末波的 est 等于「事后诸葛亮」，会读不出「首波高估了多少」。
            if not first_est_used:
                # 冷启动档（无 learned、无 trailing）根本不是「用了某个估计」：那批是
                # `spread_games_by_stage(width)` 按机器宽度铺的。记 `-1` 而不是 `0`
                # （0 在下游是「无估计」的值，会和「估出来是 0」混成一件事）。
                cold = all(v == "cold-start" for v in est_src_now.values())
                first_est_used = {s: (-1 if cold else int(est_dispatch.get(s, 0))) for s in stages}
                first_est_raw = {s: (-1 if cold else int(est_s_now.get(s, 0))) for s in stages}
                first_est_src = dict(est_src_now)
            pairs = continuous_pairs(self._rotate_seed, it, plan_g, start_idx)
            for s, n in plan_g.items():
                start_idx[s] = start_idx.get(s, 0) + int(n)
            self._volume_waves += 1
            log(
                f"[volume] it{it}: batch{self._volume_waves} "
                f"games={plan_g}（{pool_note}；差额 {short_note} "
                f"est_used={ {s: est_dispatch.get(s) for s in plan_g} } "
                f"est_raw={ {s: est_s_now.get(s) for s in plan_g} } src={est_src}"
                + (
                    f"，无历史 ⇒ 按宽度 {width} 满铺（不猜 est））"
                    if est_src == "cold-start"
                    else "）"
                )
            )
            wave_report = self._dispatch_volume_wave(
                it, pairs, dist_cfg, halt_event=halt, on_result=_quota_watch, pool_halt=True
            )
            combined = wave_report if combined is None else combine_reports([combined, wave_report])
            if plan_capped:
                self._volume_capped = True
            # ★ 满额即收工：池里剩下的候选**没有派出去**（不是"派了再丢"），
            #   所以这里直接退出循环——不会再有第二批，也没有第二批的固定开销。
            if halt.is_set():
                self._volume_halted = True
                break

        wver = common.distribution.weights_fingerprint(args.out)
        totals = settled_stage_totals(
            self._traj_dir,
            wver,
            extra_wver=extra_wver,
            course_fp=course_fp,
            corpus_fp=corpus_fp,
            state_init=self._state_init,
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
                # `est_s` = 盘上历史（最近一轮），与旧行的同名字段同源，保持不变；
                # `est_used` = **首波实际派局用的值**（已乘安全系数），`est_raw` = 乘系数**之前**
                # 的估计原值（learned / trailing），两者放在一起才读得出「首波高估了多少」，
                # 也才分得清「估计器错」与「0.92 系数错」（P0-2 的口径；2026-10-21 评审 P1）。
                # `-1` = 冷启动（那批不是按估计铺的，是按机器宽度满铺）。
                "est_s": int(est_s.get(s, est_global)),
                "est_used": int(first_est_used.get(s, 0)),
                "est_raw": int(first_est_raw.get(s, 0)),
                "est_src": first_est_src.get(s, "none"),
                "cap_games": int(stage_caps.get(s, 0)),
            }
            for s in stages
        }
        # 进 iteration 事件的**分关账**（2026-10-07，plan/rollout-stage-balance §4.2）：
        # 规则版本 + 分关 collected/games/est_s/cap_games + 缺口（硬指标）。为什么带上规则
        # 版本：分关分配不进 `corpus_identity_fp`（与 est 同类，用户拍板 ②），所以「哪一轮起
        # 换了分配口径」必须在**事件**里可读（§15.5 的精神：不许静默漂移，也不拒收在训课程）。
        # 顶层 `est_src`：只在全关同源时给具体值，否则 `mixed`（拼一个「首关的源」会把
        # 「一半 learned 一半 trailing」这轮最该看的情形抹平）。
        _srcs = set(first_est_src.values()) or {"none"}
        self._volume_stage_stats = {
            "rule": VOLUME_ALLOC_RULE,
            "est_src": next(iter(_srcs)) if len(_srcs) == 1 else "mixed",
            # 聚合口径也进事件：`--est-agg` 的 A/B（latest vs pooled）会产出**同一形状**的读数，
            # 不写这一格就分不清「哪一轮用的是哪个口径」（P0-2 的偏差表要按它分组）。
            "est_agg": str(getattr(self.args, "est_agg", "latest") or "latest"),
            # `halted` = 逐关配额全部达成、池里剩下的候选没派出去（2026-10-21 用户口径）
            "halted": bool(getattr(self, "_volume_halted", False)),
            "quota": int(quota),
            "stages": {str(s): stage_stats[s] for s in stages},
            "shortfall": {str(s): int(v) for s, v in unmet.items()},
        }
        log(
            f"[volume] it{it}: continuous 收官 batches={self._volume_waves} "
            f"collected={collected_total}/{target} 达标关="
            f"{len(stages) - len(unmet)}/{len(stages)} rule={VOLUME_ALLOC_RULE} stats={stage_stats}"
        )
        self._volume_collected = collected_total
        # 报告真源 = 本轮盘上 shard（与 settled_stage_totals 同源）；wave 只补时间锚点。
        # 禁止 combine 进上一轮 _report（pure_collect 起点会钉在历史波，§adopt_volume_report）。
        # 配额已满重启 ⇒ batches=0，若只 adopt(combined=None) 会把除零保护的 winRate=0
        # 写进账本（x20-steady it76 / §107）——必须从 shard 回填 outcomes。
        from worker.reports import merge_volume_report
        from worker.resume import resumed_manifests

        disk_reports = resumed_manifests(
            self._traj_dir,
            wver,
            extra_wver=extra_wver,
            course_fp=course_fp,
            state_init=self._state_init,
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
