"""eval 的全局执行池：线程按**节点**持有，任务按 `Grant` 从仲裁器领取（2026-11-04）。

旧派发器每轮按节点起 `c` 条线程——两轮并发时同一节点会被开出两份额度，且第二轮
的线程只看得见第二轮自己的队列。本模块把线程和轮解耦：池子按节点 id（本机用
`"local"`）生长，线程循环 `take(node) → execute(grant)`；`Grant.round` 决定用哪份
权重 / iterId / course_fp / 快照 / 超时。

池子按节点许可可增可缩：轮收工/节点让位后多余线程收到退出信号，空闲线程下一轮
登记后仍可复用——这也是「所有 eval 局都派出后空闲 worker 进入竞速」的物理载体。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import common.distribution
from worker.eval_arbiter import LOCAL_NODE, EvalRound, Grant, eval_arbiter


@dataclass
class _Slot:
    """一条池线程 + 它的退出信号（缩容只置信号，不强杀在飞局）。"""

    thread: threading.Thread
    stop: threading.Event


class EvalExecutors:
    """按节点 id 的可缩容线程池（进程级单例；rollout 冻结由仲裁器负责）。"""

    def __init__(self, arbiter: Any | None = None) -> None:
        self._arb = arbiter if arbiter is not None else eval_arbiter()
        self._lock = threading.Lock()
        self._pools: dict[str, list[_Slot]] = {}
        # 每个 owner（一轮 eval）对节点的期望线程数；同节点取 max，轮收工时释放。
        self._wants: dict[str, dict[int | None, int]] = {}
        self._closed = False

    # —— 池子维护 ——

    def _set_want_locked(self, key: str, want: int, owner: Any | None) -> None:
        owners = self._wants.setdefault(key, {})
        owners[None if owner is None else id(owner)] = max(0, int(want))

    def _target_locked(self, key: str) -> int:
        return max(self._wants.get(key, {}).values(), default=0)

    def _resize_locked(self, key: str) -> int:
        """把池子对齐到当前期望数：多的置退出信号，少的下一拍补。"""
        pool = self._pools.setdefault(key, [])
        pool[:] = [s for s in pool if s.thread.is_alive()]
        target = self._target_locked(key)
        for i, slot in enumerate(pool):
            if i < target:
                slot.stop.clear()
            else:
                slot.stop.set()
        return target

    def _ensure(
        self,
        key: str,
        want: int,
        target: Any,
        name: str,
        owner: Any | None = None,
    ) -> int:
        self._lock.acquire()
        try:
            if self._closed:
                return 0
            self._set_want_locked(key, want, owner)
            target_n = self._resize_locked(key)
            pool = self._pools[key]
            add = max(0, target_n - len(pool))
            for _ in range(add):
                stop = threading.Event()
                t = threading.Thread(
                    target=target, args=(key, stop), daemon=True, name=name
                )
                pool.append(_Slot(t, stop))
                t.start()
            return add
        finally:
            self._lock.release()

    def ensure_node(
        self, nd: dict[str, Any], want: int, owner: Any | None = None
    ) -> int:
        nid = str(nd["id"])
        return self._ensure(nid, want, self._node_loop, f"eval-{nid}", owner)

    def ensure_local(self, want: int, owner: Any | None = None) -> int:
        return self._ensure(LOCAL_NODE, want, self._local_loop, "eval-local", owner)

    def release_owner(self, owner: Any) -> None:
        """一轮收工：撤掉它的期望数，池子按其余 owner 缩容。"""
        oid = id(owner)
        with self._lock:
            for key, owners in list(self._wants.items()):
                if oid not in owners:
                    continue
                owners.pop(oid)
                if not owners:
                    self._wants.pop(key, None)
                self._resize_locked(key)

    def live(self, nid: str) -> int:
        """池里还活着的线程数（含已置退出信号、正在跑最后一局的线程）。"""
        with self._lock:
            pool = self._pools.get(nid, [])
            pool[:] = [s for s in pool if s.thread.is_alive()]
            return len(pool)

    def active(self, nid: str) -> int:
        """还在领牌的线程数（缩容中/已置退出信号的不算）。"""
        with self._lock:
            pool = self._pools.get(nid, [])
            pool[:] = [s for s in pool if s.thread.is_alive()]
            return sum(1 for s in pool if not s.stop.is_set())

    def node_alive_for(self, rnd: EvalRound) -> bool:
        """本机改派判据：该轮还有**可路由**节点且它的池子还有能领牌的线程。"""
        now = time.time()
        return any(
            rnd.node_routable(nid, now) and self.active(nid) > 0
            for nid in rnd.node_ids()
        )

    def close_pools(self) -> None:
        with self._lock:
            self._closed = True
            for pool in self._pools.values():
                for slot in pool:
                    slot.stop.set()
            self._pools.clear()
            self._wants.clear()

    # —— 线程循环 ——

    def _node_loop(self, nid: str, stop: threading.Event) -> None:
        while True:
            if self._closed or stop.is_set():
                return
            grant = self._arb.take(nid, stop=stop)
            if grant is None:
                return
            try:
                self._execute_node(grant)
            except Exception as e:  # 单局异常不得杀死整条腿（旧 worker 同样兜底）
                self._log_round(grant.round, f"[eval] exec error (ignored): {type(e).__name__}: {e}")
                grant.round.settle_task(
                    grant,
                    ok=False,
                    err=f"{type(e).__name__}: {str(e)[:160]}",
                    transient=False,
                    node_alive=False,
                )

    def _local_loop(self, _nid: str, stop: threading.Event) -> None:
        while True:
            if self._closed or stop.is_set():
                return
            grant = self._arb.take(LOCAL_NODE, stop=stop)
            if grant is None:
                return
            try:
                self._execute_local(grant)
            except Exception as e:
                self._log_round(grant.round, f"[eval] local exec error (ignored): {type(e).__name__}: {e}")
                grant.round.settle_task(
                    grant,
                    ok=False,
                    err=f"{type(e).__name__}: {str(e)[:160]}",
                    transient=False,
                    node_alive=self.node_alive_for(grant.round),
                )

    @staticmethod
    def _log_round(rnd: EvalRound, msg: str) -> None:
        rnd.log(msg)

    # —— 单局执行 ——

    def _execute_node(self, grant: Grant) -> None:
        rnd = grant.round
        common.distribution.set_request_tag(rnd.req_scope)
        nd = rnd.node_info(grant.node)
        if nd is None:
            rnd.settle_task(
                grant,
                ok=False,
                err="node gone（POST 后节点被剔除）",
                transient=True,
                node_alive=False,
            )
            return
        task = grant.task
        ok = False
        err = ""
        transient = False
        task_lost = False
        manifest: dict[str, Any] = {}
        try:
            from worker.config import args_rollout_overrides, stage_json_for_args

            _ov = args_rollout_overrides(rnd.args)
            manifest, _files = common.distribution.fetch_task(
                nd["url"],
                nd["key"],
                iter_id=rnd.eval_iter_id,
                wver=rnd.wver,
                stage=task[0],
                seed=task[1],
                max_ticks=rnd.args.max_ticks,
                difficulty=rnd.args.difficulty,
                timeout=rnd.task_timeout,
                mode="eval",
                kind=rnd.weights_kind,
                course_fp=rnd.course_fp,
                stage_json=stage_json_for_args(rnd.args, task[0]) or "",
                decision_events=bool(getattr(rnd.args, "decision_events", False)),
                decision_k=int(getattr(rnd.args, "decision_k", 10)),
                skip_steel_only_shots=bool(getattr(rnd.args, "skip_steel_only_shots", False)),
                break_bricks_on_path=bool(getattr(rnd.args, "break_bricks_on_path", False)),
                suppress_useless_fire=bool(getattr(rnd.args, "suppress_useless_fire", False)),
                suppress_lead_miss_fire=bool(getattr(rnd.args, "suppress_lead_miss_fire", False)),
                lives_override=int(_ov["lives_override"]) if "lives_override" in _ov else None,
                player_level=int(_ov["player_level"]) if "player_level" in _ov else None,
            )
            why = common.distribution.validate_eval_result(manifest, rnd.wver)
            if why:
                raise common.distribution.DistError(0, why)
            ok = True
        except Exception as e:
            err = str(e)[:200]
            transient = common.distribution.is_transient_error(e)
            if (
                isinstance(e, common.distribution.DistError)
                and e.status == 409
                and common.distribution.refresh_weights(
                    nd,
                    iter_id=rnd.eval_iter_id,
                    wver=rnd.wver,
                    weights_bytes=rnd.weights_bytes or b"",
                    timeout=min(300.0, max(60.0, rnd.task_timeout)),
                    kind=rnd.weights_kind,
                    err=err,
                    log=lambda m: self._log_round(rnd, m),
                )
            ):
                transient = True
            task_lost = common.distribution.is_task_lost_error(e)
            if task_lost:
                common.distribution.forget_weights_node(
                    nd["id"], kind=rnd.weights_kind
                )
                transient = True
        wall_sec = round(time.time() - grant.started, 3)
        accepted = rnd.settle_task(
            grant,
            ok=ok,
            manifest=manifest,
            err=err,
            transient=transient,
            task_lost=task_lost,
            wall_sec=wall_sec,
            node_alive=self.node_alive_for(rnd),
        )
        if accepted:
            self._record_and_log(rnd, grant, manifest, wall_sec)

    def _execute_local(self, grant: Grant) -> None:
        rnd = grant.round
        common.distribution.set_request_tag(rnd.req_scope)
        task = grant.task
        ok = False
        err = ""
        err_errno = ""
        msg_head = ""
        transient = False
        manifest: dict[str, Any] = {}
        try:
            from worker.config import args_rollout_overrides, stage_json_for_args

            _ov = args_rollout_overrides(rnd.args)
            manifest = rnd.local_fn(
                rnd.bun,
                rnd.snapshot_path,
                task[0],
                task[1],
                rnd.eval_dir / "local-eval" / f"rl_s{task[0]}_seed{task[1]}",
                max_ticks=rnd.args.max_ticks,
                difficulty=rnd.args.difficulty,
                timeout_sec=rnd.task_timeout,
                wver=rnd.wver,
                stage_json=stage_json_for_args(rnd.args, task[0]) or "",
                lives_override=int(_ov["lives_override"]) if "lives_override" in _ov else None,
                player_level=int(_ov["player_level"]) if "player_level" in _ov else None,
                decision_events=bool(getattr(rnd.args, "decision_events", False)),
                decision_k=int(getattr(rnd.args, "decision_k", 10)),
                skip_steel_only_shots=bool(getattr(rnd.args, "skip_steel_only_shots", False)),
                break_bricks_on_path=bool(getattr(rnd.args, "break_bricks_on_path", False)),
                suppress_useless_fire=bool(getattr(rnd.args, "suppress_useless_fire", False)),
                suppress_lead_miss_fire=bool(getattr(rnd.args, "suppress_lead_miss_fire", False)),
            )
            why = common.distribution.validate_eval_result(manifest, rnd.wver)
            if why:
                raise common.distribution.DistError(0, why)
            ok = True
        except Exception as e:
            err = str(e)[:200]
            err_errno = str(getattr(e, "errno_code", "") or "")
            msg_head = str(getattr(e, "msg_head", "") or "")[:160]
            transient = common.distribution.is_transient_error(e)
        wall_sec = round(time.time() - grant.started, 3)
        accepted = rnd.settle_task(
            grant,
            ok=ok,
            manifest=manifest,
            err=err,
            transient=transient,
            err_errno=err_errno,
            msg_head=msg_head,
            wall_sec=wall_sec,
            node_alive=self.node_alive_for(rnd),
        )
        if accepted:
            self._record_and_log(rnd, grant, manifest, wall_sec)

    def _record_and_log(
        self,
        rnd: EvalRound,
        grant: Grant,
        manifest: dict[str, Any],
        wall_sec: float | None,
    ) -> None:
        try:
            recorded = rnd.record_with_retry(grant, manifest, wall_sec)
        finally:
            rnd.finish_write()
        if not recorded:
            return
        task = grant.task
        el = manifest.get("elapsedSec")
        self._log_round(
            rnd,
            f"[eval] {rnd.landed_count()}/{rnd.total} s{task[0]}/seed{task[1]} "
            f"node={grant.node} outcome={manifest.get('outcome')} "
            f"ticks={manifest.get('ticks')} "
            f"elapsed={str(el) + 's' if el is not None else '-'}",
        )


_EXECUTORS: EvalExecutors | None = None


def eval_executors() -> EvalExecutors:
    global _EXECUTORS
    if _EXECUTORS is None:
        _EXECUTORS = EvalExecutors()
    return _EXECUTORS


def reset_eval_executors() -> EvalExecutors:
    global _EXECUTORS
    if _EXECUTORS is not None:
        _EXECUTORS.close_pools()
    _EXECUTORS = EvalExecutors()
    return _EXECUTORS
