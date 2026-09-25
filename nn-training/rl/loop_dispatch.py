"""loop_dispatch —— **本轮派发与让位** mixin（2026-09-25 从 rl/loop_core.py 拆出，S4 第二十刀）。

三个成员共享一个判据源：**本轮把活派给谁 / 让位给谁**。

- `_rollout_phase`：采集派发三路（dist 流式 / dist 串行 / 纯本地），结果落 `self._report`
  （`rl.rollout_phase.dispatch_rollout_phase` 是它的实现口，**延迟** import 保留）。
- `_eval_on_round`：本轮是否派 A-eval（吞吐 T3 稀疏化的三条件：eval-games / eval_every / eval_at）。
- `_evalboard_yield`：rollout 抢占时**关窗**（在途 B/C 局停派新 seed），并写 R4-G1 心跳。

## 宿主：`RoundSteps`，且 `_eval_on_round` 必须**早于** `TrainingEval` 的占位

唯一父调用者是 `RoundSteps`（`step_rollout` / `step_eval_dispatch` / `step_record_iteration`），
另有 `TrainingEval` / `TrainingGuards` 以 `self.` 调 `_eval_on_round`——三者**互不继承**（祖先集
交集为空），但本簇**不需要**组合根：挂在 `RoundSteps` 的基类里就已经在 `TrainingLoop` 的 MRO 上
（`RoundSteps` 是它的第一个基类）⇒ 三条入边照旧解析。

⚠ `_eval_on_round` 有一处**顺序契约**：`rl/loop_eval.py` 的同名成员是**占位**（body 用 `raise`，
MRO 被改坏就响亮失败），真实现必须**在 MRO 里更靠前**。挂 `RoundSteps` 一侧天然满足
（`RoundSteps` 基类的线性化位置早于 `TrainingSteps` 的基类 `TrainingEval`）；若把本簇改成
`TrainingLoop` 的**末位**基类，占位会反过来胜出 ⇒ 守卫把这条顺序钉住。

## DI seam

`rl.rollout_phase`（`dispatch_rollout_phase`）· `rl.batch_eval`（`maybe_dispatch_batch`）·
`rl.eval_heartbeat`（`write_state`）三处**延迟** import：那是原有的注入面，测试 patch 的一直是
实现模块。
"""

from __future__ import annotations

from typing import Any

from rl.rollout_phase import dispatch_rollout_phase


class TrainingDispatch:
    """本轮派发与让位（采集三路 / A-eval 稀疏化 / EvalBoard 关窗；见本模块头注）。"""

    # 依赖的 `TrainingLoop` 实例属性（声明类型供 mypy/阅读）。与其它混入里的同类声明**有意并存**。
    args: Any
    bun: Any
    update_kwargs: Any
    _model: Any
    _opt: Any
    _device: Any
    _ref_model: Any
    _start_it: Any
    _traj_dir: Any
    _jsonl_path: Any
    _extra_wver: Any
    _course_fp: Any
    _corpus_fp: Any
    _report: Any
    _stream_meta: Any
    _eval_thread: Any
    _eval_gate: Any
    _collect_child: Any
    _spawned_early: Any
    _eval_every: Any
    _eval_at_set: Any
    _eb_window: Any
    _eb_thread: Any

    #: 兄弟混入的方法（混入常态：在组合实例上解析）——声明类型，理由同 `rl/loop_volume.py`。
    ppo_backend: Any

    def _eval_on_round(self, it: int) -> bool:
        """吞吐 T3：本轮是否派发干净评估。per-tick 按 eval-games/eval-every/eval-at
        三条件；intent/goal 按 eval_at（默认 '5,10,15'）——别的模式不派发不 join。

        本实现 MRO 胜过 `TrainingEval._eval_on_round` 的占位（`rl/loop_eval.py`，
        S4 第十七刀）——占位存在是为了「MRO 被改坏就响亮失败」。"""
        args = self.args
        if args.mode == "per-tick":
            return (
                int(getattr(args, "eval_games_per_stage", 0) or 0) > 0
                and self._eval_every > 0
                and (self._eval_every == 1 or it % self._eval_every == 0)
                and (not self._eval_at_set or it in self._eval_at_set)
            )
        return it in self._eval_at_set

    def _evalboard_yield(self) -> None:
        """rollout 抢占：关窗让出集群。在途 B/C 局停派新 seed（window_event 清位）。"""
        self._eb_window.clear()
        t = self._eb_thread
        if t is not None and t.is_alive():
            # 短等在途局收尾；不阻塞训练主链（超时即走，剩余 seed 下窗续跑）。
            t.join(timeout=15.0)
        self._eb_thread = None
        # R4-G1 心跳：关窗 + 关窗时刻（console 只读，用来显示「训练忙碌中已等 N 分钟」）。
        try:
            from rl.eval_heartbeat import now_ms, write_state

            write_state(window_open=False, last_window_closed_ts=now_ms())
        except Exception:
            pass

    def _rollout_phase(
        self, it: int, pairs: list[tuple[int, int]], dist_cfg: dict | None, eval_on_round: bool
    ) -> None:
        """单轮采集派发（三路：dist 流式 / dist 串行 / 纯本地），结果落 self._report。"""
        args = self.args
        (report, stream_meta, eval_thread, eval_gate, collect_child, spawned_early) = (
            dispatch_rollout_phase(
                args,
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
                eval_on_round,
                course_fp=self._course_fp,
                corpus_fp=self._corpus_fp,
            )
        )
        self._report = report
        self._stream_meta = stream_meta
        self._eval_thread = eval_thread
        self._eval_gate = eval_gate
        self._collect_child = collect_child
        self._spawned_early = spawned_early
