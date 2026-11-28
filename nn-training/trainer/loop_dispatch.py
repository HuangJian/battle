"""loop_dispatch —— **本轮派发与让位**：协作对象 `Dispatcher`（2026-11-28，P0-1 第四刀）。

原先是 `RoundSteps` 的第二个基类（`TrainingDispatch`，S4 第二十刀从 `loop_core.py` 拆出）。
四个成员共享一个判据源：**本轮把活派给谁 / 让位给谁**。

- `rollout_phase`：采集派发三路（dist 流式 / dist 串行 / 纯本地），结果**作为值返回**
  （`RolloutOutcome`；宿主自己落到 `_report` 等槽位 —— 组合根拥有状态，对象只算）。
- `eval_on_round`：本轮是否派 A-eval（吞吐 T3 稀疏化的三条件：eval-games / eval_every / eval_at）。
- `evalboard_yield`：rollout 抢占时**关窗**（在途 B/C 局停派新 seed），并写 R4-G1 心跳。
- `finalize_returned_shards`：轮末把 dispatch 腿的分关账写盘（零落盘）。

## 谁读谁写

* 宿主读面：`args` / `bun` / `update_kwargs` / `ppo_backend` 是**活引用**（对象不拷副本），
  `traj_dir` / `jsonl_path` / `course_fp` / `corpus_fp` / `extra_wver` / `eval_every` /
  `eval_at_set` / `eb_window` / `eb_thread` / `model` / `opt` / `device` / `ref_model` /
  `start_it` / `payload_parts` 是只读 property（`_traj_dir` 每轮重指、`_eb_thread` 会被换掉）。
* 宿主写面只有两条、都**具名**：`land_rollout(outcome)`（六个轮级槽一次性落位）与
  `eb_thread` 的 setter（关窗后清句柄）。对象不直接伸手写 `_report` / `_eval_thread` 这类槽。

## `_eval_on_round` 的顺序契约（本刀的关键）

`trainer/loop_eval.py` 的同名成员是**占位**（body 用 `raise`，MRO 被改坏就响亮失败）。原先靠
「`TrainingDispatch` 的线性化位置早于 `TrainingEval`」保证真实现胜出；拆成协作对象后，真正的
保证变成**组合根本体的转发壳**：`TrainingLoop._eval_on_round` 是类体里定义的方法，而
`TrainingLoop` 是所有混入的**第一个**类 ⇒ 永远先于任何基类解析，占位不可能反过来胜出。
守卫（`test_loop_core_tail_split.py::test_eval_on_round_position_contract`）从「比 MRO 下标」
改成钉这两件事：壳住在 `TrainingLoop.__dict__`、且它**不是** `TrainingEval` 的那个对象。

## DI seam

`trainer.rollout_phase`（`dispatch_rollout_phase`）· `trainer.batch_eval`（`maybe_dispatch_batch`）·
`worker.eval_heartbeat`（`write_state`）三处**延迟** import：那是原有的注入面，测试 patch 的一直是
实现模块（`monkeypatch.setattr(ed, "dispatch_eval_bg", …)` 之类），搬家不改注入点。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from common.log import default_router
from remote.payload_pack import ShardSegments
from trainer.rollout_phase import (
    RolloutPhaseSpec,
    begin_payload_parts,
    dispatch_rollout_phase,
)


@dataclass(frozen=True)
class RolloutOutcome:
    """一轮采集派发的六件结果（宿主 `land_rollout` 一次落位；字段名 = 旧槽位名去下划线）。"""

    report: Any
    stream_meta: Any
    eval_thread: Any
    eval_gate: Any
    collect_child: Any
    spawned_early: Any


class DispatchHost(Protocol):
    """`Dispatcher` 需要的宿主面（全是活读数；写面只有 `land_rollout` / `eb_thread`）。"""

    args: Any
    bun: Any
    update_kwargs: Any
    ppo_backend: Any

    @property
    def traj_dir(self) -> Path: ...

    @property
    def jsonl_path(self) -> Path: ...

    @property
    def course_fp(self) -> str | None: ...

    @property
    def corpus_fp(self) -> str | None: ...

    @property
    def extra_wver(self) -> str | None: ...

    @property
    def eval_every(self) -> int: ...

    @property
    def eval_at_set(self) -> set[int]: ...

    @property
    def eb_window(self) -> threading.Event: ...

    @property
    def eb_thread(self) -> threading.Thread | None: ...

    @eb_thread.setter
    def eb_thread(self, value: threading.Thread | None) -> None: ...

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
    def payload_parts(self) -> ShardSegments: ...

    def land_rollout(self, outcome: RolloutOutcome) -> None:
        """把一轮派发的六个结果落到宿主的轮级槽位（唯一写面）。"""
        ...


class Dispatcher:
    """本轮派发与让位（采集三路 / A-eval 稀疏化 / EvalBoard 关窗；见本模块头注）。"""

    def __init__(self, host: DispatchHost) -> None:
        self._host = host

    def eval_on_round(self, it: int) -> bool:
        """吞吐 T3：本轮是否派发干净评估。per-tick 按 eval-games/eval-every/eval-at
        三条件；intent/goal 按 eval_at（默认 '5,10,15'）——别的模式不派发不 join。"""
        args = self._host.args
        if args.mode == "per-tick":
            eval_every = self._host.eval_every
            eval_at_set = self._host.eval_at_set
            return (
                int(getattr(args, "eval_games_per_stage", 0) or 0) > 0
                and eval_every > 0
                and (eval_every == 1 or it % eval_every == 0)
                and (not eval_at_set or it in eval_at_set)
            )
        return it in self._host.eval_at_set

    def evalboard_yield(self) -> None:
        """rollout 抢占：关窗让出集群。在途 B/C 局停派新 seed（window_event 清位）。"""
        self._host.eb_window.clear()
        t = self._host.eb_thread
        if t is not None and t.is_alive():
            # 短等在途局收尾；不阻塞训练主链（超时即走，剩余 seed 下窗续跑）。
            t.join(timeout=15.0)
        self._host.eb_thread = None
        # R4-G1 心跳：关窗 + 关窗时刻（console 只读，用来显示「训练忙碌中已等 N 分钟」）。
        try:
            from worker.eval_heartbeat import now_ms, write_state

            write_state(window_open=False, last_window_closed_ts=now_ms())
        except Exception as e:  # 同上：心跳失败不得打断训练，但要说一声
            default_router().warn_once(
                "eval_heartbeat:close", f"关窗心跳没写成：{e!r}——console 会显示旧状态"
            )

    def rollout_phase(
        self,
        it: int,
        pairs: list[tuple[int, int]],
        dist_cfg: dict | None,
        eval_on_round: bool,
        on_collect_end: Callable[..., Any] | None = None,
        on_dispatch_drained: Callable[..., Any] | None = None,
        on_dispatch_active: Callable[..., Any] | None = None,
    ) -> RolloutOutcome:
        """单轮采集派发（三路：dist 流式 / dist 串行 / 纯本地）；结果作为值返回。"""
        host = self._host
        args = host.args
        # 零落盘（plan/payload-zero-disk §3.2）：本轮段的账从这一轮开始记（换轮即清空）。
        # 必须在派发**之前**：dispatch 线程一回传就会往里面写。
        begin_payload_parts(args, it, host.payload_parts)
        (report, stream_meta, eval_thread, eval_gate, collect_child, spawned_early) = (
            dispatch_rollout_phase(
                RolloutPhaseSpec(
                    args=args,
                    bun=host.bun,
                    dist_cfg=dist_cfg,
                    it=it,
                    traj_dir=host.traj_dir,
                    pairs=pairs,
                    jsonl_path=host.jsonl_path,
                    model=host.model,
                    opt=host.opt,
                    device=host.device,
                    ppo_backend=host.ppo_backend,
                    update_kwargs=host.update_kwargs,
                    start_it=host.start_it,
                    ref_model=host.ref_model,
                    extra_wver=host.extra_wver,
                    eval_on_round=eval_on_round,
                    course_fp=host.course_fp,
                    corpus_fp=host.corpus_fp,
                    # 收到就压（手工段）：缺的 shard（local 腿/预采子进程）打包时再读盘补。
                    on_shard=host.payload_parts.add,
                    # 采集收官（stream 的 collector 退出）→ 解冻本课 eval；串行路径由
                    # step_rollout 的 finally 解冻，这里传 None 也无害。
                    on_collect_end=on_collect_end,
                    on_dispatch_drained=on_dispatch_drained,
                    on_dispatch_active=on_dispatch_active,
                )
            )
        )
        outcome = RolloutOutcome(
            report=report,
            stream_meta=stream_meta,
            eval_thread=eval_thread,
            eval_gate=eval_gate,
            collect_child=collect_child,
            spawned_early=spawned_early,
        )
        # 槽位归组合根：一次具名落位（对象不直接写 `_report` / `_eval_thread` 这类名字）。
        host.land_rollout(outcome)
        return outcome

    def finalize_returned_shards(self, it: int) -> None:
        """零落盘：轮末把本轮 dispatch 腿（sampler agent）的分关账写盘（2026-10-28）。

        回传 shard 本身**从未落盘**（`common.shard.write_shard` 见到段账就只交字节、不写盘），这里只写
        几百字节的 `it<N>/stage_est.json` —— `trailing_stage_samples_per_game` 在盘上没有
        shard 目录时靠它（`est_agg=latest` 才不会静默退回全局 est）。

        **顺序契约**：调用点固定在 `step_record_iteration` 的**最后**（`_record_iteration` →
        `_write_iter_stats` → 巡检之后）——前三步还要读内存账 / local 腿的盘上 shard。
        """
        parts = self._host.payload_parts
        if int(getattr(parts, "it", -1)) != int(it):
            return
        from trainer.zero_disk import finalize_round_shards

        finalize_round_shards(self._host.traj_dir, parts.manifests(), it=int(it))
