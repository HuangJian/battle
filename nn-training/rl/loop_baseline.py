"""loop_baseline —— **it0 基线评估** mixin（2026-09-25 从 rl/loop_core.py 拆出，S4 第二十刀）。

两个成员是一条真链（实测连通分量）：`_maybe_dispatch_baseline_eval` → `_baseline_eval_weights`。
判据同源：**「日志里有没有这一份 bc 权重的 it0 干净评估」**——指纹（`dist_common.weights_fingerprint`
前 16 位）+ 落地摘（`baseline_summary_landed`）两者一起决定「派 / 不派」，`_baseline_eval_weights`
只是它前半段的前置条件（纯判断、零副作用）。

## 宿主：`RoundSteps`

本簇的**唯一**父调用者（mixin 级）是 `RoundSteps`（`step_course_iter`）⇒ 按本仓规则「调用者
依赖被调用者」挂 `RoundSteps` 一侧（`class RoundSteps(TrainingVolume, …, TrainingBaseline)`）。
组合实例上 `self._maybe_dispatch_baseline_eval(...)` 的解析与搬家前逐字相同。

## DI seam 是**本模块**的

基线派发走两处**延迟** import（`rl.eval_local.baseline_summary_landed` / `rl.eval_dispatch.
dispatch_eval_bg`）：搬家前它们在本模块**未**引入 ⇒ 测本簇实现的用例必须 patch
`rl.eval_dispatch.*` / `rl.eval_local.*`（实现模块），与本模块的命名空间无关。`RUN_ID` /
`dist_common` / `log` 则是本模块的**模块全局**（按名字解析）：这三者才是本模块自己的注入点。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dist_common
from rl.log import log
from rl.queue import RUN_ID


class TrainingBaseline:
    """it0 基线评估（2 方法 = 一条链；见本模块头注）。"""

    # 依赖的 `TrainingLoop` 实例属性（声明类型供 mypy/阅读；实际赋值在 `TrainingLoop.__init__`
    # 与各兄弟混入）。与其它混入里的同类声明**有意并存**：混入的状态契约必须在**每个**文件里对
    # mypy 可见，运行期的唯一真相仍是同一实例上的那一份。
    args: Any
    bun: Any
    _traj_dir: Any
    _eval_gate: Any
    _eval_every: Any
    _baseline_eval_thread: Any
    _baseline_landed_wver: Any

    def _baseline_eval_weights(self, dist_cfg: dict | None) -> str | None:
        """it0 基线可用的 bc 权重路径；前置条件不足返回 None（纯判断，零副作用）。

        条件：per-tick 课程 / 有课程上下文 / in-loop eval 已开启 / dist 有 enabled
        节点（`nodes=[]` 的纯本地路径本就不派 A-eval）/ bc 权重文件在盘上。
        """
        args = self.args
        if args.mode != "per-tick":
            return None
        if getattr(args, "course_obj", None) is None:
            return None
        if int(getattr(args, "eval_games_per_stage", 0) or 0) <= 0:
            return None
        if self._eval_every <= 0:
            return None
        nodes = (dist_cfg or {}).get("nodes") or []
        if not any(n.get("enabled", True) for n in nodes):
            return None
        bc = str(getattr(args, "bc", "") or "")
        if not bc or not Path(bc).exists():
            return None
        return bc

    def _maybe_dispatch_baseline_eval(self, dist_cfg: dict | None) -> None:
        """it0 基线评估（bc 权重）——rollout 收官后派发，**落账前每轮重试**。

        为什么（2026-09-12 用户）：in-loop eval 的配对基准此前恒取日志里**第一条**
        eval 行，而那条基准随 run 起点漂移（resume 时首条可能是 it50，配对比的是
        中途两点，不是"学会了多少"）。改为恒定补一条 it0 = 课程 bc 权重的干净评估：
        跨腿可比，且与 `gates.baseline_win_rate` 同口径。

        重试语义（2026-09-13 评审修订）：只要 eval_log 里尚无**同 bc 指纹**的 it0
        summary（`baseline_summary_landed`），每轮 rollout 收官后都尝试派发——首次
        派发撞上节点瞬时全挂/权重 POST 全失败时（EvalDispatcher 只记日志跳过），
        下一轮自动补派，而不是等进程重启。落账即停（wver 缓存于
        `_baseline_landed_wver`）；summary 带 dropped 也算落账（缺口在控制台诚实
        显示为「缺N」，不为填缺口无限重跑失败局）。

        本地参与：复用当轮 `self._eval_gate`（与 A-eval 同一把门，PPO 收官
        `_join_eval` 置位）——基线本地局与 A-eval 一样让位 PPO，且快照文件名按流
        分流（eval_dispatch 侧），并发重试轮不互相覆写。

        幂等：在飞线程即跳过；跨重启/重试由 `iter == 0` 的已评估键去重，只补缺口。
        失败绝不抛出——基线是观测设施，不得拖垮训练主线。
        """
        t_prev = self._baseline_eval_thread
        if t_prev is not None and t_prev.is_alive():
            return
        try:
            bc = self._baseline_eval_weights(dist_cfg)
            if bc is None:
                return
            from rl.eval_local import baseline_summary_landed

            try:
                wver16 = dist_common.weights_fingerprint(bc)[:16]
            except OSError:
                return  # bc 读不了（检查后被删？）：不派，派发侧同样会失败
            if wver16 == self._baseline_landed_wver:
                return
            if baseline_summary_landed(self._traj_dir, wver16):
                self._baseline_landed_wver = wver16
                return
            from rl.eval_dispatch import dispatch_eval_bg
            from rl.eval_local import BASELINE_EVAL_ITER

            self._baseline_eval_thread = dispatch_eval_bg(
                self.bun,
                bc,
                self._traj_dir,
                self.args,
                dist_cfg or {},
                iter_id=f"{RUN_ID}.{BASELINE_EVAL_ITER}",
                it=BASELINE_EVAL_ITER,
                local_gate=self._eval_gate,
                baseline=True,
            )
            log(
                f"[eval] it0 baseline dispatched（bc 权重：{bc}）——"
                "落账前每轮重试，结果见后续 [eval] 行"
            )
        except Exception as e:  # 基线派发失败不影响训练
            log(f"[eval] WARN it0 baseline dispatch failed (non-fatal): {type(e).__name__}: {e}")
