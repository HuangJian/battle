"""loop_baseline —— **it0 基线评估**：协作对象 `BaselineEval`（2026-11-28，P0-1 第二刀）。

两个成员是一条真链（实测连通分量）：`maybe_dispatch` → `weights`。判据同源：
**「日志里有没有这一份 bc 权重的 it0 干净评估」**——指纹（`common.distribution.weights_fingerprint`
前 16 位）+ 落地摘（`baseline_summary_landed`）两者一起决定「派 / 不派」，`weights` 只是它前半段的
前置条件（纯判断、零副作用）。

## 为什么从混入改成协作对象（同 `IterWorkspace`，P0-1 第一刀的口径）

原先这是 `RoundSteps` 的一个基类（`TrainingBaseline`）：线程柄与已落账指纹住在**组合根的
`__init__`**、读取 `self._traj_dir` / `self._eval_every` 却没有静态声明。现在：

* **状态归自己**：`_thread` / `_landed_wver` 只存这里（原先在 `loop_core._init_eval_state` 赋初值）；
* **依赖有声明**：`BaselineHost` 是 `Protocol`（`args` / `bun` / `traj_dir` / `eval_every`）；
* **组装一处**：`TrainingLoop.__init__` 造 `self.baseline = BaselineEval(self)`；
* **调用点不动**：组合根留两个一行转发壳（`_maybe_dispatch_baseline_eval` /
  `baseline_eval_state`）——`RoundSteps.step_course_iter` 与既有用例照旧命中。

## DI seam 是**本模块**的

基线派发走两处**延迟** import（`worker.eval_local.baseline_summary_landed` /
`trainer.eval_dispatch.dispatch_eval_bg`）：它们在本模块**未**引入 ⇒ 测本簇实现的用例必须 patch
`trainer.eval_dispatch.*` / `worker.eval_local.*`（实现模块），与本模块的命名空间无关。`RUN_ID` /
`common.distribution` / `log` 则是本模块的**模块全局**（按名字解析）：这三者才是本模块自己的注入点。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import common.distribution
from common.log import log
from trainer.dispatch_queue import RUN_ID


class BaselineHost(Protocol):
    """`BaselineEval` 需要的宿主读面（`TrainingLoop` 用 property 提供）。"""

    args: Any
    bun: Any

    @property
    def traj_dir(self) -> Path:
        """本轮目录（`eval_log.jsonl` 由它的父目录给出）。"""
        ...

    @property
    def eval_every(self) -> int:
        """in-loop eval 的间隔轮数（`0` = 关闭 ⇒ 基线也不派）。"""
        ...


@dataclass(frozen=True)
class BaselineEvalView:
    """it0 基线评估的执行面读数（派发判据本身不在这里）。"""

    landed_wver: str | None
    eval_thread: threading.Thread | None


class BaselineEval:
    """it0 基线评估（`maybe_dispatch` → `weights` 一条链；见本模块头注）。"""

    def __init__(self, host: BaselineHost) -> None:
        self._host = host
        #: 在飞守卫：线程只作「还在跑吗」的判断，不参与 join（原先住组合根 `__init__`）。
        self._thread: threading.Thread | None = None
        #: 已落账基线的权重指纹（命中即不再派；原先住组合根 `__init__`）。
        self._landed_wver: str | None = None

    def state(self) -> BaselineEvalView:
        """这一簇的公开读数（用例 / 诊断共用；不改变派发判据）。"""
        landed = self._landed_wver
        return BaselineEvalView(
            landed_wver=None if landed is None else str(landed),
            eval_thread=self._thread,
        )

    def weights(self, dist_cfg: dict | None) -> str | None:
        """it0 基线可用的 bc 权重路径；前置条件不足返回 None（纯判断，零副作用）。

        条件：per-tick 课程 / 有课程上下文 / in-loop eval 已开启 / dist 有 enabled
        节点（`nodes=[]` 的纯本地路径本就不派 A-eval）/ bc 权重文件在盘上。
        """
        args = self._host.args
        if args.mode != "per-tick":
            return None
        if getattr(args, "course_obj", None) is None:
            return None
        if int(getattr(args, "eval_games_per_stage", 0) or 0) <= 0:
            return None
        if self._host.eval_every <= 0:
            return None
        nodes = (dist_cfg or {}).get("nodes") or []
        if not any(n.get("enabled", True) for n in nodes):
            return None
        bc = str(getattr(args, "bc", "") or "")
        if not bc or not Path(bc).exists():
            return None
        return bc

    def maybe_dispatch(self, dist_cfg: dict | None) -> None:
        """it0 基线评估（bc 权重）——rollout 收官后派发，**落账前每轮重试**。

        为什么（2026-09-12 用户）：in-loop eval 的配对基准此前恒取日志里**第一条**
        eval 行，而那条基准随 run 起点漂移（resume 时首条可能是 it50，配对比的是
        中途两点，不是"学会了多少"）。改为恒定补一条 it0 = 课程 bc 权重的干净评估：
        跨腿可比，且与 `gates.baseline_win_rate` 同口径。

        重试语义（2026-09-13 评审修订）：只要 eval_log 里尚无**同 bc 指纹**的 it0
        summary（`baseline_summary_landed`），每轮 rollout 收官后都尝试派发——首次
        派发撞上节点瞬时全挂/权重 POST 全失败时（EvalDispatcher 只记日志跳过），
        下一轮自动补派，而不是等进程重启。落账即停（wver 缓存于 `_landed_wver`）；
        summary 带 dropped 也算落账（缺口在控制台诚实显示为「缺N」，不为填缺口无限
        重跑失败局）。

        本地参与（2026-10-26 改判）：走 `local_fallback_only=True`——不建/不复用
        `local_gate`，只有本轮没有任何可用 eval 节点（`nodes_ok` 为空）时由派发器
        启动本机兜底；健康集群纯远端。快照文件名仍按流分流（eval_dispatch 侧），
        并发重试轮不互相覆写。

        缺口重派（2026-10-08，plan/eval-baseline-undispatched §2 P1-1）：落账判据多一条
        阈值例外——`baseline_needs_retry` 看**最后一条**同 wver 的 it0 summary，
        `games <= 0` 或 `dropped/(games+dropped) > 2%` ⇒ 不算落账、继续重派（预算 3 轮，
        由**账本行数**导出 ⇒ 跨重启成立）。缺口小（如单局节点失败 1/400 = 0.25%）照旧
        落账，不因噪声重派。人工补基线那道门（`trainer/eval_a_once.py` 的幂等早退）
        用同一个 `baseline_needs_retry`（判据单点）。

        幂等：在飞线程即跳过；跨重启/重试由 `iter == 0` 的已评估键去重，只补缺口。
        失败绝不抛出——基线是观测设施，不得拖垮训练主线。
        """
        t_prev = self._thread
        if t_prev is not None and t_prev.is_alive():
            return
        try:
            bc = self.weights(dist_cfg)
            if bc is None:
                return
            from worker.eval_local import (
                BASELINE_DROP_FRAC_MAX,
                BASELINE_RETRY_MAX,
                baseline_needs_retry,
                baseline_summary_landed,
            )

            try:
                wver16 = common.distribution.weights_fingerprint(bc)[:16]
            except OSError:
                return  # bc 读不了（检查后被删？）：不派，派发侧同样会失败
            if wver16 == self._landed_wver:
                return
            ledger = Path(self._host.traj_dir).parent / "eval_log.jsonl"
            landed = baseline_summary_landed(self._host.traj_dir, wver16)
            if landed and not baseline_needs_retry(ledger, wver16):
                self._landed_wver = wver16
                return
            if landed:
                log(
                    f"[eval] it0 基线 summary 在但缺口 > {BASELINE_DROP_FRAC_MAX:.0%} "
                    f"（重派预算 {BASELINE_RETRY_MAX} 轮，计数按 wver 记在账本里）——重派补缺口"
                )
            from trainer.eval_dispatch import EvalDispatchSpec, dispatch_eval_bg
            from worker.eval_local import BASELINE_EVAL_ITER

            self._thread = dispatch_eval_bg(EvalDispatchSpec(
                self._host.bun,
                bc,
                self._host.traj_dir,
                self._host.args,
                dist_cfg or {},
                iter_id=f"{RUN_ID}.{BASELINE_EVAL_ITER}",
                it=BASELINE_EVAL_ITER,
                local_gate=None,
                baseline=True,
                local_fallback_only=True,
            ))
            log(
                f"[eval] it0 baseline dispatched（bc 权重：{bc}）——"
                "落账前每轮重试，结果见后续 [eval] 行"
            )
        except Exception as e:  # 基线派发失败不影响训练
            log(f"[eval] WARN it0 baseline dispatch failed (non-fatal): {type(e).__name__}: {e}")
