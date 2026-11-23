"""iter_run —— `iter_rollout` 的**轮级/批级状态**（`RoundContext` + `IterRun` 的家）。

从 `worker/iter_rollout.py` 搬出（LOC 预算：那个文件顶在 1000 行上）：这两个类装的是
「这一轮 / 这一批」的资源与读数，与「怎么跑」分家。`iter_rollout` 保留同名 re-export
（`remote/job_shell.py` 与 `tests/remote/test_round_ctx.py` 的 import 逐字不变）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from common import game_watch, scratch
from common.platform_utils import call_bounded


@dataclass
class RoundContext:
    """一轮 job 的**轮级**资源：首批解析一次，补差批复用（`plan/rollout-topup-fastpath` §3.2）。

    为什么需要它（2026-10-11 现场）：补差批复用的是同一个 `run_iter_rollout`，于是**每一批**
    都重付一遍「与批内局数无关」的开销——落点探针（8MB + fsync ×(1+候选数)）、TS 树安装、
    权重镜像、以及最贵的**长驻池建池 + 冷启动**（94 个 bun：exec + traverse + wasm + attest×3）。
    批越小这笔开销占比越高（it34 补差2 只有 **1 局**，却付了 48.9s 的壳子）⇒ 池的收益
    （`docs/nn/runtime-opt.md` §20 的 1.59×）被「池的生命周期 = 批」吃掉了。

    ⚠ 只装**轮级**的东西：`jd` / `s_layout` / `exec_dir` 是**批级**的（每批一个 `topup{k}/`，
    各自的 scratch 私有目录 / `--out` 续号 / 轮末清理都属于批）。
    ⚠ **池的所有权随 ctx 转移**：`ctx=None`（老调用）= 本轮自建自收（逐字今天）；
    传 ctx = 调用方建、调用方在**补差跑完之后、PPO 之前** `close()`（`remote/worker.py`）
    ——别让 94 个常驻 bun 陪 PPO 吃内存。
    """

    #: **轮级** scratch 布局（探针结论；`None` = 回退档：不换根 / 产出住真 job 目录）。
    #: 批级布局由它镜像而来：`batch_layout(layout, batch_rel(轮 job 目录, 批 job 目录))`。
    layout: scratch.ScratchLayout | None = None
    #: TS 树在 scratch 的家（按名寻址 ⇒ 跨批同一份）；`None` = 本轮未换根。
    ts_home: Path | None = None
    #: 轮的真实 job 目录（首批那个；补差批是它下面的 `topup{k}/`）。
    job_dir: Path | None = None
    #: 长驻池（生命周期 = 轮）。
    pool: Any = None
    #: 轮级**槽位**（= 本机能同时跑的局数上限；补差批的满铺器拿它当 `width`，池宽也按它）。
    workers: int = 0
    #: 首批是否已完成解析（落点 / TS 树）；补差批看到 `True` 就跳过那一整段。
    resolved: bool = False
    #: 落点段耗时（读数用；旧名 `probe_sec` —— 探针已删，2026-10-14）。
    setup_sec: float = 0.0

    @property
    def root(self) -> Path | None:
        """scratch 介质根（`None` = 回退档）。"""
        return None if self.layout is None else self.layout.root

    def close(self) -> None:
        """收掉本 ctx 拥有的长驻池（幂等）。**不**动 scratch 上的产出。"""
        pool, self.pool = self.pool, None
        if pool is not None:
            pool.close()

    def cleanup(self, log=lambda msg: None) -> None:
        """轮末回收本地盘占用（**PPO 之后**叫；幂等、尽力而为、绝不抛）。

        取消搬回（§3.5）后产出就住在轮 scratch 目录里（PPO 要读它）⇒ 清理从「每批 `finally`」
        前移到「全 job 收尾、PPO 之后」：清**本轮的** scratch 目录（ts 家不在里面、永不清）。
        有上界（`DRAIN_CEILING_SEC`）：挂住的挂载点清不动就留着（下一轮同名会重建），不阻塞收尾。
        """
        if self.layout is None:
            return
        ok, _ = call_bounded(
            partial(scratch.clear_dir, self.layout.job),
            float(game_watch.DRAIN_CEILING_SEC),
            name="scratch-cleanup",
        )
        if not ok:
            log(
                f"WARN rollout scratch 轮末没清完（{game_watch.DRAIN_CEILING_SEC:g}s 内没返回）："
                f"{self.layout.job}——本地盘占着就占着（下一轮同名会重建），不阻塞收尾"
            )


@dataclass(frozen=True, kw_only=True)
class IterRunSpec:
    """开始一轮 rollout 时**由调用方给定**的 9 个旋钮（`run_iter_rollout` 的唯一参数）。

    这一组名字原先排在 `run_iter_rollout` 的位置口上（`job_dir, spec` 两个位置 + 七个
    关键字）：`job_dir` 是「产出落哪个目录」、`spec` 是「跑哪几局」，两个相邻的 `str|Path` /
    `dict` —— 互换不抛（`Path(dict)` 才报、`dict(Path)` 也不报），只会把这一轮跑进错的目录
    或拿空模板去开池。另外 `next_game` / `on_game_settled` / `volume_line` 三个回调与
    `ts_dir` / `it` 两个「标签位」混在一起，全凭名字记。

    与 `IterRun` 的分工：**本对象只装输入的旋钮**（frozen），`IterRun` 装本轮跑起来的
    可变状态（`jd` / `tsd` / `pool` / `game_secs` / …）。`IterRun.from_spec` 是两者之间
    唯一的一次搬运，字段表由 `tests/worker/test_iter_run_spec.py` 对账。
    """

    #: 本轮 job 目录（产出落点；`spec` 里的相对路径都相对它）。
    job_dir: str | Path
    #: 已过 `protocol.validate_rollout_spec` 的归一化字典（argv 白名单 + 逐局 stage/seed）。
    spec: dict
    #: 解包后的 TS 代码根；`None` = 用 job 目录（单测桩脚本是绝对路径，不需要独立 TS 根）。
    ts_dir: str | Path | None = None
    #: 本轮日志出口（`run_iter_rollout` 原先缺省 `_log`，57 处调用点全部显式传）。
    log: Callable[[str], None]
    #: 轮级资源（补差批复用）；`None` = 本轮自建自收。
    ctx: RoundContext | None = None
    #: 轮号（只用于打点标签，不参与任何判据；缺省 `0` 会让进度行显示「第 0 轮」）。
    it: int = 0
    #: 逐局流式调度器：每个槽位一空就调一次取下一局；`None` = 逐字老行为（argv 即全部）。
    next_game: Callable[[], list[str] | None] | None = None
    #: `(argv, n_samples)` → None：把刚结算那一局的产出告诉调度器。
    on_game_settled: Callable[..., None] | None = None
    #: 轮末并进**同一行**的读数（流式采集的 `summary_line`）；异常只记一行。
    volume_line: Callable[[], str] | None = None


@dataclass
class IterRun:
    """一轮 rollout 的批级状态（轮级资源仍住 `RoundContext`）。"""

    job_dir: str | Path
    spec: dict
    ts_dir: str | Path | None
    log: Callable[[str], None]
    ctx: RoundContext | None
    it: int
    next_game: Callable[[], list[str] | None] | None
    on_game_settled: Callable[..., None] | None
    volume_line: Callable[[], str] | None
    jd: Path = field(default_factory=Path)
    tsd: Path = field(default_factory=Path)
    t0: float = 0.0
    bun: str = ""
    ver: str = ""
    templates: list[list[str]] = field(default_factory=list)
    argvs: list[list[str]] = field(default_factory=list)
    slots: int = 0
    workers: int = 0
    timeout_sec: float = 0.0
    #: 面板 tick 预算与「慢局点名线」（`_setup_run` 定，轮末读数用）。
    panel_ticks: int = 0
    warn_sec: float = 0.0
    explicit: bool = False
    rb: Any = None
    rctx: RoundContext = field(default_factory=RoundContext)
    exec_jd: Path = field(default_factory=Path)
    shards_root: Path = field(default_factory=Path)
    pool: Any = None
    game_secs: dict[int, float] = field(default_factory=dict)
    game_attempts: dict[int, int] = field(default_factory=dict)
    round_retries: int = 0
    retry_cap: int = 0
    ok: bool = False

    @classmethod
    def from_spec(cls, spec: IterRunSpec) -> IterRun:
        """输入旋钮 → 批级状态（`run_iter_rollout` 的唯一入口）。

        逐名搬运而非 `**asdict()`：这九个名字是**输入面**，多抄/少抄一格都会被
        `tests/worker/test_iter_run_spec.py` 的字段表对账到；`asdict` 会把日后给
        `IterRunSpec` 加的可选位默默漏过一次搬运（`IterRun` 侧同名位仍是旧缺省值）。
        """
        return cls(
            job_dir=spec.job_dir,
            spec=spec.spec,
            ts_dir=spec.ts_dir,
            log=spec.log,
            ctx=spec.ctx,
            it=spec.it,
            next_game=spec.next_game,
            on_game_settled=spec.on_game_settled,
            volume_line=spec.volume_line,
        )
