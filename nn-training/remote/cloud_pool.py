"""cloud_pool.py — 云机跨轮 serve pool 的归属（L0，无业务依赖）。

**为什么单独一层**：`worker/serve_pool.py` 只管「一个池怎么跑局」，不负责「池跨 job 活多久」。
把生命周期放进 `worker/` 会把「谁拥有池」变成 worker 的第二个职责；放进 `remote/worker.py`
又会让 `offline_eval` / `run_loop` / `plan_run` 各自复制一份。这里只做一件事：

    同一条腿（rollout / eval）+ 同一个 TS 代码 sha ⇒ 同一个暖池，可跨轮取回。

**所有权**是显式的：
  * `take()` 把池从 keeper 摘走（同一时刻只有一个调用方拥有它）；
  * `put()` 在轮末把池放回；key 不同/宽度不够/目录被 prune/池已 closed ⇒ 关旧、返回 None；
  * `close_all()` 是段末/进程退出的兜底，幂等。

**key 是 `ts_code_sha256`，不是目录路径**：scratch 镜像的 basename 是
`nn-rollout-ts-<48hex>`（62 字符）且 `_slug` 还会截断；从路径反推必然在 scratch 路径上失败。
调用方负责把 manifest 的 sha 传进来；本模块只按字符串比较，不猜来源。

**eval 的 atexit 竞态**：`CloudEvalRunner` 是后台线程，进程退出时它可能正在写盘。`run_cloud_eval`
用 `set_eval_busy(True/False)` 标出在飞区间；`CLOUD_EVAL_POOL.close_all()` 在 busy 时跳过并记一行，
不硬杀正在写 shard 的子进程（硬杀另立，见 plan §7）。正常段末由 `run_standalone` 的 finally 收。
"""

from __future__ import annotations

import atexit
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: 跨轮保留的开关。缺省开（函数缺省）；notebook 会按可用内存显式写 `0/1`。
ENV_KEEP = "NN_CLOUD_SERVE_POOL_KEEP"

_OFF_VALUES = {"0", "false", "off", "no"}

#: eval 在飞区间（`run_cloud_eval` 设置）。只用于 atexit/段末的 close_all 判据。
_EVAL_BUSY = threading.Event()


def keep_enabled() -> bool:
    """跨轮保池是否开启。空/未设置 = 开；`0/false/off/no` = 关。"""
    raw = (os.environ.get(ENV_KEEP) or "").strip().lower()
    return raw not in _OFF_VALUES


def set_eval_busy(busy: bool) -> None:
    """标记云机 eval 是否在飞（`run_cloud_eval` 的整个函数体）。"""
    if busy:
        _EVAL_BUSY.set()
    else:
        _EVAL_BUSY.clear()


def eval_busy() -> bool:
    return _EVAL_BUSY.is_set()


class CloudPoolKeeper:
    """一条腿的跨轮池槽位（线程安全；只保存**一个**池）。"""

    def __init__(self, leg: str, *, log: Any = None) -> None:
        self.leg = str(leg)
        self._log = log or (lambda msg: None)
        self._lock = threading.Lock()
        self._key: str | None = None
        self._pool: Any = None
        self._ts_dir: Path | None = None

    @property
    def enabled(self) -> bool:
        return keep_enabled()

    def _log_line(self, msg: str) -> None:
        try:
            self._log(f"[pool:{self.leg}] {msg}")
        except Exception:
            pass

    def _detach_locked(self) -> Any:
        pool, self._pool = self._pool, None
        self._key = None
        self._ts_dir = None
        return pool

    def _close(self, pool: Any) -> None:
        if pool is None:
            return
        try:
            pool.close()
        except Exception as e:  # 收池不能把退出路径弄挂
            self._log_line(f"收池异常（忽略）：{type(e).__name__}: {e}")

    def take(self, key: str, *, min_workers: int = 0) -> Any:
        """取回同 key 的暖池；没有/不可用 ⇒ `None`（调用方新建）。

        `min_workers` 只判「够不够」：够（包括更宽）就复用；窄了关旧、返回 None。
        宽度判断只应在一处做（调用方或这里），不要两边同时关。
        """
        key = str(key or "").strip().lower()
        old: Any = None
        with self._lock:
            if not keep_enabled():
                old = self._detach_locked()
                pool = None
            elif self._pool is None:
                return None
            elif self._key != key:
                old = self._detach_locked()
                pool = None
            else:
                pool = self._detach_locked()
        if old is not None:
            self._close(old)
        if pool is None:
            return None
        if bool(getattr(pool, "closed", False)):
            self._close(pool)
            return None
        width = int(getattr(pool, "max_workers", 0) or 0)
        if width < int(min_workers or 0):
            self._close(pool)
            return None
        ts_dir = getattr(pool, "ts_dir", None)
        if ts_dir is not None and not Path(ts_dir).is_dir():
            self._log_line(f"TS 目录已不在（{ts_dir}）——关旧池")
            self._close(pool)
            return None
        hook = getattr(pool, "begin_round", None)
        if callable(hook):
            try:
                hook()
            except Exception as e:
                # 计数不准不能把一轮弄挂；池本身仍可用。
                self._log_line(f"begin_round 异常（忽略，继续用池）：{type(e).__name__}: {e}")
        return pool

    def put(self, key: str, pool: Any) -> None:
        """轮末存回；已有旧槽位就关掉旧的（同一时刻只保留一个池）。"""
        if pool is None:
            return
        if not keep_enabled() or bool(getattr(pool, "closed", False)):
            self._close(pool)
            return
        key = str(key or "").strip().lower()
        old: Any = None
        with self._lock:
            old = self._pool
            self._pool = pool
            self._key = key
            ts_dir = getattr(pool, "ts_dir", None)
            self._ts_dir = Path(ts_dir) if ts_dir is not None else None
        if old is not None and old is not pool:
            self._close(old)

    def close_all(self) -> None:
        """段末/atexit 收池；幂等。eval 在飞时跳过（后台线程可能正在写盘）。"""
        if self.leg == "eval" and eval_busy():
            self._log_line("评估仍在飞——跳过 close_all（交给 CloudEvalRunner 的收线/进程退出）")
            return
        with self._lock:
            pool = self._detach_locked()
        self._close(pool)


#: 两条腿各自一个槽位（不共享：宽度/生命周期/收线纪律不同）。
CLOUD_ROLLOUT_POOL = CloudPoolKeeper("rollout")
CLOUD_EVAL_POOL = CloudPoolKeeper("eval")


def acquire_rollout(key: str, log: Callable[[str], None]) -> Any:
    """取回同 key 的暖 rollout 池；不可用返回 None，并保留原来的可见日志。"""
    if not CLOUD_ROLLOUT_POOL.enabled:
        return None
    if len(key) != 64:
        log(f"WARN ts_code_sha256 不是 64 hex（{key!r}）——本轮不跨轮保池")
        return None
    pool = CLOUD_ROLLOUT_POOL.take(key)
    if pool is not None:
        log(f"[pool] 复用跨轮暖池 {pool.max_workers} 宽（ts={key[:12]}…）")
    return pool


def release_rollout(key: str, pool: Any) -> None:
    """轮末归还 rollout 池；开关关了就关池。"""
    if pool is None:
        return
    if CLOUD_ROLLOUT_POOL.enabled:
        CLOUD_ROLLOUT_POOL.put(key, pool)
    else:
        pool.close()


atexit.register(CLOUD_ROLLOUT_POOL.close_all)
atexit.register(CLOUD_EVAL_POOL.close_all)
