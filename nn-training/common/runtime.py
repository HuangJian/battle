"""并发的**唯一所有者**：`Runtime` —— executor 生命周期、线程命名、取消点（§P1-6 / B6）。

## 为什么要有这个对象

2026-11-17 量到全仓 **10 处** `with ThreadPoolExecutor(...) as ex:` 就地建池（生产包 9 + `tools/e2a_readout` 1），
四件事一件也守不住：

1. **没有所有者**：池的生死绑在词法作用域上 —— 一个提前 `return`、一次异常、一个 `break` 就把
   「还有在飞的活」和「池」分家，而 `close` 由解释器替你做，你没法说「这一轮结束了、别再投了」。
2. **没有上限**：`min(8, len(nodes))` 在 `common/distribution` 出现**两次**，`len(items)` 一次，
   `n_slots`（云 eval 槽位，按几十量级）一次；同一进程里两层并发叠加，谁都不报数。
   `describe()` 是让这个数第一次可见的那一行。
3. **没有取消点**：`halt_event` 是本仓的事实约定（6 个模块传它），但它到不了池边 ——
   `submit` 完就只能等。`check()` / `sleep()` / `stop=` 把熔断接到池边（**接哪儿由调用方定**）。
4. **线程名各写各的**：10 处里只有 1 处写了 `thread_name_prefix`，`py-spy` 里 8 个
   `ThreadPoolExecutor-3_0` 分不清是谁 —— 与 G15 同一条理由（转储里名字是唯一身份）。

## 这一刀**没做**什么（写在这里，免得下一个人当成隐藏的债）

* **不节流**：`pool(workers=N)` 的 N 是调用方给的（多半来自 `--workers` / 课程配置），一律照办 ——
  静默砍并发是拿吞吐换好看的数字，而这个仓的并发数**此前没有读数**可依据。上限只约束**隐式**
  请求（`workers=None`）与 `describe()["over"]` 的报数。
* **不读 env**：上限走 `Runtime(ceiling=)` 参数。`os.environ` 直读有 G13 计数（只许调小），
  给并发再加一根环境变量旋钮等于把「同一根旋钮两个默认值」的病再种一遍（§P1-5）。
* **不接管裸 `threading.Thread`**：那 55 处的搬迁是 B6 的另一半。
* **不替调用方接 `halt_event`**：把 6 个模块的 halt 语义一起改需要一轮真跑；`stop=` 已经吃
  `threading.Event`，接的时候是调用方一行的事。    设计说明与读数 → `docs/nn/engineering.md` §119。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from types import TracebackType
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: 线程名前缀格式（stdlib 会在后面补 `_<序号>`）。`<scope>-<pool>` 两段都必需：
#: scope 回答「哪一轮/哪条腿」，pool 回答「这个域里的哪个池」。
NAME_FORMAT = "{scope}-{pool}"

#: 缺省上限的下限：单核云机也得能跑两个并发，否则池退化成串行。
MIN_CEILING = 2


def thread_ceiling() -> int:
    """缺省线程上限 = 逻辑核数（不少于 `MIN_CEILING`）。

    为什么以核数为界：隐式请求（`workers=None`）的调用方**没有给出自己的理由**，
    那么「和机器一样宽」是唯一有依据的缺省。自己给了数字的调用方不受它约束（见 `pool`）。
    """
    return max(MIN_CEILING, os.cpu_count() or MIN_CEILING)


class RuntimeCancelledError(RuntimeError):
    """取消点被触发（`Runtime.stop` 已置位）。

    故意**不**并进 `common.errors` 那一族：那组异常表达**失败分类**（网络 / 协议 / 节点），
    而取消表达「这一轮不许再往下走」。混一族就会把熔断记成故障 —— 两者的善后完全不同。
    """


class Runtime:
    """一个并发域的所有者：池、线程名、取消事件、上限报数都归它。

    它就是 `ThreadPoolExecutor` 的**同形替身**：`with Runtime(scope=…, workers=N) as ex:` 之后
    `ex.submit(...)` / `ex.map(...)` 照旧（转发到默认池 `"main"`），退出时由它自己收尾：

        with Runtime(scope="cloud-eval", workers=n_slots) as ex:
            ex.map(run_one, pairs)                      # 有序、池内共享
            ex.pool("compress").submit(fn, arg)         # 要独立线程名就开独立池
            if ex.sleep(5.0):                           # 可取消的等待
                ex.check()                              # 取消点：置位即抛 RuntimeCancelledError

    `close()`（`__exit__` 调的）：等待在飞的活收尾；**只有** `stop` 已置位时才取消排队的活 ——
    「正常退出」不等于「取消」，把两者混起来会让一次提前 `return` 静默丢掉已经投出去的局。
    """

    def __init__(
        self,
        *,
        scope: str,
        workers: int | None = None,
        stop: threading.Event | None = None,
        ceiling: int | None = None,
    ) -> None:
        if not scope:
            raise ValueError("Runtime 必须给 scope（线程名/日志里要认得出是谁）")
        self.scope = scope
        self.workers = workers
        self.ceiling = ceiling if ceiling is not None else thread_ceiling()
        # `stop` 允许外部传入：`halt_event` 那种已有的事件不用复制一份语义。
        self._stop = stop if stop is not None else threading.Event()
        self._pools: dict[str, ThreadPoolExecutor] = {}
        self._sizes: dict[str, int] = {}
        self._lock = threading.Lock()
        self._closed = False

    # ── 取消点 ───────────────────────────────────────────────────────────────

    @property
    def stop_event(self) -> threading.Event:
        """本域的取消事件（与传入的 `stop` 是**同一个对象**）。"""
        return self._stop

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        """置位取消：之后的 `check()/submit()/map()` 一律拒绝新活。"""
        self._stop.set()

    def check(self) -> None:
        """取消点：已置位就抛 `RuntimeCancelledError`。放在循环体内 = 显式的可取消边界。"""
        if self._stop.is_set():
            raise RuntimeCancelledError(f"{self.scope}: 已取消（stop 已置位）")

    def sleep(self, sec: float) -> bool:
        """可取消的 `time.sleep`：`True` = 睡满了，`False` = 被取消打断。

        返回布尔而不是抛异常：轮询循环里「被打断」通常是**正常出路**（该退了），
        逼调用方写 `try/except` 会把控制流藏进异常里。
        """
        if sec <= 0:
            return not self._stop.is_set()
        return not self._stop.wait(sec)

    # ── 池 ───────────────────────────────────────────────────────────────────

    @property
    def in_use(self) -> int:
        """本域所有池的 worker 数之和（`describe()` 的底下那一半）。"""
        with self._lock:
            return sum(self._sizes.values())

    def pool(self, name: str = "main", *, workers: int | None = None) -> ThreadPoolExecutor:
        """取本域里名为 `name` 的池（**同域同名 = 同一个池**）。

        `workers` 优先级：显式参数 > `Runtime(workers=…)` > 上限减去已用（至少 1）。
        前两档是「调用方说了算」，第三档才是这条上限真正约束的隐式请求。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError(f"Runtime[{self.scope}] 已关闭，不能再开池 {name!r}")
            ex = self._pools.get(name)
            if ex is not None:
                return ex
            n = self._resolve_workers(workers)
            ex = ThreadPoolExecutor(
                max_workers=n,
                thread_name_prefix=NAME_FORMAT.format(scope=self.scope, pool=name),
            )
            self._pools[name] = ex
            self._sizes[name] = n
            return ex

    def _resolve_workers(self, workers: int | None) -> int:
        """**调用方持有 `self._lock`**（`threading.Lock` 不可重入，这里不再抢一次）。"""
        want = workers if workers is not None else self.workers
        if want is not None:
            return max(1, int(want))
        return max(1, self.ceiling - sum(self._sizes.values()))

    def submit(self, fn: Callable[..., R], /, *args: object, **kwargs: object) -> Future[R]:
        """投到默认池（`"main"`）；要独立线程名就 `rt.pool("名字").submit(...)`。"""
        self.check()
        return self.pool().submit(fn, *args, **kwargs)

    def map(
        self,
        fn: Callable[[T], R],
        items: Iterable[T],
        *,
        name: str = "main",
        workers: int | None = None,
    ) -> list[R]:
        """有序 `map`：结果顺序 == `items` 顺序（本仓所有并行扇出都靠这个保序）。

        取消语义：开投之前 + 每投一个之前各 `check()` 一次；一旦置位，**已投的取消、
        已开跑的不打断**（Python 线程杀不掉，等它们自然收尾），然后抛 `RuntimeCancelledError`。
        """
        seq = list(items)
        if not seq:
            return []
        self.check()
        ex = self.pool(name, workers=workers)
        futs: list[Future[R]] = []
        try:
            for item in seq:
                self.check()
                futs.append(ex.submit(fn, item))
        finally:
            if self._stop.is_set():
                for f in futs:
                    f.cancel()
        return [f.result() for f in futs]

    # ── 读数与收尾 ───────────────────────────────────────────────────────────

    def describe(self) -> dict[str, object]:
        """一行读数：`over=True` 就是「这个域超订了」，之前没有任何地方能看见它。"""
        with self._lock:
            pools = dict(self._sizes)
            used = sum(pools.values())
            return {
                "scope": self.scope,
                "ceiling": self.ceiling,
                "workers": used,
                "over": used > self.ceiling,
                "pools": pools,
                "stopped": self._stop.is_set(),
                "closed": self._closed,
            }

    def close(self, *, wait: bool = True) -> None:
        """关掉本域全部池（幂等）。`wait=False` 只给「进程马上要退」的场合。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pools = list(self._pools.values())
            self._pools.clear()
            self._sizes.clear()
            cancel = self._stop.is_set()
        for ex in pools:
            ex.shutdown(wait=wait, cancel_futures=cancel)

    def __enter__(self) -> Runtime:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
