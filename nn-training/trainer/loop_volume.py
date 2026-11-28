"""loop_volume —— 动态采集（按样本量）编排：协作对象 `VolumePlanner` + 宿主协议 `VolumeHost`
（P0-1 第五刀，2026-11-28；前身是 S4 第十八刀的 `TrainingVolume` 混入）。

这一簇是回合里唯一一条真正的方法间调用链：**入口（轮内预排表）→ 关集/估计解算 →
逐关补波或连续配额派发 → 报告合并 → WAL**（v2 起生产路径是 `volume_collect_continuous`；
`volume_topup` 的 wave 规则只留给旧 e2e / 单测）：

```
iteration_pairs ──────┬─► volume_active
（轮内预排表入口）     ├─► volume_est_samples ─┐
                      └─► volume_stages ──────┼─► volume_stage_ests_map ─┐
                                              │                          │
volume_topup ─┬─► volume_journal_replay       │                          ├─► dispatch_volume_wave
（wave 规则）  └─► volume_active ──────────────┘                          │
volume_collect_continuous（生产路径）──► dispatch_volume_wave ────────────┘
volume_collect_greedy（结果配额）──► greedy_scan / greedy_budget / greedy_wave_pairs / greedy_settle
```

## 形状：状态归对象，宿主只给读面

15 个槽位只在 `VolumePlanner.__init__` 赋值；宿主经 `VolumeHost` 协议提供**活读数**
（`report` / `stream_meta` / `traj_dir` / `rotate_seed` … 全是 property，不拷副本），写面只有
`land_collect` / `commit_journal`。组合根 `TrainingLoop` 保留旧方法名的一行转发壳，
所以既有调用点与 patch 点零改动；装配在 `TrainingLoop.__init__` 的一行。

外簇（`loop_steps` / `loop_round_steps` / `loop_remote_job` …）只能经只读视图
（`volume_view` / `greedy_view`）读、经三个命令（`reset_greedy` / `reset_ordinary_seeds` /
`merge_greedy`）写——私有槽位不是公共 API。

## ⚠ DI seam：`log` 是**本模块**的注入点

同名 seam 在两个命名空间里是两个各自真实的注入点：往 `trainer.loop_core.log` 打补丁对这里
是**静默空操作**（S4 第二步的教训）。`build_pairs` / `combine_reports` /
`dispatch_rollout_phase` / `settled_stage_totals` / `trailing_samples_per_game` 同理随簇住在
本模块；`common.distribution` / `worker.volume_waves` / `worker.volume_quota` / `worker.resume`
只在**方法体内**延迟 import（原有的 DI 面，测试 patch 的一直是实现模块）。


"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol

from biz.course import build_pairs
from common.log import log
from remote.payload_pack import ShardSegments
from trainer.rollout_phase import (
    RolloutPhaseSpec,
    begin_payload_parts,
    dispatch_rollout_phase,
)
from worker.commit_journal import CommitJournal
from worker.reports import combine_reports
from worker.resume import settled_stage_totals, trailing_samples_per_game
from worker.volume_wave_plan import (
    ContinuousState,
    learned_samples_per_game,
    plan_continuous_wave,
    remember_first_est,
)

if TYPE_CHECKING:  # 仅类型面：这两个模块是延迟 import 的 DI 目标（运行期不加载）
    import threading

    from worker.greedy_quota import GreedyQuotaPlan
    from worker.volume_waves import WaveRecord


@dataclass(frozen=True, slots=True)
class VolumeView:
    """本轮采集的**只读观测面**（配额进度 / 分关账 / greedy 读数）。

    `_volume_*` 与 `_greedy_*` 是这一簇的实现状态：它们在方法之间传递、也会逐波改写。
    读它们（尤其是断言它们）原本只能伸进实例私有槽位；本对象是那条链的公开读数 ——
    字段名与 `worker.events` 的 iteration 字段同义，是**观测契约**，不是内部布局的镜像。
    `report` / `stage_stats` / `greedy_stats` 是活引用（dict 不复制）：调用方只读。
    """

    report: dict
    target: int | None
    collected: int | None
    waves: int
    g0: int
    est: int
    capped: bool
    halted: bool
    stage_stats: dict | None
    rotation_seed: int
    greedy_plan: object | None
    greedy_stats: dict | None
    greedy_waves: int


@dataclass(frozen=True, slots=True)
class GreedyView:
    """greedy 采集槽位的只读快照（采集阶段写、发布阶段读、跨轮清零）。

    `report` 是发布阶段的留痕（`_greedy_report`）；跨轮清零是契约（旧槽位不许漏进下一轮
    的发布预筛），而这条契约原本只能伸进 `_greedy_*` 私有槽位去断言。
    """

    plan: object | None
    stats: dict | None
    report: dict | None
    waves: int


def _greedy_collected(
    manifests: dict, stages: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """按 greedy shard manifest 累计每关样本/局数（自 `_volume_collect_greedy` 搬出）。"""
    from worker.greedy_quota import is_greedy_manifest, manifest_samples

    collected = {s: 0 for s in stages}
    games = {s: 0 for s in stages}
    for _name, mm in manifests.items():
        if mm is None or not is_greedy_manifest(mm):
            continue
        raw_stage = mm.get("stage")
        if not isinstance(raw_stage, (int, float, str)):
            continue
        try:
            st = int(raw_stage)
        except (TypeError, ValueError):
            continue
        if st not in collected:
            continue
        n = manifest_samples(mm)
        if n > 0:
            collected[st] += n
            games[st] += 1
    return collected, games


def _greedy_plan_games(
    stages, collected, games, est_s, est_global, game_caps, width, target: int
) -> dict:
    """这一波的逐关派发局数（自 `_volume_collect_greedy` 搬出；本地池按缺口 1× 派）。"""
    from worker.volume_alloc import pool_games_by_stage, spread_games_by_stage

    learned = {
        s: learned_samples_per_game(collected.get(s, 0), games.get(s, 0)) for s in stages
    }
    has_est = bool(est_global) or any(int(est_s.get(s, 0) or 0) > 0 for s in stages)
    if any(learned.values()) or has_est:
        est_dispatch = {
            s: max(1, int(learned.get(s) or est_s.get(s, 0) or est_global or 0))
            for s in stages
        }
        # 本地串行池**一次跑完整批**（halt/on_result 只在整批结束后回来），
        # 所以候选池必须按缺口 1× 派；×2 的候选会全部落盘 = 2× collect_target
        # （2026-11-09 S3 实测 18407/8602）。远端队列仍靠 halt 逐局熔断，
        # 少派候选不会改变墙钟，只少一层无用的排队。
        return pool_games_by_stage(
            stages=stages,
            collected=collected,
            games_done=games,
            target_transitions=target,
            ests=est_dispatch,
            game_caps=game_caps,
            width=0,
            mult=1.0,
        )
    # 冷启动（无历史、无本轮结算）⇒ 按机器宽度满铺，不猜 est。
    return spread_games_by_stage(
        stages=stages,
        collected=collected,
        games_done=games,
        target_transitions=target,
        game_caps=game_caps,
        width=width,
    )


@dataclass(frozen=True)
class _GreedyTargets:
    """greedy 计划的**只读标量面**：读数与硬目标判据要的那五个数。"""

    total_target: int
    collect_target: int
    per_stage_target: int
    cap: float
    soft: bool


@dataclass(frozen=True)
class _GreedyBudget:
    """开跑前一次算清的派生预算；`start_idx` 是共享可变字典（每波按实派局推进）。"""

    stages: list[int]
    targets: dict[str, int]
    est_s: dict[int, int]
    est_global: int
    game_caps: dict
    width: int
    max_batches: int
    start_idx: dict[int, int]
    ordinary: set[tuple[int, int]]


class VolumeHost(Protocol):
    """`VolumePlanner` 需要的宿主面（活读数走 property；写面只有 `land_collect` / `commit_journal`）。"""

    args: argparse.Namespace | SimpleNamespace
    bun: str
    ppo_backend: object
    update_kwargs: Callable[..., dict]

    @property
    def model(self) -> object: ...

    @property
    def opt(self) -> object: ...

    @property
    def device(self) -> str: ...

    @property
    def ref_model(self) -> object: ...

    @property
    def start_it(self) -> int: ...

    @property
    def traj_root(self) -> Path: ...

    @property
    def traj_dir(self) -> Path: ...

    @property
    def jsonl_path(self) -> Path: ...

    @property
    def rotate_seed(self) -> int: ...

    @property
    def course_fp(self) -> str | None: ...

    @property
    def corpus_fp(self) -> str | None: ...

    @property
    def state_init(self) -> bool: ...

    @property
    def extra_wver(self) -> str | None: ...

    @property
    def payload_parts(self) -> ShardSegments: ...

    @property
    def report(self) -> dict: ...

    @property
    def stream_meta(self) -> dict | None: ...

    def commit_journal(self) -> CommitJournal: ...

    def land_collect(self, report: dict, stream_meta: dict | None = None) -> None: ...


class VolumePlanner:
    """动态采集（按样本量）编排 mixin：初波/补波/连续配额 → 派发 → 报告合并 → WAL。

    状态与逻辑都在对象里；组合根只留一行转发壳，装配在 `TrainingLoop.__init__`。
    外簇只能经只读视图（`volume_view` / `greedy_view`）或三个命令
    （`reset_greedy` / `reset_ordinary_seeds` / `merge_greedy`）碰它。
    """

    def __init__(self, host: VolumeHost) -> None:
        self._host = host
        # ---- 本簇自己的状态（S4 第十八刀随簇搬来；P0-1 第五刀起归本对象）------------
        self._volume_target: int | None = None
        self._volume_collected: int | None = None
        self._volume_waves = 0
        self._volume_g0 = 0
        self._volume_est = 0
        self._volume_capped = False
        self._volume_halted = False
        self._volume_stage_ests: dict[int, int] | None = None
        self._volume_stage_stats: dict | None = None
        self._greedy_plan: GreedyQuotaPlan | None = None
        self._greedy_stats: dict | None = None
        self._greedy_report: dict | None = None
        self._greedy_waves = 0
        self._volume_ordinary_seeds: set[tuple[int, int]] = set()

    # ------------------------------------------------- 观测面
    def volume_view(self) -> VolumeView:
        """本轮采集的只读快照（字段语义见 `VolumeView`）。

        读它不改变任何状态；dict 字段是活引用（只读契约）。写作侧照旧改私有槽位 ——
        视图不给调用方一个可写句柄，也不替生产逻辑做决定。
        """
        return VolumeView(
            report=self._host.report,
            target=self._volume_target,
            collected=self._volume_collected,
            waves=int(self._volume_waves),
            g0=int(self._volume_g0),
            est=int(self._volume_est),
            capped=bool(self._volume_capped),
            halted=bool(self._volume_halted),
            stage_stats=self._volume_stage_stats,
            rotation_seed=int(self._host.rotate_seed),
            greedy_plan=self._greedy_plan,
            greedy_stats=self._greedy_stats,
            greedy_waves=int(self._greedy_waves),
        )

    def greedy_view(self) -> GreedyView:
        """greedy 槽位的只读快照（字段语义见 `GreedyView`；不改变任何状态）。"""
        return GreedyView(
            plan=self._greedy_plan,
            stats=self._greedy_stats,
            report=self._greedy_report,
            waves=int(self._greedy_waves),
        )

    # ------------------------------------------------- 零落盘（按轮）
    def round_mem_shards(self, it: int) -> dict[str, dict] | None:
        """零落盘：本轮 dispatch 腿回传 shard 的内存 manifest（轮号不符 ⇒ `None`）。

        dispatch 腿的 shard **从不落盘**（`write_shard` 见到段账就只交字节）——补波的
        「本波已采多少」只能读这份账，否则 `settled_stage_totals` 恒 0 ⇒ 白烧到波次上限。
        """
        parts = self._host.payload_parts
        if int(getattr(parts, "it", -1)) != int(it):
            return None
        return parts.manifests() or None

    # ------------------------------------------------- 动态采集（按样本量）
    #
    # plan/dynamic-rollout-volume.plan.md：课程用 target_transitions 代替固定局数做
    # 配额，轮中按**已结算 transitions** 逐关补波。纯逻辑在 biz/volume_waves.py，
    # 账本在 biz/resume.settled_stage_totals；本节只做接线 + 日志 + WAL。
    #
    # v1 边界（计划 §3-P0/P1 明确）：只接串行路径；stream/double-buffer 保持老语义
    # （不再动它是为了让双缓冲那套墙钟优化不被这一版搅动）。

    def volume_active(self) -> bool:
        """本课程是否开了动态采集（`target_transitions > 0`；缺席 = 一个函数都不调）。"""
        return int(getattr(self._host.args, "target_transitions", 0) or 0) > 0

    def volume_stages(self, it: int) -> list[int]:
        """本轮动态采集的关集（分关配额的分母）= `--stages` 的**本轮窗口**。

        ★ 2026-10-27（plan/it-stage-ratation §3.4，评审 A2）：允许**逐轮窗口**
        （`rotation_mode`）。窗口由 `worker.stage_rotation.window_from_args` 算 —— 与导出侧
        （`loop_export` 的 volume 块）和裁剪侧（`_per_stage_quota`）**同一份**，所以
        「本轮采哪些关」在三处不可能各说各话；`rotation_mode` 缺席 ⇒ 原样全集（老行为）。

        旧门控（`--curriculum-stages` / `--rotate-stages`）仍**响亮拒收**（v1 边界保留）：
        那是**另一套** pair 构造（`build_pairs` 的事），与 volume 的「按关配额」混用无解 ——
        它们的「窗口」是逐轮扩关/epoch 重排，与本模块的采样窗口不是同一件事，硬凑等于
        同一轮两套关集。要轮换请用 `rotation_mode`（它会同时管住配额分母）。
        """
        args = self._host.args
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
        # ★ 窗口（三条腿共用一份）：`rotation_mode` 缺席 ⇒ 原样返回全集（逐字节老行为）。
        from worker.stage_rotation import window_from_args

        try:
            return window_from_args(
                args, int(it), stages, rotate_seed=int(self._host.rotate_seed)
            )
        except ValueError as exc:  # 未知 mode / pinned 不在 --stages / per_it 非法
            raise SystemExit(f"[volume] it{it}: rotation 配置非法（{exc}）") from exc

    def volume_est_samples(self) -> int:
        """局均 **samples** 估计：jsonl 的 trailing 均值（可 replay）；**无历史 ⇒ `0`（无估计）**。

        2026-10-12（plan/rollout-estfree-worksteal §4.1）：课程/CLI 的声明值已退场 —— est
        只剩「盘上历史派生量」一个身份，首轮无历史是**正常状态**（调用方走「不猜」档：按
        宽度满铺 / 初批每关 1 局），绝不再拿一个假估计去反解局数。

        量纲（2026-09-15 T9）：samples（jsonl 的 `samples` 字段 = nSamples 之和），
        **不是 ticks**——后者差 K 倍（x3 实测 samples/ticks≈0.1007），会让第二轮起
        采量偏离 10×（旧 `trailing_ticks_per_game` 就是这条 bug 的一半）。
        """
        return trailing_samples_per_game(self._host.jsonl_path, window=5)

    def iteration_pairs(self, it: int) -> list[tuple[int, int]]:
        """本轮对局表：动态采集串行路径**不预排初波**（连续配额实时派发）；其余 build_pairs。

        volume 仍返回按全局 est 均分的保守表——仅供 `rollout_src=node` / export_bundle
        等需要固定 pairs 的路径；串行 ` _volume_collect_continuous` **不用**这张表。
        """
        if not self.volume_active():
            self._volume_target = None
            self._volume_collected = None
            return build_pairs(self._host.args, it, self._host.rotate_seed)
        from worker.volume_quota import target_per_stage
        from worker.volume_waves import initial_games, initial_wave_pairs

        args = self._host.args
        stages = self.volume_stages(it)
        est = self.volume_est_samples()
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
        pairs = initial_wave_pairs(self._host.rotate_seed, it, stages=stages, games_per_stage=g0)
        log(
            f"[volume] it{it}: continuous quota mode target={target} "
            f"stages={stages} per_stage={target_per_stage(target, len(stages))} est_global={est} "
            f"（node/export 预排表 {len(pairs)} 局；串行路径按差额实时派发）"
        )
        return pairs

    def volume_stage_ests_map(self, it: int) -> dict[int, int]:
        """分关 est_s：近轮盘上 shard 局均 nSamples，缺省回退全局 est。

        `it` = **正在跑的这一轮**：必须排除它。`step_prepare_iter` 在本轮开跑前就
        `mkdir(traj_root/it{it})`（`loop_iter_dir._prepare_iter_dir`），所以「mtime 最新的
        目录」在**每轮首批**恒等于这个空目录 ⇒ 不排除就会整份回退 `fallback`（全局单值），
        把分关估计静默抹平（plan/rollout-corpus-supply 评审 P0，2026-10-21）。
        """
        from worker.resume import trailing_stage_samples_per_game

        # ⚠ 关集**只**来自 `_volume_stages(it)`（= 本轮窗口）。2026-10-27 起删掉了下面那段
        #   「按 `--stages` 全集再解析一次」的旧代码：它会把窗口静默抹回全集，于是 est 按
        #   全集取、配额按窗口算 —— 正是要防的那类「同一轮两份反解」（评审 B4）。
        stages = self.volume_stages(it)
        fallback = int(self._volume_est or self.volume_est_samples() or 0)  # 0 = 无估计（首轮）
        # `agg` 可选（`--est-agg`，2026-10-21 评审 #8）：latest = 最近一个真有 shard 的轮（缺省）；
        # pooled = 近 3 轮池化（旧口径）。两侧（本链 / `loop_export`）必须同值——不一致
        # 就是「同一轮两份反解」（云机拿到的 G_s 与本地派局不同源）。
        agg = str(getattr(self._host.args, "est_agg", "latest") or "latest")
        return trailing_stage_samples_per_game(
            self._host.traj_root,
            stages,
            window_iters=3,
            fallback=fallback,
            agg=agg,
            exclude_it=int(it),
        )

    def dispatch_volume_wave(
        self,
        it: int,
        pairs: list[tuple[int, int]],
        dist_cfg: dict | None,
        *,
        # 句柄由 `_volume_collect_continuous` 内部构造并透传——它只需要"能被 set / is_set"。
        halt_event: threading.Event | None = None,
        on_result: Callable[..., None] | None = None,
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
        begin_payload_parts(self._host.args, it, self._host.payload_parts)
        (report, stream_meta, _thread, _gate, _child, _early) = dispatch_rollout_phase(
            RolloutPhaseSpec(
                args=self._host.args,
                bun=self._host.bun,
                dist_cfg=dist_cfg,
                it=it,
                traj_dir=self._host.traj_dir,
                pairs=pairs,
                jsonl_path=self._host.jsonl_path,
                model=self._host.model,
                opt=self._host.opt,
                device=self._host.device,
                ppo_backend=self._host.ppo_backend,
                update_kwargs=self._host.update_kwargs,
                start_it=self._host.start_it,
                ref_model=self._host.ref_model,
                extra_wver=self._host.extra_wver,
                eval_on_round=False,
                course_fp=self._host.course_fp,
                corpus_fp=self._host.corpus_fp,
                halt_event=halt_event,
                on_result=on_result,
                on_shard=self._host.payload_parts.add,
            )
        )
        if stream_meta is not None:
            raise SystemExit("[volume] 补波落到 stream 路径——v1 只支持串行路径")
        return report

    def volume_journal_replay(self, it: int) -> WaveRecord | None:
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

        path = Path(self._host.traj_dir) / "commit_journal.jsonl"
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

    def volume_topup(self, it: int, dist_cfg: dict | None) -> None:
        """按已结算 transitions 逐关补波直到达标/触顶（计划 §2.2.3/2.2.4）。

        账本口径：`settled_stage_totals`（只数已落盘 manifest 的 nSamples）——掉局零
        样本天然触发补采；超时局的 transitions 是真实 on-policy 数据，计入。
        每波决策进 WAL（`volume_wave` 相位）：预算跨重启续算（不重领额度），停在波次
        中间的那一波按 WAL 的对局表原样重放。**不能只按账本重算**——`wave_idx` 是决策
        而非账本的函数，它同时是种子流的键（首版「纯函数就够、无需重放」的假设已被
        e2e 证伪，见 `_volume_journal_replay`）。
        """
        if not self.volume_active():
            return
        args = self._host.args
        if self._host.stream_meta is not None:
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
            TopUpSpec,
            plan_topup,
            wave_pairs,
            wave_round_key,
        )

        stages = self.volume_stages(it)
        est = self._volume_est
        cap = int(getattr(args, "max_games_per_stage", 0) or 0)
        target = int(args.target_transitions)
        journal = self._host.commit_journal()
        collected_total = 0
        # 重启续跑：先重放 WAL 里那一波未完成的决策（同 wave_idx + 同对局表 ⇒ 同种子流；
        # 已结算的由调度器剔除），再按账本继续后面的波。
        replay = self.volume_journal_replay(it)
        if replay is not None and replay.games:
            replay_pairs = wave_pairs(self._host.rotate_seed, it, replay.games, replay.wave_idx)
            log(
                f"[volume] it{it}: WAL 重放未完成的补波 w{replay.wave_idx} "
                f"games={replay.games} → {len(replay_pairs)} 局（同种子流，不重抛硬币）"
            )
            replay_report = self.dispatch_volume_wave(it, replay_pairs, dist_cfg)
            self._host.land_collect(combine_reports([self._host.report, replay_report]))
        while True:
            wver = common.distribution.weights_fingerprint(self._host.args.out)
            totals = settled_stage_totals(
                self._host.traj_dir,
                wver,
                extra_wver=self._host.extra_wver,
                course_fp=self._host.course_fp,
                corpus_fp=self._host.corpus_fp,
                state_init=self._host.state_init,
                mem=self.round_mem_shards(it),
            )
            collected = {s: totals.get(s, (0, 0))[1] for s in stages}
            games_done = {s: totals.get(s, (0, 0))[0] for s in stages}
            collected_total = sum(collected.values())
            plan = plan_topup(
                TopUpSpec(
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
            pairs = wave_pairs(self._host.rotate_seed, it, plan.games_by_stage, plan.wave_idx)
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
            wave_report = self.dispatch_volume_wave(it, pairs, dist_cfg)
            # 报告合并（既有多轮聚合口径：combine_reports 吃单轮报告，与远端单局摘要同构）
            self._host.land_collect(combine_reports([self._host.report, wave_report]))
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
        if self._volume_waves > 1 and self._host.report.get("pure_collect_sec") is not None:
            agg_ok = self._host.report.get("rollout_collect_aggregated")
            log(
                f"[volume] it{it}: rollout 聚合 waves={self._host.report.get('rollout_collect_waves', self._volume_waves)} "
                f"pure_collect_sec={self._host.report['pure_collect_sec']}"
                f"（首波分发→样本齐；aggregated={agg_ok}）"
            )

    def volume_collect_continuous(self, it: int, dist_cfg: dict | None) -> None:
        """配额感知连续采集（2026-09-19 用户指令；VOLUME_RULE_V2）。

        **退役离散补波**：loop 读账本 → 按各关 `quota - collected - inflight*est_s`
        决定下一批局数 → 派发 → 再读账本，直到各关达标 / game_cap / batch 安全阀。
        即将足额（软停）不再派；差额大的关多派。种子 = `(it, stage, 本轮第k局)`。

        §15.5：相对 wave 规则的新语料语义 —— 课程若从 wave 迁到 continuous，
        应 fresh `--out/--traj`（用户已知）。
        """
        if not self.volume_active():
            return
        args = self._host.args
        if self._host.stream_meta is not None:
            log("[volume] 流式路径不支持连续配额 v2（保持 stream 老语义）")
            from worker.reports import adopt_volume_report

            self._host.land_collect(adopt_volume_report(None))
            return
        if int(getattr(args, "collect_only", 0) or 0):
            from worker.reports import adopt_volume_report

            self._host.land_collect(adopt_volume_report(None))
            return
        from worker.volume_alloc import default_game_caps
        from worker.volume_quota import DEFAULT_MAX_BATCHES, target_per_stage

        stages = self.volume_stages(it)
        # greedy 阶段用它过滤普通批已派过的 (stage, seed)，保证两批种子不相交。
        self._volume_ordinary_seeds = set()
        target = int(args.target_transitions)
        quota = target_per_stage(target, len(stages))
        est_global = int(self._volume_est or self.volume_est_samples() or 0)  # 0 = 无估计（首轮）
        est_s = self.volume_stage_ests_map(it)
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
        course_fp = self._host.course_fp
        corpus_fp = self._host.corpus_fp
        extra_wver = self._host.extra_wver
        start_idx = {s: 0 for s in stages}
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

        self._volume_halted = False
        state = ContinuousState(
            stages=stages,
            target=target,
            quota=quota,
            max_batches=max_batches,
            width=width,
            game_cap=game_cap,
            stage_caps=stage_caps,
            course_fp=course_fp,
            corpus_fp=corpus_fp,
            extra_wver=extra_wver,
            est_global=est_global,
            est_s=est_s,
            start_idx=start_idx,
            first_est_used=first_est_used,
            first_est_raw=first_est_raw,
            first_est_src=first_est_src,
        )
        combined = self.volume_continuous_loop(it, dist_cfg, state)
        self.volume_continuous_settle(it, state, combined)

    def volume_continuous_loop(
        self, it: int, dist_cfg: dict | None, state: ContinuousState
    ) -> dict | None:
        import threading

        import common.distribution
        from worker.resume import settled_stage_totals
        from worker.volume_alloc import DEFAULT_POOL_MULT, quota_met
        from worker.volume_quota import continuous_pairs

        # ★★ 铺满 + 满额即停（用户口径 2026-10-21）：
        #   派出去的是一个**候选池**（缺口 × mult），不是"刚好够的局数"；dispatcher 的队列语义
        #   本来就是「空一个槽就从池里取下一个」，所以每个槽从 t=0 起满负荷、结算一局补一局；
        #   `_quota_watch`（on_result 回调）实时累加逐关样本，**逐关**全满额就置 `halt` ⇒ worker 立即停止取新任务
        #   ⇒ 池里剩下的候选**从不运行**（不是"派了再丢"）。在飞副本走既有 dedup 路径丢弃。
        halt = threading.Event()
        live: dict[int, int] = {}
        stage_set = {int(s) for s in state.stages}
        pool_mult = float(getattr(self._host.args, "volume_pool_mult", 0) or DEFAULT_POOL_MULT)
        combined: dict | None = None

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
            if quota_met(stages=state.stages, collected=live, target_transitions=state.target):
                halt.set()

        while self._volume_waves < state.max_batches:
            wver = common.distribution.weights_fingerprint(self._host.args.out)
            totals = settled_stage_totals(
                self._host.traj_dir,
                wver,
                extra_wver=state.extra_wver,
                course_fp=state.course_fp,
                corpus_fp=state.corpus_fp,
                state_init=self._host.state_init,
                mem=self.round_mem_shards(it),
            )
            collected = {s: int(totals.get(s, (0, 0))[1]) for s in state.stages}
            games_done = {s: int(totals.get(s, (0, 0))[0]) for s in state.stages}
            # 实时账本从盘上事实起步（**原地**更新：`_quota_watch` 持有的是同一个 dict）
            live.clear()
            live.update(collected)
            # 同步 dispatch：批间无在飞；批内调度器自己竞速。
            # （`inflight` 只喂给旧版 `plan_continuous_batch` 的软停；改成候选池后不再需要——
            #   满额判据由 `on_result` 的实时账本承担。）
            plan = plan_continuous_wave(state, collected, games_done, pool_mult)
            if not plan.games:
                break
            # 首波读数：进 iteration 事件（`est_used` / `est_src`）—— 批数由首波决定，
            # 记末波的 est 等于「事后诸葛亮」，会读不出「首波高估了多少」。
            remember_first_est(state, plan)
            pairs = continuous_pairs(self._host.rotate_seed, it, plan.games, state.start_idx)
            self._volume_ordinary_seeds.update(pairs)
            for s, n in plan.games.items():
                state.start_idx[s] = state.start_idx.get(s, 0) + int(n)
            self._volume_waves += 1
            log(
                f"[volume] it{it}: batch{self._volume_waves} "
                f"games={plan.games}（{plan.pool_note}；差额 {plan.shortfall} "
                f"est_used={ {s: plan.dispatch_est.get(s) for s in plan.games} } "
                f"est_raw={ {s: plan.raw_est.get(s) for s in plan.games} } src={plan.src}"
                + (
                    f"，无历史 ⇒ 按宽度 {state.width} 满铺（不猜 est））"
                    if plan.src == "cold-start"
                    else "）"
                )
            )
            wave_report = self.dispatch_volume_wave(
                it, pairs, dist_cfg, halt_event=halt, on_result=_quota_watch, pool_halt=True
            )
            combined = wave_report if combined is None else combine_reports([combined, wave_report])
            if plan.capped:
                self._volume_capped = True
            # ★ 满额即收工：池里剩下的候选**没有派出去**（不是"派了再丢"），
            #   所以这里直接退出循环——不会再有第二批，也没有第二批的固定开销。
            if halt.is_set():
                self._volume_halted = True
                break
        return combined

    def volume_continuous_settle(
        self, it: int, state: ContinuousState, combined: dict | None
    ) -> None:
        import common.distribution
        from worker.resume import settled_stage_totals
        from worker.volume_alloc import VOLUME_ALLOC_RULE

        wver = common.distribution.weights_fingerprint(self._host.args.out)
        totals = settled_stage_totals(
            self._host.traj_dir,
            wver,
            extra_wver=state.extra_wver,
            course_fp=state.course_fp,
            corpus_fp=state.corpus_fp,
            state_init=self._host.state_init,
            mem=self.round_mem_shards(it),
        )
        collected_total = sum(int(totals.get(s, (0, 0))[1]) for s in state.stages)
        unmet = {
            s: state.quota - int(totals.get(s, (0, 0))[1])
            for s in state.stages
            if int(totals.get(s, (0, 0))[1]) < state.quota
        }
        if unmet:
            self._volume_capped = True
            log(
                f"[volume] WARN it{it}: 连续配额未满 batches={self._volume_waves}/{state.max_batches} "
                f"unmet={unmet} per_stage_quota={state.quota} game_cap={state.game_cap}——"
                f"est 持续低估或触硬顶（非静默；pooled={collected_total}/{state.target}）"
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
                "est_s": int(state.est_s.get(s, state.est_global)),
                "est_used": int(state.first_est_used.get(s, 0)),
                "est_raw": int(state.first_est_raw.get(s, 0)),
                "est_src": state.first_est_src.get(s, "none"),
                "cap_games": int(state.stage_caps.get(s, 0)),
            }
            for s in state.stages
        }
        # 进 iteration 事件的**分关账**（2026-10-07，plan/rollout-stage-balance §4.2）：
        # 规则版本 + 分关 collected/games/est_s/cap_games + 缺口（硬指标）。为什么带上规则
        # 版本：分关分配不进 `corpus_identity_fp`（与 est 同类，用户拍板 ②），所以「哪一轮起
        # 换了分配口径」必须在**事件**里可读（§15.5 的精神：不许静默漂移，也不拒收在训课程）。
        # 顶层 `est_src`：只在全关同源时给具体值，否则 `mixed`（拼一个「首关的源」会把
        # 「一半 learned 一半 trailing」这轮最该看的情形抹平）。
        _srcs = set(state.first_est_src.values()) or {"none"}
        self._volume_stage_stats = {
            "rule": VOLUME_ALLOC_RULE,
            "est_src": next(iter(_srcs)) if len(_srcs) == 1 else "mixed",
            # 聚合口径也进事件：`--est-agg` 的 A/B（latest vs pooled）会产出**同一形状**的读数，
            # 不写这一格就分不清「哪一轮用的是哪个口径」（P0-2 的偏差表要按它分组）。
            "est_agg": str(getattr(self._host.args, "est_agg", "latest") or "latest"),
            # `halted` = 逐关配额全部达成、池里剩下的候选没派出去（2026-10-21 用户口径）
            "halted": bool(self._volume_halted),
            "quota": int(state.quota),
            "stages": {str(s): stage_stats[s] for s in state.stages},
            "shortfall": {str(s): int(v) for s, v in unmet.items()},
        }
        log(
            f"[volume] it{it}: continuous 收官 batches={self._volume_waves} "
            f"collected={collected_total}/{state.target} 达标关="
            f"{len(state.stages) - len(unmet)}/{len(state.stages)} rule={VOLUME_ALLOC_RULE} stats={stage_stats}"
        )
        self._volume_collected = collected_total
        # 报告真源 = 本轮盘上 shard（与 settled_stage_totals 同源）；wave 只补时间锚点。
        # 禁止 combine 进上一轮 _report（pure_collect 起点会钉在历史波，§adopt_volume_report）。
        # 配额已满重启 ⇒ batches=0，若只 adopt(combined=None) 会把除零保护的 winRate=0
        # 写进账本（x20-steady it76 / §107）——必须从 shard 回填 outcomes。
        from worker.reports import merge_volume_report
        from worker.resume import ResumedSpec, resumed_manifests

        disk_reports = resumed_manifests(
            ResumedSpec(
                self._host.traj_dir,
                wver,
                extra_wver=state.extra_wver,
                course_fp=state.course_fp,
                state_init=self._host.state_init,
                mem=self.round_mem_shards(it),
            )
        )
        if state.stages:
            stage_set = {int(s) for s in state.stages}
            filtered: list[dict] = []
            for r in disk_reports:
                st = r.get("stage")
                if st is None or int(st) in stage_set:
                    filtered.append(r)
            disk_reports = filtered
        self._host.land_collect(merge_volume_report(combined, disk_reports))
        if self._volume_waves > 0 and self._host.report.get("pure_collect_sec") is not None:
            log(
                f"[volume] it{it}: rollout 聚合 batches={self._host.report.get('rollout_collect_waves', self._volume_waves)} "
                f"pure_collect_sec={self._host.report['pure_collect_sec']}"
                f"（首批分发→样本齐；aggregated={self._host.report.get('rollout_collect_aggregated')}）"
            )

    def dispatch_greedy_wave(
        self, it: int, pairs, dist_cfg, total: int, plan, bins: dict, targets: dict
    ) -> bool:
        """派发一波 greedy 并合并报告；返回 halt（配额已满）是否置位（自调用点搬出）。"""
        import threading

        from worker.greedy_quota import manifest_samples

        halt = threading.Event()
        live = {"total": int(total)}

        # 绑定本轮的 live/halt：回调可能在下一轮开始后才被调（避免串轮）
        def _watch(summary: object, live: dict[str, int] = live, halt: threading.Event = halt) -> None:
            if not isinstance(summary, dict):
                return
            # 单局摘要的字段口径：本地 TS shard 写 nSamples，远端/队列 path 经
            # `_ensure_games` 转写 totalSamples（`collect_only._on_result` 同款）。
            # 只认 nSamples ⇒ 队列路径的逐局熔断永不触发，整批候选跑完 = 过采。
            n = manifest_samples(summary)
            if n > 0:
                live["total"] += n
                if live["total"] >= plan.collect_target:
                    halt.set()

        self._greedy_waves += 1
        args = self._host.args
        old_argmax = bool(getattr(args, "rollout_argmax", False))
        args.rollout_argmax = True
        try:
            wave_report = self.dispatch_volume_wave(
                it,
                pairs,
                dist_cfg,
                halt_event=halt,
                on_result=_watch,
                pool_halt=True,
            )
        finally:
            args.rollout_argmax = old_argmax
        self._greedy_report = (
            wave_report
            if self._greedy_report is None
            else combine_reports([self._greedy_report, wave_report])
        )
        log(
            f"[greedy] it{it}: batch{self._greedy_waves} games={len(pairs)} "
            f"bins={bins} targets={targets} total={total}/{plan.collect_target}"
        )
        return halt.is_set()

    def greedy_budget(
        self,
        it: int,
        stages: list[int],
        targets: dict[str, int],
        args: object,
        *,
        collect_target: int,
        cap: float,
    ) -> _GreedyBudget:
        """关硬顶 / 宽度 / 波数 / 续抽下标 / 已派过的普通局 —— 一次算清。"""
        from worker.volume_alloc import default_game_caps
        from worker.volume_quota import DEFAULT_MAX_BATCHES

        est_s = self.volume_stage_ests_map(it)
        est_global = int(self._volume_est or 0)
        # 硬顶按**全局** collect_target 算（`default_game_caps` 会再除以关数）；传
        # `per_stage_collect` 会把每关硬顶再除一次关数 ⇒ 第一波结算后即触顶，
        # greedy 阶段提前收官（2026-11-09 S3 实测）。
        game_caps = default_game_caps(
            stages,
            collect_target,
            ests=est_s,
            mult=max(1, int(cap + 0.999)),
        )
        width = (
            int(getattr(args, "local_slots", 0) or 0)
            or int(getattr(args, "workers", 0) or 0)
            or len(stages)
        )
        max_batches = int(getattr(args, "volume_max_batches", 0) or DEFAULT_MAX_BATCHES)
        start_idx = {s: 0 for s in stages}
        ordinary = set(self._volume_ordinary_seeds)

        return _GreedyBudget(
            stages=stages,
            targets=targets,
            est_s=est_s,
            est_global=est_global,
            game_caps=game_caps,
            width=width,
            max_batches=max_batches,
            start_idx=start_idx,
            ordinary=ordinary,
        )

    def greedy_wave_pairs(
        self,
        it: int,
        budget: _GreedyBudget,
        manifests: dict,
        *,
        seed_salt: int,
        collect_target: int,
    ) -> list[tuple[int, int]] | None:
        """这一波要派的 `(stage, seed)`；`None` = 无可派局，调用方收官。"""
        from worker.volume_quota import continuous_pairs

        stages = budget.stages
        est_s, est_global = budget.est_s, budget.est_global
        game_caps, width = budget.game_caps, budget.width
        start_idx, ordinary = budget.start_idx, budget.ordinary

        collected, games = _greedy_collected(manifests, stages)
        # 重启后从已结算局数续抽，不重放已结算的 seed。
        for s in stages:
            start_idx[s] = max(start_idx.get(s, 0), games.get(s, 0))

        plan_g = _greedy_plan_games(
            stages,
            collected,
            games,
            est_s,
            est_global,
            game_caps,
            width,
            collect_target,
        )
        if not plan_g:
            return None
        pairs = continuous_pairs(
            self._host.rotate_seed ^ seed_salt, it, plan_g, start_idx
        )
        pairs = [(s, sd) for s, sd in pairs if (s, sd) not in ordinary]
        if not pairs:
            return None
        # 冲突过滤会削掉部分 (stage, seed)：seed 索引只能按**实际派出**的局前进，
        # 否则被跳过的 seed 永久丢失（plan rev4 §5.1「冲突就跳过，下一波补」）。
        # 下一波开头的 max(start_idx, games) 仍保证掉局不重派。
        for s, _sd in pairs:
            start_idx[s] = start_idx.get(s, 0) + 1

        return pairs

    def greedy_settle(
        self,
        it: int,
        quota: _GreedyTargets,
        targets: dict[str, int],
        *,
        stopped_no_games: bool,
    ) -> None:
        """收官：重扫 → degraded / cap_hit → 合并 report → 账目 → 日志与硬目标。"""
        from worker.greedy_quota import (
            GREEDY_QUOTA_RULE_V1,
            is_greedy_manifest,
            manifest_samples,
        )

        names, manifests, bins, total = self.greedy_scan(it)
        degraded = any(int(bins[b]) < int(targets[b]) for b in targets)
        cap_hit = bool(degraded and total >= quota.collect_target)
        # 2026-11-10（用户裁决）：主 rollout report 改**全量**口径。此前 greedy 波只进
        # `_greedy_report`（死数据），`write_rollout_end` / `write_iteration` 读的是
        # `self._host.report` ⇒ 控制台恒显示纯采样的 >98%，greedy 败局/超时局完全不可见。
        # 合并前各存一份 brief，供控制台下钻「采样 vs greedy」两份读数（实现与云机腿共用一份）。
        from worker.greedy_quota import report_brief as _brief

        sampling_report = _brief(self._host.report)
        greedy_report = _brief(self._greedy_report)
        if self._greedy_report:
            self._host.land_collect(combine_reports([self._host.report, self._greedy_report]))
        # 全量指标：`transitions_collected` 由 `_volume_collected` 落账（不读 report）。
        # 两边同加才守得住 e2e 的 `report.totalSamples == _volume_collected` 等式。
        if self._volume_collected is not None:
            self._volume_collected = int(self._volume_collected) + int(total)
        self._greedy_stats = {
            "rule": GREEDY_QUOTA_RULE_V1,
            "target": int(quota.total_target),
            "collect_target": int(quota.collect_target),
            "cap": float(quota.cap),
            "soft": bool(quota.soft),
            "per_stage_target": int(quota.per_stage_target),
            "bins": dict(bins),
            "targets": dict(targets),
            "total": int(total),
            "degraded": bool(degraded),
            "cap_hit": cap_hit,
            "waves": int(self._greedy_waves),
            "stopped_no_games": bool(stopped_no_games),
            # 拆分读数（合并前的采样 / 本轮 greedy）；`merge_greedy_stats` 只挂
            # `prefilter` 子键，这两个键会一路带进 iteration 事件。
            "sampling_report": sampling_report,
            "greedy_report": greedy_report,
        }
        # 分关 greedy 账挂到 `volume_stage_stats` 的**增量**子键：控制台的分关表能看见
        # greedy 样本落在哪几关，同时普通配额诊断的 stages/shortfall/est_src 口径不动。
        if isinstance(self._volume_stage_stats, dict):
            by_stage: dict[str, dict] = {}
            greedy_games = 0
            for name in names:
                mm = manifests.get(name) or {}
                if not is_greedy_manifest(mm):
                    continue
                greedy_games += 1
                row = by_stage.setdefault(
                    str(mm.get("stage")), {"samples": 0, "games": 0}
                )
                row["samples"] += manifest_samples(mm)
                row["games"] += 1
            self._volume_stage_stats["greedy"] = {
                "samples": int(total),
                "games": greedy_games,
                "stages": by_stage,
            }
        log(
            f"[greedy] it{it}: 收官 bins={bins} targets={targets} total={total} "
            f"degraded={degraded} cap_hit={cap_hit} waves={self._greedy_waves}"
        )
        if degraded and not quota.soft:
            raise RuntimeError(
                f"greedy_quota 硬目标未达成（soft=false）：bins={bins} targets={targets} "
                f"total={total}/{quota.collect_target}"
            )

    def greedy_scan(self, it: int):
        """盘上 greedy 进度（自 `_volume_collect_greedy` 搬出）：`(names, manifests, bins, total)`。"""
        from worker.greedy_quota import greedy_progress, load_manifests, shard_dirs_for_round

        mem = self.round_mem_shards(it) or {}
        shard_dirs = shard_dirs_for_round(self._host.traj_root, it, mem)
        names = [p.name for p in shard_dirs]
        manifests = load_manifests(shard_dirs, mem)
        bins, total = greedy_progress(names, manifests)
        return names, manifests, bins, total

    def volume_collect_greedy(self, it: int, dist_cfg: dict | None) -> None:
        """greedy 结果配额采集阶段（plan/greedy-outcome-quota rev4）。

        **所有采集通道都支持**（2026-11-10）：本机串行池 / dist 队列节点 /
        `kind=iter` 云节点 / 半离线模板。`args.rollout_argmax=True` 期间，本机与
        argv 型节点由 `build_rollout_cmd` 拼 `--teacher-argmax`，队列节点由
        `?labelMode=greedy` 透传（已进 agent taskKey 与轮询配方，缓存隔离），
        shard manifest 一律带 `labelMode="greedy"`。课程 volume 块保持原样：
        本阶段用自己的 `collect_target`/`per_stage_collect`，不碰
        `validate_runtime_volume` 的同源门。

        为什么是**独立阶段**而不是放大普通配额：`widen_by_width` 是「铺到至少 width 个
        候选」，不是倍率；把课程 quota 改大同时会改 `corpus_identity_fp` 与所有关的
        预算。独立阶段只多派 greedy 局，普通语料逐字节不变。
        """
        args = self._host.args
        self._greedy_plan = None
        self._greedy_stats = None
        self._greedy_report = None
        self._greedy_waves = 0
        if getattr(args, "greedy_quota", None) is None:
            return
        if self._host.stream_meta is not None:
            raise RuntimeError(
                "greedy_quota 不支持 stream 编排路径（该路径没有第二遍采集钩子，"
                "不是节点能力限制；请走串行/队列节点通道）"
            )
        if int(getattr(args, "collect_only", 0) or 0):
            return
        if str(getattr(args, "export_bundle", "") or ""):
            # 包是给人看的全量；不跑第二遍、不预筛。
            return

        from worker.greedy_quota import bin_targets, parse_greedy_quota

        stages = self.volume_stages(it)
        plan = parse_greedy_quota(
            getattr(args, "greedy_quota", None),
            target_transitions=int(getattr(args, "target_transitions", 0) or 0),
            stages=stages,
        )
        if plan is None:
            return
        self._greedy_plan = plan

        # 扫描根用 `_traj_root`：shard 在 `<traj_root>/it{it}/w*/rl_s*_seed*`，
        # 递归扫描由 `shard_dirs_for_round` 负责（生产布局，非 itN 直接子目录）。
        names, manifests, bins, total = self.greedy_scan(it)
        targets = bin_targets(plan)
        budget = self.greedy_budget(
            it, stages, targets, args, collect_target=plan.collect_target, cap=plan.cap
        )
        quota = _GreedyTargets(
            total_target=plan.total_target,
            collect_target=plan.collect_target,
            per_stage_target=plan.per_stage_target,
            cap=plan.cap,
            soft=plan.soft,
        )
        stopped_no_games = False

        while self._greedy_waves < budget.max_batches:
            names, manifests, bins, total = self.greedy_scan(it)
            if all(int(bins[b]) >= int(budget.targets[b]) for b in budget.targets):
                break
            if total >= plan.collect_target:
                break

            pairs = self.greedy_wave_pairs(
                it,
                budget,
                manifests,
                seed_salt=plan.seed_salt,
                collect_target=plan.collect_target,
            )
            if pairs is None:
                stopped_no_games = True
                break

            if self.dispatch_greedy_wave(
                it, pairs, dist_cfg, int(total), plan, bins, targets
            ):
                break

        self.greedy_settle(it, quota, targets, stopped_no_games=stopped_no_games)


    # ------------------------------------------------- 跨簇命令（外簇唯一能碰状态的方式）
    def reset_greedy(self) -> None:
        """每轮清零（`loop_round_steps` 在采集前调）：上一轮的 greedy 留痕不得进发布相位。"""
        self._greedy_plan = None
        self._greedy_stats = None
        self._greedy_report = None

    def reset_ordinary_seeds(self) -> None:
        """每轮清零普通连续配额的已派种子（greedy 阶段据此过滤，两批种子不相交）。"""
        self._volume_ordinary_seeds = set()

    def merge_greedy(self, stats: dict) -> None:
        """把发布端预筛读数并进 greedy stats（`loop_remote_job` 的唯一写入口）。"""
        from worker.greedy_quota import merge_greedy_stats

        self._greedy_stats = merge_greedy_stats(self._greedy_stats, stats)

